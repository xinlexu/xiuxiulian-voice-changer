import express from "express";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import multer from "multer";
import {
  ensureBackend,
  getBridgeHealth,
  getDevices,
  getModelImagePath,
  getRuntimeSettings,
  getVirtualAudioStatus,
  importModelFromUpload,
  getStatus,
  listModels,
  openVirtualAudioInstallerPage,
  readConfig,
  restartVoice,
  shutdownSpawnedBackend,
  startVoice,
  stopVoice,
  updateVoiceRoot,
  updateVoice,
  writeConfig,
} from "./voiceBridge.mjs";

const uploadDir = path.join(os.tmpdir(), "xiuxiulian-model-imports");
await fs.mkdir(uploadDir, { recursive: true });

const upload = multer({
  dest: uploadDir,
  limits: {
    fileSize: 1024 * 1024 * 1024,
  },
  fileFilter: (_req, file, callback) => {
    if (!file.originalname.toLowerCase().endsWith(".pth")) {
      const error = new Error("只支持导入 .pth 模型文件。");
      error.status = 400;
      callback(error);
      return;
    }
    callback(null, true);
  },
});

export function createApiRouter() {
  const router = express.Router();

  router.use(express.json({ limit: "2mb" }));

  router.get("/health", async (_req, res) => {
    res.json(await getBridgeHealth());
  });

  router.post("/backend/ensure", async (_req, res) => {
    res.json(await ensureBackend());
  });

  router.post("/backend/shutdown", async (_req, res) => {
    res.json(await shutdownSpawnedBackend());
  });

  router.get("/settings", async (_req, res) => {
    res.json(await getRuntimeSettings());
  });

  router.post("/settings/voice-root", async (req, res) => {
    const settings = await updateVoiceRoot(req.body?.voiceRoot);
    res.json({
      settings,
      health: await getBridgeHealth(),
      models: await listModels(),
      config: await readConfig(),
    });
  });

  router.get("/virtual-audio", async (_req, res) => {
    res.json(await getVirtualAudioStatus());
  });

  router.post("/virtual-audio/open-installer", async (_req, res) => {
    res.json(await openVirtualAudioInstallerPage());
  });

  router.get("/status", async (_req, res) => {
    res.json(await getStatus());
  });

  router.get("/devices", async (_req, res) => {
    res.json(await getDevices());
  });

  router.get("/models", async (_req, res) => {
    res.json({ models: await listModels() });
  });

  router.post("/models/import", upload.single("model"), async (req, res) => {
    try {
      if (!req.file) {
        const error = new Error("请选择一个 .pth 模型文件。");
        error.status = 400;
        throw error;
      }

      const model = await importModelFromUpload(req.file.path, req.file.originalname);
      res.json({ model });
    } finally {
      if (req.file?.path) {
        await fs.unlink(req.file.path).catch(() => {});
      }
    }
  });

  router.get("/models/:key/image", async (req, res) => {
    res.sendFile(await getModelImagePath(req.params.key));
  });

  router.get("/config", async (_req, res) => {
    res.json(await readConfig());
  });

  router.post("/config", async (req, res) => {
    res.json(await writeConfig(req.body));
  });

  router.post("/start", async (req, res) => {
    res.json(await startVoice(req.body));
  });

  router.post("/stop", async (_req, res) => {
    res.json(await stopVoice());
  });

  router.post("/restart", async (req, res) => {
    res.json(await restartVoice(req.body));
  });

  router.post("/update", async (req, res) => {
    res.json(await updateVoice(req.body));
  });

  router.use((error, _req, res, _next) => {
    const status = error.status && Number.isInteger(error.status) ? error.status : 500;
    res.status(status).json({
      error: error.message || "Unexpected bridge error",
      details: error.payload || null,
    });
  });

  return router;
}
