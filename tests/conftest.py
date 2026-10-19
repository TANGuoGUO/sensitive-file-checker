"""Make vendored runtime dependencies available to tests and spawned workers."""
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
for directory in (PROJECT_ROOT, PROJECT_ROOT / "vendor"):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))
