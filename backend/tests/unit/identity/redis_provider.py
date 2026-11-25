"""Robust Redis fixture helper supporting CI containers, local Redis, and isolated subprocesses."""

import os
import shutil
import subprocess
import time
from collections.abc import Generator

import pytest
from redis import Redis


def provide_test_redis(port: int, db: int = 14) -> Generator[Redis]:
    """Yield a connected, isolated Redis client.

    Order of preference:
    1. Running Redis on FORMAAI_REDIS_URL or localhost:6379 (CI service container / Docker / local daemon).
    2. Spawning redis-server subprocess on the dedicated port if binary is available.
    """
    candidates = []
    env_url = os.getenv("FORMAAI_REDIS_URL")
    if env_url:
        candidates.append(env_url)
    candidates.append("redis://127.0.0.1:6379")

    for url in candidates:
        try:
            client = Redis.from_url(url, db=db, decode_responses=True)
            if client.ping():
                client.flushdb()
                yield client
                try:
                    client.flushdb()
                    client.close()
                except Exception:
                    pass
                return
        except Exception:
            pass

    redis_exe = shutil.which("redis-server")
    if not redis_exe and os.path.exists(r"D:\Redis-7.4.0-Windows\redis-server.exe"):
        redis_exe = r"D:\Redis-7.4.0-Windows\redis-server.exe"

    if not redis_exe:
        pytest.skip(f"No running Redis service found and redis-server executable not available for port {port}")

    proc = subprocess.Popen(
        [
            redis_exe,
            "--port",
            str(port),
            "--save",
            "",
            "--appendonly",
            "no",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    client = Redis(host="127.0.0.1", port=port, db=0, decode_responses=True)
    connected = False
    for _ in range(30):
        try:
            if client.ping():
                connected = True
                break
        except Exception:
            time.sleep(0.1)

    if not connected:
        proc.kill()
        pytest.fail(f"Failed to connect to isolated test Redis server on port {port}")

    try:
        client.flushdb()
        yield client
    finally:
        try:
            client.flushdb()
            client.close()
        except Exception:
            pass
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
