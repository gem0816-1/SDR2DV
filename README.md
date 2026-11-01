# SDR2DV

Windows 下将 SDR 视频转换为 Dolby Vision Profile 8.1（PQ）或 8.4（HLG），输出 MP4 / MKV。
支持普通映射、NVIDIA TrueHDR 和 B站投稿预设，保留源分辨率和帧数。

## 安装

需要 Windows 10/11、PowerShell 5.1 或 7，以及以下外部工具：

- [FFmpeg](https://ffmpeg.org/download.html)：包含 `ffmpeg`、`ffprobe`，支持 `libx265` 和 `zscale`；已验证 7.1 full。
- [dovi_tool](https://github.com/quietvoid/dovi_tool/releases)：已验证 2.3.4。
- [Dolby mp4muxer](https://github.com/DolbyLaboratories/dlb_mp4base)：MP4 和 TrueHDR 模式需要，自行准备 Windows 可执行文件。

将工具加入 `PATH`；也可用 `-DoviTool`、`-Mp4Muxer` 指定后两者。仓库不附带这些工具或 NVIDIA SDK。

```powershell
git clone https://github.com/gem0816-1/SDR2DV.git
cd SDR2DV
Set-ExecutionPolicy -Scope Process Bypass
```

## 使用

```powershell
# 普通转换：输出同目录下的 movie_DV81.mp4
.\SDR2DV\SDR2DV.ps1 -InputPath "D:\movie.mov" -Preset medium

# B站预设：Profile 8.4 / HLG，视频目标 28 Mbps、上限 30 Mbps
.\SDR2DV\SDR2DV.ps1 -InputPath "D:\movie.mov" -Bilibili -Preset medium

# 同时生成 MP4 和 MKV
.\SDR2DV\SDR2DV.ps1 -InputPath "D:\movie.mov" -Format both -Preset medium
```

也可将视频拖到 `SDR2DV\一键转DV.bat` 或 `SDR2DV\Bilibili-DV.bat`。输入文件夹可批量处理；已有成片默认跳过。

## NVIDIA TrueHDR（可选）

另需 Python 3.11、兼容 NVIDIA GPU/驱动和网络；先核对 [SDK 的硬件及外接显示器条件](https://docs.nvidia.com/maxine/vfx/latest/WindowsVFXSDK/GetStartedonWindows.html)。
安装脚本创建独立虚拟环境并执行真实推理检查，普通模式不需要 Python 或 NVIDIA GPU。

```powershell
.\SDR2DV\Setup-TrueHDR.ps1
.\SDR2DV\SDR2DV.ps1 -InputPath "D:\movie.mov" -HdrMode TrueHDR -Bilibili -Preset medium
```

也可拖到 `SDR2DV\TrueHDR-DV.bat`。默认目标 1000 nit，输出 `movie_TrueHDR_DV84.mp4`。

## 配置与限制

- `-OutputDir` 指定输出目录，`-Force` 覆盖已有成片；`-Mp4Muxer "D:\tools\mp4muxer.exe"` 指定封装工具。
- B站 MP4 默认将 16/24bit 整数 PCM 无损编码为 ALAC，保留采样值、采样率、位深和声道；其他音轨、普通模式及 MKV 直接复制。`-Mp4AudioCodec copy` 可强制原编码复制。
- 默认成功或失败均清理本次缓存、日志和中间文件，保留源视频与已校验成片；`-KeepTemp` 主动保留排错文件。强制结束或断电无法保证清理。
- TrueHDR 缓存可能很大，需预留临时空间；输入须有明确 SDR 色彩标记、连续固定帧率、零起始 PTS、偶数尺寸、方形像素且无旋转。
- 已是 HDR 的输入会被拒绝；MP4 不保留字幕，MKV 保留。ALAC 路径不自动降低 32bit/浮点 PCM 的规格或混音，不支持时会报错。
- SDR 转 HDR 需要视频重编码；RPU 为采样估计的静态元数据，不等同专业逐镜头 Dolby 调色。目标 1000 nit 不代表屏幕实测亮度。
- B站的 Dolby Vision / Hi-Res 识别与实际播放需上传后验证，不能仅凭本地元数据保证。

完整参数、音轨兼容处理和转换原理见 [使用说明](docs/usage.md)。
