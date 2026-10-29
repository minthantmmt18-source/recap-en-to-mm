"""Validate the options the browser sends before they reach the workers."""
from __future__ import annotations

import os
import re
from pathlib import Path

from .bootstrap import ROOT  # noqa: F401  (puts app/ on sys.path)

from core import FRAME_FILLS, OUTPUT_RATIOS, QUALITY_LABELS, video_quality
from gemini_tts import VOICES as GEMINI_VOICE_PAIRS
from gemini_tts import GEMINI_VOICES
from recap_planner import SOURCE_LANGUAGES
from subtitle_png import DEFAULTS as SUBTITLE_DEFAULTS
from subtitle_png import style as subtitle_style

EDGE_VOICES = ("my-MM-ThihaNeural", "my-MM-NilarNeural")
NARRATOR_VOICES = EDGE_VOICES + GEMINI_VOICES
OTHER_WHISPER_MODELS = ("tiny", "base", "small", "medium")
_SAFE_ID = re.compile(r"^[0-9a-f]{8,32}$")

FILL_LABELS = (
    ("blur", "Blur background · နောက်ခံ ဝါး"),
    ("black", "Black bars · အမည်း ကွက်လပ်"),
    ("crop", "Crop to fill · ပြည့်အောင် ဖြတ်"),
)


def _int(value: object, default: int, low: int, high: int) -> int:
    try:
        number = int(round(float(value)))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        number = default
    return max(low, min(high, number))


def _unit(value: object, default: float, low: float = 0.1, high: float = 1.0) -> float:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        number = default
    return max(low, min(high, number))


def _pair(value: object, default: list[float]) -> list[float]:
    if not isinstance(value, (list, tuple)) or len(value) < 2:
        return [float(default[0]), float(default[1])]
    try:
        return [max(0.0, min(1.0, float(value[0]))), max(0.0, min(1.0, float(value[1])))]
    except (TypeError, ValueError):
        return [float(default[0]), float(default[1])]


def _boxes(value: object) -> list[dict[str, float]]:
    if not isinstance(value, list):
        return []
    boxes: list[dict[str, float]] = []
    for item in value[:12]:
        if not isinstance(item, dict):
            continue
        try:
            boxes.append({
                "x": max(0.0, min(1.0, float(item.get("x", 0)))),
                "y": max(0.0, min(1.0, float(item.get("y", 0)))),
                "w": max(0.02, min(1.0, float(item.get("w", 0.2)))),
                "h": max(0.02, min(1.0, float(item.get("h", 0.1)))),
            })
        except (TypeError, ValueError):
            continue
    return boxes


def installed_myanmar_fonts() -> list[str]:
    try:
        from subtitle_fonts import myanmar_fonts
        return sorted(myanmar_fonts())
    except Exception:
        return []


def default_subtitle_font() -> str:
    fonts = installed_myanmar_fonts()
    if "Noto Sans Myanmar" in fonts:
        return "Noto Sans Myanmar"
    if "Myanmar Text" in fonts:
        return "Myanmar Text"
    return fonts[0] if fonts else "Myanmar Text"


def default_settings() -> dict:
    subtitles = dict(SUBTITLE_DEFAULTS)
    subtitles["font"] = default_subtitle_font()
    return {
        "source_language": "auto",
        "voice": EDGE_VOICES[0],
        "gemini_direction": "",
        "edge_rate_percent": 0,
        "short_pauses": True,
        "voice_volume_percent": 100,
        "zoom_factor": 1.05,
        "mirror_video": True,
        "output_ratio": "16:9",
        "frame_fill": "blur",
        "video_quality": "720",
        "reuse": True,
        "whisper_model_other": os.getenv("WHISPER_MODEL_OTHER", "small") or "small",
        "title_text": "",
        "title_font_size": 54,
        "title_position": [0.5, 0.12],
        "extra_text": "",
        "extra_text_font_size": 42,
        "extra_text_opacity": 0.65,
        "extra_text_position": [0.5, 0.84],
        "logo_id": "",
        "logo_enabled": False,
        "logo_width_fraction": 0.16,
        "logo_opacity": 0.65,
        "logo_position": [0.80, 0.06],
        "blur_boxes": [],
        "subtitles": subtitles,
    }


def normalize_recap(raw: object) -> dict:
    """Return safe recap settings. Raises ValueError when a value cannot be used."""
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ValueError("Settings must be an object.")
    settings = default_settings()
    language = str(raw.get("source_language") or "auto").strip().lower()
    if language != "auto" and language not in SOURCE_LANGUAGES:
        raise ValueError(f"Unknown source language: {language}")
    settings["source_language"] = language

    voice = str(raw.get("voice") or settings["voice"]).strip()
    if voice not in NARRATOR_VOICES:
        raise ValueError("Choose an Edge or Gemini narrator voice.")
    settings["voice"] = voice
    settings["gemini_direction"] = str(raw.get("gemini_direction") or "").strip()[:400]
    settings["edge_rate_percent"] = _int(raw.get("edge_rate_percent", 0), 0, -50, 100)
    settings["short_pauses"] = bool(raw.get("short_pauses", True))
    settings["voice_volume_percent"] = _int(raw.get("voice_volume_percent", 100), 100, 50, 300)
    try:
        zoom = float(raw.get("zoom_factor", 1.05))
    except (TypeError, ValueError):
        zoom = 1.05
    if not 1.0 <= zoom <= 1.25:
        raise ValueError("Zoom must be between 1.00 and 1.25.")
    settings["zoom_factor"] = zoom
    settings["mirror_video"] = bool(raw.get("mirror_video", True))

    ratio = str(raw.get("output_ratio") or "16:9")
    settings["output_ratio"] = ratio if ratio in OUTPUT_RATIOS else "16:9"
    fill = str(raw.get("frame_fill") or "blur")
    settings["frame_fill"] = fill if fill in FRAME_FILLS else "blur"
    settings["video_quality"] = video_quality(raw.get("video_quality") or "720")
    settings["reuse"] = bool(raw.get("reuse", True))

    other = str(raw.get("whisper_model_other") or settings["whisper_model_other"]).strip().lower()
    if other not in OTHER_WHISPER_MODELS:
        raise ValueError("Whisper model must be tiny, base, small, or medium.")
    settings["whisper_model_other"] = other

    settings["title_text"] = str(raw.get("title_text") or "").strip()[:200]
    settings["title_font_size"] = _int(raw.get("title_font_size"), 54, 12, 144)
    settings["title_position"] = _pair(raw.get("title_position"), [0.5, 0.12])
    settings["extra_text"] = str(raw.get("extra_text") or "").strip()[:400]
    settings["extra_text_font_size"] = _int(raw.get("extra_text_font_size"), 42, 12, 120)
    settings["extra_text_opacity"] = _unit(raw.get("extra_text_opacity"), 0.65)
    settings["extra_text_position"] = _pair(raw.get("extra_text_position"), [0.5, 0.84])

    logo_id = str(raw.get("logo_id") or "").strip()
    settings["logo_enabled"] = bool(raw.get("logo_enabled", False)) and bool(logo_id)
    if logo_id and not _SAFE_ID.fullmatch(logo_id):
        raise ValueError("Unknown logo.")
    settings["logo_id"] = logo_id if settings["logo_enabled"] else ""
    settings["logo_width_fraction"] = _unit(raw.get("logo_width_fraction"), 0.16, 0.04, 0.40)
    settings["logo_opacity"] = _unit(raw.get("logo_opacity"), 0.65)
    settings["logo_position"] = _pair(raw.get("logo_position"), [0.80, 0.06])
    settings["blur_boxes"] = _boxes(raw.get("blur_boxes"))
    try:
        settings["subtitles"] = subtitle_style(raw.get("subtitles") or settings["subtitles"])
    except ValueError as exc:
        raise ValueError(str(exc)) from exc
    return settings


def normalize_split(raw: object) -> dict:
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ValueError("Settings must be an object.")
    try:
        minutes = float(raw.get("minutes", 3))
    except (TypeError, ValueError):
        minutes = 3.0
    try:
        max_extra = float(raw.get("max_extra", 20))
    except (TypeError, ValueError):
        max_extra = 20.0
    ratio = str(raw.get("ratio") or "Original")
    fill = str(raw.get("fill") or "blur")
    return {
        "minutes": max(0.1, min(60.0, minutes)),
        "smart_cut": bool(raw.get("smart_cut", True)),
        "max_extra": max(0.0, min(120.0, max_extra)),
        "ratio": ratio if ratio in OUTPUT_RATIOS else "Original",
        "fill": fill if fill in FRAME_FILLS else "blur",
        "quality": video_quality(raw.get("quality") or "720"),
        "text": str(raw.get("text") or "").strip()[:300],
        "text_size": _int(raw.get("text_size"), 42, 10, 144),
        "text_pos": _pair(raw.get("text_pos"), [0.5, 0.86]),
        "text_opacity": _unit(raw.get("text_opacity"), 1.0),
        "text_box": bool(raw.get("text_box", True)),
        "label_on": bool(raw.get("label_on", True)),
        "label_template": (str(raw.get("label_template") or "Part {n}/{total}").strip() or "Part {n}")[:80],
        "label_size": _int(raw.get("label_size"), 48, 10, 144),
        "label_pos": _pair(raw.get("label_pos"), [0.5, 0.10]),
        "label_opacity": _unit(raw.get("label_opacity"), 1.0),
        "label_box": bool(raw.get("label_box", True)),
    }


def logo_file(logos_dir: Path, logo_id: str) -> Path | None:
    if not logo_id or not _SAFE_ID.fullmatch(logo_id):
        return None
    folder = logos_dir / logo_id
    if not folder.is_dir():
        return None
    files = sorted(p for p in folder.iterdir() if p.is_file())
    return files[0] if files else None


def build_overlays(settings: dict, logos_dir: Path) -> dict:
    """The overlay dict the render worker already understands."""
    logo = logo_file(logos_dir, str(settings.get("logo_id") or ""))
    logo_on = bool(settings.get("logo_enabled") and logo is not None)
    return {
        "subtitles": settings.get("subtitles") or {},
        "blur_boxes": settings.get("blur_boxes") or [],
        "blur_strength": _int(os.getenv("OVERLAY_BLUR_STRENGTH"), 18, 4, 40),
        "title_text": settings.get("title_text") or "",
        "title_position": settings.get("title_position") or [0.5, 0.12],
        "title_font_size": int(settings.get("title_font_size") or 54),
        "title_font_path": os.getenv("RECAP_MM_FONT", ""),
        "logo_enabled": logo_on,
        "logo_path": str(logo) if logo_on and logo is not None else "",
        "logo_position": settings.get("logo_position") or [0.80, 0.06],
        "logo_width_fraction": float(settings.get("logo_width_fraction") or 0.16),
        "logo_opacity": float(settings.get("logo_opacity") or 0.65),
        "extra_text": settings.get("extra_text") or "",
        "extra_text_position": settings.get("extra_text_position") or [0.5, 0.84],
        "extra_text_font_size": int(settings.get("extra_text_font_size") or 42),
        "extra_text_opacity": float(settings.get("extra_text_opacity") or 0.65),
    }


def options_payload(saved: dict) -> dict:
    languages = [{"id": "auto", "label": "Auto-detect · အလိုအလျောက်"}]
    languages += [{"id": code, "label": name} for code, name in SOURCE_LANGUAGES.items()]
    voices = [
        {"id": "my-MM-ThihaNeural", "label": "Thiha · ယောက်ျားသံ (Edge)"},
        {"id": "my-MM-NilarNeural", "label": "Nilar · မိန်းမသံ (Edge)"},
    ]
    gender_mm = {"male": "ယောက်ျားသံ", "female": "မိန်းမသံ"}
    for name, gender in GEMINI_VOICE_PAIRS:
        voices.append({"id": f"gemini:{name}", "label": f"{name} · {gender_mm.get(gender, gender)} (Gemini)"})
    try:
        max_upload = int(os.getenv("WEB_MAX_UPLOAD_MB", "8192"))
    except ValueError:
        max_upload = 8192
    return {
        "languages": languages,
        "voices": voices,
        "ratios": list(OUTPUT_RATIOS),
        "fills": [{"id": key, "label": label} for key, label in FILL_LABELS],
        "qualities": [{"id": key, "label": label} for key, label in QUALITY_LABELS.items()],
        "whisper_models": list(OTHER_WHISPER_MODELS),
        "fonts": installed_myanmar_fonts(),
        "settings": saved,
        "whisper": {
            "device": os.getenv("WHISPER_DEVICE", "cpu"),
            "compute_type": os.getenv("WHISPER_COMPUTE_TYPE", "int8"),
            "model_other_default": os.getenv("WHISPER_MODEL_OTHER", "small"),
        },
        "max_upload_mb": max(1, max_upload),
    }
