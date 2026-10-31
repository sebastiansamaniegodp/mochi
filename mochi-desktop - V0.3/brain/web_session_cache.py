"""Caché breve y no sensible del contexto web activo.

La caché guarda únicamente metadatos de navegación (destino, perfil solicitado
y título observado). Nunca serializa elementos UIA, cookies, credenciales ni
contenido de formularios. Las referencias UIA siguen viviendo solo dentro de
un snapshot en memoria.
"""
import json
import time
import unicodedata
from pathlib import Path
from urllib.parse import urlparse

from config_manager import get_config
from mochi_environment import environment


DEFAULT_TTL_SECONDS = 900


def _normal(value):
    text = unicodedata.normalize("NFD", str(value or "").strip().casefold())
    return "".join(char for char in text if unicodedata.category(char) != "Mn")


class WebSessionCache:
    """Mantiene una única sesión web activa y verificable por dispositivo."""

    def __init__(self, path=None, ttl_seconds=None, config_loader=get_config, clock=time.time):
        self.path = Path(path) if path else environment.cache_dir / "web_session.json"
        self._ttl_seconds = ttl_seconds
        self._config_loader = config_loader
        self._clock = clock

    def _ttl(self):
        if self._ttl_seconds is not None:
            return max(60, int(self._ttl_seconds))
        try:
            value = self._config_loader().get("preferences", {}).get("web_session_cache_ttl_seconds", DEFAULT_TTL_SECONDS)
            return max(60, int(value))
        except Exception:
            return DEFAULT_TTL_SECONDS

    def _read(self):
        try:
            with self.path.open("r", encoding="utf-8") as file:
                record = json.load(file)
        except (OSError, ValueError, TypeError):
            return None
        if not isinstance(record, dict) or record.get("expires_at", 0) <= self._clock():
            self.clear()
            return None
        return record

    def _write(self, record):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(".tmp")
            with temporary.open("w", encoding="utf-8") as file:
                json.dump(record, file, ensure_ascii=False, separators=(",", ":"))
            temporary.replace(self.path)
        except OSError:
            # Cache availability must never prevent normal browser use.
            pass

    def clear(self):
        try:
            self.path.unlink(missing_ok=True)
        except OSError:
            pass

    def begin(self, url, target, requested_profile, browser_result):
        """Start a short-lived context after browser_open launches Chrome."""
        now = self._clock()
        aliases = [requested_profile, browser_result.get("profile_id"), browser_result.get("profile_name")]
        self._write({
            "version": 1,
            "url": str(url or ""),
            "target": str(target or ""),
            "profile_aliases": [_normal(alias) for alias in aliases if alias],
            "created_at": now,
            "last_observed_at": None,
            "expires_at": now + self._ttl(),
            "page": None,
        })

    def find(self, url, requested_profile=None):
        """Return a live matching record, but never trust it without UIA check."""
        record = self._read()
        if not record or str(record.get("url", "")) != str(url or ""):
            return None
        # Without an explicit requested browser profile we cannot prove that a
        # cached Marian/Work/etc. page is the caller's default-profile intent.
        # Prefer opening Chrome normally over risking the wrong account.
        if requested_profile is None:
            return None
        profile = _normal(requested_profile)
        if profile and profile not in record.get("profile_aliases", []):
            return None
        # begin() intentionally writes page=None until the first UIA snapshot.
        # A normal cache miss must never make browser_open fail.
        page = record.get("page") or {}
        return record if page.get("title") else None

    @staticmethod
    def matches_observation(record, observation):
        """Require visible evidence that the active Chrome page is the cached site."""
        if not record or not observation.get("ok"):
            return False
        title = _normal((observation.get("page") or {}).get("title"))
        cached_title = _normal((record.get("page") or {}).get("title"))
        target = _normal(record.get("target"))
        hostname = _normal((urlparse(str(record.get("url", ""))).hostname or "").split(".")[0])
        return bool(title and (title == cached_title or (target and target in title) or (hostname and hostname in title)))

    def observe(self, observation):
        """Refresh the TTL only when the inspected page matches cached context."""
        record = self._read()
        if not self.matches_observation(record, observation):
            return False
        now = self._clock()
        record["page"] = {
            "title": str((observation.get("page") or {}).get("title") or ""),
            "url": (observation.get("page") or {}).get("url"),
        }
        record["last_observed_at"] = now
        record["expires_at"] = now + self._ttl()
        self._write(record)
        return True

    def mark_observed_after_open(self, observation):
        """Record the first UIA observation after begin, if it belongs to the target."""
        record = self._read()
        if not record or not observation.get("ok"):
            return False
        title = _normal((observation.get("page") or {}).get("title"))
        target = _normal(record.get("target"))
        hostname = _normal((urlparse(str(record.get("url", ""))).hostname or "").split(".")[0])
        if not title or not ((target and target in title) or (hostname and hostname in title)):
            return False
        now = self._clock()
        record["page"] = {"title": str((observation.get("page") or {}).get("title") or ""), "url": (observation.get("page") or {}).get("url")}
        record["last_observed_at"] = now
        record["expires_at"] = now + self._ttl()
        self._write(record)
        return True
