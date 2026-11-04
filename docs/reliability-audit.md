# 可靠性审查（2026-11-04）

审查开始时，工作分支 HEAD 与远端 main 均为 `80f30fa`（v2.1.0），两个本地工作树干净。本轮保留已验收的亮度数学或音频策略；修复与回归完成后，经用户确认提交并推送仓库，未新建 Release。

## 已确认的问题（修改前位置）

| 级别 | 代码位置 | 触发与根因 |
| --- | --- | --- |
| P1 | `SDR2DV.ps1:1257,1387,1401` | HDR10 提前发布，MP4/MKV 分别 Move-Item；后续失败时覆盖旧片、留下部分新片。跨卷 Move 还可能变为直接向正式文件复制。 |
| P1 | `SDR2DV.ps1:1014-1025` | 检查存在与发布之间没有跨进程锁，Force 任务可互相覆盖。 |
| P1 | `SDR2DV.ps1:986,1627-1628` | 同名不同扩展名的输入共用输出名；跳过函数直接 return，外层仍计成功。 |
| P1 | `SDR2DV.ps1:926-931` | 所有字幕直接 copy；mov_text 不受 MKV 支持，附件未映射，可能长时间编码后失败或丢字体。 |
| P1 | `SDR2DV.ps1:408-409` | 仅检查临时卷，不预检或监测输出卷；Quiet 进程无磁盘监测。 |
| P1 | `hdr_analysis.py:177-226` | 所有逐帧字典常驻内存并写入完整 JSON，再由 PowerShell 反序列化，内存随片长增长。 |
| P1 | `core.ps1:25-31;SDR2DV.ps1:1658-1661` | EventPath 指向源文件会追加日志，ReportPath 指向源文件会直接覆盖；可选诊断路径未做冲突检查。 |
| P1 | `core.ps1:119;audio_audit.py:97-98` | 显式 AC3/EAC3 的 allow-lossy 被传给 MKV，且可放宽复制/ALAC 的采样校验；实测改动采样仍可通过此旧放宽分支。 |
| P2 | `truehdr_backend.py:116-139` | stdout 顺序读完才读取 stderr；大量探测错误可以塞满 stderr 管道。 |
| P2 | `SDR2DV.ps1:393-394,506` | ReadToEndAsync 无界保留外部程序完整输出。 |
| P2 | `SDR2DV.ps1:1612` | 过滤式遗漏 realDolby HDR10 成品。 |
| P2 | `SDR2DV.ps1:655-663` | 未知色彩标记静默按 BT.709/limited 解释。 |
| P2 | `SDR2DV.ps1:651-656` | 已知 gamma22/gamma28 标签原样传给 zscale；FFmpeg 7.1 实测拒绝 gamma22，正确枚举为 bt470m/bt470bg。 |
| P2 | `core.ps1:25-31` | 可选 TUI 事件文件异常会传播为转换失败。 |
| P2 | `truehdr_stream.py:114;encoding.ps1:14` | 多任务无设备互斥，全部争用 GPU 0。 |
| P2 | `truehdr_backend.py:30-55` | 媒体 SHA256 已校验，但调色/SDK 元数据未与媒体内部身份绑定；校验与使用之间还存在写入竞争窗口。 |
| P2 | `scripts/deploy-common.ps1:118-124` | ZIP 有目录边界检查，但缺少 Windows ADS/设备名/别名及解压容量预检。 |
| P2 | `rpu_audit.py:7-20` | 逐帧 L1 导出仍整片 json.load，抵消长片统计的内存优化。 |
| P2 | `tui_runner.py:38,67;SDR2DV.ps1:477-487` | 合作取消已有进程树清理，但强杀 TUI/核心时不能依靠 finally；父进程退出可遗留子进程。 |
| P2 | `tui_runner.py:36,65` | 从 PowerShell 7 继承 PSModulePath 再经 Python 启动 5.1，实测会导入错误版本 Utility 模块、缺少 Get-FileHash。 |
| P2 | `SDR2DV.ps1:247-248` | 未显式指定 CancelFile 时，Python 帮手可继承其他任务残留的 SDR2DV_CANCEL_FILE；改为只传播本次参数，退出后恢复原环境。 |

## 基线测试

项目 TUI Python：81 项，76 通过、5 条件跳过。SDK Python 不含 Rich/Questionary：71 项，2 导入错误、5 跳过，不算通过；按环境分别运行后续验证。

## 复现证据与实现

`validation/reliability_20261104/baseline-reproduction.json` 是从 80f30fa 提取函数后的隔离故障注入，不是假 GPU 转换：

- MKV 封装失败：旧 MP4 的 SHA256 已改变（`old_mp4_unchanged=false`）。
- HDR10 完成后注入 RPU 失败：正式 HDR10 已出现（`hdr10_published=true`）。
- 新版相同注入均保留旧文件，包含 Force 下已有 HDR10 的场景。真实媒体转换另行测试。

| 修改文件 | 实现与理由 |
| --- | --- |
| `reliability.ps1`、`SDR2DV.ps1` | 输出路径排他文件句柄；全部产物验证后复制至输出卷的 GUID 暂存目录并比 SHA256，再用同卷 File.Move / File.Replace 交付。替换保留旧备份，日志先写后改文件，处理失败时逆序回滚。提交前检查旧文件摘要，防止把外部变化当作原文件。 |
| `Repair-Delivery.ps1` | 未完成交付阻止重跑覆盖；按旧/新摘要确认身份后恢复，不覆盖未知外部修改。回滚被播放器锁住时保留备份，解锁后可再次恢复。 |
| `reliability.ps1`、`core.ps1` | 诊断文件路径先检查，冲突输入/切点/缓存或彼此重叠时不写文件；不覆写不属于本工具的既有报告。可选日志/报告失败只警告；结果报告同目录暂存替换。 |
| `reliability.ps1`、`tui_model.py` | 同名不同扩展名加输入扩展后缀，冲突时加摘要；批量预检最终名字。过滤包括 realDolby HDR10，跳过单独统计，TUI 摘要使用后端给出的名字。 |
| `reliability.ps1`、`SDR2DV.ps1` | MKV 字幕早期预检，mov_text/text 明确转 SubRip；ASS/SSA 等复制，映射附件，核对语言/标题/轨道标志和附件内容 SHA256。 |
| `process_io.cs`、`SDR2DV.ps1` | 双流同时排空；诊断 RAM 尾部通常各64 KiB，结构化输出上限4 Mi字符，磁盘日志各限制16 Mi字符（UTF-8 字节数取决于文本）。进度只读尾部64 KiB。非零退出、日志写入错误、超额元数据均报错；不把截断结构化数据当成功。 |
| `process_io.cs`、`windows_job.py`、`tui_runner.py` | Windows kill-on-close Job Object 绑定本次树，核心和 TUI 两层均保护；普通取消优先合作，超时只终止本次树。TUI 容忍事件权限/JSON错误、限制读取和显示量，隔离 5.1 的模块环境。 |
| `hdr_analysis.py`、`rpu_audit.py` | 保留原每帧统计、检测器、求和顺序、四舍五入和 CMv4 限制，改为一帧待决统计与逐镜头累计；逐帧诊断可选 JSONL；RPU L1 数组增量解析。默认报告内存随镜头数而非帧数增长。 |
| `cache_binding.py`、`truehdr_stream.py`、`truehdr_backend.py` | 新 HLG 和 PQ 缓存内部写身份及配置 SHA256，绑定源摘要、SDK参数/版本、曲线、尺寸、帧率与完整帧数；核对媒体摘要，并在核心复用期间持有只读句柄。旧缓存保留兼容并警告未绑定的解释元数据。 |
| `task_io.py`、`media_probe.py`、`truehdr_backend.py` | 探测 stdout 流读、stderr 独立文件，错误尾部有界；有限元数据上限、非法 FPS/尺寸/空帧、坏 PTS、解码错误明确失败，不自动改时间线。 |
| `core.ps1`、`audio_audit.py` | 显式有损许可只用于 MP4；即便带 allow-lossy，复制和 ALAC 音轨仍须样本/采样数完全一致。仅实际改为有损编码的音轨允许原有时长容差；没有改变编码选择或音频处理策略。 |
| `SDR2DV.ps1` | 拒绝未知 SDR 解释、封面作为主视频、隔行/旋转和不能保真的负起始 PCM；gamma22/gamma28 正确转为 zscale 枚举。过长/非法/ADS 输出名在编码前拒绝；临时名限长并用完整 GUID。 |
| `scripts/deploy-common.ps1`、`Deploy.ps1`、Setup/依赖文件 | 既有 ZIP SHA256 流程保留；解压前完整检查路径、ADS、设备名（含上标数字）、链接、别名、重复/冲突、数量、总尺寸和空间；部署互斥、配置暂存替换，传递依赖固定版本。 |

临时卷与输出卷都有容量预检；媒体进程、Quiet 进程和暂存复制持续检查可用空间，输出卷复制前还以完整文件大小检查。滚动/码率预算是估算，不是对磁盘的预留；其他程序仍可能抢占空间，最终安全依赖暂存与失败回滚。

## 测试与执行范围

环境：Windows x64；PowerShell 5.1.26100.9711 / 7.6.5；Python 3.11.9；FFmpeg 7.1-full、dovi_tool 2.3.4；RTX 4060 Laptop、驱动596.49；SDK1.3.0 / nvidia-vfx0.2.0.0 / CuPy14.2.0。
命令在项目根目录执行；SDK Python 使用本机已部署的独立环境，不是全新 NVIDIA 安装。

```powershell
# 默认套件：CPU、真实 Windows 文件/进程故障注入、音频与 RPU 小样本
.\SDR2DV\.venv-tui\Scripts\python.exe -X utf8 -B -m unittest discover -s SDR2DV -p 'test_*.py' -v
# 另在 5.1 重跑可靠性回归
$env:SDR2DV_TEST_SHELL="$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
.\SDR2DV\.venv-tui\Scripts\python.exe -X utf8 -B -m unittest discover -s SDR2DV -p test_reliability.py -v
# 实际媒体集成（两种 shell 分别执行；结束后清除这些仅供测试的环境变量）
$env:SDR2DV_TEST_RELIABILITY_MEDIA='1'; $env:SDR2DV_TEST_NVENC='1'
.\SDR2DV\.venv-tui\Scripts\python.exe -X utf8 -B -m unittest discover -s SDR2DV -p test_reliability_media.py -v
# 部署回归，两种 PowerShell 均执行
powershell -NoProfile -ExecutionPolicy Bypass -File .\tests\Deploy.Tests.ps1
pwsh -NoProfile -File .\tests\Deploy.Tests.ps1
.\Deploy.ps1 -Mode Standard -CheckOnly
```

最终数量记录在本机 `validation/reliability_20261104/acceptance.json`；本页只将实际执行的条件测试计为通过，不把默认跳过视为已验证。

| 实际运行 | 通过 | 失败 | 跳过 |
| --- | ---: | ---: | ---: |
| 默认套件（114项，含 Windows/FFmpeg/RPU 检查） | 104 | 0 | 10 |
| PowerShell 5.1 可靠性回归 | 27 | 0 | 0 |
| 真实媒体集成，PowerShell 7 | 5 | 0 | 0 |
| 真实媒体集成，PowerShell 5.1 | 5 | 0 | 0 |
| 缓存绑定增强后的 GPU 缓存/桥接追加重跑 | 2 | 0 | 0 |
| CUDA参考 / NVENC Main10 无损回读 | 4 | 0 | 0 |
| 真实核心合作取消 | 1 | 0 | 0 |
| 部署缓存/哈希/ZIP回归（每种shell） | 5 | 0 | 0 |

这些行存在重复覆盖，不累加为不同测试数量。默认跳过的 CUDA/NVENC/取消/媒体用显式条件分别补测。

- 双真实进程争抢输出/Force、GPU互斥、第二目标被真实文件锁占用、真实 ACL 拒绝、只读文件、提交中取消、强杀后恢复、回滚本身受阻再修复、子孙进程回收、无关进程/缓存哨兵保留，均有自动回归。
- 源文件被误用为日志/报告，在真实 CLI 与函数测试中均拒绝；不相关既有 JSON 未改变。故障注入的旧 MP4/MKV/HDR10 均按 SHA256 核对。
- 100000帧 CPU 统计与100000条 L1 JSON 压力测试：分别用 tracemalloc 检查峰值低于512 KiB / 2 MiB（统计对象/流式解析增量，不是整个媒体解码的 RSS 或显存）。CPU 参考与旧算法逐项相等：镜头、测量/编码L1、MaxCLL、MaxFALL、CIE Y、平均值和超参考像素比例。
- 真实 FFmpeg/dovi_tool：320×180/25fps 合成片，PQ/8.1 x265 Source 两遍、HLG/8.4 x265、TrueHDR→NVENC、MP4/MKV、同次 HDR10、已绑定缓存复用，以及实际 TUI bridge 的嵌套 Job；测试目录含中文、空格、`&`、方括号。mov_text→SRT 双字幕、ASS 双轨及 Arial 字体附件；语言、默认/forced 标记按源实际值核对。48kHz/24bit PCM→ALAC 和 MKV PCM 复制的解码采样、帧数/RPU、时长、SAR 检查均实际执行。
- 未标记色彩、非零 PTS、VFR、零字节/损坏输入使用真实媒体/FFprobe 执行，返回失败且旧输出摘要不变。分辨率、隔行、封面、异常 FPS 与文件名边界另有 CPU/预检回归。磁盘耗尽是故障注入，没有故意填满用户 C 盘。
- 另用已有原片无损切片实际转换1秒4K/60 realDolby，约46秒得到 DV8.4 MP4/MKV 及 HDR10：Main10/HLG、DOVI兼容ID4、60帧RPU、24bit/48kHz音频逐样本相同、A/V起始偏移0。该验证片因无损制作视频流458.585 Mbps，明确用 Custom31.338 Mbps，**不冒充 Source 模式验收**；Source 自动匹配走上述合成片测试。
- 此4K极短样本的 MP4 实际视频35.927 Mbps（+14.64%），MKV35.963 Mbps（+14.76%）；偏差如实提示，未用填充或二次编码伪装匹配。
- 实际 GPU 的两项 HDR 重建参考、一项 realDolby CUDA 参考与一项 NVENC Main10 无损回读单独执行；真实核心合作取消单独执行，退出130、清理自身工作目录、保留输入和无关缓存。
- Standard CheckOnly、TUI/SDK requirements 的 `pip install --dry-run --no-index` 复用核验实际执行。新部署下载/完整 wheel 离线供应链验收不在本轮；版本 constraints 不是 wheel SHA256 锁。

## 必要行为调整与边界

- 全部新产物通过后才交付。单文件同卷替换与多文件回滚不能混称：多文件提交期间可见新旧组合；断电/系统故障不能保证整套原子性。未完成日志会阻止静默重跑，要求显式恢复。共享微小锁文件保留以避免清锁竞争。
- 新任务保守串行使用 GPU0（含可能用 CUDA 分析的核心任务）。锁只约束采用此实现的 SDR2DV，不能为其他软件预留显存或磁盘。
- 可选事件失败不再终止转换；未知既有报告不被覆盖。普通模式不再猜 BT.709；非法/不能保真的时间线早期失败，不通过删字幕、重采样、改帧或降位深使测试通过。
- 原 CLI 参数、Neutral/Natural/Reference/realDolby、1000nit参考与realDolby超白、PQ/HLG数学、视频码率策略、PCM→ALAC策略不改。`brightness_mapping.py`、`hdr_math.py`、`hdr_cuda.py`、`pq_to_hlg.py`、`encoding.ps1` 和 BAT 启动器保持基线字节；统计字段改为汇总及可选 JSONL 是有意的诊断格式调整。
- PS1 保持 UTF-8 BOM/CRLF；BAT 保持 ASCII/CRLF；不改 `Desktop/1/dubi` 工具、备份、用户原始素材。审查阶段未自动提交或发布，后续提交推送依据用户确认执行。

交付包含37个新增/修改文件。复制前确认桌面工作树干净并逐文件匹配基线，保存覆盖前快照后同步相同代码；桌面 `Start-Tui.ps1` 经5.1实际 dry-run，Standard CheckOnly 复用依赖通过。用户确认后将本轮修复提交至 `main`，同步两处本地检出；GitHub 的 v2.1.0 Release 附件仍为此前版本，本次未重新打包发布。

## 剩余风险 / 未验证

1. 未执行两小时/极多镜头的真实 GPU 全片；统计内存为 O(shots)，极密剪辑仍会增加镜头元数据。100000帧 CPU 压测不等于两小时4K解码或显存压力验收。
2. 本机只有 C 盘，未物理执行跨卷、SMB/NAS断网、FAT/exFAT、不可靠文件系统、断电、真实磁盘写满；按 Windows API 和故障注入验证流程，不能扩大为这些环境通过。
3. 强杀测试在 Job 绑定后执行；进程启动至绑定的极短窗口仍不是崩溃原子操作。强杀/断电可留下普通缓存或已完成事务的备份目录，工具不批量删除未知残留。
4. 同账户恶意程序能改代码/文件/元数据；当前锁与摘要是协调和意外损坏防护，不是对该攻击者的安全边界。新缓存摘要不是签名；旧缓存的解释数据仍不绑定，已警告保留兼容。
5. 仅实测上述字幕/字体及已有音频夹具；复杂 mov_text 样式不能用 SRT 等价表达，已提示。特殊位图字幕、负起始/不连续音频、罕见布局和超长路径仍需各自样本；不支持时应拒绝，不能宣称全部兼容。
6. 未做新的 HDR 设备观感、B站投稿/Hi-Res最终音轨回读、官方 Dolby 创作/认证或全新机器 NVIDIA 部署。

Windows 行为依据：[File.Replace](https://learn.microsoft.com/en-us/dotnet/api/system.io.file.replace)、[Job Objects](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects)、[文件名规则](https://learn.microsoft.com/en-us/windows/win32/fileio/naming-a-file)。实现未据此宣称多文件全局原子性。
