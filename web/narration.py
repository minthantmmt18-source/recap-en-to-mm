"""Read and save Myanmar narration lines in a job's smart_sync_plan.json.

Same rules as the desktop Edit Text window (app/narration_editor.py). That window
uses Tkinter, which this server does not import. Saving a line changes its text,
so the next Start / Resume makes a new voice for that line only.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .bootstrap import ROOT  # noqa: F401  (puts app/ on sys.path)

from core import normalize_segments, segment_basename
from recap_planner import _foreign_letters, _latin_words, _note_text

_MIXED_DIGITS_RE = re.compile(r"[၀-၉][0-9]|[0-9][၀-၉]")


def narration_problems(text: str) -> list[str]:
    """Kept in step with NarrationEditor.narration_problems."""
    issues = []
    latin = _latin_words(text)
    if latin:
        issues.append("English words: " + ", ".join(latin[:6]))
    letters = _foreign_letters(text)
    if letters:
        issues.append("letters of another script: " + " ".join(letters))
    if _note_text(text):
        issues.append("a note about IDs instead of narration")
    if _MIXED_DIGITS_RE.search(str(text or "")):
        issues.append("mixed Myanmar/Latin digits")
    return issues


def _atomic_write(path: Path, data: Any) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _load_plan(job_dir: Path) -> tuple[Path, dict[str, Any], list[dict[str, Any]]]:
    plan_path = Path(job_dir) / "smart_sync_plan.json"
    if not plan_path.is_file():
        raise FileNotFoundError(
            "Translation is not ready yet. Wait until the transcript step finishes, then edit."
        )
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    segments = normalize_segments(plan)
    return plan_path, plan, segments


def _row_for(plan: dict[str, Any], seg: dict[str, Any]) -> dict[str, Any]:
    return plan["segments"][int(seg["source_index"])]


def _public_row(job_dir: Path, plan: dict[str, Any], seg: dict[str, Any]) -> dict[str, Any]:
    row = _row_for(plan, seg)
    wav = job_dir / "edge_tts_smart_sync" / "tts_parts" / f"{segment_basename(seg)}.wav"
    text = str(seg["narration"])
    return {
        "index": int(seg["segment_id"]),
        "source_text": str(row.get("source_text") or ""),
        "narration": text,
        "timestamp": str(row.get("timestamp") or ""),
        "video_start": seg["video_start"],
        "video_end": seg["video_end"],
        "wav_ready": wav.is_file(),
        "problems": narration_problems(text),
    }


def list_segments(job_dir: Path) -> list[dict[str, Any]]:
    plan_path, plan, segments = _load_plan(job_dir)
    del plan_path
    return [_public_row(job_dir, plan, seg) for seg in segments]


def get_segment(job_dir: Path, index: int) -> dict[str, Any]:
    _plan_path, plan, segments = _load_plan(job_dir)
    if index < 1 or index > len(segments):
        raise IndexError(f"Segment {index} is out of range (1–{len(segments)}).")
    return _public_row(job_dir, plan, segments[index - 1])


def save_segment(job_dir: Path, index: int, text: str) -> dict[str, Any]:
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    if not text:
        raise ValueError("Narration cannot be empty.")
    plan_path, plan, segments = _load_plan(job_dir)
    if index < 1 or index > len(segments):
        raise IndexError(f"Segment {index} is out of range (1–{len(segments)}).")
    seg = segments[index - 1]
    row = _row_for(plan, seg)
    row["narration"] = text
    row["translation"] = text
    _atomic_write(plan_path, plan)
    translations = job_dir / "timestamp_translations.json"
    if translations.is_file() and row.get("atomic_id") is not None:
        try:
            saved = json.loads(translations.read_text(encoding="utf-8"))
            for item in saved.get("items") or []:
                if int(item.get("id", -1)) == int(row["atomic_id"]):
                    item["translation"] = text
            _atomic_write(translations, saved)
        except (OSError, ValueError, TypeError):
            pass
    seg["narration"] = text
    return _public_row(job_dir, plan, seg)
