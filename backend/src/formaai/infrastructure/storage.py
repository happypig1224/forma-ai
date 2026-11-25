"""Storage service abstraction and local filesystem implementation."""

import hashlib
import re
from pathlib import Path
from typing import Protocol


class StorageServiceProtocol(Protocol):
    """Protocol for persisting and retrieving asset files."""

    def save_file(
        self,
        file_data: bytes,
        filename: str,
        content_type: str,
        user_id: int,
    ) -> tuple[str, int]:
        """Save file data and return (file_key, file_size_in_bytes)."""
        ...

    def get_file(self, file_key: str) -> bytes | None:
        """Retrieve file content by key, or None if not found."""
        ...

    def delete_file(self, file_key: str) -> bool:
        """Delete file by key. Return True if deleted, False if not found."""
        ...


class LocalStorageService:
    """Local filesystem storage with SHA-256 addressing and path sanitization."""

    def __init__(self, base_dir: Path | str) -> None:
        self.base_dir = Path(base_dir).resolve()
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _resolve_safe_path(self, file_key: str) -> Path:
        """Resolve file_key to absolute path and prevent directory traversal."""
        clean_key = file_key.strip().lstrip("/\\")
        target_path = (self.base_dir / clean_key).resolve()
        if not target_path.is_relative_to(self.base_dir):
            raise ValueError("Path traversal attempt detected")
        return target_path

    def _sanitize_extension(self, filename: str, content_type: str) -> str:
        """Extract and sanitize safe extension, falling back to content_type."""
        raw_ext = Path(filename).suffix.lower()
        if raw_ext and re.match(r"^\.[a-z0-9]{1,10}$", raw_ext):
            return raw_ext
        if content_type:
            ct = content_type.lower()
            if "png" in ct:
                return ".png"
            if "jpeg" in ct or "jpg" in ct:
                return ".jpg"
            if "webp" in ct:
                return ".webp"
            if "mp4" in ct:
                return ".mp4"
            if "pdf" in ct:
                return ".pdf"
        return ""

    def save_file(
        self,
        file_data: bytes,
        filename: str,
        content_type: str,
        user_id: int,
    ) -> tuple[str, int]:
        if user_id <= 0:
            raise ValueError("user_id must be a positive integer")

        sha256_hash = hashlib.sha256(file_data).hexdigest()
        file_size = len(file_data)
        ext = self._sanitize_extension(filename, content_type)

        # Key structure: library/{user_id}/{hash_prefix}/{full_hash}{ext}
        file_key = f"library/{user_id}/{sha256_hash[:2]}/{sha256_hash}{ext}"
        target_path = self._resolve_safe_path(file_key)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_bytes(file_data)
        return file_key, file_size

    def get_file(self, file_key: str) -> bytes | None:
        try:
            target_path = self._resolve_safe_path(file_key)
            if target_path.is_file():
                return target_path.read_bytes()
        except ValueError, OSError:
            return None
        return None

    def delete_file(self, file_key: str) -> bool:
        try:
            target_path = self._resolve_safe_path(file_key)
            if target_path.is_file():
                target_path.unlink()
                return True
        except ValueError, OSError:
            return False
        return False


class MockStorageService:
    """In-memory storage service for fast unit and integration testing."""

    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}

    def _sanitize_extension(self, filename: str, content_type: str) -> str:
        raw_ext = Path(filename).suffix.lower()
        if raw_ext and re.match(r"^\.[a-z0-9]{1,10}$", raw_ext):
            return raw_ext
        if content_type:
            ct = content_type.lower()
            if "png" in ct:
                return ".png"
            if "jpeg" in ct or "jpg" in ct:
                return ".jpg"
            if "webp" in ct:
                return ".webp"
            if "mp4" in ct:
                return ".mp4"
            if "pdf" in ct:
                return ".pdf"
        return ""

    def save_file(
        self,
        file_data: bytes,
        filename: str,
        content_type: str,
        user_id: int,
    ) -> tuple[str, int]:
        if user_id <= 0:
            raise ValueError("user_id must be a positive integer")

        sha256_hash = hashlib.sha256(file_data).hexdigest()
        ext = self._sanitize_extension(filename, content_type)
        file_key = f"library/{user_id}/{sha256_hash[:2]}/{sha256_hash}{ext}"
        self.files[file_key] = file_data
        return file_key, len(file_data)

    def get_file(self, file_key: str) -> bytes | None:
        return self.files.get(file_key)

    def delete_file(self, file_key: str) -> bool:
        if file_key in self.files:
            del self.files[file_key]
            return True
        return False
