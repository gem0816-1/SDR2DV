"""Decode-sample, packet and timing verification. Never edits/remixes audio."""
import argparse
import hashlib
from pathlib import Path
import subprocess
import tempfile

from media_probe import number,probe,effective_bits,hires_precheck
from task_io import CREATE_FLAGS,check_cancel,compact_fields,progress,stream_lines,write_json
from mp4_audio import validate_entries


def layout(stream):
    return stream.get('channel_layout') or {1:'mono',2:'stereo'}.get(int(stream.get('channels',0)),'unknown')


def canonical_format(stream):
    fmt=stream.get('sample_fmt','')
    if fmt.startswith('dbl'):return 'f64le','pcm_f64le',8
    if fmt.startswith('flt'):return 'f32le','pcm_f32le',4
    bits=effective_bits(stream)
    if 0<bits<=16:return 's16le','pcm_s16le',2
    if 0<bits<=24:return 's24le','pcm_s24le',3
    return 's32le','pcm_s32le',4


def decoded_samples(path,index,source_stream,ffmpeg='ffmpeg',progress_path=None,offset_seconds=0):
    raw_format,codec,width=canonical_format(source_stream)
    count=0
    digest=hashlib.sha256()
    rate=int(source_stream['sample_rate'])
    channels=int(source_stream['channels'])
    command=[ffmpeg,'-v','error','-xerror','-nostdin','-err_detect','explode','-ss','0','-i',str(path),'-map',f'0:a:{index}',
             '-vn','-sn','-dn','-c:a',codec,'-f',raw_format,'pipe:1']
    with tempfile.TemporaryFile() as errors:
        child=subprocess.Popen(command,stdout=subprocess.PIPE,stderr=errors,creationflags=CREATE_FLAGS)
        try:
            while True:
                check_cancel()
                block=child.stdout.read(1024*1024)
                if not block:break
                digest.update(block)
                count+=len(block)
                progress(progress_path,0,offset_seconds+count/(width*channels*rate))
            if child.wait():
                errors.seek(0)
                raise RuntimeError(errors.read().decode('utf-8',errors='replace'))
        finally:
            if child.poll() is None:child.kill();child.wait()
            child.stdout.close()
    if count%(width*channels):raise ValueError('Incomplete interleaved audio sample')
    return dict(sha256=digest.hexdigest(),bytes=count,samples_per_channel=count//(width*channels),
                canonical_format=raw_format,duration_seconds=count/(width*channels*rate))


def packet_digest(path,index,ffprobe='ffprobe'):
    command=[ffprobe,'-v','error','-select_streams',f'a:{index}','-show_packets','-show_data_hash','sha256',
             '-show_entries','packet=data_hash,size,flags','-of','compact=p=0:nk=0',str(path)]
    digest=hashlib.sha256()
    packets=size=0
    for line in stream_lines(command):
        item=compact_fields(line)
        if 'data_hash' not in item:continue
        digest.update(item['data_hash'].encode('ascii')+b'\n')
        packets+=1
        size+=int(item['size'])
    return dict(sha256=digest.hexdigest(),packets=packets,bytes=size)


def compare(source,target,ffmpeg='ffmpeg',ffprobe='ffprobe',progress_path=None,allow_lossy=False):
    src=probe(source,ffprobe);dst=probe(target,ffprobe)
    original=[s for s in src['streams'] if s['codec_type']=='audio']
    output=[s for s in dst['streams'] if s['codec_type']=='audio']
    source_descriptors=validate_entries(source,original)
    if len(original)!=len(output):raise ValueError('Audio track count changed')
    sv=next((s for s in src['streams'] if s['codec_type']=='video'),{})
    tv=next((s for s in dst['streams'] if s['codec_type']=='video'),{})
    results=[];any_lossy=False
    for index,(a,b) in enumerate(zip(original,output)):
        for field in ('sample_rate','channels'):
            if str(a.get(field))!=str(b.get(field)):raise ValueError(f'Audio {index+1}: {field} changed')
        if layout(a)!=layout(b):raise ValueError(f'Audio {index+1}: channel layout changed')
        copied=a['codec_name']==b['codec_name']
        alac=b['codec_name']=='alac' and a['codec_name']!=b['codec_name']
        track_lossy=allow_lossy and not copied and not alac
        any_lossy=any_lossy or track_lossy
        if not copied and not alac and not allow_lossy:raise ValueError('Unexpected lossy audio conversion')
        if alac and effective_bits(a)!=effective_bits(b):raise ValueError('ALAC effective bit depth changed')
        source_offset=number(a.get('start_time'),0)-number(sv.get('start_time'),0)
        output_offset=number(b.get('start_time'),0)-number(tv.get('start_time'),0)
        # Matroska timestamps are commonly on a 1ms grid; decoded samples must
        # still be identical. Keep this separate from signal/sample integrity.
        tolerance=max(1/int(a['sample_rate']),.0011 if Path(target).suffix.lower()=='.mkv' else .0001)
        if abs(output_offset-source_offset)>tolerance:
            raise ValueError(f'Audio {index+1}: A/V start offset changed ({source_offset} -> {output_offset})')
        source_pcm=decoded_samples(source,index,a,ffmpeg,progress_path)
        target_pcm=decoded_samples(target,index,a,ffmpeg,progress_path,source_pcm['duration_seconds'])
        exact=source_pcm==target_pcm
        if not exact and not track_lossy:raise ValueError(f'Audio {index+1}: decoded samples differ or samples were lost/repeated')
        if track_lossy and abs(target_pcm['duration_seconds']-source_pcm['duration_seconds'])>.01:
            raise ValueError('Explicit lossy audio duration changed by more than 10ms')
        packets_equal=None
        if copied:
            source_packets=packet_digest(source,index,ffprobe)
            target_packets=packet_digest(target,index,ffprobe)
            packets_equal=source_packets==target_packets
        if copied and effective_bits(a)>0 and effective_bits(b)>0 and effective_bits(a)!=effective_bits(b):
            raise ValueError(f'Audio {index+1}: effective bits changed')
        results.append(dict(index=index,source_codec=a['codec_name'],output_codec=b['codec_name'],
            codec_tag=b.get('codec_tag_string'),sample_rate=int(a['sample_rate']),effective_bits=effective_bits(b),
            channels=a['channels'],channel_layout=layout(a),decoded_samples_identical=exact,
            source_pcm=source_pcm,target_pcm=target_pcm,compressed_packets_identical=packets_equal,
            av_offset_seconds=output_offset,offset_tolerance_seconds=tolerance,hires=hires_precheck(a),
            packet_note='载荷相同' if packets_equal else ('重新编码；压缩包不要求相同' if not copied else '封装/预滚包可能改变；以采样与时间检查共同判断')))
    descriptors=validate_entries(target,output)
    return dict(source=str(Path(source).resolve()),output=str(Path(target).resolve()),
                verification='parameters_passed_explicit_lossy' if any_lossy else 'passed',tracks=results,
                source_mp4_sample_entries=source_descriptors,mp4_sample_entries=descriptors,platform_audio_verified=False)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source');parser.add_argument('target')
    parser.add_argument('--ffmpeg',default='ffmpeg');parser.add_argument('--ffprobe',default='ffprobe')
    parser.add_argument('--output',required=True);parser.add_argument('--allow-lossy',action='store_true')
    parser.add_argument('-progress',dest='progress');parser.add_argument('-nostats',action='store_true')
    args=parser.parse_args()
    result=compare(args.source,args.target,args.ffmpeg,args.ffprobe,args.progress,args.allow_lossy)
    write_json(args.output,result)
    print(f'Audio integrity passed: {len(result["tracks"])} tracks; platform playback remains unverified')


if __name__=='__main__':main()
