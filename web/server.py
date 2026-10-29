"""Password-protected web UI.

Run from the repo root:

    python -m web

Or, the way systemd starts it:

    python -m uvicorn web.server:app --host 127.0.0.1 --port 8765
"""
from __future__ import annotations

import hashlib
import hmac
import os
import sys
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from .bootstrap import ROOT, apply_whisper_defaults
from .engine import extract_frame, preview_split
from .jobs import JobError, JobManager
from .narration import get_segment, list_segments, save_segment
from .settings import logo_file, normalize_recap, normalize_split, options_payload

STATIC = Path(__file__).resolve().parent / "static"
VIDEO_LIMIT_DEFAULT_MB = 8192
LOGO_LIMIT_BYTES = 20 * 1024 * 1024


class SecurityHeaders:
    """Add a few response headers without Starlette's BaseHTTPMiddleware.

    BaseHTTPMiddleware breaks large uploads and file downloads, so this is a
    small ASGI wrapper instead.
    """

    _EXTRA = (
        (b"x-content-type-options", b"nosniff"),
        (b"x-frame-options", b"DENY"),
        (b"referrer-policy", b"same-origin"),
        (b"x-robots-tag", b"noindex, nofollow"),
    )

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers") or [])
                have = {key.lower() for key, _value in headers}
                for key, value in self._EXTRA:
                    if key not in have:
                        headers.append((key, value))
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_with_headers)


class LoginGuard:
    """Slow down password guessing from the open internet."""

    def __init__(self) -> None:
        self._fails: list[float] = []

    def blocked(self) -> bool:
        import time
        now = time.time()
        self._fails = [stamp for stamp in self._fails if now - stamp < 600]
        return len(self._fails) >= 8

    def fail(self) -> None:
        import time
        self._fails.append(time.time())

    def reset(self) -> None:
        self._fails.clear()


def _password_ok(expected: str, given: str) -> bool:
    if not expected or not given:
        return False
    return hmac.compare_digest(
        hashlib.sha256(given.encode("utf-8")).digest(),
        hashlib.sha256(expected.encode("utf-8")).digest(),
    )


def _page(name: str, **repl: str) -> str:
    text = (STATIC / name).read_text(encoding="utf-8")
    for key, value in repl.items():
        text = text.replace("{{" + key + "}}", value)
    return text


def _require(request: Request) -> None:
    if request.session.get("auth") is True and request.app.state.password:
        return
    raise HTTPException(status_code=401, detail="Login required")


def _jobs(request: Request) -> JobManager:
    return request.app.state.jobs


def _job_error(exc: JobError) -> HTTPException:
    text = str(exc)
    code = 409 if text.startswith("A job is still") or text.startswith("Stop the job") or "queue" in text.lower() else 400
    if "not found" in text.lower() or "missing" in text.lower():
        code = 404
    return HTTPException(status_code=code, detail=text)


def create_app(
    *,
    password: str | None = None,
    data_dir: Path | None = None,
    runners: dict | None = None,
    secure_cookie: bool | None = None,
    max_upload_mb: int | None = None,
) -> FastAPI:
    """Build the site. Tests pass their own password, data folder, and runners."""
    app = FastAPI(title="Recap Myanmar", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.password = os.getenv("WEB_PASSWORD", "") if password is None else password
    folder = Path(data_dir) if data_dir is not None else Path(os.getenv("WEB_DATA_DIR", str(ROOT / "web_data")))
    app.state.jobs = JobManager(folder, runners=runners)
    if max_upload_mb is None:
        try:
            max_upload_mb = int(os.getenv("WEB_MAX_UPLOAD_MB", str(VIDEO_LIMIT_DEFAULT_MB)))
        except ValueError:
            max_upload_mb = VIDEO_LIMIT_DEFAULT_MB
    app.state.max_upload_bytes = max(1, int(max_upload_mb)) * 1024 * 1024
    app.state.guard = LoginGuard()
    if secure_cookie is None:
        secure_cookie = os.getenv("WEB_COOKIE_SECURE", "").lower() in {"1", "true", "yes", "on"}
    secret = os.getenv("WEB_SECRET", "").strip()
    if not secret:
        secret = hashlib.sha256(b"recap-web-v1\0" + str(app.state.password).encode("utf-8")).hexdigest()
    app.add_middleware(
        SessionMiddleware,
        secret_key=secret,
        session_cookie="recap_session",
        max_age=14 * 24 * 3600,
        same_site="lax",
        https_only=bool(secure_cookie),
    )
    # Added last so it runs first and still sees the session cookie on the way out.
    app.add_middleware(SecurityHeaders)

    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/healthz")
    def healthz() -> dict:
        return {"ok": True, "password_configured": bool(app.state.password)}

    @app.get("/", response_class=HTMLResponse)
    def home(request: Request) -> HTMLResponse:
        if request.session.get("auth") is True and app.state.password:
            return HTMLResponse(_page("index.html"))
        warning = ""
        if not app.state.password:
            warning = (
                "WEB_PASSWORD is not set. Add WEB_PASSWORD=… to the .env file and restart. "
                "စကားဝှက် မသတ်မှတ်ရသေးပါ။ .env ထဲမှာ WEB_PASSWORD= ထည့်ပြီး service ကို restart လုပ်ပါ။"
            )
        return HTMLResponse(_page("login.html", WARNING=warning, ERROR=""))

    @app.post("/login")
    async def login(request: Request) -> HTMLResponse:
        form = await request.form()
        given = str(form.get("password") or "")
        guard: LoginGuard = request.app.state.guard
        if guard.blocked():
            page = _page("login.html", WARNING="", ERROR="Too many tries. Wait 10 minutes and try again. / ၁၀ မိနစ် စောင့်ပါ။")
            return HTMLResponse(page, status_code=429)
        if not _password_ok(request.app.state.password, given):
            guard.fail()
            page = _page("login.html", WARNING="", ERROR="Wrong password. / စကားဝှက် မမှန်ပါ။")
            return HTMLResponse(page, status_code=401)
        guard.reset()
        request.session["auth"] = True
        return RedirectResponse("/", status_code=303)

    @app.post("/logout")
    def logout(request: Request) -> RedirectResponse:
        request.session.clear()
        return RedirectResponse("/", status_code=303)

    @app.get("/api/options")
    def options(request: Request) -> dict:
        _require(request)
        return options_payload(_jobs(request).get_settings())

    @app.get("/api/settings")
    def read_settings(request: Request) -> dict:
        _require(request)
        return _jobs(request).get_settings()

    @app.put("/api/settings")
    async def write_settings(request: Request) -> dict:
        _require(request)
        body = await request.json()
        try:
            settings = normalize_recap(body if isinstance(body, dict) else {})
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        _jobs(request).save_settings(settings)
        return settings

    @app.get("/api/uploads")
    def uploads(request: Request) -> dict:
        _require(request)
        return {"uploads": _jobs(request).list_uploads()}

    @app.post("/api/uploads")
    async def upload_video(request: Request, file: UploadFile = File(...)) -> dict:
        _require(request)
        manager = _jobs(request)
        try:
            upload_id, dest = manager.begin_upload(file.filename or "video.mp4")
        except JobError as exc:
            raise _job_error(exc) from exc
        size = 0
        try:
            with dest.open("wb") as handle:
                while True:
                    chunk = await file.read(1024 * 1024)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > request.app.state.max_upload_bytes:
                        raise HTTPException(
                            413,
                            f"This video is larger than {request.app.state.max_upload_bytes // (1024 * 1024)} MB.",
                        )
                    handle.write(chunk)
            if size < 1024:
                raise HTTPException(400, "That file looks empty.")
            return manager.finish_upload(upload_id, size)
        except Exception:
            manager.drop_upload(upload_id)
            raise
        finally:
            await file.close()

    @app.get("/api/uploads/{upload_id}/frame.jpg")
    def frame(upload_id: str, request: Request, t: float = Query(1.0, ge=0, le=86400)) -> FileResponse:
        _require(request)
        manager = _jobs(request)
        try:
            source = manager.upload_path(upload_id)
        except JobError as exc:
            raise _job_error(exc) from exc
        dest = manager.frames / f"{upload_id}_{int(t * 2)}.jpg"
        if not dest.is_file():
            try:
                extract_frame(source, dest, t)
            except RuntimeError as exc:
                raise HTTPException(400, str(exc)) from exc
        return FileResponse(dest, media_type="image/jpeg")

    @app.post("/api/uploads/{upload_id}/split-plan")
    async def split_plan(upload_id: str, request: Request) -> dict:
        _require(request)
        manager = _jobs(request)
        try:
            source = manager.upload_path(upload_id)
            body = await request.json()
            settings = normalize_split(body.get("settings") if isinstance(body, dict) else {})
        except JobError as exc:
            raise _job_error(exc) from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        try:
            return preview_split(source, settings)
        except RuntimeError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/api/logos")
    async def upload_logo(request: Request, file: UploadFile = File(...)) -> dict:
        _require(request)
        manager = _jobs(request)
        try:
            logo_id, dest = manager.begin_logo(file.filename or "logo.png")
        except JobError as exc:
            raise _job_error(exc) from exc
        size = 0
        try:
            with dest.open("wb") as handle:
                while True:
                    chunk = await file.read(256 * 1024)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > LOGO_LIMIT_BYTES:
                        raise HTTPException(413, "Logo images must be 20 MB or smaller.")
                    handle.write(chunk)
            if size < 32:
                raise HTTPException(400, "That image looks empty.")
            return manager.finish_logo(logo_id, dest.name, size)
        except Exception:
            import shutil
            shutil.rmtree(dest.parent, ignore_errors=True)
            raise
        finally:
            await file.close()

    @app.get("/api/logos/{logo_id}")
    def read_logo(logo_id: str, request: Request) -> FileResponse:
        _require(request)
        path = logo_file(_jobs(request).logos, logo_id)
        if path is None:
            raise HTTPException(404, "Logo not found.")
        return FileResponse(path)

    @app.get("/api/jobs")
    def jobs(request: Request) -> dict:
        _require(request)
        return {"jobs": _jobs(request).list_jobs()}

    @app.post("/api/jobs/recap")
    async def start_recap(request: Request) -> dict:
        _require(request)
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(400, "Expected a JSON object.")
        try:
            settings = normalize_recap(body.get("settings") or {})
            return _jobs(request).enqueue("recap", str(body.get("upload_id") or ""), settings)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except JobError as exc:
            raise _job_error(exc) from exc

    @app.post("/api/jobs/split")
    async def start_split(request: Request) -> dict:
        _require(request)
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(400, "Expected a JSON object.")
        try:
            settings = normalize_split(body.get("settings") or {})
            return _jobs(request).enqueue("split", str(body.get("upload_id") or ""), settings)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except JobError as exc:
            raise _job_error(exc) from exc

    @app.get("/api/jobs/{job_id}")
    def job_detail(job_id: str, request: Request) -> dict:
        _require(request)
        try:
            return _jobs(request).get(job_id)
        except JobError as exc:
            raise _job_error(exc) from exc

    @app.get("/api/jobs/{job_id}/log")
    def job_log(job_id: str, request: Request, after: int = 0, limit: int = 300) -> dict:
        _require(request)
        try:
            return _jobs(request).read_log(job_id, after, limit)
        except JobError as exc:
            raise _job_error(exc) from exc

    @app.post("/api/jobs/{job_id}/stop")
    def stop_job(job_id: str, request: Request) -> dict:
        _require(request)
        try:
            return _jobs(request).stop(job_id)
        except JobError as exc:
            raise _job_error(exc) from exc

    @app.delete("/api/jobs/{job_id}")
    def delete_job(job_id: str, request: Request, files: int = 0) -> dict:
        _require(request)
        try:
            _jobs(request).delete(job_id, files=bool(files))
        except JobError as exc:
            raise _job_error(exc) from exc
        return {"ok": True}

    @app.get("/api/jobs/{job_id}/download/{kind}")
    def download(job_id: str, kind: str, request: Request) -> FileResponse:
        _require(request)
        return _send(request, job_id, kind, "")

    @app.get("/api/jobs/{job_id}/download/part/{name}")
    def download_part(job_id: str, name: str, request: Request) -> FileResponse:
        _require(request)
        return _send(request, job_id, "part", name)

    @app.get("/api/jobs/{job_id}/narration")
    def narration_list(job_id: str, request: Request) -> dict:
        _require(request)
        try:
            folder = _jobs(request).job_dir(job_id)
            rows = list_segments(folder)
        except JobError as exc:
            raise _job_error(exc) from exc
        except FileNotFoundError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {
            "total": len(rows),
            "items": [
                {
                    "index": row["index"],
                    "preview": row["narration"][:80],
                    "problems": bool(row["problems"]),
                    "video_start": row["video_start"],
                }
                for row in rows
            ],
        }

    @app.get("/api/jobs/{job_id}/narration/{index}")
    def narration_one(job_id: str, index: int, request: Request) -> dict:
        _require(request)
        try:
            return get_segment(_jobs(request).job_dir(job_id), index)
        except JobError as exc:
            raise _job_error(exc) from exc
        except FileNotFoundError as exc:
            raise HTTPException(404, str(exc)) from exc
        except IndexError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.put("/api/jobs/{job_id}/narration/{index}")
    async def narration_save(job_id: str, index: int, request: Request) -> dict:
        _require(request)
        body = await request.json()
        text = body.get("text") if isinstance(body, dict) else ""
        manager = _jobs(request)
        try:
            folder = manager.assert_editable(job_id)
            return save_segment(folder, index, str(text or ""))
        except JobError as exc:
            raise _job_error(exc) from exc
        except FileNotFoundError as exc:
            raise HTTPException(404, str(exc)) from exc
        except IndexError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    return app


def _send(request: Request, job_id: str, kind: str, name: str) -> FileResponse:
    try:
        path = _jobs(request).download(job_id, kind, name)
    except JobError as exc:
        raise _job_error(exc) from exc
    media = "application/octet-stream"
    if path.suffix.lower() == ".mp4":
        media = "video/mp4"
    elif path.suffix.lower() == ".srt":
        media = "application/x-subrip"
    elif path.suffix.lower() == ".json":
        media = "application/json"
    elif path.suffix.lower() in {".txt", ".log"}:
        media = "text/plain; charset=utf-8"
    return FileResponse(path, media_type=media, filename=path.name)


def _build_default_app() -> FastAPI:
    """Used by uvicorn. CPU defaults are applied in the worker child and by systemd.

    Importing this module must not force WHISPER_DEVICE for a desktop test run
    that happens to load the web package.
    """
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env", override=False)
    except Exception:
        pass
    return create_app()


app = _build_default_app()


def main() -> None:
    import uvicorn

    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env", override=False)
    except Exception:
        pass
    apply_whisper_defaults()
    if not os.getenv("WEB_PASSWORD"):
        sys.stderr.write(
            "WEB_PASSWORD is empty.\n"
            "Copy .env.example to .env and set WEB_PASSWORD=your-password, then start again.\n"
            "WEB_PASSWORD မထည့်ရသေးပါ။ .env.example ကို .env လို့ ကူးပြီး "
            "WEB_PASSWORD=စကားဝှက် ထည့်ပါ။\n"
        )
        raise SystemExit(1)
    host = os.getenv("WEB_HOST", "127.0.0.1")
    try:
        port = int(os.getenv("WEB_PORT", "8765"))
    except ValueError:
        port = 8765
    uvicorn.run(
        "web.server:app",
        host=host,
        port=port,
        proxy_headers=True,
        forwarded_allow_ips=os.getenv("WEB_FORWARDED_ALLOW_IPS", "127.0.0.1"),
        timeout_keep_alive=75,
    )


if __name__ == "__main__":
    main()
