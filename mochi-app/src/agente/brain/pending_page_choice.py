"""Short-lived persisted context for a user-facing ambiguous page choice.

The agent process can be launched once per spoken command.  A choice such as
"reproduce the third" therefore needs a tiny semantic bridge across process
boundaries.  This cache deliberately stores only result labels and page
metadata: never UIA ids, DOM references, cookies, credentials or form data.
The next command must still inspect the current page and rediscover the same
choices before any control can be activated.
"""

import json
import time
from pathlib import Path

from config_manager import get_config
from mochi_environment import environment


DEFAULT_TTL_SECONDS = 900
RECORD_VERSION = 3


class PendingPageChoiceCache:
    """Persist one explicit, expiring choice list for the active web task."""

    def __init__(self, path=None, ttl_seconds=None, config_loader=get_config, clock=time.time):
        self.path = Path(path) if path else environment.cache_dir / "pending_page_choice.json"
        self._ttl_seconds = ttl_seconds
        self._config_loader = config_loader
        self._clock = clock

    def _ttl(self):
        if self._ttl_seconds is not None:
            return max(60, int(self._ttl_seconds))
        try:
            value = self._config_loader().get("preferences", {}).get(
                "pending_page_choice_ttl_seconds", DEFAULT_TTL_SECONDS
            )
            return max(60, int(value))
        except Exception:
            return DEFAULT_TTL_SECONDS

    def _read(self):
        try:
            with self.path.open("r", encoding="utf-8") as file:
                record = json.load(file)
        except (OSError, ValueError, TypeError):
            return None
        # Version 1 could preserve autosuggestions as though they were media
        # choices.  Those records are unsafe to continue after the media-card
        # distinction was introduced, so discard them instead of letting a
        # later "reproduce el primero" activate a search suggestion.
        if (
            not isinstance(record, dict)
            or record.get("version") != RECORD_VERSION
            or record.get("expires_at", 0) <= self._clock()
        ):
            self.clear()
            return None
        options = record.get("options")
        if not isinstance(options, list) or not options:
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
            # Memory continuity is helpful, never a requirement for normal
            # browser interaction.
            pass

    def remember(
        self, *, page, target, query, options,
        selection_operation="select_result", continuation=None,
    ):
        """Save an ordered public option list after ambiguity was reported."""
        cleaned = []
        seen = set()
        for option in options or []:
            name = str((option or {}).get("name") or "").strip()
            key = name.casefold()
            if not name or key in seen:
                continue
            seen.add(key)
            cleaned.append({"name": name})
        if len(cleaned) < 2:
            return False
        now = self._clock()
        self._write({
            "version": RECORD_VERSION,
            "created_at": now,
            "expires_at": now + self._ttl(),
            "page": {
                "title": str((page or {}).get("title") or ""),
                "url": (page or {}).get("url"),
            },
            "target": str(target or ""),
            "query": str(query or ""),
            "options": cleaned[:5],
            "selection_operation": str(selection_operation or "select_result"),
            "continuation": [
                {
                    "operation": str(item.get("operation") or ""),
                    "scope": str(item.get("scope") or "web_page"),
                    "value": item.get("value"),
                }
                for item in (continuation or [])
                if isinstance(item, dict) and item.get("operation")
            ][:12],
        })
        return True

    def current(self):
        record = self._read()
        if not record:
            return None
        # Do not leak mutable internal state to callers.
        return {
            **record,
            "page": dict(record.get("page") or {}),
            "options": [dict(option) for option in record.get("options") or []],
            "continuation": [
                dict(item) for item in record.get("continuation") or []
                if isinstance(item, dict)
            ],
        }

    def clear(self):
        try:
            self.path.unlink(missing_ok=True)
        except OSError:
            pass
