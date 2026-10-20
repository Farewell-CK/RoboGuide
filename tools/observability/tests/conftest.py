"""Load the standalone configuration renderer without adding an application dependency."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
