import unittest
from unittest.mock import Mock, PropertyMock, patch

from app import main
from fastapi import HTTPException


class PresignedUploadTests(unittest.TestCase):
    def test_returns_signed_post_form_fields(self):
        signed_post = {"url": "https://storage.example", "fields": {"key": "random-key", "policy": "signed"}}
        payload = main.PresignAsset(
            kind="clip",
            name="clip.mp4",
            content_type="video/mp4",
            size=4096,
            duration=3.5,
        )
        with (
            patch.object(type(main.storage), "is_s3", new_callable=PropertyMock, return_value=True),
            patch.object(main.storage, "presigned_upload", return_value=signed_post),
            patch.object(main, "_write"),
        ):
            result = main.presign_upload(payload, "project-id", {})

        self.assertEqual(result["url"], signed_post["url"])
        self.assertEqual(result["fields"], signed_post["fields"])
        self.assertEqual(len(result["id"]), 36)


class JobStatusTests(unittest.TestCase):
    def test_voiceover_subtitles_require_a_voiceover_asset_before_queueing(self):
        project = {
            "id": "project-id",
            "assets": [
                {"id": "clip-id", "kind": "clip", "project_id": "project-id", "key": "clip.mp4"}
            ],
        }
        payload = main.CreateJob(
            clips=["clip-id"],
            options=main.JobOptions(subtitle_source="voiceover"),
        )
        with self.assertRaises(HTTPException) as raised:
            main.create_job(payload, "project-id", project)
        self.assertIn("önce seslendirme yükleyin", raised.exception.detail)

    def test_completed_job_returns_protected_download_route(self):
        job = {
            "id": "job-id",
            "project_id": "project-id",
            "status": "completed",
            "progress": 100,
            "output_key": "private/output.mp4",
            "clips": [{"key": "private/input.mp4"}],
        }
        with patch.object(main, "_read", return_value=job):
            result = main.get_job("project-id", "job-id", {})
        self.assertEqual(result["download_url"], "/api/projects/project-id/jobs/job-id/output")
        self.assertNotIn("output_key", result)
        self.assertNotIn("clips", result)

    def test_failed_worker_changes_processing_job_to_error(self):
        job = {"id": "job-id", "project_id": "project-id", "status": "processing", "progress": 35}
        queued_job = Mock()
        queued_job.get_status.return_value = "failed"
        with (
            patch.object(main, "_read", return_value=job),
            patch.object(main.queue, "fetch_job", return_value=queued_job),
            patch.object(main, "_write") as save_job,
        ):
            result = main.get_job("project-id", "job-id", {})
        self.assertEqual(result["status"], "error")
        self.assertIn("Worker", result["error"])
        save_job.assert_called_once()


if __name__ == "__main__":
    unittest.main()