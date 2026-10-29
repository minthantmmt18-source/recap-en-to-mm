"""One background queue for recap and splitter jobs.

The queue is saved on disk. A restart does not continue a job by itself: the
finished steps stay in the job folder, and Start / Resume runs the rest.
"""
from __future__ import annotations

import json
import secrets
import shutil
import threading
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .bootstrap import ROOT
from .engine import JobContext, job_dir_for, probe_duration
from .settings import default_settings

Runner = Callable[[JobContext], dict[str, Any]]
VIDEO_EXTS = {".mp4", ".mkv", ".mov", ".webm", ".avi"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}
def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _safe_filename(name: str, fallback: str) -> str:
    base = Path(name or "").name
    cleaned = "".join(ch if ch.isalnum() or ch in "._- ()" or "\u1000" <= ch <= "\u109f" else "_" for ch in base)
    cleaned = cleaned.strip("._ ")[:120]
    return cleaned or fallback


def _under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


class JobError(Exception):
    """A request the user can fix (bad id, busy delete, missing file)."""


class JobManager:
    def __init__(self, data_dir: Path, runners: dict[str, Runner] | None = None) -> None:
        from .engine import run_recap, run_split

        self.data_dir = Path(data_dir)
        self.uploads = self.data_dir / "uploads"
        self.logos = self.data_dir / "logos"
        self.logs = self.data_dir / "logs"
        self.splits = self.data_dir / "splits"
        self.frames = self.data_dir / "frames"
        self.jobs_root = ROOT / "jobs"
        for folder in (self.data_dir, self.uploads, self.logos, self.logs, self.splits, self.frames, self.jobs_root):
            folder.mkdir(parents=True, exist_ok=True)
        self.state_path = self.data_dir / "state.json"
        self.runners = runners or {"recap": run_recap, "split": run_split}
        self._lock = threading.Lock()
        self._worker_running = False
        self._last_save = 0.0
        self.state = self._load()
        self._recover_after_restart()

    # ---------------------------------------------------------------- state
    def _load(self) -> dict[str, Any]:
        if self.state_path.is_file():
            try:
                data = json.loads(self.state_path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    data.setdefault("settings", {})
                    data.setdefault("uploads", {})
                    data.setdefault("jobs", {})
                    data.setdefault("queue", [])
                    return data
            except (OSError, json.JSONDecodeError):
                pass
        return {"settings": {}, "uploads": {}, "jobs": {}, "queue": []}

    def _save(self) -> None:
        """Caller holds self._lock."""
        tmp = self.state_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self.state, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.state_path)
        self._last_save = time.time()

    def _recover_after_restart(self) -> None:
        changed = False
        note = "The server restarted. Finished steps were kept. Press Start / Resume to continue."
        for job in self.state["jobs"].values():
            if job.get("status") in {"queued", "running"}:
                job["status"] = "stopped"
                job["stop"] = True
                job["message"] = note
                job["updated"] = _now()
                changed = True
        if self.state["queue"]:
            self.state["queue"] = []
            changed = True
        if changed:
            with self._lock:
                self._save()

    # -------------------------------------------------------------- settings
    def get_settings(self) -> dict[str, Any]:
        merged = default_settings()
        with self._lock:
            saved = self.state.get("settings") or {}
        if isinstance(saved, dict):
            for key, value in saved.items():
                if key in merged and key != "subtitles":
                    merged[key] = value
            if isinstance(saved.get("subtitles"), dict):
                merged["subtitles"] = {**merged["subtitles"], **saved["subtitles"]}
        return merged

    def save_settings(self, settings: dict[str, Any]) -> None:
        clean = {key: value for key, value in settings.items() if not str(key).startswith("_")}
        with self._lock:
            self.state["settings"] = clean
            self._save()

    # --------------------------------------------------------------- uploads
    def begin_upload(self, filename: str) -> tuple[str, Path]:
        ext = Path(filename).suffix.lower()
        if ext not in VIDEO_EXTS:
            raise JobError("Upload an mp4, mkv, mov, webm, or avi video.")
        upload_id = secrets.token_hex(8)
        folder = self.uploads / upload_id
        folder.mkdir(parents=True, exist_ok=True)
        dest = folder / _safe_filename(filename, "video" + ext)
        with self._lock:
            self.state["uploads"][upload_id] = {
                "id": upload_id,
                "name": dest.name,
                "path": str(dest),
                "size": 0,
                "duration": None,
                "created": _now(),
                "ready": False,
            }
            self._save()
        return upload_id, dest

    def finish_upload(self, upload_id: str, size: int) -> dict[str, Any]:
        with self._lock:
            meta = self.state["uploads"].get(upload_id)
            if not meta:
                raise JobError("Upload not found.")
            path = Path(meta["path"])
        duration = probe_duration(path)
        with self._lock:
            meta = self.state["uploads"][upload_id]
            meta["size"] = int(size)
            meta["duration"] = duration
            meta["ready"] = True
            self._save()
            return self._public_upload(meta)

    def drop_upload(self, upload_id: str) -> None:
        with self._lock:
            meta = self.state["uploads"].pop(upload_id, None)
            self._save()
        if meta:
            shutil.rmtree(self.uploads / upload_id, ignore_errors=True)

    def list_uploads(self) -> list[dict[str, Any]]:
        with self._lock:
            items = [self._public_upload(meta) for meta in self.state["uploads"].values() if meta.get("ready")]
        return sorted(items, key=lambda item: item["created"], reverse=True)

    def _public_upload(self, meta: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": meta["id"],
            "name": meta["name"],
            "size": meta.get("size") or 0,
            "duration": meta.get("duration"),
            "created": meta.get("created"),
        }

    def upload_path(self, upload_id: str) -> Path:
        with self._lock:
            meta = self.state["uploads"].get(upload_id)
        if not meta or not meta.get("ready"):
            raise JobError("Upload that video first.")
        path = Path(meta["path"])
        if not path.is_file() or not _under(path, self.uploads):
            raise JobError("That upload file is missing. Upload it again.")
        return path

    def begin_logo(self, filename: str) -> tuple[str, Path]:
        ext = Path(filename).suffix.lower()
        if ext not in IMAGE_EXTS:
            raise JobError("Logo must be a png, jpg, or webp image.")
        logo_id = secrets.token_hex(8)
        folder = self.logos / logo_id
        folder.mkdir(parents=True, exist_ok=True)
        dest = folder / _safe_filename(filename, "logo" + ext)
        return logo_id, dest

    def finish_logo(self, logo_id: str, filename: str, size: int) -> dict[str, Any]:
        return {"id": logo_id, "name": Path(filename).name, "size": size}

    # ------------------------------------------------------------------ jobs
    def enqueue(self, kind: str, upload_id: str, settings: dict[str, Any]) -> dict[str, Any]:
        if kind not in {"recap", "split"}:
            raise JobError("Unknown job type.")
        source = self.upload_path(upload_id)
        with self._lock:
            meta = self.state["uploads"][upload_id]
            waiting = [job for job in self.state["jobs"].values() if job.get("status") in {"queued", "running"}]
            if len(waiting) >= 8:
                raise JobError("The queue already has 8 jobs. Wait for one to finish.")
            job_id = secrets.token_hex(8)
            if kind == "recap":
                # Same path as a previous run of this upload, so Resume keeps the transcript.
                job_dir = job_dir_for(source, self.jobs_root)
            else:
                job_dir = self.splits / job_id
            job_dir.mkdir(parents=True, exist_ok=True)
            job = {
                "id": job_id,
                "kind": kind,
                "status": "queued",
                "stage": "queued",
                "progress": 0,
                "progress_label": "Waiting for the current job to finish" if any(
                    item.get("status") == "running" for item in waiting) else "Starting",
                "message": "",
                "created": _now(),
                "updated": _now(),
                "upload_id": upload_id,
                "source_name": meta.get("name") or source.name,
                "job_dir": str(job_dir),
                "settings": settings,
                "stop": False,
                "result": {},
            }
            self.state["jobs"][job_id] = job
            self.state["queue"].append(job_id)
            if kind == "recap":
                self.state["settings"] = {
                    key: value for key, value in settings.items() if not str(key).startswith("_")
                }
            self._save()
            self._ensure_worker()
            return self._public_locked(job)

    def _ensure_worker(self) -> None:
        """Caller holds self._lock."""
        if self._worker_running:
            return
        self._worker_running = True
        threading.Thread(target=self._loop, name="recap-jobs", daemon=True).start()

    def _loop(self) -> None:
        try:
            while True:
                with self._lock:
                    if not self.state["queue"]:
                        return
                    job_id = self.state["queue"][0]
                    job = self.state["jobs"].get(job_id)
                    if not job or job.get("status") not in {"queued", "running"}:
                        self.state["queue"].pop(0)
                        self._save()
                        continue
                    job["status"] = "running"
                    job["stage"] = "starting"
                    job["stop"] = False
                    job["updated"] = _now()
                    self._save()
                    snapshot = json.loads(json.dumps(job))
                self._execute(snapshot)
                with self._lock:
                    if self.state["queue"] and self.state["queue"][0] == job_id:
                        self.state["queue"].pop(0)
                        self._save()
        finally:
            with self._lock:
                self._worker_running = False
                if self.state["queue"]:
                    self._ensure_worker()

    def _execute(self, job: dict[str, Any]) -> None:
        job_id = job["id"]
        self._log(job_id, f"Started {job['kind']} for {job.get('source_name')}")
        ctx = self._context(job)
        try:
            result = self.runners[job["kind"]](ctx) or {}
            if ctx.should_stop():
                self._finish(job_id, "stopped", "Stopped. Start / Resume keeps the finished steps.", result)
            else:
                self._finish(job_id, "done", "Finished.", result)
                self._log(job_id, "Finished.")
        except InterruptedError as exc:
            self._log(job_id, str(exc))
            self._finish(job_id, "stopped", str(exc))
        except Exception as exc:
            self._log(job_id, traceback.format_exc())
            self._finish(job_id, "error", str(exc))

    def _context(self, job: dict[str, Any]) -> JobContext:
        job_id = job["id"]
        source = self.upload_path(job["upload_id"])

        def log(text: str) -> None:
            self._log(job_id, text)

        def progress(current: int, total: int, stage: str, label: str) -> None:
            total = max(1, int(total))
            frac = max(0.0, min(1.0, int(current) / total))
            if stage == "planner":
                overall = 0.04 + 0.40 * frac
            elif stage == "render":
                overall = 0.45 + 0.55 * frac
            else:
                overall = frac
            self._touch(job_id, stage=stage, progress=round(overall, 4), progress_label=str(label)[:180])

        def should_stop() -> bool:
            with self._lock:
                current = self.state["jobs"].get(job_id)
                return bool(current and current.get("stop"))

        def mark_plan_ready() -> None:
            self._touch(job_id, plan_ready=True, progress_label="Translation ready")

        return JobContext(
            job_id=job_id,
            source=source,
            job_dir=Path(job["job_dir"]),
            settings=job.get("settings") or {},
            logos_dir=self.logos,
            log=log,
            progress=progress,
            should_stop=should_stop,
            mark_plan_ready=mark_plan_ready,
        )

    def _log(self, job_id: str, text: str) -> None:
        path = self.logs / f"{job_id}.log"
        with self._lock:
            with path.open("a", encoding="utf-8") as handle:
                handle.write(str(text).rstrip() + "\n")
            job = self.state["jobs"].get(job_id)
            if job:
                job["updated"] = _now()

    def _touch(self, job_id: str, **fields: Any) -> None:
        with self._lock:
            job = self.state["jobs"].get(job_id)
            if not job or job.get("status") not in {"queued", "running"}:
                return
            job.update(fields)
            job["updated"] = _now()
            if time.time() - self._last_save > 1.0:
                self._save()

    def _finish(self, job_id: str, status: str, message: str, result: dict[str, Any] | None = None) -> None:
        with self._lock:
            job = self.state["jobs"].get(job_id)
            if not job:
                return
            # A queue cancel that already marked the job stopped wins over a late "done".
            if job.get("status") == "stopped" and status == "done":
                status = "stopped"
            job["status"] = status
            job["message"] = message[:2000]
            job["updated"] = _now()
            if status == "done":
                job["progress"] = 1
                job["progress_label"] = "Finished"
            if result:
                stored: dict[str, Any] = {}
                for key, value in result.items():
                    if isinstance(value, (str, int, float)):
                        stored[key] = value
                    elif isinstance(value, list):
                        stored[key] = [str(item) for item in value]
                job["result"] = stored
            self._save()

    def stop(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            job = self.state["jobs"].get(job_id)
            if not job:
                raise JobError("Job not found.")
            if job["status"] == "queued":
                job["status"] = "stopped"
                job["stop"] = True
                job["message"] = "Removed from the queue before it started."
                job["updated"] = _now()
                self.state["queue"] = [item for item in self.state["queue"] if item != job_id]
                self._save()
                return self._public_locked(job)
            if job["status"] != "running":
                return self._public_locked(job)
            job["stop"] = True
            job["message"] = "Stopping at the next safe point. Finished steps are kept."
            job["updated"] = _now()
            try:
                (Path(job["job_dir"]) / ".stop_requested").write_text("stop", encoding="utf-8")
            except OSError:
                pass
            self._save()
            return self._public_locked(job)

    def delete(self, job_id: str, *, files: bool) -> None:
        with self._lock:
            job = self.state["jobs"].get(job_id)
            if not job:
                raise JobError("Job not found.")
            if job.get("status") in {"queued", "running"}:
                raise JobError("Stop the job before deleting it.")
            self.state["jobs"].pop(job_id, None)
            self.state["queue"] = [item for item in self.state["queue"] if item != job_id]
            self._save()
            job_dir = Path(job["job_dir"])
            kind = job.get("kind")
        (self.logs / f"{job_id}.log").unlink(missing_ok=True)
        if files and kind == "split" and _under(job_dir, self.splits):
            shutil.rmtree(job_dir, ignore_errors=True)
        if files and kind == "recap" and _under(job_dir, self.jobs_root):
            shutil.rmtree(job_dir, ignore_errors=True)

    def get(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            job = self.state["jobs"].get(job_id)
            if not job:
                raise JobError("Job not found.")
            return self._public_locked(job)

    def list_jobs(self) -> list[dict[str, Any]]:
        with self._lock:
            items = [self._public_locked(job) for job in self.state["jobs"].values()]
        return sorted(items, key=lambda item: item["created"], reverse=True)

    def job_dir(self, job_id: str) -> Path:
        with self._lock:
            job = self.state["jobs"].get(job_id)
            if not job:
                raise JobError("Job not found.")
            return Path(job["job_dir"])

    def assert_editable(self, job_id: str) -> Path:
        """Narration edits wait until nothing is rendering this job folder."""
        with self._lock:
            job = self.state["jobs"].get(job_id)
            if not job:
                raise JobError("Job not found.")
            folder = str(job["job_dir"])
            for other in self.state["jobs"].values():
                if other.get("status") == "running" and str(other.get("job_dir")) == folder:
                    raise JobError(
                        "A job is still rendering this video. Stop it or wait, then edit and press Start / Resume."
                    )
            return Path(folder)

    def job_status(self, job_id: str) -> str:
        with self._lock:
            job = self.state["jobs"].get(job_id)
            if not job:
                raise JobError("Job not found.")
            return str(job.get("status") or "")

    def read_log(self, job_id: str, after: int, limit: int = 300) -> dict[str, Any]:
        self.get(job_id)
        path = self.logs / f"{job_id}.log"
        if not path.is_file():
            return {"lines": [], "next": 0, "total": 0}
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        total = len(lines)
        limit = max(1, min(1000, int(limit)))
        if after < 0:
            start = max(0, total - limit)
        else:
            start = min(max(0, after), total)
        chunk = lines[start:start + limit]
        return {"lines": chunk, "next": start + len(chunk), "total": total}

    def download(self, job_id: str, kind: str, name: str = "") -> Path:
        with self._lock:
            job = self.state["jobs"].get(job_id)
            if not job:
                raise JobError("Job not found.")
            job_dir = Path(job["job_dir"]).resolve()
        path = self._download_file(job, job_dir, kind, name)
        if path is None or not path.is_file():
            raise JobError("That file is not ready yet.")
        path = path.resolve()
        allowed = (job_dir, self.logs.resolve(), self.jobs_root.resolve(), self.splits.resolve())
        if not any(_under(path, root) for root in allowed):
            raise JobError("That file is not part of this job.")
        return path

    def _download_file(self, job: dict[str, Any], job_dir: Path, kind: str, name: str) -> Path | None:
        if kind == "video":
            return self._video_file(job, job_dir)
        if kind == "srt":
            return self._srt_file(job_dir)
        if kind == "transcript":
            return job_dir / "whisper_transcript.txt"
        if kind == "plan":
            return job_dir / "smart_sync_plan.json"
        if kind == "translations":
            return job_dir / "timestamp_translations.json"
        if kind == "log":
            return self.logs / f"{job['id']}.log"
        if kind == "part":
            safe = Path(name).name
            if not safe or safe != name or not safe.lower().endswith(".mp4") or safe.startswith("."):
                raise JobError("Unknown part file.")
            matches = [
                item for item in job_dir.rglob(safe)
                if item.is_file() and ".overlays" not in item.parts and not item.name.endswith(".partial.mp4")
            ]
            return matches[0] if matches else None
        raise JobError("Unknown download.")

    def _video_file(self, job: dict[str, Any], job_dir: Path) -> Path | None:
        final = job_dir / "edge_tts_smart_sync" / "final_edge_tts_smart_sync.mp4"
        if final.is_file() and final.stat().st_size > 1000:
            return final
        raw = str((job.get("result") or {}).get("final") or "")
        if raw and Path(raw).is_file():
            return Path(raw)
        return None

    def _srt_file(self, job_dir: Path) -> Path | None:
        preferred = sorted((job_dir / "edge_tts_smart_sync" / "SRT_for_upload").glob("*.srt"))
        if preferred:
            return preferred[0]
        found = [item for item in job_dir.rglob("*.srt") if "subtitle_png" not in item.parts]
        return found[0] if found else None

    def _public_locked(self, job: dict[str, Any]) -> dict[str, Any]:
        """Caller holds self._lock."""
        job_dir = Path(job["job_dir"])
        video = self._video_file(job, job_dir)
        srt = self._srt_file(job_dir)
        downloads: list[dict[str, Any]] = []

        def add(kind: str, path: Path | None, label: str) -> None:
            if path is not None and path.is_file() and path.stat().st_size > 0:
                downloads.append({"kind": kind, "name": label or path.name, "size": path.stat().st_size})

        add("video", video, video.name if video else "")
        add("srt", srt, srt.name if srt else "")
        add("transcript", job_dir / "whisper_transcript.txt", "whisper_transcript.txt")
        add("plan", job_dir / "smart_sync_plan.json", "smart_sync_plan.json")
        add("translations", job_dir / "timestamp_translations.json", "timestamp_translations.json")
        add("log", self.logs / f"{job['id']}.log", "run.log")
        parts: list[dict[str, Any]] = []
        if job.get("kind") == "split" and job_dir.is_dir():
            for item in sorted(job_dir.rglob("*.mp4")):
                if ".overlays" in item.parts or item.name.endswith(".partial.mp4") or item.name.startswith("."):
                    continue
                parts.append({"name": item.name, "size": item.stat().st_size})
                downloads.append({"kind": "part", "name": item.name, "size": item.stat().st_size})
        ahead = 0
        for queued_id in self.state["queue"]:
            if queued_id == job["id"]:
                break
            ahead += 1
        if job.get("status") == "running":
            ahead = 0
        return {
            "id": job["id"],
            "kind": job["kind"],
            "status": job.get("status"),
            "stage": job.get("stage") or "",
            "progress": job.get("progress") or 0,
            "progress_label": job.get("progress_label") or "",
            "message": job.get("message") or "",
            "created": job.get("created"),
            "updated": job.get("updated"),
            "upload_id": job.get("upload_id"),
            "source_name": job.get("source_name"),
            "plan_ready": (job_dir / "smart_sync_plan.json").is_file(),
            "downloads": downloads,
            "parts": parts,
            "queue_ahead": ahead,
        }
