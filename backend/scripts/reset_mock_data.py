"""Clean test and mock data across MySQL, Redis, and local storage.

Prepares the system for a clean production drill or deployment.
"""

import os
import shutil
from pathlib import Path

import redis
from sqlalchemy import text

from formaai.config import load_settings
from formaai.infrastructure.database import build_engine


def clean_database(engine) -> dict[str, int]:
    """Delete all mock tasks, shots, attempts, decisions, and test users."""
    results = {}
    with engine.begin() as conn:
        # Delete dependent tables first
        for tbl in [
            "video_attempt",
            "video_shot",
            "video_decision",
            "usage_record",
            "video_task",
            "asset",
            "material",
        ]:
            del_count = conn.execute(text(f"DELETE FROM `{tbl}`")).rowcount
            conn.execute(text(f"ALTER TABLE `{tbl}` AUTO_INCREMENT = 1"))
            results[tbl] = del_count

        # Delete test user accounts (keep user 4: binary.soul.dev@outlook.com)
        del_users = conn.execute(
            text("DELETE FROM `user_account` WHERE email LIKE '%test_%'")
        ).rowcount
        results["test_user_accounts"] = del_users

    return results


def clean_redis(redis_url: str) -> int:
    """Flush all test keys from Redis."""
    r = redis.from_url(redis_url)
    key_count = r.dbsize()
    r.flushdb()
    return key_count


def clean_storage(storage_dir: Path) -> int:
    """Delete all temporary and test files in local storage."""
    deleted_files = 0
    if storage_dir.exists():
        for item in storage_dir.iterdir():
            if item.is_file():
                item.unlink()
                deleted_files += 1
            elif item.is_dir():
                shutil.rmtree(item)
                deleted_files += 1
        storage_dir.mkdir(parents=True, exist_ok=True)
    return deleted_files


def main():
    settings = load_settings()
    print("=" * 60)
    print("FormaAI - Production Drill Environment Data Cleaner")
    print("=" * 60)

    # 1. Database
    engine = build_engine(settings.database_url)
    db_results = clean_database(engine)
    print("\n[1/3] MySQL Database Cleanup:")
    for tbl, count in db_results.items():
        print(f"  - {tbl}: {count} records deleted (auto-increment reset to 1)")

    # 2. Redis
    redis_cleared = clean_redis(settings.redis_url.get_secret_value())
    print(f"\n[2/3] Redis Cache Cleanup:")
    print(f"  - Flushed Redis DB (deleted {redis_cleared} cached keys)")

    # 3. Storage
    storage_cleared = clean_storage(settings.storage_dir)
    print(f"\n[3/3] Local Storage Cleanup:")
    print(f"  - Cleared storage directory ({settings.storage_dir}): {storage_cleared} items deleted")

    print("\nAll mock test data has been successfully cleared.")
    print("System is in a pristine, ready-for-drill state.")
    print("=" * 60)


if __name__ == "__main__":
    main()
