"""Estado efímero de la conversación web activa.

No intenta conservar punteros UI Automation: éstos pertenecen a un snapshot y
pueden quedar stale después de cualquier cambio de página. Conserva solamente
la representación semántica que puede ayudar a interpretar una orden posterior
y siempre exige una nueva inspección antes de actuar.
"""
import time
import unicodedata


def _normal(value):
    text = unicodedata.normalize("NFD", str(value or "").strip().casefold())
    return "".join(char for char in text if unicodedata.category(char) != "Mn")


class WebSession:
    def __init__(self, ttl_seconds=900, clock=time.time):
        self._ttl_seconds = max(60, int(ttl_seconds))
        self._clock = clock
        self._context = None

    def observe(self, observation, browser_profile=None, source="inspection"):
        """Refresh semantic context from a real UIA observation."""
        if not observation.get("ok") or not (observation.get("page") or {}).get("title"):
            return False
        now = self._clock()
        elements = [
            {"role": item.get("role"), "name": item.get("name"), "value": item.get("value", "")}
            for item in observation.get("elements", [])[:60]
        ]
        self._context = {
            "page": {
                "title": str(observation["page"].get("title") or ""),
                "url": observation["page"].get("url"),
            },
            "browser_profile": browser_profile,
            "semantic_items": elements,
            "fingerprint": tuple((item["role"], item["name"], item["value"]) for item in elements),
            "source": source,
            "observed_at": now,
            "expires_at": now + self._ttl_seconds,
        }
        return True

    def current(self):
        if not self._context or self._context["expires_at"] <= self._clock():
            self._context = None
            return None
        return self._context.copy()

    def matches_target(self, target, observation=None):
        """Use only page-title evidence to decide whether a tab can be adopted."""
        context = self.current()
        page = (observation or {}).get("page") if observation else (context or {}).get("page")
        title = _normal((page or {}).get("title"))
        expected = _normal(target)
        return bool(title and expected and expected in title)

    def clear(self):
        self._context = None
