# 参数与运行

快速开始见 [README](../README.md)。转换唯一入口仍为 `SDR2DV/SDR2DV.ps1`；TUI 不实现推理、编码或封装。

## TUI

双击或拖拽单个视频到 `SDR2DV/TrueHDR-DV.bat`。菜单支持中文、方向键、Enter，摘要确认后启动独立转换进程。
`L` 展开日志，`Ctrl+C` 写入本次取消标记，等待核心清理；超时只终止本次进程树。强制退出仍可能残留本次缓存。
不扫描/清理其他任务目录，不删除输入。界面日志与报告留在 `.tools/tui-runs/<随机ID>/`，依赖保存在项目 venv。
当前 TUI 接收单个视频；文件夹批量继续使用 PowerShell CLI。没有终端时不会自动开始转换。

```powershell
.\SDR2DV\Start-Tui.ps1 "D:\中文 视频.mov"
# 无转换的参数/硬件/媒体预检
.\SDR2DV\.venv-tui\Scripts\python.exe -X utf8 .\SDR2DV\tui.py "D:\movie.mov" --dry-run
# 非交互保留原 CLI
.\SDR2DV\Start-Tui.ps1 -NonInteractive -InputPath "D:\movie.mov" -Encoder NVENC
```

共享默认值由 `SDR2DV/defaults.json` 提供。TUI 固定 DV8.4/HLG/1000 nit，硬件菜单根据真实 Main10/lossless 初始化和 SDK 推理检查禁用不可用项；不会静默用普通映射冒充 TrueHDR。

## PowerShell 参数

`Get-Help .\SDR2DV\SDR2DV.ps1 -Full` 保留全部旧参数。新默认和重要参数：

| 参数 | 默认 / 说明 |
| --- | --- |
| `-HdrMode` / `-Profile` | CLI 为 Original / 8.1；TUI 为 TrueHDR / 8.4 |
| `-Bilibili` | 固定8.4、HLG、1000 nit；不强制28/30 Mbps |
| `-Encoder` | Auto，8.4 优先实测可用 NVENC，否则 x265 |
| `-NvencPreset` / `-Preset` | p6 / medium |
| `-BitrateMode` | Source；可选 Platform / Custom / Quality |
| `-VideoBitrate` | kbps；单独显式正数选择 Custom，显式0选择 Quality |
| `-MaxVideoBitrate` | Source/Custom 默认目标的1.5倍 VBV；显式值不得低于目标 |
| `-Crf` / `-NvencCq` | 显式指定选择 Quality；分别用于 x265 / NVENC |
| `-Lossless` | 编码器视频无损模式，需 Quality，不代表 SDR→HDR 全流程无损 |
| `-BrightnessMapping` | Neutral；Natural 实验；Reference 旧创作预设 |
| `-BrightnessStrength` | 1，0..1，0旁路曲线 |
| `-SkinToneCorrection` | Neutral/Natural 为0；显式 Reference 未指定时沿用0.5 |
| `-KeepHDR10` | TrueHDR 额外输出同次推理的 PQ 对照，x265无损缓存，保留1000nit MDCV |
| `-Mp4AudioCodec` | auto；copy / alac；普通模式保留显式 ac3/eac3 有损选项 |
| `-Format` | mp4 / mkv / both；MKV 始终原音复制 |
| `-OutputDir` / `-Force` | 源目录 / 不覆盖 |
| `-StatsFps` | 旧参数兼容；最终分析不再跳帧，始终逐帧全分辨率 |
| `-SceneCutsFile` | 可选JSON切点帧号，如 `[0,120,285]`；必须从0开始、有序、不越界 |
| `-ActiveArea` | L,R,T,B 像素偏移，默认0,0,0,0；仅影响分析区域及L5，不缩放/裁剪视频 |
| `-ReportPath` / `-EventPath` | 可选JSON验收报告 / JSONL进度事件 |
| `-KeepTemp` | 显式保留本次中间媒体，默认成功/失败/合作式取消清理 |

## 码率与编码

只读取第一视频流 `bit_rate` 和对应流时长；缺失/无效时累计该视频流包大小，以 PTS（退回 DTS）覆盖范围计算平均值。绝不把容器总码率当视频码率。
Source 保留8/15/25/50 Mbps等源目标；高于B站公开历史建议30 Mbps时提示，仍保留目标。Platform 才显式限制目标≤28 Mbps、VBV≤30 Mbps。
NVENC 为 VBR + HQ / fullres 逐帧多遍、lookahead20、空间/时间AQ；不是整片两遍。x265 ABR 使用整片两遍；高码率 NVENC 使用 High tier。
编码后按最终视频包重新测量实际码率，展示源/目标/实际/偏差；偏差>10%会提示。不会插入无意义填充以冒充画质或重跑 AI 强凑码率。
短片、低复杂度或超出编码器可用预算时偏差可能很大。低比特/像素风险提示是启发式检查，不是画质保证。
保留源尺寸、SAR、CFR和完整帧数，最终封装再次核对；VFR/不连续时间线当前拒绝隐式改帧。8.1与独立HDR10保留x265，NVENC只用于8.4。

## 亮度与精度

TrueHDR SDK 输出RGB10A2/PQ；使用ST2084绝对EOTF，float32线性BT.2020调色，再以BT.2100 HLG逆OOTF/OETF转换至GBR16，最后一次4:2:0/10bit量化。
HLG参考显示1000 nit、理想黑、gamma1.2；75% HLG信号约203 nit。超出HLG色体积的RGB等比缩限；不做自动全片曝光/饱和度提升。
Natural 在0..600 nit保持亮度恒等（含暗部/中间调/漫反射白），600..1000采用C1连续单调多项式，以上指数软肩渐近1000；默认不修肤色。
这是中性起点上的高光分配，不是对所有镜头强拉到1000 nit。Reference 是历史创作预设，可能压暗阴影/抬高中间调，仅保留对照。
AI结果是推断，不是SDR剪切区域真实细节的证明。Natural通过数值/管线检查后仍需HDR显示观感和舞台灯光实测，故默认Neutral。
4:2:0色度重建和有损编码可能产生高于目标的像素；最终MaxCLL如实报告，不用1000的目标值覆盖实测值。
缓存为最终Main10像素的无损HEVC，不重复AI。可选HDR10在同一GPU调色结果上生成，避免额外RGB10量化。旧中性HLG缓存应用曲线需一次RGB/YUV往返，并非新路径像素等价。
`-TrueHDRCache` 仍核对源/缓存SHA256和SDK/曲线参数；外部缓存不会被本次清理删除。旧已调色缓存不能反向恢复Neutral。
宽色域SDR（如BT.2020/P3）不送入只接受Rec.709的SDK去隐式裁色，可用Original。TrueHDR要求8bit且明确SDR色彩标记、CFR/零视频起始PTS/偶数尺寸/方形像素/无旋转，拒绝自动降位深、缩放或重定时。

## 音频与元数据

MP4 auto优先复制AAC/ALAC/FLAC/MP3/AC3/EAC3/Opus等兼容音轨；受支持16/24bit整数PCM转ALAC。其他格式拒绝自动有损降级；32bit/浮点/不支持布局可用MKV保留。
不重采样、不改声道、不标准化；所有输出音轨逐条解码到同一源规格PCM，核对SHA256、采样数和相对视频的起始偏移；压缩流复制另比数据包哈希。
MP4 `pcmC` 的位深/字节序、ALAC配置的位深/采样率/声道与解码结果交叉检查。legacy sample_size=16、pcmC=24不能单凭此认定文件损坏；下游可能存在解释风险。
16bit保持16bit；工具的≥24bit/≥48kHz无损规格候选仅为保守预检，非平台官方验收承诺。详见 [音频A/B](audio-ab.md)。
最终基础层每帧全像素以参考显示模型分析。CTA MaxCLL=max(MaxRGB)，MaxFALL=max(每帧mean(MaxRGB))，在线性nit域；CIE Y和PQ均值另记，互不冒充。
GPU原生YUV10重建经FFmpeg双线性参考数值核验；CPU使用zscale。切镜为保守RGB-PQ缩略图检测，抑制单帧闪光，可提供人工切点。
L1使用社区MaxRGB/PQ分析，不等同Dolby专有LMS/CM兼容算法；记录镜头PQ最小/最大/平均及dovi_tool CMv4兼容约束后的编码值，逐帧导出再核对L1与scene_refresh_flag。L6全片MaxCLL/FALL，L9 BT.2020，人工ActiveArea写L5。
不会生成L2/L8创作性Trim；社区分析与dovi_tool 8.4模板不等同Dolby认证制作系统。HDR10对照含MDCV，不凭空填写未分析的MaxCLL/FALL SEI。
本地DOVI/RPU、像素、音频通过不能证明B站或某台设备最终DV渲染正确；见 [验证边界](validation.md)。
