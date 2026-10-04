#!/usr/bin/env python3
"""Read process accounting without signalling. Linux fields are not macOS footprint."""
import platform
from pathlib import Path


def kb_fields(text):
    result = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 3 and parts[2] == 'kB':
            result[parts[0].rstrip(':')] = int(parts[1]) / 1024
    return result


def linux_memory(pid, destination=None):
    base = Path('/proc') / str(pid)
    status = base.joinpath('status').read_text()
    values = kb_fields(status)
    result = {'platform': 'Linux', 'rss_mib': values.get('VmRSS'),
              'rss_peak_mib': values.get('VmHWM'), 'virtual_mib': values.get('VmSize'),
              'rss_anon_mib': values.get('RssAnon'), 'rss_file_mib': values.get('RssFile'),
              'rss_shmem_mib': values.get('RssShmem'), 'swap_mib': values.get('VmSwap')}
    try:
        rollup = base.joinpath('smaps_rollup').read_text()
        fields = kb_fields(rollup)
        result.update({'pss_mib': fields.get('Pss'), 'private_dirty_mib': fields.get('Private_Dirty'),
                       'private_clean_mib': fields.get('Private_Clean'),
                       'anonymous_mib': fields.get('Anonymous'), 'smaps_rss_mib': fields.get('Rss')})
    except (PermissionError, FileNotFoundError) as error:
        rollup = type(error).__name__
        result['smaps_unavailable'] = type(error).__name__
    result['threads'] = len(list(base.joinpath('task').iterdir()))
    result['file_descriptors'] = len(list(base.joinpath('fd').iterdir()))
    if destination:
        Path(destination).write_text(status + '\n' + rollup)
        Path(destination).chmod(0o600)
    return result
