"""Paths and process defaults shared by the web package.

Importing this does not import Tkinter. The desktop app never imports this module,
so its GPU behaviour stays as it was.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))


def apply_whisper_defaults() -> None:
    """CPU / int8 defaults for a 2-core VPS with no GPU and about 8 GB of RAM.

    Values already set in the environment (for example by systemd) are kept.
    English audio still uses the small.en model inside the planner. Other languages
    default to the multilingual ``small`` model, which fits in 8 GB; ``medium`` is
    more accurate and heavier. Leave these unset on a Windows PC that should keep
    using an NVIDIA GPU — the desktop app does not call this function.
    """
    os.environ.setdefault("WHISPER_DEVICE", "cpu")
    os.environ.setdefault("WHISPER_COMPUTE_TYPE", "int8")
    os.environ.setdefault("WHISPER_MODEL_OTHER", "small")
