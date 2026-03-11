from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import aioboto3

from app.config import Settings


class FileValidationError(ValueError):
    pass


def validate_upload(content: bytes, filename: str) -> None:
    if len(content) > 10 * 1024 * 1024:
        raise FileValidationError("Файл больше 10 МБ")
    extension = Path(filename).suffix.lower()
    if extension not in {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif", ".pdf", ".txt"}:
        raise FileValidationError("Неподдерживаемый формат файла")


class StorageService:
    async def save(self, content: bytes, filename: str) -> str:
        raise NotImplementedError


class LocalStorageService(StorageService):
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    async def save(self, content: bytes, filename: str) -> str:
        suffix = Path(filename).suffix
        key = f"{uuid4()}{suffix}"
        destination = self.root / key
        destination.write_bytes(content)
        return key


class S3StorageService(StorageService):
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def save(self, content: bytes, filename: str) -> str:
        session = aioboto3.Session()
        key = f"{uuid4()}{Path(filename).suffix}"
        async with session.client(
            "s3",
            endpoint_url=self.settings.s3_endpoint_url,
            aws_access_key_id=self.settings.s3_access_key,
            aws_secret_access_key=self.settings.s3_secret_key,
            region_name=self.settings.s3_region,
        ) as client:
            await client.put_object(Bucket=self.settings.s3_bucket, Key=key, Body=content)
        return key
