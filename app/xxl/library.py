"""Voice model library (Assets/Models) and settings storage."""
import json
import logging
import os
import re
import shutil
import tempfile
import threading
import zipfile

log = logging.getLogger(__name__)

IMAGE_NAMES = ("cover.jpg", "cover.png", "cover.jpeg", "cover.webp", "icon.png", "image.png")


def _read_json(path, default):
    try:
        with open(path, "r", encoding="utf-8-sig") as handle:
            return json.load(handle)
    except Exception:
        return default


def _write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    os.replace(tmp, path)


def safe_name(name):
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(name)).strip().rstrip(".")
    return cleaned[:80] or "model"


class Library:
    def __init__(self, root, data_dir):
        self.root = root
        self.data_dir = data_dir
        self.models_dir = os.path.join(root, "Assets", "Models")
        self.settings_path = os.path.join(data_dir, "settings.json")
        self.meta_path = os.path.join(data_dir, "model-meta.json")
        self.lock = threading.RLock()
        self.meta = _read_json(self.meta_path, {})
        os.makedirs(self.models_dir, exist_ok=True)

    # ------------------------------------------------------------ settings
    def load_settings(self):
        return _read_json(self.settings_path, None) or {}

    def save_settings(self, settings):
        _write_json(self.settings_path, settings)

    # -------------------------------------------------------------- models
    def _entry(self, folder):
        path = os.path.join(self.models_dir, folder)
        info = _read_json(os.path.join(path, "info.json"), {}) or {}
        try:
            files = os.listdir(path)
        except OSError:
            return None
        pth_name = info.get("ModelFile") if info.get("ModelFile") in files else None
        if not pth_name:
            pths = sorted(f for f in files if f.lower().endswith(".pth"))
            pth_name = pths[0] if pths else None
        if not pth_name:
            return None
        pth = os.path.join(path, pth_name)
        try:
            stat = os.stat(pth)
        except OSError:
            return None
        if stat.st_size < 1024 * 1024:
            return None
        indexes = sorted(f for f in files if f.lower().endswith(".index"))
        index = ""
        if indexes:
            added = [f for f in indexes if f.lower().startswith("added")]
            index = os.path.join(path, (added or indexes)[0])
        image = info.get("ImageFile") if info.get("ImageFile") in files else None
        if not image:
            image = next((f for f in files if f.lower() in IMAGE_NAMES), None)
        label = str(info.get("Label") or "")
        if label in ("无标签", "本地导入", "手动导入"):
            label = ""
        meta = self.meta.get(folder) or {}
        if meta.get("mtime") != int(stat.st_mtime) or meta.get("size") != stat.st_size:
            meta = {}
        return {
            "key": folder,
            "name": str(info.get("Name") or folder),
            "label": label,
            "pth": pth,
            "index": index,
            "size": stat.st_size,
            "mtime": int(stat.st_mtime),
            "cover": bool(image),
            "image": os.path.join(path, image) if image else "",
            "version": meta.get("version"),
            "sr": meta.get("sr"),
            "f0": meta.get("f0"),
        }

    def list_models(self):
        items = []
        try:
            folders = os.listdir(self.models_dir)
        except OSError:
            folders = []
        for folder in folders:
            if not os.path.isdir(os.path.join(self.models_dir, folder)):
                continue
            entry = self._entry(folder)
            if entry:
                items.append(entry)
        items.sort(key=lambda e: e["name"].lower())
        return items

    @staticmethod
    def public(entry):
        out = {k: entry[k] for k in ("key", "name", "label", "size", "cover", "version", "sr", "f0", "mtime")}
        out["index"] = bool(entry["index"])
        return out

    def get(self, key):
        folder = os.path.basename(str(key))
        if not folder or folder in (".", ".."):
            raise KeyError(key)
        entry = self._entry(folder)
        if not entry:
            raise KeyError(key)
        return entry

    def resolve(self, key):
        entry = self.get(key)
        return entry["pth"], entry["index"]

    def scan_meta(self):
        """Fill version / sample-rate tags in the background (reads each .pth once)."""
        import torch

        changed = False
        for entry in self.list_models():
            if entry["version"]:
                continue
            try:
                with open(entry["pth"], "rb") as handle:
                    cpt = torch.load(handle, map_location="cpu", weights_only=False)
                meta = {
                    "version": cpt.get("version", "v1"),
                    "sr": int(cpt["config"][-1]),
                    "f0": int(cpt.get("f0", 1)),
                    "mtime": entry["mtime"],
                    "size": entry["size"],
                }
                del cpt
            except Exception as error:
                meta = {"version": "?", "error": str(error)[:120], "mtime": entry["mtime"], "size": entry["size"]}
            with self.lock:
                self.meta[entry["key"]] = meta
            changed = True
        if changed:
            with self.lock:
                _write_json(self.meta_path, self.meta)

    def _unique_folder(self, base):
        name = safe_name(base)
        candidate, n = name, 2
        while os.path.exists(os.path.join(self.models_dir, candidate)):
            candidate = "%s-%d" % (name, n)
            n += 1
        return candidate

    @staticmethod
    def validate_pth(path):
        import torch

        try:
            with open(path, "rb") as handle:
                cpt = torch.load(handle, map_location="cpu", weights_only=False)
        except Exception as error:
            raise ValueError("文件损坏或不是 PyTorch 模型（%s）" % str(error)[:80])
        if not isinstance(cpt, dict):
            raise ValueError("文件不是 RVC 模型")
        if "weight" not in cpt or "config" not in cpt:
            if "model" in cpt or "G" in cpt:
                raise ValueError("这是 So-VITS 等其它格式，本软件只支持 RVC 模型")
            raise ValueError("文件不是 RVC 推理模型（缺少 weight/config，可能是训练中间文件 G_xxx.pth）")
        if not any("enc_p" in k for k in cpt["weight"].keys()):
            raise ValueError("模型结构不对（缺少 enc_p）")
        return {"version": cpt.get("version", "v1"), "sr": int(cpt["config"][-1]), "f0": int(cpt.get("f0", 1))}

    def import_file(self, temp_path, filename):
        """Import a .pth, or a .zip that contains a .pth (+ optional .index / cover)."""
        lower = filename.lower()
        work = tempfile.mkdtemp(prefix="xxl-import-")
        try:
            if lower.endswith(".zip"):
                with zipfile.ZipFile(temp_path) as archive:
                    members = [m for m in archive.infolist() if not m.is_dir()]
                    pths = [m for m in members if m.filename.lower().endswith(".pth")]
                    if not pths:
                        raise ValueError("压缩包里没有 .pth 模型")
                    pths.sort(key=lambda m: m.file_size, reverse=True)
                    pick = [pths[0]]
                    pick += [m for m in members if m.filename.lower().endswith(".index")][:1]
                    pick += [m for m in members if os.path.basename(m.filename).lower() in IMAGE_NAMES or
                             m.filename.lower().endswith((".jpg", ".jpeg", ".png", ".webp"))][:1]
                    extracted = {}
                    for member in pick:
                        target = os.path.join(work, os.path.basename(member.filename) or "file")
                        with archive.open(member) as src, open(target, "wb") as dst:
                            shutil.copyfileobj(src, dst)
                        extracted[member.filename] = target
                pth_path = extracted[pths[0].filename]
                stem = os.path.splitext(os.path.basename(pths[0].filename))[0]
                others = [p for k, p in extracted.items() if k != pths[0].filename]
            elif lower.endswith(".pth"):
                pth_path, stem, others = temp_path, os.path.splitext(os.path.basename(filename))[0], []
            else:
                raise ValueError("只支持 .pth 模型或包含模型的 .zip")
            meta = self.validate_pth(pth_path)
            folder = self._unique_folder(stem)
            dest = os.path.join(self.models_dir, folder)
            os.makedirs(dest)
            shutil.copyfile(pth_path, os.path.join(dest, folder + ".pth"))
            image = ""
            for other in others:
                name = os.path.basename(other)
                if name.lower().endswith(".index"):
                    shutil.copyfile(other, os.path.join(dest, name))
                else:
                    image = "cover" + os.path.splitext(name)[1].lower()
                    shutil.copyfile(other, os.path.join(dest, image))
            _write_json(os.path.join(dest, "info.json"), {
                "Id": folder, "Name": stem, "Type": 1, "Label": "", "ImageFile": image, "ModelFile": folder + ".pth",
            })
            stat = os.stat(os.path.join(dest, folder + ".pth"))
            with self.lock:
                self.meta[folder] = dict(meta, mtime=int(stat.st_mtime), size=stat.st_size)
                _write_json(self.meta_path, self.meta)
            return self.public(self.get(folder))
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def rename(self, key, name):
        entry = self.get(key)
        folder = os.path.join(self.models_dir, entry["key"])
        info_path = os.path.join(folder, "info.json")
        info = _read_json(info_path, {}) or {}
        info.setdefault("Id", entry["key"])
        info.setdefault("ModelFile", os.path.basename(entry["pth"]))
        info["Name"] = str(name).strip()[:60] or entry["key"]
        _write_json(info_path, info)
        return self.public(self.get(key))

    def set_cover(self, key, temp_path, filename):
        entry = self.get(key)
        ext = os.path.splitext(filename)[1].lower()
        if ext not in (".jpg", ".jpeg", ".png", ".webp"):
            raise ValueError("封面只支持 jpg / png / webp")
        folder = os.path.join(self.models_dir, entry["key"])
        name = "cover" + ext
        shutil.copyfile(temp_path, os.path.join(folder, name))
        info_path = os.path.join(folder, "info.json")
        info = _read_json(info_path, {}) or {}
        info.setdefault("Id", entry["key"])
        info.setdefault("Name", entry["name"])
        info.setdefault("ModelFile", os.path.basename(entry["pth"]))
        info["ImageFile"] = name
        _write_json(info_path, info)
        return self.public(self.get(key))

    def model_folder(self, key):
        return os.path.join(self.models_dir, self.get(key)["key"])
