import { execFile, spawn } from "node:child_process";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";

const DEFAULT_VOICE_ROOT = "D:\\VoiceChanger";
const RVC_BASE_URL = process.env.RVC_BASE_URL || "http://127.0.0.1:5000";
const VIRTUAL_AUDIO_DOWNLOAD_URL = "https://vb-audio.com/Cable/";
const __dirname = path.dirname(fileURLToPath(import.meta.url));
const projectRoot = path.resolve(__dirname, "..");
const settingsDir = process.env.XIUXIULIAN_CONFIG_DIR
  ? path.resolve(process.env.XIUXIULIAN_CONFIG_DIR)
  : path.join(os.homedir(), "AppData", "Roaming", "xiuxiulian-voice-changer");
const settingsPath = path.join(settingsDir, "settings.json");

let voiceRoot = "";
let pythonPath = "";
let configPath = "";
let modelsRoot = "";
let supplementalModelsRoot = "";
let voiceRootSource = "fallback";

let spawnedController = null;

const execFileAsync = promisify(execFile);

const defaultConfig = {
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

function deepMerge(base, patch) {
  const output = structuredClone(base);
  for (const [key, value] of Object.entries(patch || {})) {
    if (
      value &&
      typeof value === "object" &&
      !Array.isArray(value) &&
      output[key] &&
      typeof output[key] === "object"
    ) {
      output[key] = deepMerge(output[key], value);
    } else {
      output[key] = value;
    }
  }
  return output;
}

function normalizeConfig(config) {
  return deepMerge(defaultConfig, config || {});
}

function assertInside(base, target) {
  const resolvedBase = path.resolve(base).toLowerCase();
  const resolvedTarget = path.resolve(target).toLowerCase();
  if (resolvedTarget !== resolvedBase && !resolvedTarget.startsWith(`${resolvedBase}${path.sep}`)) {
    throw new Error("Path escapes the configured voice root.");
  }
}

async function exists(filePath) {
  try {
    await fs.access(filePath);
    return true;
  } catch {
    return false;
  }
}

function createStatusError(message, status = 400) {
  const error = new Error(message);
  error.status = status;
  return error;
}

function applyVoiceRoot(nextRoot, source = "manual") {
  voiceRoot = path.resolve(nextRoot);
  pythonPath = path.join(voiceRoot, "runtime", "python.exe");
  configPath = path.join(voiceRoot, "userconfig.json");
  modelsRoot = path.join(voiceRoot, "Assets", "Models");
  supplementalModelsRoot = path.join(voiceRoot, "配置文件", "111");
  voiceRootSource = source;
}

async function readAppSettings() {
  try {
    return JSON.parse(await fs.readFile(settingsPath, "utf8"));
  } catch {
    return {};
  }
}

async function writeAppSettings(settings) {
  await fs.mkdir(settingsDir, { recursive: true });
  await fs.writeFile(settingsPath, `${JSON.stringify(settings, null, 2)}\n`, "utf8");
}

function getCandidateVoiceRoots(settings = {}) {
  const roots = [
    { root: process.env.VOICE_ROOT, source: "env" },
    { root: settings.voiceRoot, source: "saved" },
    { root: path.join(projectRoot, "VoiceChanger"), source: "portable" },
    { root: path.resolve(projectRoot, "..", "VoiceChanger"), source: "sibling" },
    { root: "D:\\VoiceChanger", source: "drive" },
    { root: "C:\\VoiceChanger", source: "drive" },
    { root: "E:\\VoiceChanger", source: "drive" },
    { root: "F:\\VoiceChanger", source: "drive" },
  ];
  const seen = new Set();

  return roots
    .filter((candidate) => candidate.root)
    .map((candidate) => ({ ...candidate, root: path.resolve(candidate.root) }))
    .filter((candidate) => {
      const key = candidate.root.toLowerCase();
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    });
}

async function isUsableVoiceRoot(candidateRoot) {
  return exists(path.join(candidateRoot, "runtime", "python.exe"));
}

async function initializeVoiceRoot() {
  const settings = await readAppSettings();
  const candidates = getCandidateVoiceRoots(settings);

  for (const candidate of candidates) {
    if (await isUsableVoiceRoot(candidate.root)) {
      applyVoiceRoot(candidate.root, candidate.source);
      return;
    }
  }

  applyVoiceRoot(process.env.VOICE_ROOT || settings.voiceRoot || DEFAULT_VOICE_ROOT, "fallback");
}

function sanitizeModelName(name) {
  const cleaned = name
    .replace(/[<>:"/\\|?*\x00-\x1f]/g, "_")
    .replace(/\.+$/g, "")
    .trim();

  if (!cleaned) {
    throw createStatusError("模型文件名无效。");
  }

  return cleaned.slice(0, 80);
}

async function getUniqueModelName(baseName) {
  let candidate = baseName;
  let index = 2;

  while (await exists(path.join(modelsRoot, candidate))) {
    candidate = `${baseName}-${index}`;
    index += 1;
  }

  return candidate;
}

async function getFileStats(filePath) {
  try {
    const stats = await fs.stat(filePath);
    return stats.isFile() ? stats : null;
  } catch {
    return null;
  }
}

async function getUsableModelFile(folderPath, modelFile) {
  if (!modelFile || path.isAbsolute(modelFile)) return null;

  const modelPath = path.join(folderPath, modelFile);
  assertInside(folderPath, modelPath);

  const stats = await getFileStats(modelPath);
  if (!stats || stats.size <= 0) return null;

  return { file: modelFile, path: modelPath, size: stats.size };
}

async function findUsableModelFile(folderPath, preferredFile) {
  const preferred = await getUsableModelFile(folderPath, preferredFile);
  if (preferred) return preferred;

  const entries = await fs.readdir(folderPath, { withFileTypes: true });
  const pthFiles = entries
    .filter((entry) => entry.isFile() && entry.name.toLowerCase().endsWith(".pth"))
    .map((entry) => entry.name)
    .sort((a, b) => a.localeCompare(b, "zh-Hans-CN"));

  for (const modelFile of pthFiles) {
    const usable = await getUsableModelFile(folderPath, modelFile);
    if (usable) return usable;
  }

  return null;
}

async function getAvailableImageFile(folderPath, imageFile) {
  if (!imageFile || path.isAbsolute(imageFile)) return "";

  const imagePath = path.join(folderPath, imageFile);
  assertInside(folderPath, imagePath);

  return (await getFileStats(imagePath)) ? imageFile : "";
}

async function syncSupplementalModels() {
  if (!(await exists(supplementalModelsRoot)) || !(await exists(modelsRoot))) {
    return;
  }

  const entries = await fs.readdir(supplementalModelsRoot, { withFileTypes: true });
  for (const entry of entries) {
    if (!entry.isFile() || !entry.name.toLowerCase().endsWith(".pth")) continue;

    const name = path.basename(entry.name, ".pth");
    const sourcePath = path.join(supplementalModelsRoot, entry.name);
    const targetFolder = path.join(modelsRoot, name);
    const targetPath = path.join(targetFolder, entry.name);
    const infoPath = path.join(targetFolder, "info.json");

    assertInside(supplementalModelsRoot, sourcePath);
    assertInside(modelsRoot, targetFolder);
    await fs.mkdir(targetFolder, { recursive: true });

    if (!(await exists(targetPath))) {
      await fs.copyFile(sourcePath, targetPath);
    }

    if (!(await exists(infoPath))) {
      await fs.writeFile(
        infoPath,
        `${JSON.stringify(
          {
            Id: name,
            Name: name,
            Type: 1,
            Label: "本地导入",
            ImageFile: "",
            ModelFile: entry.name,
          },
          null,
          2,
        )}\n`,
        "utf8",
      );
    }
  }
}

async function requestJson(endpoint, options = {}) {
  const response = await fetch(`${RVC_BASE_URL}${endpoint}`, {
    method: options.method || "GET",
    headers: options.body ? { "Content-Type": "application/json" } : undefined,
    body: options.body ? JSON.stringify(options.body) : undefined,
    signal: AbortSignal.timeout(options.timeoutMs || 2500),
  });

  const text = await response.text();
  let payload = null;
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch {
      payload = { raw: text };
    }
  }

  if (!response.ok) {
    const error = new Error(payload?.msg || payload?.message || `RVC API ${response.status}`);
    error.status = response.status;
    error.payload = payload;
    throw error;
  }

  return payload ?? {};
}

async function queryDeviceDetails() {
  if (!(await exists(pythonPath))) return null;

  const script = `
import json
import sounddevice as sd
payload = {
    "hostapis": sd.query_hostapis(),
    "devices": sd.query_devices(),
}
print(json.dumps(payload, ensure_ascii=False))
`;

  try {
    const { stdout } = await execFileAsync(pythonPath, ["-c", script], {
      cwd: voiceRoot,
      windowsHide: true,
      maxBuffer: 1024 * 1024 * 4,
      timeout: 8000,
      env: {
        ...process.env,
        PYTHONUTF8: "1",
      },
    });
    return JSON.parse(stdout);
  } catch {
    return null;
  }
}

function normalizeArray(value) {
  if (!value) return [];
  return Array.isArray(value) ? value : [value];
}

function isVirtualAudioName(name) {
  return /vb-audio|vb-cable|voicemeeter|virtual audio cable|cable input|cable output|cable a|cable b|cable c|cable d|伴侣扬声器|伴侣麦克风/i.test(
    String(name || ""),
  );
}

async function queryWindowsVirtualAudioDevices() {
  if (process.platform !== "win32") return [];

  const script = `
$OutputEncoding = [System.Text.Encoding]::UTF8
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Get-CimInstance Win32_PnPEntity |
  Where-Object {
    ($_.PNPClass -eq 'AudioEndpoint' -or $_.PNPClass -eq 'MEDIA') -and
    ($_.Name -match 'VB-Audio|VB-Cable|VoiceMeeter|Virtual Audio Cable|CABLE Input|CABLE Output|Cable A|Cable B|Cable C|Cable D|伴侣扬声器|伴侣麦克风')
  } |
  Select-Object Name, Status, PNPClass, Manufacturer |
  ConvertTo-Json -Depth 3
`;

  try {
    const { stdout } = await execFileAsync("powershell.exe", ["-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script], {
      windowsHide: true,
      timeout: 8000,
      maxBuffer: 1024 * 1024,
    });

    if (!stdout.trim()) return [];
    return normalizeArray(JSON.parse(stdout)).filter((device) => isVirtualAudioName(device.Name));
  } catch {
    return [];
  }
}

function findDeviceDetail(details, hostapiName, deviceName, direction) {
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

function getDeviceRate(device) {
  const rate = Math.round(Number(device?.default_samplerate || 0));
  return rate || null;
}

function findCompatibleHostapi(details, preferredHostapi, inputDeviceName, outputDeviceName) {
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
  const uniqueOrder = [...new Set(order)];

  for (const hostapiName of uniqueOrder) {
    const input = findDeviceDetail(details, hostapiName, inputDeviceName, "input");
    const output = findDeviceDetail(details, hostapiName, outputDeviceName, "output");
    const inputRate = getDeviceRate(input);
    const outputRate = getDeviceRate(output);

    if (input && output && inputRate && outputRate && inputRate === outputRate) {
      return {
        hostapi: hostapiName,
        inputRate,
        outputRate,
        changed: hostapiName !== preferredHostapi,
      };
    }
  }

  return null;
}

async function withCompatibleAudioRoute(config) {
  const normalized = normalizeConfig(config);
  if (normalized.audio.sr_type !== "sr_device") return normalized;

  const details = await queryDeviceDetails();
  const input = findDeviceDetail(details, normalized.device.hostapi, normalized.device.input_device, "input");
  const output = findDeviceDetail(details, normalized.device.hostapi, normalized.device.output_device, "output");

  if (!input || !output) return normalized;

  const inputRate = getDeviceRate(input);
  const outputRate = getDeviceRate(output);
  if (!inputRate || !outputRate || inputRate === outputRate) return normalized;

  const compatible = findCompatibleHostapi(
    details,
    normalized.device.hostapi,
    normalized.device.input_device,
    normalized.device.output_device,
  );

  if (compatible) {
    return {
      ...normalized,
      device: {
        ...normalized.device,
        hostapi: compatible.hostapi,
      },
    };
  }

  throw createStatusError(
    `输入和输出设备采样率不一致：${normalized.device.input_device} 是 ${inputRate} Hz，${normalized.device.output_device} 是 ${outputRate} Hz。没有找到可自动兼容的 Host API，请把这两个设备在 Windows 声音设置里改成相同采样率。`,
  );
}

await initializeVoiceRoot();

export async function getRuntimeSettings() {
  const settings = await readAppSettings();
  const candidates = await Promise.all(
    getCandidateVoiceRoots(settings).map(async (candidate) => ({
      ...candidate,
      usable: await isUsableVoiceRoot(candidate.root),
    })),
  );

  return {
    settingsPath,
    voiceRoot,
    voiceRootSource,
    rvcBaseUrl: RVC_BASE_URL,
    portableVoiceRoot: path.join(projectRoot, "VoiceChanger"),
    candidates,
  };
}

export async function getVirtualAudioStatus() {
  const windowsDevices = await queryWindowsVirtualAudioDevices();
  let backendDevices = [];

  const details = await queryDeviceDetails();
  if (details?.devices) {
    backendDevices = details.devices
      .filter((device) => isVirtualAudioName(device.name))
      .map((device) => ({
        name: device.name,
        hostapi: details.hostapis?.[device.hostapi]?.name || "",
        input: Number(device.max_input_channels || 0) > 0,
        output: Number(device.max_output_channels || 0) > 0,
      }));
  }

  return {
    installed: windowsDevices.length > 0 || backendDevices.length > 0,
    downloadUrl: VIRTUAL_AUDIO_DOWNLOAD_URL,
    windowsDevices,
    backendDevices,
  };
}

export async function openVirtualAudioInstallerPage() {
  const status = await getVirtualAudioStatus();

  if (process.platform === "win32") {
    await execFileAsync(
      "powershell.exe",
      ["-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", `Start-Process '${VIRTUAL_AUDIO_DOWNLOAD_URL}'`],
      {
        windowsHide: true,
        timeout: 5000,
      },
    ).catch(() => {});
  }

  return {
    ...status,
    opened: process.platform === "win32",
    note: "下载 Windows 包后，解压并以管理员身份运行 VBCABLE_Setup_x64.exe，安装完成后重启本应用。",
  };
}

export async function updateVoiceRoot(nextRoot) {
  const requestedRoot = String(nextRoot || "").trim();
  if (!requestedRoot) {
    throw createStatusError("请输入 VoiceChanger 后端文件夹路径。");
  }

  const resolvedRoot = path.resolve(requestedRoot);
  if (!(await exists(resolvedRoot))) {
    throw createStatusError(`后端路径不存在：${resolvedRoot}`);
  }

  if (!(await isUsableVoiceRoot(resolvedRoot))) {
    throw createStatusError(`没有找到运行时：${path.join(resolvedRoot, "runtime", "python.exe")}`);
  }

  if (spawnedController) {
    await shutdownSpawnedBackend();
  }

  applyVoiceRoot(resolvedRoot, "saved");
  await writeAppSettings({
    ...(await readAppSettings()),
    voiceRoot: resolvedRoot,
  });

  return getRuntimeSettings();
}

export async function getBridgeHealth() {
  const [rootOk, pythonOk, configOk, modelsOk] = await Promise.all([
    exists(voiceRoot),
    exists(pythonPath),
    exists(configPath),
    exists(modelsRoot),
  ]);

  let backend = { reachable: false, status: null };
  try {
    backend = { reachable: true, status: await requestJson("/status", { timeoutMs: 900 }) };
  } catch {
    backend = { reachable: false, status: null };
  }

  return {
    voiceRoot,
    voiceRootSource,
    settingsPath,
    paths: {
      root: rootOk,
      python: pythonOk,
      config: configOk,
      models: modelsOk,
    },
    backend: {
      ...backend,
      spawnedPid: spawnedController?.pid ?? null,
    },
  };
}

export async function ensureBackend() {
  try {
    return { alreadyRunning: true, status: await requestJson("/status", { timeoutMs: 900 }) };
  } catch {
    // Start below.
  }

  if (!(await exists(pythonPath))) {
    throw createStatusError(`找不到后端运行时：${pythonPath}。请先设置正确的 VoiceChanger 文件夹。`);
  }

  spawnedController = spawn(pythonPath, ["-c", "from rvc_core._ctl import main; main()"], {
    cwd: voiceRoot,
    windowsHide: true,
    stdio: "ignore",
    env: {
      ...process.env,
      PYTHONUTF8: "1",
    },
  });

  spawnedController.once("exit", () => {
    spawnedController = null;
  });

  const deadline = Date.now() + 15_000;
  let lastError = null;
  while (Date.now() < deadline) {
    try {
      const status = await requestJson("/status", { timeoutMs: 900 });
      return { alreadyRunning: false, status, pid: spawnedController?.pid ?? null };
    } catch (error) {
      lastError = error;
      await new Promise((resolve) => setTimeout(resolve, 500));
    }
  }

  throw new Error(`RVC backend did not become ready: ${lastError?.message || "timeout"}`);
}

export async function shutdownSpawnedBackend() {
  if (!spawnedController) {
    return { stopped: false, reason: "Only backend processes spawned by this app are stopped." };
  }

  const pid = spawnedController.pid;
  spawnedController.kill("SIGTERM");
  spawnedController = null;
  return { stopped: true, pid };
}

export async function readConfig() {
  try {
    const raw = await fs.readFile(configPath, "utf8");
    return normalizeConfig(JSON.parse(raw));
  } catch {
    return normalizeConfig({});
  }
}

export async function writeConfig(config) {
  const normalized = normalizeConfig(await resolveModelPath(config));
  await fs.writeFile(configPath, `${JSON.stringify(normalized, null, 2)}\n`, "utf8");
  return normalized;
}

export async function listModels() {
  await syncSupplementalModels();
  if (!(await exists(modelsRoot))) {
    return [];
  }

  const entries = await fs.readdir(modelsRoot, { withFileTypes: true });
  const models = [];

  for (const entry of entries) {
    if (!entry.isDirectory()) continue;

    const folder = entry.name;
    const folderPath = path.join(modelsRoot, folder);
    const infoPath = path.join(folderPath, "info.json");

    let info = null;
    try {
      info = JSON.parse(await fs.readFile(infoPath, "utf8"));
    } catch {
      const pth = (await fs.readdir(folderPath)).find((name) => name.toLowerCase().endsWith(".pth"));
      info = {
        Id: folder,
        Name: folder,
        Type: 0,
        Label: "",
        ImageFile: "",
        ModelFile: pth || "",
      };
    }

    const usableModel = await findUsableModelFile(folderPath, info.ModelFile || "");
    if (!usableModel) continue;

    const imageFile = await getAvailableImageFile(folderPath, info.ImageFile || "");

    models.push({
      key: Buffer.from(folder, "utf8").toString("base64url"),
      folder,
      id: info.Id || folder,
      name: info.Name || folder,
      type: Number(info.Type ?? 0),
      label: info.Label || "",
      modelFile: usableModel.file,
      modelPath: usableModel.path,
      modelSize: usableModel.size,
      imageFile,
      imageUrl: imageFile ? `/api/models/${Buffer.from(folder, "utf8").toString("base64url")}/image` : null,
    });
  }

  return models.sort((a, b) => a.name.localeCompare(b.name, "zh-Hans-CN"));
}

export async function importModelFromUpload(sourcePath, originalName) {
  if (!originalName.toLowerCase().endsWith(".pth")) {
    throw createStatusError("只支持导入 .pth 模型文件。");
  }

  const sourceStats = await getFileStats(sourcePath);
  if (!sourceStats || sourceStats.size <= 0) {
    throw createStatusError("模型文件为空或不可读取。");
  }

  const baseName = sanitizeModelName(path.basename(originalName, path.extname(originalName)));
  await fs.mkdir(modelsRoot, { recursive: true });
  const modelName = await getUniqueModelName(baseName);
  const modelFile = `${modelName}.pth`;
  const targetFolder = path.join(modelsRoot, modelName);
  const targetPath = path.join(targetFolder, modelFile);
  const infoPath = path.join(targetFolder, "info.json");

  assertInside(modelsRoot, targetFolder);
  assertInside(targetFolder, targetPath);
  await fs.mkdir(targetFolder, { recursive: true });
  await fs.copyFile(sourcePath, targetPath);

  let existingInfo = {};
  try {
    existingInfo = JSON.parse(await fs.readFile(infoPath, "utf8"));
  } catch {
    existingInfo = {};
  }

  await fs.writeFile(
    infoPath,
    `${JSON.stringify(
      {
        Id: existingInfo.Id || modelName,
        Name: existingInfo.Name || modelName,
        Type: Number(existingInfo.Type ?? 1),
        Label: existingInfo.Label || "手动导入",
        ImageFile: existingInfo.ImageFile || "",
        ModelFile: modelFile,
      },
      null,
      2,
    )}\n`,
    "utf8",
  );

  const importedModels = await listModels();
  const importedModel = importedModels.find((model) => model.folder === modelName);
  if (!importedModel) {
    throw createStatusError("模型已复制，但没有通过可用性校验。");
  }

  return importedModel;
}

export async function resolveModelPath(config) {
  const current = config?.model?.pth_path || "";
  if (!current) return config;

  if (path.isAbsolute(current) && (await exists(current))) {
    return config;
  }

  const models = await listModels();
  const normalizedCurrent = current.toLowerCase();
  const match = models.find((model) => {
    return (
      model.modelFile.toLowerCase() === normalizedCurrent ||
      model.folder.toLowerCase() === normalizedCurrent ||
      model.id.toLowerCase() === normalizedCurrent ||
      model.name.toLowerCase() === normalizedCurrent ||
      path.basename(model.modelPath || "").toLowerCase() === normalizedCurrent
    );
  });

  if (!match?.modelPath || !(await exists(match.modelPath))) {
    return config;
  }

  return {
    ...config,
    model: {
      ...config.model,
      pth_path: match.modelPath,
    },
  };
}

export async function getModelImagePath(key) {
  const folder = Buffer.from(key, "base64url").toString("utf8");
  const folderPath = path.join(modelsRoot, folder);
  assertInside(modelsRoot, folderPath);

  const infoPath = path.join(folderPath, "info.json");
  const info = JSON.parse(await fs.readFile(infoPath, "utf8"));
  if (!info.ImageFile) {
    throw new Error("Model does not define an image.");
  }

  const imagePath = path.join(folderPath, info.ImageFile);
  assertInside(folderPath, imagePath);
  return imagePath;
}

export async function getStatus() {
  return requestJson("/status");
}

export async function getDevices() {
  const devices = await requestJson("/devices", { timeoutMs: 5000 });
  const details = await queryDeviceDetails();
  return {
    ...devices,
    device_details: details,
  };
}

export async function startVoice(config) {
  const normalized = await withCompatibleAudioRoute(await resolveModelPath(config || (await readConfig())));
  await ensureBackend();
  return requestJson("/start", { method: "POST", body: normalized, timeoutMs: 10_000 });
}

export async function stopVoice() {
  await ensureBackend();
  return requestJson("/stop", { method: "POST", timeoutMs: 5000 });
}

export async function updateVoice(config) {
  const normalized = await withCompatibleAudioRoute(await resolveModelPath(config || (await readConfig())));
  const health = await getBridgeHealth();
  if (!health.backend.reachable) {
    return { saved: false, applied: false, config: normalized };
  }

  const result = await requestJson("/update", { method: "POST", body: normalized, timeoutMs: 8000 });
  return { saved: true, applied: true, result, config: normalized };
}

export async function restartVoice(config) {
  const normalized = await withCompatibleAudioRoute(await resolveModelPath(config || (await readConfig())));
  await ensureBackend();

  let wasRunning = false;
  try {
    const status = await requestJson("/status", { timeoutMs: 1500 });
    wasRunning = Boolean(status.running);
  } catch {
    // Continue with a clean start attempt below.
  }

  if (wasRunning) {
    await requestJson("/stop", { method: "POST", timeoutMs: 5000 }).catch(() => {});
    await new Promise((resolve) => setTimeout(resolve, 450));
  }

  const result = await requestJson("/start", { method: "POST", body: normalized, timeoutMs: 10_000 });
  return { restarted: wasRunning, result, config: normalized };
}
