"""
HTTP response caching backed by Redis.

All external service calls must go through get_or_fetch() so that:
  - Repeated requests within TTL return cached JSON without a network hit.
  - Cache keys are deterministic: sha256(source + url + sorted params).
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Any

import redis
import requests
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.config import get_settings

logger = logging.getLogger(__name__)

_redis_client: redis.Redis | None = None


def _get_redis() -> redis.Redis:
    global _redis_client
    if _redis_client is None:
        settings = get_settings()
        _redis_client = redis.from_url(settings.redis_url, decode_responses=True)
    return _redis_client


def _cache_key(source: str, url: str, params: dict | None) -> str:
    payload = json.dumps(
        {"source": source, "url": url, "params": params or {}},
        sort_keys=True,
    )
    return "drug_intel:cache:" + hashlib.sha256(payload.encode()).hexdigest()


@retry(
    retry=retry_if_exception_type((requests.Timeout, requests.ConnectionError)),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    stop=stop_after_attempt(3),
    reraise=True,
)
def _do_http_get(url: str, params: dict | None, timeout: int) -> requests.Response:
    resp = requests.get(url, params=params, timeout=timeout)
    resp.raise_for_status()
    return resp


def get_or_fetch(
    source: str,
    url: str,
    params: dict | None = None,
    headers: dict | None = None,
    ttl: int | None = None,
    *,
    raw_text: bool = False,
) -> Any:
    """
    Fetch JSON (or raw text) from `url`, caching in Redis for `ttl` seconds.

    Parameters
    ----------
    source  : Logical source name (e.g. 'pubchem', 'chembl') – part of cache key.
    url     : Full request URL.
    params  : Query parameters dict.
    headers : Extra HTTP headers (not part of cache key — keep static).
    ttl     : Override default cache TTL (seconds).
    raw_text: If True, cache/return raw response text instead of parsed JSON.
    """
    settings = get_settings()
    effective_ttl = ttl if ttl is not None else settings.cache_ttl_seconds
    key = _cache_key(source, url, params)

    try:
        rdb = _get_redis()
        cached = rdb.get(key)
        if cached is not None:
            logger.debug("cache_hit source=%s url=%s", source, url)
            if raw_text:
                return cached
            return json.loads(cached)
    except Exception as exc:
        logger.warning("redis_cache_read_error source=%s err=%s", source, exc)

    logger.debug("cache_miss source=%s url=%s", source, url)
    t0 = time.monotonic()
    resp = _do_http_get(url, params, settings.http_timeout)
    elapsed = time.monotonic() - t0
    logger.info(
        "http_fetch source=%s status=%s elapsed_ms=%.0f url=%s",
        source, resp.status_code, elapsed * 1000, url,
    )

    data: Any
    if raw_text:
        data = resp.text
        to_cache = data
    else:
        try:
            data = resp.json()
        except Exception:
            data = resp.text
        to_cache = json.dumps(data)

    try:
        rdb = _get_redis()
        rdb.setex(key, effective_ttl, to_cache)
    except Exception as exc:
        logger.warning("redis_cache_write_error source=%s err=%s", source, exc)

    return data


def invalidate(source: str, url: str, params: dict | None = None) -> bool:
    """Remove a cached entry. Returns True if key existed."""
    key = _cache_key(source, url, params)
    try:
        rdb = _get_redis()
        return bool(rdb.delete(key))
    except Exception as exc:
        logger.warning("redis_invalidate_error source=%s err=%s", source, exc)
        return False
