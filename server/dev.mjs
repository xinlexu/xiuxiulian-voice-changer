import express from "express";
import { createServer as createViteServer } from "vite";
import { createApiRouter } from "./api.mjs";

const host = "127.0.0.1";
const port = Number(process.env.PORT || 5174);

const app = express();
app.use("/api", createApiRouter());

const vite = await createViteServer({
  server: { middlewareMode: true },
  appType: "spa",
});

app.use(vite.middlewares);

app.listen(port, host, () => {
  console.log(`羞羞脸变声器 dev server: http://${host}:${port}`);
});
