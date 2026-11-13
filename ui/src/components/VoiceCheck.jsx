import { CheckCircle2, Loader2, Mic, Pause, Play, RotateCcw, Sparkles, Star, Wand2 } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api.js";
import { Meter } from "./controls.jsx";

export const TARGETS = [
  { label: "成熟女声", value: 200 },
  { label: "少女", value: 235 },
  { label: "萝莉", value: 270 },
  { label: "男声", value: 115 },
];

function usePlayer() {
  const audio = useRef(null);
  const [playing, setPlaying] = useState("");
  useEffect(() => () => audio.current?.pause(), []);
  const toggle = useCallback(
    (url) => {
      if (!audio.current) {
        audio.current = new Audio();
        audio.current.onended = () => setPlaying("");
        audio.current.onerror = () => setPlaying("");
      }
      const a = audio.current;
      if (playing === url) {
        a.pause();
        setPlaying("");
        return;
      }
      a.src = url;
      a.currentTime = 0;
      a.play().then(() => setPlaying(url)).catch(() => setPlaying(""));
    },
    [playing],
  );
  return [playing, toggle];
}

function PlayButton({ url, playing, onToggle, label }) {
  const on = playing === url;
  return (
    <button type="button" className={`btn btn-sm ${on ? "is-on" : ""}`} onClick={() => onToggle(url)}>
      {on ? <Pause size={14} /> : <Play size={14} />}
      {label}
    </button>
  );
}

function Bar({ label, value }) {
  return (
    <span className="vc-bar" title={`${label} ${value}`}>
      <span className="vc-bar-label">{label}</span>
      <span className="vc-bar-track">
        <span className={`vc-bar-fill ${value < 50 ? "is-low" : ""}`} style={{ width: `${Math.max(3, value)}%` }} />
      </span>
    </span>
  );
}

function describeF0(hz) {
  if (!hz) return "";
  if (hz < 100) return "偏低沉的男声";
  if (hz < 145) return "常见男声";
  if (hz < 175) return "偏高的男声";
  if (hz < 210) return "中性/低女声";
  return "女声";
}

function Profile({ p, pitch, target }) {
  const rows = [
    ["你的音高", p.f0_median ? `平均 ${Math.round(p.f0_median)} Hz（${describeF0(p.f0_median)}，范围 ${Math.round(p.f0_low)}–${Math.round(p.f0_high)} Hz）` : "没测出来"],
    ["说话音量", `${Math.round(p.speech_db)} dB${p.speech_db < -32 ? "（偏小，动圈麦常见）" : ""}`],
    ["环境噪音", `${Math.round(p.noise_db)} dB · 信噪比 ${Math.round(p.snr_db)} dB${p.snr_db < 20 ? "（噪音偏大）" : ""}`],
  ];
  return (
    <div className="kv vc-kv">
      {rows.map(([k, v]) => (
        <span key={k} className="vc-kv-row">
          <span>{k}</span>
          <b>{v}</b>
        </span>
      ))}
      {pitch != null && (
        <span className="vc-kv-row">
          <span>建议设置</span>
          <b className="accent">
            音高 {pitch > 0 ? `+${pitch}` : pitch}（到 {target} Hz）· 麦克风增益 +{p.suggested_gain_db} dB
            {p.suggested_threshold != null ? ` · 静音门限 ${p.suggested_threshold} dB` : ""}
          </b>
        </span>
      )}
    </div>
  );
}

export default function VoiceCheck({ cfg, setCfg, models, toast, status }) {
  const [vc, setVc] = useState(null);
  const [target, setTarget] = useState(() => (TARGETS.some((t) => t.value === cfg.target_f0) ? cfg.target_f0 : 235));
  const [localPhase, setLocalPhase] = useState("");
  const [playing, togglePlay] = usePlayer();
  const offset = useRef(0); // server clock - local clock

  const refresh = useCallback(async () => {
    try {
      const s = await api("voicecheck");
      offset.current = s.now * 1000 - Date.now();
      setVc(s);
      return s;
    } catch {
      return null;
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const phase = localPhase || vc?.phase || "idle";
  useEffect(() => {
    if (phase !== "recording" && phase !== "analyzing" && phase !== "opening") return undefined;
    const t = setInterval(refresh, phase === "analyzing" ? 900 : 200);
    return () => clearInterval(t);
  }, [phase, refresh]);

  async function start() {
    setCfg({ target_f0: target });
    setLocalPhase("opening");
    try {
      const rec = api("voicecheck-record", { seconds: 25 });
      setTimeout(refresh, 300);
      await rec;
      setLocalPhase("");
      setVc(await api("voicecheck-analyze", { target_f0: target }));
    } catch (error) {
      setLocalPhase("");
      toast("warn", error.message);
      refresh();
    }
  }

  async function reanalyze(t) {
    setTarget(t);
    setCfg({ target_f0: t });
    try {
      setVc(await api("voicecheck-analyze", { target_f0: t }));
    } catch (error) {
      toast("warn", error.message);
    }
  }

  function apply(modelKey) {
    const p = vc.profile;
    const patch = { pitch: vc.pitch, input_gain_db: p.suggested_gain_db, target_f0: vc.target_f0 };
    if (p.suggested_threshold != null) patch.threshold = p.suggested_threshold;
    if (modelKey) patch.model = modelKey;
    setCfg(patch);
    toast("info", modelKey ? "已换成这个模型，并按你的声音调好了参数" : "已按你的声音调好音高、增益和门限");
  }

  const known = new Set(models.map((m) => m.key));
  const stamp = vc?.finished ? Math.round(vc.finished) : 0;
  const sampleUrl = (name) => `./api/voicecheck/${name}?t=${stamp}`;

  // ------------------------------------------------------------ recording
  if (phase === "opening" || phase === "recording") {
    const started = vc?.phase === "recording" && vc.started ? vc.started * 1000 - offset.current : null;
    const elapsed = started ? (Date.now() - started) / 1000 : 0;
    const lead = vc?.lead || 2;
    const quiet = !started || elapsed < lead;
    const left = Math.max(0, Math.ceil((vc?.seconds || 25) - elapsed));
    return (
      <div className="tab-body">
        <section className="card vc-stage">
          {quiet ? (
            <div className="vc-quiet">
              <Mic size={30} className="pulse" />
              <h2>{started ? "先别出声…" : "正在打开麦克风…"}</h2>
              <p className="muted">{started ? `正在听环境噪音，${Math.max(1, Math.ceil(lead - elapsed))} 秒后开始读` : "马上就好"}</p>
            </div>
          ) : (
            <>
              <div className="vc-go">
                <span className="vc-go-tag">开始读 ↓</span>
                <span className="muted small">像平时开黑那样正常说，不用刻意捏嗓子 · 还剩 {left} 秒</span>
              </div>
              <p className="vc-text">{vc?.text}</p>
            </>
          )}
          <Meter label="麦克风" db={vc?.level_db ?? -120} active={!quiet} />
          <div className="row-actions">
            <button type="button" className="btn btn-primary" disabled={quiet} onClick={() => api("voicecheck-finish", {}).catch(() => {})}>
              <CheckCircle2 size={16} /> 读完了
            </button>
          </div>
        </section>
      </div>
    );
  }

  const results = (vc?.results || []).filter((r) => known.has(r.key));
  const best = results.find((r) => !r.error);

  return (
    <div className="tab-body">
      <section className="card">
        <header className="card-head">
          <Sparkles size={17} />
          <h2>为我定制</h2>
          {vc?.phase === "done" && <span className="muted small">上次体检 {new Date(vc.finished * 1000).toLocaleString("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" })}</span>}
        </header>
        <p className="vc-intro">
          读一段话（约 20 秒），软件会根据<b>你自己的声音</b>调好音高、麦克风增益和静音门限，再用你的声音把所有模型都试一遍，按<b>咬字清晰度、音准、干净程度</b>排名，可以直接试听对比。
        </p>
        <div className="vc-targets">
          <span className="field-label">想变成</span>
          <div className="chips">
            {TARGETS.map((t) => (
              <button
                key={t.value}
                type="button"
                className={`chip ${target === t.value ? "is-active" : ""}`}
                onClick={() => (vc?.phase === "done" && vc.profile ? reanalyze(t.value) : setTarget(t.value))}
                disabled={phase === "analyzing"}
              >
                {t.label}
              </button>
            ))}
          </div>
        </div>
        <div className="row-actions">
          <button type="button" className="btn btn-primary" onClick={start} disabled={phase === "analyzing"}>
            {vc?.phase === "done" ? <RotateCcw size={16} /> : <Mic size={16} />}
            {vc?.phase === "done" ? "重新体检" : "开始体检"}
          </button>
          {status?.state === "running" && <span className="muted small">体检时会先暂停变声</span>}
        </div>
        {vc?.phase === "error" && <div className="hint is-warn">体检出错：{vc.error}</div>}
      </section>

      {vc?.profile && (
        <section className="card">
          <header className="card-head">
            <Wand2 size={17} />
            <h2>你的声音</h2>
            <PlayButton url={`./api/voicecheck/reading.wav?t=${stamp}`} playing={playing} onToggle={togglePlay} label="听原声" />
          </header>
          <Profile p={vc.profile} pitch={vc.phase === "done" ? vc.pitch : null} target={vc.target_f0} />
          {vc.phase === "done" && (
            <div className="row-actions">
              <button type="button" className="btn btn-sm" onClick={() => apply("")}>
                只应用参数（不换模型）
              </button>
            </div>
          )}
        </section>
      )}

      {phase === "analyzing" && (
        <section className="card">
          <div className="vc-progress">
            <Loader2 size={18} className="spin" />
            <div>
              <b>
                正在用你的声音试模型 {Math.min((vc?.done || 0) + 1, vc?.total || 1)}/{vc?.total || "…"}
              </b>
              <span className="muted small">{vc?.current}</span>
            </div>
          </div>
          <span className="meter-bar">
            <span className="meter-fill" style={{ width: `${((vc?.done || 0) / Math.max(1, vc?.total || 1)) * 100}%` }} />
          </span>
        </section>
      )}

      {results.length > 0 && (
        <section className="card">
          <header className="card-head">
            <Star size={17} />
            <h2>最适合你的模型</h2>
            <span className="muted small">分数只看你的声音转换后的效果</span>
          </header>
          <ol className="vc-list">
            {results.map((r, i) => {
              const model = models.find((m) => m.key === r.key);
              const using = cfg.model === r.key;
              return (
                <li key={r.key} className={`vc-item ${r === best ? "is-best" : ""}`}>
                  <span className="vc-rank">{i + 1}</span>
                  <span className="vc-cover">
                    {model?.cover ? <img src={`./api/models/${encodeURIComponent(r.key)}/cover?m=${model.mtime}`} alt="" /> : <span className="vc-cover-ph">{(r.name || "?").slice(0, 1)}</span>}
                  </span>
                  <span className="vc-main">
                    <span className="vc-name">
                      {model?.name || r.name}
                      {r === best && <span className="vc-badge">推荐</span>}
                      {using && <span className="vc-badge is-using">正在用</span>}
                    </span>
                    {r.error ? (
                      <span className="muted small">这个模型转换失败：{r.error}</span>
                    ) : (
                      <span className="vc-bars">
                        <Bar label="清晰" value={r.clarity} />
                        <Bar label="音准" value={r.pitch} />
                        <Bar label="干净" value={r.clean} />
                      </span>
                    )}
                  </span>
                  <span className="vc-score">{r.error ? "—" : r.score}</span>
                  <span className="vc-actions">
                    {!r.error && <PlayButton url={sampleUrl(r.sample)} playing={playing} onToggle={togglePlay} label="试听" />}
                    {!r.error && (
                      <button type="button" className={`btn btn-sm ${r === best ? "btn-primary" : ""}`} onClick={() => apply(r.key)}>
                        用这个
                      </button>
                    )}
                  </span>
                </li>
              );
            })}
          </ol>
          <p className="muted small">试听是离线整段转换的效果；实时变声为了低延迟会稍微差一点，但模型之间的好坏顺序是一样的。</p>
        </section>
      )}
    </div>
  );
}
