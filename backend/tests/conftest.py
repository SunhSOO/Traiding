"""Pytest configuration shared across unit and integration tests.

Adds the `backend/` directory to sys.path so test modules can import
project packages (`core.*`, `markets.*`, `brokers.*`, `technical.*`, etc.)
without installing the project.
"""
from __future__ import annotations

import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
