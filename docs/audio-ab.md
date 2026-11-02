# B站 Hi-Res 音频 A/B 投稿

目的：分开检查本地封装、服务器转码与播放端，避免仅凭一条客户端现象认定责任。
当前没有取得该BV最终处理音轨，也没有执行新版本投稿，因此不能宣称电流声已彻底修复。

## 制作对照

选10–30秒有音乐、安静段与瞬态的原素材，保留48kHz/24bit等源规格；不要混入音量调整。
先用本工具生成带ALAC的短片A并保留ReportPath。再使用同一视频基础层与原PCM制作B，只更换封装：

```powershell
# A是已经核验通过的DV短片；B故意使用PCM/ipcm，用于兼容性对照
ffmpeg -copyts -i "A_ALAC_DV84.mp4" -ss 0 -i "source.mov" -map 0:v:0 -map '1:a?' -c copy -tag:v hvc1 -strict unofficial -avoid_negative_ts disabled -movflags +faststart "B_PCM_ipcm_DV84.mp4"
# 使用原素材和每个成品分别做采样/时间/封装验证
.\SDR2DV\.venv-tui\Scripts\python.exe -X utf8 .\SDR2DV\audio_audit.py "source.mov" "A_ALAC_DV84.mp4" --output "A_audio.json"
.\SDR2DV\.venv-tui\Scripts\python.exe -X utf8 .\SDR2DV\audio_audit.py "source.mov" "B_PCM_ipcm_DV84.mp4" --output "B_audio.json"
```

两个视频都核对DOVI配置；PCM B用于测试，不是建议的日常默认。16bit源保持16bit，不能据ALAC容器宣称真24bit Hi-Res。
若源含非PCM或不受支持布局，不要套用该PCM对照；按报告为相同音轨单独测试。

## 投稿与取证

1. A/B分别投稿为清楚标记的测试分P，使用相同视频基础层、账号、投稿设置，等待平台处理完成；记录BV/分P、时间、文件SHA256和平台给出的规格。
2. 每个分P在同一设备/播放器分别开关Hi-Res；再换设备/有线与无线输出。记录客户端/系统版本、码率档、异常时间点及是否只出现在Hi-Res。
3. 经账号正常允许的下载/获取方式保存平台普通音轨和Hi-Res音轨，保留原文件及响应的格式/时间线信息；不要把客户端录音当作服务器原始音轨。
4. 本地播放A/B并运行audio_audit；本地失败先查封装/描述/样本/时间戳。平台允许取得的无损音轨与源按声道、采样率、有效位深解码比较；有损档不能要求哈希一致，需另做对齐频谱/波形与失真检查。
5. 若上传文件本地完全一致，服务器返回音轨已含噪声，才有服务器处理异常证据；服务器音轨正常但特定播放端异常，才进一步查播放器/设备链路。
6. 若无法取得平台处理后音轨，只能报告“在所列设备听测结果”，不得声称服务器无损或所有播放链路已解决。

验收记录至少包含A/B本地哈希校验、ALAC/pcmC描述、源与输出偏移、平台最终音轨证据或缺失声明，以及每台设备Hi-Res开/关结果。
