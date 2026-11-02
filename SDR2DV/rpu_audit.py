"""Verify dovi_tool's exported L1 and scene flags against measured config."""
import argparse
import json
from pathlib import Path


def verify(config,levels,scene_indices):
    expected=sum(s['duration'] for s in config['shots'])
    if len(levels)!=expected:raise ValueError('RPU frame count differs from final base layer')
    if scene_indices!=[s['start'] for s in config['shots']]:raise ValueError('RPU scene refresh flags differ from shot boundaries')
    for shot in config['shots']:
        l1=next(block['Level1'] for block in shot['metadata_blocks'] if 'Level1' in block)
        for frame in range(shot['start'],shot['start']+shot['duration']):
            actual=levels[frame]
            if actual['frame']!=frame or any(actual[k]!=v for k,v in l1.items()):
                raise ValueError(f'RPU encoded L1 mismatch at frame {frame}')
    return dict(frames=expected,shots=len(config['shots']),encoded_l1_verified=True,scene_refresh_flags_verified=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('config');parser.add_argument('levels');parser.add_argument('scenes')
    args=parser.parse_args()
    load=lambda p:json.loads(Path(p).read_text(encoding='utf-8-sig'))
    scenes=[int(line) for line in Path(args.scenes).read_text().splitlines() if line.strip()]
    print(json.dumps(verify(load(args.config),load(args.levels),scenes)))
