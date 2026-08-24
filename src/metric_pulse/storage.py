"""上传对象与导出文件的本地存储实现。

对象键由内容哈希生成，相同文件可复用且不会因原始文件名覆盖。路径在使用前解析并校验仍
位于配置根目录，防止路径穿越；数据库只保存对象键，不保存任意磁盘绝对路径。
"""

from __future__ import annotations

import hashlib
import io
import shutil
from pathlib import Path

import boto3

from .config import get_settings


class FileObjectStore:
    """基于内容寻址、可选 S3 后端的对象存储。"""

    def __init__(self, root: Path | None = None) -> None:
        settings = get_settings()
        self.root = root or settings.object_root
        self.root.mkdir(parents=True, exist_ok=True)
        self.backend = "filesystem" if root else settings.storage_backend
        self.bucket = settings.s3_bucket
        self.s3 = None
        if self.backend == "s3":
            self.s3 = boto3.client(
                "s3",
                endpoint_url=settings.s3_endpoint_url or None,
                aws_access_key_id=settings.s3_access_key,
                aws_secret_access_key=settings.s3_secret_key,
                region_name=settings.s3_region,
            )

    @staticmethod
    def digest(data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    def put_bytes(self, data: bytes, *, namespace: str, suffix: str = "") -> tuple[str, str]:
        """以 SHA-256 生成对象键；已存在内容不重复写入本地磁盘。"""

        digest = self.digest(data)
        relative = Path(namespace) / digest[:2] / f"{digest}{suffix}"
        target = self.root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if self.s3:
            self.s3.upload_fileobj(io.BytesIO(data), self.bucket, relative.as_posix())
        elif not target.exists():
            target.write_bytes(data)
        return relative.as_posix(), digest

    def put_file(self, source: Path, *, namespace: str, suffix: str = "") -> tuple[str, str]:
        """流式计算大文件哈希后复制/上传，避免把整个导出文件读入内存。"""

        digest = hashlib.sha256()
        with source.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        hex_digest = digest.hexdigest()
        relative = Path(namespace) / hex_digest[:2] / f"{hex_digest}{suffix}"
        target = self.root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if self.s3:
            self.s3.upload_file(str(source), self.bucket, relative.as_posix())
        elif not target.exists():
            shutil.copy2(source, target)
        return relative.as_posix(), hex_digest

    def path(self, key: str) -> Path:
        """安全解析对象键；S3 对象在首次读取时下载到本地缓存。"""

        path = (self.root / key).resolve()
        if self.root.resolve() not in path.parents:
            raise ValueError("Object key escapes storage root")
        if self.s3 and not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            self.s3.download_file(self.bucket, key, str(path))
        return path

    def read_bytes(self, key: str) -> bytes:
        return self.path(key).read_bytes()


def export_path(filename: str) -> Path:
    """在导出根目录内创建路径，并拒绝 ``..`` 等路径逃逸。"""

    root = get_settings().export_root.resolve()
    path = (root / filename).resolve()
    if root not in path.parents:
        raise ValueError("Export path escapes export root")
    path.parent.mkdir(parents=True, exist_ok=True)
    return path
