#!/usr/bin/env python
"""
Script to generate an index.html listing .tar.gz files in an S3 bucket, performing the following:
 * Lists .tar.gz files in the specified S3 bucket.
 * Generates HTML page with sorting and filtering options
 * Saves the HTML locally as index.html
 * Uploads index.html back to the same S3 bucket

Requirements:
 * `boto3` Python package must be installed, e.g.: pip install boto3

Examples:
Generate index.html for all tarballs in a bucket:
./index_generation_s3_tar.py --bucket <bucketname>


Generate index.html for files under a prefix and in a specific region:
./index_generation_s3_tar.py --bucket <bucketname> --prefix nightly/ --region us-east-1

"""

import os
import argparse
import boto3
from botocore.exceptions import NoCredentialsError, ClientError
import re
import json
from github_actions.github_actions_utils import gha_append_step_summary


def extract_gpu_details(files):
    gpu_family_pattern = re.compile(r"gfx(?:\d+[A-Za-z]*|\w+)")
    gpu_families = set()
    for file_name, _ in files:
        match = gpu_family_pattern.search(file_name)
        if match:
            gpu_families.add(match.group(0))
    return sorted(list(gpu_families))


def generate_index_s3(s3_client, bucket_name):
    # List all objects and select .tar.gz keys
    try:
        paginator = s3_client.get_paginator("list_objects_v2")
        page_iterator = paginator.paginate(Bucket=bucket_name)
    except NoCredentialsError:
        raise Exception("AWS credentials not found. Please set AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY.")
    except ClientError as e:
        raise Exception(f"Error accessing bucket {bucket_name}: {e}")

    files = []
    for page in page_iterator:
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if key.endswith(".tar.gz"):
                files.append((key, obj["LastModified"].timestamp()))

    if not files:
        raise Exception(f"No .tar.gz files found in bucket {bucket_name}.")

    # Page title
    bucket_lower = bucket_name.lower()
    if "dev" in bucket_lower:
        page_title = "ROCm SDK dev tarballs"
    elif "nightly" in bucket_lower or "nightlies" in bucket_lower:
        page_title = "ROCm SDK nightly tarballs"
    else:
        page_title = "ROCm SDK tarballs"

    # Prepare filter options and files array for JS
    gpu_families = extract_gpu_details(files)
    gpu_families_options = "".join([f'<option value="{family}">{family}</option>' for family in gpu_families])
    files_js_array = json.dumps([{"name": f[0], "mtime": f[1]} for f in files])

    # HTML content for displaying files
    html_content = f"""
    <html>
    <head>
        <title>{page_title}</title>
        <meta charset="utf-8"/>
        <meta http-equiv="x-ua-compatible" content="ie=edge"/>
        <meta name="viewport" content="width=device-width, initial-scale=1"/>
        <style>
            body {{ font-family: Arial, sans-serif; margin: 20px; background-color: #f4f4f9; color: #333; }}
            h1 {{ color: #0056b3; }}
            select {{ margin-bottom: 10px; padding: 5px; font-size: 16px; }}
            ul {{ list-style-type: none; padding: 0; }}
            li {{ margin-bottom: 5px; padding: 10px; background-color: white; border-radius: 5px; box-shadow: 0 0 5px rgba(0,0,0,0.1); }}
            a {{ text-decoration: none; color: #0056b3; word-break: break-all; }}
            a:hover {{ color: #003d82; }}
            .controls {{ display: flex; gap: 12px; align-items: center; flex-wrap: wrap; }}
            label {{ font-weight: bold; }}
        </style>
        <script>
            const files = {files_js_array};
            function applyFilter(fileList, filter) {{
                if (filter === 'all') return fileList;
                return fileList.filter(file => file.name.includes(filter));
            }}
            function renderFiles(fileList) {{
                const ul = document.getElementById('fileList');
                ul.innerHTML = '';
                fileList.forEach(file => {{
                    const li = document.createElement('li');
                    const href = encodeURIComponent(file.name).replace(/%2F/g, '/');
                    li.innerHTML = `<a href="${{href}}" target="_blank" rel="noopener noreferrer">${{file.name}}</a>`;
                    ul.appendChild(li);
                }});
            }}
            function updateDisplay() {{
                const order = document.getElementById('sortOrder').value;
                const filter = document.getElementById('filter').value;
                let sortedFiles = [...files].sort((a, b) => {{
                    return (order === 'desc') ? b.mtime - a.mtime : a.mtime - b.mtime;
                }});
                sortedFiles = applyFilter(sortedFiles, filter);
                renderFiles(sortedFiles);
            }}
            document.addEventListener('DOMContentLoaded', function() {{
                updateDisplay();
                document.getElementById('sortOrder').addEventListener('change', updateDisplay);
                document.getElementById('filter').addEventListener('change', updateDisplay);
            }});
        </script>
    </head>
    <body>
        <h1>{page_title}</h1>
        <div class="controls">
            <label for="sortOrder">Sort by:</label>
            <select id="sortOrder">
                <option value="desc">Last Updated (Recent to Old)</option>
                <option value="asc">First Updated (Old to Recent)</option>
            </select>
            <label for="filter">Filter by:</label>
            <select id="filter">
                <option value="all">All</option>
                {gpu_families_options}
            </select>
        </div>
        <ul id="fileList"></ul>
    </body>
    </html>
    """

    local_path = "index.html"
    with open(local_path, "w", encoding="utf-8") as f:
        f.write(html_content)

    message = f"index.html generated successfully for bucket '{bucket_name}'. File saved as {local_path}"
    gha_append_step_summary(message)
    print(message)

    try:
        s3_client.upload_file(local_path, bucket_name, "index.html", ExtraArgs={"ContentType": "text/html"})
        message = f"index.html successfully uploaded to bucket '{bucket_name}'."
        gha_append_step_summary(message)
        print(message)
    except ClientError as e:
        message = f"Failed to upload index.html to bucket '{bucket_name}': {e}"
        gha_append_step_summary(message)
        raise Exception(message)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate index.html for S3 bucket .tar.gz files")
    parser.add_argument("--bucket", required=True, help="S3 bucket name")
    parser.add_argument("--region", default="us-east-2", help="AWS region name")
    args = parser.parse_args()
    s3 = boto3.client("s3", region_name=args.region)
    generate_index_s3(s3_client=s3, bucket_name=args.bucket)
