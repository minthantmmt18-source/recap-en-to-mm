"""Run one recap or one split with the same workers the desktop app uses.

planner_worker.py and render_worker.py are started as child processes. The web
server only watches their progress lines. One job at a time is enforced by the
job queue, which matters on a 2-core VPS.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable

from .bootstrap import ROOT
from .settings import build_overlays

EVENT_PREFIX = "@@VRS_EVENT@@"
LogFn = Callable[[str], None]
ProgressFn = Callable[[int, int, str, str], None]
StopFn = Callable[[], bool]


class JobContext:
    """What a runner is allowed to see. Paths stay on the server."""

    def __init__(
        self,
        *,
        job_id: str,
        source: Path,
        job_dir: Path,
        settings: dict[str, Any],
        logos_dir: Path,
        log: LogFn,
        progress: ProgressFn,
        should_stop: StopFn,
        mark_plan_ready: Callable[[], None],
    ) -> None:
        self.job_id = job_id
        self.source = source
        self.job_dir = job_dir
        self.settings = settings
        self.logos_dir = logos_dir
        self.log = log
        self.progress = progress
        self.should_stop = should_stop
        self.mark_plan_ready = mark_plan_ready


def tool_path(name: str) -> str:
    """ffmpeg / ffprobe: an explicit path, then PATH, then a ffmpeg folder beside the app."""
    explicit = os.environ.get(f"{name.upper()}_PATH", "")
    for candidate in (
        explicit,
        shutil.which(name) or "",
        str(ROOT / "ffmpeg" / name),
        str(ROOT / "ffmpeg" / "bin" / name),
        str(ROOT / "ffmpeg" / f"{name}.exe"),
        str(ROOT / "ffmpeg" / "bin" / f"{name}.exe"),
    ):
        if candidate and Path(candidate).is_file():
            return candidate
    return shutil.which(name) or name


def require_tools() -> tuple[str, str]:
    ffmpeg, ffprobe = tool_path("ffmpeg"), tool_path("ffprobe")
    missing = [label for label, path in (("ffmpeg", ffmpeg), ("ffprobe", ffprobe)) if not Path(path).is_file()]
    if missing:
        raise RuntimeError(
            "FFmpeg was not found (" + ", ".join(missing) + "). "
            "On Ubuntu install it with: sudo apt install ffmpeg   then restart the service."
        )
    return ffmpeg, ffprobe


def probe_duration(path: Path) -> float | None:
    try:
        _ffmpeg, ffprobe = require_tools()
    except RuntimeError:
        return None
    try:
        proc = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
        )
        return max(0.0, float(proc.stdout.strip()))
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None


def safe_stem(name: str) -> str:
    text = re.sub(r"[^\w\-]+", "_", Path(name).stem, flags=re.UNICODE).strip("_")
    return (text[:80] or "video")


def job_dir_for(source: Path, jobs_root: Path) -> Path:
    """Same folder name the desktop app would use for this exact file path."""
    sig = hashlib.sha1(str(source.resolve()).encode("utf-8")).hexdigest()[:8]
    return jobs_root / f"{safe_stem(source.name)}_{sig}"


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _child_env() -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    env.setdefault("WHISPER_DEVICE", "cpu")
    env.setdefault("WHISPER_COMPUTE_TYPE", "int8")
    return env


def run_child(script: str, cfg: dict[str, Any], cfg_path: Path, ctx: JobContext, stage_name: str) -> dict[str, Any]:
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    ctx.log(f"\n{'=' * 64}\n{stage_name}\n{'=' * 64}")
    cmd = [sys.executable, "-u", str(ROOT / "app" / script), str(cfg_path)]
    ctx.log("▶ " + " ".join(cmd))
    proc = subprocess.Popen(
        cmd,
        cwd=str(ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        env=_child_env(),
    )
    result: dict[str, Any] = {}
    reported = ""
    stopped = ""
    code = 1
    try:
        assert proc.stdout is not None
        for raw in proc.stdout:
            line = raw.rstrip("\r\n")
            if not line:
                continue
            if line.startswith(EVENT_PREFIX):
                try:
                    event = json.loads(line[len(EVENT_PREFIX):])
                except json.JSONDecodeError:
                    ctx.log(line)
                    continue
                kind = event.get("kind")
                if kind == "log":
                    ctx.log(str(event.get("text", "")))
                elif kind == "progress":
                    ctx.progress(
                        int(event.get("current") or 0),
                        int(event.get("total") or 0),
                        str(event.get("stage") or "work"),
                        str(event.get("label") or stage_name),
                    )
                elif kind == "result":
                    result = event
                elif kind == "error":
                    reported = str(event.get("message") or "")
                elif kind == "stopped":
                    stopped = str(event.get("message") or "Stopped")
                continue
            ctx.log(line)
        code = proc.wait()
    finally:
        if proc.poll() is None:
            proc.kill()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                pass
        if proc.stdout is not None:
            proc.stdout.close()

    if code == 10 or stopped:
        raise InterruptedError(stopped or "Stopped. Start / Resume continues from the steps that finished.")
    if code != 0:
        detail = reported or f"The worker stopped with exit code {code}."
        raise RuntimeError(detail)
    return result


def run_recap(ctx: JobContext) -> dict[str, Any]:
    ffmpeg, ffprobe = require_tools()
    source = Path(ctx.source)
    job_dir = Path(ctx.job_dir)
    job_dir.mkdir(parents=True, exist_ok=True)
    settings = ctx.settings
    stop_file = job_dir / ".stop_requested"
    if not ctx.should_stop():
        stop_file.unlink(missing_ok=True)
    overlays = build_overlays(settings, ctx.logos_dir)
    try:
        (job_dir / "editor_overlays.json").write_text(
            json.dumps(overlays, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass
    env_file = ROOT / ".env"
    ctx.log(f"Job folder: {job_dir.name}")
    ctx.log(
        f"Whisper on this server: device={os.getenv('WHISPER_DEVICE', 'cpu')} "
        f"compute={os.getenv('WHISPER_COMPUTE_TYPE', 'int8')} "
        f"other-language model={settings.get('whisper_model_other', 'small')} "
        "(English uses small.en)"
    )
    planner = {
        "source_path": str(source),
        "source_title": source.stem,
        "job_dir": str(job_dir),
        "runtime_root": str(ROOT / "recap_runtime"),
        "gemini_env_path": str(env_file) if env_file.is_file() else "",
        "ffprobe_path": ffprobe,
        "reuse": bool(settings.get("reuse", True)),
        "stop_file": str(stop_file),
        "source_language": settings.get("source_language", "auto"),
        "whisper_model_other": settings.get("whisper_model_other", "small"),
        "translation_batch_max_items": _env_int("TRANSLATION_BATCH_MAX_ITEMS", 40),
        "story_context_batch_max_items": _env_int("STORY_CONTEXT_BATCH_MAX_ITEMS", 80),
        "context_overlap": _env_int("CONTEXT_OVERLAP", 6),
        "lookahead": _env_int("LOOKAHEAD", 6),
        "translation_audit": os.getenv("TRANSLATION_AUDIT", "1").lower() not in {"0", "false", "no"},
        "atomic_max_duration": float(os.getenv("ATOMIC_MAX_DURATION", "5.6") or 5.6),
        "atomic_target_duration": float(os.getenv("ATOMIC_TARGET_DURATION", "4.2") or 4.2),
        "gemini_request_timeout_seconds": _env_int("GEMINI_REQUEST_TIMEOUT_SECONDS", 120),
        "gemini_text_model": os.getenv("GEMINI_TEXT_MODEL", "gemini-3.5-flash") or "gemini-3.5-flash",
    }
    planned = run_child("planner_worker.py", planner, job_dir / "_planner_worker.json", ctx,
                        "STEP 1 — Listen, then translate to Myanmar")
    plan_path = str(planned.get("plan_path") or (job_dir / "smart_sync_plan.json"))
    ctx.mark_plan_ready()
    ctx.log("Translation plan is ready. You can edit the Myanmar lines, then press Start / Resume.")
    if stop_file.exists() or ctx.should_stop():
        raise InterruptedError("Stopped after translation. Start / Resume makes the voice and the video.")

    render = {
        "job_dir": str(job_dir),
        "source_path": str(source),
        "plan_path": plan_path,
        "voice": settings["voice"],
        "edge_tts_rate": f"{int(settings.get('edge_rate_percent') or 0):+d}%",
        "edge_tts_pitch": "+0Hz",
        "ffmpeg_path": ffmpeg,
        "ffprobe_path": ffprobe,
        "max_video_speed": float(os.getenv("MAX_VIDEO_SPEED", "1.25") or 1.25),
        "max_video_slow": float(os.getenv("MAX_VIDEO_SLOW", "1.35") or 1.35),
        "smooth_freeze_fallback": False,
        "max_freeze_hold": 0.75,
        "mirror_video": bool(settings.get("mirror_video", True)),
        "zoom_factor": float(settings.get("zoom_factor") or 1.05),
        "render_final_video": True,
        "overlays": overlays,
        "voice_volume_percent": int(settings.get("voice_volume_percent") or 100),
        "short_pauses": bool(settings.get("short_pauses", True)),
        "output_ratio": settings.get("output_ratio") or "16:9",
        "video_quality": settings.get("video_quality") or "720",
        "frame_fill": settings.get("frame_fill") or "blur",
        "gemini_env_path": str(env_file) if env_file.is_file() else "",
        "gemini_direction": settings.get("gemini_direction") or "",
        "stop_file": str(stop_file),
    }
    rendered = run_child(
        "render_worker.py", render, job_dir / "_render_worker.json", ctx,
        "STEP 2 — Myanmar voice and Smart Sync video",
    )
    result = rendered.get("result") or {}
    final = str(result.get("final") or "")
    srt = str(result.get("srt") or "")
    if final:
        ctx.log(f"Final video: {Path(final).name}")
    return {"final": final, "srt": srt, "plan": plan_path}


def run_split(ctx: JobContext) -> dict[str, Any]:
    ffmpeg, ffprobe = require_tools()
    from splitter import split_video

    settings = dict(ctx.settings)
    # parts land in <job_dir>/<video name>_parts/
    settings["output_root"] = str(ctx.job_dir)
    stop = threading.Event()

    def watch() -> None:
        while not stop.is_set():
            if ctx.should_stop():
                stop.set()
                return
            time.sleep(0.4)

    threading.Thread(target=watch, name=f"split-stop-{ctx.job_id}", daemon=True).start()

    def on_progress(frac: float, label: str) -> None:
        ctx.progress(int(max(0.0, min(1.0, float(frac))) * 1000), 1000, "split", label)

    parts = split_video(
        ctx.source,
        settings,
        ffmpeg=ffmpeg,
        ffprobe=ffprobe,
        log=lambda msg: ctx.log(str(msg)),
        progress=on_progress,
        stop=stop,
    )
    ctx.log(f"Splitter finished: {len(parts)} file(s).")
    return {"parts": [str(p) for p in parts]}


def preview_split(source: Path, settings: dict[str, Any]) -> dict[str, Any]:
    """Where the cuts would fall, without encoding."""
    ffmpeg, ffprobe = require_tools()
    from splitter import _has_audio, audio_levels, plan_parts, probe_duration

    duration = probe_duration(ffprobe, source)
    levels = None
    smart = bool(settings.get("smart_cut", True))
    if smart and _has_audio(ffprobe, source):
        levels = audio_levels(ffmpeg, source)
    parts = plan_parts(
        duration,
        float(settings.get("minutes", 3)) * 60.0,
        smart=smart and levels is not None,
        max_extra=float(settings.get("max_extra", 20)),
        levels=levels,
    )
    return {
        "duration": duration,
        "parts": [
            {"n": p["n"], "start": p["start"], "end": p["end"], "cut": p["cut"],
             "seconds": round(p["end"] - p["start"], 2)}
            for p in parts
        ],
    }


def extract_frame(source: Path, dest: Path, seconds: float) -> None:
    ffmpeg, _ffprobe = require_tools()
    dest.parent.mkdir(parents=True, exist_ok=True)
    seconds = max(0.0, float(seconds))
    proc = subprocess.run(
        [ffmpeg, "-y", "-ss", f"{seconds:.2f}", "-i", str(source), "-frames:v", "1",
         "-vf", "scale=960:-2", str(dest)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
    )
    if proc.returncode != 0 or not dest.is_file():
        raise RuntimeError("Could not read a preview frame from this video.")
