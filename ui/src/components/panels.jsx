import {
  AlertTriangle,
  CheckCircle2,
  Cpu,
  Gauge,
  Headphones,
  Loader2,
  Mic,
  MonitorSmartphone,
  Power,
  Radio,
  ShieldCheck,
  Sparkles,
  Wand2,
} from "lucide-react";
import { useState } from "react";
import { api } from "../api.js";
import { Meter, Segmented, Select, Slider, Toggle } from "./controls.jsx";
import { TARGETS } from "./VoiceCheck.jsx";

export const PRESET_INFO = {
  game: { label: "低延迟", sub: "打游戏" },
  balanced: { label: "均衡", sub: "聊天开黑" },
  quality: { label: "高音质", sub: "录音唱歌" },
};

export function estimateLatency(cfg) {
  const block = (cfg.block_time || 0.12) * 1000;
  const fade = Math.min(cfg.crossfade_time || 0.03, 0.04) * 1000;
  return Math.round(block + fade + 10 + 25 + (cfg.min_buffer_ms || 15) + 20);
}

const STATE_TEXT = {
  idle: "未启动",
  loading: "正在加载模型…",
  ready: "准备就绪",
  starting: "正在启动…",
  running: "变声中",
  error: "出错了",
};

const HEALTH_CAUSE = {
  gpu: "显卡被占满，算不过来（开着「防卡顿自动调节」会自动放宽）",
  device: "声卡驱动送音不稳（麦克风或虚拟声卡在用兼容模式）",
  buffer: "缓冲太小，已自动加大",
};

export function PowerPanel({ status, cfg, busy, onToggle, modelName }) {
  const running = status?.state === "running";
  const starting = busy === "toggle" || status?.state === "starting";
  const lat = status?.latency;
  const levels = status?.levels;
  const load = lat?.load ?? 0;
  const loadPct = Math.min(100, Math.round(load * 100));
  return (
    <section className={`card power ${running ? "is-running" : ""}`}>
      <button type="button" className="power-button" onClick={onToggle} disabled={starting || status?.state === "loading"} aria-pressed={running}>
        <span className="power-ring" />
        {starting ? <Loader2 size={40} className="spin" /> : <Power size={40} strokeWidth={2.4} />}
        <span className="power-label">{running ? "停止" : starting ? "启动中" : "开始变声"}</span>
      </button>
      <div className="power-info">
        <div className="power-state">
          <span className={`dot dot-${status?.state || "idle"}`} />
          {STATE_TEXT[status?.state] || "连接中…"}
          {status?.model_loading && <span className="muted"> · 正在加载 {status.model_loading}</span>}
        </div>
        <div className="latency">
          <span className="latency-num">{running && lat ? lat.total_ms : `≈${estimateLatency(cfg)}`}</span>
          <span className="latency-unit">ms 延迟</span>
        </div>
        {running && lat ? (
          <div className="latency-break" title="分块+交叉淡化 / 推理 / 缓冲 / 声卡">
            分块 {lat.algorithm_ms} · 推理 {Math.round(lat.infer_ms)} · 缓冲 {Math.round(lat.buffer_ms)} · 声卡 {Math.round(lat.device_ms)}
          </div>
        ) : (
          <div className="latency-break">{modelName ? `模型：${modelName}` : "先在右边选一个声音模型"}</div>
        )}
        <div className="meters">
          <Meter label="麦克风" db={levels?.in_db ?? -120} active={running && levels?.gate} />
          <Meter label="变声后" db={levels?.out_db ?? -120} active={running} />
          <div className="meter">
            <span className="meter-label">算力占用</span>
            <span className="meter-bar">
              <span className={`meter-fill load ${load > 0.85 ? "is-hot" : load > 0.6 ? "is-warm" : ""}`} style={{ width: `${running ? loadPct : 0}%` }} />
            </span>
          </div>
        </div>
      </div>
      {status?.adapt_note && (
        <div className="adapt-note">
          <Gauge size={14} /> {status.adapt_note}
        </div>
      )}
      {running && status?.health?.glitches_60s > 0 && (
        <div className="adapt-note is-warn">
          <AlertTriangle size={14} /> 最近 1 分钟断音 {status.health.glitches_60s} 次
          {HEALTH_CAUSE[status.health.cause] ? ` · ${HEALTH_CAUSE[status.health.cause]}` : ""}
        </div>
      )}
    </section>
  );
}

const PITCH_CHIPS = [
  { label: "男→女", value: 10 },
  { label: "男→少女", value: 13 },
  { label: "原调", value: 0 },
  { label: "女→男", value: -10 },
];


function RecordBox({ cfg, setCfg, toast, running }) {
  const [rec, setRec] = useState(null);

  async function record() {
    setRec({ phase: "rec" });
    try {
      const res = await api("record", { seconds: 6 });
      setRec({ phase: "done", ...res });
    } catch (error) {
      setRec(null);
      toast("warn", error.message);
    }
  }

  const tips = [];
  if (rec?.phase === "done") {
    if (rec.speech_db < -38) tips.push(`麦克风音量偏小（说话约 ${Math.round(rec.speech_db)} dB）。动圈麦要离嘴近一点，或去「为我定制」读一段话，自动调好增益和门限。`);
    if (rec.cut_ratio > 0.08) tips.push(`有 ${Math.round(rec.cut_ratio * 100)}% 的说话声低于静音门限，轻的字和尾音会被吞掉。`);
    if (rec.f0_in && rec.f0_out && rec.f0_out < 185 && cfg.pitch > 0) tips.push(`变声后音高只有 ${Math.round(rec.f0_out)} Hz，对女声模型偏低，声音会发闷、咬字糊。试试把音高再加 3~5。`);
  }

  return (
    <div className="record">
      <div className="record-head">
        <button type="button" className="btn btn-sm" onClick={record} disabled={!running || rec?.phase === "rec"}>
          {rec?.phase === "rec" ? <Loader2 size={14} className="spin" /> : <Mic size={14} />}
          {rec?.phase === "rec" ? "正在录，正常说几句话…" : "录 6 秒听听效果"}
        </button>
        <span className="muted small">{running ? "录完回放，比边说边听更容易听出口齿问题" : "开始变声后可以用"}</span>
      </div>
      {rec?.phase === "done" && (
        <div className="record-result">
          <div className="players">
            <label>
              原声
              <audio controls src={`./api/recordings/${rec.id}-in.wav`} />
            </label>
            <label>
              变声后
              <audio controls src={`./api/recordings/${rec.id}-out.wav`} />
            </label>
          </div>
          <div className="record-stats muted small">
            说话音量 {Math.round(rec.speech_db)} dB · 底噪 {Math.round(rec.noise_db)} dB · 门限 {rec.threshold} dB
            {rec.f0_in ? ` · 你的音高 ${Math.round(rec.f0_in)} Hz` : ""}
            {rec.f0_out ? ` → 变声后 ${Math.round(rec.f0_out)} Hz` : ""}
          </div>
          {tips.map((t) => (
            <div key={t} className="hint is-warn">
              {t}
            </div>
          ))}
          {rec.suggested_threshold != null && rec.suggested_threshold !== cfg.threshold && (
            <button type="button" className="btn btn-sm" onClick={() => setCfg({ threshold: rec.suggested_threshold })}>
              门限改成建议的 {rec.suggested_threshold} dB
            </button>
          )}
        </div>
      )}
    </div>
  );
}

export function VoicePanel({ cfg, setCfg, toast, status }) {
  const [calib, setCalib] = useState(null);

  async function calibrate() {
    setCalib({ phase: "listen" });
    try {
      const res = await api("calibrate", { seconds: 4 });
      setCalib({ phase: "done", ...res });
    } catch (error) {
      setCalib(null);
      toast("warn", error.message);
    }
  }

  return (
    <section className="card">
      <header className="card-head">
        <Wand2 size={17} />
        <h2>声音</h2>
      </header>
      <Slider
        label="音高"
        value={cfg.pitch}
        min={-24}
        max={24}
        step={1}
        accent
        format={(v) => (v > 0 ? `+${v}` : `${v}`) + " 半音"}
        onChange={(v) => setCfg({ pitch: v })}
      />
      <div className="chips">
        {PITCH_CHIPS.map((c) => (
          <button key={c.label} type="button" className={`chip ${cfg.pitch === c.value ? "is-active" : ""}`} onClick={() => setCfg({ pitch: c.value })}>
            {c.label}
          </button>
        ))}
        <button type="button" className="chip chip-accent" onClick={calibrate} disabled={calib?.phase === "listen"}>
          <Sparkles size={13} /> 测我的音高
        </button>
      </div>
      {calib && (
        <div className="calib">
          {calib.phase === "listen" ? (
            <>
              <Mic size={16} className="pulse" /> 用平常的声音说几句话（4 秒）…
            </>
          ) : (
            <div className="calib-result">
              <div>
                你的平均音高 <b>{Math.round(calib.median_hz)} Hz</b>，目标
                <select value={cfg.target_f0} onChange={(e) => setCfg({ target_f0: Number(e.target.value) })}>
                  {TARGETS.map((t) => (
                    <option key={t.value} value={t.value}>
                      {t.label}（{t.value} Hz）
                    </option>
                  ))}
                </select>
              </div>
              <div className="calib-actions">
                <span>
                  建议音高 <b className="accent">{suggest(calib.median_hz, cfg.target_f0)}</b>
                </span>
                <button type="button" className="btn btn-primary btn-sm" onClick={() => (setCfg({ pitch: suggestValue(calib.median_hz, cfg.target_f0) }), setCalib(null))}>
                  用这个
                </button>
                <button type="button" className="btn btn-sm" onClick={() => setCalib(null)}>
                  关闭
                </button>
              </div>
            </div>
          )}
        </div>
      )}
      <Slider
        label="音色（共振峰）"
        value={cfg.formant}
        min={-4}
        max={4}
        step={0.1}
        format={(v) => (v > 0 ? `+${v.toFixed(1)}` : v.toFixed(1))}
        hint="往右更细更年轻，往左更厚更成熟。男变女可以试 +0.5 ~ +1.5，太大会发虚。"
        onChange={(v) => setCfg({ formant: Math.round(v * 10) / 10 })}
      />
      <RecordBox cfg={cfg} setCfg={setCfg} toast={toast} running={status?.state === "running"} />
    </section>
  );
}

function suggestValue(median, target) {
  return Math.max(-24, Math.min(24, Math.round(12 * Math.log2(target / median))));
}
function suggest(median, target) {
  const v = suggestValue(median, target);
  return v > 0 ? `+${v}` : `${v}`;
}

export function ModePanel({ cfg, setCfg, status }) {
  return (
    <section className="card">
      <header className="card-head">
        <Gauge size={17} />
        <h2>模式</h2>
        <span className="muted small">{cfg.preset === "custom" ? "自定义参数" : `≈${estimateLatency(cfg)} ms`}</span>
      </header>
      <Segmented
        value={cfg.preset}
        onChange={(v) => setCfg({ preset: v })}
        options={Object.entries(PRESET_INFO).map(([value, info]) => ({ value, label: info.label, sub: info.sub }))}
      />
      <Toggle
        label="防卡顿自动调节"
        hint="显卡被游戏占满时自动换更轻的算法，宁可多一点延迟也不断音；空闲后自动恢复。"
        checked={cfg.auto_tune}
        onChange={(v) => setCfg({ auto_tune: v })}
      />
      {status?.active && status.state === "running" && (
        <div className="active-line">
          当前：分块 {status.active.block_time} ms · 上下文 {status.active.extra_time} ms · 音高算法 {String(status.active.f0_method).toUpperCase()}
        </div>
      )}
    </section>
  );
}

// The game must record from the other end of the cable the voice is sent to:
// "CABLE Input (VB-Audio Virtual Cable)" -> "CABLE Output (VB-Audio Virtual Cable)",
// "CABLE-A Input (VB-Audio Cable A)" -> "CABLE-A Output (VB-Audio Cable A)",
// "伴侣扬声器 (VB-Audio Cable A)" -> "伴侣麦克风 (VB-Audio Cable A)".
function pairedCableInput(outputName, inputs) {
  const virtual = inputs.filter((d) => d.virtual);
  const tag = /\(([^)]*)\)\s*$/.exec(outputName || "")?.[1];
  const same = tag ? virtual.filter((d) => d.name.endsWith(`(${tag})`)) : [];
  return same[0] || virtual.find((d) => d.name.includes("伴侣") || /cable(-[a-d])? output/i.test(d.name)) || null;
}

function deviceOptions(list) {
  return list.map((d) => ({ value: d.name, label: d.name + (d.virtual ? "  · 虚拟声卡" : "") }));
}

function probeHint(status, key, name, fallback) {
  const d = status?.selftest?.devices?.[key];
  if (!d || d.name !== name) return { text: fallback, warn: false };
  if (!d.ok) return { text: d.error, warn: true };
  return { text: `${fallback}（自检正常：${d.api}）`, warn: false };
}

export function DevicesTab({ cfg, setCfg, devices, onRefresh, system, toast, status }) {
  const inputs = devices.inputs || [];
  const outputs = devices.outputs || [];
  const input = inputs.find((d) => d.name === cfg.input_device);
  const output = outputs.find((d) => d.name === cfg.output_device);
  const hasCable = outputs.some((d) => d.virtual);
  const cableMic = pairedCableInput(output?.name || cfg.output_device, devices.inputs || []);

  async function installDriver() {
    try {
      await api("install-driver", {});
      toast("info", "已打开声卡驱动安装程序，装完后重启电脑再打开本软件");
    } catch (error) {
      toast("warn", error.message);
    }
  }

  return (
    <div className="tab-body">
      <section className="card">
        <header className="card-head">
          <Radio size={17} />
          <h2>声音从哪来、到哪去</h2>
          <button type="button" className="btn btn-sm btn-ghost" onClick={onRefresh}>
            刷新设备
          </button>
        </header>
        <div className="grid-2">
          <Select
            icon={Mic}
            label="麦克风"
            value={cfg.input_device}
            options={deviceOptions(inputs.filter((d) => !d.virtual))}
            onChange={(v) => setCfg({ input_device: v })}
            warn={input?.broadcast || probeHint(status, "input_device", cfg.input_device, "").warn}
            hint={
              input?.broadcast
                ? "NVIDIA Broadcast 会额外占用显卡、增加 20~40 ms 延迟。打游戏建议直接选真实麦克风，用「高级」里的「静音门限」去底噪。"
                : probeHint(status, "input_device", cfg.input_device, "选你实际说话的那个麦克风。").text
            }
          />
          <Select
            icon={MonitorSmartphone}
            label="输出到游戏"
            value={cfg.output_device}
            options={deviceOptions(outputs)}
            onChange={(v) => setCfg({ output_device: v })}
            warn={(output && !output.virtual) || probeHint(status, "output_device", cfg.output_device, "").warn}
            hint={
              output && !output.virtual
                ? "这是真实扬声器/耳机，游戏里的队友听不到。一般选带「虚拟声卡」的那个。"
                : probeHint(status, "output_device", cfg.output_device, "选虚拟声卡，名字里带 CABLE Input 或 VB-Audio 的那个。").text
            }
          />
        </div>
        {!hasCable && (
          <div className="callout warn">
            <AlertTriangle size={16} />
            <div>
              <b>没检测到虚拟声卡。</b> 游戏需要从虚拟声卡拿到变声后的声音。
              {system?.driver_installer ? (
                <button type="button" className="btn btn-sm" onClick={installDriver}>
                  安装 VB-Cable 驱动
                </button>
              ) : (
                <span> 请安装 VB-Audio Virtual Cable。</span>
              )}
            </div>
          </div>
        )}
      </section>

      <section className="card">
        <header className="card-head">
          <Headphones size={17} />
          <h2>监听（自己听）</h2>
        </header>
        <Toggle
          label="同时输出到耳机"
          hint="变声后的声音同时送到游戏和你的耳机，不用来回切换。"
          checked={cfg.monitor_enabled}
          onChange={(v) => setCfg({ monitor_enabled: v })}
        />
        <div className="grid-2">
          <Select
            icon={Headphones}
            label="监听设备"
            value={cfg.monitor_device}
            options={deviceOptions(outputs.filter((d) => !d.virtual))}
            onChange={(v) => setCfg({ monitor_device: v })}
          />
          <Slider label="监听音量" value={Math.round(cfg.monitor_volume * 100)} min={0} max={150} step={5} unit="%" onChange={(v) => setCfg({ monitor_volume: v / 100 })} />
        </div>
      </section>

      <section className="card guide">
        <header className="card-head">
          <ShieldCheck size={17} />
          <h2>游戏里这样设置（三角洲行动 / 其它游戏通用）</h2>
        </header>
        <ol>
          <li>
            这里的「输出到游戏」选 <b>{output?.virtual ? output.name : "CABLE Input (VB-Audio …)"}</b>。
          </li>
          <li>
            游戏 → 设置 → 声音/语音 → <b>麦克风（输入设备）</b>选 <b>{cableMic ? cableMic.name : "CABLE Output (VB-Audio …)"}</b>，不要选你的真实麦克风。
          </li>
          <li>想听自己的效果就打开上面的「监听」。说话时「变声后」的音量条在跳，就说明队友能听到。</li>
          <li>显卡吃满时，在游戏里把帧率限制到比平时低 10~20%，变声会更稳。</li>
        </ol>
      </section>
    </div>
  );
}

export function AdvancedTab({ cfg, setCfg, status }) {
  const running = status?.state === "running";
  const inDb = running ? status?.levels?.in_db : null;
  const hasIndex = Boolean(status?.model_info?.index);
  return (
    <div className="tab-body">
      <section className="card">
        <header className="card-head">
          <Mic size={17} />
          <h2>输入处理</h2>
        </header>
        <Slider
          label="静音门限"
          value={cfg.threshold}
          min={-90}
          max={-20}
          step={1}
          unit=" dB"
          marker={inDb}
          format={(v) => (v <= -89 ? "关闭" : `${v} dB`)}
          hint="比这个小的声音（键盘、风扇、底噪）不变声。白线是你此刻的音量：说话时白线要在门限右边。"
          onChange={(v) => setCfg({ threshold: v })}
        />
        <div className="grid-2">
          <Slider label="麦克风增益" value={cfg.input_gain_db} min={-20} max={20} step={1} unit=" dB" onChange={(v) => setCfg({ input_gain_db: v })} />
          <Slider label="输出音量" value={cfg.output_gain_db} min={-20} max={20} step={1} unit=" dB" onChange={(v) => setCfg({ output_gain_db: v })} />
        </div>
        <Slider
          label="响度跟随"
          value={Math.round((1 - cfg.rms_mix_rate) * 100)}
          min={0}
          max={100}
          step={5}
          unit="%"
          hint="100% = 变声后的音量完全跟着你说话的大小走（推荐）；0% = 用模型自己的音量。"
          onChange={(v) => setCfg({ rms_mix_rate: 1 - v / 100 })}
        />
        <Slider
          label="索引强度"
          value={Math.round(cfg.index_rate * 100)}
          min={0}
          max={100}
          step={5}
          unit="%"
          disabled={!hasIndex}
          hint={hasIndex ? "越高越像目标音色、越少带出你本来的声音，但咬字可能变糊。30~60% 常用。" : "当前模型没有 .index 文件，用不了。"}
          onChange={(v) => setCfg({ index_rate: v / 100 })}
        />
      </section>
      <section className="card">
        <header className="card-head">
          <Cpu size={17} />
          <h2>延迟与算力</h2>
          <span className="muted small">改了就是「自定义」</span>
        </header>
        <Slider label="分块大小" value={Math.round(cfg.block_time * 1000)} min={60} max={400} step={10} unit=" ms" hint="越小延迟越低，但每秒要算的次数越多。显卡好可以 80~120。" onChange={(v) => setCfg({ block_time: v / 1000 })} />
        <Slider label="上下文长度" value={Math.round(cfg.extra_time * 1000)} min={200} max={2000} step={50} unit=" ms" hint="给模型看的前文。越长咬字越稳、越费显卡，不增加延迟。" onChange={(v) => setCfg({ extra_time: v / 1000 })} />
        <Slider label="交叉淡化" value={Math.round(cfg.crossfade_time * 1000)} min={10} max={80} step={5} unit=" ms" hint="块与块之间的过渡，听到咔哒声就调大一点。超过 40 ms 只增加延迟。" onChange={(v) => setCfg({ crossfade_time: v / 1000 })} />
        <Slider label="最小缓冲" value={cfg.min_buffer_ms} min={5} max={80} step={5} unit=" ms" hint="防断音的安全余量下限，会自动在它之上调节。" onChange={(v) => setCfg({ min_buffer_ms: v })} />
        <div className="field">
          <span className="field-label">音高算法</span>
          <Segmented
            value={cfg.f0_method}
            onChange={(v) => setCfg({ f0_method: v })}
            options={[
              { value: "rmvpe", label: "RMVPE", sub: "最准，男变女首选" },
              { value: "fcpe", label: "FCPE", sub: "更快更省" },
            ]}
          />
        </div>
      </section>
    </div>
  );
}

export function SystemTab({ system, refreshSystem, toast, status }) {
  async function run(action, body, okText) {
    try {
      const res = await api(action, body || {});
      if (okText) toast("info", okText);
      await refreshSystem();
      return res;
    } catch (error) {
      toast("warn", error.message);
      return null;
    }
  }

  const gp = system?.gpu_priority;
  const st = status?.selftest;
  const rows = st
    ? [
        ["显卡推理", st.gpu ? (st.gpu.ok ? `正常，每块 ${st.gpu.infer_ms} ms（分块 ${st.gpu.block_ms} ms）` : st.gpu.error) : "未测（没有选模型）", st.gpu ? st.gpu.ok : null],
        ...[
          ["input_device", "麦克风"],
          ["output_device", "输出到游戏"],
          ["monitor_device", "监听设备"],
        ].map(([key, label]) => {
          const d = st.devices?.[key];
          return [label, d ? (d.ok ? `正常（${d.api || "已连接"}）` : d.error) : "未设置", d ? d.ok : null];
        }),
      ]
    : [];
  return (
    <div className="tab-body">
      <section className="card">
        <header className="card-head">
          <CheckCircle2 size={17} />
          <h2>自检</h2>
          <span className="muted small">{st ? st.time : "启动后自动检查"}</span>
          <button type="button" className="btn btn-sm" disabled={status?.state === "running"} onClick={() => run("selftest", {}, "自检完成")}>
            重新自检
          </button>
        </header>
        {st ? (
          <ul className="checks">
            {rows.map(([label, text, ok]) => (
              <li key={label} className={ok === true ? "ok" : ok === false ? "bad" : ""}>
                <span className="check-dot" />
                <span className="check-label">{label}</span>
                <span className="check-text">{text}</span>
              </li>
            ))}
          </ul>
        ) : (
          <p className="muted">软件启动、模型加载完以后会自动检查显卡和声音设备。</p>
        )}
      </section>
      <section className="card">
        <header className="card-head">
          <Cpu size={17} />
          <h2>性能</h2>
        </header>
        <div className="kv">
          <span>推理设备</span>
          <b>
            {system?.device || status?.device}
            {system?.half ? " · 半精度" : ""}
          </b>
          <span>显卡优先级</span>
          <b className={gp === "high" || gp === "realtime" ? "ok" : "warnc"}>{gp === "high" || gp === "realtime" ? "高（游戏抢不走）" : gp ? `${gp === "above_normal" ? "较高" : gp}（需要管理员才能更高）` : "未设置"}</b>
          <span>CPU 优先级</span>
          <b>{system?.boost?.cpu_priority ? "高" : "普通"}</b>
        </div>
        {!(gp === "high" || gp === "realtime") && system?.platform === "win32" && (
          <div className="callout">
            <ShieldCheck size={16} />
            <div>
              游戏把显卡吃满时，管理员权限能让变声的显卡任务优先执行（和 OBS 录屏防卡同一个办法）。
              <button type="button" className="btn btn-sm btn-primary" onClick={() => run("admin-restart", {}, "正在以管理员身份重启…")}>
                以管理员身份重启
              </button>
            </div>
          </div>
        )}
      </section>

      <section className="card">
        <header className="card-head">
          <CheckCircle2 size={17} />
          <h2>安装</h2>
        </header>
        <div className="kv">
          <span>软件位置</span>
          <b className="mono">{system?.root}</b>
          <span>版本</span>
          <b>{system?.version}</b>
        </div>
        <div className="row-actions">
          <button type="button" className="btn" onClick={() => run("shortcut", {}, "桌面快捷方式已创建")}>
            {system?.shortcut ? "重新创建桌面快捷方式" : "创建桌面快捷方式"}
          </button>
          <button type="button" className="btn" onClick={() => run("open-folder", { which: "models" })}>
            打开模型文件夹
          </button>
          <button type="button" className="btn" onClick={() => run("open-folder", { which: "logs" })}>
            打开日志
          </button>
        </div>
      </section>

    </div>
  );
}
