"""Plain-language menus and configuration edits; PowerShell owns conversion."""
from pathlib import Path
from rich.text import Text

from tui_controls import BACK, Option, choose, number, text, toggle
from tui_model import defaults, encoder_options, normalize_path, nvenc_available


def active_encoder(config, caps):
    if config.encoder != 'Auto': return config.encoder
    return 'NVENC' if nvenc_available(caps, config.hdr_mode, config.lossless) else 'x265'


def mapping_label(config):
    if config.hdr_mode != 'TrueHDR': return '标准 SDR 上变换'
    if config.mapping == 'Neutral' or config.brightness_strength == 0:
        return 'Neutral（旁路额外调色）'
    return f'{config.mapping} · 曲线 {config.brightness_strength:g} · 肤色 {config.skin_strength:g}'


def main_options(config):
    rates = {'Source': '源码率匹配', 'Platform': '平台限码率', 'Custom': '自定义', 'Quality': '质量优先'}
    audio = {'auto': '兼容性优先', 'copy': '原样复制', 'alac': '无损 ALAC'}
    return [
        Option('start', '查看摘要并开始', '核对码率、音频、输出路径；再次确认后才会转换。'),
        Option('encoder', f'编码器 · {config.encoder}', '决定由显卡还是 CPU 压缩视频，影响速度和压缩效率。'),
        Option('mapping', f'HDR 亮度映射 · {config.mapping}', '决定暗部、中间调和高光的分配；参考峰值固定 1000 nit。',
               '仅 TrueHDR 模式使用' if config.hdr_mode != 'TrueHDR' else None),
        Option('container', f'输出容器 · {config.container.upper()}', 'MP4 用于投稿/播放；MKV 保留原音和字幕；可同时输出。'),
        Option('rate', f'码率策略 · {rates.get(config.bitrate_mode, config.bitrate_mode)}', '默认匹配源视频流；平台限码率需主动选择。'),
        Option('audio', '音频策略 · ' + ('MKV 原样复制' if config.container == 'mkv' else audio.get(config.audio_codec, config.audio_codec)),
               '优先复制；需要兼容 MP4 的 PCM 可无损转 ALAC。',
               'MKV 固定原音复制；HDR10 对照用兼容性优先' if config.container == 'mkv' else None),
        Option('advanced', '高级质量与输出设置', '分别调整编码预设、曲线强度、对照文件、保存位置等。'),
        Option('mode', f'处理模式 · {config.hdr_mode}', 'TrueHDR 使用 NVIDIA AI；标准上变换无需 AI，效果不同。'),
        Option('exit', '退出', '结束本次配置，不启动转换。'),
    ]


def advanced_options(config, caps):
    encoder = active_encoder(config, caps)
    grading = config.hdr_mode == 'TrueHDR' and config.mapping != 'Neutral'
    quality = config.bitrate_mode == 'Quality' and not config.lossless
    return [
        Option('preset', f'编码速度/效率 · {config.nvenc_preset if encoder == "NVENC" else config.preset}',
               f'当前 {encoder}；预设权衡编码速度与压缩效率，不改变分辨率或峰值。'),
        Option('quality', f'画质数值 · {"CQ " + str(config.nvenc_cq) if encoder == "NVENC" else "CRF " + str(config.crf)}',
               '正数越小通常文件越大；CQ 的 0 为自动。CQ 和 CRF 不可直接比较。',
               None if quality else '先选择“质量优先”并关闭无损；码率模式不使用此值'),
        Option('strength', f'亮度曲线强度 · {config.brightness_strength:g}',
               '0 旁路额外调色；1 完整应用选定曲线；不会更改 1000 nit 目标。',
               None if grading else '仅 TrueHDR 的 Natural / Reference 使用'),
        Option('skin', f'暖色肤色去黄 · {config.skin_strength:g}',
               '0 保留原色；越大去黄越强，也可能影响相近颜色的灯光。',
               None if grading and config.brightness_strength > 0 else '需启用 Natural / Reference 且曲线强度大于 0'),
        Option('lossless', '视频无损编码 · ' + ('开启' if config.lossless else '关闭'),
               '只保证最终编码不损失输入像素；SDR→HDR 调色全流程仍不是无损。'),
        Option('hdr10', 'HDR10/PQ 对照 · ' + ('开启' if config.hdr10 else '关闭'),
               '同次 AI 生成一份 PQ 对照，帮助排查 HLG/DV 播放差异，较占空间。',
               None if config.hdr_mode == 'TrueHDR' else '仅 TrueHDR 支持'),
        Option('output', '输出目录', '留空放在原视频旁；指定新文件夹可避免和旧成片混淆。'),
        Option('temp', '保留中间文件 · ' + ('开启' if config.keep_temp else '关闭'),
               '默认结束后清理本次媒体缓存；开启仅适合排错，会占用较多空间。'),
        Option('force', '覆盖同名成片 · ' + ('开启' if config.force else '关闭'),
               '关闭时跳过已有文件；开启会替换同名成片，请在摘要中核对路径。'),
    ]


def preset_options(encoder):
    if encoder == 'NVENC':
        details = [
            '最快；压缩分析较少，适合快速试片。', '偏重速度，适合粗看效果。',
            '较快，适合更在意耗时的任务。', '速度与压缩效率的中间档。',
            '偏重压缩效率，适合速度和细节兼顾。', '当前默认；较充分的压缩分析，适合日常成片。',
            '分析最充分、耗时更长；相对 p6 的改善取决于素材。',
        ]
        return [Option(f'p{i}', f'p{i}', detail) for i, detail in enumerate(details, 1)]
    names = ['ultrafast', 'superfast', 'veryfast', 'faster', 'fast', 'medium', 'slow', 'slower', 'veryslow']
    details = ['最快，压缩效率较低，适合试片。', '很快，优先缩短等待。', '较快，适合预览。',
               '偏快，减少压缩分析耗时。', '比 medium 快，可能需要更多码率保留细节。',
               '当前默认；CPU 编码速度与压缩效率折中。', '花更多时间分析，适合在意压缩效率的成片。',
               '分析更充分，等待明显增加，收益取决于素材。', '很慢；改善可能有限，建议先用短片比较。']
    return [Option(name, name, detail) for name, detail in zip(names, details)]


def output_directory_valid(value):
    if not value.strip(): return True
    try:
        path = Path(normalize_path(value))
        while not path.exists() and path.parent != path: path = path.parent
        if path.is_file(): return '输出位置或其上级是一个文件，请改用文件夹路径'
    except (OSError, ValueError): return '请输入有效的文件夹路径'
    return True


def input_file_valid(value):
    try:
        if value.strip() and Path(normalize_path(value)).is_file(): return True
    except (OSError, ValueError): pass
    return '请填写一个存在的视频文件路径'


def configure_advanced(config, caps):
    while True:
        action = choose('高级设置', advanced_options(config, caps))
        if action == BACK: return
        encoder = active_encoder(config, caps)
        if action == 'preset':
            field = 'nvenc_preset' if encoder == 'NVENC' else 'preset'
            value = choose(f'{encoder} 编码预设', preset_options(encoder), getattr(config, field))
        elif action == 'quality':
            field = 'nvenc_cq' if encoder == 'NVENC' else 'crf'
            value = number('画质数值（0～51）', getattr(config, field), 0, 51,
                ('CQ 为 0 时自动；1～51 越小压缩越轻。' if encoder == 'NVENC' else 'CRF 越小压缩越轻，文件通常越大。') +
                '只在质量优先模式生效；填 0 不等于开启无损。', integer=True)
        elif action == 'strength':
            field = 'brightness_strength'
            value = number('曲线强度（0～1）', config.brightness_strength, 0, 1,
                '0 关闭曲线和肤色修正；0.5 混合原始与映射效果；1 完整应用。参考峰值仍为 1000 nit。')
        elif action == 'skin':
            field = 'skin_strength'
            value = number('暖色去黄强度（0～1）', config.skin_strength, 0, 1,
                '建议先保留当前值；此修正按颜色选区，不识别人脸。过强可能让肤色或灯光失真。')
        elif action == 'lossless':
            field = 'lossless'
            value = toggle('视频无损编码', config.lossless,
                '切换为 Quality，取消码率目标；文件和缓存可能很大，部分播放端/投稿流程不接受。',
                '使用常规 Main10 压缩；若从无损关闭，将保留 Quality，可返回码率菜单选择源码率匹配。')
            if value is True:
                if config.encoder == 'NVENC' and not nvenc_available(caps, config.hdr_mode, True):
                    from questionary import print as print_note
                    print_note('此硬件不支持当前 NVENC 无损模式，请先选择 Auto 或 x265。', style='fg:#8b929d')
                    continue
                config.bitrate_mode = 'Quality'; config.video_bitrate = 0; config.max_video_bitrate = 0
        elif action == 'hdr10':
            field = 'hdr10'
            value = toggle('保存 HDR10/PQ 对照', config.hdr10,
                '额外保存 PQ 文件，与 DV 成片对照颜色和亮度；共用同次 AI，额外编码和文件占用较多空间。',
                '仅输出所选 DV 容器，减少等待和磁盘占用。')
        elif action == 'output':
            field = 'output_dir'
            value = text('输出文件夹', config.output_dir,
                '留空与源视频放一起；支持拖入带引号的中文文件夹路径。源视频不会被替换。', output_directory_valid)
            if value != BACK: value = normalize_path(value) if value.strip() else ''
        elif action == 'temp':
            field = 'keep_temp'
            value = toggle('保留本次中间媒体', config.keep_temp,
                '保留缓存和中间文件用于排错，可能远大于成片。',
                '默认：结束/合作式取消后清理本次媒体缓存；小型日志和校验报告仍保留。')
        elif action == 'force':
            field = 'force'
            value = toggle('覆盖已有同名成片', config.force,
                '允许替换摘要中列出的同名成片；旧成片没有自动备份。',
                '默认：保留已有成片并跳过，不会把旧文件重新编码。')
        if value != BACK: setattr(config, field, value)


def configure(config, caps, description, summary, console):
    while True:
        action = choose('转换设置', main_options(config), back=False)
        if action in ('exit', BACK): raise KeyboardInterrupt
        if action == 'start':
            try: config.validate(caps, description['media'], description=description)
            except ValueError as error: console.print(Text(str(error), style='red')); continue
            summary(config, caps, description)
            if toggle('开始转换？', True, '使用上方摘要中的设置开始；转换中 Ctrl+C 可安全取消。',
                      '返回设置，继续调整。', labels=('开始转换', '继续调整')) is True: return config
        elif action == 'advanced': configure_advanced(config, caps)
        elif action == 'encoder':
            details = {'Auto': '默认：可用时选 NVIDIA，其他情况选 x265；实际选择会显示在摘要。',
                       'NVENC': '用 NVIDIA 显卡编码，速度快；VBR 可能低于目标，输出后会报告偏差。',
                       'x265': '用 CPU 编码，通常较慢；目标码率使用整片两遍分析，再编码。'}
            value = choose('视频编码器', [Option(name, name, details[name], reason)
                for name, _, reason in encoder_options(caps, config.hdr_mode, config.lossless)], config.encoder)
            if value != BACK: config.encoder = value
        elif action == 'mapping':
            details = {'Neutral': '默认：保留 TrueHDR 的亮度和颜色，不叠加本工具的曲线或肤色修正。',
                       'Natural': '实验：保持中低亮度，柔和收住高光；仍需 HDR 设备观感确认。',
                       'Reference': '旧参考风格，可能压暗阴影/提亮中间调；默认带 0.5 去黄，仅适合对照。'}
            value = choose('HDR 亮度分配 · 1000 nit', [Option(name, name, detail,
                None if name in caps['mappings'] else '后端未实现') for name, detail in details.items()], config.mapping)
            if value != BACK and value != config.mapping:
                config.mapping = value; config.brightness_strength = defaults()['brightness_strength']
                config.skin_strength = defaults()['reference_skin_tone_correction'] if value == 'Reference' else 0
        elif action == 'container':
            value = choose('输出容器', [
                Option('mp4', 'MP4', '适合投稿及多数播放器；不保留字幕；音频按兼容性策略封装。'),
                Option('mkv', 'MKV', '适合留档，复制原音和字幕；设备与网站的 DV 支持可能不同。'),
                Option('both', 'MP4 + MKV', '同一份视频编码封装两份；不重复 AI，但需要更多成片空间。')], config.container)
            if value != BACK:
                config.container = value
                if value == 'mkv': config.audio_codec = 'auto'
        elif action == 'rate':
            d = defaults(); source = description['video_bitrate']['bps'] / 1e6
            limit = d['platform_target_kbps'] / 1000; maximum = d['platform_max_kbps'] / 1000
            options = [Option('Source', '匹配源视频流（默认）', f'目标 {source:.3f} Mbps；不会静默限到 {limit:g}。同码率仍不等于无损。'),
                Option('Platform', '平台兼容限码率', f'目标最多 {limit:g} Mbps，瞬时上限 {maximum:g}；高码率素材可能丢细节，不保证平台验收。'),
                Option('Custom', '自定义平均码率', '手填视频目标，单位 kbps；1000 kbps = 1 Mbps。过低容易出现色带和块状失真。'),
                Option('Quality', '质量优先 / 无损', '不追求固定码率；使用 CQ/CRF 控制压缩，无损在高级设置另开，体积不固定。')]
            options = [Option(o.value, o.label, o.detail, None if o.value in caps['bitrate_modes'] else '后端未实现') for o in options]
            value = choose('视频码率', options, config.bitrate_mode)
            if value == BACK: continue
            custom = 0
            if value == 'Custom':
                custom = number('目标视频码率（kbps）', config.video_bitrate or round(source * 1000), 1, 2147483647,
                    f'源视频约 {source:.3f} Mbps。只计算视频；音频和容器不计入此目标。', integer=True)
                if custom == BACK: continue
            if value == 'Platform' and source > limit:
                if toggle('确认降低码率目标？', False, f'将 {source:.3f} Mbps 的源目标降为 {limit:g} Mbps。',
                          '保留原来的码率策略。', labels=('使用平台限制', '保留原设置')) is not True: continue
            config.bitrate_mode = value; config.video_bitrate = custom; config.max_video_bitrate = 0
            if value != 'Quality': config.lossless = False
        elif action == 'audio':
            value = choose('MP4 音频策略', [
                Option('auto', '兼容性优先（默认）', '可复制则复制；兼容性风险 PCM 无损转 ALAC，并校验解码采样一致。', description.get('audio_plan_error')),
                Option('copy', '强制原样复制', '不重新编码；MP4 中 PCM 的下游识别有风险，B站 Hi-Res 需投稿实测。'),
                Option('alac', '显式 ALAC 无损', '对支持的音轨无损封装并校验样本；不提升源音质或把 16bit 变成真正 24bit。', description.get('alac_plan_error'))], config.audio_codec)
            if value != BACK: config.audio_codec = value
        elif action == 'mode':
            value = choose('SDR → HDR 处理方式', [
                Option('TrueHDR', 'NVIDIA TrueHDR', '用 NVIDIA AI 推断 HDR；已剪切的 SDR 高光无法恢复成真实原始细节。',
                       description.get('truehdr_input_error') if caps['truehdr_ready'] else 'SDK / GPU 未就绪，请先部署'),
                Option('Original', '标准 SDR 上变换', '按固定曲线扩展亮度，无 AI 推理；只改变映射，不恢复原片没有的信息。')], config.hdr_mode)
            if value != BACK:
                config.hdr_mode = value
                if value == 'Original': config.hdr10 = False
