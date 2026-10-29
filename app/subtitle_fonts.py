"""Installed fonts that can draw Myanmar script, and helpers to hand them to libass."""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

DEFAULT_FONT = "Myanmar Text"
ROOT = Path(__file__).resolve().parent
CACHE_FILE = ROOT.parent / "logs" / "subtitle_fonts.json"
# Linux packages (fonts-noto-core, fonts-sil-padauk) keep Myanmar faces in subfolders.
# Only filenames that look like a Myanmar font are opened there; Windows Fonts stays a flat scan.
_NAME_HINTS = ("myanmar", "padauk", "pyidaungsu", "burma", "mmrtext")


def _font_dirs() -> list[Path]:
    home = Path.home()
    dirs = [
        Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "Windows" / "Fonts",
        Path("/usr/share/fonts"),
        Path("/usr/local/share/fonts"),
        home / ".local" / "share" / "fonts",
        home / ".fonts",
    ]
    extra = os.environ.get("RECAP_FONT_DIR")
    if extra:
        dirs.append(Path(extra))
    return dirs


def _font_files(directory: Path):
    """Windows Fonts is one flat folder. Everywhere else, Myanmar faces sit in subfolders."""
    if not directory.is_dir():
        return
    windows_flat = directory.name.casefold() == "fonts" and "windows" in str(directory).casefold()
    try:
        found = directory.iterdir() if windows_flat else directory.rglob("*")
        paths = sorted(p for p in found if p.is_file() and p.suffix.lower() in {".ttf", ".otf", ".ttc"})
    except OSError:
        return
    for path in paths:
        if not windows_flat and not any(hint in path.name.casefold() for hint in _NAME_HINTS):
            continue
        yield path


def _scan() -> dict[str, list[str]]:
    from fontTools.ttLib import TTCollection, TTFont

    families: dict[str, list[str]] = {}
    for directory in _font_dirs():
        for path in _font_files(directory):
            collection = None
            try:
                if path.suffix.lower() == ".ttc":
                    collection = TTCollection(str(path))
                    opened = collection.fonts
                else:
                    opened = [TTFont(str(path), lazy=True)]
                for font in opened:
                    try:
                        cmap = font.getBestCmap() or {}
                        # KA + vowel sign E: a real Myanmar (Unicode) font, not a Latin fallback.
                        if 0x1000 in cmap and 0x1031 in cmap:
                            family = str(font["name"].getDebugName(1) or "").strip()
                            if family:
                                families.setdefault(family, [])
                                if str(path) not in families[family]:
                                    families[family].append(str(path))
                    finally:
                        if collection is None:
                            font.close()
            except Exception:
                continue
            finally:
                if collection is not None:
                    collection.close()
    return families


def _signature() -> list:
    sig = []
    for directory in _font_dirs():
        try:
            sig.append([str(directory), directory.stat().st_mtime_ns])
        except OSError:
            sig.append([str(directory), 0])
    return sig


def myanmar_fonts(refresh: bool = False) -> dict[str, list[str]]:
    """{family name: [font file paths]} for every installed Myanmar-capable font (cached)."""
    signature = _signature()
    if not refresh and CACHE_FILE.is_file():
        try:
            saved = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
            if saved.get("signature") == signature:
                return saved["families"]
        except (OSError, ValueError, KeyError):
            pass
    families = _scan()
    families.setdefault(DEFAULT_FONT, families.get(DEFAULT_FONT, []))
    try:
        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        CACHE_FILE.write_text(json.dumps({"signature": signature, "families": families},
                                         ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError:
        pass
    return families


def ass_filter(ass: Path, families: list[str]) -> str:
    """`ass=` filter text that also finds the chosen fonts.

    The font files are copied beside the ASS file into `fonts/` and passed as a relative
    `fontsdir`, so user-installed fonts work and no Windows path needs filter escaping.
    FFmpeg must run with the ASS file's folder as its working directory (it already does).
    """
    wanted = [f for f in dict.fromkeys(families) if f and f != DEFAULT_FONT]
    if not wanted:
        return f"ass={ass.name}"
    known = myanmar_fonts()
    target = ass.parent / "fonts"
    copied = False
    for family in wanted:
        for source in known.get(family, []):
            src = Path(source)
            if not src.is_file():
                continue
            target.mkdir(exist_ok=True)
            dst = target / src.name
            if not dst.is_file() or dst.stat().st_size != src.stat().st_size:
                shutil.copy2(src, dst)
            copied = True
    return f"ass={ass.name}:fontsdir=fonts" if copied else f"ass={ass.name}"
