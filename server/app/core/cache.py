"""
缓存抽象层。

配置了 REDIS_URL 时使用 Redis（多实例共享）；否则使用带 TTL 的
进程内内存缓存，保证无 Redis 环境下行为一致。
"""
from __future__ import annotations

import json
import threading
import time
from typing import Any

from app.config import settings


class _MemoryCache:
    """线程安全的内存缓存，支持 TTL 与容量上限。"""

    def __init__(self, max_size: int = 10_000) -> None:
        self._data: dict[str, tuple[Any, float]] = {}
        self._lock = threading.RLock()
        self._max_size = max_size

    def _purge(self) -> None:
        now = time.time()
        expired = [k for k, (_, exp) in self._data.items() if exp < now]
        for k in expired:
            self._data.pop(k, None)
        # 容量保护：淘汰最早过期的一批
        if len(self._data) > self._max_size:
            for k, _ in sorted(self._data.items(), key=lambda kv: kv[1][1])[
                : len(self._data) - self._max_size
            ]:
                self._data.pop(k, None)

    def get(self, key: str) -> Any | None:
        with self._lock:
            item = self._data.get(key)
            if item is None:
                return None
            value, exp = item
            if exp < time.time():
                self._data.pop(key, None)
                return None
            return value

    def setex(self, key: str, ttl: int, value: Any) -> None:
        with self._lock:
            self._data[key] = (value, time.time() + ttl)
            if len(self._data) > self._max_size:
                self._purge()

    def delete(self, key: str) -> None:
        with self._lock:
            self._data.pop(key, None)

    def incr(self, key: str, ttl: int = 60) -> int:
        """自增计数。固定窗口语义：TTL 仅在首次创建时生效，不随后续自增重置。

        若每次自增都重置 TTL，限频窗口会变成"最后一次尝试起算"，
        用户反复点击将永远等不到窗口结束（2026-09 实测 1005 反复触发的根因）。
        """
        with self._lock:
            item = self._data.get(key)
            if item is None or item[1] < time.time():
                self._data[key] = (1, time.time() + ttl)
                return 1
            value, exp = item
            cur = int(value) + 1
            self._data[key] = (cur, exp)
            return cur

    def ttl(self, key: str) -> int:
        """返回 key 剩余存活秒数；不存在或已过期返回 0。"""
        with self._lock:
            item = self._data.get(key)
            if item is None:
                return 0
            _, exp = item
            return max(0, int(exp - time.time()))


class Cache:
    """统一缓存门面，自动选择后端。"""

    def __init__(self) -> None:
        self._redis = None
        self._mem = _MemoryCache()
        self._init_redis()

    def _init_redis(self) -> None:
        if not settings.REDIS_URL:
            return
        try:
            import redis  # type: ignore

            self._redis = redis.Redis.from_url(
                settings.REDIS_URL, decode_responses=True, socket_timeout=1.0
            )
            self._redis.ping()
        except Exception:
            # Redis 不可用时静默降级，不影响主流程
            self._redis = None

    @property
    def backend(self) -> str:
        return "redis" if self._redis is not None else "memory"

    def get_json(self, key: str) -> Any | None:
        raw = None
        if self._redis is not None:
            try:
                raw = self._redis.get(key)
            except Exception:
                raw = None
        else:
            raw = self._mem.get(key)
        if raw is None:
            return None
        if isinstance(raw, (dict, list)):
            return raw
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return None

    def setex_json(self, key: str, ttl: int, value: Any) -> None:
        payload = json.dumps(value, ensure_ascii=False)
        if self._redis is not None:
            try:
                self._redis.setex(key, ttl, payload)
                return
            except Exception:
                pass
        self._mem.setex(key, ttl, payload)

    def incr(self, key: str, ttl: int = 60) -> int:
        if self._redis is not None:
            try:
                # SET NX：TTL 仅在首次创建时生效（固定窗口），后续自增不重置
                created = self._redis.set(key, 1, ex=ttl, nx=True)
                if created:
                    return 1
                return int(self._redis.incr(key))
            except Exception:
                pass
        return self._mem.incr(key, ttl)

    def ttl(self, key: str) -> int:
        """返回 key 剩余存活秒数；不存在返回 0。"""
        if self._redis is not None:
            try:
                return max(0, int(self._redis.ttl(key)))
            except Exception:
                pass
        return self._mem.ttl(key)

    def delete(self, key: str) -> None:
        if self._redis is not None:
            try:
                self._redis.delete(key)
            except Exception:
                pass
        self._mem.delete(key)


cache = Cache()
