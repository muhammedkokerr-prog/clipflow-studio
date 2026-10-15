from __future__ import annotations

import json
import logging
import secrets
import shutil
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, File, Header, HTTPException, UploadFile
from fastapi.responses import RedirectResponse, StreamingResponse
from pydantic import BaseModel, Field
from redis import Redis
from rq import Queue

from app.config import settings
from app.media import MediaError, probe_media
from app.storage import storage

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("clipflow.api")
redis = Redis.from_url(settings.redis_url, decode_responses=True)
queue = Queue("videos", connection=Redis.from_url(settings.redis_url))
app = FastAPI(title="ClipFlow Studio API", version="1.0.0")

VIDEO_TYPES = {"video/mp4", "video/quicktime", "video/x-m4v", "video/webm"}
AUDIO_TYPES = {
    "audio/mpeg", "audio/mp3", "audio/mp4", "audio/x-m4a", "audio/wav",
    "audio/x-wav", "audio/wave", "audio/vnd.wave", "audio/aac", "audio/x-aac", "audio/ogg",
}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".webm"}
AUDIO_EXTENSIONS = {".mp3", ".m4a", ".wav", ".aac", ".ogg"}


class JobOptions(BaseModel):
    fit_mode: str = "crop"
    keep_clip_audio: bool = True
    music_start: float = Field(default=0, ge=0, le=3600)
    music_volume: float = Field(default=0.2, ge=0, le=4)
    music_loop: bool = False
    voiceover_start: float = Field(default=0, ge=0, le=3600)
    voiceover_volume: float = Field(default=1, ge=0, le=4)
    voiceover_loop: bool = False
    subtitle_source: str = "none"
    subtitle_language: str = "tr"
    subtitle_style: dict[str, Any] = Field(default_factory=dict)


class CreateJob(BaseModel):
    clips: list[str] = Field(min_length=1)
    music: str | None = None
    voiceover: str | None = None
    options: JobOptions = Field(default_factory=JobOptions)


class PresignAsset(BaseModel):
    kind: str
    name: str = Field(min_length=1, max_length=180)
    content_type: str
    size: int = Field(gt=0)
    duration: float = Field(gt=0, le=24 * 60 * 60)


def _read(key: str) -> dict[str, Any] | None:
    value = redis.get(key)
    return json.loads(value) if value else None


def _write(key: str, value: dict[str, Any]) -> None:
    redis.set(key, json.dumps(value), ex=60 * 60 * 24 * 30)


def _authorize(project_id: str, authorization: str | None) -> dict[str, Any]:
    project = _read(f"clipflow:project:{project_id}")
    if not project:
        raise HTTPException(status_code=404, detail="Proje bulunamadı.")
    token = authorization.removeprefix("Bearer ") if authorization else ""
    if not secrets.compare_digest(token, project["token"]):
        raise HTTPException(status_code=403, detail="Bu projeye erişim izniniz yok.")
    return project


def project_auth(project_id: str, authorization: str | None = Header(default=None)) -> dict[str, Any]:
    return _authorize(project_id, authorization)


@app.get("/health")
def health() -> dict[str, str]:
    redis.ping()
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        raise HTTPException(status_code=503, detail="FFmpeg/FFprobe kurulu değil.")
    return {"status": "ok", "ffmpeg": "ready"}


@app.post("/api/projects")
def create_project() -> dict[str, Any]:
    project_id = str(uuid.uuid4())
    token = secrets.token_urlsafe(32)
    project = {"id": project_id, "token": token, "assets": []}
    _write(f"clipflow:project:{project_id}", project)
    return {"id": project_id, "token": token, "direct_upload": storage.is_s3}


def _validate_asset(kind: str, name: str, content_type: str, size: int) -> str:
    if kind not in {"clip", "music", "voiceover"}:
        raise HTTPException(status_code=400, detail="Dosya türü clip, music veya voiceover olmalı.")
    allowed_types = VIDEO_TYPES if kind == "clip" else AUDIO_TYPES
    if content_type.lower() not in allowed_types:
        raise HTTPException(status_code=415, detail="Bu dosya türü desteklenmiyor.")
    suffix = Path(name).suffix.lower()
    allowed_extensions = VIDEO_EXTENSIONS if kind == "clip" else AUDIO_EXTENSIONS
    if suffix not in allowed_extensions:
        raise HTTPException(status_code=415, detail="Bu dosya türü için dosya uzantısı desteklenmiyor.")
    if kind != "clip" and (
        (suffix == ".mp3" and content_type.lower() not in {"audio/mpeg", "audio/mp3"})
        or (suffix == ".m4a" and content_type.lower() not in {"audio/mp4", "audio/x-m4a"})
        or (suffix == ".wav" and content_type.lower() not in {"audio/wav", "audio/x-wav", "audio/wave", "audio/vnd.wave"})
        or (suffix == ".aac" and content_type.lower() not in {"audio/aac", "audio/x-aac"})
        or (suffix == ".ogg" and content_type.lower() != "audio/ogg")
    ):
        raise HTTPException(status_code=415, detail="Ses dosyasının MIME türü uzantısıyla eşleşmiyor.")
    if size > settings.max_upload_mb * 1024 * 1024:
        raise HTTPException(status_code=413, detail=f"Dosya boyutu {settings.max_upload_mb} MB sınırını aşıyor.")
    return suffix


@app.post("/api/projects/{project_id}/uploads/presign")
def presign_upload(
    payload: PresignAsset,
    project_id: str,
    project: dict[str, Any] = Depends(project_auth),
) -> dict[str, Any]:
    if not storage.is_s3:
        raise HTTPException(status_code=400, detail="Doğrudan yükleme yalnızca S3 depolamada etkin.")
    suffix = _validate_asset(payload.kind, payload.name, payload.content_type, payload.size)
    asset_id = str(uuid.uuid4())
    key = f"{project_id}/assets/{asset_id}{suffix}"
    stage = {
        "id": asset_id,
        "project_id": project_id,
        "kind": payload.kind,
        "key": key,
        "name": Path(payload.name).name,
        "duration": payload.duration,
        "size": payload.size,
        "content_type": payload.content_type.lower(),
    }
    _write(f"clipflow:upload:{project_id}:{asset_id}", stage)
    upload = storage.presigned_upload(
        key,
        payload.content_type,
        settings.max_upload_mb * 1024 * 1024,
    )
    return {
        "id": asset_id,
        "url": upload["url"],
        "fields": upload["fields"],
    }


@app.post("/api/projects/{project_id}/uploads/{asset_id}/complete")
def complete_upload(
    project_id: str,
    asset_id: str,
    project: dict[str, Any] = Depends(project_auth),
) -> dict[str, Any]:
    stage_key = f"clipflow:upload:{project_id}:{asset_id}"
    staged = _read(stage_key)
    if not staged:
        raise HTTPException(status_code=404, detail="Yükleme oturumu bulunamadı veya süresi doldu.")
    try:
        metadata = storage.object_metadata(staged["key"])
    except Exception as error:
        logger.warning("S3 yükleme kontrolü başarısız: %s", error)
        raise HTTPException(status_code=400, detail="Dosya depolamaya yüklenemedi.") from error
    if metadata["size"] != staged["size"] or metadata["content_type"].lower() != staged["content_type"]:
        raise HTTPException(status_code=400, detail="Yüklenen dosyanın boyutu veya türü doğrulanamadı.")
    project["assets"].append(staged)
    _write(f"clipflow:project:{project_id}", project)
    redis.delete(stage_key)
    return {key: value for key, value in staged.items() if key not in {"key", "content_type"}}


@app.post("/api/projects/{project_id}/assets")
async def upload_asset(
    project_id: str,
    kind: str,
    project: dict[str, Any] = Depends(project_auth),
    file: UploadFile = File(...),
) -> dict[str, Any]:
    content_type = (file.content_type or "").lower()
    filename = Path(file.filename or "upload").name
    suffix = _validate_asset(kind, filename, content_type, 0)

    asset_id = str(uuid.uuid4())
    key = f"{project_id}/assets/{asset_id}{suffix}"
    temporary_path: Path | None = None
    size = 0
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temporary:
            temporary_path = Path(temporary.name)
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > settings.max_upload_mb * 1024 * 1024:
                    raise HTTPException(status_code=413, detail=f"Dosya boyutu {settings.max_upload_mb} MB sınırını aşıyor.")
                temporary.write(chunk)
        metadata = probe_media(temporary_path)
        duration = float(metadata.get("format", {}).get("duration", 0))
        streams = metadata.get("streams", [])
        required_stream = "video" if kind == "clip" else "audio"
        if duration <= 0 or not any(item.get("codec_type") == required_stream for item in streams):
            raise HTTPException(status_code=400, detail="Dosyada geçerli medya bulunamadı.")
        storage.put_file(temporary_path, key)
    except MediaError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    finally:
        if temporary_path:
            temporary_path.unlink(missing_ok=True)
        await file.close()

    asset = {
        "id": asset_id,
        "project_id": project_id,
        "kind": kind,
        "key": key,
        "name": filename[:180],
        "duration": duration,
        "size": size,
    }
    project["assets"].append(asset)
    _write(f"clipflow:project:{project_id}", project)
    return {key: value for key, value in asset.items() if key != "key"}


@app.post("/api/projects/{project_id}/jobs", status_code=202)
def create_job(
    payload: CreateJob,
    project_id: str,
    project: dict[str, Any] = Depends(project_auth),
) -> dict[str, Any]:
    if len(payload.clips) > settings.max_clips_per_job:
        raise HTTPException(status_code=400, detail=f"En fazla {settings.max_clips_per_job} klip eklenebilir.")
    assets = {asset["id"]: asset for asset in project["assets"]}
    try:
        clips = [assets[asset_id] for asset_id in payload.clips]
    except KeyError as error:
        raise HTTPException(status_code=400, detail="Kliplerden biri bu projeye ait değil.") from error
    if any(asset["kind"] != "clip" for asset in clips):
        raise HTTPException(status_code=400, detail="Video listesinde geçersiz bir dosya var.")
    music = assets.get(payload.music) if payload.music else None
    voiceover = assets.get(payload.voiceover) if payload.voiceover else None
    if payload.music and (not music or music["kind"] != "music"):
        raise HTTPException(status_code=400, detail="Müzik dosyası bu projeye ait değil.")
    if payload.voiceover and (not voiceover or voiceover["kind"] != "voiceover"):
        raise HTTPException(status_code=400, detail="Seslendirme dosyası bu projeye ait değil.")
    if payload.music and music["project_id"] != project_id:
        raise HTTPException(status_code=400, detail="Müzik dosyası bu projeye ait değil.")
    if payload.voiceover and voiceover["project_id"] != project_id:
        raise HTTPException(status_code=400, detail="Seslendirme dosyası bu projeye ait değil.")
    if payload.options.fit_mode not in {"crop", "fit"}:
        raise HTTPException(status_code=400, detail="Görüntü yerleşimi geçersiz.")
    if payload.options.subtitle_source not in {"none", "clips", "voiceover"}:
        raise HTTPException(status_code=400, detail="Altyazı kaynağı geçersiz.")
    if payload.options.subtitle_source == "voiceover" and not voiceover:
        raise HTTPException(status_code=400, detail="Seslendirme altyazısı için önce seslendirme yükleyin.")

    job_id = str(uuid.uuid4())
    job = {
        "id": job_id,
        "project_id": project_id,
        "status": "queued",
        "progress": 0,
        "clips": clips,
        "music": music,
        "voiceover": voiceover,
        "options": payload.options.model_dump(),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    _write(f"clipflow:job:{job_id}", job)
    queue.enqueue(
        "app.worker.process_job",
        job_id,
        job_id=job_id,
        job_timeout="2h",
        result_ttl=60 * 60 * 24 * 30,
        failure_ttl=60 * 60 * 24 * 30,
    )
    logger.info("Video işi kuyruğa alındı: %s", job_id)
    return {"id": job_id, "status": "queued", "progress": 0}


@app.get("/api/projects/{project_id}/jobs/{job_id}")
def get_job(
    project_id: str,
    job_id: str,
    project: dict[str, Any] = Depends(project_auth),
) -> dict[str, Any]:
    job = _read(f"clipflow:job:{job_id}")
    if not job or job["project_id"] != project_id:
        raise HTTPException(status_code=404, detail="Video işi bulunamadı.")
    if job["status"] in {"queued", "processing"}:
        queued_job = queue.fetch_job(job_id)
        if queued_job and str(queued_job.get_status(refresh=True)).lower().endswith("failed"):
            job.update(status="error", error=job.get("error") or "Worker işi tamamlayamadan durdu.")
            _write(f"clipflow:job:{job_id}", job)
    if job["status"] == "completed":
        job["download_url"] = f"/api/projects/{project_id}/jobs/{job_id}/output"
    return {key: value for key, value in job.items() if key not in {"clips", "music", "voiceover", "options", "output_key"}}


@app.get("/api/projects/{project_id}/jobs/{job_id}/output")
def download_output(
    project_id: str,
    job_id: str,
    project: dict[str, Any] = Depends(project_auth),
):
    job = _read(f"clipflow:job:{job_id}")
    if not job or job.get("project_id") != project_id or job.get("status") != "completed":
        raise HTTPException(status_code=404, detail="Hazır video bulunamadı.")
    signed_url = storage.signed_url(job["output_key"])
    if signed_url:
        return RedirectResponse(signed_url)
    return StreamingResponse(
        storage.open(job["output_key"]),
        media_type="video/mp4",
        headers={"Content-Disposition": f'attachment; filename="clipflow-{job_id}.mp4"'},
    )


static_dir = Path(__file__).resolve().parents[1] / "static"
if static_dir.is_dir():
    from fastapi.staticfiles import StaticFiles

    app.mount("/", StaticFiles(directory=static_dir, html=True), name="frontend")