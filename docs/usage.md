# 参数与运行

快速开始见 [README](../README.md)。转换唯一入口仍为 `SDR2DV/SDR2DV.ps1`；TUI 不实现推理、编码或封装。

## TUI

双击或拖拽单个视频到 `SDR2DV/TrueHDR-DV.bat`。菜单支持中文、方向键、Enter，摘要确认后启动独立转换进程。
每个选项及子选项下方有灰色解释；子菜单按 Esc 返回，高级设置可按需修改，不必逐项填写。
当前不生效的 CQ/CRF、曲线或肤色设置会禁用并说明原因；数值输入即时校验，Esc 放弃本项修改。
启动显示程序位置，摘要列出成片路径、已有文件状态和视频体积估算（不含音频、缓存、HDR10 对照）。
平台限码率降低源目标时需要确认；结束后若跳过旧文件或实际码率偏差超过 10%，界面会明确提示。
`L` 展开日志，`Ctrl+C` 写入本次取消标记，等待核心清理；超时只终止本次进程树。强制退出仍可能残留本次缓存。
失败时先保留堆栈、已处理帧数和编码器错误日志到任务目录内的 `diagnostics_*`，再清理本次媒体缓存；普通 CLI 放在 `.tools/failures/`。
开始阶段的“源时间线检查”使用解码核对所有帧时间戳，此时尚未执行 TrueHDR 推理，会单独显示进度。
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
| `-Bilibili` | 固定8.4、HLG；默认1000 nit目标，realDolby保留参考超白；不强制28/30 Mbps |
| `-Encoder` | Auto，8.4 优先实测可用 NVENC，否则 x265 |
| `-NvencPreset` / `-Preset` | p6 / medium |
| `-BitrateMode` | Source；可选 Platform / Custom / Quality |
| `-VideoBitrate` | kbps；单独显式正数选择 Custom，显式0选择 Quality |
| `-MaxVideoBitrate` | Source/Custom 默认目标的1.5倍 VBV；显式值不得低于目标 |
| `-Crf` / `-NvencCq` | 显式指定选择 Quality；分别用于 x265 / NVENC |
| `-Lossless` | 编码器视频无损模式，需 Quality，不代表 SDR→HDR 全流程无损 |
| `-BrightnessMapping` | Neutral；Natural 实验；Reference 旧创作预设；realDolby 配对苹果参考曲线 |
| `-BrightnessStrength` | 1，0..1，0旁路曲线 |
| `-SkinToneCorrection` | Neutral/Natural/realDolby 为0；显式 Reference 未指定时沿用0.5 |
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

TrueHDR SDK 输出RGB10A2/PQ；使用ST2084绝对EOTF，float32线性BT.2020调色，再以BT.2100 HLG逆OOTF/OETF转换，最后一次4:2:0/10bit量化。
HLG测量参考为1000 nit、理想黑、gamma1.2；75% HLG信号约203 nit。Neutral/Natural/Reference 沿用GBR16及名义HLG色体积等比缩限；不做自动全片曝光/饱和度提升。
`realDolby` 使用GBR float32管道，允许参考片实际存在的扩展HLG信号，不在RGB/HLG/PQ中截到1000 nit；1000仅为SDK校准基准和测量参考，不是本模式成片峰值限制。详见[配对校准说明](realDolby.md)。
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

## 失败保护与恢复

全部新产物（含 HDR10）校验完成后，才复制到输出目录下的独立 `.sdr2dv-txn-*` 暂存目录，核对 SHA256 并交付。
跨卷只复制到暂存文件；正式文件通过同卷移动/替换交付，Force 替换先保留旧文件备份。多文件交付使用日志和失败回滚，**不是整体原子事务**；交付期间其他播放器可能观察到新旧文件的组合。
若回滚受文件锁/断网影响或进程强制结束，保留恢复目录并拒绝直接重跑覆盖。先关闭占用文件的播放器，再执行日志提示的确切目录：

```powershell
.\SDR2DV\Repair-Delivery.ps1 -Directory "D:\output\.sdr2dv-txn-实际GUID"
```

恢复会核对旧备份/新文件 SHA256；遇到无法识别的外部修改会保留文件并报错。断电、文件系统故障、网络共享的服务端行为不提供全局原子/持久性保证。
输出路径使用跨进程锁；微小 `.sdr2dv-lock-*` 文件长期保留以避免删除锁的竞争，不能在任务运行时手工移除。GPU 0 与可能使用 CUDA 分析的核心任务采取保守串行策略；其他 SDR2DV 转换运行时会明确报忙，不自动争抢显存或降级编码器。
批量目录内同名不同扩展名用 `__src_mp4` / `__src_mkv` 等后缀区分；必要时再加摘要避免与真实文件名碰撞。全部已存在的输入只计“跳过”，不计“成功”。生成文件的保留后缀不参与批量扫描；需要处理同名的原始文件时请显式指定单个路径。
临时卷和输出卷均有空间预检与运行中监测，输出卷暂存时额外核对完整文件容量。预估不能预留其他程序的写入空间；磁盘耗尽将按失败退出并保护旧成片。强杀/断电留下的普通媒体缓存不会自动批量删除。
输出目录及缓存路径含 junction/symlink 时拒绝使用，请改用物理路径；清理遇到内部链接也会保留目录。长路径/共享盘仍取决于 PowerShell、Windows 及文件系统能力，失败不会降级成不安全的直接覆盖。

普通模式也要求明确的 SDR transfer/primaries/matrix/range；未标记素材须先根据真实拍摄设置确认解释，不能通过猜测 BT.709 绕过检查。
不支持的字幕编码在视频编码前报错。MKV 的 mov_text/text 显式转 SubRip（容器样式可能丢失，转换前提示），ASS/SSA 与其他受支持字幕原码复制；保留语言、标题、轨道标记及全部附件，并核对附件 SHA256。MP4 仍不保留字幕。
HDR 分析默认只记录逐镜头和全片统计；需要逐帧诊断时可直接调用 `hdr_analysis.py --frame-statistics frames.jsonl`。数值口径不变，逐帧 JSONL 不载入 PowerShell；RPU 导出的逐帧 L1 也按流校验，内存主要随镜头数增长。
新 TrueHDR 缓存将 SDK/曲线/时间线等参数摘要和任务身份写入媒体标签，并与媒体 SHA256 一起核验；使用期间保持只读共享句柄。旧缓存继续兼容但明确提示其解释元数据未与媒体内部绑定，建议重新生成。
日志/报告路径不能与输入、切点或缓存冲突；已有非本工具报告不会被覆盖。可选事件/报告写入失败只警告，不把已经完成的媒体交付误报为失败。
