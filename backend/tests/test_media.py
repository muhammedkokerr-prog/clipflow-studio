import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from app.media import create_video, media_duration, probe_media
from app.speech import _group_words, _timestamp
from app.worker import _caption_sources_for_clips


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg gerekli")
class MediaPipelineTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name)
        self.first = self.directory / "first.mp4"
        self.second = self.directory / "second.mp4"
        self.music = self.directory / "music.wav"
        self.subtitles = self.directory / "captions.srt"
        self.output = self.directory / "output.mp4"
        subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=red:s=160x90:r=12:d=0.5", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-an", str(self.first)],
            check=True,
        )
        subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=880:duration=1", "-c:a", "pcm_s16le", str(self.music)],
            check=True,
        )
        self.subtitles.write_text(
            "1\n00:00:00,200 --> 00:00:00,400\nMerhaba dünya, güzel!\n", encoding="utf-8"
        )
        subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=blue:s=90x160:r=12:d=0.5", "-f", "lavfi", "-i", "sine=frequency=440:duration=0.5", "-shortest", "-c:v", "libx264", "-c:a", "aac", "-pix_fmt", "yuv420p", str(self.second)],
            check=True,
        )

    def tearDown(self):
        self.temporary.cleanup()

    def test_normalizes_order_and_silent_clip_audio(self):
        duration = create_video(
            [{"path": str(self.first)}, {"path": str(self.second)}],
            self.output,
            fit_mode="fit",
            music_path=self.music,
            music_start=0.1,
            music_volume=0.4,
            subtitles_path=self.subtitles,
            subtitle_style={"font_name": "DejaVu Sans", "font_size": 30, "color": "yellow", "background": True, "position": 2},
        )
        metadata = probe_media(self.output)
        video = next(stream for stream in metadata["streams"] if stream["codec_type"] == "video")
        self.assertEqual((video["width"], video["height"]), (1080, 1920))
        self.assertAlmostEqual(duration, 1.0, delta=0.08)
        self.assertTrue(any(stream["codec_type"] == "audio" for stream in metadata["streams"]))

        def center_pixel(at: str) -> tuple[int, int, int]:
            frame = subprocess.run(
                ["ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", at, "-i", str(self.output), "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                check=True,
                capture_output=True,
            ).stdout
            offset = (960 * 1080 + 540) * 3
            return tuple(frame[offset : offset + 3])

        red = center_pixel("0.2")
        blue = center_pixel("0.8")
        self.assertGreater(red[0], red[2] * 2)
        self.assertGreater(blue[2], blue[0] * 2)

    def test_adds_voiceover_without_clip_audio(self):
        create_video(
            [{"path": str(self.second)}],
            self.output,
            keep_clip_audio=False,
            voiceover_path=self.music,
            voiceover_start=0.1,
            voiceover_volume=0.5,
        )
        metadata = probe_media(self.output)
        self.assertTrue(any(stream["codec_type"] == "audio" for stream in metadata["streams"]))

    def test_burns_turkish_subtitles_into_synthetic_video(self):
        plain = self.directory / "plain.mp4"
        captioned = self.directory / "captioned.mp4"
        create_video([{"path": str(self.second)}], plain)
        create_video(
            [{"path": str(self.second)}],
            captioned,
            subtitles_path=self.subtitles,
            subtitle_style={"font_name": "DejaVu Sans", "font_size": 40, "position": 5},
        )
        metadata = probe_media(captioned)
        self.assertTrue(any(stream["codec_type"] == "video" for stream in metadata["streams"]))
        self.assertTrue(any(stream["codec_type"] == "audio" for stream in metadata["streams"]))

        def frame(path: Path) -> bytes:
            return subprocess.run(
                [
                    "ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", "0.3",
                    "-i", str(path), "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-",
                ],
                check=True,
                capture_output=True,
            ).stdout

        self.assertGreater(sum(left != right for left, right in zip(frame(plain), frame(captioned))), 100)

    def test_supported_external_audio_formats_and_optional_loop(self):
        formats = (
            ("mp3", ["-c:a", "libmp3lame"]),
            ("wav", ["-c:a", "pcm_s16le"]),
            ("m4a", ["-c:a", "aac"]),
            ("aac", ["-c:a", "aac", "-f", "adts"]),
        )
        for extension, codec in formats:
            with self.subTest(extension=extension):
                audio = self.directory / f"tone.{extension}"
                subprocess.run(
                    [
                        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                        "-f", "lavfi", "-i", "sine=frequency=660:duration=0.2",
                        *codec, str(audio),
                    ],
                    check=True,
                )
                create_video(
                    [{"path": str(self.first)}],
                    self.output,
                    keep_clip_audio=False,
                    music_path=audio,
                    music_volume=1,
                )
                metadata = probe_media(self.output)
                self.assertTrue(any(stream["codec_type"] == "video" for stream in metadata["streams"]))
                audio_stream = next(
                    stream for stream in metadata["streams"] if stream["codec_type"] == "audio"
                )
                self.assertLess(float(audio_stream["duration"]), media_duration(self.output) - 0.1)

        short_audio = self.directory / "short-loop.wav"
        subprocess.run(
            [
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-f", "lavfi", "-i", "sine=frequency=660:duration=0.15",
                "-c:a", "pcm_s16le", str(short_audio),
            ],
            check=True,
        )
        create_video(
            [{"path": str(self.first)}],
            self.output,
            keep_clip_audio=False,
            music_path=short_audio,
            music_loop=True,
            music_volume=1,
        )
        audio_stream = next(
            stream for stream in probe_media(self.output)["streams"] if stream["codec_type"] == "audio"
        )
        self.assertAlmostEqual(float(audio_stream["duration"]), media_duration(self.output), delta=0.08)


class ClipCaptionOffsetTests(unittest.TestCase):
    def test_offsets_follow_the_order_received_for_render(self):
        clips = [{"path": "second-selected-first.mp4"}, {"path": "first-selected-second.mp4"}]
        result = _caption_sources_for_clips(clips, [3.5, 2.25])
        self.assertEqual(
            result,
            [(Path("second-selected-first.mp4"), 0.0), (Path("first-selected-second.mp4"), 3.5)],
        )


class SubtitleTimingTests(unittest.TestCase):
    def test_word_groups_keep_clip_offset(self):
        words = [
            SimpleNamespace(word=" Merhaba", start=0.1, end=0.4),
            SimpleNamespace(word=" dünya.", start=0.45, end=0.8),
        ]
        self.assertEqual(_group_words(words, 2.0), [(2.1, 2.8, "Merhaba dünya.")])
        self.assertEqual(_timestamp(65.25), "00:01:05,250")


if __name__ == "__main__":
    unittest.main()