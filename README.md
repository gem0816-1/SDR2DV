# SDR2DV

Windows 下将 SDR 转为 Dolby Vision 8.4 / HLG 或 8.1 / PQ，输出 MP4 / MKV。
支持 NVIDIA TrueHDR、NVENC / x265、源视频流码率匹配及音频完整性检查。

## 安装

需要 Windows 10/11 x64、64 位 PowerShell 5.1 或 7，首次部署需要网络。
下载并解压 [Windows 发布包](https://github.com/gem0816-1/SDR2DV/releases/latest)，或克隆源码，然后运行部署。

```powershell
.\deploy.bat
```

部署复用可用工具，缺少时下载并校验 FFmpeg、dovi_tool、mp4muxer 和项目内 Python 环境。
不修改系统 PATH，不向系统 Python 安装包。TrueHDR 另需兼容 NVIDIA GPU / 驱动，部署会实际检查推理。
仅准备普通转换：`deploy.bat -Mode Standard`。详见 [部署说明](docs/deployment.md)。

## 使用

双击 `SDR2DV\TrueHDR-DV.bat`，或将 SDR 视频拖到它上面。
方向键选择编码器、亮度映射、容器、码率和音频策略；灰字解释各选项，Esc 返回，检查摘要后确认开始。
转换中按 `L` 展开日志，`Ctrl+C` 请求安全取消本次任务。

默认：DV 8.4、BT.2020 / HLG、Main10、1000 nit 参考峰值、Neutral、源码率匹配、Auto 优先 NVENC P6。
Natural 提供保护中低亮度的平滑高光曲线，仍待 HDR 设备观感验收，暂不默认启用。
MP4 默认复制兼容音轨；受支持的整数 PCM 无损转 ALAC，并核对解码样本；MKV 原音复制。

保留 PowerShell 非交互运行：

```powershell
# TrueHDR + 源视频流码率匹配
.\SDR2DV\SDR2DV.ps1 -InputPath "D:\movie.mov" -HdrMode TrueHDR -Bilibili
# Natural 试用，并保留 HDR10/PQ 对照
.\SDR2DV\SDR2DV.ps1 -InputPath "D:\movie.mov" -HdrMode TrueHDR -Bilibili -BrightnessMapping Natural -KeepHDR10
# 普通映射，MP4 + MKV；无需 NVIDIA
.\SDR2DV\SDR2DV.ps1 -InputPath "D:\movie.mov" -Format both -Encoder x265
```

`-BitrateMode Platform` 显式使用平台兼容码率；`-BitrateMode Quality -NvencCq 18` 使用质量模式。
`-OutputDir` 指定目录，`-Force` 覆盖，`-KeepTemp` 保留本次缓存；默认清理本次中间媒体。
参数、TUI 和排错见 [使用说明](docs/usage.md)，审计与测试边界见 [验证说明](docs/validation.md)。

## 限制

- SDR 转 HDR 必须重编码；同码率不是数学无损。AI 高光推断无法恢复源中已剪切的真实细节。
- 1000 nit 是参考目标；色度重建和有损编码可能超调，报告使用最终像素测量值。
- Source 模式不静默限制高码率，但短片、简单画面或编码器限制可能无法用完预算；输出显示实际偏差。
- TrueHDR 要求明确 SDR 标记、8bit 输入、连续 CFR、零视频起始 PTS、偶数尺寸、方形像素、无旋转；不支持时拒绝隐式改帧/降位深。
- 无损 Main10 缓存及 HDR10 对照可能很大；磁盘空间保护不保证其他程序不会占满磁盘。
- RPU 为全帧社区镜头分析，非官方认证调色；CMv4 L1 有兼容范围限制。MP4 不保留字幕，MKV 保留。
- 音频不自动降规格；不支持的 PCM / ALAC 布局需选择 MKV 或显式复制并验证，16bit 不冒充 Hi-Res。
- B站 Dolby Vision / Hi-Res 的最终识别、转码和播放仍需 [A/B 投稿实测](docs/audio-ab.md)，本地校验不能保证。
- 正常结束和合作式取消仅清理本次媒体目录；强制结束/断电可能残留缓存。TUI 的小型日志与报告保存在 `.tools/tui-runs/`。
