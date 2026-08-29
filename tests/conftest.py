"""pytest config: make projects/refill importable as top-level packages."""

import sys
from pathlib import Path

REFILL_ROOT = Path(__file__).resolve().parent.parent
if str(REFILL_ROOT) not in sys.path:
    sys.path.insert(0, str(REFILL_ROOT))
