import { Cpu, Layers, LogOut, Settings2, SlidersHorizontal, Sparkles, Speaker, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, subscribe } from "./api.js";
import ModelGallery from "./components/ModelGallery.jsx";
import VoiceCheck from "./components/VoiceCheck.jsx";
import { AdvancedTab, DevicesTab, ModePanel, PowerPanel, SystemTab, VoicePanel } from "./components/panels.jsx";

const TABS = [
  { id: "models", label: "声音模型", icon: Layers },
  { id: "custom", label: "为我定制", icon: Sparkles },
  { id: "devices", label: "设备与监听", icon: Speaker },
  { id: "advanced", label: "高级", icon: SlidersHorizontal },
  { id: "system", label: "系统", icon: Settings2 },
];

const FALLBACK = {
  pitch: 10, formant: 0, preset: "game", block_time: 0.12, crossfade_time: 0.03, extra_time: 1.0,
  threshold: -60, rms_mix_rate: 0, monitor_volume: 0.8, input_gain_db: 0, output_gain_db: 0,
  index_rate: 0, min_buffer_ms: 15, target_f0: 220, auto_tune: true, f0_method: "rmvpe",
};

function useToasts() {
  const [toasts, setToasts] = useState([]);
  const push = useCallback((level, text) => {
    const id = Math.random().toString(36).slice(2);
    setToasts((t) => [...t.slice(-3), { id, level, text }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), level === "warn" ? 7000 : 3500);
  }, []);
  const dismiss = useCallback((id) => setToasts((t) => t.filter((x) => x.id !== id)), []);
  return [toasts, push, dismiss];
}

export default function App() {
  const [status, setStatus] = useState(null);
  const [models, setModels] = useState([]);
  const [devices, setDevices] = useState({ inputs: [], outputs: [] });
  const [system, setSystem] = useState(null);
  const [draft, setDraft] = useState({});
  const [busy, setBusy] = useState("");
  const [tab, setTab] = useState(() => {
    try {
      return localStorage.getItem("xxl-tab") || "models";
    } catch {
      return "models";
    }
  });
  const [online, setOnline] = useState(true);
  const [bootError, setBootError] = useState("");
  const [toasts, toast, dismiss] = useToasts();
  const lastEvent = useRef(0);
  const pending = useRef({});
  const timer = useRef(null);

  const cfg = useMemo(() => ({ ...FALLBACK, ...(status?.config || {}), ...draft }), [status, draft]);

  const refreshSystem = useCallback(async () => {
    try {
      setSystem(await api("system"));
    } catch {
      /* offline */
    }
  }, []);

  const refreshDevices = useCallback(async (hard) => {
    try {
      setDevices(await api(hard ? "devices?refresh=1" : "devices"));
    } catch (error) {
      toast("warn", `读取声音设备失败：${error.message}`);
    }
  }, [toast]);

  useEffect(() => {
    let cancelled = false;
    let stop = null;
    async function boot() {
      // The window opens before the engine has finished loading; keep asking.
      while (!cancelled) {
        try {
          const s = await api("state");
          if (cancelled) return;
          setStatus(s.status);
          setModels(s.models);
          setSystem((prev) => ({ ...s.system, ...(prev || {}) }));
          lastEvent.current = Math.max(0, ...(s.status.events || []).map((e) => e.id));
          setBootError("");
          break;
        } catch (error) {
          setBootError(error.message === "starting" || /Failed to fetch/i.test(error.message) ? "" : error.message);
          await new Promise((r) => setTimeout(r, 700));
        }
      }
      if (cancelled) return;
      refreshDevices(false);
      refreshSystem();
      stop = subscribe(
        (s) => {
          setOnline(true);
          setStatus(s);
          for (const ev of s.events || []) {
            if (ev.id > lastEvent.current) {
              lastEvent.current = ev.id;
              if (ev.level === "warn" || /模型|显卡|负载/.test(ev.text)) toast(ev.level, ev.text);
            }
          }
        },
        () => setOnline(false),
      );
    }
    boot();
    return () => {
      cancelled = true;
      stop?.();
    };
  }, [refreshDevices, refreshSystem, toast]);

  // refresh model tags once the background scan has filled them in
  useEffect(() => {
    if (!models.some((m) => !m.version)) return undefined;
    const t = setTimeout(() => api("models").then((r) => setModels(r.models)).catch(() => {}), 4000);
    return () => clearTimeout(t);
  }, [models]);

  // drop local overrides once the engine reports the same value
  useEffect(() => {
    if (!status?.config) return;
    setDraft((d) => {
      const next = {};
      let changed = false;
      for (const [k, v] of Object.entries(d)) {
        if (status.config[k] === v || (typeof v === "number" && Math.abs(status.config[k] - v) < 1e-6)) changed = true;
        else next[k] = v;
      }
      return changed ? next : d;
    });
  }, [status]);

  const setCfg = useCallback(
    (patch) => {
      setDraft((d) => ({ ...d, ...patch }));
      Object.assign(pending.current, patch);
      clearTimeout(timer.current);
      timer.current = setTimeout(async () => {
        const body = pending.current;
        pending.current = {};
        try {
          const res = await api("settings", body);
          setStatus((s) => (s ? { ...s, config: res.config } : s));
        } catch (error) {
          toast("warn", error.message);
          setDraft({});
        }
      }, 140);
    },
    [toast],
  );

  useEffect(() => {
    const onBeforeUnload = (e) => {
      if (status?.state === "running") {
        e.preventDefault();
        e.returnValue = "";
      }
    };
    window.addEventListener("beforeunload", onBeforeUnload);
    return () => window.removeEventListener("beforeunload", onBeforeUnload);
  }, [status?.state]);

  useEffect(() => {
    // Tell the engine the window really closed (a sleeping window must not stop the voice).
    const onHide = (e) => {
      if (!e.persisted) navigator.sendBeacon?.("./api/window-closed", new Blob(["{}"], { type: "application/json" }));
    };
    window.addEventListener("pagehide", onHide);
    return () => window.removeEventListener("pagehide", onHide);
  }, []);

  async function toggle() {
    setBusy("toggle");
    try {
      await api(status?.state === "running" ? "stop" : "start", {});
    } catch (error) {
      toast("warn", error.message);
    } finally {
      setBusy("");
    }
  }

  async function quit() {
    if (!window.confirm("退出羞羞脸变声？（变声会停止）")) return;
    await api("quit", {}).catch(() => {});
    window.close();
  }

  function selectTab(id) {
    setTab(id);
    try {
      localStorage.setItem("xxl-tab", id);
    } catch {
      /* ignore */
    }
  }

  const current = models.find((m) => m.key === cfg.model);
  const running = status?.state === "running";

  if (!status) {
    return (
      <div className="boot">
        <img src="./icon.svg" alt="" />
        <h1>羞羞脸变声</h1>
        {bootError ? (
          <p className="boot-error">{bootError}</p>
        ) : (
          <>
            <span className="boot-bar">
              <span />
            </span>
            <p>正在启动变声引擎…（第一次打开要多等几秒）</p>
          </>
        )}
      </div>
    );
  }

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <img src="./icon.svg" alt="" />
          <div>
            <h1>羞羞脸变声</h1>
            <span>实时 AI 变声 · 低延迟</span>
          </div>
        </div>
        <div className="top-right">
          <span className={`pill ${running ? "pill-run" : status?.state === "error" ? "pill-err" : ""}`}>
            <span className={`dot dot-${status?.state || "idle"}`} />
            {running ? "变声中" : status?.state === "error" ? "出错" : status?.state === "loading" ? "加载中" : "未开启"}
          </span>
          <span className="pill" title="推理设备">
            <Cpu size={14} />
            {status?.device || "…"}
          </span>
          <button type="button" className="icon-btn" title="退出软件" onClick={quit}>
            <LogOut size={17} />
          </button>
        </div>
      </header>

      {!online && <div className="banner">和变声引擎的连接断了，正在重连…（如果软件已经退出，重新打开「羞羞脸变声」）</div>}
      {status?.state === "error" && status.error && <div className="banner banner-err">{status.error}</div>}

      <main className="layout">
        <div className="col-left">
          <PowerPanel status={status} cfg={cfg} busy={busy} onToggle={toggle} modelName={current?.name} />
          <VoicePanel cfg={cfg} setCfg={setCfg} toast={toast} status={status} />
          <ModePanel cfg={cfg} setCfg={setCfg} status={status} />
        </div>
        <div className="col-right">
          <nav className="tabs" role="tablist">
            {TABS.map(({ id, label, icon: Icon }) => (
              <button key={id} type="button" role="tab" aria-selected={tab === id} className={tab === id ? "is-active" : ""} onClick={() => selectTab(id)}>
                <Icon size={15} />
                {label}
              </button>
            ))}
          </nav>
          <div className="tab-panel">
            {tab === "models" && (
              <ModelGallery
                models={models}
                setModels={setModels}
                current={cfg.model}
                loadingKey={status?.model_loading}
                onSelect={(m) => setCfg({ model: m.key })}
                toast={toast}
              />
            )}
            {tab === "custom" && <VoiceCheck cfg={cfg} setCfg={setCfg} models={models} toast={toast} status={status} />}
            {tab === "devices" && <DevicesTab cfg={cfg} setCfg={setCfg} devices={devices} onRefresh={() => refreshDevices(true)} system={system} toast={toast} status={status} />}
            {tab === "advanced" && <AdvancedTab cfg={cfg} setCfg={setCfg} status={status} />}
            {tab === "system" && <SystemTab system={system} refreshSystem={refreshSystem} toast={toast} status={status} />}
          </div>
        </div>
      </main>

      <div className="toasts" aria-live="polite">
        {toasts.map((t) => (
          <div key={t.id} className={`toast toast-${t.level}`}>
            <span>{t.text}</span>
            <button type="button" aria-label="关闭" onClick={() => dismiss(t.id)}>
              <X size={14} />
            </button>
          </div>
        ))}
      </div>
    </div>
  );
}
