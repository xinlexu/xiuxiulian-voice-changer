# 羞羞脸变声

本地运行的实时 AI 变声器（RVC 模型），给打游戏、开黑用。双击 `羞羞脸变声.exe` 就能用。

- **低延迟**：游戏模式端到端约 0.2 秒（旧版分块 0.42 秒，实际 0.5～0.7 秒）。
- **不卡**：声卡和推理分开跑，中间有自动调节的小缓冲；游戏把显卡吃满时自动换更轻的算法，不会断音、爆音。
- **显卡优先**：用和 OBS 一样的办法让 Windows 优先调度变声的显卡任务（管理员权限下效果最好）。
- **同时监听**：变声后的声音同时送到游戏（虚拟声卡）和你的耳机。
- **随便搭配设备**：所有设备统一 48 kHz（Windows 自动转换），不会再出现“采样率不一致”。
- **实时调**：音高、音色、门限、音量、换模型都不用重启。
- **测我的音高**：说几句话自动算出男变女该用的音高。

## 用法

1. 双击 `羞羞脸变声.exe`。
2. 「设备与监听」里：麦克风选你真实的麦克风，「输出到游戏」选 `伴侣扬声器 (VB-Audio Cable A)`（或 `CABLE Input`）。
3. 游戏的语音设置里，麦克风选 `伴侣麦克风 (VB-Audio Cable A)`（或 `CABLE Output`）。
4. 右边选一个声音模型，点大按钮「开始变声」。

关窗口就是退出。想看详细日志就用 `调试启动（显示日志）.bat`。

## 文件夹里有什么

```text
羞羞脸变声\
├─ 羞羞脸变声.exe        启动器（launcher\ 是它的源码）
├─ app\                  变声引擎（Python）+ 界面（app\web 是 ui\ 编译出来的）
├─ ui\                   界面源码（React + Vite）
├─ runtime\              Python 3.9 + PyTorch（不进 git）
├─ dependencies\basemodels\  hubert_base.pt、rmvpe.pt（不进 git）
├─ Assets\Models\        声音模型，每个模型一个文件夹（.pth，可选 .index 和 cover.jpg）
├─ 配置文件\vbcable\      VB-Cable 虚拟声卡安装程序
├─ data\                 设置和日志（删了就恢复默认）
└─ scripts\              新电脑安装、打包运行环境
```

## 新电脑安装

1. 下载本仓库 zip，解压到一个固定位置，比如 `H:\羞羞脸变声`。
2. 双击 `首次安装.bat`：自动下载运行环境（约 5.6 GB，支持断点续传和国内加速镜像）、安装虚拟声卡、启动软件。

## 引擎说明

- 变声算法是 RVC（v1/v2 模型都支持，带 .index 的模型可以开「索引强度」）。
- 内容特征用 torchaudio 版 HuBERT（和原版 fairseq 权重一致，不再依赖 fairseq），音高用 RMVPE / FCPE。
- 推理流程改编自 [RVC-Project](https://github.com/RVC-Project/Retrieval-based-Voice-Conversion-WebUI)（MIT），相关文件在 `app\xxl\vendor\rvc`，许可证见同目录 `LICENSE.txt`。
- 本地接口只监听 `127.0.0.1:18674`。

## 开发

```powershell
# 界面
cd ui
npm install
npm run build          # 输出到 app\web
npm run dev            # 开发时用，接口会代理到正在运行的软件

# 启动器（Linux 上交叉编译，需要 mingw-w64）
sh launcher/build.sh
```

## 范围

- 不做会员登录，不联网（除了首次安装下载运行环境）。
- 只发布你有权分发的运行环境、模型和驱动。
