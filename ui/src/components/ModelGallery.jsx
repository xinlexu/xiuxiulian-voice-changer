import { Check, FolderOpen, ImagePlus, Loader2, MoreHorizontal, Pencil, Search, Trash2, Upload } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { api, upload } from "../api.js";

const PALETTE = [
  ["#ff6b9a", "#8b5cf6"],
  ["#f97316", "#ec4899"],
  ["#22d3ee", "#6366f1"],
  ["#a3e635", "#14b8a6"],
  ["#facc15", "#f43f5e"],
  ["#c084fc", "#38bdf8"],
];

function hash(text) {
  let h = 0;
  for (const ch of text) h = (h * 31 + ch.codePointAt(0)) >>> 0;
  return h;
}

function Placeholder({ name }) {
  const [a, b] = PALETTE[hash(name) % PALETTE.length];
  const letter = Array.from(name.replace(/[【】()（）\[\]\d._-]/g, ""))[0] || name[0] || "?";
  return (
    <span className="cover-placeholder" style={{ background: `linear-gradient(135deg, ${a}, ${b})` }}>
      {letter.toUpperCase()}
    </span>
  );
}

function ModelCard({ model, selected, loading, onSelect, onAction }) {
  const [menu, setMenu] = useState(false);
  const ref = useRef(null);
  useEffect(() => {
    if (!menu) return undefined;
    const close = (e) => !ref.current?.contains(e.target) && setMenu(false);
    window.addEventListener("pointerdown", close);
    return () => window.removeEventListener("pointerdown", close);
  }, [menu]);
  const tags = [model.version && model.version !== "?" ? model.version : null, model.sr ? `${model.sr / 1000}k` : null, model.index ? "索引" : null].filter(Boolean);
  return (
    <div className={`model-card ${selected ? "is-selected" : ""}`} ref={ref}>
      <button type="button" className="model-hit" onClick={() => onSelect(model)} title={model.name}>
        <span className="model-cover">
          {model.cover ? <img src={`./api/models/${encodeURIComponent(model.key)}/cover?m=${model.mtime}`} alt="" loading="lazy" /> : <Placeholder name={model.name} />}
          {selected && !loading && (
            <span className="model-check">
              <Check size={14} strokeWidth={3} />
            </span>
          )}
          {loading && (
            <span className="model-loading">
              <Loader2 size={22} className="spin" />
            </span>
          )}
        </span>
        <span className="model-name">{model.name}</span>
        <span className="model-tags">{tags.length ? tags.join(" · ") : "RVC"}</span>
      </button>
      <button type="button" className="model-more" aria-label="更多操作" onClick={() => setMenu((v) => !v)}>
        <MoreHorizontal size={16} />
      </button>
      {menu && (
        <div className="menu" role="menu">
          <button type="button" onClick={() => (setMenu(false), onAction("rename", model))}>
            <Pencil size={14} /> 改名
          </button>
          <button type="button" onClick={() => (setMenu(false), onAction("cover", model))}>
            <ImagePlus size={14} /> 换封面
          </button>
          <button type="button" onClick={() => (setMenu(false), onAction("folder", model))}>
            <FolderOpen size={14} /> 打开文件夹
          </button>
          <button type="button" className="is-danger" onClick={() => (setMenu(false), onAction("delete", model))}>
            <Trash2 size={14} /> 删除（进回收站）
          </button>
        </div>
      )}
    </div>
  );
}

export default function ModelGallery({ models, setModels, current, loadingKey, onSelect, toast }) {
  const [query, setQuery] = useState("");
  const [drag, setDrag] = useState(false);
  const [progress, setProgress] = useState(null);
  const fileRef = useRef(null);
  const coverRef = useRef(null);
  const coverTarget = useRef(null);

  const shown = useMemo(() => {
    const q = query.trim().toLowerCase();
    return q ? models.filter((m) => m.name.toLowerCase().includes(q) || m.key.toLowerCase().includes(q)) : models;
  }, [models, query]);

  async function importFiles(files) {
    for (const file of files) {
      if (!/\.(pth|zip)$/i.test(file.name)) {
        toast("warn", `${file.name}：只支持 .pth 或 .zip`);
        continue;
      }
      try {
        setProgress({ name: file.name, value: 0 });
        const res = await upload("models/import", file, (v) => setProgress({ name: file.name, value: v }));
        setModels(res.models);
        toast("info", `已导入：${res.model.name}`);
        onSelect(res.model);
      } catch (error) {
        toast("warn", `${file.name} 导入失败：${error.message}`);
      } finally {
        setProgress(null);
      }
    }
  }

  async function onAction(kind, model) {
    try {
      if (kind === "rename") {
        const name = window.prompt("新的名字", model.name);
        if (!name || name === model.name) return;
        const res = await api(`models/${encodeURIComponent(model.key)}/rename`, { name });
        setModels(res.models);
      } else if (kind === "cover") {
        coverTarget.current = model;
        coverRef.current?.click();
      } else if (kind === "folder") {
        await api("open-folder", { model: model.key });
      } else if (kind === "delete") {
        if (!window.confirm(`把「${model.name}」移到回收站？`)) return;
        const res = await api(`models/${encodeURIComponent(model.key)}/delete`, {});
        setModels(res.models);
        toast("info", `已删除：${model.name}`);
      }
    } catch (error) {
      toast("warn", error.message);
    }
  }

  async function onCover(e) {
    const file = e.target.files?.[0];
    e.target.value = "";
    const model = coverTarget.current;
    if (!file || !model) return;
    try {
      const res = await upload(`models/${encodeURIComponent(model.key)}/cover`, file);
      setModels(res.models);
    } catch (error) {
      toast("warn", error.message);
    }
  }

  return (
    <div
      className={`gallery ${drag ? "is-drag" : ""}`}
      onDragOver={(e) => {
        e.preventDefault();
        setDrag(true);
      }}
      onDragLeave={(e) => e.currentTarget === e.target && setDrag(false)}
      onDrop={(e) => {
        e.preventDefault();
        setDrag(false);
        importFiles(Array.from(e.dataTransfer.files || []));
      }}
    >
      <div className="gallery-bar">
        <label className="search">
          <Search size={15} />
          <input value={query} placeholder={`搜索 ${models.length} 个模型`} onChange={(e) => setQuery(e.target.value)} />
        </label>
        <button type="button" className="btn" onClick={() => fileRef.current?.click()} disabled={Boolean(progress)}>
          <Upload size={15} /> 导入模型
        </button>
        <input ref={fileRef} type="file" accept=".pth,.zip" multiple hidden onChange={(e) => (importFiles(Array.from(e.target.files || [])), (e.target.value = ""))} />
        <input ref={coverRef} type="file" accept=".jpg,.jpeg,.png,.webp" hidden onChange={onCover} />
      </div>
      {progress && (
        <div className="upload-progress">
          <span>正在导入 {progress.name}</span>
          <span className="bar">
            <span style={{ width: `${Math.round(progress.value * 100)}%` }} />
          </span>
        </div>
      )}
      {models.length === 0 ? (
        <div className="empty">
          <Upload size={28} />
          <p>还没有声音模型</p>
          <span>把 RVC 的 .pth（或带 .index 的 .zip）拖到这里</span>
        </div>
      ) : (
        <div className="model-grid">
          {shown.map((m) => (
            <ModelCard key={m.key} model={m} selected={m.key === current} loading={m.key === loadingKey} onSelect={onSelect} onAction={onAction} />
          ))}
          {shown.length === 0 && <div className="empty small">没有匹配「{query}」的模型</div>}
        </div>
      )}
      {drag && <div className="drop-hint">松手导入模型</div>}
    </div>
  );
}
