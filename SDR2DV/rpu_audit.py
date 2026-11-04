"""Verify dovi_tool's exported L1 and scene flags against measured config."""
import argparse
import json
from pathlib import Path


def verify(config,levels,scene_indices):
    expected=sum(s['duration'] for s in config['shots'])
    if scene_indices!=[s['start'] for s in config['shots']]:raise ValueError('RPU scene refresh flags differ from shot boundaries')
    records=iter(levels);index=0
    for shot in config['shots']:
        if shot['start']!=index or type(shot['duration']) is not int or shot['duration']<=0:raise ValueError('Invalid RPU shot timeline')
        l1=next(block['Level1'] for block in shot['metadata_blocks'] if 'Level1' in block)
        for frame in range(shot['start'],shot['start']+shot['duration']):
            try:actual=next(records)
            except StopIteration:raise ValueError('RPU frame count differs from final base layer') from None
            if actual['frame']!=frame or any(actual[k]!=v for k,v in l1.items()):
                raise ValueError(f'RPU encoded L1 mismatch at frame {frame}')
            index+=1
    if next(records,None) is not None:raise ValueError('RPU frame count differs from final base layer')
    return dict(frames=expected,shots=len(config['shots']),encoded_l1_verified=True,scene_refresh_flags_verified=True)


def iter_level_records(path):
    """Incrementally decode dovi_tool's JSON object array, bounded per record.

    Avoid json.load on a full-film per-frame export. Shot configuration and cut
    indices remain O(shots); no brightness values or checks are approximated.
    """
    decoder=json.JSONDecoder();limit=65536
    with open(path,encoding='utf-8-sig') as source:
        text='';eof=False
        def more():
            nonlocal text,eof
            block=source.read(limit);eof=not block;text+=block
        def space():
            nonlocal text
            text=text.lstrip()
            while not text and not eof:more();text=text.lstrip()
        space()
        if not text.startswith('['):raise ValueError('RPU levels must be a JSON array')
        text=text[1:];first=True
        while True:
            space()
            if text.startswith(']'):
                if text[1:].strip():raise ValueError('Trailing data in RPU levels')
                while True:
                    block=source.read(limit)
                    if not block:break
                    if block.strip():raise ValueError('Trailing data in RPU levels')
                return
            if not first:
                if not text.startswith(','):raise ValueError('Missing JSON record separator')
                text=text[1:];space()
                if text.startswith(']'):raise ValueError('Trailing comma in RPU levels')
            while True:
                try:value,end=decoder.raw_decode(text);break
                except json.JSONDecodeError as error:
                    if eof:raise ValueError('Truncated/invalid RPU levels JSON') from error
                    if len(text)>=limit:raise ValueError('RPU level record exceeds size limit') from error
                    more()
            if not isinstance(value,dict):raise ValueError('RPU level record must be an object')
            yield value;text=text[end:];first=False


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('config');parser.add_argument('levels');parser.add_argument('scenes')
    args=parser.parse_args()
    load=lambda p:json.loads(Path(p).read_text(encoding='utf-8-sig'))
    scenes=[int(line) for line in Path(args.scenes).read_text().splitlines() if line.strip()]
    print(json.dumps(verify(load(args.config),iter_level_records(args.levels),scenes)))
