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
import os, glob, time, errno, random
import msgpack, yaml, json

try:
    import ctypes
except Exception:
    ctypes = None

# ---------------------- logging helpers ---------------------------------------

def _verbosity():
    try:
        return int(os.getenv("HIPBLASLT_EXTOP_VERBOSE", "1"))
    except Exception:
        return 1

_V = _verbosity()

import sys
try:
    # Python 3.7+: force immediate writes even when not attached to a TTY
    sys.stdout.reconfigure(line_buffering=True, write_through=True)
except Exception:
    pass

def _log(msg, level=1):
    if _verbosity() >= level:
        print(f"[hipBLASLt][ExtOpCreateLibrary] {msg}", flush=True)


def _err_detail(e):
    winerr = getattr(e, "winerror", None)
    return f"{type(e).__name__}(errno={getattr(e,'errno',None)}, winerror={winerr}): {e}"

# ---------------------- format helpers ----------------------------------------

_WRITERS = {"dat": msgpack, "yaml": yaml, "json": json}

def _dump_to(path, fmt, data, mode):
    with open(path, mode) as f:
        _WRITERS[fmt].dump(data, f)
        try:
            f.flush(); os.fsync(f.fileno())
        except Exception:
            # fsync may not be available/necessary everywhere; ignore errors
            pass

def _replace_atomic(src, dst):
    # Windows: use MoveFileExW for atomic replace + write-through
    if os.name == "nt" and ctypes:
        MOVEFILE_REPLACE_EXISTING = 0x1
        MOVEFILE_WRITE_THROUGH    = 0x8
        rc = ctypes.windll.kernel32.MoveFileExW(
            ctypes.c_wchar_p(src),
            ctypes.c_wchar_p(dst),
            MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH,
        )
        if rc == 0:
            raise OSError(ctypes.get_last_error(), "MoveFileExW failed")
    else:
        # POSIX: rename within same dir is atomic and overwrites
        os.rename(src, dst)

def _retry_read(path, fmt, is_bin, tries=30, base=0.02):
    mode = "rb" if is_bin else "r"
    for i in range(tries):
        try:
            if i == 0:
                _log(f"Reading existing library '{path}' (fmt={fmt}, bin={is_bin})", 1)
            elif _V >= 2:
                _log(f"read attempt {i+1} on '{path}'", 2)

            with open(path, mode) as f:
                return _WRITERS[fmt].load(f)

        except (PermissionError, OSError) as e:
            if os.name != "nt":
                raise
            if getattr(e, "winerror", 0) in (5, 32, 33) or getattr(e, "errno", None) in (errno.EACCES, errno.EBUSY):
                # Sharing violation: backoff and retry
                sleep = base * (2 ** min(i, 8)) + random.random() * 0.01
                _log(f"read locked -> retry in {sleep:.3f}s ({_err_detail(e)})", 2)
                time.sleep(sleep)
                continue
            raise
    raise PermissionError(f"Could not read '{path}' due to sharing violation after {tries} attempts.")

def _atomic_dump(path, fmt, data, is_bin):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = f"{path}.tmp-{os.getpid()}-{int(time.time()*1000)}"
    mode = "wb" if is_bin else "w"

    _log(f"Writing to temp '{tmp}' (fmt={fmt}, bin={is_bin})", 1)
    _dump_to(tmp, fmt, data, mode)

    try:
        size = os.path.getsize(tmp)
        _log(f"Temp file size = {size} bytes", 2)
    except Exception:
        pass

    # Final atomic replace with retry/backoff on Windows sharing violations
    for i in range(120):  # ~6s max with backoff (0.05 * 2^8 ~= 12.8ms.. up to ~6s total)
        try:
            if _V >= 2:
                _log(f"replace attempt {i+1}: '{tmp}' -> '{path}'", 2)
            _replace_atomic(tmp, path)
            _log(f"Replaced '{path}' on attempt {i+1}", 1)
            return
        except (PermissionError, OSError) as e:
            if os.name != "nt":
                raise
            if getattr(e, "winerror", 0) in (5, 32, 33) or getattr(e, "errno", None) in (errno.EACCES, errno.EBUSY):
                sleep = 0.05 * (2 ** min(i, 8)) + random.random() * 0.01
                _log(f"replace locked -> retry in {sleep:.3f}s ({_err_detail(e)})", 2)
                time.sleep(sleep)
                continue
            raise
    raise PermissionError(f"Could not replace '{path}' due to sharing violation after 120 attempts.")

def _meta_stats(meta):
    archs = len(meta)
    ops = sum(len(meta[a]) for a in meta)
    entries = 0
    for a in meta.values():
        for dct in a.values():
            for lst in dct.values():
                entries += len(lst)
    return archs, ops, entries

# --------------------------- main ---------------------------------------------

if __name__ == "__main__":
    ap = ArgumentParser(description="Parse op YAMLs and create library for hipBLASLt")
    ap.add_argument("--src", type=str, required=True, help="Folder that contains op meta files")
    ap.add_argument("--co",  type=str, required=True, help="Path to code object file")
    ap.add_argument("--input-format", type=str, default="yaml", choices=("yaml", "json"), help="Input kernel meta format")
    ap.add_argument("--format", type=str, default="dat", choices=("yaml", "json", "dat"), help="Library format, default is dat")
    ap.add_argument("--output", type=str, default="./", help="Output folder")
    ap.add_argument("--arch", type=str, required=True, help="GPU Architecture, e.g. gfx90a")
    args = ap.parse_args()

    src_folder  = os.path.expandvars(os.path.expanduser(args.src))
    lib_format  = args.format
    input_format= args.input_format
    co_path     = args.co
    output      = args.output
    opt_arch    = args.arch

    _log(f"Start (arch={opt_arch}, fmt={lib_format}, input={input_format})", 1)
    _log(f"src='{src_folder}', output='{output}', co='{co_path}'", 2)

    lib_meta = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))

    # Scan and parse YAML/JSON
    files = sorted(glob.glob(f"{src_folder}/*{opt_arch}.{input_format}"))
    _log(f"Found {len(files)} *{opt_arch}.{input_format} files under '{src_folder}'", 1)

    for p in files:
        if _V >= 2:
            _log(f"Parsing meta: {p}", 2)
        with open(p) as f:
            meta_dict = yaml.load(f, yaml.SafeLoader) if input_format == "yaml" else json.load(f)
        meta_dict["co_path"] = os.path.basename(co_path)
        arch      = meta_dict.pop("arch")
        op        = meta_dict.pop("op")
        datatype  = meta_dict["io_type"]
        lib_meta[arch][op][datatype].append(meta_dict)

    a, o, n = _meta_stats(lib_meta)
    _log(f"Built meta in-memory: archs={a}, ops={o}, entries={n}", 1)

    is_binary = (lib_format == "dat")
    output_lib_path = os.path.join(output, f"hipblasltExtOpLibrary.{lib_format}")

    # Merge with existing .dat/.yaml/.json if present
    if os.path.exists(output_lib_path):
        try:
            sz = os.path.getsize(output_lib_path)
            _log(f"Existing library detected: '{output_lib_path}' (size={sz} bytes)", 1)
        except Exception:
            _log(f"Existing library detected: '{output_lib_path}'", 1)

        org = _retry_read(output_lib_path, lib_format, is_binary)
        oa, oo, on = _meta_stats(org) if isinstance(org, dict) else (0, 0, 0)
        _log(f"Existing meta: archs={oa}, ops={oo}, entries={on}", 1)

        # Merge (new definitions win on key conflict at top level)
        lib_meta = {**org, **lib_meta}

        ma, mo, mn = _meta_stats(lib_meta)
        _log(f"Merged meta: archs={ma}, ops={mo}, entries={mn}", 1)

    # Final atomic write
    _atomic_dump(output_lib_path, lib_format, lib_meta, is_binary)

    try:
        final_sz = os.path.getsize(output_lib_path)
        _log(f"Done. Wrote '{output_lib_path}' ({final_sz} bytes)", 1)
    except Exception:
        _log(f"Done. Wrote '{output_lib_path}'", 1)
