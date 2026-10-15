import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.media import MediaError
from app.speech import transcribe_to_srt


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg gerekli")
class SpeechTranscriptionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name)
        self.audio = self.directory / "elevenlabs.wav"
        subprocess.run(
            [
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-f", "lavfi", "-i", "sine=frequency=440:duration=0.25",
                "-c:a", "pcm_s16le", str(self.audio),
            ],
            check=True,
        )

    def tearDown(self):
        self.temporary.cleanup()

    def test_voiceover_transcription_applies_offset_and_repeats_only_to_video_end(self):
        transcript = SimpleNamespace(
            words=[SimpleNamespace(word=" Merhaba.", start=0.05, end=0.15)],
            text="Merhaba.",
        )
        with (
            patch("app.speech.settings.openai_api_key", "test-key"),
            patch("openai.OpenAI") as openai,
        ):
            openai.return_value.audio.transcriptions.create.return_value = transcript
            destination = self.directory / "captions.srt"
            transcribe_to_srt(
                [(self.audio, 0.1)],
                destination,
                "tr",
                timeline_duration=0.8,
                loop_to_duration=True,
            )

        srt = destination.read_text(encoding="utf-8")
        self.assertIn("00:00:00,150 --> 00:00:00,250\nMerhaba.", srt)
        self.assertIn("00:00:00,400 --> 00:00:00,500\nMerhaba.", srt)
        self.assertIn("00:00:00,650 --> 00:00:00,750\nMerhaba.", srt)
        self.assertEqual(srt.count("Merhaba."), 3)
        openai.return_value.audio.transcriptions.create.assert_called_once()

    def test_missing_api_key_returns_clear_error(self):
        with (
            patch("app.speech.settings.openai_api_key", None),
            self.assertRaisesRegex(MediaError, "OPENAI_API_KEY"),
        ):
            transcribe_to_srt([(self.audio, 0)], self.directory / "captions.srt", "tr")

    def test_transcription_provider_error_is_reported(self):
        with (
            patch("app.speech.settings.openai_api_key", "test-key"),
            patch("openai.OpenAI") as openai,
            self.assertRaisesRegex(MediaError, "Konuşma yazıya çevrilemedi: provider unavailable"),
        ):
            openai.return_value.audio.transcriptions.create.side_effect = RuntimeError(
                "provider unavailable"
            )
            transcribe_to_srt([(self.audio, 0)], self.directory / "captions.srt", "tr")

    def test_segment_timestamps_are_used_when_word_timestamps_are_unavailable(self):
        transcript = SimpleNamespace(
            words=[],
            segments=[SimpleNamespace(start=0.1, end=0.2, text="Kısa segment.")],
            text="Kısa segment.",
        )
        with (
            patch("app.speech.settings.openai_api_key", "test-key"),
            patch("openai.OpenAI") as openai,
        ):
            openai.return_value.audio.transcriptions.create.return_value = transcript
            destination = self.directory / "segments.srt"
            transcribe_to_srt([(self.audio, 0.3)], destination, "tr")

        self.assertIn("00:00:00,400 --> 00:00:00,500\nKısa segment.", destination.read_text(encoding="utf-8"))

    def test_transcript_without_timestamps_fails_instead_of_guessing(self):
        transcript = SimpleNamespace(words=[], segments=[], text="Gerçek döküm.")
        with (
            patch("app.speech.settings.openai_api_key", "test-key"),
            patch("openai.OpenAI") as openai,
            self.assertRaisesRegex(MediaError, "zaman bilgisi içermiyor"),
        ):
            openai.return_value.audio.transcriptions.create.return_value = transcript
            transcribe_to_srt([(self.audio, 0)], self.directory / "untimed.srt", "tr")


if __name__ == "__main__":
    unittest.main()
