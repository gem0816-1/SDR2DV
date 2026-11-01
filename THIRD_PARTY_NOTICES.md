# 第三方组件

源码仓库通过外部程序调用 FFmpeg、dovi_tool、Dolby dlb_mp4base、NVIDIA VFX Python SDK、CuPy 和 NumPy；可选部署还使用 uv 及其 Python 发行包。
各组件受各自许可证约束。FFmpeg、dovi_tool、uv、Python 和 NVIDIA 依赖由部署脚本从对应项目来源下载，不随 SDR2DV Windows ZIP 打包。

Windows Release 中的 `.tools/mp4muxer/mp4muxer.exe` 由下列上游源码构建：

- 项目：[DolbyLaboratories/dlb_mp4base](https://github.com/DolbyLaboratories/dlb_mp4base)
- 提交：`dbc2a9721ae2724591a4b594d19a60423e511cd0`
- 许可证：BSD-3-Clause，完整文本随二进制置于 `LICENSE.dlb_mp4base.txt`。
- 构建信息：同目录 `BUILDINFO.txt`；对应源码另附于 Release。

该二进制为 SDR2DV 社区构建，不是 Dolby 官方二进制发行。项目不以 Dolby 名义背书。
