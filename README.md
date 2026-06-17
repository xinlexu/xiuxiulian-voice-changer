# 羞羞脸变声器

本地优先的 RVC 变声器控制台。项目包含原创 React 前端、Node bridge 和 Windows 安装脚本。

## 从零安装

软件安装包：[点击下载 xiuxiulian-voice-changer.zip](https://github.com/xinlexu/xiuxiulian-voice-changer/archive/refs/heads/main.zip)

1. 下载上面的软件安装包。
2. 如果之前解压过旧版本，先删除旧文件夹，再解压到一个固定文件夹，例如：

```text
D:\XiuxiulianVoiceChanger
```

3. 双击：

```text
setup-windows.bat
```

安装器会按固定顺序执行。首次安装会自动下载后端运行包，文件比较大；如果网络中断，重新运行 `setup-windows.bat` 会从已下载的位置继续。

```text
1. 下载路径
2. 安装声卡驱动
3. 软件安装并启动
```

安装完成后浏览器会打开：

```text
http://127.0.0.1:5174/
```

以后再次使用，双击：

```text
start-windows.bat
```

## 后端包说明

默认安装脚本会从 GitHub Release 自动下载后端分卷，合并后解压到软件目录。后端包解压后必须包含：

```text
VoiceChanger
├─ runtime\python.exe
├─ rvc_core
├─ userconfig.json
└─ Assets\Models
```

如果后端包里带有 `VB虚拟.zip` 或 `VBCABLE` 驱动包，安装器会自动提取并启动声卡驱动安装程序。Windows 会弹出管理员权限确认，这是驱动安装必须经过的系统确认。

## 当前能力

- 连接、启动、停止本地 RVC 控制服务。
- 顶部显示运行状态和预估延迟秒数。
- 扫描 `Assets\Models` 下的声音模型，并过滤不可用模型。
- 支持手动导入 `.pth` 模型。
- 读取输入设备、输出设备和 Host API。
- 支持监听模式，临时把输出切到耳机或音箱试听。
- 自动处理部分输入/输出设备采样率不一致的问题。
- 检测虚拟声卡驱动是否已安装。

## 发布说明

源码仓库不提交大型后端、模型和驱动压缩包。发布给朋友时，后端包通过 GitHub Release 小分卷提供：

```text
VoiceChanger.zip.001
VoiceChanger.zip.002
...
VoiceChanger.zip.022
```

可以用这个脚本从本机后端生成 `release\VoiceChanger.zip`，再按发布平台的单文件大小限制切分上传：

```powershell
powershell -ExecutionPolicy Bypass -File tools\package-voicechanger-backend.ps1 -VoiceRoot "D:\VoiceChanger"
```

只在确认有权分发其中 runtime、模型和驱动文件时再发布这个 zip。

## 开发运行

```powershell
npm install
npm run dev
```

## 范围

- 不做会员登录。
- 不复用第三方套壳 UI、图标、素材或私有代码。
- 监听模式目前是“临时切换输出设备”，不是同时输出到虚拟声卡和耳机的双路混音。
