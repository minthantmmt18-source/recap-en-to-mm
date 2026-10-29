"""Browser UI: login, one-at-a-time jobs, narration edits, and a real ffmpeg split.

Gemini is not called. A recap job with no API key must stop with a clear error
before Whisper downloads a model.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from web.jobs import JobError, JobManager
from web.server import create_app

PASSWORD = "secret-password"
ROOT = Path(__file__).resolve().parents[1]


def _note_job(box: list, tmp: str, job_id: str) -> None:
    """Remember a recap folder under the repo so the test can delete it later."""
    try:
        state = json.loads(Path(tmp, "state.json").read_text(encoding="utf-8"))
        folder = Path(state["jobs"][job_id]["job_dir"]).resolve()
    except (OSError, json.JSONDecodeError, KeyError):
        return
    if ROOT.resolve() in folder.parents:
        box.append(folder)


def _wait(client: TestClient, job_id: str, timeout: float = 30) -> dict:
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        response = client.get(f"/api/jobs/{job_id}")
        response.raise_for_status()
        last = response.json()
        if last["status"] in {"done", "error", "stopped"}:
            return last
        time.sleep(0.05)
    raise AssertionError(f"job did not finish: {last}")


def _login(client: TestClient, password: str = PASSWORD) -> None:
    response = client.post("/login", data={"password": password}, follow_redirects=False)
    if response.status_code not in {302, 303}:
        raise AssertionError(f"login failed: {response.status_code} {response.text[:300]}")


def _app(tmp: str, runners=None) -> TestClient:
    application = create_app(
        password=PASSWORD, data_dir=Path(tmp), runners=runners, secure_cookie=False,
    )
    client = TestClient(application)
    _login(client)
    return client


def _upload(client: TestClient, name: str = "clip.mp4", payload: bytes | None = None) -> dict:
    payload = payload if payload is not None else (b"\x00" * 2048)
    response = client.post("/api/uploads", files={"file": (name, payload, "video/mp4")})
    if response.status_code != 200:
        raise AssertionError(response.text)
    return response.json()


class LoginTests(unittest.TestCase):
    def test_password_wall(self):
        with tempfile.TemporaryDirectory() as tmp:
            application = create_app(password=PASSWORD, data_dir=Path(tmp), secure_cookie=False)
            client = TestClient(application)
            self.assertEqual(client.get("/healthz").status_code, 200)
            self.assertIn("password_configured", client.get("/healthz").json())
            home = client.get("/")
            self.assertIn("စကားဝှက်", home.text)
            self.assertEqual(client.get("/api/options").status_code, 401)
            bad = client.post("/login", data={"password": "nope"}, follow_redirects=False)
            self.assertEqual(bad.status_code, 401)
            self.assertIn("မမှန်", bad.text)
            _login(client)
            page = client.get("/")
            self.assertEqual(page.status_code, 200)
            self.assertIn("Recap စမယ်", page.text)
            options = client.get("/api/options")
            self.assertEqual(options.status_code, 200)
            self.assertIn("my-MM-ThihaNeural", [item["id"] for item in options.json()["voices"]])
            self.assertEqual(client.post("/logout", follow_redirects=False).status_code, 303)
            self.assertEqual(client.get("/api/jobs").status_code, 401)

    def test_lockout_after_repeated_guesses(self):
        with tempfile.TemporaryDirectory() as tmp:
            application = create_app(password=PASSWORD, data_dir=Path(tmp), secure_cookie=False)
            client = TestClient(application)
            for _ in range(8):
                self.assertEqual(
                    client.post("/login", data={"password": "wrong"}, follow_redirects=False).status_code,
                    401,
                )
            blocked = client.post("/login", data={"password": PASSWORD}, follow_redirects=False)
            self.assertEqual(blocked.status_code, 429)


class JobTests(unittest.TestCase):
    def _runners(self):
        def recap(ctx):
            ctx.log("planner hello")
            ctx.progress(1, 1, "planner", "translated")
            plan = {
                "segments": [
                    {
                        "narration": "မင်္ဂလာပါ",
                        "translation": "မင်္ဂလာပါ",
                        "source_text": "Hello there",
                        "video_start": 0.0,
                        "video_end": 2.0,
                        "timestamp": "00:00-00:02",
                        "atomic_id": 1,
                    },
                    {
                        "narration": "Hello again",
                        "translation": "Hello again",
                        "source_text": "Hi",
                        "video_start": 2.0,
                        "video_end": 4.0,
                        "timestamp": "00:02-00:04",
                        "atomic_id": 2,
                    },
                ]
            }
            (ctx.job_dir / "smart_sync_plan.json").write_text(
                json.dumps(plan, ensure_ascii=False), encoding="utf-8")
            (ctx.job_dir / "timestamp_translations.json").write_text(json.dumps({
                "items": [
                    {"id": 1, "translation": "မင်္ဂလာပါ"},
                    {"id": 2, "translation": "Hello again"},
                ]
            }), encoding="utf-8")
            ctx.mark_plan_ready()
            final = ctx.job_dir / "edge_tts_smart_sync" / "final_edge_tts_smart_sync.mp4"
            final.parent.mkdir(parents=True, exist_ok=True)
            final.write_bytes(b"f" * 2000)
            (ctx.job_dir / "whisper_transcript.txt").write_text("Hello there\n", encoding="utf-8")
            ctx.log("render done")
            return {"final": str(final)}

        return {"recap": recap, "split": recap}

    def test_upload_job_narration_and_download(self):
        made: list[Path] = []
        self.addCleanup(lambda: [shutil.rmtree(path, ignore_errors=True) for path in made])
        with tempfile.TemporaryDirectory() as tmp:
            client = _app(tmp, self._runners())
            bad = client.post("/api/uploads", files={"file": ("notes.txt", b"hello world!!", "text/plain")})
            self.assertEqual(bad.status_code, 400)
            uploaded = _upload(client)
            rejected = client.post("/api/jobs/recap", json={
                "upload_id": uploaded["id"],
                "settings": {"zoom_factor": 4},
            })
            self.assertEqual(rejected.status_code, 400)
            started = client.post("/api/jobs/recap", json={
                "upload_id": uploaded["id"],
                "settings": {"source_language": "en", "video_quality": "480", "mirror_video": False},
            })
            self.assertEqual(started.status_code, 200, started.text)
            _note_job(made, tmp, started.json()["id"])
            job = _wait(client, started.json()["id"])
            self.assertEqual(job["status"], "done")
            self.assertTrue(job["plan_ready"])
            kinds = {item["kind"] for item in job["downloads"]}
            self.assertIn("video", kinds)
            self.assertIn("transcript", kinds)
            video = client.get(f"/api/jobs/{job['id']}/download/video")
            self.assertEqual(video.status_code, 200)
            self.assertGreater(len(video.content), 1000)
            listing = client.get(f"/api/jobs/{job['id']}/narration")
            self.assertEqual(listing.status_code, 200)
            self.assertEqual(listing.json()["total"], 2)
            self.assertTrue(any(item["problems"] for item in listing.json()["items"]))
            saved = client.put(
                f"/api/jobs/{job['id']}/narration/2",
                json={"text": "နောက်တစ်ကြောင်း ပြောတယ်"},
            )
            self.assertEqual(saved.status_code, 200, saved.text)
            self.assertEqual(saved.json()["problems"], [])
            state = json.loads(Path(tmp, "state.json").read_text(encoding="utf-8"))
            folder = Path(state["jobs"][job["id"]]["job_dir"])
            plan_data = json.loads((folder / "smart_sync_plan.json").read_text(encoding="utf-8"))
            self.assertEqual(plan_data["segments"][1]["narration"], "နောက်တစ်ကြောင်း ပြောတယ်")
            translations = json.loads((folder / "timestamp_translations.json").read_text(encoding="utf-8"))
            self.assertEqual(translations["items"][1]["translation"], "နောက်တစ်ကြောင်း ပြောတယ်")
            empty = client.put(f"/api/jobs/{job['id']}/narration/2", json={"text": "   "})
            self.assertEqual(empty.status_code, 400)
            missing = client.get(f"/api/jobs/{job['id']}/download/part/not-a-part.mp4")
            self.assertIn(missing.status_code, {400, 404})

    def test_one_job_at_a_time_and_stop(self):
        release = threading.Event()

        def blocker(ctx):
            ctx.log("started")
            while not release.is_set():
                if ctx.should_stop():
                    raise InterruptedError("stopped by user")
                time.sleep(0.05)
            ctx.log("released")
            return {}

        made: list[Path] = []
        self.addCleanup(lambda: [shutil.rmtree(path, ignore_errors=True) for path in made])
        with tempfile.TemporaryDirectory() as tmp:
            client = _app(tmp, {"recap": blocker, "split": blocker})
            first_upload = _upload(client, "one.mp4")
            second_upload = _upload(client, "two.mp4")
            first = client.post("/api/jobs/recap", json={"upload_id": first_upload["id"], "settings": {}}).json()
            _note_job(made, tmp, first["id"])
            deadline = time.time() + 5
            while time.time() < deadline:
                if client.get(f"/api/jobs/{first['id']}").json()["status"] == "running":
                    break
                time.sleep(0.05)
            else:
                self.fail("first job did not start")
            second = client.post("/api/jobs/recap", json={"upload_id": second_upload["id"], "settings": {}}).json()
            _note_job(made, tmp, second["id"])
            self.assertEqual(second["status"], "queued")
            self.assertGreaterEqual(second["queue_ahead"], 1)
            stopped = client.post(f"/api/jobs/{first['id']}/stop")
            self.assertEqual(stopped.status_code, 200)
            first_done = _wait(client, first["id"])
            self.assertEqual(first_done["status"], "stopped")
            release.set()
            second_done = _wait(client, second["id"])
            self.assertEqual(second_done["status"], "done")


class RealToolTests(unittest.TestCase):
    def test_missing_gemini_key_is_reported(self):
        env_file = ROOT / ".env"
        if env_file.is_file() and "GEMINI" in env_file.read_text(encoding="utf-8"):
            text = env_file.read_text(encoding="utf-8")
            if any(line.split("=", 1)[-1].strip().strip('"') for line in text.splitlines()
                   if line.startswith("GEMINI") and "KEY" in line and not line.startswith("#")):
                self.skipTest("a Gemini key is configured; not calling the API from this test")
        # Other tests may leave a dummy key in this process. The job child inherits it.
        # A key in the repo .env is a real one, so this test stays away from the API.
        removed = {
            key: os.environ.pop(key) for key in list(os.environ)
            if key.startswith("GEMINI") and key.endswith("_KEY")
        }
        self.addCleanup(lambda: os.environ.update(removed))
        made: list[Path] = []
        self.addCleanup(lambda: [shutil.rmtree(path, ignore_errors=True) for path in made])
        with tempfile.TemporaryDirectory() as tmp:
            client = _app(tmp, runners=None)
            sample = Path(tmp) / "tiny.mp4"
            if shutil.which("ffmpeg") is None:
                self.skipTest("ffmpeg is not installed")
            subprocess.run(
                ["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=size=160x90:rate=8:duration=1",
                 "-f", "lavfi", "-i", "sine=frequency=440:duration=1", "-shortest",
                 "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(sample)],
                check=True, capture_output=True,
            )
            uploaded = _upload(client, sample.name, sample.read_bytes())
            started = client.post("/api/jobs/recap", json={
                "upload_id": uploaded["id"], "settings": {"source_language": "en"},
            })
            self.assertEqual(started.status_code, 200, started.text)
            job_id = started.json()["id"]
            _note_job(made, tmp, job_id)
            job = _wait(client, job_id, timeout=60)
            self.assertEqual(job["status"], "error")
            log = client.get(f"/api/jobs/{job['id']}/log?after=0&limit=500").json()
            blob = job["message"] + "\n" + "\n".join(log["lines"])
            self.assertIn("Gemini", blob)

    def test_splitter_on_a_short_video(self):
        if shutil.which("ffmpeg") is None:
            self.skipTest("ffmpeg is not installed")
        with tempfile.TemporaryDirectory() as tmp:
            web = Path(tmp) / "web"
            source = Path(tmp) / "sample.mp4"
            subprocess.run(
                ["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=size=320x180:rate=12:duration=8",
                 "-f", "lavfi", "-i", "sine=frequency=440:duration=8", "-shortest",
                 "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(source)],
                check=True, capture_output=True,
            )
            client = _app(str(web), runners=None)
            uploaded = _upload(client, source.name, source.read_bytes())
            plan = client.post(f"/api/uploads/{uploaded['id']}/split-plan", json={
                "settings": {"minutes": 0.1, "smart_cut": False, "label_on": False, "text": ""},
            })
            self.assertEqual(plan.status_code, 200, plan.text)
            self.assertGreaterEqual(len(plan.json()["parts"]), 2)
            frame = client.get(f"/api/uploads/{uploaded['id']}/frame.jpg?t=0.2")
            self.assertEqual(frame.status_code, 200, frame.text)
            self.assertTrue(frame.content.startswith(b"\xff\xd8"))
            started = client.post("/api/jobs/split", json={
                "upload_id": uploaded["id"],
                "settings": {
                    "minutes": 0.1, "smart_cut": False, "label_on": False, "text": "",
                    "ratio": "Original", "quality": "480",
                },
            })
            self.assertEqual(started.status_code, 200, started.text)
            job = _wait(client, started.json()["id"], timeout=90)
            self.assertEqual(job["status"], "done", job.get("message"))
            parts = job["parts"]
            self.assertGreaterEqual(len(parts), 2)
            downloaded = client.get(
                f"/api/jobs/{job['id']}/download/part/{parts[0]['name']}"
            )
            self.assertEqual(downloaded.status_code, 200)
            self.assertGreater(len(downloaded.content), 1000)


class GuardTests(unittest.TestCase):
    def test_download_rejects_a_path_trick(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager = JobManager(Path(tmp), runners={"recap": lambda ctx: {}, "split": lambda ctx: {}})
            upload_id, dest = manager.begin_upload("clip.mp4")
            dest.write_bytes(b"\x00" * 2048)
            manager.finish_upload(upload_id, 2048)
            job = manager.enqueue("split", upload_id, {"minutes": 1, "label_on": False, "text": ""})
            with self.assertRaises(JobError):
                manager.download(job["id"], "part", "../secret.mp4")
            manager.stop(job["id"])
            deadline = time.time() + 3
            while time.time() < deadline:
                if manager.job_status(job["id"]) in {"stopped", "done", "error"}:
                    break
                time.sleep(0.05)
            time.sleep(0.15)


if __name__ == "__main__":
    unittest.main()
