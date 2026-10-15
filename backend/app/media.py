from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any


class MediaError(RuntimeError):
    pass


def probe_media(path: Path) -> dict[str, Any]:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration:stream=codec_type,width,height,duration",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        raise MediaError("Dosya okunamadı; lütfen geçerli bir video veya ses seçin.")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise MediaError("Medya bilgileri okunamadı.") from error


def media_duration(path: Path) -> float:
    metadata = probe_media(path)
    try:
        duration = float(metadata["format"]["duration"])
    except (KeyError, TypeError, ValueError) as error:
        raise MediaError("Dosyanın süresi belirlenemedi.") from error
    if duration <= 0:
        raise MediaError("Dosya boş veya süresi geçersiz.")
    return duration


def _run_ffmpeg(arguments: list[str]) -> None:
    result = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *arguments],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        detail = result.stderr.strip().splitlines()
        message = detail[-1] if detail else "Bilinmeyen FFmpeg hatası"
        raise MediaError(f"Video oluşturulamadı: {message[:300]}")


def create_video(
    clips: list[dict[str, Any]],
    output: Path,
    *,
    fit_mode: str = "crop",
    keep_clip_audio: bool = True,
    music_path: Path | None = None,
    voiceover_path: Path | None = None,
    music_start: float = 0,
    music_volume: float = 0.2,
    music_loop: bool = False,
    voiceover_start: float = 0,
    voiceover_volume: float = 1,
    voiceover_loop: bool = False,
    subtitles_path: Path | None = None,
    subtitle_style: dict[str, Any] | None = None,
) -> float:
    if not clips:
        raise MediaError("En az bir video klip ekleyin.")
    if fit_mode not in {"crop", "fit"}:
        raise MediaError("Görüntü yerleşimi crop veya fit olmalı.")

    output.parent.mkdir(parents=True, exist_ok=True)
    intermediates: list[Path] = []
    durations: list[float] = []
    try:
        for index, clip in enumerate(clips):
            source = Path(clip["path"])
            metadata = probe_media(source)
            duration = media_duration(source)
            durations.append(duration)
            intermediate = output.parent / f"segment-{index:04d}.mp4"
            intermediates.append(intermediate)
            scale = (
                "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920"
                if fit_mode == "crop"
                else "scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2:color=black"
            )
            video_filter = f"{scale},setsar=1,fps=30,format=yuv420p"
            has_audio = any(stream.get("codec_type") == "audio" for stream in metadata.get("streams", []))
            command = ["-i", str(source)]
            if keep_clip_audio and not has_audio:
                command += ["-f", "lavfi", "-t", f"{duration:.4f}", "-i", "anullsrc=r=48000:cl=stereo"]
            command += ["-map", "0:v:0", "-vf", video_filter]
            if keep_clip_audio and has_audio:
                command += ["-map", "0:a:0", "-af", "aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo"]
            elif keep_clip_audio:
                command += ["-map", "1:a:0"]
            else:
                command += ["-an"]
            command += [
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "23",
                "-c:a",
                "aac",
                "-ar",
                "48000",
                "-ac",
                "2",
                "-t",
                f"{duration:.4f}",
                "-movflags",
                "+faststart",
                str(intermediate),
            ]
            _run_ffmpeg(command)

        concat_file = output.parent / "segments.txt"
        concat_file.write_text(
            "".join(f"file '{path.as_posix()}'\n" for path in intermediates),
            encoding="utf-8",
        )
        video_file = output.parent / "joined.mp4"
        _run_ffmpeg(
            ["-f", "concat", "-safe", "0", "-i", str(concat_file), "-c", "copy", str(video_file)]
        )

        total_duration = sum(durations)
        inputs = ["-i", str(video_file)]
        audio_labels: list[str] = []
        filters: list[str] = []
        input_index = 1
        if keep_clip_audio:
            filters.append("[0:a]aresample=48000[clipaudio]")
            audio_labels.append("[clipaudio]")
        for kind, path, start, volume, loop in (
            ("music", music_path, music_start, music_volume, music_loop),
            ("voice", voiceover_path, voiceover_start, voiceover_volume, voiceover_loop),
        ):
            if path is None:
                continue
            if loop:
                inputs += ["-stream_loop", "-1"]
            inputs += ["-itsoffset", str(max(0, start)), "-i", str(path)]
            label = f"{kind}audio"
            filters.append(
                f"[{input_index}:a]aresample=48000,volume={max(0, min(volume, 4))},"
                f"atrim=duration={total_duration:.4f}[{label}]"
            )
            audio_labels.append(f"[{label}]")
            input_index += 1

        if not audio_labels:
            filters.append("anullsrc=r=48000:cl=stereo,atrim=duration=" f"{total_duration:.4f}[mix]")
        elif len(audio_labels) == 1:
            filters.append(f"{audio_labels[0]}anull[mix]")
        else:
            filters.append(
                f"{''.join(audio_labels)}amix=inputs={len(audio_labels)}:duration=longest:normalize=0,"
                f"alimiter=limit=0.95[mix]"
            )

        command = [*inputs, "-filter_complex", ";".join(filters), "-map", "0:v:0", "-map", "[mix]"]
        if subtitles_path is not None:
            style = subtitle_style or {}
            font_size = max(16, min(int(style.get("font_size", 48)), 96))
            color = str(style.get("color", "white"))
            font_name = str(style.get("font_name", "Liberation Sans"))
            if font_name not in {"Liberation Sans", "DejaVu Sans"}:
                font_name = "Liberation Sans"
            alignment = max(1, min(int(style.get("position", 2)), 9))
            border_style = 3 if style.get("background", True) else 1
            force_style = (
                f"FontName={font_name},FontSize={font_size},PrimaryColour=&H00FFFFFF,Outline=2,Shadow=1,"
                f"BorderStyle={border_style},BackColour=&H80000000,"
                f"Alignment={alignment}"
            )
            if color == "yellow":
                force_style = force_style.replace("&H00FFFFFF", "&H0000FFFF")
            command += [
                "-vf",
                f"subtitles='{subtitles_path.as_posix()}':force_style='{force_style}'",
            ]
        command += [
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "23",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-t",
            f"{total_duration:.4f}",
            "-movflags",
            "+faststart",
            str(output),
        ]
        _run_ffmpeg(command)
        return total_duration
    finally:
        for path in intermediates:
            path.unlink(missing_ok=True)
        for name in ("segments.txt", "joined.mp4"):
            (output.parent / name).unlink(missing_ok=True)