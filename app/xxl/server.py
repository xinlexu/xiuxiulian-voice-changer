"""Local HTTP server: serves the web UI and a small JSON API (127.0.0.1 only)."""
import glob
import json
import logging
import mimetypes
import os
import shutil
import sys
import tempfile
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import winutil
from .library import Library

log = logging.getLogger(__name__)

VERSION = "2.0.0"
APP_ID = "xiuxiulian-voice"
MAX_UPLOAD = 1536 * 1024 * 1024


class ApiError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


class App:
    def __init__(self, root, data_dir, port, backend_factory, keep_alive=False, exe_path=""):
        self.root = root
        self.data_dir = data_dir
        self.port = port
        self.keep_alive = keep_alive
        self.exe_path = exe_path
        self.web_dir = os.path.join(root, "app", "web")
        self.tmp_dir = os.path.join(data_dir, "tmp")
        shutil.rmtree(self.tmp_dir, ignore_errors=True)
        os.makedirs(self.tmp_dir, exist_ok=True)
        self.library = Library(root, data_dir)
        self._clean_web_assets()
        from .engine import Engine  # imports torch (slow) — the server is already answering by now

        self.engine = Engine(root, backend_factory, self.library.resolve)
        settings = self.library.load_settings()
        if int(settings.get("v") or 1) < 2:
            # v1 gated at -50 dB, which swallows quiet consonants on dynamic mics
            if float(settings.get("threshold", -50)) == -50:
                settings["threshold"] = -60.0
            settings["v"] = 2
        models = self.library.list_models()
        if models and settings.get("model") not in {m["key"] for m in models}:
            settings["model"] = models[0]["key"]
        self.engine.configure(settings)
        from .voicecheck import VoiceCheck

        self.voicecheck = VoiceCheck(self.engine, self.library, os.path.join(data_dir, "voicecheck"))
        self._default_devices()
        self.save_settings()
        self.shutdown_event = threading.Event()
        self.clients = 0
        self.clients_lock = threading.Lock()
        self.had_client = False
        self.close_requested_at = None
        self.no_client_since = time.monotonic()
        self.started = time.monotonic()
        threading.Thread(target=self._scan_meta, name="meta", daemon=True).start()
        if winutil.IS_WINDOWS:
            threading.Thread(target=self.fix_shortcut, name="shortcut", daemon=True).start()
            threading.Thread(target=self.cleanup_temp_helpers, name="tmp", daemon=True).start()

    # -------------------------------------------------------------- helpers
    def _scan_meta(self):
        try:
            self.library.scan_meta()
        except Exception:
            log.exception("meta scan failed")

    def _default_devices(self):
        cfg = self.engine.cfg
        if cfg["input_device"] and cfg["output_device"] and cfg["monitor_device"]:
            return
        try:
            devices = self.engine.get_backend().list_devices()
        except Exception:
            log.exception("device listing failed")
            return
        patch = {}
        if not cfg["output_device"]:
            cable = [d for d in devices["outputs"] if d["virtual"]]
            cable.sort(key=lambda d: (0 if ("伴侣" in d["name"] or "CABLE Input" in d["name"]) else 1))
            if cable:
                patch["output_device"] = cable[0]["name"]
        if not cfg["input_device"]:
            mics = [d for d in devices["inputs"] if not d["virtual"]]
            mics.sort(key=lambda d: (0 if d["default"] else 1, 1 if d["broadcast"] else 0))
            if mics:
                patch["input_device"] = mics[0]["name"]
        if not cfg["monitor_device"]:
            phones = [d for d in devices["outputs"] if not d["virtual"]]
            phones.sort(key=lambda d: 0 if d["default"] else 1)
            if phones:
                patch["monitor_device"] = phones[0]["name"]
        if patch:
            self.engine.configure(dict(cfg, **patch))

    def save_settings(self):
        try:
            self.library.save_settings(self.engine.cfg)
        except Exception:
            log.exception("saving settings failed")

    def _clean_web_assets(self):
        """Remove build files of older versions (index.html only references the current ones)."""
        assets = os.path.join(self.web_dir, "assets")
        try:
            with open(os.path.join(self.web_dir, "index.html"), "r", encoding="utf-8") as handle:
                html = handle.read()
            for name in os.listdir(assets):
                if name not in html:
                    os.remove(os.path.join(assets, name))
        except OSError:
            pass

    def save_selftest(self, report):
        if not report:
            return
        try:
            path = os.path.join(self.data_dir, "logs", "selftest.json")
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(report, handle, ensure_ascii=False, indent=2)
        except OSError:
            log.exception("writing selftest.json failed")

    def models(self):
        return [Library.public(m) for m in self.library.list_models()]

    def devices(self, refresh=False):
        backend = self.engine.get_backend()
        if refresh and self.engine.state != "running":
            backend.refresh()
        return backend.list_devices()

    def desktop_dir(self):
        if not winutil.IS_WINDOWS:
            return ""
        try:
            import win32com.client

            winutil.com_init()

            return win32com.client.Dispatch("WScript.Shell").SpecialFolders("Desktop")
        except Exception:
            return os.path.join(os.path.expanduser("~"), "Desktop")

    def shortcut_path(self):
        desktop = self.desktop_dir()
        return os.path.join(desktop, "羞羞脸变声.lnk") if desktop else ""

    def system(self):
        import torch

        engine = self.engine
        info = {
            "version": VERSION,
            "root": self.root,
            "data_dir": self.data_dir,
            "platform": sys.platform,
            "python": sys.version.split()[0],
            "torch": torch.__version__,
            "device": engine.gpu_name,
            "device_type": engine.device.type,
            "half": engine.is_half,
            "admin": winutil.is_admin(),
            "gpu_priority": engine.gpu_priority,
            "boost": engine.boost,
            "exe": self.exe_path,
            "shortcut": bool(self.shortcut_path() and os.path.exists(self.shortcut_path())),
            "driver_installer": bool(self._driver_installer()),
        }
        return info

    def _driver_installer(self):
        for rel in (("配置文件", "vbcable", "VBCABLE_Setup_x64.exe"), ("drivers", "vbcable", "VBCABLE_Setup_x64.exe")):
            path = os.path.join(self.root, *rel)
            if os.path.isfile(path):
                return path
        return ""

    def open_window(self):
        from .window import open_app_window

        open_app_window("http://127.0.0.1:%d/" % self.port, self.data_dir)

    def watch_clients(self):
        """Quit after the window was closed.

        A closed window tells us so (beacon on pagehide). A window that merely went
        quiet (minimised for hours, Edge put it to sleep) must NOT stop the voice in
        the middle of a game, so without that signal we only quit while idle.
        """
        while not self.shutdown_event.wait(1.0):
            if self.keep_alive:
                continue
            now = time.monotonic()
            with self.clients_lock:
                clients = self.clients
            if clients > 0:
                self.close_requested_at = None
                continue
            idle = now - self.no_client_since
            closed = self.close_requested_at is not None and now - self.close_requested_at > 4 and idle > 3
            idle_quit = self.had_client and idle > 10 and self.engine.state != "running"
            if closed or idle_quit or (not self.had_client and idle > 180):
                log.info("window closed (closed=%s idle=%.0fs), shutting down", closed, idle)
                self.shutdown_event.set()

    # ------------------------------------------------------------- actions
    def action(self, name, body):
        engine = self.engine
        if name == "settings":
            cfg = engine.update(body or {})
            self.save_settings()
            return {"config": cfg}
        if name == "start":
            if self.voicecheck.busy:
                raise ApiError("正在做声音体检，做完再开变声")
            engine.start(body or None)
            self.save_settings()
            return {"ok": True}
        if name == "stop":
            engine.stop()
            return {"ok": True}
        if name == "toggle":
            if engine.state == "running":
                engine.stop()
            elif self.voicecheck.busy:
                raise ApiError("正在做声音体检，做完再开变声")
            else:
                engine.start()
            return {"running": engine.state == "running"}
        if name == "selftest":
            if engine.state == "running":
                raise ApiError("先停止变声再自检")
            report = engine.selftest()
            self.save_selftest(report)
            return report or {}
        if name == "record":
            result = engine.record((body or {}).get("seconds", 5), os.path.join(self.data_dir, "recordings"))
            os.makedirs(os.path.join(self.data_dir, "logs"), exist_ok=True)
            with open(os.path.join(self.data_dir, "logs", "last-recording.json"), "w", encoding="utf-8") as handle:
                json.dump(result, handle, ensure_ascii=False, indent=2)
            return result
        if name == "voicecheck-record":
            self.voicecheck.record((body or {}).get("seconds", 25))
            return self.voicecheck.status()
        if name == "voicecheck-finish":
            self.voicecheck.finish_recording()
            return {"ok": True}
        if name == "voicecheck-analyze":
            target = (body or {}).get("target_f0") or engine.cfg["target_f0"]
            return self.voicecheck.start_analysis(float(target))
        if name == "calibrate":
            return engine.calibrate((body or {}).get("seconds", 4))
        if name == "window-closed":
            self.close_requested_at = time.monotonic()
            return {"ok": True}
        if name == "quit":
            threading.Timer(0.3, self.shutdown_event.set).start()
            return {"ok": True}
        if name == "show":
            self.open_window()
            return {"ok": True}
        if name == "shortcut":
            return {"ok": self.create_shortcut()}
        if name == "open-folder":
            which = (body or {}).get("which", "models")
            target = {"models": self.library.models_dir, "data": self.data_dir,
                      "logs": os.path.join(self.data_dir, "logs"), "root": self.root}.get(which, self.root)
            if (body or {}).get("model"):
                target = self.library.model_folder(body["model"])
            if winutil.IS_WINDOWS:
                os.startfile(target)
            return {"ok": True}
        if name == "install-driver":
            path = self._driver_installer()
            if not path:
                raise ApiError("没有找到声卡驱动安装程序（配置文件\\vbcable）")
            if not winutil.relaunch_as_admin(path, "", os.path.dirname(path)):
                raise ApiError("启动驱动安装失败")
            return {"ok": True}
        if name == "admin-restart":
            if not self.exe_path or not os.path.exists(self.exe_path):
                raise ApiError("找不到 羞羞脸变声.exe，无法以管理员身份重启")
            was_running = engine.state == "running"
            engine.stop()
            self.shutdown_event.set()
            time.sleep(0.5)
            if not winutil.relaunch_as_admin(self.exe_path, "--after-admin", self.root):
                raise ApiError("没有获得管理员权限")
            return {"ok": True, "was_running": was_running}
        raise ApiError("未知操作 %s" % name, 404)

    def create_shortcut(self, exe_path=None, root=None):
        target = self.shortcut_path()
        exe_path = exe_path or self.exe_path
        root = root or self.root
        if not target or not exe_path:
            return False
        import win32com.client

        winutil.com_init()
        shell = win32com.client.Dispatch("WScript.Shell")
        link = shell.CreateShortCut(target)
        link.TargetPath = exe_path
        link.WorkingDirectory = root
        link.IconLocation = exe_path + ",0"
        link.Description = "羞羞脸变声"
        link.save()
        return True

    def fix_shortcut(self):
        """Repair a desktop shortcut whose target no longer exists (folder renamed/moved)."""
        path = self.shortcut_path()
        if not path or not os.path.exists(path) or not self.exe_path or not os.path.exists(self.exe_path):
            return
        try:
            import win32com.client

            winutil.com_init()

            link = win32com.client.Dispatch("WScript.Shell").CreateShortCut(path)
            target = link.TargetPath or ""
            if not target or not os.path.exists(target):
                self.create_shortcut()
                log.info("desktop shortcut repaired: %s -> %s", target, self.exe_path)
        except Exception:
            log.exception("shortcut check failed")

    @staticmethod
    def cleanup_temp_helpers():
        temp = os.environ.get("TEMP") or tempfile.gettempdir()
        for path in glob.glob(os.path.join(temp, "xiuxiulian-move-*.exe")):
            try:
                os.remove(path)
            except OSError:
                pass

class AppHolder:
    """Lets the HTTP server start before the (slow) engine is ready."""

    def __init__(self, port, web_dir):
        self.app = None
        self.port = port
        self.web_dir = web_dir
        self.boot_error = ""


def make_handler(holder):
    class Handler(BaseHTTPRequestHandler):
        server_version = "Xiuxiulian/" + VERSION
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):  # quiet
            pass

        # ---------------------------------------------------------- utils
        def _send(self, status, body, ctype="application/json; charset=utf-8", headers=None):
            if isinstance(body, (dict, list)):
                body = json.dumps(body, ensure_ascii=False).encode("utf-8")
            elif isinstance(body, str):
                body = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            for key, value in (headers or {}).items():
                self.send_header(key, value)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json_body(self):
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0:
                return {}
            raw = self.rfile.read(length)
            try:
                return json.loads(raw.decode("utf-8"))
            except Exception:
                raise ApiError("请求格式错误")

        def _upload(self):
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0:
                raise ApiError("没有收到文件")
            if length > MAX_UPLOAD:
                raise ApiError("文件太大")
            name = urllib.parse.unquote(self.headers.get("X-Filename") or "upload.bin")
            name = os.path.basename(name.replace("\\", "/"))
            fd, path = tempfile.mkstemp(dir=holder.app.tmp_dir, suffix="-" + name[-60:])
            remaining = length
            with os.fdopen(fd, "wb") as handle:
                while remaining > 0:
                    chunk = self.rfile.read(min(1 << 20, remaining))
                    if not chunk:
                        break
                    handle.write(chunk)
                    remaining -= len(chunk)
            if remaining:
                os.remove(path)
                raise ApiError("上传中断")
            return path, name

        def _guard(self):
            # Only our own page may call the API (blocks drive-by requests from other sites).
            origin = self.headers.get("Origin")
            if origin and not origin.startswith(("http://127.0.0.1:%d" % holder.port, "http://localhost:%d" % holder.port)):
                raise ApiError("forbidden", 403)

        # -------------------------------------------------------- routes
        def do_GET(self):
            self._dispatch("GET")

        def do_HEAD(self):
            self._dispatch("GET")

        def do_POST(self):
            self._dispatch("POST")

        def _dispatch(self, method):
            path = urllib.parse.urlparse(self.path).path
            query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            try:
                if path.startswith("/api/"):
                    self._guard()
                    if holder.app is None:
                        if path == "/api/ping":
                            return self._send(200, {"app": APP_ID, "version": VERSION, "starting": True})
                        raise ApiError(holder.boot_error or "starting", 500 if holder.boot_error else 503)
                    self._api(method, path[5:], query)
                else:
                    self._static(path)
            except ApiError as error:
                self._send(error.status, {"error": str(error)})
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass
            except KeyError:
                self._send(404, {"error": "找不到这个模型"})
            except Exception as error:
                if isinstance(error, (RuntimeError, ValueError, LookupError)):
                    log.warning("request failed: %s %s: %s", method, path, error)
                else:
                    log.exception("request failed: %s %s", method, path)
                self._send(500, {"error": str(error) or error.__class__.__name__})

        def _api(self, method, route, query):
            app = holder.app
            parts = [urllib.parse.unquote(p) for p in route.split("/") if p]
            if method == "GET":
                if parts == ["ping"]:
                    return self._send(200, {"app": APP_ID, "version": VERSION})
                if parts == ["state"]:
                    from .engine import DEFAULTS, PRESETS

                    return self._send(200, {
                        "status": app.engine.status(), "models": app.models(), "presets": PRESETS,
                        "defaults": DEFAULTS, "system": app.system(),
                    })
                if parts == ["status"]:
                    return self._send(200, app.engine.status())
                if parts == ["models"]:
                    return self._send(200, {"models": app.models()})
                if parts == ["devices"]:
                    return self._send(200, app.devices(refresh="refresh" in query))
                if parts == ["system"]:
                    return self._send(200, app.system())
                if parts == ["events"]:
                    return self._events()
                if parts == ["voicecheck"]:
                    return self._send(200, app.voicecheck.status())
                if len(parts) == 2 and parts[0] in ("recordings", "voicecheck"):
                    name = os.path.basename(parts[1])
                    path = os.path.join(app.data_dir, parts[0], name)
                    if not name.endswith(".wav") or not os.path.isfile(path):
                        raise ApiError("not found", 404)
                    with open(path, "rb") as handle:
                        return self._send(200, handle.read(), "audio/wav", {"Cache-Control": "no-cache"})
                if len(parts) == 3 and parts[0] == "models" and parts[2] == "cover":
                    entry = app.library.get(parts[1])
                    if not entry["image"]:
                        raise ApiError("no cover", 404)
                    with open(entry["image"], "rb") as handle:
                        data = handle.read()
                    ctype = mimetypes.guess_type(entry["image"])[0] or "image/jpeg"
                    return self._send(200, data, ctype, {"Cache-Control": "max-age=60"})
                raise ApiError("not found", 404)
            # POST
            if parts == ["models", "import"]:
                temp, name = self._upload()
                try:
                    model = app.library.import_file(temp, name)
                finally:
                    try:
                        os.remove(temp)
                    except OSError:
                        pass
                return self._send(200, {"model": model, "models": app.models()})
            if len(parts) == 3 and parts[0] == "models":
                key, op = parts[1], parts[2]
                if op == "cover":
                    temp, name = self._upload()
                    try:
                        model = app.library.set_cover(key, temp, name)
                    finally:
                        os.remove(temp)
                    return self._send(200, {"model": model, "models": app.models()})
                body = self._json_body()
                if op == "rename":
                    return self._send(200, {"model": app.library.rename(key, body.get("name", "")), "models": app.models()})
                if op == "delete":
                    if key == app.engine.cfg["model"] and app.engine.state == "running":
                        raise ApiError("正在使用这个模型，先换一个再删")
                    folder = app.library.model_folder(key)
                    if winutil.IS_WINDOWS:
                        ok, failed = winutil.send_to_recycle_bin([folder])
                        if failed:
                            raise ApiError("删除失败（文件被占用？）")
                    else:
                        trash = os.path.join(app.data_dir, "trash")
                        os.makedirs(trash, exist_ok=True)
                        shutil.move(folder, os.path.join(trash, "%s-%d" % (key, time.time())))
                    return self._send(200, {"models": app.models()})
                raise ApiError("not found", 404)
            if len(parts) == 1:
                return self._send(200, app.action(parts[0], self._json_body()))
            raise ApiError("not found", 404)

        def _events(self):
            app = holder.app
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            with app.clients_lock:
                app.clients += 1
                app.had_client = True
            try:
                while not app.shutdown_event.is_set():
                    payload = json.dumps(app.engine.status(), ensure_ascii=False)
                    self.wfile.write(("data: %s\n\n" % payload).encode("utf-8"))
                    self.wfile.flush()
                    time.sleep(0.25)
            except Exception:
                pass
            finally:
                with app.clients_lock:
                    app.clients -= 1
                    if app.clients == 0:
                        app.no_client_since = time.monotonic()
                self.close_connection = True

        def _static(self, path):
            web_dir = holder.web_dir
            rel = os.path.normpath(urllib.parse.unquote(path).lstrip("/")) if path not in ("", "/") else "index.html"
            full = os.path.abspath(os.path.join(web_dir, rel))
            if not full.startswith(os.path.abspath(web_dir)) or not os.path.isfile(full):
                full = os.path.join(web_dir, "index.html")
            if not os.path.isfile(full):
                return self._send(500, "界面文件缺失：app\\web\\index.html", "text/plain; charset=utf-8")
            with open(full, "rb") as handle:
                data = handle.read()
            ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype in ("application/javascript",):
                ctype += "; charset=utf-8"
            cache = "no-cache"  # local server; file names are fixed, always revalidate
            self._send(200, data, ctype, {"Cache-Control": cache})

    return Handler


def serve(holder):
    mimetypes.add_type("application/javascript", ".js")
    mimetypes.add_type("text/css", ".css")
    mimetypes.add_type("image/svg+xml", ".svg")
    mimetypes.add_type("image/webp", ".webp")
    httpd = ThreadingHTTPServer(("127.0.0.1", holder.port), make_handler(holder))
    httpd.daemon_threads = True
    thread = threading.Thread(target=httpd.serve_forever, name="http", daemon=True)
    thread.start()
    return httpd
