# 羞羞脸变声

在自己电脑上运行的实时 AI 变声器（RVC 声音模型），打游戏、开黑用。男声变女声，延迟低、不卡顿。

## 电脑要求

- Windows 10 或 11（64 位）
- **NVIDIA 显卡**，需要与所带 PyTorch/CUDA 运行环境兼容的型号和驱动。不同显卡型号仍需实机确认，尤其不能仅凭代数推定兼容。
- 安装时要有约 **16 GB** 空闲空间（装完后占用约 9 GB），网络要能下载约 5.6 GB。
- 一个麦克风。

## 新电脑安装（只需做一次）

1. **下载程序**：打开本仓库的 [Releases](https://github.com/xinlexu/xiuxiulian-voice-changer/releases/latest) 页面，下载最新版本里的 `羞羞脸变声-程序.zip`。
   （也可以点仓库首页绿色的 **Code → Download ZIP**，效果一样。）
2. **解压到一个短路径**，比如 `D:\羞羞脸变声`。
   不要直接在压缩包里运行，也不要放在很深的文件夹里（路径太长会装不上）。
3. **双击 `首次安装.bat`**，它会自动：
   - 下载运行环境和声音模型（约 5.6 GB，断了重新双击会接着下，国内会自动走加速镜像）；
   - 安装虚拟声卡 VB-Cable（Windows 会弹窗要管理员权限，点「是」，然后在安装窗口点 Install Driver）；
   - 在桌面创建「羞羞脸变声」快捷方式，并打开软件。
4. 如果装完虚拟声卡后软件里找不到它，**重启一次电脑**。

> 程序未签名，Windows 可能显示未知发布者或 SmartScreen 提示。请确认来自本仓库的 Release，并核对该版本提供的 SHA-256；未签名不能证明文件安全。遇到拦截时先核实来源，不要关闭系统安全防护。

以后每次使用，直接双击桌面上的 **羞羞脸变声**。

## 怎么用

1. 打开软件，在「设备与监听」里：
   - **麦克风**：选你真实的麦克风；
   - **输出到游戏**：选虚拟声卡的输入端，名字一般是 `CABLE Input (VB-Audio Virtual Cable)` 或 `CABLE-A Input (VB-Audio Cable A)`。
2. 在**游戏（或 QQ、微信、Discord）的语音设置**里，麦克风选虚拟声卡的输出端：`CABLE Output (...)`。软件「设备与监听」页最下面会写出你电脑上具体该选哪个。
3. 打开「**为我定制**」，读一段屏幕上的文字（约 20 秒）。软件会按你的声音自动调好音高、麦克风音量和静音门限，并给所有声音模型打分，挑一个分高的点「用这个」。
4. 点左上角的大按钮「**开始变声**」。想听自己变声后的效果，打开「监听」。

关掉窗口就是退出软件。

## 常见问题

| 现象 | 解决办法 |
| --- | --- |
| 下载很慢或中断 | 重新双击 `首次安装.bat`，已下好的部分不会重下。 |
| 队友听不到我 | 游戏里的麦克风要选 `CABLE Output`，不是你真实的麦克风；软件里「输出到游戏」要选 `CABLE Input`。 |
| 声音断断续续 | 看开关下面的提示：写「显卡被占满」就把游戏帧数限制低一点，或者换「均衡」模式；写「声卡驱动」就去「设备与监听」看设备是不是在用「兼容模式」。 |
| 口齿不清、发闷 | 用「为我定制」重新测一次，换个分数高的模型；音高不要太低。 |
| 软件打不开 / 报错 | 双击 `调试启动（显示日志）.bat` 看具体错误，日志也在 `data\logs` 里。 |

## 文件夹里有什么

```text
羞羞脸变声\
├─ 羞羞脸变声.exe        启动器（launcher\ 是它的源码）
├─ 首次安装.bat          新电脑安装（调用 scripts\setup.ps1）
├─ app\                  变声引擎（Python）+ 编译好的界面（app\web）
├─ ui\                   界面源码（React + Vite）
├─ runtime\              Python 3.9 + PyTorch（安装时下载，不进 git）
├─ dependencies\basemodels\  hubert_base.pt、rmvpe.pt（安装时下载）
├─ Assets\Models\        声音模型，每个模型一个文件夹（.pth，可选 .index 和 cover.jpg）
├─ 配置文件\vbcable\      VB-Cable 虚拟声卡安装程序（安装时下载）
├─ data\                 你的设置、日志、录音（删了就恢复默认，不进 git）
└─ scripts\              安装脚本
```

## 引擎说明

- 变声算法是 RVC（v1/v2 模型都支持，带 .index 的模型可以开「索引强度」）。
- 内容特征用 torchaudio 版 HuBERT，音高用 RMVPE / FCPE。推理流程改编自 [RVC-Project](https://github.com/RVC-Project/Retrieval-based-Voice-Conversion-WebUI)（MIT），相关文件在 `app\xxl\vendor\rvc`，许可证见同目录 `LICENSE.txt`。
- 声卡优先走 WASAPI；个别驱动下 PortAudio 打不开时，改用内置的 [SoundCard](https://github.com/bastibe/SoundCard)（BSD-3，`app\soundcard`）直接连 WASAPI，最后才退回兼容模式。
- 本地接口只监听 `127.0.0.1:18674`，除了首次安装下载运行环境，不联网。

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

运行环境安装包在 Release `backend-v2`（`VoiceChanger.zip.001`～`.022`，安装脚本会校验大小和 SHA256）。只发布你有权分发的运行环境、模型和驱动。
