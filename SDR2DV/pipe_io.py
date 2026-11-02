"""Bounded binary frame writes; never retry an ambiguous failed media write."""
import os

CHUNK_BYTES = 1024 * 1024


class FrameWriteError(RuntimeError):
    pass


def windows_io_error():
    """Capture the CRT's underlying Win32 error before cleanup overwrites it."""
    if os.name != 'nt': return None
    try:
        import ctypes
        value = ctypes.c_ulong()
        crt = ctypes.CDLL('ucrtbase')
        if crt._get_doserrno(ctypes.byref(value)) == 0: return value.value
    except (OSError, AttributeError): pass
    return None


def write_frame(pipe, data, label='video', frame_number=None):
    from task_io import check_cancel
    payload = memoryview(data).cast('B')
    offset = 0
    while offset < len(payload):
        check_cancel()
        block = payload[offset:offset+CHUNK_BYTES]
        try:
            written = pipe.write(block)
        except OSError as error:
            native = windows_io_error()
            raise FrameWriteError(f'{label} frame={frame_number}: pipe write failed after '
                f'{offset}/{len(payload)} confirmed bytes; chunk={len(block)}, '
                f'errno={error.errno}, win32_error={native}. No frame was retried.') from error
        if written is None or written <= 0 or written > len(block):
            raise FrameWriteError(f'{label} frame={frame_number}: invalid short write {written}; '
                                  f'confirmed {offset}/{len(payload)} bytes')
        offset += written
