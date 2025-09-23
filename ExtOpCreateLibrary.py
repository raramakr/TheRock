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
import logging

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),  # Output to stdout (visible in build logs)
        logging.FileHandler('ExtOpCreateLibrary.log')  # Save to file for debugging
    ]
)
logger = logging.getLogger(__name__)

if __name__ == '__main__':
    ap = ArgumentParser(description='Parse op YAMLs and create library for hipBLASLt')
    ap.add_argument('--src', type=str, required=True, help='Folder that contains op meta files')
    ap.add_argument('--co', type=str, required=True, help='Path to code object file')
    ap.add_argument('--input-format', type=str, default='yaml', choices=('yaml', 'json'), help='Input kernel meta format')
    ap.add_argument('--format', type=str, default='dat', choices=('yaml', 'json', 'dat'), help='Library format, default is dat')
    ap.add_argument('--output', type=str, default='./', help='Output folder')
    ap.add_argument('--arch', type=str, required=True, help='GPU Architecture, e.g. gfx90a')
    ap.add_argument('--intermediate', action='store_true', help='Output intermediate per-arch file instead of merging')
    args = ap.parse_args()

    src_folder = args.src
    lib_format = args.format
    input_format = args.input_format
    co_path = args.co
    output = args.output
    opt_arch = args.arch
    intermediate = args.intermediate

    logger.info(f"Starting ExtOpCreateLibrary.py with args: src={src_folder}, co={co_path}, input-format={input_format}, format={lib_format}, output={output}, arch={opt_arch}, intermediate={intermediate}")

    src_folder = os.path.expandvars(os.path.expanduser(src_folder))
    logger.info(f"Expanded src_folder: {src_folder}")

    lib_meta = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))

    input_files = glob.glob(f'{src_folder}/*{opt_arch}.{input_format}')
    logger.info(f"Found {len(input_files)} input files matching pattern '{src_folder}/*{opt_arch}.{input_format}': {input_files}")

    for p in input_files:
        logger.info(f"Reading input file: {p}")
        meta_dict = {}
        try:
            with open(p) as f:
                if input_format == 'yaml':
                    meta_dict = yaml.load(f, yaml.SafeLoader)
                elif input_format == 'json':
                    meta_dict = json.load(f)
            logger.info(f"Successfully loaded metadata from {p}")
        except Exception as e:
            logger.error(f"Failed to load {p}: {str(e)}")
            raise

        meta_dict['co_path'] = os.path.basename(co_path)
        arch = meta_dict.pop('arch')
        op = meta_dict.pop('op')
        datatype = meta_dict['io_type']
        lib_meta[arch][op][datatype].append(meta_dict)
        logger.info(f"Processed metadata for arch={arch}, op={op}, datatype={datatype}")

    output_format_2_writer = {
        'dat': msgpack,
        'yaml': yaml,
        'json': json
    }

    if intermediate:
        # Output per-arch intermediate file
        output_lib_path = os.path.join(output, f'hipblasltExtOpLibrary_{opt_arch}.{lib_format}')
        output_open_format = 'wb' if lib_format == 'dat' else 'w'
        logger.info(f"Writing intermediate file: {output_lib_path}")
        try:
            with open(output_lib_path, output_open_format) as f:
                output_format_2_writer[lib_format].dump(lib_meta, f)
            logger.info(f"Successfully wrote intermediate file: {output_lib_path}")
        except Exception as e:
            logger.error(f"Failed to write intermediate file {output_lib_path}: {str(e)}")
            raise
    else:
        # Merge logic for final file
        output_lib_path = os.path.join(output, f'hipblasltExtOpLibrary.{lib_format}')
        output_open_format = 'wb' if lib_format == 'dat' else 'w'
        logger.info(f"Preparing to write final file: {output_lib_path}")
        if os.path.exists(output_lib_path):
            update_open_format = 'rb' if lib_format == 'dat' else 'r'
            logger.info(f"Reading existing file for merge: {output_lib_path}")
            try:
                with open(output_lib_path, update_open_format) as f:
                    org_content = output_format_2_writer[lib_format].load(f)
                logger.info(f"Successfully read existing file: {output_lib_path}")
                lib_meta = {**org_content, **lib_meta}
                logger.info(f"Merged existing metadata with new metadata")
            except Exception as e:
                logger.error(f"Failed to read existing file {output_lib_path}: {str(e)}")
                raise

        try:
            with open(output_lib_path, output_open_format) as f:
                output_format_2_writer[lib_format].dump(lib_meta, f)
            logger.info(f"Successfully wrote final file: {output_lib_path}")
        except Exception as e:
            logger.error(f"Failed to write final file {output_lib_path}: {str(e)}")
            raise
