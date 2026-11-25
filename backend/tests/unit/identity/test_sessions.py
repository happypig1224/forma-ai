"""Unit and concurrency tests for Phase 2 Redis session management and Lua rotation."""

import concurrent.futures
import shutil
import subprocess
import time
from collections.abc import Generator

import pytest
from redis import Redis

from formaai.modules.identity.sessions import (
    RotationResult,
    SessionData,
    SessionManager,
)
from .redis_provider import provide_test_redis

TEST_REDIS_PORT = 6389


@pytest.fixture(scope="module")
def isolated_redis_server() -> Generator[Redis]:
    """Launch an isolated real Redis server or connect to available test Redis."""
    yield from provide_test_redis(TEST_REDIS_PORT, db=11)


@pytest.fixture
def session_manager(isolated_redis_server: Redis) -> SessionManager:
    """Create a SessionManager pointed at the isolated test Redis."""
    isolated_redis_server.flushdb()
    return SessionManager(
        redis_client=isolated_redis_server,
        app_env="test",
        idle_ttl_seconds=7 * 86400,
        absolute_ttl_seconds=30 * 86400,
    )


def test_create_session_success_and_ttl(session_manager: SessionManager):
    """AC-P2-1: Create session writes hash and sets TTL."""
    sid = "test-session-1"
    now = int(time.time())
    created = session_manager.create_session(
        session_id=sid,
        user_id=101,
        version=1,
        refresh_digest="digest-hash-1111",
        now=now,
    )
    assert created is True

    # Duplicate sid creation fails
    duplicate = session_manager.create_session(
        session_id=sid,
        user_id=101,
        version=1,
        refresh_digest="digest-hash-2222",
        now=now,
    )
    assert duplicate is False

    # Verify session data
    data = session_manager.get_session(sid)
    assert data is not None
    assert isinstance(data, SessionData)
    assert data.session_id == sid
    assert data.user_id == 101
    assert data.account_version == 1
    assert data.refresh_digest == "digest-hash-1111"
    assert data.absolute_expires_at == now + 30 * 86400
    assert data.refresh_expires_at == now + 7 * 86400

    # Verify session liveness
    assert session_manager.verify_session(sid, user_id=101, version=1) is True
    # Wrong version or wrong user returns False
    assert session_manager.verify_session(sid, user_id=101, version=2) is False
    assert session_manager.verify_session(sid, user_id=999, version=1) is False


def test_rotate_session_success(session_manager: SessionManager):
    """AC-P2-2: Normal rotation updates digest and refreshes TTL."""
    sid = "test-session-rotate-success"
    now = int(time.time())
    session_manager.create_session(
        session_id=sid,
        user_id=202,
        version=0,
        refresh_digest="old-digest-value",
        now=now,
    )

    result = session_manager.rotate_session(
        session_id=sid,
        user_id=202,
        version=0,
        old_digest="old-digest-value",
        new_digest="new-digest-value",
        now=now + 100,
    )
    assert result == RotationResult.SUCCESS

    # Verify updated session
    data = session_manager.get_session(sid)
    assert data is not None
    assert data.refresh_digest == "new-digest-value"
    assert data.refresh_expires_at == now + 100 + 7 * 86400


def test_rotate_session_not_found(session_manager: SessionManager):
    """AC-P2-2: Rotating non-existent session returns NOT_FOUND."""
    result = session_manager.rotate_session(
        session_id="non-existent-sid",
        user_id=303,
        version=0,
        old_digest="any-digest",
        new_digest="new-digest",
    )
    assert result == RotationResult.NOT_FOUND


def test_rotate_session_replay_detected_and_punished(
    session_manager: SessionManager,
):
    """AC-P2-3: Stale digest triggers replay mitigation and revokes session."""
    sid = "test-session-replay"
    now = int(time.time())
    session_manager.create_session(
        session_id=sid,
        user_id=404,
        version=0,
        refresh_digest="digest-v1",
        now=now,
    )

    # First rotation succeeds: digest-v1 -> digest-v2
    res1 = session_manager.rotate_session(
        session_id=sid,
        user_id=404,
        version=0,
        old_digest="digest-v1",
        new_digest="digest-v2",
        now=now + 50,
    )
    assert res1 == RotationResult.SUCCESS

    # Attack: Attacker (or replayed request) presents digest-v1 again!
    res_replay = session_manager.rotate_session(
        session_id=sid,
        user_id=404,
        version=0,
        old_digest="digest-v1",
        new_digest="digest-attacker",
        now=now + 60,
    )
    assert res_replay == RotationResult.REPLAY_DETECTED

    # Replay penalty: The entire session family MUST be deleted!
    assert session_manager.get_session(sid) is None
    assert session_manager.verify_session(sid, user_id=404, version=0) is False


def test_rotate_session_version_mismatch(session_manager: SessionManager):
    """AC-P2-4: Rotation with mismatched user version revokes session."""
    sid = "test-session-version-mismatch"
    now = int(time.time())
    session_manager.create_session(
        session_id=sid,
        user_id=505,
        version=0,
        refresh_digest="digest-initial",
        now=now,
    )

    # User changed password: account version in MySQL is now 1, token has 0
    res = session_manager.rotate_session(
        session_id=sid,
        user_id=505,
        version=1,
        old_digest="digest-initial",
        new_digest="digest-next",
        now=now,
    )
    assert res == RotationResult.VERSION_MISMATCH
    assert session_manager.get_session(sid) is None


def test_rotate_session_absolute_lifetime_exceeded(
    session_manager: SessionManager,
):
    """AC-P2-4: Rotating past absolute session lifetime revokes session."""
    sid = "test-session-absolute-expiry"
    now = int(time.time())
    session_manager.create_session(
        session_id=sid,
        user_id=606,
        version=0,
        refresh_digest="digest-initial",
        now=now,
    )

    # Attempt rotation after 31 days (absolute expiry was 30 days)
    res = session_manager.rotate_session(
        session_id=sid,
        user_id=606,
        version=0,
        old_digest="digest-initial",
        new_digest="digest-next",
        now=now + 31 * 86400,
    )
    assert res == RotationResult.ABSOLUTE_LIFETIME_EXCEEDED
    assert session_manager.get_session(sid) is None


def test_revoke_session_single_and_cross_user_protection(
    session_manager: SessionManager,
):
    """AC-P2-6: Normal revocation succeeds; cross-user revocation is denied."""
    sid = "test-session-revoke"
    session_manager.create_session(
        session_id=sid,
        user_id=707,
        version=0,
        refresh_digest="digest-val",
    )

    # Unauthorized user tries to revoke someone else's sid
    assert session_manager.revoke_session(sid, user_id=999) is False
    assert session_manager.get_session(sid) is not None

    # Authorized user revokes their sid
    assert session_manager.revoke_session(sid, user_id=707) is True
    assert session_manager.get_session(sid) is None

    # Idempotent second revocation succeeds
    assert session_manager.revoke_session(sid, user_id=707) is True


def test_concurrent_refresh_rotation_race_condition(
    session_manager: SessionManager,
):
    """AC-P2-5: Real multithreaded concurrent rotation with same old_digest.

    Exactly one thread must succeed (SUCCESS), while concurrent duplicates
    must be flagged as replay (REPLAY_DETECTED), leaving the session cleanly
    cleared.
    """
    sid = "concurrent-race-session"
    now = int(time.time())
    shared_digest = "shared-original-digest"

    session_manager.create_session(
        session_id=sid,
        user_id=808,
        version=0,
        refresh_digest=shared_digest,
        now=now,
    )

    num_threads = 5
    results: list[RotationResult] = []

    def attempt_rotation(idx: int) -> RotationResult:
        return session_manager.rotate_session(
            session_id=sid,
            user_id=808,
            version=0,
            old_digest=shared_digest,
            new_digest=f"new-digest-from-thread-{idx}",
            now=now + 10,
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as executor:
        futures = [executor.submit(attempt_rotation, i) for i in range(num_threads)]
        for f in concurrent.futures.as_completed(futures):
            results.append(f.result())

    # Exactly one thread succeeds
    success_count = results.count(RotationResult.SUCCESS)
    replay_count = results.count(RotationResult.REPLAY_DETECTED)
    not_found_count = results.count(RotationResult.NOT_FOUND)

    assert success_count == 1
    # The others must either detect replay or find it already cleared
    assert replay_count + not_found_count == num_threads - 1

    # In the end, because replay was detected, the session must be completely revoked
    assert session_manager.get_session(sid) is None
