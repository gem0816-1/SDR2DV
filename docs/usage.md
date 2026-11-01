# 参数与转换说明

下列命令从仓库根目录运行。快速安装及基本转换见 [README](../README.md)。

## 工具与依赖

优先读取一键部署生成的 `.tools/tools.json`；未部署时从 `PATH` 查找。`dovi_tool`、`mp4muxer` 仍可用 `-DoviTool`、`-Mp4Muxer` 显式覆盖。
MP4 必须使用 Dolby mp4muxer 先写入 DOVI 配置；TrueHDR 的 MKV 路径也需要它恢复正确视频时间戳。
普通 MKV 可不依赖 mp4muxer，但封装后仍会检查时长。

一键部署可自动准备项目内的 Python 3.11；独立安装脚本仍可用已有 Python 创建 `SDR2DV/.venv-truehdr`：

```powershell
.\SDR2DV\Setup-TrueHDR.ps1 -Python "D:\Python311\python.exe"
.\SDR2DV\Setup-TrueHDR.ps1 -CheckOnly
```

依赖为 `nvidia-vfx==0.2.0.0`（NVIDIA 包索引）、`numpy==2.4.6` 和 `cupy-cuda12x[ctk]==14.2.0`。
SDK 的 Windows 条件见 [官方说明](https://docs.nvidia.com/maxine/vfx/latest/WindowsVFXSDK/GetStartedonWindows.html)，其中列有外接显示器条件；单台机器推理通过不能证明其他配置兼容。

## 常用参数

| 参数 | 默认 | 作用 |
| --- | --- | --- |
| `-InputPath` | 必填 | 视频文件或文件夹 |
| `-Format` | `mp4` | `mp4` / `mkv` / `both` |
| `-Profile` | `8.1` | `8.1` 为 PQ，`8.4` 为 HLG |
| `-HdrMode` | `Original` | `Original` / `TrueHDR` |
| `-BrightnessMapping` | `Reference` | TrueHDR 亮度风格：`Reference` / `Neutral` |
| `-BrightnessStrength` | `1` | 参考曲线强度 0..1，0 等同 Neutral |
| `-SkinToneCorrection` | `0.5` | Reference 暖色去黄强度 0..1；0 关闭 |
| `-Bilibili` | 关 | 固定 8.4、HLG、目标 28000 kbps、VBV 30000 kbps |
| `-Preset` | `slow` | x265 预设；`medium` 为质量与速度折中 |
| `-Crf` | `17` | 质量模式，越低通常越大；与目标码率模式互斥 |
| `-VideoBitrate` | `0` | kbps，正数启用两遍 ABR；不能与 `-Crf` / `-Lossless` 同用 |
| `-MaxVideoBitrate` | 自动 | 目标码率模式的 VBV 上限；B站预设上限 30000 kbps |
| `-WhitePoint` | `203` | 普通模式 SDR 白点，可选 `100` / `203` nit |
| `-PeakNits` | `0` | 普通模式可选 `0` / `1000`；B站默认 `1000` |
| `-StatsFps` | `4` | 每秒亮度采样帧数 |
| `-Mp4AudioCodec` | `auto` | `auto` / `copy` / `alac` / `eac3` / `ac3` |
| `-AudioBitrate` | `640` | 仅显式 `eac3` / `ac3` 使用，单位 kbps |
| `-OutputDir` | 源目录 | 成片目录 |
| `-KeepTemp` | 关 | 保留本次缓存、中间文件及日志 |
| `-Force` | 关 | 覆盖已有成片 |
| `-Lossless` | 关 | x265 无损编码，体积很大；不表示整个 SDR→DV 流程无损 |

完整帮助：`Get-Help .\SDR2DV\SDR2DV.ps1 -Full`。
文件夹模式只处理当前层的视频文件，跳过识别出的 `_DV81` / `_DV84` / `_TrueHDR_HDR10` / `_TrueHDR_Ref_HDR10` 成片。

## TrueHDR 参数与缓存

默认传给 SDK：`-TrueHDRLuminance 1000 -TrueHDRContrast 100 -TrueHDRSaturation 100 -TrueHDRMiddleGray 50`。
默认启用 AI Debanding，`-TrueHDRDebandingOff` 可关闭。SDK 失败会报错，不替换成其他算法。
TrueHDR 固定输出 8.4，不接受叠加的 `-WhitePoint` / `-PeakNits` 高光曲线。
`-TrueHDRPython` 可指定其他独立 Python 环境；`-KeepHDR10` 另存独立 HDR10/PQ MP4。

SDK 使用逐帧内存管道，缓存为最终编码所需 10bit 4:2:0 像素的无损 HEVC，两遍编码复用缓存，不重复执行 AI。
这减少了 RGB 中间文件的占用，但缓存仍可能大于源视频，不能按固定压缩比预估所有素材。
运行中会估算剩余空间；临时盘低于 1 GB 时终止。复杂画面变化和其他程序写入仍可能导致空间不足。

要保留重试能力，首次执行时主动加 `-KeepTemp`；默认失败清理后无法复用本次缓存。
已有完成缓存可这样续跑：

```powershell
.\SDR2DV\SDR2DV.ps1 -InputPath "D:\movie.mov" -HdrMode TrueHDR -Bilibili -Preset medium -TrueHDRCache "D:\saved-work"
```

工具核验源文件 SHA256、缓存 SHA256 和全部 SDK 参数，不一致会拒绝；外部传入缓存不会被本次清理删除。
旧 PQ FFV1 缓存也可读取；未保存 HDR10 的缓存不能补出 HDR10 对照文件。

### 参考亮度曲线

TrueHDR 默认使用 `Reference`：SDK 输出先转换成绝对显示线性 RGB，再按亮度应用全片固定的单调曲线，最后转换到 HLG。
PowerShell、TrueHDR 拖拽入口以及流式/旧 PQ 适配器使用相同默认值：参考曲线强度 1、肤色去黄强度 0.5。
它是基于参考片亮度特征的创作预设，不是 HDR 标记修改，也不是逐帧自动曝光。黑色仍为零，亮度曲线通过 RGB 同比例缩放保持色度，中高亮展开，高光在 1000 nit 附近柔和收拢。
随后按色相、饱和度和亮度软选取暖色肤色范围，轻微减绿、补蓝，并归一化保持显示线性亮度。默认强度 0.5，`-SkinToneCorrection 0` 关闭；灰阶与高饱和原色不参与修正。这是颜色范围选择，不识别人脸，相似颜色的物体也可能受影响。
新转换在 GPU 上、首次 4:2:0 量化之前应用，HDR10 对照也使用同一亮度曲线；参考模式的 HDR10 mastering peak 为 1000 nit。
`TrueHDRLuminance` 仍控制 SDK 本身，参考曲线的输出参考峰值固定为 1000 nit。曲线强度用于显示线性亮度混合，0 完全旁路。

曲线依据 20 帧分散采样的 1000 nit HLG 参考模型及非近黑区域分位数拟合。参考片超出标称 RGB 信号范围的像素不直接复制为更高峰值，高光保留软肩。
由于参考与测试 SDR 不是同画面素材，预设对齐的是亮度风格和亮区层次；不同场景的平均亮度不会相同，也不证明实机 Dolby 渲染逐镜头一致。

```powershell
.\SDR2DV\SDR2DV.ps1 -InputPath "D:\movie.mov" -HdrMode TrueHDR -Bilibili -BrightnessStrength 0.7
.\SDR2DV\SDR2DV.ps1 -InputPath "D:\movie.mov" -HdrMode TrueHDR -Bilibili -BrightnessMapping Neutral
```

参考模式使用 `_TrueHDR_Ref_DV84` / `_TrueHDR_Ref_HDR10` 文件名；Neutral 保留原命名，已有成片不会被默认覆盖。
新缓存记录曲线 ID、哈希、亮度及肤色修正强度，复用时防止重复叠加。已映射缓存只能按原参数复用，不能反向恢复中性画面。
旧中性 HLG 缓存可在最终编码时用 129³ LUT 应用曲线，避免重跑 AI；这一路径增加一次 RGB/4:2:0 往返及 CPU 滤镜处理，和新转换的 GPU 路径不是逐像素等价。
旧 PQ FFV1 缓存转换时直接应用 GPU 曲线。两种缓存路径都会重新测量最终基础层，并保留原有 RPU 统计限制。

## 音频与 Hi-Res

`auto` 仅在 B站 MP4 中将整数 PCM 转为 ALAC。16bit 保持 16bit，24bit 保持 24bit，不重采样、不调整音量、不混音。
已有 ALAC、FLAC、AAC 等音轨直接复制；普通模式及 MKV 原音复制。
显式 `alac` 仅接受受支持的 16/24bit 整数 PCM、ALAC、FLAC 和声道布局；不静默截断浮点或 32bit 源。
TrueHDR 仅允许 `auto` / `copy` / `alac`。`eac3` / `ac3` 属于显式有损音频选项，仅用于普通画面转换模式。

PCM 直接复制进 MP4 会使用 `ipcm`，其位深曾出现读取器识别差异（[FFmpeg 问题记录](https://ffmpeg.org/pipermail/ffmpeg-trac/2024-February/068631.html)）。
ALAC 策略用于避开该封装路径，不能保证解决所有平台 Hi-Res 故障；平台转码仍需实测。
16bit 源不会被伪装成 24bit Hi-Res，音频无损也不表示平台输出一定保留同样规格。

已有 DV MP4 且源音频确为 48kHz/24bit 整数 PCM 时，可只复制视频、无损重新封装音频：

```powershell
ffmpeg -i "D:\movie_DV84.mp4" -i "D:\movie.mov" -map 0:v:0 -map '1:a?' -c copy -c:a alac -sample_fmt:a s32p -bits_per_raw_sample:a 24 -tag:v hvc1 -strict unofficial -movflags +faststart "D:\movie_DV84_ALAC.mp4"
```

输出后核对音频参数、解码采样值及 `DOVI configuration record`，再替换投稿。此命令不执行 TrueHDR 或视频重编码。

## 画面映射与元数据限制

普通模式将 SDR 白点映射到 203 nit。`-PeakNits 1000` 在线性 BT.2020 MaxRGB 大于 0.9 的区域逐渐扩展高光，等比缩放 RGB 保持色相；`-PeakNits 0` 关闭扩展。
曲线预留 5% 编码余量，有损重建仍可能超调。它无法区分白色物体与镜面反射，也不能恢复 SDR 已丢失的高光细节。

TrueHDR 输出 BT.2020/PQ RGB10A2，经 PQ EOTF、HLG 逆 OOTF 与 OETF 转为 HLG。
HLG 参考模型为理想黑、1000 nit 参考白、系统 gamma 1.2；超出色体积时等比缩放 RGB，保留色度但降低这些像素的亮度。
参考：[NVIDIA TrueHDR](https://docs.nvidia.com/maxine/vfx/latest/Filters/TrueHDR.html)、[Python API](https://docs.nvidia.com/maxine/vfx-python/latest/api.html)、[BT.2100](https://www.itu.int/rec/R-REC-BT.2100)。

RPU 使用全片单段静态采样估计：L1 不是逐镜头分析，CM v4.0 的平均值下限为 1229，L6 MaxFALL 使用全片平均值近似。
TrueHDR 的 L9 参考原色为 BT.2020；8.4 reshaping 采用 dovi_tool 静态模板，不等同专业动态调色。
P8.4 会从编码后的 HLG 基础层回读亮度；默认每秒 4 帧的采样不能证明未采样帧没有瞬时高光。

## 检查

MP4 检查 DOVI 配置记录；MKV 提取流内 RPU 并核对帧数，检查时长偏差小于 0.1 秒。
本地元数据通过不等于电视、手机或 B站必定启用 Dolby Vision，需另外播放确认。
TrueHDR 对色彩标记、CFR、PTS、尺寸、像素比例和旋转均有检查；普通模式遇 VFR 仅警告，长片需自行核对同步。

```powershell
.\SDR2DV\.venv-truehdr\Scripts\python.exe -B -m unittest discover -s SDR2DV -p "test_*.py"
.\SDR2DV\.venv-truehdr\Scripts\python.exe -B .\SDR2DV\analyze_hdr.py "D:\movie_TrueHDR_DV84.mp4" --output-prefix "D:\movie_metrics"
```

分析器测量解码像素在参考显示模型下的亮度，不以峰值元数据代替像素；结果不等于实机屏幕实测 nit。
