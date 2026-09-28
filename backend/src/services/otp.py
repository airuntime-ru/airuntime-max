import hashlib
import secrets
import threading
from datetime import UTC, datetime, timedelta

from redis import Redis
from redis.exceptions import RedisError

from src.core.config import settings

OTP_PREFIX = "auth-otp:"
MAX_ATTEMPTS = 5


def _hash_code(email: str, code: str) -> str:
    payload = f"{email.lower()}:{code}:{settings.jwt_secret_key}"
    return hashlib.sha256(payload.encode()).hexdigest()


class _MemoryOtpStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._entries: dict[str, tuple[str, datetime, int]] = {}

    def set(self, email: str, code: str, ttl_seconds: int) -> None:
        expires_at = datetime.now(UTC) + timedelta(seconds=ttl_seconds)
        with self._lock:
            self._entries[email.lower()] = (_hash_code(email, code), expires_at, 0)

    def verify(self, email: str, code: str) -> bool:
        key = email.lower()
        with self._lock:
            entry = self._entries.get(key)
            if not entry:
                return False
            code_hash, expires_at, attempts = entry
            if datetime.now(UTC) > expires_at:
                self._entries.pop(key, None)
                return False
            if attempts >= MAX_ATTEMPTS:
                self._entries.pop(key, None)
                return False
            if code_hash != _hash_code(email, code):
                self._entries[key] = (code_hash, expires_at, attempts + 1)
                return False
            self._entries.pop(key, None)
            return True


class _RedisOtpStore:
    def __init__(self, client: Redis) -> None:
        self._client = client

    def _key(self, email: str) -> str:
        return f"{OTP_PREFIX}{email.lower()}"

    def set(self, email: str, code: str, ttl_seconds: int) -> None:
        self._client.setex(self._key(email), ttl_seconds, f"{_hash_code(email, code)}:0")

    def verify(self, email: str, code: str) -> bool:
        key = self._key(email)
        raw = self._client.get(key)
        if not raw:
            return False
        code_hash, attempts_raw = raw.split(":", 1)
        attempts = int(attempts_raw)
        if attempts >= MAX_ATTEMPTS:
            self._client.delete(key)
            return False
        if code_hash != _hash_code(email, code):
            ttl = self._client.ttl(key)
            if ttl > 0:
                self._client.setex(key, ttl, f"{code_hash}:{attempts + 1}")
            return False
        self._client.delete(key)
        return True


class OtpService:
    def __init__(self) -> None:
        self._store: _MemoryOtpStore | _RedisOtpStore
        if settings.debug:
            self._store = _MemoryOtpStore()
            return
        try:
            client = Redis.from_url(
                settings.redis_url,
                decode_responses=True,
                socket_connect_timeout=0.2,
                socket_timeout=0.2,
            )
            client.ping()
            self._store = _RedisOtpStore(client)
        except RedisError:
            self._store = _MemoryOtpStore()

    def issue_code(self) -> str:
        return f"{secrets.randbelow(900_000) + 100_000:06d}"

    def store(self, email: str, code: str, ttl_seconds: int) -> None:
        self._store.set(email, code, ttl_seconds)

    def verify(self, email: str, code: str) -> bool:
        return self._store.verify(email, code)


otp_service = OtpService()
