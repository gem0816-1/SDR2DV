# realDolby 配对参考曲线

`realDolby` 是针对配对 SDR / 苹果 HLG 视频测量得到的固定亮度预设，不是苹果或 Dolby 的官方算法。
目标是接近参考片的明暗关系；它不能保证还原 SDR 已丢失的纹理、颜色或相机原始动态范围。

## 使用

双击或拖视频到 `SDR2DV\TrueHDR-DV.bat`，在「HDR 亮度映射」中选 `realDolby`，核对摘要后开始。
它是独立选项；默认仍为 Neutral。输出名含 `_realDolby`，避免与旧成片混淆。

```powershell
.\SDR2DV\SDR2DV.ps1 -InputPath "D:\SDR.mov" -HdrMode TrueHDR -Bilibili -BrightnessMapping realDolby
# 同次推理额外保存 HDR10 / PQ 对照
.\SDR2DV\SDR2DV.ps1 -InputPath "D:\SDR.mov" -HdrMode TrueHDR -Bilibili -BrightnessMapping realDolby -KeepHDR10
```

默认完整应用曲线、不修改饱和度、不自动去黄；`-BrightnessStrength 0..1` 可控制强度，0 回到 Neutral 路径。
源码率匹配、源尺寸/帧率、PCM → 经样本校验的 ALAC 无损封装及 MKV 原音复制策略保持原有行为。

## 校准依据

本次配对输入是 `SDR.mov`、`真杜比.mp4`，旧输出是 `SDR_TrueHDR_DV84.mp4`。
三者都是 3840×2160 / 60 fps / 223.05 秒 / 13,383 帧；不是拿不同内容匹配直方图。
36 个分布于 2..220 秒的时间点确认对应画面；非全黑帧的对数亮度相关系数中位数约 0.9935。
另外实际调用 TrueHDR 处理这些 4K SDR 帧，在量化/旧色体积缩限之前提取配对像素，作为曲线校准输入。

亮度采用 [BT.2100](https://www.itu.int/rec/R-REC-BT.2100/en) HLG EOTF、BT.2020 CIE Y，统一使用1000 cd/m²、gamma1.2、理想黑的**测量参考**。
这不是对具体手机屏幕的测光，也不是输出峰值限制；[EBU 参考模型](https://qc.ebu.io/apps/r167/)说明了 HLG 扩展信号和名义显示峰值的区别。
苹果样片有 HLG 超白：11 个全分辨率抽样帧测得最高 CIE Y 约2060 nit、MaxRGB 约4794 nit。
这些分别是亮度和单通道峰值，不能混用；参考片 RPU 的1000 nit描述也不能替代基础层像素测量。

旧输出在多数中低调比参考亮，高光又受旧管线名义色体积限制。
新曲线用固定单调 PCHIP 在 PQ 感知坐标中插值：保持零黑位，改善近黑过渡，收回偏亮的中低调，并保留参考高光。
代表锚点为 TrueHDR 显示线性输入20→10.5、120→106、400→400、780→930、1000→1750 nit。
所有镜头用同一曲线，不做逐帧曝光/直方图归一，不因场景改变亮度分配。
高于SDK校准数据范围的部分使用平滑、单调外推；它不是新测得的高光细节。

## 超白与元数据

`realDolby` 的 HLG 中间管道采用 `gbrpf32le`，不经过只能表示0..1的GBR16归一整数传输。
HLG 逆OOTF/OETF保留扩展信号，随后才转为Main10 YUV；最终编码/RPU均分析实际回读像素。
HDR10/PQ 对照也保留超过1000 nit的实际像素，遵循PQ自身的10000 nit信号域。
参考片与本工具的DV8.4基础映射相同，但参考片含逐帧动态分析、L3/L4/L8等额外信息。
本工具仍使用社区镜头分析，不复制参考片的创作性Trim，不将其宣称为苹果/Dolby认证调色。

新生成的缓存可按原有SHA256和曲线版本核验复用。
已有Neutral HLG缓存通过浮点适配器调色，不重跑AI；此前已被缩限/量化的信息无法撤销，结果不等价于新管线。
HLG 的名义0..1三维cube会截掉超白，因此本模式拒绝这种导出，缓存路径也不用它；PQ cube仍可用于旧PQ缓存。

## 复现与验收

可使用项目Python环境对同长度、同帧率、同画面的HLG文件做亮度对照：

```powershell
.\SDR2DV\.venv-tui\Scripts\python.exe .\SDR2DV\compare_hdr_reference.py --reference "D:\真杜比.mp4" --baseline "D:\旧输出.mp4" --candidate "D:\SDR_TrueHDR_realDolby_DV84.mp4" --frames 24 --output "D:\新报告.json"
```

默认明确标记为640像素宽抽样，`--width 0`使用完整分辨率；不将RPU/MaxCLL当作像素值。
报告中的PQ亮度绝对误差只衡量与本参考的亮度接近程度，不是画质分数，不证明算法对其他素材更好。
CPU数值、GPU推理、完整转换、音频采样、HDR设备观感和B站最终播放属于不同验收层级。
本次本地验证结果见[验证说明](validation.md)。2026-11-03，用户对8秒DV样片反馈“整体接近参考”，记录为本素材主观观感通过；设备型号、系统/播放器版本和DV标识未在此次回复中进一步确认。其他素材、完整视频和B站最终播放仍需分别验证。
