"""UI configuration and argument mapping. Media operations stay in SDR2DV.ps1."""
from dataclasses import asdict,dataclass,fields
import json
import math
from pathlib import Path

HERE=Path(__file__).resolve().parent


def defaults():return json.loads((HERE/'defaults.json').read_text(encoding='utf-8-sig'))


def nvenc_available(capabilities,hdr_mode='TrueHDR',lossless=False):
    ready=capabilities.get('nvenc_ready',False)
    return ready and (not (hdr_mode=='TrueHDR' or lossless) or capabilities.get('nvenc_lossless_ready',ready))


def normalize_path(value):
    value=str(value).strip()
    if value.startswith('& ') and len(value)>3 and value[2] in ('"',"'"):
        value=value[2:].strip()
    if len(value)>=2 and value[0]==value[-1] and value[0] in ('"',"'"):
        value=value[1:-1]
    if not value or '\0' in value or '\r' in value or '\n' in value:
        raise ValueError('请输入一个有效的视频/文件夹路径')
    return str(Path(value).expanduser().resolve())


@dataclass
class ConversionConfig:
    input_path:str=''
    hdr_mode:str='TrueHDR'
    profile:str='8.4'
    encoder:str='Auto'
    mapping:str='Neutral'
    brightness_strength:float=1.
    skin_strength:float=0.
    container:str='mp4'
    bitrate_mode:str='Source'
    video_bitrate:int=0
    max_video_bitrate:int=0
    audio_codec:str='auto'
    nvenc_preset:str='p6'
    nvenc_cq:int=18
    preset:str='medium'
    crf:int=17
    lossless:bool=False
    hdr10:bool=False
    output_dir:str=''
    keep_temp:bool=False
    force:bool=False

    @classmethod
    def initial(cls,path=''):
        d=defaults()
        return cls(input_path=path,encoder=d['encoder'],mapping=d['brightness_mapping'],
            brightness_strength=d['brightness_strength'],skin_strength=d['skin_tone_correction'],
            container=d['format'],bitrate_mode=d['bitrate_mode'],audio_codec=d['audio_codec'],
            nvenc_preset=d['nvenc_preset'],nvenc_cq=d['nvenc_cq'],preset=d['preset'],crf=d['crf'])

    @classmethod
    def from_dict(cls,data):
        if not isinstance(data,dict):raise ValueError('配置文件必须是 JSON 对象')
        names={f.name for f in fields(cls)}
        unknown=set(data)-names
        if unknown:raise ValueError('未知配置字段: '+', '.join(sorted(unknown)))
        if data.get('mapping')=='Reference' and 'skin_strength' not in data:
            data={**data,'skin_strength':defaults()['reference_skin_tone_correction']}
        value=cls(**{**asdict(cls.initial()),**data});value.validate_types();return value

    def validate_types(self):
        for name in ('video_bitrate','max_video_bitrate','nvenc_cq','crf'):
            value=getattr(self,name)
            if isinstance(value,bool) or not isinstance(value,int) or not 0<=value<=2147483647:raise ValueError(name+' 必须为非负整数')
        for name in ('brightness_strength','skin_strength'):
            value=getattr(self,name)
            if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value):raise ValueError(name+' 必须为有限数值')
        for name in ('lossless','hdr10','keep_temp','force'):
            if not isinstance(getattr(self,name),bool):raise ValueError(name+' 必须为 true / false')

    def validate(self,capabilities,media=None,require_path=True,description=None):
        self.validate_types()
        if require_path and not Path(normalize_path(self.input_path)).exists():raise ValueError('输入路径不存在')
        if self.profile!='8.4':raise ValueError('TUI 固定 Dolby Vision 8.4 / HLG；其他流程使用 PowerShell CLI')
        if self.encoder not in ('Auto','NVENC','x265'):raise ValueError('未知编码器')
        available=nvenc_available(capabilities,self.hdr_mode,self.lossless)
        if self.encoder=='NVENC' and not available:raise ValueError('当前硬件/驱动不支持本模式所需 NVENC Main10 / 无损缓存')
        if self.encoder=='x265' and not capabilities.get('x265_ready'):raise ValueError('FFmpeg 缺少 x265')
        if self.encoder=='Auto' and not (available or capabilities.get('x265_ready')):raise ValueError('没有可用编码器')
        if self.hdr_mode not in ('TrueHDR','Original'):raise ValueError('未知 HDR 模式')
        if self.hdr_mode=='TrueHDR' and not capabilities.get('truehdr_ready'):raise ValueError('TrueHDR 未就绪，请部署或明确选择普通映射')
        if self.hdr_mode=='TrueHDR' and description and description.get('truehdr_input_error'):raise ValueError(description['truehdr_input_error'])
        if self.mapping not in capabilities.get('mappings',[]):raise ValueError('后端尚未实现此亮度映射')
        if self.bitrate_mode not in capabilities.get('bitrate_modes',[]):raise ValueError('后端尚未实现此码率策略')
        if self.container not in ('mp4','mkv','both'):raise ValueError('未知输出容器')
        if not capabilities.get('muxer_ready'):raise ValueError('缺少 Dolby mp4muxer，请先部署')
        if self.audio_codec not in ('auto','copy','alac'):raise ValueError('TUI 不提供隐式有损音频处理')
        if self.audio_codec=='alac' and self.container=='mkv':raise ValueError('MKV 路径原样复制音频；ALAC 封装策略只用于 MP4')
        if description and (self.container!='mkv' or self.hdr10) and self.audio_codec in ('auto','alac'):
            error=description.get('audio_plan_error' if self.audio_codec=='auto' else 'alac_plan_error')
            if error:raise ValueError(error)
        if self.bitrate_mode=='Custom' and self.video_bitrate<=0:raise ValueError('自定义视频码率必须为正数 kbps')
        if self.max_video_bitrate and self.bitrate_mode=='Custom' and self.max_video_bitrate<self.video_bitrate:raise ValueError('码率上限不能低于目标')
        if self.bitrate_mode=='Quality' and self.max_video_bitrate:raise ValueError('质量模式不接受独立码率上限')
        if self.lossless and self.bitrate_mode!='Quality':raise ValueError('无损编码不能与目标码率策略混用')
        for value in (self.brightness_strength,self.skin_strength):
            if not math.isfinite(value) or not 0<=value<=1:raise ValueError('亮度/肤色强度须为 0..1')
        if not 0<=self.nvenc_cq<=51 or not 0<=self.crf<=51:raise ValueError('质量参数须为 0..51')
        if self.nvenc_preset not in ('p1','p2','p3','p4','p5','p6','p7'):raise ValueError('无效 NVENC 预设')
        if self.preset not in ('ultrafast','superfast','veryfast','faster','fast','medium','slow','slower','veryslow'):raise ValueError('无效 x265 预设')
        if self.hdr10 and self.hdr_mode!='TrueHDR':raise ValueError('HDR10 对照需要 TrueHDR')
        if media:
            video=next(s for s in media['streams'] if s['codec_type']=='video')
            if video.get('color_transfer') in ('smpte2084','arib-std-b67'):raise ValueError('输入已是 HDR')
            if self.hdr_mode=='TrueHDR':
                pix=video.get('pix_fmt','')
                if int(video.get('bits_per_raw_sample') or 0)>8 or any(x in pix for x in ('p9','p10','p12','p14','p16','p010','p016','rgb48','bgr48','rgba64','bgra64','f32','f16')):
                    raise ValueError('TrueHDR SDK 输入为 8bit；本程序不自动降低此 SDR 的位深')
        return self

    def backend_args(self,capabilities):
        args=['-InputPath',normalize_path(self.input_path),'-HdrMode',self.hdr_mode,'-Bilibili',
              '-Profile','8.4','-Encoder',self.encoder,'-Format',self.container,'-BitrateMode',self.bitrate_mode,
              '-Mp4AudioCodec',self.audio_codec,'-Preset',self.preset,'-NvencPreset',self.nvenc_preset]
        if self.hdr_mode=='TrueHDR':
            args+=['-TrueHDRLuminance','1000','-BrightnessMapping',self.mapping,
                   '-BrightnessStrength',str(self.brightness_strength),'-SkinToneCorrection',str(self.skin_strength)]
        if self.bitrate_mode=='Custom':args+=['-VideoBitrate',str(self.video_bitrate)]
        if self.max_video_bitrate:args+=['-MaxVideoBitrate',str(self.max_video_bitrate)]
        if self.bitrate_mode=='Quality' and not self.lossless:
            gpu=self.encoder=='NVENC' or (self.encoder=='Auto' and nvenc_available(capabilities,self.hdr_mode,self.lossless))
            args+=['-NvencCq' if gpu else '-Crf',str(self.nvenc_cq if gpu else self.crf)]
        if self.output_dir:args+=['-OutputDir',normalize_path(self.output_dir)]
        for enabled,flag in ((self.lossless,'-Lossless'),(self.hdr10,'-KeepHDR10'),(self.keep_temp,'-KeepTemp'),(self.force,'-Force')):
            if enabled:args.append(flag)
        return args


def planned_outputs(config):
    """Display the core's existing filenames, without creating or probing media."""
    source = Path(normalize_path(config.input_path))
    folder = Path(normalize_path(config.output_dir)) if config.output_dir else source.parent
    tone = ''
    if config.hdr_mode == 'TrueHDR' and config.brightness_strength > 0:
        tone = {'Reference': '_Ref', 'Natural': '_Natural', 'realDolby': '_realDolby'}.get(config.mapping, '')
    prefix = source.stem + ('_TrueHDR' + tone if config.hdr_mode == 'TrueHDR' else '')
    extensions = ('mp4', 'mkv') if config.container == 'both' else (config.container,)
    paths = [folder / (prefix + '_DV' + config.profile.replace('.', '') + '.' + ext) for ext in extensions]
    if config.hdr10: paths.append(folder / (prefix + '_HDR10.mp4'))
    return paths


def encoder_options(capabilities,hdr_mode='TrueHDR',lossless=False):
    available=nvenc_available(capabilities,hdr_mode,lossless)
    error=(capabilities.get('nvenc_lossless_error') if capabilities.get('nvenc_ready') else capabilities.get('nvenc_error')) or '本模式所需 Main10 / 无损缓存不可用'
    return [('Auto','自动选择后端可用编码器',None if available or capabilities.get('x265_ready') else '没有可用编码器'),
            ('NVENC','NVIDIA Main10 硬件编码',None if available else error),
            ('x265','CPU 软件编码',None if capabilities.get('x265_ready') else 'FFmpeg 缺少 x265')]
