"""Redis Session management with Lua-atomic rotation and replay mitigation."""

import time
from dataclasses import dataclass
from enum import IntEnum
from typing import Any, cast

from redis import Redis
from redis.commands.core import Script

LUA_CREATE_SESSION = """
local key = KEYS[1]
if redis.call("EXISTS", key) == 1 then
    return 0
end

redis.call("HSET", key,
    "user_id", ARGV[1],
    "account_version", ARGV[2],
    "refresh_digest", ARGV[3],
    "absolute_expires_at", ARGV[4],
    "refresh_expires_at", ARGV[5]
)
redis.call("EXPIRE", key, tonumber(ARGV[6]))
return 1
"""

LUA_ROTATE_SESSION = """
local key = KEYS[1]
if redis.call("EXISTS", key) == 0 then
    return 0
end

local user_id = redis.call("HGET", key, "user_id")
local ver = redis.call("HGET", key, "account_version")
if user_id ~= ARGV[1] or ver ~= ARGV[2] then
    redis.call("DEL", key)
    return -2
end

local abs_exp = tonumber(redis.call("HGET", key, "absolute_expires_at") or "0")
local now = tonumber(ARGV[7])
if abs_exp > 0 and now >= abs_exp then
    redis.call("DEL", key)
    return -3
end

local current_digest = redis.call("HGET", key, "refresh_digest")
if current_digest ~= ARGV[3] then
    -- Replay attack detected: revoke session family immediately
    redis.call("DEL", key)
    return -1
end

redis.call("HSET", key,
    "refresh_digest", ARGV[4],
    "refresh_expires_at", ARGV[5]
)
redis.call("EXPIRE", key, tonumber(ARGV[6]))
return 1
"""

LUA_REVOKE_SESSION = """
local key = KEYS[1]
if redis.call("EXISTS", key) == 0 then
    return 1
end

local expected_user = ARGV[1]
if expected_user and expected_user ~= "" then
    local current_user = redis.call("HGET", key, "user_id")
    if current_user ~= expected_user then
        return 0
    end
end

redis.call("DEL", key)
return 1
"""

LUA_VERIFY_SESSION = """
local key = KEYS[1]
if redis.call("EXISTS", key) == 0 then
    return 0
end

local user_id = redis.call("HGET", key, "user_id")
local ver = redis.call("HGET", key, "account_version")

if user_id == ARGV[1] and ver == ARGV[2] then
    return 1
else
    return 0
end
"""


class RotationResult(IntEnum):
    """Result code of an atomic session rotation."""

    SUCCESS = 1
    NOT_FOUND = 0
    REPLAY_DETECTED = -1
    VERSION_MISMATCH = -2
    ABSOLUTE_LIFETIME_EXCEEDED = -3


@dataclass(frozen=True)
class SessionData:
    """Strongly-typed representation of an active session hash in Redis."""

    session_id: str
    user_id: int
    account_version: int
    refresh_digest: str
    absolute_expires_at: int
    refresh_expires_at: int


class SessionManager:
    """Manage Redis session storage, atomic Lua rotation, and replay detection."""

    def __init__(
        self,
        redis_client: Redis,
        *,
        app_env: str = "production",
        idle_ttl_seconds: int = 7 * 86400,
        absolute_ttl_seconds: int = 30 * 86400,
    ) -> None:
        self.redis = redis_client
        self.app_env = app_env
        self.idle_ttl_seconds = idle_ttl_seconds
        self.absolute_ttl_seconds = absolute_ttl_seconds

        self._create_script: Script = self.redis.register_script(LUA_CREATE_SESSION)
        self._rotate_script: Script = self.redis.register_script(LUA_ROTATE_SESSION)
        self._revoke_script: Script = self.redis.register_script(LUA_REVOKE_SESSION)
        self._verify_script: Script = self.redis.register_script(LUA_VERIFY_SESSION)

    def format_key(self, session_id: str) -> str:
        """Format namespaced Redis key for session hash."""
        return f"formaai:{self.app_env}:auth:session:{session_id}"

    def create_session(
        self,
        session_id: str,
        user_id: int,
        version: int,
        refresh_digest: str,
        *,
        now: int | None = None,
    ) -> bool:
        """Create new session hash with idle and absolute expiration TTL."""
        current_time = now if now is not None else int(time.time())
        absolute_expires_at = current_time + self.absolute_ttl_seconds
        refresh_expires_at = min(
            current_time + self.idle_ttl_seconds, absolute_expires_at
        )
        ttl = max(1, refresh_expires_at - current_time)

        key = self.format_key(session_id)
        result: Any = self._create_script(
            keys=[key],
            args=[
                str(user_id),
                str(version),
                refresh_digest,
                str(absolute_expires_at),
                str(refresh_expires_at),
                str(ttl),
            ],
        )
        return int(result) == 1

    def rotate_session(
        self,
        session_id: str,
        user_id: int,
        version: int,
        old_digest: str,
        new_digest: str,
        *,
        now: int | None = None,
    ) -> RotationResult:
        """Atomically verify old digest, rotate to new digest, or punish replay."""
        current_time = now if now is not None else int(time.time())
        key = self.format_key(session_id)

        raw_abs = cast(str | bytes | None, self.redis.hget(key, "absolute_expires_at"))
        if raw_abs is not None:
            val_str = raw_abs.decode() if isinstance(raw_abs, bytes) else str(raw_abs)
            absolute_expires_at = int(val_str)
        else:
            absolute_expires_at = current_time + self.absolute_ttl_seconds

        new_refresh_expires_at = min(
            current_time + self.idle_ttl_seconds, absolute_expires_at
        )
        new_ttl = max(1, new_refresh_expires_at - current_time)

        result: Any = self._rotate_script(
            keys=[key],
            args=[
                str(user_id),
                str(version),
                old_digest,
                new_digest,
                str(new_refresh_expires_at),
                str(new_ttl),
                str(current_time),
            ],
        )
        return RotationResult(int(result))

    def revoke_session(self, session_id: str, user_id: int | None = None) -> bool:
        """Revoke a session cleanly, optionally ensuring user ownership."""
        key = self.format_key(session_id)
        expected_user = str(user_id) if user_id is not None else ""
        result: Any = self._revoke_script(keys=[key], args=[expected_user])
        return int(result) == 1

    def verify_session(self, session_id: str, user_id: int, version: int) -> bool:
        """Fast session liveness and version check for Access Token evaluation."""
        key = self.format_key(session_id)
        result: Any = self._verify_script(keys=[key], args=[str(user_id), str(version)])
        return int(result) == 1

    def get_session(self, session_id: str) -> SessionData | None:
        """Fetch strongly-typed session data from Redis."""
        key = self.format_key(session_id)
        raw = cast(dict[Any, Any], self.redis.hgetall(key))
        if not raw:
            return None

        data: dict[str, str] = {
            (k.decode() if isinstance(k, bytes) else str(k)): (
                v.decode() if isinstance(v, bytes) else str(v)
            )
            for k, v in raw.items()
        }

        return SessionData(
            session_id=session_id,
            user_id=int(data["user_id"]),
            account_version=int(data["account_version"]),
            refresh_digest=data["refresh_digest"],
            absolute_expires_at=int(data["absolute_expires_at"]),
            refresh_expires_at=int(data["refresh_expires_at"]),
        )
