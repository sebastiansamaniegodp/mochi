"""Resolución portable de destinos web.

El catálogo vive en la configuración del usuario; este módulo no conoce ni
depende del Brain.  Los servicios son atajos de nombres, no tools.
"""

import re
import unicodedata
from difflib import SequenceMatcher
from urllib.parse import quote_plus, urlparse

from config_manager import get_config


def normalize_name(value):
    text = unicodedata.normalize("NFD", str(value or "").strip().casefold())
    text = "".join(char for char in text if unicodedata.category(char) != "Mn")
    return re.sub(r"[\s_.-]+", "", text)


def is_web_url(value):
    parsed = urlparse(str(value).strip())
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def is_domain(value):
    value = str(value).strip().rstrip("/")
    return bool(re.fullmatch(r"(?:[a-zA-Z0-9-]+\.)+[a-zA-Z]{2,}(?::\d+)?(?:/[^\s]*)?", value))


class WebDestinationResolver:
    """Convierte una intención de destino en URL, servicio o búsqueda."""

    def __init__(self, config_loader=get_config):
        self._config_loader = config_loader

    def _services(self):
        return (self._config_loader().get("services", {}) or {})

    def _find_service(self, target):
        """Return one unambiguous configured service for a human name."""

        normalized = normalize_name(target)
        candidates = []
        for service_id, service in self._services().items():
            if not isinstance(service, dict) or not service.get("url"):
                continue
            aliases = [service_id, service.get("name"), *(service.get("aliases", []) or [])]
            for alias in aliases:
                alias_normalized = normalize_name(alias)
                if alias_normalized:
                    candidates.append((service_id, service, alias_normalized))

        for service_id, service, alias in candidates:
            if normalized == alias:
                return service_id, service

        # A small spelling error (for example "netflx") is useful only when
        # it has one clearly superior configured destination.  This avoids
        # silently choosing between related services such as YouTube and
        # YouTube Music.
        scores = {}
        for service_id, service, alias in candidates:
            score = SequenceMatcher(None, normalized, alias).ratio()
            previous = scores.get(service_id)
            if previous is None or score > previous[0]:
                scores[service_id] = (score, service)
        ranked = sorted(
            ((score, service_id, service) for service_id, (score, service) in scores.items()),
            key=lambda entry: entry[0],
            reverse=True,
        )
        if not ranked or ranked[0][0] < 0.84:
            return None, None
        if len(ranked) > 1 and ranked[0][0] - ranked[1][0] < 0.08:
            return None, None
        _, service_id, service = ranked[0]
        return service_id, service

    def resolve(self, target):
        target = str(target or "").strip()
        if not target:
            return {"ok": False, "message": "No se indicó un destino web."}

        if is_web_url(target):
            return {"ok": True, "kind": "url", "url": target, "target": target}

        if is_domain(target):
            return {"ok": True, "kind": "domain", "url": f"https://{target}", "target": target}

        service_id, service = self._find_service(target)
        if service_id:
            return {
                "ok": True,
                "kind": "service",
                "service": service_id,
                "url": service["url"],
                "target": target,
            }

        return {
            "ok": True,
            "kind": "search",
            "query": target,
            "target": target,
            "message": "Destino no catalogado; se realizará una búsqueda web.",
        }

    def search_url(self, query):
        preferences = self._config_loader().get("preferences", {}) or {}
        template = preferences.get("search_url_template", "https://www.google.com/search?q={query}")
        return template.format(query=quote_plus(str(query).strip()))
