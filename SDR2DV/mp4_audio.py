"""Read ISO BMFF audio sample entries without loading media payloads."""
import struct
from pathlib import Path


def boxes(stream,start,end):
    position=start
    while position+8<=end:
        stream.seek(position);size,kind=struct.unpack('>I4s',stream.read(8));header=8
        if size==1:
            size=struct.unpack('>Q',stream.read(8))[0];header=16
        elif size==0:size=end-position
        if size<header or position+size>end:raise ValueError('Invalid MP4 box bounds')
        yield kind,position+header,position+size
        position+=size


def audio_entries(path):
    result=[]
    def descend(stream,start,end):
        for kind,body,limit in boxes(stream,start,end):
            if kind in (b'moov',b'trak',b'mdia',b'minf',b'stbl'):descend(stream,body,limit)
            elif kind==b'stsd':
                stream.seek(body+4);count=struct.unpack('>I',stream.read(4))[0]
                for tag,payload,stop in list(boxes(stream,body+8,limit))[:count]:
                    if tag not in (b'alac',b'ipcm',b'fpcm',b'sowt',b'twos',b'lpcm',b'in24',b'in32',
                                   b'fl32',b'fl64',b'raw ',b'NONE',b'ulaw',b'alaw'):continue
                    stream.seek(payload);base=stream.read(28)
                    if len(base)<28:raise ValueError('Short audio sample entry')
                    version=struct.unpack('>H',base[8:10])[0]
                    entry=dict(tag=tag.decode(),legacy_sample_size=struct.unpack('>H',base[18:20])[0])
                    offset={0:28,1:44,2:64}.get(version)
                    if offset is None:raise ValueError('Unsupported audio sample entry version')
                    for child,data,child_end in boxes(stream,payload+offset,stop):
                        if child==b'pcmC':
                            stream.seek(data);raw=stream.read(child_end-data)
                            if len(raw)<6:raise ValueError('Short pcmC')
                            entry['pcmC']=dict(version=raw[0],format_flags=raw[4],sample_size=raw[5],
                                little_endian=bool(raw[4]&1))
                        elif child==b'alac':
                            stream.seek(data);raw=stream.read(child_end-data)
                            if len(raw)<28:raise ValueError('Short ALAC configuration')
                            entry['alac']=dict(bit_depth=raw[9],channels=raw[13],sample_rate=int.from_bytes(raw[24:28],'big'))
                    result.append(entry)
    with Path(path).open('rb') as stream:descend(stream,0,Path(path).stat().st_size)
    return result


def validate_entries(path,streams):
    if Path(path).suffix.lower() not in ('.mov','.mp4','.m4a','.m4v'):return []
    entries=audio_entries(path)
    relevant=[s for s in streams if s['codec_name'] in ('alac',) or s['codec_name'].startswith('pcm_')]
    if len(entries)!=len(relevant):raise ValueError('MP4 PCM/ALAC sample entry count mismatch')
    for stream,entry in zip(relevant,entries):
        bits=int(stream.get('bits_per_raw_sample') or stream.get('bits_per_sample') or 0)
        if entry['tag']=='alac':
            cfg=entry.get('alac',{})
            if (cfg.get('bit_depth')!=bits or cfg.get('channels')!=stream['channels'] or
                cfg.get('sample_rate')!=int(stream['sample_rate'])):raise ValueError('ALAC configuration disagrees with decoded stream')
        elif entry['tag'] in ('ipcm','fpcm'):
            cfg=entry.get('pcmC',{})
            if cfg.get('sample_size')!=bits:raise ValueError('pcmC effective sample size mismatch')
            if cfg.get('little_endian')!=stream['codec_name'].endswith('le'):raise ValueError('pcmC byte order mismatch')
            entry['platform_compatibility_risk']='ISO PCM sample entry; platform interpretation unverified, ALAC recommended'
    return entries
