import os
import sys
from pathlib import Path

# Automatically enable test mode to isolate OS Keyring and filesystem in test runs
os.environ["ACS_TEST_MODE"] = "1"

# Ensure src is in sys.path when running tests directly
src_dir = str(Path(__file__).resolve().parent.parent / "src")
if src_dir not in sys.path:
    sys.path.insert(0, src_dir)
