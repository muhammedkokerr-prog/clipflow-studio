from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = "development"
    redis_url: str = "redis://localhost:6379/0"
    storage_backend: str = "local"
    storage_dir: Path = Path("/tmp/clipflow")
    s3_bucket: str = ""
    s3_region: str = "us-east-1"
    s3_endpoint_url: str | None = None
    aws_access_key_id: str | None = None
    aws_secret_access_key: str | None = None
    openai_api_key: str | None = None
    max_upload_mb: int = 1024
    max_clips_per_job: int = 50


settings = Settings()

if settings.storage_backend == "s3" and not settings.s3_bucket:
    raise RuntimeError("S3_STORAGE_BACKEND seçildiğinde S3_BUCKET zorunludur.")
if settings.app_env == "production" and settings.storage_backend != "s3":
    raise RuntimeError("Render üretiminde kalıcı dosyalar için STORAGE_BACKEND=s3 ayarlanmalıdır.")