import logging
import os
import shlex
import subprocess
import sys
from pathlib import Path

THEROCK_BIN_DIR = os.getenv("THEROCK_BIN_DIR")
OUTPUT_ARTIFACTS_DIR = os.getenv("OUTPUT_ARTIFACTS_DIR")
SCRIPT_DIR = Path(__file__).resolve().parent
THEROCK_DIR = SCRIPT_DIR.parent.parent.parent
sys.path.append(str(THEROCK_DIR / "build_tools" / "github_actions"))
from github_actions_utils import *

logging.basicConfig(level=logging.INFO)

# GTest sharding
SHARD_INDEX = os.getenv("SHARD_INDEX", 1)
TOTAL_SHARDS = os.getenv("TOTAL_SHARDS", 1)
envion_vars = os.environ.copy()
# For display purposes in the GitHub Action UI, the shard array is 1th indexed. However for shard indexes, we convert it to 0th index.
envion_vars["GTEST_SHARD_INDEX"] = str(int(SHARD_INDEX) - 1)
envion_vars["GTEST_TOTAL_SHARDS"] = str(TOTAL_SHARDS)

# If smoke tests are enabled, we run smoke tests only.
# Otherwise, we run the normal test suite
smoke_test_enabled = str2bool(os.getenv("SMOKE_TEST_ENABLED", "false"))
if smoke_test_enabled:
    test_filter = f"--yaml {THEROCK_BIN_DIR}/rocsparse_smoke.yaml"
else:
    test_filter = "--gtest_filter=*quick*"

cmd = [
    f"{THEROCK_BIN_DIR}/rocsparse-test",
    test_filter,
    "--matrices-dir",
    f"{OUTPUT_ARTIFACTS_DIR}/clients/matrices/",
]
logging.info(f"++ Exec [{THEROCK_DIR}]$ {shlex.join(cmd)}")
subprocess.run(cmd, cwd=THEROCK_DIR, check=True, env=envion_vars)
