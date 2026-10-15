import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import main, worker
from app.media import probe_media


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg gerekli")
class AudioUploadRenderTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name)
        self.storage_dir = self.directory / "storage"
        self.records = {}
        self.project = {"id": "test-project", "token": "test-token", "assets": []}
        self.client = TestClient(main.app)
        main.app.dependency_overrides[main.project_auth] = lambda: self.project
        self.clip_path = self.directory / "clip.mp4"
        subprocess.run(
            [
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-f", "lavfi", "-i", "color=c=green:s=160x90:r=12:d=0.7",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-an", str(self.clip_path),
            ],
            check=True,
        )

    def tearDown(self):
        main.app.dependency_overrides.clear()
        self.temporary.cleanup()

    def _copy_into_storage(self, source, key):
        destination = self.storage_dir / key
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)

    def _copy_from_storage(self, key, destination):
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(self.storage_dir / key, destination)

    def test_audio_upload_is_attached_to_render_and_encoded(self):
        formats = (
            ("tone.mp3", "audio/mpeg", ["-c:a", "libmp3lame"]),
            ("tone.wav", "audio/wav", ["-c:a", "pcm_s16le"]),
            ("tone.m4a", "audio/mp4", ["-c:a", "aac"]),
        )
        with (
            patch.object(main, "_write", side_effect=lambda key, value: self.records.__setitem__(key, value)),
            patch.object(main.storage, "put_file", side_effect=self._copy_into_storage),
            patch.object(main.queue, "enqueue"),
            patch.object(worker.storage, "download", side_effect=self._copy_from_storage),
            patch.object(worker.storage, "put_file", side_effect=self._copy_into_storage),
        ):
            for filename, content_type, codec in formats:
                with self.subTest(format=filename):
                    self.project = {"id": f"project-{filename}", "token": "test-token", "assets": []}
                    clip_response = self.client.post(
                        f"/api/projects/{self.project['id']}/assets?kind=clip",
                        files={"file": ("clip.mp4", self.clip_path.read_bytes(), "video/mp4")},
                    )
                    self.assertEqual(clip_response.status_code, 200, clip_response.text)
                    clip = clip_response.json()

                    audio_path = self.directory / filename
                    subprocess.run(
                        [
                            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                            "-f", "lavfi", "-i", "sine=frequency=540:duration=0.25",
                            *codec, str(audio_path),
                        ],
                        check=True,
                    )
                    audio_response = self.client.post(
                        f"/api/projects/{self.project['id']}/assets?kind=music",
                        files={"file": (filename, audio_path.read_bytes(), content_type)},
                    )
                    self.assertEqual(audio_response.status_code, 200, audio_response.text)
                    audio = audio_response.json()
                    self.assertEqual(audio["kind"], "music")
                    self.assertEqual(audio["project_id"], self.project["id"])

                    job_response = self.client.post(
                        f"/api/projects/{self.project['id']}/jobs",
                        json={
                            "clips": [clip["id"]],
                            "music": audio["id"],
                            "options": {"keep_clip_audio": False, "music_volume": 1},
                        },
                    )
                    self.assertEqual(job_response.status_code, 202, job_response.text)
                    job_id = job_response.json()["id"]
                    queued = self.records[f"clipflow:job:{job_id}"]
                    self.assertEqual(queued["music"]["id"], audio["id"])
                    self.assertEqual(queued["music"]["kind"], "music")

                    class JobRedis:
                        def __init__(self, initial):
                            self.values = {f"clipflow:job:{job_id}": json.dumps(initial)}

                        def get(self, key):
                            return self.values.get(key)

                        def set(self, key, value, ex=None):
                            self.values[key] = value

                    job_redis = JobRedis(queued)
                    with patch.object(worker, "redis", job_redis):
                        worker.process_job(job_id)

                    completed = json.loads(job_redis.get(f"clipflow:job:{job_id}"))
                    self.assertEqual(completed["status"], "completed", completed.get("error"))
                    output_path = self.storage_dir / completed["output_key"]
                    streams = probe_media(output_path)["streams"]
                    self.assertTrue(any(stream["codec_type"] == "video" for stream in streams))
                    self.assertTrue(any(stream["codec_type"] == "audio" for stream in streams))

    def test_voiceover_asset_is_the_transcription_source_with_render_timing(self):
        voiceover_path = self.directory / "elevenlabs.wav"
        subprocess.run(
            [
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-f", "lavfi", "-i", "sine=frequency=600:duration=0.25",
                "-c:a", "pcm_s16le", str(voiceover_path),
            ],
            check=True,
        )
        with (
            patch.object(main, "_write", side_effect=lambda key, value: self.records.__setitem__(key, value)),
            patch.object(main.storage, "put_file", side_effect=self._copy_into_storage),
            patch.object(main.queue, "enqueue"),
        ):
            clip_response = self.client.post(
                f"/api/projects/{self.project['id']}/assets?kind=clip",
                files={"file": ("clip.mp4", self.clip_path.read_bytes(), "video/mp4")},
            )
            self.assertEqual(clip_response.status_code, 200, clip_response.text)
            clip = clip_response.json()
            voiceover_response = self.client.post(
                f"/api/projects/{self.project['id']}/assets?kind=voiceover",
                files={"file": ("elevenlabs.wav", voiceover_path.read_bytes(), "audio/wav")},
            )
            self.assertEqual(voiceover_response.status_code, 200, voiceover_response.text)
            voiceover = voiceover_response.json()

            job_response = self.client.post(
                f"/api/projects/{self.project['id']}/jobs",
                json={
                    "clips": [clip["id"]],
                    "voiceover": voiceover["id"],
                    "options": {
                        "subtitle_source": "voiceover",
                        "voiceover_start": 0.25,
                        "voiceover_loop": True,
                    },
                },
            )
            self.assertEqual(job_response.status_code, 202, job_response.text)
            job_id = job_response.json()["id"]
            queued = self.records[f"clipflow:job:{job_id}"]
            self.assertEqual(queued["voiceover"]["id"], voiceover["id"])

        class JobRedis:
            def __init__(self, initial):
                self.values = {f"clipflow:job:{job_id}": json.dumps(initial)}

            def get(self, key):
                return self.values.get(key)

            def set(self, key, value, ex=None):
                self.values[key] = value

        observed = {}

        def create_srt(sources, destination, language, **options):
            observed.update(sources=sources, language=language, options=options)
            destination.write_text(
                "1\n00:00:00,300 --> 00:00:00,500\nMerhaba dünya\n",
                encoding="utf-8",
            )

        job_redis = JobRedis(queued)
        with (
            patch.object(worker, "redis", job_redis),
            patch.object(worker.storage, "download", side_effect=self._copy_from_storage),
            patch.object(worker.storage, "put_file", side_effect=self._copy_into_storage),
            patch.object(worker, "transcribe_to_srt", side_effect=create_srt),
        ):
            worker.process_job(job_id)

        self.assertEqual(len(observed["sources"]), 1)
        source_path, offset = observed["sources"][0]
        self.assertEqual(source_path.name, "voiceover.wav")
        self.assertEqual(offset, 0.25)
        self.assertAlmostEqual(observed["options"]["timeline_duration"], 0.7, delta=0.08)
        self.assertTrue(observed["options"]["loop_to_duration"])
        completed = json.loads(job_redis.get(f"clipflow:job:{job_id}"))
        self.assertEqual(completed["status"], "completed", completed.get("error"))

    def test_empty_audio_upload_is_rejected(self):
        response = self.client.post(
            f"/api/projects/{self.project['id']}/assets?kind=voiceover",
            files={"file": ("empty.mp3", b"", "audio/mpeg")},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("Dosya okunamadı", response.json()["detail"])


if __name__ == "__main__":
    unittest.main()
