"""Bind a completed cache's interpretation to tags inside the hashed media.

This detects accidental/stale sidecar changes, not a malicious author who can
rewrite both the media and manifest. SHA256 is an integrity check, not a signer.
"""
import hashlib
import json
import uuid
from task_io import json_command

FIELDS=('cache_kind','input_sha256','parameters','brightness_mapping','fps','input_frames',
        'frames','sdk','binding','width','height','hlg_rgb_transport','input_filter')


def config_digest(report):
    data={key:report.get(key) for key in FIELDS}
    return hashlib.sha256(json.dumps(data,sort_keys=True,separators=(',',':'),ensure_ascii=True,
                                    allow_nan=False).encode('ascii')).hexdigest()


def new_binding(report):
    return dict(schema=1,id=uuid.uuid4().hex,config_sha256=config_digest(report))


def media_tags(path,ffprobe):
    data=json_command([ffprobe,'-v','error','-show_entries','format_tags','-of','json',str(path)])
    return {k.lower():str(v) for k,v in data.get('format',{}).get('tags',{}).items()}


def verify_binding(report,path,ffprobe):
    tags=media_tags(path,ffprobe);binding=report.get('cache_binding')
    if not binding:
        if 'sdr2dv_cache_id' in tags or 'sdr2dv_config_sha256' in tags:
            raise ValueError('Cache binding removed from manifest')
        return 'legacy_media_sha256_only_unbound_interpretation'
    if (binding.get('schema')!=1 or report.get('frames')!=report.get('input_frames') or
        config_digest(report)!=binding.get('config_sha256') or
        tags.get('sdr2dv_cache_id')!=binding.get('id') or
        tags.get('sdr2dv_config_sha256')!=binding.get('config_sha256')):
        raise ValueError('Cache media/manifest configuration binding mismatch')
    return 'media_and_configuration_sha256'
