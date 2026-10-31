"""Capabilities web expuestas al Brain."""
import os
import time

from actions.browser import BrowserController
from brain.web_destinations import WebDestinationResolver
if os.name != "nt":
    # Linux: el mismo inspector, por AT-SPI en lugar de UI Automation.
    from linux_platform.web import WebInspector, WebInteractor
else:
    from web_interaction.inspector import WebInspector
    from web_interaction.interactor import WebInteractor
from brain.web_session_cache import WebSessionCache
from brain.web_session import WebSession


_browser = BrowserController()
_resolver = WebDestinationResolver()
_web_inspector = WebInspector()
_web_interactor = WebInteractor(_web_inspector)
_web_session_cache = WebSessionCache()
_web_session = WebSession()


def _prefer_profile_window(directory, aliases=()):
    prefer_profile = getattr(_web_inspector, "prefer_profile_directory", None)
    if not callable(prefer_profile):
        return
    try:
        prefer_profile(directory, aliases=aliases)
    except TypeError:
        # Keep lightweight test/double inspectors compatible.
        prefer_profile(directory)


def browser_open(target, profile=None):
    destination = _resolver.resolve(target)
    if not destination.get("ok"):
        return destination
    if destination["kind"] == "search":
        return browser_search(destination["query"], profile=profile, fallback_for=target)

    # UIA cannot infer a Chrome account from page content.  When the caller
    # explicitly names one, constrain subsequent inspections to top-level
    # Chrome windows whose real process command line carries that profile
    # directory.  A profile-less contextual request intentionally leaves the
    # current page preference unchanged.
    if profile is not None:
        profile_id = _browser.resolve_profile(profile)
        profile_record = _browser.get_profiles().get(profile_id, {}) if profile_id else {}
        _prefer_profile_window(
            profile_record.get("directory") or profile_id,
            aliases=(
                profile,
                profile_id,
                profile_record.get("directory"),
                profile_record.get("name"),
                profile_record.get("gaia_name"),
                profile_record.get("email"),
            ),
        )

    # A cached page is only a hint.  We always inspect the current Chrome page
    # before reusing it; stale cache data can never make us act on another tab.
    # `None` means the configured default Chrome profile, not an unknown
    # profile.  Passing that resolved identity to the cache lets a later CLI
    # invocation reuse a page it opened with that same default profile.
    requested_profile = profile or _browser.get_default_profile()
    cached = _web_session_cache.find(destination["url"], requested_profile)
    if cached:
        observation = _web_inspector.inspect()
        if _web_session_cache.matches_observation(cached, observation):
            _web_session_cache.observe(observation)
            age_seconds = int(max(0, time.time() - cached.get("created_at", 0)))
            return {
                "ok": True,
                "message": "Reutilizo la sesión web activa verificada.",
                "url": destination["url"],
                "target": destination.get("target"),
                "kind": destination.get("kind"),
                "service": destination.get("service"),
                "session_reused": True,
                "cache_age_seconds": age_seconds,
                "profile_context": "cached",
            }
        # The cached page may be in an inactive tab.  The profile was checked
        # above, then the tab is selected via UIA and inspected again before
        # any reuse is claimed.  This avoids an extra Chrome tab without
        # trusting cached UIA references or page content.
        tab_result = _web_inspector.activate_matching_tab(
            destination.get("target") or target,
            preferred_title=(cached.get("page") or {}).get("title"),
        )
        if tab_result.get("ok"):
            observation = _web_inspector.inspect()
            if _web_session_cache.matches_observation(cached, observation):
                _web_session_cache.observe(observation)
                age_seconds = int(max(0, time.time() - cached.get("created_at", 0)))
                return {
                    "ok": True,
                    "message": "Reutilizo una pestaña web existente verificada.",
                    "url": destination["url"],
                    "target": destination.get("target"),
                    "kind": destination.get("kind"),
                    "service": destination.get("service"),
                    "session_reused": True,
                    "tab_activated": True,
                    "cache_age_seconds": age_seconds,
                    "profile_context": "cached",
                }
        # A cache is a performance hint, never an authority to prevent a
        # requested navigation.  Multiple similarly named tabs cannot be
        # selected safely, but rejecting browser_open here made Chrome look
        # unavailable even though its normal URL launch was perfectly valid.
        # Drop the uncertain cache record and continue below with Chrome.
        _web_session_cache.clear()

    # If no Chrome profile was requested, a visible page that matches the
    # destination is more useful than opening a duplicate tab.  An explicit
    # profile intentionally bypasses adoption unless the verified disk cache
    # above matches it; UIA alone cannot prove a manually-opened tab's Chrome
    # profile.
    if profile is None:
        observation = _web_inspector.inspect()
        if observation.get("ok") and _web_session.matches_target(destination.get("target") or target, observation):
            _web_session.observe(observation, source="adopted_active_tab")
            return {
                "ok": True,
                "message": "Adopté la pestaña activa ya abierta.",
                "url": destination["url"],
                "target": destination.get("target"),
                "kind": destination.get("kind"),
                "service": destination.get("service"),
                "session_adopted": True,
                "profile_context": "not_requested",
            }

    result = _browser.open_url_for_profile(destination["url"], profile=profile)
    result.update({key: value for key, value in destination.items() if key in {"kind", "service", "target"}})
    if result.get("ok"):
        profile_record = _browser.get_profiles().get(result.get("profile_id"), {})
        _prefer_profile_window(
            result.get("profile_directory"),
            aliases=(
                result.get("profile_id"), result.get("profile_name"),
                result.get("profile_directory"), profile_record.get("gaia_name"),
                profile_record.get("email"),
            ),
        )
        _web_session_cache.begin(destination["url"], destination.get("target") or target, profile, result)
        # Chrome acknowledges the process/URL before its top-level window and
        # renderer Document become visible to UI Automation.  Voice commands
        # arrive as one complete transaction, so returning immediately here
        # caused the following in-page step to see ``chrome_window_not_found``
        # or ``Sin título``.  Wait only for matching page identity; content
        # controls retain their own provider-specific readiness checks.
        ready_observation = None
        for attempt in range(12):
            ready_observation = _web_inspector.inspect()
            if (
                ready_observation.get("ok")
                and _web_session.matches_target(
                    destination.get("target") or target,
                    ready_observation,
                )
            ):
                _web_session_cache.mark_observed_after_open(ready_observation)
                _web_session.observe(
                    ready_observation,
                    browser_profile=result.get("profile_id"),
                    source="browser_open_ready",
                )
                result["page_ready"] = True
                break
            if attempt < 11:
                time.sleep(0.5)
        if not result.get("page_ready"):
            result["page_ready"] = False
    return result


def browser_search(query, profile=None, fallback_for=None):
    query = str(query or "").strip()
    if not query:
        return {"ok": False, "message": "No se indicó qué buscar en la web."}
    result = _browser.open_url_for_profile(_resolver.search_url(query), profile=profile)
    if result.get("ok"):
        profile_record = _browser.get_profiles().get(result.get("profile_id"), {})
        _prefer_profile_window(
            result.get("profile_directory"),
            aliases=(
                result.get("profile_id"), result.get("profile_name"),
                result.get("profile_directory"), profile_record.get("gaia_name"),
                profile_record.get("email"),
            ),
        )
    result.update({"kind": "search", "query": query})
    if fallback_for:
        result["fallback_for"] = fallback_for
    return result


def browser_profiles(action="list", profile=None):
    # Some model responses try to pass a profile name to this diagnostic
    # listing tool. It cannot select or switch a browser profile, so return a
    # structured validation error instead of leaking a Python TypeError into
    # the agent loop.
    if profile is not None:
        return {
            "ok": False,
            "error": "invalid_argument",
            "message": "browser_profiles no acepta un perfil individual; usá browser_open.profile para abrir un sitio con ese perfil.",
        }
    action = str(action or "list").strip().lower()
    if action == "active":
        profile = _browser.get_active_profile_info()
        return {"ok": bool(profile), "profile": profile, "message": "Perfil activo consultado." if profile else "No hay un perfil activo configurado."}
    if action != "list":
        return {"ok": False, "message": "Acción de perfiles no válida."}

    profiles = []
    for profile_id, profile in _browser.get_profiles().items():
        profiles.append({"id": profile_id, "name": profile.get("name"), "gaia_name": profile.get("gaia_name"), "email": profile.get("email"), "directory": profile.get("directory")})
    return {"ok": True, "profiles": profiles, "default_profile": _browser.get_default_profile(), "message": f"Hay {len(profiles)} perfiles de navegador disponibles."}


def web_inspect(query=None):
    """Snapshot compacto de la página accesible de Chrome."""
    result = _web_inspector.inspect(query=query)
    # A semantic snapshot is used only to refresh page metadata.  The cache
    # intentionally never persists element IDs or underlying UIA references.
    _web_session_cache.mark_observed_after_open(result)
    _web_session_cache.observe(result)
    _web_session.observe(result, source="inspection")
    return result


def web_session_context():
    """Diagnóstico interno: contexto semántico, nunca IDs UIA ni credenciales."""
    return _web_session.current()


def web_act(element_id, action, value=None, snapshot_id=None):
    """Ejecuta una acción UIA sobre un ID de un snapshot reciente."""
    return _web_interactor.act(element_id, action, value=value, snapshot_id=snapshot_id)
