################################################################################
#
# Copyright (C) 2023 Advanced Micro Devices, Inc. All rights reserved.
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell cop-
# ies of the Software, and to permit persons to whom the Software is furnished
# to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IM-
# PLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS
# FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR
# COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER
# IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNE-
# CTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
################################################################################

from argparse import ArgumentParser
from collections import defaultdict
import os
import glob
import msgpack
import yaml
import json
import time
import errno
import random
try:
    import ctypes  # Used only on Windows for MoveFileExW
except Exception:
    ctypes = None

if __name__ == '__main__':
    ap = ArgumentParser(description='Parse op YAMLs and create library for hipBLASLt')
    ap.add_argument('--src', type=str, required=True, help='Folder that contains op meta files')
    ap.add_argument('--co', type=str, required=True, help='Path to code object file')
    ap.add_argument('--input-format', type=str, default='yaml', choices=('yaml', 'json'), help='Input kernel meta format')
    ap.add_argument('--format', type=str, default='dat', choices=('yaml', 'json', 'dat'), help='Library format, default is dat')
    ap.add_argument('--output', type=str, default='./', help='Output folder')
    ap.add_argument('--arch', type=str, required=True, help='GPU Architecture, e.g. gfx90a')
    args = ap.parse_args()
    src_folder: str = args.src
    lib_format: str = args.format
    input_format: str = args.input_format
    co_path: str = args.co
    output: str = args.output
    opt_arch: str = args.arch

    src_folder = os.path.expandvars(os.path.expanduser(src_folder))

    lib_meta = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))

    for p in glob.glob(f'{src_folder}/*{opt_arch}.{input_format}'):
        meta_dict = {}

        with open(p) as f:
            if input_format == 'yaml':
                meta_dict = yaml.load(f, yaml.SafeLoader)
            elif input_format == 'json':
                meta_dict = json.load(f)

        meta_dict['co_path'] = os.path.basename(co_path)
        arch = meta_dict.pop('arch')
        op = meta_dict.pop('op')
        datatype = meta_dict['io_type']
        lib_meta[arch][op][datatype].append(meta_dict)

    output_open_foramt = 'wb' if lib_format == 'dat' else 'w'
    output_format_2_writer = {
        'dat': msgpack,
        'yaml': yaml,
        'json': json
    }

    output_lib_path = os.path.join(output, f'hipblasltExtOpLibrary.{lib_format}')

    if os.path.exists(output_lib_path):
        update_open_foramt = 'rb' if lib_format == 'dat' else 'r'

        # Inline retry loop
        tries = 30
        base = 0.02  # seconds
        for i in range(tries):
            try:
                with open(output_lib_path, update_open_foramt) as f:
                    org_content = output_format_2_writer[lib_format].load(f)
                break
            except (PermissionError, OSError) as e:
                if os.name == 'nt' and (
                    getattr(e, 'winerror', 0) in (5, 32, 33) or
                    getattr(e, 'errno', None) in (errno.EACCES, errno.EBUSY)
                ):
                    time.sleep(base * (2 ** min(i, 8)) + random.random() * 0.01)
                    continue
                else:
                    raise
        else:
            raise PermissionError(f"Could not read '{output_lib_path}' due to sharing violation.")

        lib_meta = {**org_content, **lib_meta}

    # Atomic write: temp file + replace (MoveFileExW on Windows with retry)
    # Write to a temp file next to the destination so the final rename/replace is atomic.
    tmp_path = f"{output_lib_path}.tmp-{os.getpid()}-{int(time.time()*1000)}"

    # Serialize to temp first; attempt to flush() + fsync() for durability.
    with open(tmp_path, output_open_foramt) as f:
        output_format_2_writer[lib_format].dump(lib_meta, f)
        try:
            f.flush()
            os.fsync(f.fileno())
        except Exception:
            pass

    # Final atomic replacement:
    # - On Windows, prefer MoveFileExW(REPLACE_EXISTING | WRITE_THROUGH) and
    #   retry on transient sharing violations (WinError 5/32/33).
    # - Elsewhere, rename within the same directory is atomic and overwrites.
    def _atomic_replace(src, dst):
        if os.name == 'nt' and ctypes:
            MOVEFILE_REPLACE_EXISTING = 0x1
            MOVEFILE_WRITE_THROUGH = 0x8
            rc = ctypes.windll.kernel32.MoveFileExW(
                ctypes.c_wchar_p(src),
                ctypes.c_wchar_p(dst),
                MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH,
            )
            if rc == 0:
                raise OSError(ctypes.get_last_error(), "MoveFileExW failed")
        else:
            # Use rename to overwrite atomically within the same directory
            os.rename(src, dst)

    # Retry the final replace on Windows sharing violations only.
    for i in range(120):  # ~6s total with exponential backoff
        try:
            _atomic_replace(tmp_path, output_lib_path)
            break
        except (PermissionError, OSError) as e:
            if os.name == 'nt' and (
                getattr(e, 'winerror', 0) in (5, 32, 33) or
                getattr(e, 'errno', None) in (errno.EACCES, errno.EBUSY)
            ):
                time.sleep(0.05 * (2 ** min(i, 8)) + random.random() * 0.01)
                continue
            else:
                raise

    # Cleanup if temp remains
    try:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
    except Exception:
        pass
