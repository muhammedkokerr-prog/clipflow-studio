from pathlib import Path
from shutil import copyfile

import boto3

from app.config import settings


class Storage:
    def __init__(self) -> None:
        self.s3 = None
        if settings.storage_backend == "s3":
            self.s3 = boto3.client(
                "s3",
                region_name=settings.s3_region,
                endpoint_url=settings.s3_endpoint_url,
                aws_access_key_id=settings.aws_access_key_id,
                aws_secret_access_key=settings.aws_secret_access_key,
            )
        else:
            settings.storage_dir.mkdir(parents=True, exist_ok=True)

    @property
    def is_s3(self) -> bool:
        return self.s3 is not None

    def presigned_upload(
        self, key: str, content_type: str, max_size: int, expires_in: int = 3600
    ) -> dict:
        if not self.s3:
            raise RuntimeError("İmzalı yükleme yalnızca S3 depolamasında kullanılabilir.")
        return self.s3.generate_presigned_post(
            settings.s3_bucket,
            key,
            Fields={"Content-Type": content_type},
            Conditions=[
                {"Content-Type": content_type},
                ["content-length-range", 1, max_size],
            ],
            ExpiresIn=expires_in,
        )

    def object_metadata(self, key: str) -> dict:
        if not self.s3:
            raise RuntimeError("S3 nesne bilgisi yerel depolamada kullanılamaz.")
        result = self.s3.head_object(Bucket=settings.s3_bucket, Key=key)
        return {"size": result["ContentLength"], "content_type": result.get("ContentType", "")}

    def put_file(self, source: Path, key: str) -> None:
        if self.s3:
            self.s3.upload_file(str(source), settings.s3_bucket, key)
        else:
            destination = self._local_path(key)
            destination.parent.mkdir(parents=True, exist_ok=True)
            copyfile(source, destination)

    def download(self, key: str, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        if self.s3:
            self.s3.download_file(settings.s3_bucket, key, str(destination))
        else:
            source = self._local_path(key)
            if not source.is_file():
                raise FileNotFoundError("Depolanan dosya bulunamadı.")
            copyfile(source, destination)

    def open(self, key: str):
        if self.s3:
            return self.s3.get_object(Bucket=settings.s3_bucket, Key=key)["Body"]
        return self._local_path(key).open("rb")

    def signed_url(self, key: str, expires_in: int = 900) -> str | None:
        if not self.s3:
            return None
        return self.s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": settings.s3_bucket, "Key": key},
            ExpiresIn=expires_in,
        )

    def _local_path(self, key: str) -> Path:
        path = (settings.storage_dir / key).resolve()
        if not path.is_relative_to(settings.storage_dir.resolve()):
            raise ValueError("Geçersiz depolama anahtarı.")
        return path


storage = Storage()