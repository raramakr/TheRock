from argparse import ArgumentParser
from collections import defaultdict
import os
import glob
import msgpack
import yaml
import json
import logging

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),  # Output to stdout (visible in build logs)
        logging.FileHandler('ExtOpMergeLibrary.log')  # Save to file for debugging
    ]
)
logger = logging.getLogger(__name__)

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
    logger.info(f"Starting ExtOpMergeLibrary.py with args: output={output}, format={lib_format}")

    output_format_2_loader = {'dat': msgpack, 'yaml': yaml, 'json': json}
    lib_meta = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))

    intermediate_files = glob.glob(os.path.join(output, f'hipblasltExtOpLibrary_*.{lib_format}'))
    logger.info(f"Found {len(intermediate_files)} intermediate files matching pattern '{os.path.join(output, f'hipblasltExtOpLibrary_*.{lib_format}')}': {intermediate_files}")

    for p in intermediate_files:
        open_format = 'rb' if lib_format == 'dat' else 'r'
        logger.info(f"Reading intermediate file: {p}")
        try:
            with open(p, open_format) as f:
                per_arch_meta = output_format_2_loader[lib_format].load(f)
            logger.info(f"Successfully loaded intermediate file: {p}")
            deep_merge(lib_meta, per_arch_meta)
            logger.info(f"Merged metadata from {p}")
        except Exception as e:
            logger.error(f"Failed to load intermediate file {p}: {str(e)}")
            raise

    output_lib_path = os.path.join(output, f'hipblasltExtOpLibrary.{lib_format}')
    output_open_format = 'wb' if lib_format == 'dat' else 'w'
    logger.info(f"Writing final merged file: {output_lib_path}")
    try:
        with open(output_lib_path, output_open_format) as f:
            output_format_2_loader[lib_format].dump(lib_meta, f)
        logger.info(f"Successfully wrote final file: {output_lib_path}")
    except Exception as e:
        logger.error(f"Failed to write final file {output_lib_path}: {str(e)}")
        raise

    # Clean up intermediate files
    logger.info(f"Cleaning up intermediate files: {intermediate_files}")
    for p in intermediate_files:
        try:
            os.remove(p)
            logger.info(f"Deleted intermediate file: {p}")
        except Exception as e:
            logger.error(f"Failed to delete intermediate file {p}: {str(e)}")
            raise
