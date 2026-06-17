import {
  Activity,
  Cable,
  FolderOpen,
  Gauge,
  Mic,
  Play,
  Power,
  RefreshCw,
  Save,
  SlidersHorizontal,
  Square,
  Upload,
  Volume2,
  WandSparkles,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

const emptyDevices = {
  hostapis: [],
  input_devices: [],
  output_devices: [],
  device_details: null,
};

const emptyVirtualAudio = {
  installed: false,
  downloadUrl: "https://vb-audio.com/Cable/",
  windowsDevices: [],
  backendDevices: [],
};

const fallbackConfig = {
  audio: {
    sr_type: "sr_device",
    block_time: 0.42,
    crossfade_time: 0.05,
    extra_time: 0.45,
    threshold: -50,
    rms_mix_rate: 0,
    input_noise_reduce: false,
    output_noise_reduce: false,
    use_phase_vocoder: false,
  },
  model: {
    pth_path: "",
    index_path: "",
    pitch: 0,
    formant: 0,
    index_rate: 0,
    f0_method: "fcpe",
    n_cpu: 4,
  },
  device: {
    hostapi: "Windows WASAPI",
    input_device: "",
    output_device: "",
    wasapi_exclusive: false,
  },
};

async function api(path, options = {}) {
  const isFormData = options.formData instanceof FormData;
  const response = await fetch(`/api${path}`, {
    method: options.method || "GET",
    headers: !isFormData && options.body ? { "Content-Type": "application/json" } : undefined,
    body: isFormData ? options.formData : options.body ? JSON.stringify(options.body) : undefined,
  });

  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(payload.error || `API ${response.status}`);
  }
  return payload;
}

function mergeConfig(config) {
  return {
    audio: { ...fallbackConfig.audio, ...(config?.audio || {}) },
    model: { ...fallbackConfig.model, ...(config?.model || {}) },
    device: { ...fallbackConfig.device, ...(config?.device || {}) },
  };
}

function Toggle({ checked, onChange, label, help }) {
  return (
    <span className="toggle-wrap">
      <button
        className={`toggle ${checked ? "is-on" : ""}`}
        type="button"
        onClick={() => onChange(!checked)}
        aria-pressed={checked}
      >
        <span />
        {label}
      </button>
      {help && <small>{help}</small>}
    </span>
  );
}

function Slider({ label, value, min, max, step, unit, help, onChange }) {
  return (
    <label className="slider">
      <span className="slider-top">
        <span>{label}</span>
        <strong>
          {value}
          {unit || ""}
        </strong>
      </span>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(event) => onChange(Number(event.target.value))}
      />
      {help && <small className="help-text">{help}</small>}
    </label>
  );
}

function FieldSelect({ icon: Icon, label, value, options, help, includeCurrent = true, onChange }) {
  const fullOptions = includeCurrent && value && !options.includes(value) ? [value, ...options] : options;
  return (
    <label className="field">
      <span>
        <Icon size={16} />
        {label}
      </span>
      <select value={value || ""} onChange={(event) => onChange(event.target.value)}>
        <option value="">未选择</option>
        {fullOptions.map((option) => (
          <option value={option} key={option}>
            {option}
          </option>
        ))}
      </select>
      {help && <small className="help-text">{help}</small>}
    </label>
  );
}

function StatusPill({ status, backend }) {
  const running = Boolean(status?.running);
  const reachable = Boolean(backend?.reachable);

  if (!reachable) {
    return <span className="pill pill-warn">本地引擎未连接</span>;
  }

  return <span className={`pill ${running ? "pill-run" : "pill-idle"}`}>{running ? "变声中" : "待机"}</span>;
}

function findDeviceDetail(devices, hostapiName, deviceName, direction) {
  const details = devices.device_details;
  if (!details || !hostapiName || !deviceName) return null;

  const hostapiIndex = details.hostapis?.findIndex((hostapi) => hostapi.name === hostapiName);
  if (hostapiIndex < 0) return null;

  const channelKey = direction === "input" ? "max_input_channels" : "max_output_channels";
  return (
    details.devices?.find((device) => {
      return device.hostapi === hostapiIndex && device.name === deviceName && Number(device[channelKey] || 0) > 0;
    }) || null
  );
}

function getDeviceSampleRate(devices, hostapiName, deviceName, direction) {
  const detail = findDeviceDetail(devices, hostapiName, deviceName, direction);
  const rate = Number(detail?.default_samplerate || 0);
  return rate ? Math.round(rate) : null;
}

function getHostapiDevices(devices, hostapiName, direction) {
  const details = devices.device_details;
  if (!details || !hostapiName) {
    return direction === "input" ? devices.input_devices : devices.output_devices;
  }

  const hostapiIndex = details.hostapis?.findIndex((hostapi) => hostapi.name === hostapiName);
  if (hostapiIndex < 0) {
    return direction === "input" ? devices.input_devices : devices.output_devices;
  }

  const channelKey = direction === "input" ? "max_input_channels" : "max_output_channels";
  return (
    details.devices
      ?.filter((device) => device.hostapi === hostapiIndex && Number(device[channelKey] || 0) > 0)
      .map((device) => device.name) || []
  );
}

function findCompatibleRoute(devices, preferredHostapi, inputDeviceName, outputDeviceName) {
  const details = devices.device_details;
  if (!details || !inputDeviceName || !outputDeviceName) return null;

  const hostapiNames = details.hostapis?.map((hostapi) => hostapi.name) || [];
  const order = [
    preferredHostapi,
    "Windows DirectSound",
    "MME",
    "Windows WASAPI",
    "Windows WDM-KS",
    ...hostapiNames,
  ].filter(Boolean);

  for (const hostapiName of [...new Set(order)]) {
    const inputRate = getDeviceSampleRate(devices, hostapiName, inputDeviceName, "input");
    const outputRate = getDeviceSampleRate(devices, hostapiName, outputDeviceName, "output");

    if (inputRate && outputRate && inputRate === outputRate) {
      return {
        hostapi: hostapiName,
        sampleRate: inputRate,
        changed: hostapiName !== preferredHostapi,
      };
    }
  }

  return null;
}

export default function App() {
  const [health, setHealth] = useState(null);
  const [status, setStatus] = useState(null);
  const [models, setModels] = useState([]);
  const [devices, setDevices] = useState(emptyDevices);
  const [config, setConfig] = useState(fallbackConfig);
  const [virtualAudio, setVirtualAudio] = useState(emptyVirtualAudio);
  const [voiceRootInput, setVoiceRootInput] = useState("");
  const [busy, setBusy] = useState("");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [monitorEnabled, setMonitorEnabled] = useState(false);
  const [monitorOutputDevice, setMonitorOutputDevice] = useState("");
  const modelInputRef = useRef(null);

  const selectedModel = useMemo(
    () =>
      models.find((model) => {
        const current = String(config.model.pth_path || "").toLowerCase();
        return (
          current === String(model.modelPath || "").toLowerCase() ||
          current === String(model.modelFile || "").toLowerCase() ||
          current.endsWith(`\\${String(model.modelFile || "").toLowerCase()}`) ||
          current.endsWith(`/${String(model.modelFile || "").toLowerCase()}`) ||
          current === String(model.id || "").toLowerCase()
        );
      }),
    [models, config.model.pth_path],
  );

  const displayModelName = selectedModel?.name || String(config.model.pth_path || "").split(/[\\/]/).pop() || "未选择";

  const hostapiInputDevices = useMemo(
    () => getHostapiDevices(devices, config.device.hostapi, "input"),
    [devices, config.device.hostapi],
  );

  const hostapiOutputDevices = useMemo(
    () => getHostapiDevices(devices, config.device.hostapi, "output"),
    [devices, config.device.hostapi],
  );

  const inputSampleRate = useMemo(
    () => getDeviceSampleRate(devices, config.device.hostapi, config.device.input_device, "input"),
    [devices, config.device.hostapi, config.device.input_device],
  );

  const compatibleMonitorOutputDevices = useMemo(() => {
    return hostapiOutputDevices;
  }, [hostapiOutputDevices]);

  const compatibleMonitorOutputDevice = compatibleMonitorOutputDevices.includes(monitorOutputDevice)
    ? monitorOutputDevice
    : compatibleMonitorOutputDevices[0] || "";

  const monitorRoute = useMemo(
    () =>
      findCompatibleRoute(
        devices,
        config.device.hostapi,
        config.device.input_device,
        compatibleMonitorOutputDevice,
      ),
    [devices, config.device.hostapi, config.device.input_device, compatibleMonitorOutputDevice],
  );

  const monitorOutputSampleRate = useMemo(
    () => getDeviceSampleRate(devices, config.device.hostapi, compatibleMonitorOutputDevice, "output"),
    [devices, config.device.hostapi, compatibleMonitorOutputDevice],
  );

  const monitorHelp = monitorRoute?.changed
    ? `${compatibleMonitorOutputDevice} 会自动用 ${monitorRoute.hostapi} 兼容模式启动；只影响本次启动。`
    : inputSampleRate && monitorOutputSampleRate && inputSampleRate !== monitorOutputSampleRate
      ? `${monitorOutputSampleRate} Hz，与输入 ${inputSampleRate} Hz 不一致；请换设备或统一 Windows 采样率。`
    : inputSampleRate
      ? `${inputSampleRate} Hz 直连；只影响本次启动，不保存。`
      : "只影响本次启动，不写入永久配置。";

  const effectiveConfig = useMemo(() => {
    if (!monitorEnabled || !compatibleMonitorOutputDevice) return config;
    return {
      ...config,
      device: {
        ...config.device,
        hostapi: monitorRoute?.hostapi || config.device.hostapi,
        output_device: compatibleMonitorOutputDevice,
      },
    };
  }, [config, monitorEnabled, compatibleMonitorOutputDevice, monitorRoute]);

  async function refreshBase() {
    const [healthPayload, configPayload, modelsPayload, virtualAudioPayload] = await Promise.all([
      api("/health"),
      api("/config"),
      api("/models"),
      api("/virtual-audio"),
    ]);
    setHealth(healthPayload);
    setVoiceRootInput(healthPayload.voiceRoot || "");
    setConfig(mergeConfig(configPayload));
    setModels(modelsPayload.models || []);
    setVirtualAudio(virtualAudioPayload || emptyVirtualAudio);
    if (healthPayload.backend?.reachable) {
      await refreshRuntime();
    }
  }

  async function refreshRuntime() {
    const [statusPayload, devicesPayload, healthPayload] = await Promise.all([
      api("/status"),
      api("/devices"),
      api("/health"),
    ]);
    setStatus(statusPayload);
    setDevices(devicesPayload || emptyDevices);
    setHealth(healthPayload);
    setVoiceRootInput(healthPayload.voiceRoot || "");
  }

  async function runAction(label, action, success) {
    setBusy(label);
    setError("");
    setMessage("");
    try {
      await action();
      if (success) setMessage(success);
    } catch (actionError) {
      setError(actionError.message);
    } finally {
      setBusy("");
    }
  }

  useEffect(() => {
    refreshBase().catch((loadError) => setError(loadError.message));
  }, []);

  useEffect(() => {
    if (!health?.backend?.reachable) return undefined;
    const timer = window.setInterval(() => {
      api("/status")
        .then(setStatus)
        .catch(() => {});
    }, 2000);
    return () => window.clearInterval(timer);
  }, [health?.backend?.reachable]);

  useEffect(() => {
    if (!compatibleMonitorOutputDevices.length) {
      if (monitorOutputDevice) setMonitorOutputDevice("");
      return;
    }

    if (compatibleMonitorOutputDevices.includes(monitorOutputDevice)) return;

    const preferred = compatibleMonitorOutputDevices.find((device) => !/vb-audio|cable|伴侣/i.test(device));
    setMonitorOutputDevice(preferred || compatibleMonitorOutputDevices[0] || "");
  }, [compatibleMonitorOutputDevices, monitorOutputDevice]);

  function updateSection(section, key, value) {
    setConfig((current) => ({
      ...current,
      [section]: {
        ...current[section],
        [key]: value,
      },
    }));
  }

  function selectModel(model) {
    setConfig((current) => ({
      ...current,
      model: {
        ...current.model,
        pth_path: model.modelPath || model.modelFile,
      },
    }));
  }

  async function importModelFile(file) {
    if (!file.name.toLowerCase().endsWith(".pth")) {
      throw new Error("只支持 .pth 模型文件");
    }

    const formData = new FormData();
    formData.append("model", file);

    const payload = await api("/models/import", { method: "POST", formData });
    const modelsPayload = await api("/models");
    setModels(modelsPayload.models || []);

    if (payload.model) {
      selectModel(payload.model);
    }
  }

  async function openVirtualAudioInstaller() {
    const payload = await api("/virtual-audio/open-installer", { method: "POST" });
    setVirtualAudio(payload || emptyVirtualAudio);
  }

  function handleModelImport(event) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;

    runAction("import", () => importModelFile(file), "模型已导入并选中");
  }

  async function toggleEngine() {
    if (!backendReady) {
      await api("/backend/ensure", { method: "POST" });
      await refreshRuntime();
      setMessage("本地引擎已连接");
      return;
    }

    if (running) {
      await api("/stop", { method: "POST" });
    }

    const payload = await api("/backend/shutdown", { method: "POST" });
    setStatus(null);
    setDevices(emptyDevices);
    await new Promise((resolve) => setTimeout(resolve, 550));
    await refreshBase();

    if (payload.stopped) {
      setMessage("本地引擎已关闭");
      return;
    }

    setMessage("变声已停止；当前引擎不是本应用启动的，所以没有强制关闭。");
  }

  async function applyVoiceRoot() {
    const payload = await api("/settings/voice-root", {
      method: "POST",
      body: { voiceRoot: voiceRootInput.trim() },
    });

    setHealth(payload.health);
    setConfig(mergeConfig(payload.config));
    setModels(payload.models || []);
    setStatus(null);
    setDevices(emptyDevices);
    setVirtualAudio(await api("/virtual-audio"));
    setVoiceRootInput(payload.health?.voiceRoot || voiceRootInput.trim());
  }

  const backendReady = Boolean(health?.backend?.reachable);
  const running = Boolean(status?.running);
  const voiceRootReady = Boolean(health?.paths?.python);
  const virtualAudioName =
    virtualAudio.backendDevices?.find((device) => device.output)?.name ||
    virtualAudio.windowsDevices?.[0]?.Name ||
    "";
  const estimatedDelaySeconds = useMemo(() => {
    const backendLatencyMs = Number(status?.latency || 0);
    if (Number.isFinite(backendLatencyMs) && backendLatencyMs > 0) {
      return backendLatencyMs / 1000;
    }

    const audio = config.audio || {};
    return ["block_time", "crossfade_time", "extra_time"].reduce((total, key) => {
      const value = Number(audio[key] || 0);
      return Number.isFinite(value) && value > 0 ? total + value : total;
    }, 0);
  }, [config.audio, status?.latency]);

  return (
    <main className="app-shell">
      <section className="topbar">
        <div>
          <p className="eyebrow">Local RVC Console</p>
          <h1>羞羞脸变声器</h1>
        </div>
        <div className="status-stack">
          <StatusPill status={status} backend={health?.backend} />
          <span className="latency">
            <Gauge size={15} />
            预估 {estimatedDelaySeconds.toFixed(2)} 秒
          </span>
        </div>
      </section>

      <section className="toolbar" aria-label="Runtime controls">
        <button
          className={`primary ${backendReady ? "primary-danger" : ""}`}
          type="button"
          disabled={Boolean(busy)}
          aria-pressed={backendReady}
          onClick={() => runAction("engine", toggleEngine)}
        >
          <Power size={18} />
          {backendReady ? "关闭引擎" : "连接引擎"}
        </button>
        <button
          type="button"
          disabled={Boolean(busy) || !backendReady}
          onClick={() =>
            runAction(
              "start",
                async () => {
                await api("/start", { method: "POST", body: effectiveConfig });
                await refreshRuntime();
              },
              "变声已启动",
            )
          }
        >
          <Play size={18} />
          启动变声
        </button>
        <button
          type="button"
          disabled={Boolean(busy) || !backendReady}
          onClick={() =>
            runAction(
              "stop",
              async () => {
                await api("/stop", { method: "POST" });
                await refreshRuntime();
              },
              "变声已停止",
            )
          }
        >
          <Square size={18} />
          停止
        </button>
        <button
          type="button"
          disabled={Boolean(busy)}
          onClick={() =>
            runAction(
              "refresh",
              async () => {
                await refreshBase();
              },
              "状态已刷新",
            )
          }
        >
          <RefreshCw size={18} />
          刷新
        </button>
        <button
          type="button"
          disabled={Boolean(busy)}
            onClick={() =>
              runAction(
                "save",
                async () => {
                  await api("/config", { method: "POST", body: config });
              },
              "配置已保存",
            )
          }
        >
          <Save size={18} />
          保存配置
        </button>
      </section>

      <section className={`path-bar ${voiceRootReady ? "path-ok" : "path-warn"}`}>
        <label>
          <span>后端路径</span>
          <input
            value={voiceRootInput}
            placeholder="例如 D:\VoiceChanger"
            onChange={(event) => setVoiceRootInput(event.target.value)}
          />
        </label>
        <button
          type="button"
          disabled={Boolean(busy) || !voiceRootInput.trim()}
          onClick={() => runAction("voice-root", applyVoiceRoot, "后端路径已保存")}
        >
          <FolderOpen size={17} />
          应用路径
        </button>
        <span>{voiceRootReady ? "运行时可用" : "未找到 runtime\\python.exe"}</span>
      </section>

      {(message || error || busy) && (
        <section className={`notice ${error ? "notice-error" : ""}`}>
          {busy ? "处理中..." : error || message}
        </section>
      )}

      <section className="layout">
        <div className="panel panel-devices">
          <div className="panel-title">
            <Mic size={18} />
            <h2>音频路由</h2>
          </div>
          <FieldSelect
            icon={Activity}
            label="Host API"
            value={config.device.hostapi}
            options={devices.hostapis}
            help="优先选 Windows WASAPI，延迟低、兼容性好；如果设备列表不完整，再试 DirectSound 或 MME。"
            onChange={(value) => updateSection("device", "hostapi", value)}
          />
          <FieldSelect
            icon={Mic}
            label="输入设备"
            value={config.device.input_device}
            options={hostapiInputDevices}
            help="选择你实际说话用的麦克风，不要选 VB-Cable 这类虚拟输出。"
            onChange={(value) => updateSection("device", "input_device", value)}
          />
          <FieldSelect
            icon={Volume2}
            label="主输出设备"
            value={config.device.output_device}
            options={hostapiOutputDevices}
            help="给游戏或 Discord 使用时，通常选 VB-Audio Cable 这类虚拟扬声器。"
            onChange={(value) => updateSection("device", "output_device", value)}
          />
          <div className={`dependency-line ${virtualAudio.installed ? "dependency-ok" : "dependency-warn"}`}>
            <span>
              <Cable size={16} />
              {virtualAudio.installed
                ? `已检测到虚拟声卡${virtualAudioName ? `：${virtualAudioName}` : ""}`
                : "未检测到虚拟声卡；游戏/Discord 通常需要 VB-Cable。"}
            </span>
            {!virtualAudio.installed && (
              <button
                type="button"
                disabled={Boolean(busy)}
                onClick={() => runAction("virtual-audio", openVirtualAudioInstaller, "已打开 VB-Audio 官方安装页")}
              >
                安装虚拟声卡
              </button>
            )}
          </div>
          <div className="monitor-box">
            <Toggle
              checked={monitorEnabled}
              label="监听模式"
              help="用于自己试听；启动时临时把变声输出切到耳机或音箱。"
              onChange={setMonitorEnabled}
            />
            <FieldSelect
              icon={Volume2}
              label="监听设备"
              value={monitorOutputDevice}
              options={compatibleMonitorOutputDevices}
              help={monitorHelp}
              includeCurrent={false}
              onChange={setMonitorOutputDevice}
            />
          </div>
          <div className="toggle-row">
            <Toggle
              checked={config.device.wasapi_exclusive}
              label="WASAPI 独占"
              help="可能降低冲突和延迟，但会独占设备；不确定时保持关闭。"
              onChange={(value) => updateSection("device", "wasapi_exclusive", value)}
            />
          </div>
        </div>

        <div className="panel panel-tune">
          <div className="panel-title">
            <SlidersHorizontal size={18} />
            <h2>实时参数</h2>
          </div>
          <div className="tune-note">
            游戏卡顿优先降低分块时长，关闭输出降噪和相位平滑；如果爆音，再把分块时长略调高。
          </div>
          <Slider
            label="音高"
            value={config.model.pitch}
            min={-24}
            max={24}
            step={1}
            help="按半音调整。升高会更尖更亮，降低会更沉；变女声可先试 +4 到 +12。"
            onChange={(value) => updateSection("model", "pitch", value)}
          />
          <Slider
            label="音色偏移"
            value={config.model.formant}
            min={-2}
            max={2}
            step={0.1}
            help="改变声道共振感，影响厚薄和年龄感。建议小幅微调，过大容易失真。"
            onChange={(value) => updateSection("model", "formant", value)}
          />
          <Slider
            label="静音阈值"
            value={config.audio.threshold}
            min={-90}
            max={-20}
            step={1}
            unit=" dB"
            help="低于这个音量会当作静音。房间噪声大就调高一点，别高到吞字。"
            onChange={(value) => updateSection("audio", "threshold", value)}
          />
          <Slider
            label="分块时长"
            value={config.audio.block_time}
            min={0.12}
            max={1.2}
            step={0.01}
            unit=" s"
            help="越小延迟越低，但更吃性能、也更容易爆音。游戏建议 0.25 到 0.45。"
            onChange={(value) => updateSection("audio", "block_time", value)}
          />
          <Slider
            label="交叉淡化"
            value={config.audio.crossfade_time}
            min={0.01}
            max={0.18}
            step={0.01}
            unit=" s"
            help="控制音频块之间的过渡。杂音或断裂多就略加，追求低延迟就别太高。"
            onChange={(value) => updateSection("audio", "crossfade_time", value)}
          />
          <div className="toggle-grid">
            <Toggle
              checked={config.audio.input_noise_reduce}
              label="输入降噪"
              help="麦克风底噪明显时开，会稍微增加处理量。"
              onChange={(value) => updateSection("audio", "input_noise_reduce", value)}
            />
            <Toggle
              checked={config.audio.output_noise_reduce}
              label="输出降噪"
              help="变声后有沙沙声再开；游戏卡顿时优先关闭。"
              onChange={(value) => updateSection("audio", "output_noise_reduce", value)}
            />
            <Toggle
              checked={config.audio.use_phase_vocoder}
              label="相位平滑"
              help="可减轻颤动和拖影，但会增加一点延迟。"
              onChange={(value) => updateSection("audio", "use_phase_vocoder", value)}
            />
          </div>
          <button
            className="wide"
            type="button"
            disabled={Boolean(busy)}
            onClick={() =>
              runAction(
                running ? "restart" : "save",
                async () => {
                  if (running) {
                    await api("/restart", { method: "POST", body: effectiveConfig });
                    await refreshRuntime();
                  } else {
                    await api("/config", { method: "POST", body: config });
                  }
                },
                running ? "参数已应用，变声器已重启" : "参数已应用，下次启动生效",
              )
            }
          >
            <WandSparkles size={18} />
            {running ? "应用并重启变声器" : "应用参数"}
          </button>
        </div>

        <div className="panel panel-models">
          <div className="panel-title">
            <WandSparkles size={18} />
            <h2>声音模型</h2>
            <span>{models.length} 个</span>
          </div>
          <div className="selected-model">
            <span>当前模型</span>
            <strong>{displayModelName}</strong>
          </div>
          <div className="model-import">
            <input ref={modelInputRef} type="file" accept=".pth" onChange={handleModelImport} />
            <button type="button" disabled={Boolean(busy)} onClick={() => modelInputRef.current?.click()}>
              <Upload size={17} />
              导入 .pth
            </button>
            <small>列表只显示可读取的模型文件。</small>
          </div>
          <div className="model-grid">
            {models.map((model) => (
              <button
                type="button"
                className={`model-card ${model === selectedModel ? "is-selected" : ""}`}
                key={model.key}
                onClick={() => selectModel(model)}
              >
                <span className="model-image">
                  {model.imageUrl ? <img src={model.imageUrl} alt="" /> : <WandSparkles size={28} />}
                </span>
                <span className="model-name">{model.name}</span>
                <span className="model-meta">Type {model.type || 0}</span>
              </button>
            ))}
          </div>
        </div>
      </section>
    </main>
  );
}
