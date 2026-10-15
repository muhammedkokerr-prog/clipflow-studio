from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

from app.config import settings
from app.media import MediaError, media_duration, probe_media


def _timestamp(seconds: float) -> str:
    milliseconds = max(0, round(seconds * 1000))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds_part, milliseconds = divmod(remainder, 1000)
    return f"{hours:02}:{minutes:02}:{seconds_part:02},{milliseconds:03}"


def _group_words(words: list[Any], offset: float) -> list[tuple[float, float, str]]:
    cues: list[tuple[float, float, str]] = []
    group: list[Any] = []

    def flush_group() -> None:
        if group:
            cues.append(
                (
                    offset + float(group[0].start),
                    offset + float(group[-1].end),
                    "".join(item.word for item in group).strip(),
                )
            )
            group.clear()

    for word in words:
        candidate = "".join(item.word for item in [*group, word]).strip()
        if group and (len(group) >= 6 or len(candidate) > 32):
            flush_group()
        group.append(word)
        text = "".join(item.word for item in group).strip()
        if len(group) >= 6 or text.endswith(('.', '!', '?', ':', ';')):
            flush_group()
    flush_group()
    return cues


def transcribe_to_srt(
    source_paths: list[tuple[Path, float]],
    destination: Path,
    language: str,
    *,
    timeline_duration: float | None = None,
    loop_to_duration: bool = False,
    on_status: Callable[[str], None] | None = None,
) -> None:
    if not settings.openai_api_key:
        raise MediaError(
            "Altyazı için OPENAI_API_KEY gerekli. API anahtarı ekleyin veya altyazı kaynağını 'Altyazısız' seçin."
        )
    from openai import OpenAI

    client = OpenAI(api_key=settings.openai_api_key)
    cues: list[tuple[float, float, str]] = []
    destination.parent.mkdir(parents=True, exist_ok=True)
    segment_seconds = 8 * 60
    try:
        for source_index, (path, offset) in enumerate(source_paths):
            if on_status and source_index == 0:
                on_status("transcribing")
            metadata = probe_media(path)
            if not any(stream.get("codec_type") == "audio" for stream in metadata.get("streams", [])):
                continue
            duration = media_duration(path)
            source_cues: list[tuple[float, float, str]] = []
            for chunk_index, start in enumerate(range(0, int(duration + segment_seconds - 1), segment_seconds)):
                chunk = destination.parent / f"speech-{source_index:03d}-{chunk_index:03d}.mp3"
                chunk_duration = min(segment_seconds, duration - start)
                if chunk_duration <= 0:
                    continue
                extraction = subprocess.run(
                    [
                        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                        "-ss", str(start), "-i", str(path), "-t", str(chunk_duration),
                        "-vn", "-ac", "1", "-ar", "16000", "-b:a", "64k", str(chunk),
                    ],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if extraction.returncode:
                    raise MediaError("Konuşma sesi videodan ayrılamadı.")
                with chunk.open("rb") as audio_file:
                    transcript = client.audio.transcriptions.create(
                        model="whisper-1",
                        file=audio_file,
                        response_format="verbose_json",
                        timestamp_granularities=["word"],
                        language=language,
                    )
                words = getattr(transcript, "words", None) or []
                chunk_offset = offset + start
                if words:
                    source_cues.extend(_group_words(words, chunk_offset))
                else:
                    segments = getattr(transcript, "segments", None) or []
                    if segments:
                        source_cues.extend(
                            (
                                chunk_offset + float(segment.start),
                                chunk_offset + float(segment.end),
                                segment.text.strip(),
                            )
                            for segment in segments
                            if segment.text.strip()
                        )
                    elif getattr(transcript, "text", "").strip():
                        raise MediaError("Konuşma dökümü zaman bilgisi içermiyor; altyazı üretilemedi.")
            if loop_to_duration and timeline_duration is not None:
                repeat = 0
                while offset + repeat * duration < timeline_duration:
                    repeat_offset = repeat * duration
                    cues.extend(
                        (start + repeat_offset, end + repeat_offset, text)
                        for start, end, text in source_cues
                    )
                    repeat += 1
            else:
                cues.extend(source_cues)
    except MediaError:
        raise
    except Exception as error:
        raise MediaError(f"Konuşma yazıya çevrilemedi: {error}") from error

    if timeline_duration is not None:
        cues = [
            (start, min(end, timeline_duration), text)
            for start, end, text in cues
            if start < timeline_duration and min(end, timeline_duration) > start
        ]
    if not cues:
        raise MediaError("Seçilen altyazı kaynağında anlaşılır konuşma bulunamadı.")
    if on_status:
        on_status("preparing_subtitles")
    destination.write_text(
        "\n\n".join(
            f"{index}\n{_timestamp(start)} --> {_timestamp(end)}\n{text}"
            for index, (start, end, text) in enumerate(cues, start=1)
        ),
        encoding="utf-8",
    )