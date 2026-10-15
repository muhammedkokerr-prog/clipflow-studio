from __future__ import annotations

import json
import logging
import tempfile
from pathlib import Path
from typing import Any

from redis import Redis

from app.config import settings
from app.media import create_video, media_duration, probe_media
from app.speech import transcribe_to_srt
from app.storage import storage

logger = logging.getLogger("clipflow.worker")
redis = Redis.from_url(settings.redis_url, decode_responses=True)


def _save_job(job_id: str, data: dict[str, Any]) -> None:
    redis.set(f"clipflow:job:{job_id}", json.dumps(data), ex=60 * 60 * 24 * 30)


def _asset_path(asset: dict[str, Any], work_dir: Path, label: str) -> Path:
    destination = work_dir / f"{label}{Path(asset['name']).suffix.lower()}"
    storage.download(asset["key"], destination)
    return destination


def _audio_asset_path(asset: dict[str, Any], work_dir: Path, label: str) -> Path:
    path = _asset_path(asset, work_dir, label)
    if not any(stream.get("codec_type") == "audio" for stream in probe_media(path).get("streams", [])):
        raise ValueError(f'"{asset["name"]}" dosyasında kullanılabilir bir ses akışı bulunamadı.')
    return path


def _caption_sources_for_clips(
    clips: list[dict[str, str]], durations: list[float]
) -> list[tuple[Path, float]]:
    sources: list[tuple[Path, float]] = []
    offset = 0.0
    for clip, duration in zip(clips, durations, strict=True):
        sources.append((Path(clip["path"]), offset))
        offset += duration
    return sources


def process_job(job_id: str) -> None:
    raw = redis.get(f"clipflow:job:{job_id}")
    if not raw:
        raise RuntimeError("İş kaydı bulunamadı.")
    job = json.loads(raw)
    job.update(status="processing", progress=3, phase="starting", error=None)
    _save_job(job_id, job)
    logger.info("Video işi başladı: %s", job_id)

    try:
        with tempfile.TemporaryDirectory(prefix=f"clipflow-{job_id}-") as temporary:
            work_dir = Path(temporary)
            clip_records = job["clips"]
            clips = [
                {"path": str(_asset_path(record, work_dir, f"clip-{index}"))}
                for index, record in enumerate(clip_records)
            ]
            clip_durations = []
            for clip in clips:
                metadata = probe_media(Path(clip["path"]))
                if not any(stream.get("codec_type") == "video" for stream in metadata.get("streams", [])):
                    raise ValueError("Yüklenen klipte video akışı bulunamadı.")
                clip_durations.append(media_duration(Path(clip["path"])))
            music = job.get("music")
            voiceover = job.get("voiceover")
            music_path = _audio_asset_path(music, work_dir, "music") if music else None
            voiceover_path = _audio_asset_path(voiceover, work_dir, "voiceover") if voiceover else None
            job.update(progress=10)
            _save_job(job_id, job)

            options = job["options"]
            source = options.get("subtitle_source", "none")
            subtitle_path = None
            if source != "none":
                if source == "clips":
                    speech_paths = _caption_sources_for_clips(clips, clip_durations)
                else:
                    if not voiceover_path:
                        raise ValueError("Altyazı kaynağı olarak seslendirme seçildi ama ses dosyası yok.")
                    speech_paths = [
                        (
                            voiceover_path,
                            float(options.get("voiceover_start", 0)),
                        )
                    ]
                subtitle_path = work_dir / "captions.srt"

                def update_caption_phase(phase: str) -> None:
                    if phase == "transcribing":
                        job["phase"] = (
                            "resolving_voiceover" if source == "voiceover" else "transcribing_subtitles"
                        )
                    else:
                        job["phase"] = "preparing_subtitles"
                    _save_job(job_id, job)

                transcribe_to_srt(
                    speech_paths,
                    subtitle_path,
                    options.get("subtitle_language", "tr"),
                    timeline_duration=sum(clip_durations) if source == "voiceover" else None,
                    loop_to_duration=(
                        source == "voiceover" and bool(options.get("voiceover_loop", False))
                    ),
                    on_status=update_caption_phase,
                )
            job.update(progress=35)
            job["phase"] = "rendering_video"
            _save_job(job_id, job)

            output = work_dir / "output.mp4"
            create_video(
                clips,
                output,
                fit_mode=options.get("fit_mode", "crop"),
                keep_clip_audio=options.get("keep_clip_audio", True),
                music_path=music_path,
                music_start=float(options.get("music_start", 0)),
                music_volume=float(options.get("music_volume", 0.2)),
                music_loop=bool(options.get("music_loop", False)),
                voiceover_start=float(options.get("voiceover_start", 0)),
                voiceover_volume=float(options.get("voiceover_volume", 1)),
                voiceover_path=voiceover_path,
                voiceover_loop=bool(options.get("voiceover_loop", False)),
                subtitles_path=subtitle_path,
                subtitle_style=options.get("subtitle_style", {}),
            )
            output_streams = probe_media(output).get("streams", [])
            if not any(stream.get("codec_type") == "video" for stream in output_streams):
                raise ValueError("Oluşturulan MP4 dosyasında video akışı bulunamadı.")
            if not any(stream.get("codec_type") == "audio" for stream in output_streams):
                raise ValueError("Oluşturulan MP4 dosyasında ses akışı bulunamadı.")
            job.update(progress=95)
            _save_job(job_id, job)
            output_key = f"{job['project_id']}/outputs/{job_id}.mp4"
            storage.put_file(output, output_key)

        job.update(
            status="completed",
            progress=100,
            output_key=output_key,
            error=None,
        )
        _save_job(job_id, job)
        logger.info("Video işi tamamlandı: %s", job_id)
    except Exception as error:
        logger.exception("Video işi başarısız: %s", job_id)
        job.update(status="error", error=str(error)[:500])
        _save_job(job_id, job)
        raise