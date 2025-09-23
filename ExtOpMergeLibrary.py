from argparse import ArgumentParser
from collections import defaultdict
import os
import glob
import msgpack
import yaml
import json

def deep_merge(target, source):
    for key, value in source.items():
        if isinstance(value, dict) and key in target:
            deep_merge(target[key], value)
        else:
            target[key] = value
    return target

if __name__ == '__main__':
    ap = ArgumentParser(description='Merge per-arch libraries into final hipBLASLt library')
    ap.add_argument('--output', type=str, default='./', help='Output folder')
    ap.add_argument('--format', type=str, default='dat', choices=('yaml', 'json', 'dat'), help='Library format')
    args = ap.parse_args()

    output = args.output
    lib_format = args.format
    output_format_2_loader = {'dat': msgpack, 'yaml': yaml, 'json': json}

    lib_meta = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    for p in glob.glob(os.path.join(output, f'hipblasltExtOpLibrary_*.{lib_format}')):
        open_format = 'rb' if lib_format == 'dat' else 'r'
        with open(p, open_format) as f:
            per_arch_meta = output_format_2_loader[lib_format].load(f)
        deep_merge(lib_meta, per_arch_meta)

    output_lib_path = os.path.join(output, f'hipblasltExtOpLibrary.{lib_format}')
    output_open_format = 'wb' if lib_format == 'dat' else 'w'
    with open(output_lib_path, output_open_format) as f:
        output_format_2_loader[lib_format].dump(lib_meta, f)

    # Clean up intermediate files
    for p in glob.glob(os.path.join(output, f'hipblasltExtOpLibrary_*.{lib_format}')):
        os.remove(p)
