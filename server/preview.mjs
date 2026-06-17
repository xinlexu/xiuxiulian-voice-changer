import express from "express";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { createApiRouter } from "./api.mjs";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(__dirname, "..");
const host = "127.0.0.1";
const port = Number(process.env.PORT || 4174);

const app = express();
app.use("/api", createApiRouter());
app.use(express.static(path.join(root, "dist")));
app.get("*", (_req, res) => {
  res.sendFile(path.join(root, "dist", "index.html"));
});

app.listen(port, host, () => {
  console.log(`羞羞脸变声器 preview server: http://${host}:${port}`);
});
