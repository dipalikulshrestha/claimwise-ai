"""
Root conftest.py — adds the backend directory to sys.path so that
`app.*` imports resolve correctly during pytest runs without requiring
the package to be pip-installed.
"""

import sys
from pathlib import Path

# Insert backend/ at the front of sys.path
sys.path.insert(0, str(Path(__file__).parent))
