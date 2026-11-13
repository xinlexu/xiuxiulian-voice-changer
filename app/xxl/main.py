"""Process entry: single instance, logging, priorities, server, window."""
import argparse
import json
import logging
import logging.handlers
import os
import sys
import threading
import time
import urllib.request

DEFAULT_PORT = 18674


def ping(port, timeout=0.6):
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d/api/ping" % port, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8")).get("app") == "xiuxiulian-voice"
    except Exception:
        return False


def post(port, action):
    req = urllib.request.Request(
        "http://127.0.0.1:%d/api/%s" % (port, action), data=b"{}", headers={"Content-Type": "application/json"}
    )
    try:
        urllib.request.urlopen(req, timeout=2).read()
    except Exception:
        pass


def setup_logging(data_dir, console):
    log_dir = os.path.join(data_dir, "logs")
    os.makedirs(log_dir, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(
        os.path.join(log_dir, "xxl.log"), maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    handler.setFormatter(fmt)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    if console and sys.stderr is not None:
        stream = logging.StreamHandler()
        stream.setFormatter(fmt)
        root.addHandler(stream)
    if sys.stdout is None or sys.stderr is None or not console:
        # pythonw: no console. Libraries that print must not crash.
        sink = open(os.path.join(log_dir, "stdout.log"), "a", encoding="utf-8", buffering=1)
        sys.stdout = sink
        sys.stderr = sink
    return logging.getLogger("xxl")


def run(root):
    parser = argparse.ArgumentParser(description="羞羞脸变声")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--no-window", action="store_true", help="不自动打开界面窗口")
    parser.add_argument("--keep-alive", action="store_true", help="关闭窗口后继续运行")
    parser.add_argument("--console", action="store_true", help="日志同时输出到控制台")
    parser.add_argument("--after-admin", action="store_true")
    parser.add_argument("--exe", default="")
    args = parser.parse_args()

    data_dir = os.path.join(root, "data")
    os.makedirs(data_dir, exist_ok=True)

    if args.after_admin:  # wait for the non-elevated instance to release the port
        deadline = time.time() + 15
        while ping(args.port) and time.time() < deadline:
            time.sleep(0.3)
    if ping(args.port):
        post(args.port, "show")
        return 0

    log = setup_logging(data_dir, args.console)
    log.info("starting 羞羞脸变声 root=%s python=%s", root, sys.version.split()[0])
    sys.setswitchinterval(0.001)  # audio callbacks get the GIL quickly
    os.environ.setdefault("PYTHONUTF8", "1")

    from . import winutil
    from .server import AppHolder, serve

    # 1) answer HTTP and show the window immediately (torch takes seconds to import)
    holder = AppHolder(args.port, os.path.join(root, "app", "web"))
    try:
        httpd = serve(holder)
    except OSError as error:
        log.error("port %d busy: %s", args.port, error)
        return 1
    if not args.no_window:
        from .window import open_app_window

        open_app_window("http://127.0.0.1:%d/" % args.port, data_dir)

    boost = winutil.boost_process()
    log.info("process boost: %s", boost)

    # 2) build the engine (imports torch, scans models, opens nothing yet)
    try:
        from .audio import SoundDeviceBackend
        from .server import App

        exe = args.exe or os.path.join(root, "羞羞脸变声.exe")
        app = App(root, data_dir, args.port, SoundDeviceBackend, keep_alive=args.keep_alive, exe_path=exe)
    except Exception as error:
        log.exception("engine failed to start")
        holder.boot_error = "引擎启动失败：%s（详细信息在 data\\logs\\xxl.log）" % error
        time.sleep(120)
        httpd.shutdown()
        return 1
    app.engine.boost = boost
    if app.engine.cfg.get("gpu_priority"):
        app.engine.gpu_priority = winutil.set_gpu_priority()
        log.info("gpu priority: %s (admin=%s)", app.engine.gpu_priority, winutil.is_admin())
    holder.app = app
    app.engine.preload()

    def save_first_selftest():
        if app.engine.preload_thread is not None:
            app.engine.preload_thread.join()
        app.save_selftest(app.engine.selftest_report)

    threading.Thread(target=save_first_selftest, name="selftest-save", daemon=True).start()
    threading.Thread(target=app.watch_clients, name="watch", daemon=True).start()
    try:
        while not app.shutdown_event.wait(0.5):
            pass
    except KeyboardInterrupt:
        pass
    log.info("shutting down")
    try:
        app.engine.stop()
    except Exception:
        log.exception("stop failed")
    app.save_settings()
    httpd.shutdown()
    return 0
