# 羞羞脸变声器

本地优先的 RVC 变声器控制台。项目包含原创 React 前端、Node bridge 和 Windows 安装辅助脚本，用来控制本机 VoiceChanger/RVC 后端。

## 普通用户从零安装

适用场景：朋友电脑上没有 Node.js、没有虚拟声卡、没有后端路径配置。

1. 下载本项目的 Release 压缩包并解压。
2. 如果 Release 里没有自带 `VoiceChanger` 文件夹，请把后端包 `VoiceChanger.zip` 放到项目根目录。
3. 双击运行：

```text
setup-windows.bat
```

脚本会自动处理：

- 检查 Node.js；没有时尝试用 `winget` 安装 Node.js LTS。
- 执行 `npm install`。
- 自动查找 `VoiceChanger` 后端文件夹。
- 如果根目录有 `VoiceChanger.zip`，自动解压成 `VoiceChanger` 文件夹。
- 保存后端路径到 `%APPDATA%\xiuxiulian-voice-changer\settings.json`。
- 检测 VB-Cable / VoiceMeeter 虚拟音频设备。
- 打开浏览器并启动本地服务。

启动后浏览器地址：

```text
http://127.0.0.1:5174/
```

以后再次使用可以双击：

```text
start-windows.bat
```

## 后端包要求

后端文件夹应至少包含：

```text
VoiceChanger
├─ runtime\python.exe
├─ rvc_core
├─ userconfig.json
└─ Assets\Models
```

程序会自动查找以下位置：

```text
项目目录\VoiceChanger
项目目录的上一级\VoiceChanger
D:\VoiceChanger
C:\VoiceChanger
E:\VoiceChanger
F:\VoiceChanger
```

如果你在 GitHub Release 里提供后端下载链接，也可以这样安装：

```powershell
powershell -ExecutionPolicy Bypass -File tools\setup-from-zero.ps1 -VoiceChangerZipUrl "https://example.com/VoiceChanger.zip" -OpenDriverDownload -StartApp
```

## 虚拟声卡

如果要把变声后的声音送进游戏、Discord、QQ 语音等软件，通常需要一个虚拟扬声器/虚拟麦克风。推荐使用 [VB-Audio 官方的 VB-CABLE](https://vb-audio.com/Cable/)。

本项目不会静默安装音频驱动，因为驱动安装需要管理员权限，也应该由用户确认。可以用三种方式处理：

- 首次运行 `setup-windows.bat` 时按提示安装。
- 在页面“音频路由”区域点击“安装虚拟声卡”。
- 双击运行 `install-virtual-audio.bat`。

安装步骤：

1. 从 VB-Audio 官方页面下载 Windows 版 VB-CABLE。
2. 解压安装包。
3. 右键 `VBCABLE_Setup_x64.exe`，选择“以管理员身份运行”。
4. 点击 `Install Driver`。
5. 重新打开本应用；如果设备列表仍然没有出现，重启 Windows。

安装完成后，在“主输出设备”里选择 `CABLE Input`、`VB-Audio Cable`、`伴侣扬声器` 或类似名称的虚拟扬声器。

## 当前能力

- 连接、启动、停止本地 RVC 控制服务。
- 顶部显示运行状态和预估延迟秒数。
- 扫描 `Assets\Models` 下的声音模型，并过滤不可用模型。
- 自动识别 `配置文件\111` 里的 `.pth` 模型，并把裸文件名解析成真实模型路径。
- 支持在界面里手动导入 `.pth` 模型。
- 读取输入设备、输出设备和 Host API。
- 支持监听模式，临时把输出切到耳机/音箱试听。
- 自动处理部分输入/输出设备采样率不一致的问题，必要时切到兼容 Host API。
- 支持在界面里设置 VoiceChanger 后端路径。
- 检测 VB-Cable / VoiceMeeter 这类虚拟音频设备，并提供安装入口。

## 发布给朋友

源码仓库不要提交大型后端和模型。推荐用 GitHub Release：

1. 上传本项目源码。
2. 准备一个后端包 `VoiceChanger.zip`，作为 Release asset 上传。
3. 朋友下载项目 Release 后，把 `VoiceChanger.zip` 放在项目根目录，双击 `setup-windows.bat`。

可以用这个脚本从本机后端生成 zip：

```powershell
powershell -ExecutionPolicy Bypass -File tools\package-voicechanger-backend.ps1 -VoiceRoot "D:\VoiceChanger"
```

只在你确认有权分发其中 runtime、模型和相关文件时再发布这个 zip。

## 开发运行

```powershell
npm install
npm run dev
```

如果要临时指定后端路径：

```powershell
$env:VOICE_ROOT="D:\VoiceChanger"
npm run dev
```

## 范围

- 不做会员登录。
- 不复用第三方套壳 UI、图标、素材或私有代码。
- 监听模式目前是“临时切换输出设备”，不是同时输出到虚拟声卡和耳机的双路混音。

## 下一步

- 打包成真正的 Windows 桌面应用，减少对 Node.js 的依赖。
- 加入低延迟/稳定/游戏语音预设。
- 给启动失败、设备冲突、模型损坏等情况增加更细的错误提示。
