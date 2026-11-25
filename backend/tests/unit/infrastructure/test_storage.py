"""Unit tests for storage service implementations and path traversal protection."""

from pathlib import Path

import pytest

from formaai.infrastructure.storage import LocalStorageService, MockStorageService


def test_local_storage_save_and_retrieve(tmp_path: Path) -> None:
    storage = LocalStorageService(base_dir=tmp_path)
    sample_data = b"sample image binary content"
    file_key, file_size = storage.save_file(
        file_data=sample_data,
        filename="product_photo.png",
        content_type="image/png",
        user_id=101,
    )
    assert file_size == len(sample_data)
    assert file_key.startswith("library/101/")
    assert file_key.endswith(".png")

    retrieved = storage.get_file(file_key)
    assert retrieved == sample_data


def test_local_storage_delete(tmp_path: Path) -> None:
    storage = LocalStorageService(base_dir=tmp_path)
    sample_data = b"delete me"
    file_key, _ = storage.save_file(
        file_data=sample_data,
        filename="temp.jpg",
        content_type="image/jpeg",
        user_id=102,
    )

    assert storage.delete_file(file_key) is True
    assert storage.get_file(file_key) is None
    # Deleting non-existent file returns False
    assert storage.delete_file(file_key) is False


def test_local_storage_path_traversal_blocked(tmp_path: Path) -> None:
    storage = LocalStorageService(base_dir=tmp_path)
    # Attempt directory traversal with ..
    with pytest.raises(ValueError, match="Path traversal attempt detected"):
        storage._resolve_safe_path("../../windows/system32/config/sam")

    with pytest.raises(ValueError, match="Path traversal attempt detected"):
        storage._resolve_safe_path("library/101/../../../secret.txt")


def test_local_storage_invalid_user_id(tmp_path: Path) -> None:
    storage = LocalStorageService(base_dir=tmp_path)
    with pytest.raises(ValueError, match="user_id must be a positive integer"):
        storage.save_file(
            file_data=b"data",
            filename="test.png",
            content_type="image/png",
            user_id=0,
        )

    with pytest.raises(ValueError, match="user_id must be a positive integer"):
        storage.save_file(
            file_data=b"data",
            filename="test.png",
            content_type="image/png",
            user_id=-10,
        )


def test_local_storage_prefix_collision_defense(tmp_path: Path) -> None:
    """Ensure paths sharing base_dir prefix string but outside directory are blocked."""
    base = tmp_path / "storage"
    base.mkdir(parents=True, exist_ok=True)
    sibling_evil = tmp_path / "storage_evil" / "leak.txt"
    sibling_evil.parent.mkdir(parents=True, exist_ok=True)
    sibling_evil.write_text("secret")

    storage = LocalStorageService(base_dir=base)
    # Trying to resolve relative path that escapes to the sibling directory
    with pytest.raises(ValueError, match="Path traversal attempt detected"):
        storage._resolve_safe_path("../storage_evil/leak.txt")


def test_local_storage_extension_sanitization(tmp_path: Path) -> None:
    """Ensure filenames with strange or malicious extensions are safely handled."""
    storage = LocalStorageService(base_dir=tmp_path)

    # 1. Traversal in filename suffix
    key1, _ = storage.save_file(
        file_data=b"binary",
        filename="fake/path/image.png",
        content_type="image/png",
        user_id=1,
    )
    assert key1.endswith(".png")

    # 2. Content-type fallback when extension is unknown
    key2, _ = storage.save_file(
        file_data=b"binary",
        filename="weird_no_ext",
        content_type="image/webp",
        user_id=1,
    )
    assert key2.endswith(".webp")

    # 3. Disallowed multi-dot or oversized extension falls back
    key3, _ = storage.save_file(
        file_data=b"binary",
        filename="hack.verylongunknownextensionnamethatexceeds10chars",
        content_type="image/jpeg",
        user_id=1,
    )
    assert key3.endswith(".jpg")


def test_mock_storage_service() -> None:
    mock_storage = MockStorageService()
    data = b"in memory buffer"
    file_key, size = mock_storage.save_file(
        file_data=data,
        filename="logo.webp",
        content_type="image/webp",
        user_id=103,
    )
    assert size == len(data)
    assert mock_storage.get_file(file_key) == data
    assert mock_storage.delete_file(file_key) is True
    assert mock_storage.get_file(file_key) is None
    assert mock_storage.delete_file(file_key) is False
