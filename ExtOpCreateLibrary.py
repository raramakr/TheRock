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
"""
ExtOpCreateLibrary.py

Builds the hipBLASLt ExtOp library by scanning per-arch YAML/JSON metadata,
merging into an existing library (if present), and writing the result safely.

Key improvements (important for Windows CI robustness):
- Writes are done via a temporary file in the same directory, then an atomic
  replace of the destination file (no partial/half-written output is ever visible).
- On Windows, the final replacement uses MoveFileExW(REPLACE_EXISTING|WRITE_THROUGH)
  to more reliably overwrite a destination that other processes may be observing.
- Reads of an existing library and the final atomic replace both tolerate transient
  sharing violations by retrying with exponential backoff.

"""

from argparse import ArgumentParser
from collections import defaultdict
import os, glob, time, errno, random
import msgpack, yaml, json

try:
    # Optional—only needed on Windows for MoveFileExW.
    import ctypes
except Exception:
    ctypes = None

# --- Helpers ---

# Map requested output format -> (de)serializer module.
# msgpack is used for the shipping .dat format; yaml/json also supported.
_WRITERS = {"dat": msgpack, "yaml": yaml, "json": json}


def _dump_to(path, fmt, data, mode):
    """
    Serialize `data` to `path` using the writer for `fmt`, opening the file with `mode`
    ('wb' for binary .dat, 'w' for text). We best-effort flush+fsync so the temp file
    is on disk before we try to replace the destination.
    """
    with open(path, mode) as f:
        _WRITERS[fmt].dump(data, f)
        # Ensure the temp file's contents are durable before the rename/replace.
        # Not strictly required on all platforms, but reduces risk after a crash.
        try:
            f.flush()
            os.fsync(f.fileno())
        except Exception:
            # fsync may not exist or be necessary; ignore failures.
            pass


def _replace_atomic(src, dst):
    """
    Atomically replace `dst` with `src` (which must be in the same directory).

    - On Windows, use MoveFileExW with REPLACE_EXISTING and WRITE_THROUGH to
      replace in-place and request the system to write through any caches.
    - On POSIX, os.rename() within the same directory is atomic and overwrites.
      (We ensure the temp file is created next to the destination.)
    """
    if os.name == "nt" and ctypes:
        MOVEFILE_REPLACE_EXISTING = 0x1
        MOVEFILE_WRITE_THROUGH = 0x8
        rc = ctypes.windll.kernel32.MoveFileExW(
            ctypes.c_wchar_p(src),
            ctypes.c_wchar_p(dst),
            MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH,
        )
        if rc == 0:
            # Convert the last Win32 error into an OSError so callers can
            # implement retry/backoff for sharing violations.
            raise OSError(ctypes.get_last_error(), "MoveFileExW failed")
    else:
        # POSIX: rename over an existing file (same dir) is atomic and effectively
        # replaces it (the old name now refers to the new inode).
        os.rename(src, dst)


def _retry_read(path, fmt, is_bin, tries=30, base=0.02):
    """
    Read+deserialize a file with retries on Windows sharing violations.

    Parameters:
      path   : file to read
      fmt    : 'dat'|'yaml'|'json'
      is_bin : True for msgpack .dat, False for text formats
      tries  : number of read attempts before giving up
      base   : base delay (seconds) for exponential backoff

    On Windows, if another process temporarily holds the file open exclusively,
    we may see WinError 5/32/33 or EACCES/EBUSY. We retry with exponential
    backoff to allow the other process to release.
    """
    mode = "rb" if is_bin else "r"
    for i in range(tries):
        try:
            with open(path, mode) as f:
                return _WRITERS[fmt].load(f)
        except (PermissionError, OSError) as e:
            # Only retry the Windows class of "file in use"/sharing violations.
            if os.name != "nt":
                raise
            if getattr(e, "winerror", 0) in (5, 32, 33) or getattr(e, "errno", None) in (
                errno.EACCES,
                errno.EBUSY,
            ):
                # Exponential backoff with a small jitter:  base * 2^i + ~10ms
                time.sleep(base * (2 ** min(i, 8)) + random.random() * 0.01)
                continue
            raise
    raise PermissionError(f"Could not read '{path}' due to sharing violation.")


def _atomic_dump(path, fmt, data, is_bin):
    """
    Atomically write the serialized `data` to `path`.

    Steps:
      1) Create a unique temp file next to the destination (same directory).
      2) Serialize + fsync the temp file.
      3) Atomically replace the destination with the temp file.
         - On Windows, retry a bounded number of times to ride out transient
           sharing violations when another process temporarily has the file open.

    If replacement never succeeds (e.g., a persistent lock), raise PermissionError.
    """
    # Ensure the directory exists; keep temp file on the same filesystem/dir
    # so the final rename/replace is guaranteed atomic.
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)

    tmp = f"{path}.tmp-{os.getpid()}-{int(time.time() * 1000)}"
    mode = "wb" if is_bin else "w"

    # Write to temp first so the destination is never half-written.
    _dump_to(tmp, fmt, data, mode)

    # Retry the final replace on Windows sharing violations only.
    for i in range(120):  # ~6s max with backoff (0.05 * 2^8 + jitter)
        try:
            _replace_atomic(tmp, path)
            return
        except (PermissionError, OSError) as e:
            if os.name != "nt":
                # On non-Windows, treat errors as fatal (no known transient share-violations).
                raise
            if getattr(e, "winerror", 0) in (5, 32, 33) or getattr(e, "errno", None) in (
                errno.EACCES,
                errno.EBUSY,
            ):
                time.sleep(0.05 * (2 ** min(i, 8)) + random.random() * 0.01)
                continue
            raise
    raise PermissionError(f"Could not replace '{path}' due to sharing violation.")


# --- main ---

if __name__ == "__main__":
    ap = ArgumentParser(description="Parse op YAMLs and create library for hipBLASLt")
    ap.add_argument("--src", type=str, required=True, help="Folder that contains op meta files")
    ap.add_argument("--co", type=str, required=True, help="Path to code object file")
    ap.add_argument(
        "--input-format",
        type=str,
        default="yaml",
        choices=("yaml", "json"),
        help="Input kernel meta format",
    )
    ap.add_argument(
        "--format",
        type=str,
        default="dat",
        choices=("yaml", "json", "dat"),
        help="Library format, default is dat",
    )
    ap.add_argument("--output", type=str, default="./", help="Output folder")
    ap.add_argument("--arch", type=str, required=True, help="GPU Architecture, e.g. gfx90a")
    args = ap.parse_args()

    # Resolve '~' and environment variables early so globbing works as expected.
    src_folder = os.path.expandvars(os.path.expanduser(args.src))
    lib_format = args.format
    input_format = args.input_format
    co_path = args.co
    output = args.output
    opt_arch = args.arch

    # Library structure:
    # lib_meta[arch][op][datatype] = [list of kernel meta dicts]
    lib_meta = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))

    # Collect per-arch meta files (yaml/json) for the requested arch suffix.
    for p in glob.glob(f"{src_folder}/*{opt_arch}.{input_format}"):
        with open(p) as f:
            meta_dict = yaml.load(f, yaml.SafeLoader) if input_format == "yaml" else json.load(f)

        # Store just the basename of the code object—consumers know where to find it.
        meta_dict["co_path"] = os.path.basename(co_path)

        # Normalize/partition the meta entries.
        arch = meta_dict.pop("arch")
        op = meta_dict.pop("op")
        datatype = meta_dict["io_type"]
        lib_meta[arch][op][datatype].append(meta_dict)

    # Destination path for the merged library in the selected serialization format.
    is_binary = lib_format == "dat"
    output_lib_path = os.path.join(output, f"hipblasltExtOpLibrary.{lib_format}")

    # If a previous library exists, read it (with Windows retry) and shallow-merge.
    # For top-level conflicts, the newly generated entries take precedence.
    if os.path.exists(output_lib_path):
        org = _retry_read(output_lib_path, lib_format, is_binary)
        lib_meta = {**org, **lib_meta}

    # Atomic write to the destination (Windows-safe).
    _atomic_dump(output_lib_path, lib_format, lib_meta, is_binary)
