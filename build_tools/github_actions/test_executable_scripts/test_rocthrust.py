import logging
import os
import shlex
import subprocess
import sys
from pathlib import Path

THEROCK_BIN_DIR = os.getenv("THEROCK_BIN_DIR")
SCRIPT_DIR = Path(__file__).resolve().parent
THEROCK_DIR = SCRIPT_DIR.parent.parent.parent
sys.path.append(str(THEROCK_DIR / "build_tools" / "github_actions"))
from github_actions_utils import *

logging.basicConfig(level=logging.INFO)

SMOKE_TESTS = "AsyncExclusiveScan*:AsyncInclusiveScan*:AsyncSort*:AsyncReduce*:AsyncTransform*:AsyncTriviallyRelocatableElements*:ConstantIteratorTests.*:CountingIteratorTests.*:DiscardIteratorTests.*:PermutationIteratorTests.*:TransformIteratorTests.*:ZipIterator*:Gather*:Replace*:ReverseIterator*:Sequence*:InnerProduct*:Merge*:MergeByKey*:Copy*:CopyN*:Count*:DeviceDelete*:Dereference*:DevicePtrTests.*:DeviceReferenceTests.*:EqualTests.*:Fill*:Find*:ForEach*:Generate*:IsPartitioned*:IsSorted*:IsSortedUntil*:Partition*:PartitionPoint*:Reduce*:ReduceByKey*:Remove*:RemoveIf*:Scan*:ScanByKey*:Scatter*:SetDifference*:SetIntersection*:SetSymmetricDifference*:Shuffle*:Sort*:StableSort*:StableSortByKey*:Tabulate*:Transform*:TransformReduce*:TransformScan*:Unique*:UninitializedCopy*:UninitializedFill*:Vector*:RandomTests.*:MemoryTests.*:AllocatorTests.*:Mr*Tests.*:VectorAllocatorTests.*:DevicePathSimpleTest:TestHipThrustCopy.DeviceToDevice:TestBijectionLength"

cmd = [
    "ctest",
    "--test-dir",
    f"{THEROCK_BIN_DIR}/rocthrust",
    "--output-on-failure",
    "--parallel",
    "8",
    "--exclude-regex",
    "^copy.hip$|scan.hip",
    "--timeout",
    "300",
    "--repeat",
    "until-pass:6",
]

# If smoke tests are enabled, we run smoke tests only.
# Otherwise, we run the normal test suite
environ_vars = os.environ.copy()
smoke_test_enabled = str2bool(os.getenv("SMOKE_TEST_ENABLED", "false"))
if smoke_test_enabled:
    environ_vars["GTEST_FILTER"] = f"'{SMOKE_TESTS}'"

logging.info(f"++ Exec [{THEROCK_DIR}]$ {shlex.join(cmd)}")

subprocess.run(cmd, cwd=THEROCK_DIR, check=True, env=environ_vars)
