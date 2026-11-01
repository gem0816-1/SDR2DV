# 部署与发布

## 一键部署

解压 Windows Release ZIP 后双击根目录 `deploy.bat`。不需要管理员权限，安装范围为当前项目目录。
默认 `Auto` 模式检测 NVIDIA 显卡：存在时配置 TrueHDR，否则只配置普通转换；显卡存在不代表 SDK 一定兼容，真实推理失败会报错，不静默替换算法。

从 PowerShell 使用：

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\Deploy.ps1                          # 自动选择模式
.\Deploy.ps1 -Mode Standard           # 只准备普通转换
.\Deploy.ps1 -Mode TrueHDR            # 同时准备并检查 TrueHDR
.\Deploy.ps1 -CheckOnly               # 只检查，不下载或重写配置
.\Deploy.ps1 -Mode Standard -Portable # 强制使用项目内工具
```

工具和 CUDA 依赖不会添加到系统 PATH；部署不会处理视频、启动播放器或创建计划任务。
转换脚本从 `.tools/tools.json` 读取经过检查的工具路径；显式 `-DoviTool` / `-Mp4Muxer` 参数优先。
默认可复用 PATH 中的工具及已有本地工具；`-Portable` 限制为项目内工具，显式提供的路径除外。
TrueHDR 已有依赖版本正确时跳过安装，执行 `pip check`、参考亮度/肤色模块检查和真实推理检查。
未找到合适 Python 时，通过固定版本 uv 下载项目内 Python 3.11，不注册 Windows Python、不创建全局命令。
部署不安装显卡驱动；驱动、GPU、操作系统和显示器需满足 [NVIDIA SDK 条件](https://docs.nvidia.com/maxine/vfx/latest/WindowsVFXSDK/GetStartedonWindows.html)。

## 本地文件与缓存

| 路径 | 用途 |
| --- | --- |
| `.tools/tools.json` | 本机工具绝对路径，仅本地生成，不随发布包分发 |
| `.tools/downloads` | SHA256 已验证的原始 ZIP 缓存 |
| `.tools/packages` | 按次解压的外部工具，保留其许可证及附带文件 |
| `.tools/mp4muxer` | Windows 发布包自带的封装器、许可证及构建说明 |
| `.tools/python` / `.tools/uv-cache` | 自动安装的 Python 及 uv 缓存 |
| `.tools/pip-cache` | TrueHDR Python 包缓存 |
| `SDR2DV/.venv-truehdr` | TrueHDR 独立虚拟环境 |

可用 `-CacheDirectory "D:\sdr2dv-downloads"` 指定工具 ZIP 缓存目录。
下载 URL、版本和 SHA256 固定在 `scripts/deploy-assets.json`。已有缓存哈希不符时明确报错，不自动执行或覆盖该文件。
下载中断只清理本次随机命名的 `.partial` 文件。工具、安装包和虚拟环境属于部署依赖，不受转换任务的中间文件清理影响。
首次部署需要联网；准备好所有工具和正确版本的虚拟环境后，重复部署可离线复用。
仅复制工具 ZIP 不足以离线首次配置 TrueHDR，还需要完整 Python/SDK 依赖。

## 封装器来源

Dolby 官方 `dlb_mp4base` 仓库没有 Windows 二进制 Release，因此本项目提供源码构建的 x64 社区版本，不复制用户机器上的 Dolby 工具。
上游固定提交为 `dbc2a9721ae2724591a4b594d19a60423e511cd0`，源 ZIP 的 SHA256 为 `BF3E2A91A94135F192B33C13B9160CB28DEF4CAA12A8C9008319DA0ED2BFD6F4`。
采用 Visual Studio 2022 / v143 / Windows SDK 10.0，release x64，静态 CRT；只调整构建设置，C 源码不变。
构建结果仅依赖 Windows 的 `KERNEL32.dll`，不额外要求 VC++ Redistributable。
发布包附带 BSD-3-Clause 许可证、构建信息和独立源代码附件，见 [第三方说明](../THIRD_PARTY_NOTICES.md)。

维护者可重建：

```powershell
.\scripts\Build-Mp4Muxer.ps1 -OutputDirectory .\.build\mp4muxer
```

需要 Visual Studio 2022 C++ Build Tools、v143 和 Windows SDK。上游已于 2026-04-17 停止维护；本项目的社区构建不代表 Dolby 官方发布或持续维护承诺。

## 发布校验

Windows ZIP 只包含 Git 跟踪的源代码/文档，以及上述封装器的三个文件；不包含本地配置、虚拟环境、用户视频或诊断日志。
Release 同时提供主 ZIP、封装器 ZIP、对应的上游源 ZIP 和 `SHA256SUMS.txt`。
核对下载包：

```powershell
Get-FileHash .\SDR2DV-v1.9.0-win-x64.zip -Algorithm SHA256
```

源码回归：`powershell -NoProfile -ExecutionPolicy Bypass -File .\tests\Deploy.Tests.ps1`。
发布前还需在独立目录运行便携部署、实际短片转换、TrueHDR 推理、重复部署和参数/编码检查。
