# 审计与验证（更新于2026-11-04）

2026-11-04：新增以 v2.1.0 / `80f30fa` 为基线的 [可靠性审查与本地回归](reliability-audit.md)，包括输出回滚、并发、日志/进程、字幕、缓存绑定和流式统计。本页后续条目是此前功能验收的历史记录。

初始审计基线：v1.10.0 / `c41284535b4f4abf598d05146cafad65f5d90ae1`，开始时当前工作树无未提交改动。本页记录开发与本地验收结果；发布版本并不代表已通过平台验收或 Dolby 专业认证。
日志、媒体和机器报告在本机 `validation/audit/`（Git忽略）；下列结果不能代替HDR设备或B站最终播放验收。

## 实施结果

| 阶段 | 本地已验证 | 仍有限制 / 待验收 |
| --- | --- | --- |
| 亮度 | 默认保持1000 nit目标；Natural 0..600nit恒等、C1软肩、默认不变色；PQ/HLG标准数值、GPU转换及HDR10对照 | Natural仍为实验，不默认启用；HDR肤色/灯光观感待实机，SDR剪切信息不可真实恢复 |
| realDolby | 配对参考曲线与float32扩展HLG，允许超过1000 nit；8秒4K/60样片完整转换及用户观感反馈通过 | 仅本配对样片；其他素材、DV标识和B站播放仍需分别验收，见下方2026-11-03记录 |
| 码率 | Source默认，取视频流字段、缺失/无效时按视频包字节/时间估算；NVENC VBR、x265整片两遍；最终包重新测量 | 不保证任何素材实际值精确等于目标；短片/低复杂度/过大预算可明显不足，均显示偏差，不填充掩盖 |
| 音频 | copy优先；PCM→ALAC bit exact；16bit保持16bit；多轨、字节序、位深、布局、采样数和偏移检查；ISO PCM/ALAC描述审计 | B站Hi-Res最终处理音轨尚未取得；不能确认电流声根因或宣称平台问题已彻底解决 |
| RPU | 最终基础层全帧全像素MaxRGB统计、镜头检测/手工切点、逐镜头L1、L6、L9及可选L5；封装后再提取核对L1/镜头/帧数 | 社区PQ/MaxRGB分析，不是Dolby专有LMS/CM兼容算法；L1兼容范围限制单独记录，未虚构L2/L8或L3补偿 |
| TUI | Rich+Questionary、中文/方向键、真实能力菜单、共享默认值、音频/TrueHDR输入预检、摘要确认、实时阶段/速度、日志展开、独立取消 | 当前交互入口处理单文件；文件夹批量保留CLI；强制结束/断电不能保证清理 |

## 实际测试

环境：Windows x64，PowerShell5.1/7，FFmpeg7.1-full、dovi_tool2.3.4，RTX4060 Laptop / NVIDIA596.49，Python3.11.9、SDK1.3.0、CuPy14.2。

- 本机TUI环境（启用真实NVENC与取消测试）运行49项测试：47通过，2项CuPy测试跳过；SDK/CUDA环境单独补测这2项通过。包含真实FFmpeg音频、dovi_tool生成/导出、NVENC10bit无损往返、独立进程取消，而不全是CPU模拟测试。
- CPU数值：黑位/阴影/203nit白点、Natural单调与C1连续性、PQ/HLG往返、MaxFALL线性MaxRGB口径、暗场切镜/闪光抑制、码率包时间跨度等通过。
- CUDA：与FFmpeg双线性BT.2020参考核对left/center/topleft采样位置，以及饱和色、full/limited范围、PQ/HLG结果通过。
- 音频：48kHz/24bit PCM→ALAC逐样本一致；16bit、big-endian PCM、多AAC轨复制/包哈希、ipcm/pcmC描述通过；主动改信号、截短音频、偏移时间、改坏pcmC会拒绝。
- 实际转换：720p/30、4K/60影片切片、600帧10秒样本；DV8.4 MP4/MKV和HDR10/PQ对照、8.1/PQ x265、8.4 x265、NVENC及缓存复用均执行。无演唱会追光/LED/烟雾验收。
- 双镜头合成片：60帧/2镜头，最终MP4/MKV提取的scene_refresh_flag和每帧L1与配置一致；源/输出尺寸、SAR、帧率、帧数与视频时长检查通过。
- 视频无损选项实际转换后，将最终MP4与Main10缓存解码到yuv420p10le，SHA256一致；HDR10首帧MDCV实读1000nit。
- 真正Windows终端运行了菜单方向键、NVENC选择、确认、L展开和实际转换；菜单取消及核心合作式取消均检查。核心退出130、仅删除自身GUID媒体目录，其他任务哨兵/输入未改；强制取消测试保留无关进程。
- PowerShell5.1/7无参均输出用法（退出1，非语法错误）；5.1 UTF-8 JSON/中文路径实际解析通过。Python AST、PS解析、BOM/换行与BAT ASCII检查通过。
- 部署缓存/拒绝坏哈希/ZIP穿越等5项通过；现有TUI环境复用、pip check、Standard CheckOnly通过。未做本次版本的全新联网便携安装/新GPU机器部署。
- `Desktop/1/dubi`的dovi_tool、其exebak、mp4muxer、mp4demuxer SHA256均与保护快照一致。

本地主要报告：`acceptance.json`、`multi-shot.json`、`source-match-5s.json`、`source-match-5s-hdr-diagnostics.json`、`final-natural.json`、`x265-pq.json`、`x265-hlg.json`、`tests-final.log`、`hdr-gpu-tests.log`、`core-cancel-test.log`。

## 实际码率与峰值边界

| 真实测试 | 源 / 目标 Mbps | 输出视频流 Mbps | 偏差 |
| --- | --- | --- | --- |
| 4K/60五秒Source（默认Neutral） | 12.356 / 12.356 | 12.320 | -0.29% |
| 4K/60两秒Source（Natural） | 13.198 / 13.198 | 11.240 | -14.84% |
| 4K/60十秒高码率Source（Natural） | 146.447 / 146.447 | 32.573 | -77.76% |
| 4K/60一秒x265 Platform | 197.246 / 28.000 | 27.700 | -1.07% |

最后一行仅为显式Platform；高码率Source从未静默降成28/30 Mbps。码率相同并不保证像素无损，预算不足也不能保证HDR画质。

映射前/中的1000nit上限不能保证有损编码和4:2:0重建后所有像素硬≤1000nit。实际五秒低码率样本测得MaxRGB1596.96、最大CIEY1466.93nit；最严重一帧MaxRGB>1000的占比约0.00887%，CIEY>1100占比约0.00109%。
这证明有少量重建超调，不能把目标值当实测值，也不能据单个样本保证其他视频无大面积剪切。报告保留真实MaxCLL/FALL、CIEY和超参考像素占比，未将1000改成650来隐藏问题。
旧float32 RGB分析同一4K/60一秒缓存耗时33.72秒；原生YUV/CUDA版本约3.59秒（后加超调计数会有开销）。新方法指定双线性重建并单独核对CPU参考，未宣称旧滤波设置与新方法逐像素相同。

## 外部验收

1. 在同一HDR/DV设备和播放器核对Neutral、Natural、realDolby、HDR10/PQ与DV渲染，记录肤色、暗部、LED高饱和光和闪切。realDolby的本次8秒配对样片已有用户正向观感反馈；Natural、其他素材、HDR10/PQ目视对照和DV标识仍需分别验收。
2. 使用本机 `validation/audit/audio-ab/A_ALAC_DV84.mp4`、`B_PCM_ipcm_DV84.mp4`；两者本地音频与源解码一致、都有DV8.4配置。依据 [A/B流程](audio-ab.md) 投稿，取得最终音轨后再定位服务器/播放端。
3. 若要专业Dolby交付，仍需经授权的分析/Trim/Metafier与监看链路验证。工具未实现或宣称官方认证系统，也没有用元数据存在替代实机验收。

## 2026-11-02 TUI 说明与交互复验

新增各级选项的灰色说明、高级设置独立菜单、Esc 返回、即时数值校验和当前不生效参数的禁用原因。
平台限码率降低源目标时明确确认；摘要显示完整成片路径、已有文件状态和仅视频体积估算；输出偏差超过 10% 单独提示。
测试发现 59 项：55 项通过，4 项条件测试跳过（两项 CUDA 数值、一项 NVENC 无损缓存、一项核心取消）；未把跳过计为通过。
真实 Windows 终端验证了灰色文字、方向键、高级菜单及取消。另由新 TUI 完成 1 秒/30 帧 TrueHDR→NVENC→DV8.4 MP4，PCM24→ALAC 采样值/采样数一致、音画偏移为零。
该极短样本目标 0.974 Mbps、实际 0.143 Mbps，界面正确报告 -85.32% 偏差；这次测试证明启动/反馈链路可用，不证明同码率质量或 HDR 观感验收。
本轮证据保存在本地 `validation/tui_help_20261102/` 与 `.tools/tui-runs/ad2ac8d4e54548cc854b3b7cbec6589b/`，没有执行新的 B站投稿或 HDR 设备目视验收。

## 2026-11-02 Windows 管道与进度文件修复

复现了大帧管道写入 `Errno 22`、解码管道提前截断，以及 PowerShell 读进度文件时 Python 写入触发的 `PermissionError`。另有保留堆栈证实父 `stderr` 状态输出也可能发生 `Errno 22`。
Python 读/写/预分配帧读取与 FFmpeg 原始像素管道统一限制每次 1 MiB，完整计数短写，失败不重试不完整媒体帧。日志改为独立文件加容错转发；失败先复制小型诊断再清媒体。
进度采用完整文件更新，PowerShell 读取允许写入和删除共享；短暂共享冲突只丢一次可选进度更新，不中止媒体转换。
本轮 71 项测试中 68 项通过，3 项条件测试跳过（两项 CUDA 数值、一项 NVENC 无损缓存）；核心合作取消和 Windows 真正关闭 stderr 管道、实际文件共享锁测试已执行。
4,096 帧的约 156 GiB 二进制管道压力测试通过，逐帧 CRC32 一致；真实 3840×1770 / 60fps 的 120 帧测试在父日志报错条件下仍完成，新旧缓存解码像素 SHA256 一致。
修复后经 TUI runner 完成现有素材 10 秒 / 600 帧 TrueHDR→NVENC→DV8.4 MP4：Main10 / HLG、DOVI 兼容 ID 4、RPU 与源帧数一致；24bit/48kHz PCM→ALAC 解码采样一致、音画偏移为零。PowerShell 5.1/7 解析、BOM 与共享读测试通过。
该短片源/目标 146.447 Mbps、实际 32.563 Mbps，偏差 -77.76% 已报告；本轮成功证明 I/O 修复和完整转换通过，不代表码率预算用满。202.15 秒原片未在修复后整片重跑，也未执行新的 HDR 设备或 B站验收。
本轮证据位于本地 `validation/stream_error_20261102/`，最终任务 `.tools/tui-runs/4232a1eca1f846d6836fde43f7a4cec1/`。

## 2026-11-03 realDolby 配对参考验证

依用户后续要求，realDolby 不再限制1000 nit输出峰值；其他预设保持原有行为。1000 nit仅为本模式SDK校准基准和BT.2100 HLG测量参考。
三份4K/60样片有相同223.05秒/13383帧时间线；抽样确认对应画面，单独提取两份DV文件的全部RPU进行比较。苹果样片含额外动态/Trim信息，未复制或伪造这些块。
36个时间点另实际执行4K TrueHDR推理，用线性BT.2020配对像素校准固定单调曲线。源码中保存曲线版本/SHA256；[使用与边界](realDolby.md)。

- CPU/静态套件：81项中76通过、5项条件跳过；另在实际GPU环境运行3项CUDA数值测试及1项NVENC无损回读测试，均通过。未将本轮未执行的合作取消测试标为通过。
- 实际完整转换：原片四段2秒素材组成8秒480帧4K/60无损验证片；每帧解码哈希与24bit音频均与对应原片段一致。realDolby经TrueHDR→浮点HLG→NVENC→DV8.4输出MP4与MKV，并生成同次推理的HDR10/PQ。
- 输出：Main10/BT.2020/HLG、DV兼容ID4、RPU480帧、时长8.000秒；源码率目标取原片31.338Mbps，验证片因无损制作故显式使用Custom，实际MP4视频34.085Mbps（+8.77%），不冒充自动Source匹配的验证。
- MP4的48kHz/24bit双声道PCM→ALAC，MKV的PCM原复制：384000采样/声道，解码SHA256均相同，音画偏移0。HDR10对照也通过现有音频校验。
- 最终基础层逐帧全分辨率测得CIE Y峰值1889.2nit、MaxRGB4395.5nit，保留参考超白；两者不是同一个统计口径。
- 新成片8个配对抽样帧（完整解码后每轴取1/4像素）的PQ亮度绝对误差由0.031236降至0.012877（减少58.8%）。这是本参考的亮度接近度，不是画质分数或跨素材结论。
- 同次HDR10/PQ与HLG四个抽样时刻的平均CIE Y偏差<0.3%；显示器渲染仍需另验收。
- 实际执行已校验Neutral缓存→realDolby浮点适配器→DV8.4路径，30帧输出通过，外部缓存保留，无重复AI推理。PowerShell5.1/7用法与解析通过，TUI的选项/灰字/参数/命名有测试覆盖。

2026-11-03，用户对上述8秒DV样片反馈“整体接近参考”，记为本素材主观观感通过。此次回复未提供设备型号、系统/播放器版本或杜比视界标识确认；不将其扩大为DV播放链、其他素材或平台验收。本轮仍未执行整片223.05秒的新版转换、B站投稿或平台音频回读。证据保存在本地 `validation/realdolby_20261103/`，用户对照样片在原素材目录的 `realDolby对照/`。

## 标准与实现依据

- [ITU-R BT.2100](https://www.itu.int/rec/R-REC-BT.2100)：PQ、HLG及参考显示模型。
- [CTA亮度统计参考实现](https://psychtoolbox.org/docs/ComputeHDRStaticMetadataType1ContentLightLevels)：在线性显示RGB计算MaxCLL/FALL，与CIEY分开。
- [ETSI TS103572](https://www.etsi.org/deliver/etsi_ts/103500_103599/103572/01.03.01_60/ts_103572v010301p.pdf)：MaxRGB/PQ元数据及偏移的规范语义；不把简单PQ差值冒充Dolby L3兼容算法。
- [Dolby元数据层级](https://professionalsupport.dolby.com/s/article/Dolby-Vision-Metadata-Levels)、[Dolby制作实践](https://professionalsupport.dolby.com/s/article/Dolby-Vision-Content-Creation-Best-Practices-Guide?language=en_US)：L1/L3兼容边界及创作Trim的区别。
- [dovi_tool2.3.4生成器](https://github.com/quietvoid/dovi_tool/blob/2.3.4/dolby_vision/src/rpu/generate.rs)：实际编码约束；原始测量和编码值分别记录。
- [FFmpeg7.1 MOV/MP4封装实现](https://github.com/FFmpeg/FFmpeg/blob/n7.1/libavformat/movenc.c)：pcmC格式/位深和字节序，ALAC配置与解码交叉验证。
- [NVENC逐帧多遍](https://docs.nvidia.com/video-technologies/video-codec-sdk/13.0/nvenc-video-encoder-api-prog-guide/index.html#multi-pass-frame-encoding)：不称为整片两遍编码。
- [B站早期DV投稿说明](https://www.bilibili.com/opus/560508706903943321)：30Mbps为公开历史建议，不能据此推定为当前硬限制；Hi-Res候选预检不是平台验收承诺。
