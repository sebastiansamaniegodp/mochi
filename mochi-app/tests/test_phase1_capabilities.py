import json
import unittest
import tempfile
import json
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import Mock, patch

from actions.browser import BrowserController
from actions import apps as app_actions
from application_registry import ApplicationRegistry
from brain.agent_loop import MochiAgentLoop
from brain.brain_provider import BrainResult
from brain.mochi_tools import (
    create_mochi_tool_registry,
    control_media,
    capture_screenshot,
    close_application_windows,
    close_window,
    control_window,
    find_folders,
    find_files,
    get_system_info,
    get_system_volume,
    launch_game,
    list_games,
    list_installed_applications,
    list_open_windows,
    list_user_folders,
    open_target,
    open_file,
    open_folder,
    open_known_folder,
    open_latest_download,
    read_clipboard,
    set_system_volume,
    show_notification,
    write_clipboard,
)
from brain.tool_registry import ToolRegistry
from brain.web_destinations import WebDestinationResolver
from brain.goal_state import plan_goal
from brain.brain_router import BrainRouter
from game_registry import GAME_CACHE_VERSION, GameRegistry
from window_manager import WindowManager
from discovery.windows import normalize_window_name
from discovery.games import is_playable_game, normalize_game_name
from web_interaction.element_store import ElementStore
from web_interaction.inspector import WebInspector
from web_interaction.interactor import WebInteractor
from brain.web_session_cache import WebSessionCache
from brain.web_session import WebSession
from brain.pending_page_choice import PendingPageChoiceCache
from brain import browser_tools, mochi_tools
from media_controller import MediaController


CONFIG = {
    "browser": {
        "executable": "chrome.exe",
        "default_profile": "Default",
        "profiles": {
            "Default": {"directory": "Default", "name": "Primary", "email": "primary@example.test", "gaia_name": "Primary"},
            "Profile 9": {"directory": "Profile 9", "name": "Isaías", "email": "isa@example.test", "gaia_name": "Isaias Gaia"},
        },
    },
    "services": {"netflix": {"url": "https://www.netflix.com"}, "youtube": {"url": "https://www.youtube.com"}, "twitch": {"url": "https://www.twitch.tv"}},
    "preferences": {"search_url_template": "https://search.example/?q={query}"},
}


class MediaSessionMatchingTests(unittest.TestCase):
    def test_media_session_requires_the_same_item_not_only_the_same_artist(self):
        self.assertTrue(MediaController._session_matches_title(
            "Chrome",
            "Don Omar - Virtual Diva (Visualizer)",
            "(933) Don Omar - Virtual Diva (Visualizer) - YouTube",
        ))
        self.assertFalse(MediaController._session_matches_title(
            "Chrome",
            "Don Omar - Virtual Diva (Visualizer)",
            "Don Omar - Duttyy Love (Visualizer) ft. Natti Natasha - YouTube",
        ))

    def test_provider_only_title_cannot_select_between_browser_sessions(self):
        self.assertFalse(MediaController._session_matches_title(
            "Chrome", "Another active video", "Netflix",
        ))

    def test_provider_only_title_requires_one_exact_browser_session(self):
        one_netflix = [{"source": "Chrome", "title": "Netflix"}]
        two_netflix = one_netflix * 2

        self.assertTrue(MediaController._unique_exact_browser_session_matches(
            "Chrome", "Netflix", "Netflix", one_netflix,
        ))
        self.assertFalse(MediaController._unique_exact_browser_session_matches(
            "Chrome", "Netflix", "Netflix", two_netflix,
        ))
        self.assertFalse(MediaController._unique_exact_browser_session_matches(
            "Spotify", "Netflix", "Netflix", one_netflix,
        ))
        self.assertFalse(MediaController._unique_exact_browser_session_matches(
            "Chrome", "Another video", "Netflix", one_netflix,
        ))

    def test_absolute_seek_outside_duration_is_rejected_but_relative_seek_clamps(self):
        absolute_target, absolute_error = MediaController._resolve_seek_target(
            0, 120, 900, False,
        )
        relative_target, relative_error = MediaController._resolve_seek_target(
            0, 120, 900, True,
        )

        self.assertEqual((absolute_target, absolute_error), (900.0, True))
        self.assertEqual((relative_target, relative_error), (120.0, False))


class WebDestinationTests(unittest.TestCase):
    def setUp(self):
        self.resolver = WebDestinationResolver(lambda: CONFIG)

    def test_urls_domains_services_and_unknowns(self):
        self.assertEqual(self.resolver.resolve("https://github.com")["url"], "https://github.com")
        self.assertEqual(self.resolver.resolve("github.com")["url"], "https://github.com")
        self.assertEqual(self.resolver.resolve("Netflix")["url"], "https://www.netflix.com")
        self.assertEqual(self.resolver.resolve("Twitch")["url"], "https://www.twitch.tv")
        unknown = self.resolver.resolve("sitio inexistente xyz")
        self.assertEqual(unknown["kind"], "search")
        self.assertEqual(unknown["query"], "sitio inexistente xyz")
        self.assertIn("Kotlin+coroutines", self.resolver.search_url("Kotlin coroutines"))

    def test_known_service_accepts_one_clear_typo_but_not_an_ambiguous_one(self):
        self.assertEqual(self.resolver.resolve("netflx")["service"], "netflix")
        self.assertEqual(self.resolver.resolve("twitc")["service"], "twitch")

    def test_profile_resolution_is_dynamic_and_accent_insensitive(self):
        controller = BrowserController()
        with patch("actions.browser.get_config", return_value=CONFIG):
            self.assertEqual(controller.resolve_profile("isaias"), "Profile 9")
            self.assertEqual(controller.resolve_profile("ISA@EXAMPLE.TEST"), "Profile 9")
            self.assertIsNone(controller.resolve_profile("perfil inexistente"))

    def test_explicit_unknown_profile_never_falls_back(self):
        controller = BrowserController()
        with patch("actions.browser.get_config", return_value=CONFIG):
            result = controller.open_url_for_profile("https://example.test", profile="no existe")
        self.assertFalse(result["ok"])
        self.assertIn("No encontré el perfil", result["message"])

    def test_explicit_profile_is_passed_to_real_chrome(self):
        controller = BrowserController()
        with patch("actions.browser.get_config", return_value=CONFIG), patch("actions.browser.get_chrome_path", return_value="chrome.exe"), patch("actions.browser.subprocess.Popen") as popen:
            result = controller.open_url_for_profile("https://www.netflix.com", profile="Isaías")
        self.assertTrue(result["ok"])
        self.assertEqual(result["profile_id"], "Profile 9")
        self.assertIn("--profile-directory=Profile 9", popen.call_args.args[0])

    def test_default_profile_is_used_when_not_requested(self):
        controller = BrowserController()
        with patch("actions.browser.get_config", return_value=CONFIG), patch("actions.browser.get_chrome_path", return_value="chrome.exe"), patch("actions.browser.subprocess.Popen") as popen:
            result = controller.open_url_for_profile("https://github.com")
        self.assertTrue(result["ok"])
        self.assertEqual(result["profile_id"], "Default")
        self.assertIn("--profile-directory=Default", popen.call_args.args[0])

    def test_browser_profiles_rejects_an_unusable_profile_argument_cleanly(self):
        result = browser_tools.browser_profiles(action="active", profile="Marian")
        self.assertEqual((result["ok"], result["error"]), (False, "invalid_argument"))

    def test_application_inventory_is_lazy_until_an_app_tool_needs_it(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch("application_registry.discover_applications", return_value=[]) as discover:
                registry = ApplicationRegistry(cache_path=Path(directory) / "applications.json")
                discover.assert_not_called()
                self.assertIsNone(registry.find("notepad"))
                discover.assert_called_once()

    def test_application_inventory_cache_reuses_a_recent_discovery(self):
        now = [1000.0]
        app = {
            "name": "Discord",
            "normalized_name": "discord",
            "type": "shortcut",
            "target": "C:/apps/Discord.lnk",
            "source": "start_menu",
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "applications.json"
            first = ApplicationRegistry(
                cache_path=path,
                ttl_seconds=60,
                clock=lambda: now[0],
                discoverer=lambda: [app],
            )
            self.assertEqual(first.list_names(), ["Discord"])

            second_discovery = Mock(return_value=[])
            second = ApplicationRegistry(
                cache_path=path,
                ttl_seconds=60,
                clock=lambda: now[0],
                discoverer=second_discovery,
            )
            self.assertEqual(second.find("discrod")["name"], "Discord")
            second_discovery.assert_not_called()

            now[0] += 61
            expired = ApplicationRegistry(
                cache_path=path,
                ttl_seconds=60,
                clock=lambda: now[0],
                discoverer=second_discovery,
            )
            self.assertIsNone(expired.find("discord"))
            second_discovery.assert_called_once()

    def test_game_inventory_is_lazy_until_a_game_tool_needs_it(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch("game_registry.discover_games", return_value=[]) as discover:
                registry = GameRegistry(cache_path=Path(directory) / "games.json")
                discover.assert_not_called()
                self.assertEqual(registry.list_names(), [])
                discover.assert_called_once()

    def test_game_inventory_cache_reuses_a_recent_discovery_across_registries(self):
        now = [1000.0]
        game = {"name": "Hades", "normalized_name": "hades", "launcher": "steam", "id": "1145360", "launch_uri": "steam://rungameid/1145360"}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "games.json"
            first_discovery = lambda: [game]
            first = GameRegistry(cache_path=path, ttl_seconds=60, clock=lambda: now[0], discoverer=first_discovery)
            self.assertEqual(first.list_names(), ["Hades"])
            second_discovery = Mock(return_value=[])
            second = GameRegistry(cache_path=path, ttl_seconds=60, clock=lambda: now[0], discoverer=second_discovery)
            self.assertEqual(second.list_names(), ["Hades"])
            second_discovery.assert_not_called()
            now[0] += 61
            expired = GameRegistry(cache_path=path, ttl_seconds=60, clock=lambda: now[0], discoverer=second_discovery)
            self.assertEqual(expired.list_names(), [])
            second_discovery.assert_called_once()

    def test_legacy_game_cache_is_migrated_without_rescanning_launchers(self):
        now = [1000.0]
        legacy_game = {
            "name": "R.E.P.O.",
            "normalized_name": "r e p o",
            "launcher": "steam",
            "id": "3241660",
            "launch_uri": "steam://rungameid/3241660",
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "games.json"
            path.write_text(
                json.dumps({"version": 1, "expires_at": 2000.0, "games": [legacy_game]}),
                encoding="utf-8",
            )
            discoverer = Mock(return_value=[])
            registry = GameRegistry(
                cache_path=path,
                clock=lambda: now[0],
                discoverer=discoverer,
            )

            self.assertEqual(registry.find("REPO")["name"], "R.E.P.O.")
            discoverer.assert_not_called()
            migrated = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(migrated["version"], GAME_CACHE_VERSION)
            self.assertEqual(migrated["games"][0]["normalized_name"], "repo")

    def test_launcher_support_package_is_not_listed_as_a_game(self):
        self.assertFalse(is_playable_game({"name": "Steamworks Common Redistributables", "launcher": "steam", "id": "228980"}))
        self.assertTrue(is_playable_game({"name": "Dead as Disco", "launcher": "steam", "id": "123"}))

    def test_dotted_game_initials_and_plain_initials_normalize_equally(self):
        self.assertEqual(normalize_game_name("R.E.P.O."), "repo")
        with tempfile.TemporaryDirectory() as directory:
            registry = GameRegistry(
                cache_path=Path(directory) / "games.json",
                discoverer=lambda: [{"name": "R.E.P.O.", "normalized_name": "repo", "launcher": "steam", "id": "123", "launch_uri": "steam://rungameid/123"}],
            )
            self.assertEqual(registry.find("REPO")["name"], "R.E.P.O.")
            self.assertEqual(registry.find("reppo")["name"], "R.E.P.O.")

    def test_game_fuzzy_match_rejects_two_similarly_plausible_titles(self):
        with tempfile.TemporaryDirectory() as directory:
            registry = GameRegistry(
                cache_path=Path(directory) / "games.json",
                discoverer=lambda: [
                    {"name": "Alpha Beta", "launcher": "steam", "id": "400", "launch_uri": "steam://rungameid/400"},
                    {"name": "Alpha Zeta", "launcher": "steam", "id": "620", "launch_uri": "steam://rungameid/620"},
                ],
            )
            self.assertIsNone(registry.find("alpha reta"))


class WindowResolutionTests(unittest.TestCase):
    @staticmethod
    def _window(hwnd, title, process):
        return {
            "hwnd": hwnd,
            "title": title,
            "process": process,
            "pid": hwnd,
            "normalized_title": normalize_window_name(title),
            "normalized_process": normalize_window_name(process.removesuffix(".exe")),
        }

    def test_single_window_accepts_a_clear_typo(self):
        manager = WindowManager()
        window = self._window(1, "Netflix - Google Chrome", "netflix.exe")
        manager.get_windows = Mock(return_value=[window])

        self.assertEqual(manager.find("netflx"), window)

    def test_multiple_matching_windows_are_refused_for_single_window_actions(self):
        manager = WindowManager()
        manager.get_windows = Mock(return_value=[
            self._window(1, "Documentos - Google Chrome", "chrome.exe"),
            self._window(2, "Netflix - Google Chrome", "chrome.exe"),
        ])

        self.assertIsNone(manager.find("chrome"))
        self.assertIn("varias ventanas", manager.last_match_message("chrome"))

        with patch("brain.mochi_tools.window_manager", manager):
            result = close_window("chrome")
        self.assertFalse(result["ok"])
        self.assertIn("varias ventanas", result["message"])

        manager.get_windows = Mock(return_value=[
            self._window(1, "Documentos - Google Chrome", "chrome.exe"),
            self._window(2, "Chromium", "chromium.exe"),
        ])
        self.assertEqual(manager.find_all("chrom"), [])

    def test_uwp_frame_host_duplicate_prefers_the_real_application_window(self):
        manager = WindowManager()
        app = self._window(1, "Calculadora", "CalculatorApp.exe")
        host = self._window(2, "Calculadora", "ApplicationFrameHost.exe")
        manager.get_windows = Mock(return_value=[app, host])

        self.assertEqual(manager.find("calculadora"), app)
        self.assertEqual(manager.find_all("calculadora"), [app])


class WebSessionCacheTests(unittest.TestCase):
    def test_cache_expires_and_never_serializes_uia_elements(self):
        now = [1000.0]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "web_session.json"
            cache = WebSessionCache(path=path, ttl_seconds=120, clock=lambda: now[0])
            cache.begin("https://www.netflix.com", "Netflix", "Marian", {"profile_id": "Profile 5", "profile_name": "Marian"})
            self.assertIsNone(cache.find("https://www.netflix.com", "Marian"))
            snapshot = {"ok": True, "page": {"title": "Netflix", "url": None}, "elements": [{"id": "e3", "name": "Isaías"}]}
            self.assertTrue(cache.mark_observed_after_open(snapshot))
            record = cache.find("https://www.netflix.com", "Marian")
            self.assertEqual(record["page"]["title"], "Netflix")
            self.assertNotIn("elements", record)
            self.assertNotIn("e3", path.read_text(encoding="utf-8"))
            self.assertIsNone(cache.find("https://www.netflix.com"))
            now[0] += 121
            self.assertIsNone(cache.find("https://www.netflix.com", "Marian"))

    def test_cache_requires_current_page_observation_before_reuse(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = WebSessionCache(path=Path(directory) / "web_session.json", ttl_seconds=120)
            cache.begin("https://www.netflix.com", "Netflix", "Marian", {"profile_id": "Profile 5"})
            cache.mark_observed_after_open({"ok": True, "page": {"title": "Netflix", "url": None}})
            record = cache.find("https://www.netflix.com", "Marian")
            self.assertTrue(cache.matches_observation(record, {"ok": True, "page": {"title": "Página de inicio — Netflix"}}))
            self.assertFalse(cache.matches_observation(record, {"ok": True, "page": {"title": "Gmail"}}))

    def test_browser_open_reuses_only_a_uia_verified_active_session(self):
        class Resolver:
            def resolve(self, _target):
                return {"ok": True, "kind": "service", "service": "netflix", "target": "Netflix", "url": "https://www.netflix.com"}
        class Inspector:
            def inspect(self):
                return {"ok": True, "page": {"title": "Página de inicio — Netflix", "url": None}}
        with tempfile.TemporaryDirectory() as directory:
            cache = WebSessionCache(path=Path(directory) / "web_session.json", ttl_seconds=120)
            cache.begin("https://www.netflix.com", "Netflix", "Marian", {"profile_id": "Profile 5", "profile_name": "Marian"})
            cache.mark_observed_after_open({"ok": True, "page": {"title": "Netflix", "url": None}})
            with patch.object(browser_tools, "_resolver", Resolver()), patch.object(browser_tools, "_web_session_cache", cache), patch.object(browser_tools, "_web_inspector", Inspector()), patch.object(browser_tools._browser, "open_url_for_profile") as open_url:
                result = browser_tools.browser_open("Netflix", profile="Marian")
            self.assertTrue(result["ok"])
            self.assertTrue(result["session_reused"])
            open_url.assert_not_called()

    def test_browser_open_activates_a_cached_inactive_tab_before_opening_new_one(self):
        class Resolver:
            def resolve(self, _target):
                return {"ok": True, "kind": "service", "service": "netflix", "target": "Netflix", "url": "https://www.netflix.com"}
        class Inspector:
            def __init__(self):
                self.activated = False
                self.activation_arguments = None
            def inspect(self):
                title = "Página de inicio — Netflix" if self.activated else "Gmail"
                return {"ok": True, "page": {"title": title, "url": None}}
            def activate_matching_tab(self, target, preferred_title=None):
                self.activation_arguments = (target, preferred_title)
                self.activated = True
                return {"ok": True, "tab_title": "Página de inicio — Netflix"}
        with tempfile.TemporaryDirectory() as directory:
            cache = WebSessionCache(path=Path(directory) / "web_session.json", ttl_seconds=120)
            cache.begin("https://www.netflix.com", "Netflix", "Marian", {"profile_id": "Profile 5", "profile_name": "Marian"})
            cache.mark_observed_after_open({"ok": True, "page": {"title": "Página de inicio — Netflix", "url": None}})
            inspector = Inspector()
            with patch.object(browser_tools, "_resolver", Resolver()), patch.object(browser_tools, "_web_session_cache", cache), patch.object(browser_tools, "_web_inspector", inspector), patch.object(browser_tools._browser, "open_url_for_profile") as open_url:
                result = browser_tools.browser_open("Netflix", profile="Marian")
            self.assertTrue(result["ok"])
            self.assertTrue(result["session_reused"])
            self.assertTrue(result["tab_activated"])
            self.assertEqual(inspector.activation_arguments, ("Netflix", "Página de inicio — Netflix"))
            open_url.assert_not_called()

    def test_default_profile_cache_reuses_a_matching_inactive_tab(self):
        class Resolver:
            def resolve(self, _target):
                return {"ok": True, "kind": "service", "service": "youtube", "target": "YouTube", "url": "https://www.youtube.com"}
        class Inspector:
            def __init__(self): self.activated = False
            def inspect(self):
                return {"ok": True, "page": {"title": "YouTube" if self.activated else "Gmail", "url": None}}
            def activate_matching_tab(self, target, preferred_title=None):
                self.activated = target == "YouTube" and preferred_title == "YouTube"
                return {"ok": self.activated}
        with tempfile.TemporaryDirectory() as directory:
            cache = WebSessionCache(path=Path(directory) / "web_session.json", ttl_seconds=120)
            cache.begin("https://www.youtube.com", "YouTube", None, {"profile_id": "Default", "profile_name": "Tu Chrome"})
            cache.mark_observed_after_open({"ok": True, "page": {"title": "YouTube", "url": None}})
            inspector = Inspector()
            with patch.object(browser_tools, "_resolver", Resolver()), patch.object(browser_tools, "_web_session_cache", cache), patch.object(browser_tools, "_web_inspector", inspector), patch.object(browser_tools._browser, "get_default_profile", return_value="Default"), patch.object(browser_tools._browser, "open_url_for_profile") as open_url:
                result = browser_tools.browser_open("YouTube")
            self.assertTrue(result["session_reused"])
            self.assertTrue(result["tab_activated"])
            open_url.assert_not_called()

    def test_ambiguous_cached_tabs_fall_back_to_a_normal_browser_open(self):
        class Resolver:
            def resolve(self, _target):
                return {"ok": True, "kind": "service", "service": "youtube", "target": "YouTube", "url": "https://www.youtube.com"}
        class Inspector:
            def inspect(self):
                return {"ok": True, "page": {"title": "Gmail", "url": None}}
            def activate_matching_tab(self, *_args, **_kwargs):
                return {"ok": False, "error": "existing_tab_ambiguous", "message": "Hay varias pestañas abiertas que podrían corresponder al destino."}
        with tempfile.TemporaryDirectory() as directory:
            cache = WebSessionCache(path=Path(directory) / "web_session.json", ttl_seconds=120)
            cache.begin("https://www.youtube.com", "YouTube", "Default", {"profile_id": "Default", "profile_name": "Tu Chrome"})
            cache.mark_observed_after_open({"ok": True, "page": {"title": "YouTube", "url": None}})
            with patch.object(browser_tools, "_resolver", Resolver()), patch.object(browser_tools, "_web_session_cache", cache), patch.object(browser_tools, "_web_inspector", Inspector()), patch.object(browser_tools._browser, "open_url_for_profile", return_value={"ok": True, "url": "https://www.youtube.com"}) as open_url:
                result = browser_tools.browser_open("YouTube", profile="Default")
            self.assertTrue(result["ok"])
            open_url.assert_called_once_with("https://www.youtube.com", profile="Default")

    def test_auxiliary_chrome_launch_resolves_profile_id_to_directory(self):
        config = {
            "browser": {
                "executable": "chrome.exe",
                "default_profile": "personal",
                "force_renderer_accessibility": True,
                "profiles": {
                    "personal": {"directory": "Default"},
                    "work": {"directory": "Profile 5"},
                },
            },
        }
        with patch("actions.apps.get_config", return_value=config), patch("actions.apps.subprocess.Popen") as popen:
            self.assertTrue(app_actions.open_chrome())
            self.assertTrue(app_actions.open_url("https://example.test", profile_directory="work"))

        self.assertEqual(popen.call_args_list[0].args[0], [
            "chrome.exe", "--profile-directory=Default", "--force-renderer-accessibility",
        ])
        self.assertEqual(popen.call_args_list[1].args[0], [
            "chrome.exe", "--profile-directory=Profile 5", "--force-renderer-accessibility", "https://example.test",
        ])

    def test_unobserved_cache_entry_falls_back_to_normal_browser_open(self):
        class Resolver:
            def resolve(self, _target):
                return {"ok": True, "kind": "service", "service": "netflix", "target": "Netflix", "url": "https://www.netflix.com"}
        with tempfile.TemporaryDirectory() as directory:
            cache = WebSessionCache(path=Path(directory) / "web_session.json", ttl_seconds=120)
            cache.begin("https://www.netflix.com", "Netflix", "Marian", {"profile_id": "Profile 5", "profile_name": "Marian"})
            with patch.object(browser_tools, "_resolver", Resolver()), patch.object(browser_tools, "_web_session_cache", cache), patch.object(browser_tools._browser, "open_url_for_profile", return_value={"ok": True, "profile_id": "Profile 5", "profile_name": "Marian", "url": "https://www.netflix.com"}) as open_url:
                result = browser_tools.browser_open("Netflix", profile="Marian")
            self.assertTrue(result["ok"])
            self.assertNotIn("session_reused", result)
            open_url.assert_called_once_with("https://www.netflix.com", profile="Marian")

    def test_browser_open_adopts_a_matching_active_tab_without_profile(self):
        class Resolver:
            def resolve(self, _target):
                return {"ok": True, "kind": "service", "service": "netflix", "target": "Netflix", "url": "https://www.netflix.com"}
        class Inspector:
            def inspect(self):
                return {"ok": True, "page": {"title": "Página de inicio — Netflix", "url": None}, "elements": [{"id": "e1", "role": "link", "name": "Inicio", "value": ""}]}
        with patch.object(browser_tools, "_resolver", Resolver()), patch.object(browser_tools, "_web_inspector", Inspector()), patch.object(browser_tools, "_web_session", WebSession()), patch.object(browser_tools._browser, "open_url_for_profile") as open_url:
            result = browser_tools.browser_open("Netflix")
        self.assertTrue(result["session_adopted"])
        open_url.assert_not_called()

    def test_in_memory_session_expires_and_omits_uia_ids(self):
        now = [1000.0]
        session = WebSession(ttl_seconds=120, clock=lambda: now[0])
        session.observe({"ok": True, "page": {"title": "Netflix", "url": None}, "elements": [{"id": "e4", "role": "link", "name": "Isaías", "value": ""}]})
        context = session.current()
        self.assertTrue(session.matches_target("Netflix"))
        self.assertEqual(context["semantic_items"], [{"role": "link", "name": "Isaías", "value": ""}])
        now[0] += 121
        self.assertIsNone(session.current())


class ScriptedRouter:
    def __init__(self, calls):
        self.calls = list(calls)
        self.last_mode = "fast"

    def generate(self, **_kwargs):
        return self.calls.pop(0)

    def should_use_deep_for(self, _command):
        return False


class AgentLoopTests(unittest.TestCase):
    def test_named_result_matches_public_acronym_to_expanded_title(self):
        observation = {"elements": [
            {"id": "gta", "role": "link", "name": "Una mirada extendida a Grand Theft Auto VI"},
            {"id": "other", "role": "link", "name": "Gran Turismo: De jugador a corredor"},
        ]}

        selected = MochiAgentLoop()._named_page_result(observation, "GTA VI")

        self.assertEqual(selected["id"], "gta")

    def test_resume_is_a_playback_control(self):
        self.assertTrue(MochiAgentLoop._is_play_media_control("Reanudar"))
        self.assertTrue(MochiAgentLoop._is_play_media_control("Resume"))

    def test_player_time_readout_is_not_a_visible_video_card(self):
        loop = MochiAgentLoop()
        observation = {"elements": [
            {"id": "time", "role": "button", "name": "-15 Minutos 34 Segundos de 15 Minutos 58 Segundos", "offscreen": False},
            {"id": "clock", "role": "link", "name": "0:12 / 15:58", "offscreen": False},
            {"id": "video", "role": "link", "name": "Tutorial de Python 15 minutos", "offscreen": False},
        ]}

        cards = loop._visible_video_cards(observation, "visible")

        self.assertEqual([item["id"] for item in cards], ["video"])

    def test_playback_link_uses_invoke_click_and_requires_pause_evidence(self):
        inspections = iter([
            {"ok": True, "snapshot_id": "s1", "page": {"title": "GTA VI - Netflix"}, "changed": False,
             "elements": [{"id": "resume", "role": "link", "name": "Reanudar", "offscreen": False}]},
            {"ok": True, "snapshot_id": "s2", "page": {"title": "GTA VI - Netflix"}, "changed": True,
             "elements": [{"id": "pause", "role": "button", "name": "Pausa", "offscreen": False}]},
        ])
        acted = []
        registry = ToolRegistry()
        registry.register("web_inspect", "inspect", {"type": "object", "properties": {}}, lambda **_kwargs: next(inspections))
        registry.register("web_act", "act", {"type": "object", "properties": {}}, lambda **kwargs: acted.append(kwargs) or {"ok": True})
        state = plan_goal("reproduce GTA VI en Netflix")
        state.mark_completed("select_result")

        with patch("brain.agent_loop.time.sleep"):
            MochiAgentLoop()._fast_play_media(registry, state)

        self.assertEqual(acted, [{"element_id": "resume", "action": "click", "snapshot_id": "s1"}])
        self.assertEqual(state.step("play_media").status, "completed")

    def test_out_of_range_seek_still_allows_requested_final_pause(self):
        state = plan_goal(
            "busca el trailer del GTA VI en Netflix, pon el minuto 15 y luego paralo"
        )
        for operation in ("browser_open", "search_page", "select_result", "play_media"):
            state.mark_completed(operation)
        registry = ToolRegistry()
        registry.register("web_inspect", "inspect", {"type": "object", "properties": {}}, lambda **_kwargs: {
            "ok": True,
            "snapshot_id": "s1",
            "page": {"title": "Una mirada extendida a Grand Theft Auto VI — Netflix"},
            "elements": [],
        })
        registry.register("web_act", "act", {"type": "object", "properties": {}}, lambda **_kwargs: self.fail("No UIA fallback after terminal range evidence"))
        registry.register("control_media", "media", {"type": "object", "properties": {}}, lambda **_kwargs: {
            "ok": False,
            "error": "media_position_out_of_range",
            "requested_position_seconds": 900.0,
            "duration_seconds": 24.0,
        })
        loop = MochiAgentLoop()

        loop._fast_player_navigation(registry, state, "seek_media")

        self.assertEqual(state.step("seek_media").status, "blocked")
        self.assertEqual(state.step("pause_media").status, "pending")
        self.assertEqual(loop._next_named_page_operation(state), "pause_media")
        summary = loop._goal_summary(state, "")
        self.assertIn("900 segundos", summary)
        self.assertIn("24 segundos", summary)

    def test_channel_selector_prefers_explicit_channel_navigation_over_named_chapter(self):
        observation = {"elements": [
            {"id": "c1", "role": "link", "name": "Ir al canal AuronPlay"},
            {"id": "v1", "role": "button", "name": "29:31 AuronPlay nunca se fue"},
        ]}

        selected = MochiAgentLoop()._named_channel_result(observation, "auronplay")

        self.assertEqual(selected["id"], "c1")

    def test_channel_selector_never_treats_a_named_chapter_as_a_channel(self):
        observation = {"elements": [
            {"id": "v1", "role": "button", "name": "29:31 AuronPlay nunca se fue"},
        ]}

        self.assertIsNone(MochiAgentLoop()._named_channel_result(observation, "auronplay"))

    def test_channel_selector_accepts_one_rich_identity_card_with_matching_handle(self):
        observation = {"elements": [
            {"id": "secondary", "role": "link", "name": "Auron Verificada @auron 15 M de suscriptores menciona AuronPlay"},
            {"id": "main", "role": "link", "name": "AuronPlay Verificada @AuronPlay 29 M de suscriptores"},
        ]}

        selected = MochiAgentLoop()._named_channel_result(observation, "auronplay")

        self.assertEqual(selected["id"], "main")

    def test_channel_ambiguity_lists_public_identities_not_video_chapters(self):
        observation = {"elements": [
            {"id": "secondary", "role": "link", "name": "Rubius Z Verificada @elrubius 12 M de suscriptores"},
            {"id": "main", "role": "link", "name": "Ir al canal elrubiusOMG"},
            {"id": "chapter", "role": "button", "name": "12:03 Rubius juega", "container": "Video"},
        ]}

        options = MochiAgentLoop()._channel_choice_options(observation, "rubius")

        self.assertEqual([option["name"] for option in options], [
            "Rubius Z Verificada @elrubius 12 M de suscriptores",
            "Ir al canal elrubiusOMG",
        ])

    def test_loaded_channel_page_is_distinguished_from_search_results(self):
        channel_page = {
            "ok": True,
            "page": {"title": "AuronPlay - YouTube"},
            "elements": [
                {"role": "link", "name": "Vídeos"},
                {"role": "tab", "name": "Listas"},
                {"role": "tab", "name": "Publicaciones"},
            ],
        }
        search_page = {
            "ok": True,
            "page": {"title": "auronplay - YouTube"},
            "elements": [
                {"role": "tab", "name": "Vídeos"},
                {"role": "tab", "name": "Todo"},
                {"role": "link", "name": "Ir al canal AuronPlay"},
            ],
        }

        loop = MochiAgentLoop()
        self.assertTrue(loop._observation_is_requested_channel(channel_page, "auronplay"))
        self.assertFalse(loop._observation_is_requested_channel(search_page, "auronplay"))

    def test_any_youtube_tutorial_runs_search_card_selection_and_verified_playback(self):
        inspections = iter([
            {"ok": True, "snapshot_id": "s1", "page": {"title": "YouTube"}, "changed": False,
             "elements": [{"id": "q1", "role": "textbox", "name": "Buscar", "value": ""}]},
            {"ok": True, "snapshot_id": "s2", "page": {"title": "YouTube"}, "changed": True,
             "elements": [{"id": "q2", "role": "textbox", "name": "Buscar", "value": "un tutorial de godot"}]},
            {"ok": True, "snapshot_id": "s3", "page": {"title": "godot - YouTube"}, "changed": True,
             "elements": [{"id": "v1", "role": "link", "name": "Godot desde cero 20 minutos", "value": "", "container": "Godot desde cero", "offscreen": False}]},
            {"ok": True, "snapshot_id": "s4", "page": {"title": "godot - YouTube"}, "changed": False,
             "elements": [{"id": "v2", "role": "link", "name": "Godot desde cero 20 minutos", "value": "", "container": "Godot desde cero", "offscreen": False}]},
            {"ok": True, "snapshot_id": "s5", "page": {"title": "Godot desde cero - YouTube"}, "changed": True,
             "elements": [{"id": "p1", "role": "button", "name": "Pausa", "value": "", "offscreen": False}]},
        ])
        acted = []
        registry = ToolRegistry()
        registry.register("browser_open", "open", {"type": "object", "properties": {}}, lambda **_kwargs: {"ok": True})
        registry.register("web_inspect", "inspect", {"type": "object", "properties": {}}, lambda **_kwargs: next(inspections))
        registry.register("web_act", "act", {"type": "object", "properties": {}}, lambda **kwargs: acted.append(kwargs) or {"ok": True})

        result = MochiAgentLoop(router=ScriptedRouter([])).run(
            "busca un tutorial de godot en youtube y reproducelo",
            registry,
        )

        self.assertEqual((result["status"], result["final_mode"]), (
            "completed", "deterministic_structured_web_plan",
        ), result)
        self.assertEqual(acted[-1], {
            "element_id": "v2", "action": "click", "snapshot_id": "s4",
        })
        self.assertEqual(len(result["messages"]), 2 + len(result["actions"]))

    def test_latest_channel_video_executes_channel_tab_sort_and_first_card(self):
        inspections = iter([
            {"ok": True, "snapshot_id": "s1", "page": {"title": "YouTube"}, "changed": False,
             "elements": [{"id": "q1", "role": "textbox", "name": "Buscar", "value": ""}]},
            {"ok": True, "snapshot_id": "s2", "page": {"title": "YouTube"}, "changed": True,
             "elements": [{"id": "q2", "role": "textbox", "name": "Buscar", "value": "auronplay"}]},
            {"ok": True, "snapshot_id": "s3", "page": {"title": "auronplay - YouTube"}, "changed": True,
             "elements": [{"id": "c1", "role": "link", "name": "auronplay", "value": "", "offscreen": False}]},
            {"ok": True, "snapshot_id": "s4", "page": {"title": "auronplay - YouTube"}, "changed": False,
             "elements": [{"id": "c2", "role": "link", "name": "auronplay", "value": "", "offscreen": False}]},
            {"ok": True, "snapshot_id": "s5", "page": {"title": "AuronPlay - YouTube"}, "changed": True,
             "elements": [{"id": "t1", "role": "tab", "name": "Videos", "value": "", "offscreen": False}]},
            {"ok": True, "snapshot_id": "s6", "page": {"title": "AuronPlay - YouTube"}, "changed": False,
             "elements": [{"id": "t2", "role": "tab", "name": "Videos", "value": "", "offscreen": False}]},
            {"ok": True, "snapshot_id": "s7", "page": {"title": "AuronPlay - YouTube"}, "changed": True,
             "elements": [{"id": "o1", "role": "button", "name": "Más recientes", "value": "", "offscreen": False}]},
            {"ok": True, "snapshot_id": "s8", "page": {"title": "AuronPlay - YouTube"}, "changed": False,
             "elements": [{"id": "o2", "role": "button", "name": "Más recientes", "value": "", "offscreen": False}]},
            {"ok": True, "snapshot_id": "s9", "page": {"title": "AuronPlay - YouTube"}, "changed": True,
             "elements": [{"id": "v1", "role": "link", "name": "Video nuevo 12 minutos", "value": "", "container": "Video nuevo", "offscreen": False}]},
            {"ok": True, "snapshot_id": "s10", "page": {"title": "AuronPlay - YouTube"}, "changed": False,
             "elements": [{"id": "v2", "role": "link", "name": "Video nuevo 12 minutos", "value": "", "container": "Video nuevo", "offscreen": False}]},
            {"ok": True, "snapshot_id": "s11", "page": {"title": "Video nuevo - YouTube"}, "changed": True,
             "elements": [{"id": "p1", "role": "button", "name": "Pausa", "value": "", "offscreen": False}]},
        ])
        acted = []
        registry = ToolRegistry()
        registry.register("browser_open", "open", {"type": "object", "properties": {}}, lambda **_kwargs: {"ok": True})
        registry.register("web_inspect", "inspect", {"type": "object", "properties": {}}, lambda **_kwargs: next(inspections))
        registry.register("web_act", "act", {"type": "object", "properties": {}}, lambda **kwargs: acted.append(kwargs) or {"ok": True})

        result = MochiAgentLoop(router=ScriptedRouter([])).run(
            "busca el ultimo video que publico auronplay en su canal principal",
            registry,
        )

        self.assertEqual(result["status"], "completed", result)
        self.assertEqual([item["element_id"] for item in acted[-4:]], ["c2", "t2", "o2", "v2"])
        self.assertIn("más reciente", result["text"])

    def setUp(self):
        # Agent-loop tests must not leave conversational choices in the real
        # per-user cache.  Each test gets its own process-persistent file so
        # cross-process behavior is still exercised without external state.
        self._choice_directory = tempfile.TemporaryDirectory()
        self._default_choice_cache = PendingPageChoiceCache(
            path=Path(self._choice_directory.name) / "pending_choice.json",
            ttl_seconds=120,
        )
        self._choice_cache_patch = patch(
            "brain.agent_loop.PendingPageChoiceCache",
            return_value=self._default_choice_cache,
        )
        self._choice_cache_patch.start()

    def tearDown(self):
        self._choice_cache_patch.stop()
        self._choice_directory.cleanup()

    def test_numbered_follow_up_reuses_persisted_choices_and_verifies_playback(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = PendingPageChoiceCache(path=Path(directory) / "pending_choice.json", ttl_seconds=120)
            cache.remember(
                page={"title": "Resultados - YouTube", "url": None},
                target="YouTube",
                query="moviendo caderas oficial",
                options=[
                    {"name": "moviendo caderas oficial de daddy yankee"},
                    {"name": "moviendo caderas oficial de daddy yanke"},
                ],
            )
            inspections = iter([
                {"ok": True, "snapshot_id": "s1", "page": {"title": "Resultados - YouTube"}, "changed": False, "elements": [
                    {"id": "e1", "role": "link", "name": "moviendo caderas oficial de daddy yankee", "value": ""},
                    {"id": "e2", "role": "link", "name": "moviendo caderas oficial de daddy yanke", "value": ""},
                ]},
                {"ok": True, "snapshot_id": "s2", "page": {"title": "moviendo caderas oficial de daddy yanke - YouTube"}, "changed": True, "elements": [
                    {"id": "e3", "role": "button", "name": "Reproducir", "value": ""},
                ]},
                {"ok": True, "snapshot_id": "s3", "page": {"title": "moviendo caderas oficial de daddy yanke - YouTube"}, "changed": False, "elements": [
                    {"id": "e4", "role": "button", "name": "Reproducir", "value": ""},
                ]},
                {"ok": True, "snapshot_id": "s4", "page": {"title": "moviendo caderas oficial de daddy yanke - YouTube"}, "changed": True, "elements": [
                    {"id": "e5", "role": "button", "name": "Pausa", "value": ""},
                ]},
            ])
            acted = []
            registry = ToolRegistry()
            registry.register("web_inspect", "inspect", {"type": "object", "properties": {}}, lambda **_kwargs: next(inspections))
            registry.register("web_act", "act", {"type": "object", "properties": {}}, lambda **kwargs: acted.append(kwargs) or {"ok": True})

            with patch("brain.agent_loop.time.sleep"):
                result = MochiAgentLoop(
                    router=ScriptedRouter([]),
                    max_steps=1,
                    pending_choice_cache=cache,
                ).run("reproduce la segunda opción", registry)

            self.assertEqual(result["status"], "completed")
            self.assertEqual(acted, [
                {"element_id": "e2", "action": "click", "snapshot_id": "s1"},
                {"element_id": "e4", "action": "keyboard_activate", "snapshot_id": "s3"},
            ])
            self.assertIn("confirmé que empezó", result["text"])
            self.assertIsNone(cache.current())

    def test_invalid_or_missing_numbered_context_never_becomes_a_web_search(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = PendingPageChoiceCache(path=Path(directory) / "pending_choice.json", ttl_seconds=120)
            cache.remember(
                page={"title": "Resultados - YouTube"}, target="YouTube", query="prueba",
                options=[{"name": "Opción uno"}, {"name": "Opción dos"}],
            )
            registry = ToolRegistry()
            out_of_range = MochiAgentLoop(
                router=ScriptedRouter([]), pending_choice_cache=cache,
            ).run("reproduce el tercero", registry)
            self.assertEqual(out_of_range["status"], "blocked")
            self.assertIn("solo tiene 2 opciones", out_of_range["text"])
            self.assertEqual(out_of_range["actions"], [])

            cache.clear()
            missing = MochiAgentLoop(
                router=ScriptedRouter([]), pending_choice_cache=cache,
            ).run("reproduce el tercero", registry)
            self.assertEqual(missing["status"], "blocked")
            self.assertIn("No tengo una lista reciente", missing["text"])
            self.assertEqual(missing["actions"], [])

    def test_only_concise_ordinals_are_pending_choice_answers(self):
        loop = MochiAgentLoop(router=ScriptedRouter([]))

        self.assertEqual(loop._requested_option_index("reproduce el tercero"), 3)
        self.assertEqual(loop._requested_option_index("selecciona el 2"), 2)
        self.assertIsNone(loop._requested_option_index(
            "busca The Office en Netflix y pon el primer capitulo de la segunda temporada"
        ))

    def test_legacy_pending_choice_is_discarded_before_it_can_activate_a_suggestion(self):
        """Old choice records predate the media-card safety distinction."""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "pending_choice.json"
            path.write_text(json.dumps({
                "version": 1,
                "expires_at": 4_000_000_000,
                "options": [{"name": "same named suggestion"}],
            }), encoding="utf-8")
            cache = PendingPageChoiceCache(path=path, ttl_seconds=120)

            self.assertIsNone(cache.current())
            self.assertFalse(path.exists())

    def test_equivalent_failed_calls_are_not_executed_twice(self):
        executions = []
        registry = ToolRegistry()
        registry.register("open_application", "local app", {"type": "object", "properties": {"application": {"type": "string"}}}, lambda application: executions.append(application) or {"ok": False, "message": "not found"})
        router = ScriptedRouter([
            BrainResult(True, tool_calls=[{"function": {"name": "open_application", "arguments": {"application": "Netflix"}}}]),
            BrainResult(True, tool_calls=[{"function": {"name": "open_application", "arguments": {"application": " netflix "}}}]),
            BrainResult(True, text="Abrí Netflix."),
        ])
        result = MochiAgentLoop(router=router, max_steps=4).run("Abre Netflix", registry)
        self.assertTrue(result["ok"])
        self.assertEqual(executions, ["Netflix"])
        self.assertEqual(result["actions"][1]["result"]["error"], "duplicate_failed_action")
        self.assertEqual(result["actions"][0]["result"]["suggested_next_capability"], "browser_open")

    def test_known_web_destination_bypasses_wrong_application_guess(self):
        registry = ToolRegistry()
        registry.register("open_application", "local app", {"type": "object", "properties": {"application": {"type": "string"}}}, lambda application: {"ok": False, "message": "not found"})
        registry.register("browser_open", "web", {"type": "object", "properties": {"target": {"type": "string"}}}, lambda target: {"ok": True, "message": "opened", "url": "https://www.netflix.com"})
        router = ScriptedRouter([
            BrainResult(True, tool_calls=[{"function": {"name": "open_application", "arguments": {"application": "Netflix"}}}]),
            BrainResult(True, tool_calls=[{"function": {"name": "browser_open", "arguments": {"target": "Netflix"}}}]),
            BrainResult(True, text="Abrí Netflix."),
        ])
        result = MochiAgentLoop(router=router, max_steps=4).run("Abre Netflix", registry)
        self.assertTrue(result["ok"])
        self.assertEqual([item["tool"] for item in result["actions"]], ["browser_open"])
        self.assertEqual(result["status"], "completed")

    def test_local_file_search_completes_without_browser_search(self):
        registry = ToolRegistry()
        registry.register("find_files", "files", {"type": "object", "properties": {"query": {"type": "string"}}}, lambda **_kwargs: {"ok": True, "count": 0, "message": "No encontré archivos."})
        registry.register("browser_search", "web", {"type": "object", "properties": {}}, lambda **_kwargs: {"ok": False, "message": "should not run"})
        router = ScriptedRouter([
            BrainResult(True, tool_calls=[{"function": {"name": "find_files", "arguments": {"query": "presupuesto"}}}]),
        ])
        result = MochiAgentLoop(router=router, max_steps=3).run("Busca un archivo llamado presupuesto en mis vacaciones.", registry)
        self.assertEqual(result["status"], "completed")
        self.assertEqual([item["tool"] for item in result["actions"]], ["find_files"])
        self.assertEqual(result["text"], "No encontré archivos.")

    def test_local_file_open_bypasses_wrong_browser_guess(self):
        browser_executions = []
        registry = ToolRegistry()
        registry.register("browser_search", "web", {"type": "object", "properties": {}}, lambda **kwargs: browser_executions.append(kwargs) or {"ok": True})
        registry.register("open_file", "file", {"type": "object", "properties": {"query": {"type": "string"}}}, lambda **_kwargs: {"ok": True, "message": "Abriendo Qué hacer en mis vacaciones.docx"})
        router = ScriptedRouter([
            BrainResult(True, tool_calls=[{"function": {"name": "browser_search", "arguments": {"query": "qué hacer en mis vacaciones"}}}]),
            BrainResult(True, tool_calls=[{"function": {"name": "open_file", "arguments": {"query": "qué hacer en mis vacaciones"}}}]),
        ])
        result = MochiAgentLoop(router=router, max_steps=3).run("Abrí el archivo qué hacer en mis vacaciones.", registry)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(browser_executions, [])
        self.assertEqual([item["tool"] for item in result["actions"]], ["open_file"])

    def test_game_list_is_rendered_and_completed_in_one_step(self):
        registry = ToolRegistry()
        registry.register("list_games", "games", {"type": "object", "properties": {}}, lambda: {"ok": True, "games": ["Dead as Disco", "R.E.P.O."], "message": "Encontré 2 juego(s)."})
        router = ScriptedRouter([
            BrainResult(True, tool_calls=[{"function": {"name": "list_games", "arguments": {}}}]),
        ])
        result = MochiAgentLoop(router=router, max_steps=3).run("Mostrame los juegos instalados", registry)
        self.assertEqual((result["status"], result["steps"]), ("completed", 1))
        self.assertEqual(result["text"], "Juegos instalados:\n1. Dead as Disco\n2. R.E.P.O.")

    def test_game_launch_reports_a_dispatched_order_in_one_step(self):
        registry = ToolRegistry()
        registry.register("launch_game", "game", {"type": "object", "properties": {}}, lambda **_kwargs: {"ok": True, "game": "Dead as Disco", "launcher": "steam", "message": "Orden de inicio enviada"})
        router = ScriptedRouter([
            BrainResult(True, tool_calls=[{"function": {"name": "launch_game", "arguments": {"game": "dead as disco"}}}]),
        ])
        result = MochiAgentLoop(router=router, max_steps=3).run("Abrí el juego dead as disco", registry)
        self.assertEqual((result["status"], result["steps"]), ("completed", 1))
        self.assertEqual(result["text"], "Envié a Steam la orden de iniciar Dead as Disco.")

    def test_bare_dotted_game_name_uses_game_launch_not_browser(self):
        browser_executions = []
        registry = ToolRegistry()
        registry.register("browser_open", "browser", {"type": "object", "properties": {}}, lambda **kwargs: browser_executions.append(kwargs) or {"ok": True})
        registry.register("launch_game", "game", {"type": "object", "properties": {}}, lambda **_kwargs: {"ok": True, "game": "R.E.P.O.", "launcher": "steam", "message": "Orden enviada"})
        router = ScriptedRouter([
            BrainResult(True, tool_calls=[{"function": {"name": "launch_game", "arguments": {"game": "R.E.P.O."}}}]),
        ])
        result = MochiAgentLoop(router=router, max_steps=3).run("Abre R.E.P.O.", registry)
        self.assertEqual((result["status"], result["steps"]), ("completed", 1))
        self.assertEqual(browser_executions, [])
        self.assertEqual(result["text"], "Envié a Steam la orden de iniciar R.E.P.O.")

    def test_bare_approximate_name_uses_deterministic_target_resolution(self):
        registry = ToolRegistry()
        registry.register(
            "open_target",
            "resolve target",
            {"type": "object", "properties": {}},
            lambda **_kwargs: {
                "ok": True,
                "goal_completed": True,
                "resolution": "game",
                "game": "R.E.P.O.",
                "launcher": "steam",
                "message": "Orden de inicio enviada",
            },
        )
        router = ScriptedRouter([])

        result = MochiAgentLoop(router=router, max_steps=3).run("Abre repo", registry)

        self.assertEqual((result["status"], result["steps"]), ("completed", 1))
        self.assertEqual([item["tool"] for item in result["actions"]], ["open_target"])
        self.assertEqual(result["text"], "Envié a Steam la orden de iniciar R.E.P.O.")

    def test_pending_named_site_profile_rejects_management_control(self):
        executions = []
        registry = ToolRegistry()
        registry.register("browser_open", "web", {"type": "object", "properties": {}}, lambda **_kwargs: {"ok": True})
        registry.register("web_inspect", "inspect", {"type": "object", "properties": {}}, lambda **_kwargs: {
            # A different title keeps this focused on the goal-element guard
            # instead of activating the browser-open fast path.
            "ok": True, "snapshot_id": "s1", "page": {"title": "Other page"}, "changed": False,
            "elements": [{"id": "e1", "role": "link", "name": "Administrar perfiles", "value": ""}, {"id": "e2", "role": "link", "name": "Isaías", "value": ""}],
        })
        registry.register("web_act", "act", {"type": "object", "properties": {}}, lambda **kwargs: executions.append(kwargs) or {"ok": True})
        router = ScriptedRouter([
            BrainResult(True, tool_calls=[{"function": {"name": "browser_open", "arguments": {"target": "Netflix", "profile": "Marian"}}}]),
            BrainResult(True, tool_calls=[{"function": {"name": "web_inspect", "arguments": {}}}]),
            BrainResult(True, tool_calls=[{"function": {"name": "web_act", "arguments": {"element_id": "e1", "action": "click"}}}]),
            BrainResult(True, tool_calls=[{"function": {"name": "web_act", "arguments": {"element_id": "e2", "action": "click"}}}]),
        ])
        result = MochiAgentLoop(router=router, max_steps=4).run("Abre Netflix con la cuenta de Google de Marian y luego elije el perfil de Isaías en Netflix.", registry)
        rejected = next(item for item in result["actions"] if item["tool"] == "web_act" and item["arguments"].get("element_id") == "e1")
        self.assertEqual(rejected["result"]["error"], "invalid_goal_element")
        self.assertEqual(executions, [{"element_id": "e2", "action": "click"}])

    def test_completed_site_profile_is_in_final_goal_summary(self):
        registry = ToolRegistry()
        registry.register("browser_open", "web", {"type": "object", "properties": {}}, lambda **_kwargs: {"ok": True})
        registry.register("web_inspect", "inspect", {"type": "object", "properties": {}}, lambda **_kwargs: {
            "ok": True, "snapshot_id": "s1", "changed": True,
            "elements": [{"id": "e1", "role": "link", "name": "Isaías", "value": ""}],
        })
        registry.register("web_act", "act", {"type": "object", "properties": {}}, lambda **_kwargs: {"ok": True})
        router = ScriptedRouter([
            BrainResult(True, tool_calls=[{"function": {"name": "browser_open", "arguments": {"target": "Netflix", "profile": "Marian"}}}]),
            BrainResult(True, tool_calls=[{"function": {"name": "web_inspect", "arguments": {}}}]),
            BrainResult(True, tool_calls=[{"function": {"name": "web_act", "arguments": {"element_id": "e1", "action": "click"}}}]),
            BrainResult(True, tool_calls=[{"function": {"name": "web_inspect", "arguments": {}}}]),
            BrainResult(True, text=""),
        ])
        result = MochiAgentLoop(router=router, max_steps=5).run("Abre Netflix con la cuenta de Google de Marian y luego elije el perfil de Isaías en Netflix.", registry)
        self.assertEqual(result["status"], "completed")
        self.assertIn("perfil Isaías está activo dentro de Netflix", result["text"])

    def test_named_site_selection_uses_fast_safe_web_micro_plan(self):
        calls = []
        inspection_results = iter([
            {"ok": True, "snapshot_id": "s-before", "page": {"title": "Netflix"}, "changed": False, "elements": [{"id": "e3", "role": "link", "name": "Isaías", "value": ""}]},
            {"ok": True, "snapshot_id": "s-after", "page": {"title": "Inicio — Netflix"}, "changed": True, "elements": [{"id": "e1", "role": "button", "name": "Isaías", "value": ""}]},
        ])
        registry = ToolRegistry()
        registry.register("browser_open", "web", {"type": "object", "properties": {}}, lambda **_kwargs: {"ok": True})
        registry.register("web_inspect", "inspect", {"type": "object", "properties": {}}, lambda **_kwargs: next(inspection_results))
        registry.register("web_act", "act", {"type": "object", "properties": {}}, lambda **kwargs: calls.append(kwargs) or {"ok": True})
        router = ScriptedRouter([
            BrainResult(True, tool_calls=[{"function": {"name": "browser_open", "arguments": {"target": "Netflix", "profile": "Marian"}}}]),
            BrainResult(True, text=""),
        ])
        result = MochiAgentLoop(router=router, max_steps=3).run("Abre Netflix con la cuenta de Google de Marian y luego elije el perfil de Isaías en Netflix.", registry)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["steps"], 1)
        self.assertEqual([entry["tool"] for entry in result["actions"]], ["browser_open", "web_inspect", "web_act", "web_inspect"])
        self.assertEqual(calls, [{"element_id": "e3", "action": "click", "snapshot_id": "s-before"}])

    def test_named_site_selection_waits_for_a_slow_profile_window(self):
        inspections = iter([
            *[
                {"ok": True, "snapshot_id": f"loading-{index}", "page": {"title": "Sin título - Google Chrome"}, "changed": False, "elements": []}
                for index in range(4)
            ],
            {"ok": True, "snapshot_id": "s-ready", "page": {"title": "Netflix"}, "changed": True, "elements": [
                {"id": "e3", "role": "link", "name": "Marian", "value": ""},
            ]},
            {"ok": True, "snapshot_id": "s-after", "page": {"title": "Inicio - Netflix"}, "changed": True, "elements": [
                {"id": "e7", "role": "button", "name": "Marian", "value": ""},
            ]},
        ])
        acted = []
        registry = ToolRegistry()
        registry.register("web_inspect", "inspect", {"type": "object", "properties": {}}, lambda **_kwargs: next(inspections))
        registry.register("web_act", "act", {"type": "object", "properties": {}}, lambda **kwargs: acted.append(kwargs) or {"ok": True})
        state = plan_goal("Abre Netflix con la cuenta de Google de Marian y entra en el perfil de Marian")

        with patch("brain.agent_loop.time.sleep"):
            records = MochiAgentLoop()._fast_named_web_selection(registry, state)

        self.assertEqual(acted, [{
            "element_id": "e3", "action": "click", "snapshot_id": "s-ready",
        }])
        self.assertEqual([record[0] for record in records], ["web_inspect", "web_act", "web_inspect"])

    def test_pending_web_goal_cannot_close_browser_after_empty_loading_snapshot(self):
        inspection_count = [0]
        closes = []

        def inspect(**_kwargs):
            inspection_count[0] += 1
            if inspection_count[0] <= 8:
                return {
                    "ok": True,
                    "snapshot_id": f"loading-{inspection_count[0]}",
                    "page": {"title": "Netflix - Google Chrome"},
                    "changed": False,
                    "elements": [],
                }
            if inspection_count[0] == 9:
                return {
                    "ok": True,
                    "snapshot_id": "ready",
                    "page": {"title": "Netflix"},
                    "changed": True,
                    "elements": [{"id": "e3", "role": "link", "name": "Marian", "value": ""}],
                }
            return {
                "ok": True,
                "snapshot_id": "selected",
                "page": {"title": "Inicio - Netflix"},
                "changed": True,
                "elements": [{"id": "e7", "role": "button", "name": "Marian", "value": ""}],
            }

        registry = ToolRegistry()
        registry.register("browser_open", "web", {"type": "object", "properties": {}}, lambda **_kwargs: {"ok": True})
        registry.register("web_inspect", "inspect", {"type": "object", "properties": {}}, inspect)
        registry.register("web_act", "act", {"type": "object", "properties": {}}, lambda **_kwargs: {"ok": True})
        registry.register("close_window", "close", {"type": "object", "properties": {}}, lambda **kwargs: closes.append(kwargs) or {"ok": True})
        router = ScriptedRouter([
            BrainResult(True, tool_calls=[{"function": {"name": "browser_open", "arguments": {"target": "Netflix", "profile": "Marian"}}}]),
            BrainResult(True, tool_calls=[{"function": {"name": "close_window", "arguments": {"window": "Netflix - Google Chrome"}}}]),
            BrainResult(True, tool_calls=[{"function": {"name": "web_inspect", "arguments": {}}}]),
            BrainResult(True, tool_calls=[{"function": {"name": "web_act", "arguments": {"element_id": "e3", "action": "click", "snapshot_id": "ready"}}}]),
            BrainResult(True, tool_calls=[{"function": {"name": "web_inspect", "arguments": {}}}]),
        ])

        with patch("brain.agent_loop.time.sleep"):
            result = MochiAgentLoop(router=router, max_steps=6).run(
                "Abre Netflix con la cuenta de Google de Marian y entra en el perfil de Marian",
                registry,
            )

        self.assertEqual(closes, [])
        self.assertEqual(result["status"], "completed")
        self.assertNotIn("close_window", [entry["tool"] for entry in result["actions"]])

    def test_verified_normal_plan_stops_without_another_model_turn(self):
        inspection_count = [0]
        def inspect():
            inspection_count[0] += 1
            # The fast-path readiness probe runs seven times before falling
            # back because the page title does not match its expected target.
            if inspection_count[0] <= 7:
                return {"ok": True, "snapshot_id": "s0", "page": {"title": "Other"}, "changed": False, "elements": []}
            if inspection_count[0] == 8:
                return {"ok": True, "snapshot_id": "s1", "page": {"title": "Other"}, "changed": False, "elements": [{"id": "e1", "role": "link", "name": "Isaías", "value": ""}]}
            return {"ok": True, "snapshot_id": "s2", "page": {"title": "Other"}, "changed": True, "elements": [{"id": "e1", "role": "link", "name": "Isaías", "value": ""}]}
        registry = ToolRegistry()
        registry.register("browser_open", "web", {"type": "object", "properties": {}}, lambda **_kwargs: {"ok": True})
        registry.register("web_inspect", "inspect", {"type": "object", "properties": {}}, lambda **_kwargs: inspect())
        registry.register("web_act", "act", {"type": "object", "properties": {}}, lambda **_kwargs: {"ok": True})
        router = ScriptedRouter([
            BrainResult(True, tool_calls=[{"function": {"name": "browser_open", "arguments": {"target": "Netflix", "profile": "Marian"}}}]),
            BrainResult(True, tool_calls=[{"function": {"name": "web_inspect", "arguments": {}}}]),
            BrainResult(True, tool_calls=[{"function": {"name": "web_act", "arguments": {"element_id": "e1", "action": "click", "snapshot_id": "s1"}}}]),
            BrainResult(True, tool_calls=[{"function": {"name": "web_inspect", "arguments": {}}}]),
        ])
        result = MochiAgentLoop(router=router, max_steps=6).run("Abre Netflix con la cuenta de Google de Marian y luego elije el perfil de Isaías en Netflix.", registry)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["steps"], 4)
        self.assertEqual(len(router.calls), 0)

    def test_visible_active_profile_completes_without_clicking_account_button(self):
        inspections = iter([
            {"ok": True, "snapshot_id": "s1", "page": {"title": "Página de inicio — Netflix"}, "changed": False, "elements": [{"id": "e12", "role": "button", "name": "Isaías", "value": ""}]},
            {"ok": True, "snapshot_id": "s2", "page": {"title": "Página de inicio — Netflix"}, "changed": False, "elements": [{"id": "e12", "role": "button", "name": "Isaías", "value": ""}]},
            {"ok": True, "snapshot_id": "s3", "page": {"title": "Página de inicio — Netflix"}, "changed": False, "elements": [{"id": "e12", "role": "button", "name": "Isaías", "value": ""}]},
        ])
        acted = []
        registry = ToolRegistry()
        registry.register("browser_open", "web", {"type": "object", "properties": {}}, lambda **_kwargs: {"ok": True})
        registry.register("web_inspect", "inspect", {"type": "object", "properties": {}}, lambda **_kwargs: next(inspections))
        registry.register("web_act", "act", {"type": "object", "properties": {}}, lambda **kwargs: acted.append(kwargs) or {"ok": True})
        router = ScriptedRouter([BrainResult(True, tool_calls=[{"function": {"name": "browser_open", "arguments": {"target": "Netflix", "profile": "Marian"}}}])])
        result = MochiAgentLoop(router=router, max_steps=3).run("Abre Netflix con la cuenta de Google de Marian y luego elije el perfil de Isaías en Netflix.", registry)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["steps"], 1)
        self.assertEqual(acted, [])
        self.assertIn("perfil Isaías está activo", result["text"])

    def test_profile_switch_opens_current_menu_then_selects_exact_target(self):
        inspections = iter([
            {"ok": True, "snapshot_id": "s1", "page": {"title": "Netflix"}, "changed": False, "elements": [{"id": "e1", "role": "button", "name": "Marian", "value": ""}]},
            {"ok": True, "snapshot_id": "s2", "page": {"title": "Netflix"}, "changed": True, "elements": [{"id": "e2", "role": "menuitem", "name": "Isaías Isaías", "value": ""}]},
            {"ok": True, "snapshot_id": "s3", "page": {"title": "Inicio — Netflix"}, "changed": True, "elements": [{"id": "e3", "role": "button", "name": "Isaías", "value": ""}]},
        ])
        acted = []
        registry = ToolRegistry()
        registry.register("web_inspect", "inspect", {"type": "object", "properties": {}}, lambda **_kwargs: next(inspections))
        registry.register("web_act", "act", {"type": "object", "properties": {}}, lambda **kwargs: acted.append(kwargs) or {"ok": True})
        router = ScriptedRouter([
            BrainResult(True, tool_calls=[{"function": {"name": "web_inspect", "arguments": {}}}]),
            BrainResult(True, tool_calls=[{"function": {"name": "web_act", "arguments": {"element_id": "e1", "action": "click", "snapshot_id": "s1"}}}]),
            BrainResult(True, tool_calls=[{"function": {"name": "web_inspect", "arguments": {}}}]),
            BrainResult(True, tool_calls=[{"function": {"name": "web_act", "arguments": {"element_id": "e2", "action": "click", "snapshot_id": "s2"}}}]),
            BrainResult(True, tool_calls=[{"function": {"name": "web_inspect", "arguments": {}}}]),
        ])
        result = MochiAgentLoop(router=router, max_steps=6).run("En Netflix selecciona Isaías.", registry)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(acted, [
            {"element_id": "e1", "action": "click", "snapshot_id": "s1"},
            {"element_id": "e2", "action": "click", "snapshot_id": "s2"},
        ])

    def test_target_only_profile_switch_uses_unique_observed_active_profile_evidence(self):
        inspections = iter([
            {"ok": True, "snapshot_id": "s1", "page": {"title": "Netflix"}, "changed": False,
             "elements": [
                 {"id": "kids", "role": "button", "name": "Niños", "value": ""},
                 {"id": "current", "role": "button", "name": "Marian", "value": ""},
             ],
             "context": ["Continuar viendo contenido de Marian", "Marian"]},
            {"ok": True, "snapshot_id": "s2", "page": {"title": "Netflix"}, "changed": True,
             "elements": [{"id": "target", "role": "menuitem", "name": "Isaías", "value": ""}]},
            {"ok": True, "snapshot_id": "s3", "page": {"title": "Netflix"}, "changed": True,
             "elements": [{"id": "active", "role": "button", "name": "Isaías", "value": ""}]},
        ])
        acted = []
        registry = ToolRegistry()
        registry.register("web_inspect", "inspect", {"type": "object", "properties": {}}, lambda **_kwargs: next(inspections))
        registry.register("web_act", "act", {"type": "object", "properties": {}}, lambda **kwargs: acted.append(kwargs) or {"ok": True})
        state = plan_goal("En Netflix selecciona el perfil de Isaías")

        records = MochiAgentLoop()._fast_named_web_selection(registry, state)

        self.assertEqual([item["element_id"] for item in acted], ["current", "target"])
        self.assertEqual([record[0] for record in records], ["web_inspect", "web_act", "web_inspect", "web_act", "web_inspect"])

    def test_profile_menu_without_target_stops_as_blocked(self):
        inspections = iter([
            {"ok": True, "snapshot_id": "s1", "page": {"title": "Netflix"}, "changed": False, "elements": [{"id": "e1", "role": "button", "name": "Marian", "value": ""}]},
            {"ok": True, "snapshot_id": "s2", "page": {"title": "Netflix"}, "changed": True, "elements": [{"id": "e2", "role": "menuitem", "name": "Paulaa", "value": ""}]},
        ])
        acted = []
        registry = ToolRegistry()
        registry.register("web_inspect", "inspect", {"type": "object", "properties": {}}, lambda **_kwargs: next(inspections))
        registry.register("web_act", "act", {"type": "object", "properties": {}}, lambda **kwargs: acted.append(kwargs) or {"ok": True})
        router = ScriptedRouter([
            BrainResult(True, tool_calls=[{"function": {"name": "web_inspect", "arguments": {}}}]),
            BrainResult(True, tool_calls=[{"function": {"name": "web_act", "arguments": {"element_id": "e1", "action": "click", "snapshot_id": "s1"}}}]),
            BrainResult(True, tool_calls=[{"function": {"name": "web_inspect", "arguments": {}}}]),
        ])
        result = MochiAgentLoop(router=router, max_steps=6).run("En Netflix selecciona Isaías.", registry)
        self.assertEqual(result["status"], "blocked")
        # The deterministic profile plan performs its inspect/open-menu/inspect
        # sequence inside one agent turn.  The important contract is that a
        # missing target never becomes a false success or an unrelated click.
        self.assertEqual(result["steps"], 1)
        self.assertEqual([item["element_id"] for item in acted], ["e1"])
        self.assertIn("No encontré una opción seleccionable", result["text"])

    def test_internal_profile_request_recovers_from_model_no_tools_response(self):
        registry = ToolRegistry()
        registry.register("web_inspect", "inspect", {"type": "object", "properties": {}}, lambda **_kwargs: {
            "ok": True, "snapshot_id": "s1", "page": {"title": "Inicio — Netflix"}, "changed": False,
            "elements": [{"id": "e1", "role": "button", "name": "Marian", "value": ""}],
        })
        router = ScriptedRouter([BrainResult(True, text="No tengo esa capacidad.")])
        result = MochiAgentLoop(router=router, max_steps=3).run("Cambia la cuenta de Netflix de Isaías a Marian.", registry)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["steps"], 1)
        self.assertEqual([item["tool"] for item in result["actions"]], ["web_inspect"])

    def test_explicit_profile_transition_runs_after_the_first_observation(self):
        inspections = iter([
            {"ok": True, "snapshot_id": "s1", "page": {"title": "Inicio — Netflix"}, "changed": False, "elements": [{"id": "e12", "role": "button", "name": "Isaías", "value": ""}]},
            {"ok": True, "snapshot_id": "s2", "page": {"title": "Inicio — Netflix"}, "changed": True, "elements": [{"id": "e3", "role": "menuitem", "name": "Marian", "value": ""}]},
            {"ok": True, "snapshot_id": "s3", "page": {"title": "Inicio — Netflix"}, "changed": True, "elements": [{"id": "e12", "role": "button", "name": "Marian", "value": ""}]},
        ])
        acted = []
        registry = ToolRegistry()
        registry.register("web_inspect", "inspect", {"type": "object", "properties": {}}, lambda **_kwargs: next(inspections))
        registry.register("web_act", "act", {"type": "object", "properties": {}}, lambda **kwargs: acted.append(kwargs) or {"ok": True})
        router = ScriptedRouter([
            BrainResult(True, tool_calls=[{"function": {"name": "web_inspect", "arguments": {}}}]),
        ])
        result = MochiAgentLoop(router=router, max_steps=4).run("Cambia la cuenta de Netflix de Isaías a Marian.", registry)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["steps"], 1)
        self.assertEqual(acted, [
            {"element_id": "e12", "action": "click", "snapshot_id": "s1"},
            {"element_id": "e3", "action": "click", "snapshot_id": "s2"},
        ])
        self.assertEqual(result["goal_state"]["site_profile_source"], "Isaías")

    def test_page_search_uses_generic_accessibility_micro_plan(self):
        inspections = iter([
            {"ok": True, "snapshot_id": "s1", "page": {"title": "Inicio — Netflix"}, "changed": False, "elements": [{"id": "e1", "role": "button", "name": "Buscar", "value": ""}]},
            {"ok": True, "snapshot_id": "s2", "page": {"title": "Inicio — Netflix"}, "changed": True, "elements": [{"id": "e2", "role": "textbox", "name": "Buscar", "value": ""}]},
            {"ok": True, "snapshot_id": "s3", "page": {"title": "Inicio — Netflix"}, "changed": True, "elements": [{"id": "e3", "role": "textbox", "name": "Buscar", "value": "Rick y Morty"}]},
            {"ok": True, "snapshot_id": "s4", "page": {"title": "Resultados — Netflix"}, "changed": True, "elements": [{"id": "e4", "role": "link", "name": "Rick y Morty", "value": ""}]},
        ])
        acted = []
        registry = ToolRegistry()
        registry.register("browser_open", "open", {"type": "object", "properties": {}}, lambda **_kwargs: {"ok": True})
        registry.register("web_inspect", "inspect", {"type": "object", "properties": {}}, lambda **_kwargs: next(inspections))
        registry.register("web_act", "act", {"type": "object", "properties": {}}, lambda **kwargs: acted.append(kwargs) or {"ok": True})
        router = ScriptedRouter([BrainResult(True, tool_calls=[{"function": {"name": "browser_open", "arguments": {"target": "Netflix"}}}])])
        result = MochiAgentLoop(router=router, max_steps=3).run("Busca Rick y Morty en Netflix.", registry)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["steps"], 1)
        self.assertEqual(acted, [
            {"element_id": "e1", "action": "click", "snapshot_id": "s1"},
            {"element_id": "e2", "action": "type", "value": "Rick y Morty", "snapshot_id": "s2"},
            {"element_id": "e3", "action": "submit", "snapshot_id": "s3"},
        ])
        self.assertIn("busqué Rick y Morty dentro de Netflix", result["text"])

    def test_page_search_waits_for_a_nonempty_accessibility_tree_after_open(self):
        inspections = iter([
            {"ok": True, "snapshot_id": "s-empty-1", "page": {"title": "YouTube"}, "changed": True, "elements": [], "context": []},
            {"ok": True, "snapshot_id": "s-empty-2", "page": {"title": "YouTube"}, "changed": False, "elements": [], "context": []},
            {"ok": True, "snapshot_id": "s1", "page": {"title": "YouTube"}, "changed": True, "elements": [{"id": "e1", "role": "textbox", "name": "Buscar", "value": ""}]},
            {"ok": True, "snapshot_id": "s2", "page": {"title": "YouTube"}, "changed": True, "elements": [{"id": "e2", "role": "textbox", "name": "Buscar", "value": "Mochi"}]},
            {"ok": True, "snapshot_id": "s3", "page": {"title": "Resultados — YouTube"}, "changed": True, "elements": [{"id": "e3", "role": "link", "name": "Mochi", "value": ""}]},
        ])
        acted = []
        registry = ToolRegistry()
        registry.register("browser_open", "open", {"type": "object", "properties": {}}, lambda **_kwargs: {"ok": True})
        registry.register("web_inspect", "inspect", {"type": "object", "properties": {}}, lambda **_kwargs: next(inspections))
        registry.register("web_act", "act", {"type": "object", "properties": {}}, lambda **kwargs: acted.append(kwargs) or {"ok": True})
        router = ScriptedRouter([BrainResult(True, tool_calls=[{"function": {"name": "browser_open", "arguments": {"target": "YouTube"}}}])])

        with patch("brain.agent_loop.time.sleep"):
            result = MochiAgentLoop(router=router, max_steps=3).run("Busca Mochi en YouTube.", registry)

        self.assertEqual(result["status"], "completed")
        self.assertEqual(acted, [
            {"element_id": "e1", "action": "type", "value": "Mochi", "snapshot_id": "s1"},
            {"element_id": "e2", "action": "submit", "snapshot_id": "s2"},
        ])

    def test_delayed_spa_result_resumes_exact_selection_on_a_new_snapshot(self):
        inspections = iter([
            {"ok": True, "snapshot_id": "s-late", "page": {"title": "Netflix"}, "changed": True, "elements": [
                {"id": "e12", "role": "link", "name": "Acaramelados", "value": ""},
                {"id": "e13", "role": "link", "name": "En la tormenta", "value": ""},
            ]},
            {"ok": True, "snapshot_id": "s-detail", "page": {"title": "Acaramelados - Netflix"}, "changed": True, "elements": []},
        ])
        acted = []
        registry = ToolRegistry()
        registry.register("web_inspect", "inspect", {"type": "object", "properties": {}}, lambda **_kwargs: next(inspections))
        registry.register("web_act", "act", {"type": "object", "properties": {}}, lambda **kwargs: acted.append(kwargs) or {"ok": True})
        state = plan_goal("Busca Acaramelados en Netflix y abre Acaramelados")
        state.mark_completed("browser_open")
        state.mark_completed("search_page")

        with patch("brain.agent_loop.time.sleep"):
            operation, records = MochiAgentLoop()._fast_pending_media_operation(registry, state)

        self.assertEqual(operation, "select_result")
        self.assertEqual(acted, [{
            "element_id": "e12", "action": "click", "snapshot_id": "s-late",
        }])
        self.assertEqual([record[0] for record in records], ["web_inspect", "web_act", "web_inspect"])

    def test_page_search_opens_one_named_accessible_result_generically(self):
        inspections = iter([
            {"ok": True, "snapshot_id": "s1", "page": {"title": "YouTube"}, "changed": False, "elements": [{"id": "e1", "role": "textbox", "name": "Buscar", "value": ""}]},
            {"ok": True, "snapshot_id": "s2", "page": {"title": "YouTube"}, "changed": True, "elements": [{"id": "e2", "role": "textbox", "name": "Buscar", "value": "Prueba de Mochi"}]},
            {"ok": True, "snapshot_id": "s3", "page": {"title": "Resultados — YouTube"}, "changed": True, "elements": [{"id": "e3", "role": "link", "name": "Prueba de Mochi", "value": ""}]},
            {"ok": True, "snapshot_id": "s4", "page": {"title": "Prueba de Mochi — YouTube"}, "changed": True, "elements": [{"id": "e4", "role": "button", "name": "Pausa", "value": ""}]},
        ])
        acted = []
        registry = ToolRegistry()
        registry.register("browser_open", "open", {"type": "object", "properties": {}}, lambda **_kwargs: {"ok": True})
        registry.register("web_inspect", "inspect", {"type": "object", "properties": {}}, lambda **_kwargs: next(inspections))
        registry.register("web_act", "act", {"type": "object", "properties": {}}, lambda **kwargs: acted.append(kwargs) or {"ok": True})
        router = ScriptedRouter([BrainResult(True, tool_calls=[{"function": {"name": "browser_open", "arguments": {"target": "YouTube"}}}])])

        result = MochiAgentLoop(router=router, max_steps=3).run(
            "Busca Prueba de Mochi en YouTube y abre Prueba de Mochi.",
            registry,
        )

        self.assertEqual((result["status"], result["steps"]), ("completed", 1))
        self.assertEqual(acted, [
            {"element_id": "e1", "action": "type", "value": "Prueba de Mochi", "snapshot_id": "s1"},
            {"element_id": "e2", "action": "submit", "snapshot_id": "s2"},
            {"element_id": "e3", "action": "click", "snapshot_id": "s3"},
        ])
        self.assertIn("abrí Prueba de Mochi", result["text"])

    def test_named_page_result_is_searched_directly_with_its_author_context(self):
        inspections = iter([
            {"ok": True, "snapshot_id": "s1", "page": {"title": "YouTube"}, "changed": False, "elements": [{"id": "e1", "role": "textbox", "name": "Buscar", "value": ""}]},
            {"ok": True, "snapshot_id": "s2", "page": {"title": "YouTube"}, "changed": True, "elements": [{"id": "e2", "role": "textbox", "name": "Buscar", "value": "Fortnite lo hizo de nuevo el Rubius"}]},
            {"ok": True, "snapshot_id": "s3", "page": {"title": "Fortnite - YouTube"}, "changed": True, "elements": [
                {"id": "e3", "role": "link", "name": "El Rubius vuelve a Fortnite, lo hizo de nuevo 12 minutos", "value": ""},
                {"id": "e4", "role": "textbox", "name": "Buscar", "value": ""},
            ]},
            {"ok": True, "snapshot_id": "s4", "page": {"title": "El Rubius vuelve a Fortnite - YouTube"}, "changed": True, "elements": [{"id": "e5", "role": "button", "name": "Pausa", "value": ""}]},
        ])
        acted = []
        registry = ToolRegistry()
        registry.register("browser_open", "open", {"type": "object", "properties": {}}, lambda **_kwargs: {"ok": True})
        registry.register("web_inspect", "inspect", {"type": "object", "properties": {}}, lambda **_kwargs: next(inspections))
        registry.register("web_act", "act", {"type": "object", "properties": {}}, lambda **kwargs: acted.append(kwargs) or {"ok": True})
        router = ScriptedRouter([BrainResult(True, tool_calls=[{"function": {"name": "browser_open", "arguments": {"target": "YouTube"}}}])])

        with patch("brain.agent_loop.time.sleep"):
            result = MochiAgentLoop(router=router, max_steps=3).run(
                "Busca el Rubius en YouTube y abre Fortnite lo hizo de nuevo.",
                registry,
            )

        self.assertEqual((result["status"], result["steps"]), ("completed", 1))
        self.assertEqual(acted, [
            {"element_id": "e1", "action": "type", "value": "Fortnite lo hizo de nuevo el Rubius", "snapshot_id": "s1"},
            {"element_id": "e2", "action": "submit", "snapshot_id": "s2"},
            {"element_id": "e3", "action": "click", "snapshot_id": "s3"},
        ])
        self.assertIn("abrí Fortnite lo hizo de nuevo", result["text"])

    def test_named_offscreen_result_is_scrolled_then_opened_semantically(self):
        inspections = iter([
            {"ok": True, "snapshot_id": "s1", "page": {"title": "YouTube"}, "changed": False, "elements": [{"id": "e1", "role": "textbox", "name": "Buscar", "value": ""}]},
            {"ok": True, "snapshot_id": "s2", "page": {"title": "YouTube"}, "changed": True, "elements": [{"id": "e2", "role": "textbox", "name": "Buscar", "value": "Prueba de Mochi"}]},
            {"ok": True, "snapshot_id": "s3", "page": {"title": "Resultados - YouTube"}, "changed": True, "elements": [{"id": "e3", "role": "link", "name": "Otro resultado", "value": ""}]},
            {"ok": True, "snapshot_id": "s4", "page": {"title": "Resultados - YouTube"}, "changed": True, "elements": [{"id": "e4", "role": "link", "name": "Prueba de Mochi", "value": "", "offscreen": True}]},
            {"ok": True, "snapshot_id": "s5", "page": {"title": "Resultados - YouTube"}, "changed": True, "elements": [{"id": "e5", "role": "link", "name": "Prueba de Mochi", "value": "", "offscreen": False}]},
            {"ok": True, "snapshot_id": "s6", "page": {"title": "Prueba de Mochi - YouTube"}, "changed": True, "elements": [{"id": "e6", "role": "button", "name": "Pausa", "value": ""}]},
        ])
        acted = []
        registry = ToolRegistry()
        registry.register("browser_open", "open", {"type": "object", "properties": {}}, lambda **_kwargs: {"ok": True})
        registry.register("web_inspect", "inspect", {"type": "object", "properties": {}}, lambda **_kwargs: next(inspections))
        registry.register("web_act", "act", {"type": "object", "properties": {}}, lambda **kwargs: acted.append(kwargs) or {"ok": True})
        router = ScriptedRouter([BrainResult(True, tool_calls=[{"function": {"name": "browser_open", "arguments": {"target": "YouTube"}}}])])

        with patch("brain.agent_loop.time.sleep"):
            result = MochiAgentLoop(router=router, max_steps=3).run(
                "Busca Prueba de Mochi en YouTube y abre Prueba de Mochi.",
                registry,
            )

        self.assertEqual(result["status"], "completed")
        self.assertEqual(acted, [
            {"element_id": "e1", "action": "type", "value": "Prueba de Mochi", "snapshot_id": "s1"},
            {"element_id": "e2", "action": "submit", "snapshot_id": "s2"},
            {"element_id": "e4", "action": "scroll_into_view", "snapshot_id": "s4"},
            {"element_id": "e5", "action": "click", "snapshot_id": "s5"},
        ])

    def test_ambiguous_named_results_are_listed_instead_of_opening_one(self):
        inspections = iter([
            {"ok": True, "snapshot_id": "s1", "page": {"title": "YouTube"}, "changed": False, "elements": [{"id": "e1", "role": "textbox", "name": "Buscar", "value": ""}]},
            {"ok": True, "snapshot_id": "s2", "page": {"title": "YouTube"}, "changed": True, "elements": [{"id": "e2", "role": "textbox", "name": "Buscar", "value": "Prueba de Mochi"}]},
            {"ok": True, "snapshot_id": "s3", "page": {"title": "Resultados - YouTube"}, "changed": True, "elements": [
                {"id": "e3", "role": "link", "name": "Prueba de Mochi — Rubius Z — hace 2 días", "value": ""},
                {"id": "e4", "role": "link", "name": "Prueba de Mochi — elrubiusOMG — 2023", "value": ""},
            ]},
            {"ok": True, "snapshot_id": "s4", "page": {"title": "Resultados - YouTube"}, "changed": False, "elements": [
                {"id": "e5", "role": "link", "name": "Prueba de Mochi — Rubius Z — hace 2 días", "value": ""},
                {"id": "e6", "role": "link", "name": "Prueba de Mochi — elrubiusOMG — 2023", "value": ""},
            ]},
        ])
        acted = []
        registry = ToolRegistry()
        registry.register("browser_open", "open", {"type": "object", "properties": {}}, lambda **_kwargs: {"ok": True})
        registry.register("web_inspect", "inspect", {"type": "object", "properties": {}}, lambda **_kwargs: next(inspections))
        registry.register("web_act", "act", {"type": "object", "properties": {}}, lambda **kwargs: acted.append(kwargs) or {"ok": True})
        router = ScriptedRouter([
            BrainResult(True, tool_calls=[{"function": {"name": "browser_open", "arguments": {"target": "YouTube"}}}]),
            BrainResult(True, text=""),
        ])

        with patch("brain.agent_loop.time.sleep"):
            result = MochiAgentLoop(router=router, max_steps=3).run(
                "Busca Prueba de Mochi en YouTube y abre Prueba de Mochi.",
                registry,
            )

        self.assertEqual(result["status"], "partial")
        self.assertEqual(acted, [
            {"element_id": "e1", "action": "type", "value": "Prueba de Mochi", "snapshot_id": "s1"},
            {"element_id": "e2", "action": "submit", "snapshot_id": "s2"},
        ])
        self.assertIn("varias opciones", result["text"])
        self.assertIn("Rubius Z", result["text"])
        self.assertIn("2023", result["text"])

    def test_change_active_video_uses_the_current_tab_without_browser_open(self):
        inspections = iter([
            {"ok": True, "snapshot_id": "s1", "page": {"title": "YouTube"}, "changed": False, "elements": [{"id": "e1", "role": "textbox", "name": "Buscar", "value": ""}]},
            {"ok": True, "snapshot_id": "s2", "page": {"title": "YouTube"}, "changed": True, "elements": [{"id": "e2", "role": "textbox", "name": "Buscar", "value": "Prueba de Mochi"}]},
            {"ok": True, "snapshot_id": "s3", "page": {"title": "Resultados - YouTube"}, "changed": True, "elements": [{"id": "e3", "role": "link", "name": "Prueba de Mochi", "value": ""}]},
            {"ok": True, "snapshot_id": "s4", "page": {"title": "Prueba de Mochi - YouTube"}, "changed": True, "elements": [{"id": "e4", "role": "button", "name": "Pausa", "value": ""}]},
        ])
        acted = []
        opened = []
        registry = ToolRegistry()
        registry.register("browser_open", "open", {"type": "object", "properties": {}}, lambda **kwargs: opened.append(kwargs) or {"ok": True})
        registry.register("web_inspect", "inspect", {"type": "object", "properties": {}}, lambda **_kwargs: next(inspections))
        registry.register("web_act", "act", {"type": "object", "properties": {}}, lambda **kwargs: acted.append(kwargs) or {"ok": True})

        with patch("brain.agent_loop.time.sleep"):
            result = MochiAgentLoop(router=ScriptedRouter([]), max_steps=3).run(
                "Cambia de video a Prueba de Mochi.",
                registry,
            )

        self.assertEqual((result["status"], result["final_mode"]), ("completed", "deterministic_current_tab_video_change"))
        self.assertEqual(opened, [])
        self.assertEqual(acted, [
            {"element_id": "e1", "action": "type", "value": "Prueba de Mochi", "snapshot_id": "s1"},
            {"element_id": "e2", "action": "submit", "snapshot_id": "s2"},
            {"element_id": "e3", "action": "click", "snapshot_id": "s3"},
        ])
        self.assertIn("pestaña actual", result["text"])

    def test_change_active_video_rejects_a_fallback_browser_tab(self):
        opened = []
        registry = ToolRegistry()
        registry.register("browser_open", "open", {"type": "object", "properties": {}}, lambda **kwargs: opened.append(kwargs) or {"ok": True})
        registry.register("web_inspect", "inspect", {"type": "object", "properties": {}}, lambda **_kwargs: {
            "ok": True,
            "snapshot_id": "s1",
            "page": {"title": "YouTube"},
            "changed": False,
            "elements": [{"id": "e1", "role": "link", "name": "Inicio", "value": ""}],
        })
        registry.register("web_act", "act", {"type": "object", "properties": {}}, lambda **_kwargs: {"ok": True})
        router = ScriptedRouter([
            BrainResult(True, tool_calls=[{"function": {"name": "browser_open", "arguments": {"target": "YouTube"}}}]),
            BrainResult(True, text="No hay un resultado accesible."),
        ])

        with patch("brain.agent_loop.time.sleep"):
            result = MochiAgentLoop(router=router, max_steps=2).run(
                "Cambia de video a Prueba de Mochi.",
                registry,
            )

        self.assertEqual(opened, [])
        rejected = next(action for action in result["actions"] if action["tool"] == "browser_open")
        self.assertEqual(rejected["result"]["error"], "invalid_scope_action")

    def test_direct_media_request_waits_for_results_and_confirms_playback(self):
        """A direct in-site video lookup must not stop at opening YouTube.

        The result link is deliberately delayed by one UIA publication turn,
        matching the SPA timing that previously left the request half-done.
        Playback then requires a separate, observed Play → Pause transition.
        """
        inspections = iter([
            {"ok": True, "snapshot_id": "s1", "page": {"title": "YouTube"}, "changed": False, "elements": [{"id": "e1", "role": "textbox", "name": "Buscar", "value": ""}]},
            {"ok": True, "snapshot_id": "s2", "page": {"title": "YouTube"}, "changed": True, "elements": [{"id": "e2", "role": "textbox", "name": "Buscar", "value": "Moviendo caderas de Daddy Yankee"}]},
            {"ok": True, "snapshot_id": "s3", "page": {"title": "Resultados - YouTube"}, "changed": True, "elements": [{"id": "e3", "role": "textbox", "name": "Buscar", "value": ""}]},
            {"ok": True, "snapshot_id": "s4", "page": {"title": "Resultados - YouTube"}, "changed": False, "elements": []},
            {"ok": True, "snapshot_id": "s5", "page": {"title": "Resultados - YouTube"}, "changed": True, "elements": [{"id": "e5", "role": "link", "name": "Moviendo caderas", "value": ""}]},
            {"ok": True, "snapshot_id": "s6", "page": {"title": "Moviendo caderas - YouTube"}, "changed": True, "elements": [{"id": "e6", "role": "button", "name": "Reproducir", "value": ""}]},
            {"ok": True, "snapshot_id": "s7", "page": {"title": "Moviendo caderas - YouTube"}, "changed": False, "elements": [{"id": "e7", "role": "button", "name": "Reproducir", "value": ""}]},
            {"ok": True, "snapshot_id": "s8", "page": {"title": "Moviendo caderas - YouTube"}, "changed": True, "elements": [{"id": "e8", "role": "button", "name": "Pausa", "value": ""}]},
        ])
        acted = []
        registry = ToolRegistry()
        registry.register("browser_open", "open", {"type": "object", "properties": {}}, lambda **_kwargs: {"ok": True})
        registry.register("web_inspect", "inspect", {"type": "object", "properties": {}}, lambda **_kwargs: next(inspections))
        registry.register("web_act", "act", {"type": "object", "properties": {}}, lambda **kwargs: acted.append(kwargs) or {"ok": True})
        router = ScriptedRouter([
            BrainResult(True, tool_calls=[{"function": {"name": "browser_open", "arguments": {"target": "YouTube"}}}]),
            BrainResult(True, text=""),
        ])

        with patch("brain.agent_loop.time.sleep"):
            result = MochiAgentLoop(router=router, max_steps=3).run(
                "Pon Moviendo caderas de Daddy Yankee en YouTube.", registry,
            )

        self.assertEqual(result["status"], "completed")
        self.assertEqual(acted, [
            {"element_id": "e1", "action": "type", "value": "Moviendo caderas de Daddy Yankee", "snapshot_id": "s1"},
            {"element_id": "e2", "action": "submit", "snapshot_id": "s2"},
            {"element_id": "e5", "action": "click", "snapshot_id": "s5"},
            {"element_id": "e7", "action": "keyboard_activate", "snapshot_id": "s7"},
        ])
        self.assertIn("confirmé que empezó la reproducción", result["text"])

    def test_direct_media_request_skips_same_named_suggestions_and_uses_video_card(self):
        """A search suggestion is not a playable result, even if it is exact.

        This reproduces the real YouTube snapshot: ``e14`` and ``e15`` are
        same-named search suggestions, while ``e18`` is the direct video link
        with a duration.  The player uses shortcut-suffixed labels once the
        direct video page has opened.
        """
        inspections = iter([
            {"ok": True, "snapshot_id": "s1", "page": {"title": "YouTube"}, "changed": False, "elements": [
                {"id": "e1", "role": "textbox", "name": "Buscar", "value": ""},
            ]},
            {"ok": True, "snapshot_id": "s2", "page": {"title": "YouTube"}, "changed": True, "elements": [
                {"id": "e2", "role": "textbox", "name": "Buscar", "value": "moviendo caderas oficial de daddy yanke"},
            ]},
            {"ok": True, "snapshot_id": "s3", "page": {"title": "Resultados - YouTube"}, "changed": True, "elements": [
                {"id": "e14", "role": "link", "name": "moviendo caderas oficial de daddy yankee", "value": ""},
                {"id": "e15", "role": "link", "name": "moviendo caderas oficial de daddy yanke", "value": ""},
                {"id": "e18", "role": "link", "name": "Yandel - Moviendo Caderas (Official Video) ft. Daddy Yankee · 3:28", "value": ""},
                {"id": "e21", "role": "link", "name": "Yandel - Moviendo Caderas (Official Video) ft. Daddy Yankee 3 minutos y 28 segundos", "value": ""},
            ]},
            {"ok": True, "snapshot_id": "s4", "page": {"title": "Yandel - Moviendo Caderas - YouTube"}, "changed": True, "elements": [
                {"id": "e4", "role": "button", "name": "Reproducir (k)", "value": ""},
            ]},
            {"ok": True, "snapshot_id": "s5", "page": {"title": "Yandel - Moviendo Caderas - YouTube"}, "changed": False, "elements": [
                {"id": "e5", "role": "button", "name": "Reproducir (k)", "value": ""},
            ]},
            {"ok": True, "snapshot_id": "s6", "page": {"title": "Yandel - Moviendo Caderas - YouTube"}, "changed": True, "elements": [
                {"id": "e6", "role": "button", "name": "Pausar (k)", "value": ""},
            ]},
        ])
        acted = []
        registry = ToolRegistry()
        registry.register("browser_open", "open", {"type": "object", "properties": {}}, lambda **_kwargs: {"ok": True})
        registry.register("web_inspect", "inspect", {"type": "object", "properties": {}}, lambda **_kwargs: next(inspections))
        registry.register("web_act", "act", {"type": "object", "properties": {}}, lambda **kwargs: acted.append(kwargs) or {"ok": True})
        router = ScriptedRouter([
            BrainResult(True, tool_calls=[{"function": {"name": "browser_open", "arguments": {"target": "YouTube"}}}]),
            BrainResult(True, text=""),
        ])

        with patch("brain.agent_loop.time.sleep"):
            result = MochiAgentLoop(router=router, max_steps=3).run(
                "reproduce moviendo caderas oficial de daddy yanke en youtube", registry,
            )

        self.assertEqual(result["status"], "completed")
        self.assertEqual(acted, [
            {"element_id": "e1", "action": "type", "value": "moviendo caderas oficial de daddy yanke", "snapshot_id": "s1"},
            {"element_id": "e2", "action": "submit", "snapshot_id": "s2"},
            {"element_id": "e18", "action": "click", "snapshot_id": "s3"},
            {"element_id": "e5", "action": "keyboard_activate", "snapshot_id": "s5"},
        ])
        self.assertIn("confirmé que empezó la reproducción", result["text"])

    def test_direct_media_request_waits_past_autocomplete_before_listing_choices(self):
        """Autocomplete alone must not end a playable-video search early."""
        inspections = iter([
            {"ok": True, "snapshot_id": "s1", "page": {"title": "YouTube"}, "changed": False, "elements": [
                {"id": "e1", "role": "textbox", "name": "Buscar", "value": ""},
            ]},
            {"ok": True, "snapshot_id": "s2", "page": {"title": "YouTube"}, "changed": True, "elements": [
                {"id": "e2", "role": "textbox", "name": "Buscar", "value": "moviendo caderas oficial de daddy yanke"},
            ]},
            # First post-submit tree: the page shell is ready but no results.
            {"ok": True, "snapshot_id": "s3", "page": {"title": "Resultados - YouTube"}, "changed": True, "elements": []},
            # Targeted text inspection can see just autocomplete suggestions.
            {"ok": True, "snapshot_id": "s4", "page": {"title": "Resultados - YouTube"}, "changed": False, "elements": [
                {"id": "e14", "role": "link", "name": "moviendo caderas oficial de daddy yankee", "value": ""},
                {"id": "e15", "role": "link", "name": "moviendo caderas oficial de daddy yanke", "value": ""},
            ]},
            # The same normal snapshot then publishes the real card.
            {"ok": True, "snapshot_id": "s5", "page": {"title": "Resultados - YouTube"}, "changed": True, "elements": [
                {"id": "e14", "role": "link", "name": "moviendo caderas oficial de daddy yankee", "value": ""},
                {"id": "e15", "role": "link", "name": "moviendo caderas oficial de daddy yanke", "value": ""},
                {"id": "e18", "role": "link", "name": "Yandel - Moviendo Caderas (Official Video) ft. Daddy Yankee · 3:28", "value": ""},
            ]},
            {"ok": True, "snapshot_id": "s6", "page": {"title": "Yandel - Moviendo Caderas - YouTube"}, "changed": True, "elements": [
                {"id": "e6", "role": "button", "name": "Reproducir (k)", "value": ""},
            ]},
            {"ok": True, "snapshot_id": "s7", "page": {"title": "Yandel - Moviendo Caderas - YouTube"}, "changed": False, "elements": [
                {"id": "e7", "role": "button", "name": "Reproducir (k)", "value": ""},
            ]},
            {"ok": True, "snapshot_id": "s8", "page": {"title": "Yandel - Moviendo Caderas - YouTube"}, "changed": True, "elements": [
                {"id": "e8", "role": "button", "name": "Pausar (k)", "value": ""},
            ]},
        ])
        acted = []
        registry = ToolRegistry()
        registry.register("browser_open", "open", {"type": "object", "properties": {}}, lambda **_kwargs: {"ok": True})
        registry.register("web_inspect", "inspect", {"type": "object", "properties": {}}, lambda **_kwargs: next(inspections))
        registry.register("web_act", "act", {"type": "object", "properties": {}}, lambda **kwargs: acted.append(kwargs) or {"ok": True})
        router = ScriptedRouter([
            BrainResult(True, tool_calls=[{"function": {"name": "browser_open", "arguments": {"target": "YouTube"}}}]),
            BrainResult(True, text=""),
        ])

        with patch("brain.agent_loop.time.sleep"):
            result = MochiAgentLoop(router=router, max_steps=3).run(
                "reproduce moviendo caderas oficial de daddy yanke en youtube", registry,
            )

        self.assertEqual(result["status"], "completed")
        self.assertEqual(acted[2], {
            "element_id": "e18", "action": "click", "snapshot_id": "s5",
        })
        self.assertFalse(any(call.get("element_id") in {"e14", "e15"} for call in acted))

    def test_generic_playback_button_uses_timeline_progress_without_toggling(self):
        """A non-stateful player button must not pause an already playing video."""
        state = plan_goal("reproduce prueba en youtube")
        state.mark_completed("select_result")
        inspections = iter([
            {"ok": True, "snapshot_id": "s1", "page": {"title": "Prueba - YouTube"}, "changed": False, "elements": [
                {"id": "e1", "role": "button", "name": "Botón de reproducción (k)", "value": ""},
                {"id": "e2", "role": "slider", "name": "Control deslizante", "value": "", "range": {"minimum": 0, "maximum": 207, "value": 7}},
                {"id": "e3", "role": "slider", "name": "Volumen", "value": "", "range": {"minimum": 0, "maximum": 100, "value": 25}},
            ]},
            {"ok": True, "snapshot_id": "s2", "page": {"title": "Prueba - YouTube"}, "changed": True, "elements": [
                {"id": "e4", "role": "button", "name": "Botón de reproducción (k)", "value": ""},
                {"id": "e5", "role": "slider", "name": "Control deslizante", "value": "", "range": {"minimum": 0, "maximum": 207, "value": 7}},
                {"id": "e6", "role": "slider", "name": "Volumen", "value": "", "range": {"minimum": 0, "maximum": 100, "value": 25}},
            ]},
            {"ok": True, "snapshot_id": "s3", "page": {"title": "Prueba - YouTube"}, "changed": True, "elements": [
                {"id": "e7", "role": "button", "name": "Botón de reproducción (k)", "value": ""},
                {"id": "e8", "role": "slider", "name": "Control deslizante", "value": "", "range": {"minimum": 0, "maximum": 207, "value": 8}},
                {"id": "e9", "role": "slider", "name": "Volumen", "value": "", "range": {"minimum": 0, "maximum": 100, "value": 25}},
            ]},
        ])
        acted = []
        registry = ToolRegistry()
        registry.register("web_inspect", "inspect", {"type": "object", "properties": {}}, lambda **_kwargs: next(inspections))
        registry.register("web_act", "act", {"type": "object", "properties": {}}, lambda **kwargs: acted.append(kwargs) or {"ok": True})

        with patch("brain.agent_loop.time.sleep"):
            records = MochiAgentLoop(router=ScriptedRouter([]))._fast_play_media(registry, state)

        self.assertEqual(acted, [
            {"element_id": "e2", "action": "focus", "snapshot_id": "s1"},
        ])
        self.assertEqual(state.step("play_media").status, "completed")
        self.assertEqual(state.step("play_media").reason, "timeline_progressed")
        self.assertEqual(
            [record[0] for record in records],
            ["web_inspect", "web_act", "web_inspect", "web_inspect"],
        )

    def test_changed_snapshot_after_generic_playback_click_is_not_goal_success(self):
        """A changed player view is not evidence of playback by itself."""
        state = plan_goal("reproduce prueba en youtube")
        state.mark_completed("select_result")
        inspections = iter([
            {"ok": True, "snapshot_id": "s1", "page": {"title": "Prueba - YouTube"}, "changed": False, "elements": [
                {"id": "e1", "role": "button", "name": "Botón de reproducción (k)", "value": ""},
                {"id": "e2", "role": "slider", "name": "Control deslizante", "value": "", "range": {"minimum": 0, "maximum": 207, "value": 7}},
            ]},
            {"ok": True, "snapshot_id": "s2", "page": {"title": "Prueba - YouTube"}, "changed": False, "elements": [
                {"id": "e3", "role": "button", "name": "Botón de reproducción (k)", "value": ""},
                {"id": "e4", "role": "slider", "name": "Control deslizante", "value": "", "range": {"minimum": 0, "maximum": 207, "value": 7}},
            ]},
            {"ok": True, "snapshot_id": "s3", "page": {"title": "Prueba - YouTube"}, "changed": True, "elements": [
                {"id": "e5", "role": "button", "name": "Botón de reproducción (k)", "value": ""},
                {"id": "e6", "role": "slider", "name": "Control deslizante", "value": "", "range": {"minimum": 0, "maximum": 207, "value": 7}},
            ]},
            {"ok": True, "snapshot_id": "s4", "page": {"title": "Prueba - YouTube"}, "changed": True, "elements": [
                {"id": "e7", "role": "button", "name": "Botón de reproducción (k)", "value": ""},
                {"id": "e8", "role": "slider", "name": "Control deslizante", "value": "", "range": {"minimum": 0, "maximum": 207, "value": 7}},
            ]},
            {"ok": True, "snapshot_id": "s5", "page": {"title": "Prueba - YouTube"}, "changed": False, "elements": [
                {"id": "e9", "role": "button", "name": "Botón de reproducción (k)", "value": ""},
                {"id": "e10", "role": "slider", "name": "Control deslizante", "value": "", "range": {"minimum": 0, "maximum": 207, "value": 7}},
            ]},
            {"ok": True, "snapshot_id": "s6", "page": {"title": "Prueba - YouTube"}, "changed": False, "elements": [
                {"id": "e11", "role": "button", "name": "Botón de reproducción (k)", "value": ""},
                {"id": "e12", "role": "slider", "name": "Control deslizante", "value": "", "range": {"minimum": 0, "maximum": 207, "value": 7}},
            ]},
            {"ok": True, "snapshot_id": "s7", "page": {"title": "Prueba - YouTube"}, "changed": False, "elements": [
                {"id": "e13", "role": "button", "name": "Botón de reproducción (k)", "value": ""},
                {"id": "e14", "role": "slider", "name": "Control deslizante", "value": "", "range": {"minimum": 0, "maximum": 207, "value": 7}},
            ]},
            {"ok": True, "snapshot_id": "s8", "page": {"title": "Prueba - YouTube"}, "changed": False, "elements": [
                {"id": "e15", "role": "button", "name": "Botón de reproducción (k)", "value": ""},
                {"id": "e16", "role": "slider", "name": "Control deslizante", "value": "", "range": {"minimum": 0, "maximum": 207, "value": 7}},
            ]},
            {"ok": True, "snapshot_id": "s9", "page": {"title": "Prueba - YouTube"}, "changed": False, "elements": [
                {"id": "e17", "role": "button", "name": "Botón de reproducción (k)", "value": ""},
                {"id": "e18", "role": "slider", "name": "Control deslizante", "value": "", "range": {"minimum": 0, "maximum": 207, "value": 7}},
            ]},
        ])
        acted = []
        registry = ToolRegistry()
        registry.register("web_inspect", "inspect", {"type": "object", "properties": {}}, lambda **_kwargs: next(inspections))
        registry.register("web_act", "act", {"type": "object", "properties": {}}, lambda **kwargs: acted.append(kwargs) or {"ok": True})

        with patch("brain.agent_loop.time.sleep"):
            records = MochiAgentLoop(router=ScriptedRouter([]))._fast_play_media(registry, state)

        action_record = next(
            record for record in records
            if record[0] == "web_act" and record[1].get("action") == "keyboard_activate"
        )
        final_observation = records[-1][2]
        self.assertEqual(action_record[1], {
            "element_id": "e5", "action": "keyboard_activate", "snapshot_id": "s3",
        })
        state.note_web_action("play_media")
        state.verify_web_observation(final_observation)
        self.assertEqual(state.step("play_media").status, "pending")

    def test_progress_on_a_different_video_does_not_confirm_requested_playback(self):
        """A player transition must preserve the result selected by the user."""
        state = plan_goal("reproduce prueba en youtube")
        state.mark_completed("select_result")
        inspections = iter([
            {"ok": True, "snapshot_id": "s1", "page": {"title": "Prueba - YouTube"}, "changed": False, "elements": [
                {"id": "e1", "role": "button", "name": "Botón de reproducción (k)", "value": ""},
                {"id": "e2", "role": "slider", "name": "Control deslizante", "value": "", "range": {"minimum": 0, "maximum": 120, "value": 4}},
            ]},
            {"ok": True, "snapshot_id": "s2", "page": {"title": "Prueba - YouTube"}, "changed": False, "elements": [
                {"id": "e3", "role": "button", "name": "Botón de reproducción (k)", "value": ""},
                {"id": "e4", "role": "slider", "name": "Control deslizante", "value": "", "range": {"minimum": 0, "maximum": 120, "value": 4}},
            ]},
            {"ok": True, "snapshot_id": "s3", "page": {"title": "Otro video - YouTube"}, "changed": True, "elements": [
                {"id": "e5", "role": "button", "name": "Botón de reproducción (k)", "value": ""},
                {"id": "e6", "role": "slider", "name": "Control deslizante", "value": "", "range": {"minimum": 0, "maximum": 120, "value": 5}},
            ]},
        ])
        registry = ToolRegistry()
        registry.register("web_inspect", "inspect", {"type": "object", "properties": {}}, lambda **_kwargs: next(inspections))
        registry.register("web_act", "act", {"type": "object", "properties": {}}, lambda **_kwargs: {"ok": True})

        with patch("brain.agent_loop.time.sleep"):
            MochiAgentLoop(router=ScriptedRouter([]))._fast_play_media(registry, state)

        self.assertEqual(state.step("play_media").status, "pending")

    def test_premature_web_inspection_is_redirected_to_the_planned_browser_open(self):
        """Never search an old about:blank tab before opening the requested site."""
        inspections = iter([
            {"ok": True, "snapshot_id": "s1", "page": {"title": "YouTube"}, "changed": False, "elements": [{"id": "e1", "role": "textbox", "name": "Buscar", "value": ""}]},
            {"ok": True, "snapshot_id": "s2", "page": {"title": "YouTube"}, "changed": True, "elements": [{"id": "e2", "role": "textbox", "name": "Buscar", "value": "Moviendo caderas de Daddy Yankee"}]},
            {"ok": True, "snapshot_id": "s3", "page": {"title": "Resultados - YouTube"}, "changed": True, "elements": [{"id": "e3", "role": "link", "name": "Moviendo caderas", "value": ""}]},
            {"ok": True, "snapshot_id": "s4", "page": {"title": "Moviendo caderas - YouTube"}, "changed": True, "elements": [{"id": "e4", "role": "button", "name": "Pausa", "value": ""}]},
        ])
        opened, acted = [], []
        registry = ToolRegistry()
        registry.register("browser_open", "open", {"type": "object", "properties": {}}, lambda **kwargs: opened.append(kwargs) or {"ok": True})
        registry.register("web_inspect", "inspect", {"type": "object", "properties": {}}, lambda **_kwargs: next(inspections))
        registry.register("web_act", "act", {"type": "object", "properties": {}}, lambda **kwargs: acted.append(kwargs) or {"ok": True})
        router = ScriptedRouter([
            BrainResult(True, tool_calls=[{"function": {"name": "web_inspect", "arguments": {}}}]),
        ])

        with patch("brain.agent_loop.time.sleep"):
            result = MochiAgentLoop(router=router, max_steps=3).run(
                "Pon Moviendo caderas de Daddy Yankee en YouTube.", registry,
            )

        self.assertEqual(result["status"], "completed")
        self.assertEqual(opened, [{"target": "YouTube"}])
        self.assertEqual(result["actions"][0]["tool"], "browser_open")
        self.assertEqual([item["action"] for item in acted], ["type", "submit", "click"])

    def test_no_tools_reply_opens_the_planned_site_before_page_recovery(self):
        inspections = iter([
            {"ok": True, "snapshot_id": "s1", "page": {"title": "YouTube"}, "changed": False, "elements": [{"id": "e1", "role": "textbox", "name": "Buscar", "value": ""}]},
            {"ok": True, "snapshot_id": "s2", "page": {"title": "YouTube"}, "changed": True, "elements": [{"id": "e2", "role": "textbox", "name": "Buscar", "value": "Prueba"}]},
            {"ok": True, "snapshot_id": "s3", "page": {"title": "Resultados - YouTube"}, "changed": True, "elements": []},
        ])
        opened = []
        registry = ToolRegistry()
        registry.register("browser_open", "open", {"type": "object", "properties": {}}, lambda **kwargs: opened.append(kwargs) or {"ok": True})
        registry.register("web_inspect", "inspect", {"type": "object", "properties": {}}, lambda **_kwargs: next(inspections))
        registry.register("web_act", "act", {"type": "object", "properties": {}}, lambda **_kwargs: {"ok": True})
        with patch("brain.agent_loop.time.sleep"):
            result = MochiAgentLoop(router=ScriptedRouter([BrainResult(True, text="")]), max_steps=2).run(
                "Busca Prueba en YouTube.", registry,
            )

        self.assertEqual(result["status"], "completed")
        self.assertEqual(opened, [{"target": "YouTube"}])
        self.assertEqual(result["actions"][0]["tool"], "browser_open")

    def test_wrong_browser_destination_is_rejected_without_failing_the_site_plan(self):
        opened = []
        registry = ToolRegistry()
        registry.register("browser_open", "open", {"type": "object", "properties": {}}, lambda **kwargs: opened.append(kwargs) or {"ok": True})
        registry.register("web_inspect", "inspect", {"type": "object", "properties": {}}, lambda **_kwargs: {
            "ok": True, "snapshot_id": "s1", "page": {"title": "YouTube"}, "changed": False,
            "elements": [{"id": "e1", "role": "textbox", "name": "Buscar", "value": ""}],
        })
        registry.register("web_act", "act", {"type": "object", "properties": {}}, lambda **_kwargs: {"ok": True})
        router = ScriptedRouter([
            BrainResult(True, tool_calls=[{"function": {"name": "browser_open", "arguments": {"target": "https://www.google.com/search?q=YouTube"}}}]),
            BrainResult(True, tool_calls=[{"function": {"name": "browser_open", "arguments": {"target": "YouTube"}}}]),
        ])

        with patch("brain.agent_loop.time.sleep"):
            result = MochiAgentLoop(router=router, max_steps=2).run("Busca Prueba en YouTube.", registry)

        # The deterministic plan now opens the scoped destination before a
        # model turn, so the proposed Google fallback is never executed at all.
        self.assertEqual(result["actions"][0]["arguments"], {"target": "YouTube"})
        self.assertTrue(result["actions"][0]["result"]["ok"])
        self.assertEqual(opened, [{"target": "YouTube"}])

    def test_same_site_search_url_is_canonicalized_to_guarded_site_search(self):
        """A model may name YouTube's URL, but not bypass its UIA plan."""
        opened = []
        registry = ToolRegistry()
        registry.register("browser_open", "open", {"type": "object", "properties": {}}, lambda **kwargs: opened.append(kwargs) or {"ok": True})
        registry.register("web_inspect", "inspect", {"type": "object", "properties": {}}, lambda **_kwargs: {
            "ok": True, "snapshot_id": "s1", "page": {"title": "YouTube"}, "changed": False,
            "elements": [{"id": "e1", "role": "textbox", "name": "Buscar", "value": ""}],
        })
        registry.register("web_act", "act", {"type": "object", "properties": {}}, lambda **_kwargs: {"ok": False, "error": "test_stop"})
        router = ScriptedRouter([
            BrainResult(True, tool_calls=[{"function": {"name": "browser_open", "arguments": {
                "target": "https://www.youtube.com/search?q=prueba",
            }}}]),
            BrainResult(True, text=""),
        ])

        with patch("brain.agent_loop.time.sleep"):
            result = MochiAgentLoop(router=router, max_steps=2).run("Busca Prueba en YouTube.", registry)

        self.assertEqual(opened, [{"target": "YouTube"}])
        self.assertEqual(result["actions"][0]["arguments"], {"target": "YouTube"})
        self.assertTrue(MochiAgentLoop()._is_planned_browser_target("https://www.youtube.com", "YouTube", require_site_root=True))
        self.assertFalse(MochiAgentLoop()._is_planned_browser_target("https://www.youtube.com/search?q=prueba", "YouTube", require_site_root=True))

    def test_in_page_plan_discards_stale_tool_calls_after_browser_open(self):
        inspections = iter([
            {"ok": True, "snapshot_id": "s1", "page": {"title": "YouTube"}, "changed": False, "elements": [{"id": "e1", "role": "textbox", "name": "Buscar", "value": ""}]},
            {"ok": True, "snapshot_id": "s2", "page": {"title": "YouTube"}, "changed": True, "elements": [{"id": "e2", "role": "textbox", "name": "Buscar", "value": "Prueba"}]},
            {"ok": True, "snapshot_id": "s3", "page": {"title": "Resultados - YouTube"}, "changed": True, "elements": [{"id": "e3", "role": "link", "name": "Prueba", "value": ""}]},
        ])
        external_searches = []
        registry = ToolRegistry()
        registry.register("browser_open", "open", {"type": "object", "properties": {}}, lambda **_kwargs: {"ok": True})
        registry.register("browser_search", "search", {"type": "object", "properties": {}}, lambda **kwargs: external_searches.append(kwargs) or {"ok": True})
        registry.register("web_inspect", "inspect", {"type": "object", "properties": {}}, lambda **_kwargs: next(inspections))
        registry.register("web_act", "act", {"type": "object", "properties": {}}, lambda **_kwargs: {"ok": True})
        router = ScriptedRouter([BrainResult(True, tool_calls=[
            {"function": {"name": "browser_open", "arguments": {"target": "YouTube"}}},
            {"function": {"name": "browser_search", "arguments": {"query": "Prueba"}}},
        ])])

        with patch("brain.agent_loop.time.sleep"):
            result = MochiAgentLoop(router=router, max_steps=3).run("Busca Prueba en YouTube.", registry)

        self.assertEqual(result["status"], "completed")
        self.assertEqual(external_searches, [])
        self.assertNotIn("browser_search", [item["tool"] for item in result["actions"]])

    def test_failed_web_inspection_is_not_blocked_as_a_duplicate_action(self):
        inspections = iter([
            {"ok": False, "error": "chrome_window_not_found", "message": "Chrome todavía no está visible."},
            {"ok": True, "snapshot_id": "s1", "page": {"title": "YouTube"}, "changed": False, "elements": []},
        ])
        registry = ToolRegistry()
        registry.register("web_inspect", "inspect", {"type": "object", "properties": {}}, lambda **_kwargs: next(inspections))
        router = ScriptedRouter([
            BrainResult(True, tool_calls=[{"function": {"name": "web_inspect", "arguments": {}}}]),
            BrainResult(True, tool_calls=[{"function": {"name": "web_inspect", "arguments": {}}}]),
            BrainResult(True, text=""),
        ])

        result = MochiAgentLoop(router=router, max_steps=3).run("Revisa la página actual.", registry)

        self.assertTrue(result["actions"][1]["result"]["ok"])
        self.assertNotEqual(result["actions"][1]["result"].get("error"), "duplicate_failed_action")

    def test_compound_site_media_command_finishes_every_verified_step(self):
        inspections = iter([
            # Profile gateway after opening the requested Chrome profile.
            {"ok": True, "snapshot_id": "s1", "page": {"title": "Netflix"}, "changed": False, "elements": [{"id": "e1", "role": "link", "name": "Marian", "value": ""}]},
            {"ok": True, "snapshot_id": "s2", "page": {"title": "Netflix"}, "changed": True, "elements": [{"id": "e2", "role": "textbox", "name": "Buscar", "value": ""}]},
            # Search and select the requested series.
            {"ok": True, "snapshot_id": "s3", "page": {"title": "Netflix"}, "changed": False, "elements": [{"id": "e3", "role": "textbox", "name": "Buscar", "value": ""}]},
            {"ok": True, "snapshot_id": "s4", "page": {"title": "Netflix"}, "changed": True, "elements": [{"id": "e4", "role": "textbox", "name": "Buscar", "value": "acaramelados"}]},
            {"ok": True, "snapshot_id": "s5", "page": {"title": "Resultados - Netflix"}, "changed": True, "elements": [{"id": "e5", "role": "link", "name": "Acaramelados", "value": ""}]},
            {"ok": True, "snapshot_id": "s6", "page": {"title": "Acaramelados - Netflix"}, "changed": True, "elements": [{"id": "e6", "role": "link", "name": "Capitulo 7", "value": ""}]},
            # Select the exact episode. Playback is not assumed: Netflix
            # exposes Play first, then exposes Pause only after activation.
            {"ok": True, "snapshot_id": "s7", "page": {"title": "Acaramelados - Netflix"}, "changed": False, "elements": [{"id": "e7", "role": "link", "name": "Capitulo 7", "value": ""}]},
            {"ok": True, "snapshot_id": "s8", "page": {"title": "Capitulo 7 - Netflix"}, "changed": True, "elements": [{"id": "e8", "role": "button", "name": "Reproducir", "value": ""}]},
            {"ok": True, "snapshot_id": "s9", "page": {"title": "Capitulo 7 - Netflix"}, "changed": False, "elements": [{"id": "e9", "role": "button", "name": "Reproducir", "value": ""}]},
            {"ok": True, "snapshot_id": "s10", "page": {"title": "Capitulo 7 - Netflix"}, "changed": True, "elements": [{"id": "e10", "role": "button", "name": "Pausa", "value": ""}]},
            {"ok": True, "snapshot_id": "s11", "page": {"title": "Capitulo 7 - Netflix"}, "changed": False, "elements": [{"id": "e11", "role": "button", "name": "Pausa", "value": ""}]},
            {"ok": True, "snapshot_id": "s12", "page": {"title": "Capitulo 7 - Netflix"}, "changed": True, "elements": [{"id": "e12", "role": "button", "name": "Reproducir", "value": ""}]},
        ])
        acted = []
        registry = ToolRegistry()
        registry.register("browser_open", "open", {"type": "object", "properties": {}}, lambda **_kwargs: {"ok": True})
        registry.register("web_inspect", "inspect", {"type": "object", "properties": {}}, lambda **_kwargs: next(inspections))
        registry.register("web_act", "act", {"type": "object", "properties": {}}, lambda **kwargs: acted.append(kwargs) or {"ok": True})
        router = ScriptedRouter([
            BrainResult(True, tool_calls=[{"function": {"name": "browser_open", "arguments": {"target": "Netflix", "profile": "Marian"}}}]),
            BrainResult(True, text=""),
            BrainResult(True, text=""),
            BrainResult(True, text=""),
            BrainResult(True, text=""),
        ])

        with patch("brain.agent_loop.time.sleep"):
            result = MochiAgentLoop(router=router, max_steps=6).run(
                "Abre Netflix en la cuenta de Google de Marian y entra en el perfil de Marian, busca la serie de Acaramelados y pon el capitulo 7 y pausalo.",
                registry,
            )

        self.assertEqual(result["status"], "completed")
        self.assertEqual(acted, [
            {"element_id": "e1", "action": "click", "snapshot_id": "s1"},
            {"element_id": "e3", "action": "type", "value": "Acaramelados", "snapshot_id": "s3"},
            {"element_id": "e4", "action": "submit", "snapshot_id": "s4"},
            {"element_id": "e5", "action": "click", "snapshot_id": "s5"},
            {"element_id": "e7", "action": "click", "snapshot_id": "s7"},
            {"element_id": "e9", "action": "keyboard_activate", "snapshot_id": "s9"},
            {"element_id": "e11", "action": "keyboard_activate", "snapshot_id": "s11"},
        ])
        self.assertIn("lo pausé", result["text"])

    def test_season_selector_opens_then_selects_the_exact_accessible_option(self):
        inspections = iter([
            {"ok": True, "snapshot_id": "s1", "page": {"title": "Acaramelados - Netflix"}, "changed": False,
             "elements": [{"id": "e1", "role": "combobox", "name": "Temporada 1", "value": ""}]},
            {"ok": True, "snapshot_id": "s2", "page": {"title": "Acaramelados - Netflix"}, "changed": True,
             "elements": [{"id": "e2", "role": "menuitem", "name": "Temporada 3", "value": ""}]},
            {"ok": True, "snapshot_id": "s3", "page": {"title": "Acaramelados - Netflix"}, "changed": True,
             "elements": [{"id": "e3", "role": "combobox", "name": "Temporada 3", "value": ""}]},
        ])
        acted = []
        registry = ToolRegistry()
        registry.register("web_inspect", "inspect", {"type": "object", "properties": {}}, lambda **_kwargs: next(inspections))
        registry.register("web_act", "act", {"type": "object", "properties": {}}, lambda **kwargs: acted.append(kwargs) or {"ok": True})
        state = plan_goal(
            "Abre Netflix y busca la serie de Acaramelados, elige temporada 3 y pon el capitulo 2."
        )

        with patch("brain.agent_loop.time.sleep"):
            records = MochiAgentLoop()._fast_named_page_action(registry, state, "select_season")

        self.assertEqual(
            acted,
            [
                {"element_id": "e1", "action": "click", "snapshot_id": "s1"},
                {"element_id": "e2", "action": "click", "snapshot_id": "s2"},
            ],
        )
        self.assertEqual([record[3] for record in records if record[0] == "web_act"], ["open_season_selector", "select_season"])

    def test_episode_alias_is_targeted_and_scrolled_before_activation(self):
        inspections = iter([
            {"ok": True, "snapshot_id": "s1", "page": {"title": "Acaramelados - Netflix"}, "changed": False, "elements": [
                {"id": "e1", "role": "button", "name": "Episodio 1", "value": ""},
            ]},
            {"ok": True, "snapshot_id": "s2", "page": {"title": "Acaramelados - Netflix"}, "changed": False, "elements": [
                {"id": "e2", "role": "button", "name": "Episodio 7", "value": "", "offscreen": True},
            ]},
            {"ok": True, "snapshot_id": "s3", "page": {"title": "Acaramelados - Netflix"}, "changed": True, "elements": [
                {"id": "e3", "role": "button", "name": "Episodio 7", "value": "", "offscreen": False},
            ]},
            {"ok": True, "snapshot_id": "s4", "page": {"title": "Acaramelados - Netflix"}, "changed": True, "elements": []},
        ])
        acted = []
        registry = ToolRegistry()
        registry.register("web_inspect", "inspect", {"type": "object", "properties": {}}, lambda **_kwargs: next(inspections))
        registry.register("web_act", "act", {"type": "object", "properties": {}}, lambda **kwargs: acted.append(kwargs) or {"ok": True})
        state = plan_goal(
            "abre Netflix en la cuenta de Google de Marian y entra en el perfil de Marian, "
            "busca Acaramelados y pon el capitulo 7"
        )
        state.mark_completed("browser_open")
        state.mark_completed("select_site_profile")
        state.mark_completed("search_page")
        state.mark_completed("select_result")

        with patch("brain.agent_loop.time.sleep"):
            records = MochiAgentLoop()._fast_named_page_action(registry, state, "select_episode")

        self.assertEqual(acted, [
            {"element_id": "e2", "action": "scroll_into_view", "snapshot_id": "s2"},
            {"element_id": "e3", "action": "click", "snapshot_id": "s3"},
        ])
        self.assertEqual(
            [record[3] for record in records if record[0] == "web_act"],
            ["scroll_episode_into_view", "select_episode"],
        )

    def test_current_page_episode_continuation_completes_play_then_pause(self):
        inspections = iter([
            {"ok": True, "snapshot_id": "s1", "page": {"title": "Any series"}, "changed": False, "elements": [
                {"id": "e1", "role": "button", "name": "Capitulo 7", "value": "", "offscreen": False},
            ]},
            {"ok": True, "snapshot_id": "s2", "page": {"title": "Any player"}, "changed": True, "elements": [
                {"id": "e2", "role": "button", "name": "Reproducir", "value": "", "offscreen": False},
            ]},
            {"ok": True, "snapshot_id": "s3", "page": {"title": "Any player"}, "changed": False, "elements": [
                {"id": "e3", "role": "button", "name": "Reproducir", "value": "", "offscreen": False},
            ]},
            {"ok": True, "snapshot_id": "s4", "page": {"title": "Any player"}, "changed": True, "elements": [
                {"id": "e4", "role": "button", "name": "Pausa", "value": "", "offscreen": False},
            ]},
            {"ok": True, "snapshot_id": "s5", "page": {"title": "Any player"}, "changed": False, "elements": [
                {"id": "e5", "role": "button", "name": "Pausa", "value": "", "offscreen": False},
            ]},
            {"ok": True, "snapshot_id": "s6", "page": {"title": "Any player"}, "changed": True, "elements": [
                {"id": "e6", "role": "button", "name": "Reproducir", "value": "", "offscreen": False},
            ]},
        ])
        acted = []
        registry = ToolRegistry()
        registry.register("web_inspect", "inspect", {"type": "object", "properties": {}}, lambda **_kwargs: next(inspections))
        registry.register("web_act", "act", {"type": "object", "properties": {}}, lambda **kwargs: acted.append(kwargs) or {"ok": True})

        with patch("brain.agent_loop.time.sleep"):
            result = MochiAgentLoop(router=ScriptedRouter([])).run(
                "pon el capitulo 7 y pausalo",
                registry,
            )

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["final_mode"], "deterministic_player_control")
        self.assertEqual(acted, [
            {"element_id": "e1", "action": "click", "snapshot_id": "s1"},
            {"element_id": "e3", "action": "keyboard_activate", "snapshot_id": "s3"},
            {"element_id": "e5", "action": "keyboard_activate", "snapshot_id": "s5"},
        ])

    def test_player_seek_maps_accessible_duration_to_range_without_site_rules(self):
        inspections = iter([
            {"ok": True, "snapshot_id": "s1", "page": {"title": "Any player"}, "changed": False, "elements": [
                {"id": "e1", "role": "slider", "name": "Progreso 0:00 / 10:00", "value": "", "range": {"value": 0, "minimum": 0, "maximum": 100}},
            ]},
            {"ok": True, "snapshot_id": "s2", "page": {"title": "Any player"}, "changed": True, "elements": [
                {"id": "e2", "role": "slider", "name": "Progreso 5:00 / 10:00", "value": "", "range": {"value": 50, "minimum": 0, "maximum": 100}},
            ]},
        ])
        acted = []
        registry = ToolRegistry()
        registry.register("web_inspect", "inspect", {"type": "object", "properties": {}}, lambda **_kwargs: next(inspections))
        registry.register("web_act", "act", {"type": "object", "properties": {}}, lambda **kwargs: acted.append(kwargs) or {"ok": True})
        state = plan_goal("pon el minuto 5")

        with patch("brain.agent_loop.time.sleep"):
            records = MochiAgentLoop()._fast_player_navigation(registry, state, "seek_media")

        self.assertEqual(acted, [{"element_id": "e1", "action": "set_range", "value": "50.000000", "snapshot_id": "s1"}])
        self.assertEqual([record[3] for record in records if record[0] == "web_act"], [None])
        self.assertEqual(state.step("seek_media").status, "completed")
        self.assertEqual(state.step("seek_media").reason, "timeline_target_observed")

    def test_player_seek_does_not_complete_for_an_unrelated_snapshot_change(self):
        inspections = iter([
            {"ok": True, "snapshot_id": "s1", "page": {"title": "Any player"}, "changed": False, "elements": [
                {"id": "e1", "role": "slider", "name": "Progreso", "value": "", "range": {"value": 10, "minimum": 0, "maximum": 600}},
            ]},
            {"ok": True, "snapshot_id": "s2", "page": {"title": "Any player"}, "changed": True, "elements": [
                {"id": "e2", "role": "slider", "name": "Progreso", "value": "", "range": {"value": 10, "minimum": 0, "maximum": 600}},
            ]},
        ])
        registry = ToolRegistry()
        registry.register("web_inspect", "inspect", {"type": "object", "properties": {}}, lambda **_kwargs: next(inspections))
        registry.register("web_act", "act", {"type": "object", "properties": {}}, lambda **_kwargs: {"ok": True})
        state = plan_goal("avanza 5 segundos")

        with patch("brain.agent_loop.time.sleep"):
            MochiAgentLoop()._fast_player_navigation(registry, state, "adjust_media_position")

        self.assertEqual(state.step("adjust_media_position").status, "pending")

    def test_player_seek_accepts_bounded_progress_while_media_keeps_playing(self):
        inspections = iter([
            {"ok": True, "snapshot_id": "s1", "page": {"title": "Any player"}, "changed": False, "elements": [
                {"id": "e1", "role": "slider", "name": "Progreso", "value": "", "range": {"value": 35, "minimum": 0, "maximum": 600}},
            ]},
            {"ok": True, "snapshot_id": "s2", "page": {"title": "Any player"}, "changed": True, "elements": [
                {"id": "e2", "role": "slider", "name": "Progreso", "value": "", "range": {"value": 43, "minimum": 0, "maximum": 600}},
            ]},
        ])
        registry = ToolRegistry()
        registry.register("web_inspect", "inspect", {"type": "object", "properties": {}}, lambda **_kwargs: next(inspections))
        registry.register("web_act", "act", {"type": "object", "properties": {}}, lambda **_kwargs: {
            "ok": True,
            "range_strategy": "focused_range_keyboard",
            "observed_range_value": 43,
        })
        state = plan_goal("avanza 5 segundos")

        with patch("brain.agent_loop.time.sleep"):
            MochiAgentLoop()._fast_player_navigation(registry, state, "adjust_media_position")

        self.assertEqual(state.step("adjust_media_position").status, "completed")
        self.assertEqual(state.step("adjust_media_position").reason, "timeline_target_observed")

    def test_windows_media_session_seeks_when_page_controls_are_hidden(self):
        observation = {
            "ok": True,
            "snapshot_id": "s1",
            "page": {"title": "Requested video - YouTube"},
            "changed": False,
            "elements": [],
        }
        calls = []
        registry = ToolRegistry()
        registry.register("web_inspect", "inspect", {"type": "object", "properties": {}}, lambda **_kwargs: observation)
        registry.register("web_act", "act", {"type": "object", "properties": {}}, lambda **_kwargs: self.fail("Hidden controls must use the observed media session"))
        registry.register("control_media", "media", {"type": "object", "properties": {}}, lambda **kwargs: calls.append(kwargs) or {
            "ok": True,
            "state_confirmed": True,
            "previous_position_seconds": 30,
            "target_position_seconds": 35,
            "position_seconds": 35.4,
        })
        state = plan_goal("adelanta 5 segundos")

        records = MochiAgentLoop()._fast_player_navigation(
            registry, state, "adjust_media_position"
        )

        self.assertEqual(calls, [{
            "action": "adjust_position",
            "amount": 5.0,
            "expected_title": "Requested video - YouTube",
        }])
        self.assertEqual(state.step("adjust_media_position").status, "completed")
        self.assertEqual(state.step("adjust_media_position").reason, "windows_media_session_position")
        self.assertEqual([record[0] for record in records], ["web_inspect", "control_media"])

    def test_player_navigation_uses_one_exact_next_button_not_global_media_key(self):
        inspections = iter([
            {"ok": True, "snapshot_id": "s1", "page": {"title": "Any player"}, "changed": False, "elements": [
                {"id": "e1", "role": "button", "name": "Siguiente episodio", "value": ""},
            ]},
            {"ok": True, "snapshot_id": "s2", "page": {"title": "Any player"}, "changed": True, "elements": []},
        ])
        acted = []
        registry = ToolRegistry()
        registry.register("web_inspect", "inspect", {"type": "object", "properties": {}}, lambda **_kwargs: next(inspections))
        registry.register("web_act", "act", {"type": "object", "properties": {}}, lambda **kwargs: acted.append(kwargs) or {"ok": True})
        state = plan_goal("pasa al siguiente episodio")

        with patch("brain.agent_loop.time.sleep"):
            MochiAgentLoop()._fast_player_navigation(registry, state, "next_media")

        self.assertEqual(acted, [{"element_id": "e1", "action": "click", "snapshot_id": "s1"}])

    def test_windows_media_session_verifies_hidden_next_item_by_title_change(self):
        observation = {
            "ok": True,
            "snapshot_id": "s1",
            "page": {"title": "Episode one - Player"},
            "changed": False,
            "elements": [],
        }
        calls = []
        registry = ToolRegistry()
        registry.register("web_inspect", "inspect", {"type": "object", "properties": {}}, lambda **_kwargs: observation)
        registry.register("web_act", "act", {"type": "object", "properties": {}}, lambda **_kwargs: self.fail("No visible control should be required"))
        registry.register("control_media", "media", {"type": "object", "properties": {}}, lambda **kwargs: calls.append(kwargs) or {
            "ok": True,
            "state_confirmed": True,
            "previous_title": "Episode one",
            "title": "Episode two",
        })
        state = plan_goal("pasa al siguiente episodio")

        MochiAgentLoop()._fast_player_navigation(registry, state, "next_media")

        self.assertEqual(calls, [{
            "action": "next", "expected_title": "Episode one - Player",
        }])
        self.assertEqual(state.step("next_media").status, "completed")
        self.assertEqual(state.step("next_media").reason, "windows_media_session_item_changed")

    def test_pause_requires_a_play_control_or_a_stable_timeline(self):
        inspections = iter([
            {"ok": True, "snapshot_id": "s1", "page": {"title": "Any player"}, "changed": False, "elements": [
                {"id": "play", "role": "button", "name": "Reproducir", "value": ""},
                {"id": "range1", "role": "slider", "name": "Progreso", "value": "", "range": {"value": 10, "minimum": 0, "maximum": 600}},
            ]},
            {"ok": True, "snapshot_id": "s2", "page": {"title": "Any player"}, "changed": False, "elements": [
                {"id": "play2", "role": "button", "name": "Reproducir", "value": ""},
                {"id": "range2", "role": "slider", "name": "Progreso", "value": "", "range": {"value": 10, "minimum": 0, "maximum": 600}},
            ]},
            {"ok": True, "snapshot_id": "s3", "page": {"title": "Any player"}, "changed": False, "elements": [
                {"id": "play3", "role": "button", "name": "Reproducir", "value": ""},
                {"id": "range3", "role": "slider", "name": "Progreso", "value": "", "range": {"value": 10, "minimum": 0, "maximum": 600}},
            ]},
        ])
        acted = []
        registry = ToolRegistry()
        registry.register("web_inspect", "inspect", {"type": "object", "properties": {}}, lambda **_kwargs: next(inspections))
        registry.register("web_act", "act", {"type": "object", "properties": {}}, lambda **kwargs: acted.append(kwargs) or {"ok": True})
        state = plan_goal("pausa el video")

        with patch("brain.agent_loop.time.sleep"):
            records = MochiAgentLoop()._fast_named_page_action(registry, state, "pause_media")

        self.assertEqual(state.step("pause_media").status, "completed")
        self.assertEqual(state.step("pause_media").reason, "timeline_stable_before_pause")
        self.assertEqual(acted, [
            {"element_id": "range1", "action": "focus", "snapshot_id": "s1"},
        ])

    def test_neutral_playback_button_uses_timeline_state_before_pausing(self):
        inspections = iter([
            {"ok": True, "snapshot_id": "s1", "page": {"title": "Any player"}, "changed": False, "elements": [
                {"id": "button1", "role": "button", "name": "Botón de reproducción (k)", "value": ""},
                {"id": "range1", "role": "slider", "name": "Progreso", "value": "", "range": {"value": 10, "minimum": 0, "maximum": 600}},
            ]},
            {"ok": True, "snapshot_id": "s2", "page": {"title": "Any player"}, "changed": False, "elements": [
                {"id": "button2", "role": "button", "name": "Botón de reproducción (k)", "value": ""},
                {"id": "range2", "role": "slider", "name": "Progreso", "value": "", "range": {"value": 10, "minimum": 0, "maximum": 600}},
            ]},
            {"ok": True, "snapshot_id": "s3", "page": {"title": "Any player"}, "changed": True, "elements": [
                {"id": "button3", "role": "button", "name": "Botón de reproducción (k)", "value": ""},
                {"id": "range3", "role": "slider", "name": "Progreso", "value": "", "range": {"value": 11, "minimum": 0, "maximum": 600}},
            ]},
            {"ok": True, "snapshot_id": "s4", "page": {"title": "Any player"}, "changed": True, "elements": [
                {"id": "button4", "role": "button", "name": "Botón de reproducción (k)", "value": ""},
                {"id": "range4", "role": "slider", "name": "Progreso", "value": "", "range": {"value": 11, "minimum": 0, "maximum": 600}},
            ]},
            {"ok": True, "snapshot_id": "s5", "page": {"title": "Any player"}, "changed": False, "elements": [
                {"id": "button5", "role": "button", "name": "Botón de reproducción (k)", "value": ""},
                {"id": "range5", "role": "slider", "name": "Progreso", "value": "", "range": {"value": 11, "minimum": 0, "maximum": 600}},
            ]},
            {"ok": True, "snapshot_id": "s6", "page": {"title": "Any player"}, "changed": False, "elements": [
                {"id": "button6", "role": "button", "name": "Botón de reproducción (k)", "value": ""},
                {"id": "range6", "role": "slider", "name": "Progreso", "value": "", "range": {"value": 11, "minimum": 0, "maximum": 600}},
            ]},
        ])
        acted = []
        registry = ToolRegistry()
        registry.register("web_inspect", "inspect", {"type": "object", "properties": {}}, lambda **_kwargs: next(inspections))
        registry.register("web_act", "act", {"type": "object", "properties": {}}, lambda **kwargs: acted.append(kwargs) or {"ok": True})
        state = plan_goal("pausa el video")

        with patch("brain.agent_loop.time.sleep"):
            MochiAgentLoop()._fast_named_page_action(registry, state, "pause_media")

        self.assertEqual(acted, [
            {"element_id": "range1", "action": "focus", "snapshot_id": "s1"},
            {"element_id": "button3", "action": "keyboard_activate", "snapshot_id": "s3"},
            {"element_id": "range4", "action": "focus", "snapshot_id": "s4"},
        ])
        self.assertEqual(state.step("pause_media").status, "completed")
        self.assertEqual(state.step("pause_media").reason, "timeline_stable_after_pause")

    def test_pause_does_not_complete_while_the_timeline_keeps_advancing(self):
        inspections = iter([
            {"ok": True, "snapshot_id": "s1", "page": {"title": "Any player"}, "changed": False, "elements": [
                {"id": "pause", "role": "button", "name": "Pausa", "value": ""},
            ]},
            {"ok": True, "snapshot_id": "s2", "page": {"title": "Any player"}, "changed": True, "elements": [
                {"id": "pause2", "role": "button", "name": "Pausa", "value": ""},
            ]},
            {"ok": True, "snapshot_id": "s3", "page": {"title": "Any player"}, "changed": True, "elements": [
                {"id": "pause3", "role": "button", "name": "Pausa", "value": ""},
            ]},
        ])
        registry = ToolRegistry()
        registry.register("web_inspect", "inspect", {"type": "object", "properties": {}}, lambda **_kwargs: next(inspections))
        registry.register("web_act", "act", {"type": "object", "properties": {}}, lambda **_kwargs: {"ok": True})
        state = plan_goal("pausa el video")

        with patch("brain.agent_loop.time.sleep"):
            MochiAgentLoop()._fast_named_page_action(registry, state, "pause_media")

        self.assertEqual(state.step("pause_media").status, "pending")

    def test_windows_media_session_controls_hidden_player_and_confirms_state(self):
        observation = {
            "ok": True,
            "snapshot_id": "s1",
            "page": {"title": "Requested video - YouTube"},
            "changed": False,
            "elements": [],
        }
        media_calls = []

        def control_media(**kwargs):
            media_calls.append(kwargs)
            if kwargs["action"] == "get_state":
                return {
                    "ok": True,
                    "state_confirmed": True,
                    "playback_status": "playing",
                }
            return {
                "ok": True,
                "state_confirmed": True,
                "playback_status": "paused",
            }

        registry = ToolRegistry()
        registry.register("web_inspect", "inspect", {"type": "object", "properties": {}}, lambda **_kwargs: observation)
        registry.register("web_act", "act", {"type": "object", "properties": {}}, lambda **_kwargs: self.fail("UIA must not toggle a hidden player"))
        registry.register("control_media", "media", {"type": "object", "properties": {}}, control_media)
        state = plan_goal("pausa el video")

        records = MochiAgentLoop()._fast_named_page_action(registry, state, "pause_media")

        self.assertEqual(media_calls, [
            {"action": "get_state", "expected_title": "Requested video - YouTube"},
            {"action": "pause", "expected_title": "Requested video - YouTube"},
        ])
        self.assertEqual(state.step("pause_media").status, "completed")
        self.assertEqual(state.step("pause_media").reason, "windows_media_session_paused")
        self.assertEqual([record[0] for record in records], ["web_inspect", "control_media", "control_media"])

    def test_windows_media_session_rejects_unconfirmed_play_state(self):
        observation = {
            "ok": True,
            "snapshot_id": "s1",
            "page": {"title": "Requested video - YouTube"},
            "changed": False,
            "elements": [],
        }
        responses = iter([
            {"ok": True, "state_confirmed": True, "playback_status": "paused"},
            {"ok": True, "state_confirmed": False, "playback_status": "paused"},
        ])
        registry = ToolRegistry()
        registry.register("web_inspect", "inspect", {"type": "object", "properties": {}}, lambda **_kwargs: observation)
        registry.register("web_act", "act", {"type": "object", "properties": {}}, lambda **_kwargs: self.fail("No blind fallback after a matched session"))
        registry.register("control_media", "media", {"type": "object", "properties": {}}, lambda **_kwargs: next(responses))
        state = plan_goal("reanuda el video")

        MochiAgentLoop()._fast_play_media(registry, state)

        self.assertEqual(state.step("play_media").status, "pending")

    def test_resume_clicks_a_stale_pause_label_after_timeline_proves_it_is_stopped(self):
        inspections = iter([
            {"ok": True, "snapshot_id": "s1", "page": {"title": "Any player"}, "changed": False, "elements": [
                {"id": "pause1", "role": "button", "name": "Pausa", "value": ""},
                {"id": "range1", "role": "slider", "name": "Progreso", "value": "", "range": {"value": 10, "minimum": 0, "maximum": 600}},
            ]},
            {"ok": True, "snapshot_id": "s2", "page": {"title": "Any player"}, "changed": False, "elements": [
                {"id": "pause2", "role": "button", "name": "Pausa", "value": ""},
                {"id": "range2", "role": "slider", "name": "Progreso", "value": "", "range": {"value": 10, "minimum": 0, "maximum": 600}},
            ]},
            {"ok": True, "snapshot_id": "s3", "page": {"title": "Any player"}, "changed": False, "elements": [
                {"id": "pause3", "role": "button", "name": "Pausa", "value": ""},
                {"id": "range3", "role": "slider", "name": "Progreso", "value": "", "range": {"value": 10, "minimum": 0, "maximum": 600}},
            ]},
            {"ok": True, "snapshot_id": "s4", "page": {"title": "Any player"}, "changed": True, "elements": [
                {"id": "pause4", "role": "button", "name": "Pausa", "value": ""},
                {"id": "range4", "role": "slider", "name": "Progreso", "value": "", "range": {"value": 10, "minimum": 0, "maximum": 600}},
            ]},
            {"ok": True, "snapshot_id": "s5", "page": {"title": "Any player"}, "changed": False, "elements": [
                {"id": "pause5", "role": "button", "name": "Pausa", "value": ""},
                {"id": "range5", "role": "slider", "name": "Progreso", "value": "", "range": {"value": 10, "minimum": 0, "maximum": 600}},
            ]},
            {"ok": True, "snapshot_id": "s6", "page": {"title": "Any player"}, "changed": True, "elements": [
                {"id": "pause6", "role": "button", "name": "Pausa", "value": ""},
                {"id": "range6", "role": "slider", "name": "Progreso", "value": "", "range": {"value": 11, "minimum": 0, "maximum": 600}},
            ]},
        ])
        acted = []
        registry = ToolRegistry()
        registry.register("web_inspect", "inspect", {"type": "object", "properties": {}}, lambda **_kwargs: next(inspections))
        registry.register("web_act", "act", {"type": "object", "properties": {}}, lambda **kwargs: acted.append(kwargs) or {"ok": True})
        state = plan_goal("reanuda el video")

        with patch("brain.agent_loop.time.sleep"):
            MochiAgentLoop()._fast_play_media(registry, state)

        self.assertEqual(
            [action for action in acted if action["action"] == "keyboard_activate"],
            [{"element_id": "pause3", "action": "keyboard_activate", "snapshot_id": "s3"}],
        )
        self.assertEqual(state.step("play_media").status, "completed")
        self.assertEqual(state.step("play_media").reason, "timeline_progressed")

    def test_direct_player_seek_recovers_from_no_tools_reply_in_one_turn(self):
        inspections = iter([
            {"ok": True, "snapshot_id": "s1", "page": {"title": "YouTube"}, "changed": False, "elements": [
                {"id": "e1", "role": "slider", "name": "Progreso 0:00 / 10:00", "value": "", "range": {"value": 0, "minimum": 0, "maximum": 100}},
            ]},
            {"ok": True, "snapshot_id": "s2", "page": {"title": "YouTube"}, "changed": True, "elements": [
                {"id": "e2", "role": "slider", "name": "Progreso 5:00 / 10:00", "value": "", "range": {"value": 50, "minimum": 0, "maximum": 100}},
            ]},
        ])
        acted = []
        registry = ToolRegistry()
        registry.register("web_inspect", "inspect", {"type": "object", "properties": {}}, lambda **_kwargs: next(inspections))
        registry.register("web_act", "act", {"type": "object", "properties": {}}, lambda **kwargs: acted.append(kwargs) or {"ok": True})
        router = ScriptedRouter([BrainResult(True, text="")])

        with patch("brain.agent_loop.time.sleep"):
            result = MochiAgentLoop(router=router, max_steps=1).run("Pon el minuto 5", registry)

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["steps"], 1)
        self.assertEqual(acted, [{"element_id": "e1", "action": "set_range", "value": "50.000000", "snapshot_id": "s1"}])
        self.assertIn("Ajusté la posición", result["text"])

    def test_third_visible_video_uses_card_order_and_confirms_playback(self):
        inspections = iter([
            {"ok": True, "snapshot_id": "s1", "page": {"title": "YouTube"}, "changed": False, "elements": [
                {"id": "n1", "role": "link", "name": "Inicio", "value": ""},
                {"id": "e1", "role": "link", "name": "Video uno", "value": "", "container": "Tarjeta video uno"},
                {"id": "c1", "role": "link", "name": "Canal uno", "value": "", "container": "Tarjeta video uno"},
                {"id": "e2", "role": "link", "name": "Video dos", "value": "", "container": "Tarjeta video dos"},
                {"id": "c2", "role": "link", "name": "Canal dos", "value": "", "container": "Tarjeta video dos"},
                {"id": "e3", "role": "link", "name": "Video tres", "value": "", "container": "Tarjeta video tres"},
                {"id": "c3", "role": "link", "name": "Canal tres", "value": "", "container": "Tarjeta video tres"},
            ]},
            {"ok": True, "snapshot_id": "s2", "page": {"title": "Video tres - YouTube"}, "changed": True, "elements": [
                {"id": "e4", "role": "button", "name": "Reproducir", "value": ""},
            ]},
            {"ok": True, "snapshot_id": "s3", "page": {"title": "Video tres - YouTube"}, "changed": False, "elements": [
                {"id": "e5", "role": "button", "name": "Reproducir", "value": ""},
            ]},
            {"ok": True, "snapshot_id": "s4", "page": {"title": "Video tres - YouTube"}, "changed": True, "elements": [
                {"id": "e6", "role": "button", "name": "Pausa", "value": ""},
            ]},
        ])
        acted = []
        registry = ToolRegistry()
        registry.register("web_inspect", "inspect", {"type": "object", "properties": {}}, lambda **_kwargs: next(inspections))
        registry.register("web_act", "act", {"type": "object", "properties": {}}, lambda **kwargs: acted.append(kwargs) or {"ok": True})

        with patch("brain.agent_loop.time.sleep"):
            result = MochiAgentLoop(
                router=ScriptedRouter([]), max_steps=1, pending_choice_cache=self._default_choice_cache,
            ).run(
                "reproduce el 3 video en pantalla", registry,
            )

        self.assertEqual(result["status"], "completed")
        self.assertEqual(acted, [
            {"element_id": "e3", "action": "click", "snapshot_id": "s1"},
            {"element_id": "e5", "action": "keyboard_activate", "snapshot_id": "s3"},
        ])
        self.assertNotIn("browser_open", [action["tool"] for action in result["actions"]])

    def test_first_recommended_video_requires_recommendation_section(self):
        inspections = iter([
            {"ok": True, "snapshot_id": "s1", "page": {"title": "YouTube"}, "changed": False, "elements": [
                {"id": "e1", "role": "link", "name": "Resultado de búsqueda", "value": "", "container": "Resultado de búsqueda"},
                {"id": "e2", "role": "link", "name": "Primer recomendado", "value": "", "container": "Tarjeta recomendada", "section": "Videos recomendados"},
            ]},
            {"ok": True, "snapshot_id": "s2", "page": {"title": "Primer recomendado - YouTube"}, "changed": True, "elements": [
                {"id": "e3", "role": "button", "name": "Pausa", "value": ""},
            ]},
        ])
        acted = []
        registry = ToolRegistry()
        registry.register("web_inspect", "inspect", {"type": "object", "properties": {}}, lambda **_kwargs: next(inspections))
        registry.register("web_act", "act", {"type": "object", "properties": {}}, lambda **kwargs: acted.append(kwargs) or {"ok": True})

        with patch("brain.agent_loop.time.sleep"):
            result = MochiAgentLoop(
                router=ScriptedRouter([]), max_steps=1, pending_choice_cache=self._default_choice_cache,
            ).run(
                "reproduce el primer video recomendado", registry,
            )

        self.assertEqual(result["status"], "completed")
        self.assertEqual(acted, [{"element_id": "e2", "action": "click", "snapshot_id": "s1"}])

    def test_unrelated_click_cannot_complete_a_pending_named_page_result(self):
        inspections = iter([
            {"ok": True, "snapshot_id": "s1", "page": {"title": "YouTube"}, "changed": False, "elements": [{"id": "e1", "role": "textbox", "name": "Buscar", "value": ""}]},
            {"ok": True, "snapshot_id": "s2", "page": {"title": "YouTube"}, "changed": True, "elements": [{"id": "e2", "role": "textbox", "name": "Buscar", "value": "Fortnite lo hizo de nuevo el Rubius"}]},
            {"ok": True, "snapshot_id": "s3", "page": {"title": "el Rubius - YouTube"}, "changed": True, "elements": [
                {"id": "e3", "role": "textbox", "name": "Buscar", "value": ""},
                {"id": "e4", "role": "link", "name": "elrubiusOMG", "value": ""},
            ]},
            {"ok": True, "snapshot_id": "s4", "page": {"title": "el Rubius - YouTube"}, "changed": True, "elements": [{"id": "e5", "role": "textbox", "name": "Buscar", "value": "el Rubius Fortnite lo hizo de nuevo"}]},
            *[
                {"ok": True, "snapshot_id": f"s{index}", "page": {"title": "el Rubius Fortnite - YouTube"}, "changed": True, "elements": [
                    {"id": "e6", "role": "button", "name": "Search", "value": ""},
                    {"id": "e7", "role": "textbox", "name": "Buscar", "value": ""},
                ]}
                for index in range(5, 16)
            ],
        ])
        acted = []
        registry = ToolRegistry()
        registry.register("browser_open", "open", {"type": "object", "properties": {}}, lambda **_kwargs: {"ok": True})
        registry.register("web_inspect", "inspect", {"type": "object", "properties": {}}, lambda **_kwargs: next(inspections))
        registry.register("web_act", "act", {"type": "object", "properties": {}}, lambda **kwargs: acted.append(kwargs) or {"ok": True})
        router = ScriptedRouter([
            BrainResult(True, tool_calls=[{"function": {"name": "browser_open", "arguments": {"target": "YouTube"}}}]),
            BrainResult(True, tool_calls=[{"function": {"name": "web_act", "arguments": {"element_id": "e6", "action": "click"}}}]),
            BrainResult(True, text="done"),
        ])

        with patch("brain.agent_loop.time.sleep"):
            result = MochiAgentLoop(router=router, max_steps=3).run(
                "Busca el Rubius en YouTube y abre Fortnite lo hizo de nuevo.",
                registry,
            )

        self.assertEqual(result["status"], "partial")
        self.assertIn("no pude identificar de forma segura", result["text"])
        self.assertNotIn({"element_id": "e6", "action": "click"}, acted)
        rejected = next(action for action in result["actions"] if action["arguments"].get("element_id") == "e6")
        self.assertEqual(rejected["result"]["error"], "ambiguous_goal_element")


class RegistryTests(unittest.TestCase):
    def test_open_target_prefers_a_clear_game_match_without_scanning_apps(self):
        game = {
            "name": "R.E.P.O.",
            "normalized_name": "repo",
            "launcher": "steam",
        }
        resolver = Mock()
        resolver.resolve.return_value = {"ok": True, "kind": "search", "query": "repo"}
        with patch("brain.mochi_tools._destination_resolver", resolver), \
             patch("brain.mochi_tools.game_registry") as games, \
             patch("brain.mochi_tools.application_registry") as applications:
            games.find.return_value = game
            games.launch.return_value = (True, "Orden de inicio enviada")

            result = open_target("reppo")

        self.assertTrue(result["ok"])
        self.assertEqual((result["resolution"], result["game"]), ("game", "R.E.P.O."))
        games.launch.assert_called_once_with("reppo")
        applications.find.assert_not_called()

    def test_generic_browser_tools_are_registered_and_window_tools_remain(self):
        names = create_mochi_tool_registry().get_tool_names()
        self.assertTrue({"browser_open", "browser_search", "browser_profiles", "web_inspect", "web_act"}.issubset(names))
        self.assertTrue({"close_window", "close_application_windows", "control_window"}.issubset(names))
        self.assertIn("control_media", names)
        self.assertTrue({
            "list_user_folders", "find_files", "find_folders",
            "open_known_folder", "open_file", "open_folder",
            "open_latest_download", "list_installed_applications",
            "list_games", "launch_game", "open_target", "get_system_info",
            "read_clipboard", "write_clipboard", "capture_screenshot",
            "show_notification",
        }.issubset(names))
        self.assertFalse(any(name.startswith("open_youtube") for name in names))

    def test_registry_exposes_the_complete_capability_contract(self):
        registry = create_mochi_tool_registry()
        schemas = registry.get_tool_schemas()
        names = {schema["function"]["name"] for schema in schemas}
        self.assertEqual(names, {
            "browser_open", "browser_profiles", "browser_search", "capture_screenshot",
            "close_application_windows", "close_window", "control_media", "control_window",
            "find_files", "find_folders", "get_system_info", "get_system_volume",
            "launch_game", "list_games", "list_installed_applications", "list_open_windows",
            "list_user_folders", "open_application", "open_file", "open_folder",
            "open_known_folder", "open_latest_download", "open_target", "play_web_media",
            "read_clipboard", "set_system_volume", "show_notification", "web_act", "web_inspect",
            "write_clipboard",
        })
        self.assertTrue(all(
            schema["function"]["parameters"].get("type") == "object"
            for schema in schemas
        ))

    def test_web_action_schema_matches_the_interactor_contract(self):
        """Never hide an action used by the deterministic page flow from the model."""
        schemas = {
            schema["function"]["name"]: schema["function"]["parameters"]
            for schema in create_mochi_tool_registry().get_tool_schemas()
        }
        self.assertEqual(
            set(schemas["web_act"]["properties"]["action"]["enum"]),
            {
                "click", "invoke", "keyboard_activate", "focus", "type", "submit", "select",
                "toggle", "scroll_into_view", "set_range",
            },
        )

    def test_every_registered_tool_has_a_safe_json_smoke_path(self):
        """Exercise every public tool through ToolRegistry without touching Windows.

        Individual wrapper tests are useful, but this is the contract Mochi's
        agent actually uses: every advertised schema must reach a handler and
        return serializable structured output.  All OS/browser dependencies
        are replaced so the audit cannot launch software, alter a real volume,
        read user data, or close a window.
        """
        file_item = {
            "name": "Informe.pdf",
            "path": Path("C:/Users/Admin/Documents/Informe.pdf"),
            "score": 1.0,
            "is_directory": False,
        }
        folder_item = {
            "name": "Proyecto",
            "path": Path("C:/Users/Admin/Documents/Proyecto"),
            "score": 1.0,
            "is_directory": True,
        }
        app = {"name": "Editor", "type": "shortcut", "source": "start_menu"}
        game = {"name": "Juego", "launcher": "steam"}
        window = {"title": "Editor - Documento", "process": "editor.exe", "pid": 42}
        snapshot = {
            "ok": True,
            "snapshot_id": "s-smoke",
            "page": {"title": "YouTube", "url": None},
            "elements": [{"id": "e1", "role": "link", "name": "Resultado", "value": ""}],
            "context": [],
            "changed": True,
            "element_count": 1,
        }

        def search_items(*, want_directory, **_kwargs):
            return [folder_item if want_directory else file_item]

        session_cache = Mock()
        session_cache.find.return_value = None

        with ExitStack() as stack:
            stack.enter_context(patch("brain.mochi_tools.media_controller.set_volume", return_value=(True, "Volumen actualizado")))
            stack.enter_context(patch("brain.mochi_tools.media_controller.get_volume", return_value=(True, "Volumen al 37%")))
            stack.enter_context(patch("brain.mochi_tools.media_controller.play_pause", return_value=(True, "Señal enviada")))
            stack.enter_context(patch("brain.mochi_tools.media_controller.get_media_session_state", return_value={"ok": True, "state_confirmed": True, "playback_status": "paused"}))
            stack.enter_context(patch("brain.mochi_tools.media_controller.set_media_session_state", side_effect=lambda action, _title=None: {"ok": True, "state_confirmed": True, "playback_status": "playing" if action == "play" else "paused"}))
            stack.enter_context(patch("brain.mochi_tools.media_controller.seek_media_session", return_value={"ok": True, "state_confirmed": True, "position_seconds": 25}))
            stack.enter_context(patch("brain.mochi_tools.media_controller.next_track", return_value=(True, "Siguiente")))
            stack.enter_context(patch("brain.mochi_tools.media_controller.previous_track", return_value=(True, "Anterior")))
            stack.enter_context(patch("brain.mochi_tools.media_controller.stop_media", return_value=(True, "Detenida")))
            stack.enter_context(patch("brain.mochi_tools.media_controller.mute", return_value=(True, "Silenciada")))
            stack.enter_context(patch("brain.mochi_tools.media_controller.unmute", return_value=(True, "Con sonido")))
            stack.enter_context(patch("brain.mochi_tools.media_controller.change_volume", return_value=(True, "Volumen actualizado")))
            stack.enter_context(patch("brain.mochi_tools.desktop_tools.read_clipboard", return_value=(True, "prueba")))
            stack.enter_context(patch("brain.mochi_tools.desktop_tools.write_clipboard", return_value=(True, "Copiado")))
            stack.enter_context(patch("brain.mochi_tools.desktop_tools.capture_screenshot", return_value=(True, Path("C:/capture.png"))))
            stack.enter_context(patch("brain.mochi_tools.desktop_tools.show_notification", return_value=(True, "Enviada")))
            stack.enter_context(patch("brain.mochi_tools.file_manager.search", side_effect=search_items))
            stack.enter_context(patch("brain.mochi_tools.file_manager.open_path", return_value=(True, "Abierto")))
            stack.enter_context(patch("brain.mochi_tools.file_manager.open_latest_download", return_value=(True, "Descarga abierta")))
            stack.enter_context(patch.object(mochi_tools.file_manager, "folders", {"documents": Path("C:/Users/Admin/Documents")}))
            stack.enter_context(patch.object(mochi_tools.file_manager, "folder_aliases", {"documentos": "documents"}))
            stack.enter_context(patch("brain.mochi_tools.application_registry.list_names", return_value=["Editor"]))
            stack.enter_context(patch("brain.mochi_tools.application_registry.find", return_value=app))
            stack.enter_context(patch("brain.mochi_tools.application_registry.launch", return_value=(True, "Editor abierto")))
            stack.enter_context(patch("brain.mochi_tools.game_registry.list_names", return_value=["Juego"]))
            stack.enter_context(patch("brain.mochi_tools.game_registry.find", return_value=game))
            stack.enter_context(patch("brain.mochi_tools.game_registry.launch", return_value=(True, "Juego iniciado")))
            stack.enter_context(patch("brain.mochi_tools.hardware_profiler.analyze", return_value={"os": {"system": "Windows"}}))
            stack.enter_context(patch("brain.mochi_tools.window_manager.get_windows", return_value=[window]))
            stack.enter_context(patch("brain.mochi_tools.window_manager.find", return_value=window))
            stack.enter_context(patch("brain.mochi_tools.window_manager.find_all", return_value=[window]))
            stack.enter_context(patch("brain.mochi_tools.window_manager.focus", return_value=(True, "Enfocada")))
            stack.enter_context(patch("brain.mochi_tools.window_manager.minimize", return_value=(True, "Minimizada")))
            stack.enter_context(patch("brain.mochi_tools.window_manager.maximize", return_value=(True, "Maximizada")))
            stack.enter_context(patch("brain.mochi_tools.window_manager.close", return_value=(True, "Cerrada")))
            stack.enter_context(patch("brain.mochi_tools.window_manager.close_all", return_value=(True, "Cerradas")))
            stack.enter_context(patch("brain.mochi_tools._destination_resolver.resolve", return_value={"ok": True, "kind": "service", "service": "youtube", "target": "YouTube", "url": "https://www.youtube.com"}))
            stack.enter_context(patch("brain.browser_tools._resolver.resolve", return_value={"ok": True, "kind": "service", "service": "youtube", "target": "YouTube", "url": "https://www.youtube.com"}))
            stack.enter_context(patch("brain.browser_tools._resolver.search_url", return_value="https://search.test/?q=mochi"))
            stack.enter_context(patch("brain.browser_tools._browser.get_default_profile", return_value="Default"))
            stack.enter_context(patch("brain.browser_tools._browser.get_profiles", return_value={"Default": {"name": "Personal", "directory": "Default"}}))
            stack.enter_context(patch("brain.browser_tools._browser.get_active_profile_info", return_value={"id": "Default"}))
            stack.enter_context(patch("brain.browser_tools._browser.open_url_for_profile", return_value={"ok": True, "message": "Destino web abierto.", "url": "https://www.youtube.com", "profile_id": "Default"}))
            stack.enter_context(patch("brain.browser_tools._web_session_cache", session_cache))
            stack.enter_context(patch("brain.browser_tools._web_session"))
            stack.enter_context(patch("brain.browser_tools._web_inspector.inspect", return_value=snapshot))
            stack.enter_context(patch("brain.browser_tools._web_interactor.act", return_value={"ok": True, "message": "Acción web realizada"}))

            registry = create_mochi_tool_registry()
            calls = [
                ("set_system_volume", "set_system_volume", {"percent": 40}),
                ("get_system_volume", "get_system_volume", {}),
                *[(f"control_media:{action}", "control_media", {"action": action}) for action in ("get_state", "play", "pause", "play_pause", "next", "previous", "stop", "mute", "unmute")],
                ("control_media:seek", "control_media", {"action": "seek", "amount": 25}),
                ("control_media:adjust_position", "control_media", {"action": "adjust_position", "amount": -5}),
                ("control_media:volume_up", "control_media", {"action": "volume_up", "amount": 5}),
                ("control_media:volume_down", "control_media", {"action": "volume_down", "amount": 5}),
                ("read_clipboard", "read_clipboard", {}),
                ("write_clipboard", "write_clipboard", {"text": "prueba"}),
                ("capture_screenshot", "capture_screenshot", {"name": "smoke"}),
                ("show_notification", "show_notification", {"title": "Mochi", "message": "prueba"}),
                ("list_user_folders", "list_user_folders", {}),
                ("find_files", "find_files", {"query": "informe"}),
                ("find_folders", "find_folders", {"query": "proyecto"}),
                ("open_known_folder", "open_known_folder", {"folder": "documentos"}),
                ("open_file", "open_file", {"query": "informe"}),
                ("open_folder", "open_folder", {"query": "proyecto"}),
                ("open_latest_download", "open_latest_download", {}),
                ("list_installed_applications", "list_installed_applications", {}),
                ("list_games", "list_games", {}),
                ("launch_game", "launch_game", {"game": "juego"}),
                ("open_target", "open_target", {"target": "youtube"}),
                ("get_system_info", "get_system_info", {}),
                ("open_application", "open_application", {"application": "editor"}),
                ("browser_open", "browser_open", {"target": "youtube", "profile": "Default"}),
                ("browser_search", "browser_search", {"query": "mochi", "profile": "Default"}),
                ("browser_profiles:list", "browser_profiles", {"action": "list"}),
                ("browser_profiles:active", "browser_profiles", {"action": "active"}),
                ("web_inspect", "web_inspect", {}),
                ("play_web_media", "play_web_media", {"site": "YouTube", "query": "Bohemian Rhapsody"}),
                *[(f"web_act:{action}", "web_act", {"element_id": "e1", "action": action, "value": "25" if action == "set_range" else "prueba", "snapshot_id": "s-smoke"}) for action in ("click", "invoke", "keyboard_activate", "focus", "type", "submit", "select", "toggle", "scroll_into_view", "set_range")],
                ("list_open_windows", "list_open_windows", {}),
                ("close_window", "close_window", {"window": "editor"}),
                ("close_application_windows", "close_application_windows", {"application": "editor"}),
                *[(f"control_window:{action}", "control_window", {"window": "editor", "action": action}) for action in ("focus", "minimize", "maximize")],
            ]
            results = {label: registry.execute(name, arguments) for label, name, arguments in calls}

        self.assertEqual({name for _label, name, _arguments in calls}, set(registry.get_tool_names()))
        # play_web_media la ejecuta el agente; sola solo avisa.
        self.assertEqual(results.pop("play_web_media")["error"], "agent_only")
        self.assertTrue(all(result.get("ok") for result in results.values()))
        for result in results.values():
            json.dumps(result, ensure_ascii=False)

    def test_browser_search_and_profile_diagnostics_delegate_without_opening_a_real_browser(self):
        with patch.object(browser_tools, "_resolver") as resolver, \
             patch.object(browser_tools._browser, "open_url_for_profile", return_value={"ok": True, "url": "https://search.test/?q=mochi"}) as open_url:
            resolver.search_url.return_value = "https://search.test/?q=mochi"
            search = browser_tools.browser_search("mochi", profile="Default")
        self.assertTrue(search["ok"])
        self.assertEqual((search["kind"], search["query"]), ("search", "mochi"))
        open_url.assert_called_once_with("https://search.test/?q=mochi", profile="Default")

        with patch.object(browser_tools._browser, "get_profiles", return_value={"Default": {"name": "Personal"}}), \
             patch.object(browser_tools._browser, "get_default_profile", return_value="Default"), \
             patch.object(browser_tools._browser, "get_active_profile_info", return_value={"id": "Default"}):
            listing = browser_tools.browser_profiles()
            active = browser_tools.browser_profiles(action="active")
        self.assertEqual((len(listing["profiles"]), active["profile"]["id"]), (1, "Default"))

    def test_volume_wrappers_normalize_numeric_and_textual_controller_results(self):
        with patch("brain.mochi_tools.media_controller.set_volume", return_value=(True, "Volumen actualizado")) as set_volume:
            changed = set_system_volume("42.4")
        set_volume.assert_called_once_with(42)
        self.assertEqual((changed["ok"], changed["percent"]), (True, 42))

        with patch("brain.mochi_tools.media_controller.get_volume", return_value=(True, "El volumen está al 37%")):
            current = get_system_volume()
        self.assertEqual((current["ok"], current["percent"]), (True, 37))

    def test_media_transport_is_exposed_without_claiming_player_state(self):
        with patch("brain.mochi_tools.media_controller.play_pause", return_value=(True, "Reproducción alternada")) as play_pause:
            result = control_media("play_pause")
        play_pause.assert_called_once_with()
        self.assertTrue(result["ok"])
        self.assertEqual(result["scope"], "active_windows_media_session")
        self.assertFalse(result["state_confirmed"])

    def test_media_relative_volume_uses_existing_controller_delta(self):
        with patch("brain.mochi_tools.media_controller.change_volume", return_value=(True, "Volumen al 35%")) as change_volume:
            result = control_media("volume_down", amount=7)
        change_volume.assert_called_once_with(-7.0)
        self.assertTrue(result["ok"])
        self.assertEqual(result["amount"], 7.0)
        self.assertTrue(result["state_confirmed"])

    def test_media_control_rejects_unknown_or_zero_adjustments(self):
        self.assertFalse(control_media("dance")["ok"])
        self.assertFalse(control_media("volume_up", amount=0)["ok"])

    def test_explicit_media_states_delegate_to_the_observable_session(self):
        with patch("brain.mochi_tools.media_controller.set_media_session_state", return_value={
            "ok": True, "state_confirmed": True, "playback_status": "paused",
        }) as set_state:
            result = control_media("pause", expected_title="Video - YouTube")
        set_state.assert_called_once_with("pause", "Video - YouTube")
        self.assertTrue(result["ok"])
        self.assertTrue(result["state_confirmed"])

    def test_find_files_returns_serializable_metadata_without_opening(self):
        result_item = {
            "name": "Presupuesto.pdf",
            "path": Path("C:/Users/Admin/Documents/Presupuesto.pdf"),
            "score": 0.97,
            "is_directory": False,
        }
        with patch("brain.mochi_tools.file_manager.search", return_value=[result_item]) as search:
            result = find_files("presupuesto", extensions=["pdf"])
        search.assert_called_once_with(
            query="presupuesto",
            extensions=["pdf"],
            want_directory=False,
            max_results=5,
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["results"][0]["path"], str(result_item["path"]))

    def test_folder_and_download_wrappers_keep_search_and_opening_separate(self):
        folder = {"name": "Proyecto", "path": Path("C:/Users/Admin/Documents/Proyecto"), "score": 1.0, "is_directory": True}
        with patch("brain.mochi_tools.file_manager.search", return_value=[folder]) as search, \
             patch("brain.mochi_tools.file_manager.open_path", return_value=(True, "Carpeta abierta")) as open_path:
            found = find_folders("proyecto")
            opened = open_folder("proyecto")
        search.assert_any_call(query="proyecto", extensions=None, want_directory=True, max_results=5)
        self.assertEqual(found["results"][0]["kind"], "folder")
        self.assertTrue(opened["ok"])
        open_path.assert_called_once_with(folder["path"])

        folders = {"documents": Path("C:/Users/Admin/Documents")}
        aliases = {"documentos": "documents"}
        with patch.object(mochi_tools.file_manager, "folders", folders), \
             patch.object(mochi_tools.file_manager, "folder_aliases", aliases), \
             patch("brain.mochi_tools.file_manager.open_path", return_value=(True, "Carpeta abierta")) as open_path, \
             patch("brain.mochi_tools.file_manager.open_latest_download", return_value=(True, "Descarga abierta")) as latest:
            known = open_known_folder("Documentos")
            listed = list_user_folders()
            download = open_latest_download()
        self.assertTrue(known["ok"])
        self.assertEqual(listed["count"], 1)
        self.assertTrue(download["ok"])
        open_path.assert_called_once_with(folders["documents"])
        latest.assert_called_once_with()

    def test_open_file_refuses_ambiguous_matches_without_opening(self):
        matches = [
            {"name": "Informe 2025.pdf", "path": Path("C:/a.pdf"), "score": 0.95, "is_directory": False},
            {"name": "Informe final.pdf", "path": Path("C:/b.pdf"), "score": 0.91, "is_directory": False},
        ]
        with patch("brain.mochi_tools.file_manager.search", return_value=matches), patch("brain.mochi_tools.file_manager.open_path") as open_path:
            result = open_file("informe")
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "ambiguous_match")
        open_path.assert_not_called()

    def test_open_file_opens_a_clear_high_confidence_match(self):
        match = {"name": "Informe.pdf", "path": Path("C:/Informe.pdf"), "score": 1.0, "is_directory": False}
        with patch("brain.mochi_tools.file_manager.search", return_value=[match]), patch("brain.mochi_tools.file_manager.open_path", return_value=(True, "Abriendo Informe.pdf")) as open_path:
            result = open_file("informe")
        self.assertTrue(result["ok"])
        open_path.assert_called_once_with(match["path"])

    def test_catalog_application_and_window_wrappers_preserve_observed_metadata(self):
        app = {"name": "Editor", "type": "shortcut", "source": "start_menu"}
        game = {"name": "Juego", "launcher": "steam"}
        window = {"title": "Editor - Documento", "process": "editor.exe", "pid": 15}
        with patch("brain.mochi_tools.application_registry.list_names", return_value=["Editor", "Correo"]), \
             patch("brain.mochi_tools.application_registry.find", return_value=app), \
             patch("brain.mochi_tools.application_registry.launch", return_value=(True, "Editor abierto")), \
             patch("brain.mochi_tools.game_registry.list_names", return_value=["Juego"]), \
             patch("brain.mochi_tools.game_registry.find", return_value=game), \
             patch("brain.mochi_tools.game_registry.launch", return_value=(True, "Juego iniciado")), \
             patch("brain.mochi_tools.window_manager.get_windows", return_value=[window]), \
             patch("brain.mochi_tools.window_manager.find", return_value=window), \
             patch("brain.mochi_tools.window_manager.find_all", return_value=[window]), \
             patch("brain.mochi_tools.window_manager.focus", return_value=(True, "Enfocada")), \
             patch("brain.mochi_tools.window_manager.close", return_value=(True, "Cerrada")), \
             patch("brain.mochi_tools.window_manager.close_all", return_value=(True, "Cerradas")):
            applications = list_installed_applications(query="edit")
            games = list_games()
            application = mochi_tools.open_application("editor")
            launched = launch_game("juego")
            windows = list_open_windows()
            focused = control_window("editor", "focus")
            closed = close_window("editor")
            closed_all = close_application_windows("editor")
        self.assertEqual((applications["applications"], games["games"]), (["Editor"], ["Juego"]))
        self.assertTrue(all(item["ok"] for item in [application, launched, windows, focused, closed, closed_all]))
        self.assertEqual((focused["pid"], closed_all["window_count"]), (15, 1))

    def test_system_info_delegates_to_the_existing_profiler(self):
        info = {"os": {"system": "Windows"}, "ram": {"total_gb": 16}}
        with patch("brain.mochi_tools.hardware_profiler.analyze", return_value=info):
            result = get_system_info()
        self.assertTrue(result["ok"])
        self.assertEqual(result["system"], info)

    def test_clipboard_tools_only_delegate_when_called(self):
        with patch("brain.mochi_tools.desktop_tools.read_clipboard", return_value=(True, "texto privado")) as read:
            result = read_clipboard()
        read.assert_called_once_with()
        self.assertEqual(result["text"], "texto privado")

        with patch("brain.mochi_tools.desktop_tools.write_clipboard", return_value=(True, "Texto copiado al portapapeles.")) as write:
            result = write_clipboard("hola")
        write.assert_called_once_with("hola")
        self.assertTrue(result["ok"])

    def test_screenshot_and_notification_return_honest_dispatch_metadata(self):
        with patch("brain.mochi_tools.desktop_tools.capture_screenshot", return_value=(True, Path("C:/capture.png"))):
            screenshot = capture_screenshot("capture")
        self.assertEqual(screenshot["path"], str(Path("C:/capture.png")))

        with patch("brain.mochi_tools.desktop_tools.show_notification", return_value=(True, "Orden de notificación enviada a Windows.")):
            notification = show_notification("Mochi", "Prueba")
        self.assertTrue(notification["ok"])
        self.assertFalse(notification["state_confirmed"])


class WebInteractionStateTests(unittest.TestCase):
    def test_inspector_exposes_selection_item_state(self):
        class Pattern:
            CurrentIsSelected = True

        class Raw:
            def QueryInterface(self, _interface):
                return Pattern()

        class Element:
            def GetCurrentPattern(self, _pattern_id):
                return Raw()

        class API:
            UIA_SelectionItemPatternId = 17
            IUIAutomationSelectionItemPattern = object()

        inspector = WebInspector()
        inspector._api = API()

        self.assertTrue(inspector._selection_metadata(Element()))

    def test_selected_sort_is_verified_even_when_snapshot_digest_did_not_change(self):
        state = plan_goal("busca el ultimo video que publico auronplay en su canal principal")
        state.mark_completed("browser_open")
        state.mark_completed("search_page")
        state.mark_completed("select_channel")
        state.mark_completed("select_content_tab")
        state.note_web_action("select_sort")

        state.verify_web_observation({
            "changed": False,
            "elements": [{"role": "tab", "name": "Más recientes", "selected": True}],
        })

        self.assertEqual(state.step("select_sort").status, "completed")

    def test_element_ids_are_snapshot_scoped_and_become_stale(self):
        store = ElementStore()
        snapshot_id = store.create("Page", None, (), {"e1": {"element": object()}})
        snapshot, item, error = store.resolve("e1", snapshot_id)
        self.assertIsNone(error)
        self.assertIsNotNone(item)
        store.mark_stale(snapshot_id)
        _, _, error = store.resolve("e1", snapshot_id)
        self.assertEqual(error, "stale_element")

    def test_renderer_document_is_selected_over_empty_webviews(self):
        class Api:
            UIA_ButtonControlTypeId=1; UIA_HyperlinkControlTypeId=2; UIA_EditControlTypeId=3; UIA_CheckBoxControlTypeId=4; UIA_RadioButtonControlTypeId=5; UIA_ComboBoxControlTypeId=6; UIA_MenuItemControlTypeId=7; UIA_TabItemControlTypeId=8; UIA_ListItemControlTypeId=9; UIA_DocumentControlTypeId=10
        class Node:
            def __init__(self, kind, name="", cls="", children=()): self.CurrentControlType=kind; self.CurrentName=name; self.CurrentClassName=cls; self.CurrentValue=""; self.CurrentIsOffscreen=False; self.children=list(children)
            def GetRuntimeId(self): return [id(self)]
        class Walker:
            def GetFirstChildElement(self, node): return node.children[0] if node.children else None
            def GetNextSiblingElement(self, node):
                parent = parents.get(id(node)); index = parent.children.index(node)
                return parent.children[index + 1] if index + 1 < len(parent.children) else None
        link_a, link_b = Node(2,"User A"), Node(2,"User B")
        document = Node(10,"Example","",[Node(9,"User A","",[link_a]),Node(9,"User B","",[link_b])])
        renderer = Node(99,"","Chrome_RenderWidgetHostHWND",[document])
        root = Node(99,"Chrome", "", [Node(10,"","WebView"), renderer, Node(10,"","NewTabFooterWebView")])
        parents={}
        def map_parents(node):
            for child in node.children: parents[id(child)]=node; map_parents(child)
        map_parents(root)
        inspector=WebInspector(); inspector._uia=object(); inspector._api=Api(); inspector._active_window=lambda:(1,"Example - Google Chrome",True)
        inspector._load_uia=lambda:(True,None)
        inspector._uia=type("U",(),{"ElementFromHandle":lambda _self,_hwnd:root,"RawViewWalker":Walker()})()
        result=inspector.inspect()
        self.assertTrue(result["ok"])
        self.assertEqual(result["page"]["title"],"Example")
        self.assertEqual([(x["id"],x["role"],x["name"]) for x in result["elements"]],[("e1","link","User A"),("e2","link","User B")])
        self.assertIsNotNone(inspector.store.get(result["snapshot_id"]))

    def test_query_without_accessible_match_falls_back_to_compact_snapshot(self):
        class Api:
            UIA_ButtonControlTypeId=1; UIA_HyperlinkControlTypeId=2; UIA_EditControlTypeId=3; UIA_CheckBoxControlTypeId=4; UIA_RadioButtonControlTypeId=5; UIA_ComboBoxControlTypeId=6; UIA_MenuItemControlTypeId=7; UIA_TabItemControlTypeId=8; UIA_ListItemControlTypeId=9; UIA_DocumentControlTypeId=10
        class Node:
            def __init__(self, kind, name="", cls="", children=()): self.CurrentControlType=kind; self.CurrentName=name; self.CurrentClassName=cls; self.CurrentValue=""; self.CurrentIsOffscreen=False; self.children=list(children)
            def GetRuntimeId(self): return [id(self)]
        class Walker:
            def GetFirstChildElement(self, node): return node.children[0] if node.children else None
            def GetNextSiblingElement(self, node):
                parent=parents[id(node)]; index=parent.children.index(node)
                return parent.children[index + 1] if index + 1 < len(parent.children) else None
        link = Node(2, "Isaías")
        document = Node(10, "Example", "", [link])
        renderer = Node(99, "", "Chrome_RenderWidgetHostHWND", [document])
        root = Node(99, "Chrome", "", [renderer])
        parents = {id(document): renderer, id(link): document, id(renderer): root}
        inspector=WebInspector(); inspector._api=Api(); inspector._active_window=lambda:(1,"Example - Google Chrome",True)
        inspector._load_uia=lambda:(True,None)
        inspector._uia=type("U",(),{"ElementFromHandle":lambda _self,_hwnd:root,"RawViewWalker":Walker()})()
        result = inspector.inspect(query="profile settings")
        self.assertTrue(result["ok"])
        self.assertFalse(result["query_matched"])
        self.assertEqual([(item["role"], item["name"]) for item in result["elements"]], [("link", "Isaías")])

    def _interactor(self, element, api):
        inspector = WebInspector()
        inspector._api = api
        snapshot_id = inspector.store.create("Page", None, (), {
            "e1": {"element": element, "runtime_id": (1,), "role": "link", "name": "Example", "value": ""}
        })
        return WebInteractor(inspector), inspector, snapshot_id

    def test_patterns_are_query_interfaced_to_generated_interfaces(self):
        class Api:
            UIA_InvokePatternId=1; UIA_SelectionItemPatternId=2; UIA_TogglePatternId=3; UIA_ValuePatternId=4; UIA_ExpandCollapsePatternId=5; UIA_LegacyIAccessiblePatternId=6; UIA_ScrollItemPatternId=7
            IUIAutomationInvokePattern="invoke-interface"; IUIAutomationSelectionItemPattern="selection-interface"; IUIAutomationTogglePattern="toggle-interface"; IUIAutomationValuePattern="value-interface"; IUIAutomationExpandCollapsePattern="expand-interface"; IUIAutomationLegacyIAccessiblePattern="legacy-interface"; IUIAutomationScrollItemPattern="scroll-interface"
        class Raw:
            def __init__(self): self.interfaces=[]
            def QueryInterface(self, interface): self.interfaces.append(interface); return object()
        class Element:
            def GetCurrentPattern(self, _pattern_id): return raw
        raw = Raw()
        interactor = WebInteractor(WebInspector())
        interactor.inspector._api = Api()
        for name, expected in (("invoke", "invoke-interface"), ("selection_item", "selection-interface"), ("toggle", "toggle-interface"), ("value", "value-interface"), ("expand_collapse", "expand-interface"), ("scroll_item", "scroll-interface"), ("legacy_accessible", "legacy-interface")):
            self.assertIsNotNone(interactor._pattern(Element(), name))
            self.assertEqual(raw.interfaces[-1], expected)

    def test_scroll_into_view_uses_scroll_item_pattern_and_invalidates_snapshot(self):
        class Api:
            UIA_ScrollItemPatternId=1
            IUIAutomationScrollItemPattern="scroll-interface"
        class ScrollItem:
            def __init__(self): self.called = False
            def ScrollIntoView(self): self.called = True
        scroll_item = ScrollItem()
        class Raw:
            def QueryInterface(self, interface):
                if interface == "scroll-interface": return scroll_item
                raise RuntimeError("unexpected interface")
        class Element:
            def GetRuntimeId(self): return [1]
            def GetCurrentPattern(self, pattern_id): return Raw() if pattern_id == 1 else None
        interactor, inspector, snapshot_id = self._interactor(Element(), Api())
        result = interactor.act("e1", "scroll_into_view", snapshot_id=snapshot_id)
        self.assertTrue(result["ok"])
        self.assertTrue(scroll_item.called)
        self.assertTrue(inspector.store.get(snapshot_id).stale)

    def test_keyboard_activate_focuses_an_actionable_element_and_uses_standard_key(self):
        class Element:
            def __init__(self): self.focused = False
            def GetRuntimeId(self): return [1]
            def SetFocus(self): self.focused = True
        element = Element()
        interactor, inspector, snapshot_id = self._interactor(element, object())

        with patch.object(interactor, "_press_virtual_key") as press_key:
            result = interactor.act("e1", "keyboard_activate", snapshot_id=snapshot_id)

        self.assertTrue(result["ok"])
        self.assertTrue(element.focused)
        press_key.assert_called_once_with(0x0D)
        self.assertEqual(result["activation_pattern"], "keyboard_enter")
        self.assertTrue(inspector.store.get(snapshot_id).stale)

    def test_set_range_uses_the_typed_uia_range_pattern_and_checks_bounds(self):
        class Api:
            UIA_RangeValuePatternId = 1
            IUIAutomationRangeValuePattern = "range-interface"
        class Range:
            CurrentMinimum = 0
            CurrentMaximum = 120
            def __init__(self): self.value = None
            def SetValue(self, value): self.value = value
        pattern = Range()
        class Raw:
            def QueryInterface(self, interface):
                if interface == "range-interface": return pattern
                raise RuntimeError("unexpected interface")
        class Element:
            def GetRuntimeId(self): return [1]
            def GetCurrentPattern(self, pattern_id): return Raw() if pattern_id == 1 else None
        interactor, inspector, snapshot_id = self._interactor(Element(), Api())
        result = interactor.act("e1", "set_range", value="75", snapshot_id=snapshot_id)
        self.assertTrue(result["ok"])
        self.assertEqual(pattern.value, 75.0)
        self.assertTrue(inspector.store.get(snapshot_id).stale)

        interactor, inspector, snapshot_id = self._interactor(Element(), Api())
        result = interactor.act("e1", "set_range", value=121, snapshot_id=snapshot_id)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "range_value_out_of_bounds")
        self.assertFalse(inspector.store.get(snapshot_id).stale)

    def test_set_range_falls_back_to_calibrated_focused_arrow_keys_when_provider_ignores_setvalue(self):
        class Api:
            UIA_RangeValuePatternId = 1
            IUIAutomationRangeValuePattern = "range-interface"
        class Range:
            CurrentMinimum = 0
            CurrentMaximum = 120
            CurrentValue = 10
            def SetValue(self, _value):
                pass
        pattern = Range()
        class Raw:
            def QueryInterface(self, interface):
                if interface == "range-interface": return pattern
                raise RuntimeError("unexpected interface")
        class Element:
            def __init__(self): self.focused = False
            def GetRuntimeId(self): return [1]
            def GetCurrentPattern(self, pattern_id): return Raw() if pattern_id == 1 else None
            def SetFocus(self): self.focused = True
        element = Element()
        interactor, inspector, snapshot_id = self._interactor(element, Api())

        def press_key(key, count=1):
            pattern.CurrentValue += (5 if key == 0x27 else -5) * count

        with (
            patch.object(interactor, "_press_virtual_key", side_effect=press_key),
            patch("web_interaction.interactor.time.sleep"),
        ):
            result = interactor.act("e1", "set_range", value="20", snapshot_id=snapshot_id)

        self.assertTrue(result["ok"])
        self.assertTrue(element.focused)
        self.assertEqual(pattern.CurrentValue, 20)
        self.assertEqual(result["range_strategy"], "focused_range_keyboard")
        self.assertEqual(result["observed_range_value"], 20)
        self.assertTrue(inspector.store.get(snapshot_id).stale)

    def test_click_uses_expand_collapse_when_invoke_is_unavailable(self):
        class Api:
            UIA_InvokePatternId=1; UIA_SelectionItemPatternId=2; UIA_ExpandCollapsePatternId=3
            IUIAutomationInvokePattern="invoke"; IUIAutomationSelectionItemPattern="selection"; IUIAutomationExpandCollapsePattern="expand"
            ExpandCollapseState_Expanded=1
        class Expander:
            CurrentExpandCollapseState=0
            def __init__(self): self.expanded = False
            def Expand(self): self.expanded = True
            def Collapse(self): raise AssertionError("a collapsed menu must be opened")
        expander = Expander()
        class Raw:
            def QueryInterface(self, interface):
                if interface == "expand": return expander
                raise RuntimeError("pattern unavailable")
        class Element:
            def GetRuntimeId(self): return [1]
            def GetCurrentPattern(self, pattern_id): return Raw() if pattern_id == 3 else None
        interactor, inspector, snapshot_id = self._interactor(Element(), Api())
        result = interactor.act("e1", "click", snapshot_id=snapshot_id)
        self.assertTrue(result["ok"])
        self.assertEqual(result["activation_pattern"], "expand_collapse")
        self.assertTrue(expander.expanded)
        self.assertTrue(inspector.store.get(snapshot_id).stale)

    def test_click_uses_legacy_default_action_when_no_modern_pattern_exists(self):
        class Api:
            UIA_InvokePatternId=1; UIA_SelectionItemPatternId=2; UIA_ExpandCollapsePatternId=3; UIA_LegacyIAccessiblePatternId=4
            IUIAutomationInvokePattern="invoke"; IUIAutomationSelectionItemPattern="selection"; IUIAutomationExpandCollapsePattern="expand"; IUIAutomationLegacyIAccessiblePattern="legacy"
        class Legacy:
            def __init__(self): self.called = False
            def DoDefaultAction(self): self.called = True
        legacy = Legacy()
        class Raw:
            def QueryInterface(self, interface):
                if interface == "legacy": return legacy
                raise RuntimeError("pattern unavailable")
        class Element:
            def GetRuntimeId(self): return [1]
            def GetCurrentPattern(self, pattern_id): return Raw() if pattern_id == 4 else None
        interactor, inspector, snapshot_id = self._interactor(Element(), Api())
        result = interactor.act("e1", "click", snapshot_id=snapshot_id)
        self.assertTrue(result["ok"])
        self.assertEqual(result["activation_pattern"], "legacy_accessible")
        self.assertTrue(legacy.called)
        self.assertTrue(inspector.store.get(snapshot_id).stale)

    def test_explicit_legacy_activation_bypasses_broken_invoke_pattern(self):
        class Api:
            UIA_LegacyIAccessiblePatternId = 4
            IUIAutomationLegacyIAccessiblePattern = "legacy"
        class Legacy:
            def __init__(self): self.called = False
            def DoDefaultAction(self): self.called = True
        legacy = Legacy()
        class Raw:
            def QueryInterface(self, interface):
                if interface == "legacy": return legacy
                raise RuntimeError("unexpected interface")
        class Element:
            def GetRuntimeId(self): return [1]
            def GetCurrentPattern(self, pattern_id): return Raw() if pattern_id == 4 else None
        interactor, inspector, snapshot_id = self._interactor(Element(), Api())

        result = interactor.act("e1", "legacy_activate", snapshot_id=snapshot_id)

        self.assertTrue(result["ok"])
        self.assertEqual(result["activation_pattern"], "legacy_accessible")
        self.assertTrue(legacy.called)
        self.assertTrue(inspector.store.get(snapshot_id).stale)

    def test_type_uses_legacy_value_when_value_pattern_is_unavailable(self):
        class Api:
            UIA_ValuePatternId=1; UIA_LegacyIAccessiblePatternId=2
            IUIAutomationValuePattern="value"; IUIAutomationLegacyIAccessiblePattern="legacy"
        class Legacy:
            def __init__(self): self.value = None
            def SetValue(self, value): self.value = value
        legacy = Legacy()
        class Raw:
            def QueryInterface(self, interface):
                if interface == "legacy": return legacy
                raise RuntimeError("value pattern unavailable")
        class Element:
            def GetRuntimeId(self): return [1]
            def GetCurrentPattern(self, pattern_id): return Raw() if pattern_id == 2 else None
        interactor, inspector, snapshot_id = self._interactor(Element(), Api())
        inspector.store.get(snapshot_id).elements["e1"]["role"] = "textbox"
        result = interactor.act("e1", "type", value="hola", snapshot_id=snapshot_id)
        self.assertTrue(result["ok"])
        self.assertEqual(legacy.value, "hola")

    def test_submit_focuses_the_inspected_textbox_and_invalidates_snapshot(self):
        class Element:
            def GetRuntimeId(self): return [1]
        interactor, inspector, snapshot_id = self._interactor(Element(), object())
        inspector.store.get(snapshot_id).elements["e1"]["role"] = "textbox"
        with patch.object(WebInteractor, "_submit_focused_element") as submit:
            result = interactor.act("e1", "submit", snapshot_id=snapshot_id)
        self.assertTrue(result["ok"])
        submit.assert_called_once_with(inspector.store.get(snapshot_id).elements["e1"]["element"])
        self.assertTrue(inspector.store.get(snapshot_id).stale)

    def test_failed_action_keeps_snapshot_valid_and_success_invalidates_it(self):
        class Api:
            UIA_InvokePatternId=1; IUIAutomationInvokePattern="invoke-interface"
        class FailingPattern:
            def Invoke(self): raise RuntimeError("provider rejected invoke")
        class Raw:
            def QueryInterface(self, _interface): return FailingPattern()
        class Element:
            def GetRuntimeId(self): return [1]
            def GetCurrentPattern(self, _pattern_id): return Raw()
        interactor, inspector, snapshot_id = self._interactor(Element(), Api())
        self.assertFalse(interactor.act("e1", "click", snapshot_id=snapshot_id)["ok"])
        self.assertFalse(inspector.store.get(snapshot_id).stale)

        class WorkingPattern:
            def Invoke(self): pass
        class WorkingRaw:
            def QueryInterface(self, _interface): return WorkingPattern()
        class WorkingElement(Element):
            def GetCurrentPattern(self, _pattern_id): return WorkingRaw()
        interactor, inspector, snapshot_id = self._interactor(WorkingElement(), Api())
        self.assertTrue(interactor.act("e1", "click", snapshot_id=snapshot_id)["ok"])
        self.assertTrue(inspector.store.get(snapshot_id).stale)

    def test_runtime_id_mismatch_is_a_real_stale_element(self):
        class Element:
            def GetRuntimeId(self): return [99]
        interactor, inspector, snapshot_id = self._interactor(Element(), object())
        result = interactor.act("e1", "focus", snapshot_id=snapshot_id)
        self.assertEqual(result["error"], "stale_element")
        self.assertTrue(inspector.store.get(snapshot_id).stale)

    def test_new_snapshot_allows_same_element_id_after_prior_failure(self):
        executions = []
        registry = ToolRegistry()
        registry.register("web_inspect", "inspect", {"type": "object", "properties": {}}, lambda: {"ok": True, "snapshot_id": "s-new", "changed": True})
        registry.register("web_act", "act", {"type": "object", "properties": {"element_id": {"type": "string"}, "action": {"type": "string"}}}, lambda **kwargs: executions.append(kwargs) or {"ok": False, "message": "first provider failure"})
        router = ScriptedRouter([
            BrainResult(True, tool_calls=[{"function": {"name": "web_act", "arguments": {"element_id": "e3", "action": "click"}}}]),
            BrainResult(True, tool_calls=[{"function": {"name": "web_inspect", "arguments": {}}}]),
            BrainResult(True, tool_calls=[{"function": {"name": "web_act", "arguments": {"element_id": "e3", "action": "click"}}}]),
            BrainResult(True, text="done"),
        ])
        result = MochiAgentLoop(router=router, max_steps=4).run("Interactúa con la página.", registry)
        self.assertEqual(len(executions), 2)
        self.assertNotEqual(result["actions"][2]["result"].get("error"), "duplicate_failed_action")


class GoalPlanningTests(unittest.TestCase):
    def test_site_account_switch_is_parsed_as_internal_profile_transition(self):
        state = plan_goal("Cambia la cuenta de Netflix de Isaías a Marian, por favor.")
        self.assertEqual(state.target, "Netflix")
        self.assertIsNone(state.browser_profile)
        self.assertEqual(state.site_profile_source, "Isaías")
        self.assertEqual(state.site_profile, "Marian")
        self.assertEqual([(step.scope, step.operation) for step in state.steps], [("web_page", "select_site_profile")])

    def test_natural_profile_switch_phrase_uses_page_context_without_reopening_chrome(self):
        state = plan_goal("Cambia de perfil de Netflix al de Isaías, estamos utilizando la cuenta de Google de Marian.")
        self.assertEqual(state.goal, "site_context_action")
        self.assertEqual((state.target, state.site_profile_source, state.site_profile), ("Netflix", "Marian", "Isaías"))
        self.assertIsNone(state.browser_profile)
        self.assertEqual([(step.scope, step.operation) for step in state.steps], [("web_page", "select_site_profile")])

    def test_explicit_site_search_has_open_then_page_search_steps(self):
        state = plan_goal("Busca Rick y Morty en Netflix.")
        self.assertEqual((state.goal, state.target, state.page_query), ("search_in_page", "Netflix", "Rick y Morty"))
        self.assertEqual([(step.scope, step.operation) for step in state.steps], [("browser", "browser_open"), ("web_page", "search_page")])

    def test_buscame_variant_is_a_scoped_page_search(self):
        state = plan_goal("buscame un tutorial para python en español en youtube")

        self.assertEqual((state.goal, state.target, state.page_query), (
            "search_in_page", "youtube", "un tutorial para python en español",
        ))

    def test_named_result_after_a_page_search_becomes_a_verified_follow_up(self):
        state = plan_goal("Busca Prueba de Mochi en YouTube y abre Prueba de Mochi.")
        self.assertEqual(
            (state.goal, state.target, state.page_query, state.page_result),
            ("search_and_open_result", "YouTube", "Prueba de Mochi", "Prueba de Mochi"),
        )
        self.assertEqual(
            [(step.operation, step.depends_on) for step in state.steps],
            [("browser_open", None), ("search_page", "open"), ("select_result", "page_search")],
        )

    def test_pronoun_playback_search_keeps_youtube_out_of_the_site_name(self):
        state = plan_goal("busca un tutorial de godot en youtube y reproducelo")

        self.assertEqual((state.goal, state.target, state.page_query), (
            "search_and_play_any_result", "youtube", "un tutorial de godot",
        ))
        self.assertEqual(
            [(step.operation, step.value, step.depends_on) for step in state.steps],
            [
                ("browser_open", None, None),
                ("search_page", "un tutorial de godot", "open"),
                ("select_visible_video", "visible:1", "page_search"),
                ("play_media", "Reproducir", "visible_video"),
            ],
        )

    def test_channel_requests_preserve_channel_navigation_and_order(self):
        latest = plan_goal("busca el ultimo video que publico auronplay en su canal principal")
        oldest = plan_goal("busca el primer video del canal de rubius")
        channel = plan_goal("busca tommy black y entra a su canal")

        self.assertEqual((latest.goal, latest.target, latest.channel_name, latest.result_order), (
            "channel_video", "YouTube", "auronplay", "newest",
        ))
        self.assertEqual(
            [(step.operation, step.value, step.depends_on) for step in latest.steps],
            [
                ("browser_open", None, None),
                ("search_page", "auronplay", "open"),
                ("select_channel", "auronplay", "page_search"),
                ("select_content_tab", "Videos", "channel"),
                ("select_sort", "Mas recientes", "content_tab"),
                ("select_visible_video", "visible:1", "sort"),
                ("play_media", "Reproducir", "visible_video"),
            ],
        )
        self.assertEqual((oldest.channel_name, oldest.result_order), ("rubius", "oldest"))
        self.assertEqual(oldest.step("select_sort").value, "Mas antiguos")
        self.assertEqual((channel.goal, channel.channel_name), ("open_channel", "tommy black"))
        self.assertIsNone(channel.step("select_content_tab"))

    def test_change_video_is_scoped_to_the_current_tab(self):
        state = plan_goal("Cambia de video a Prueba de Mochi.")
        self.assertEqual(
            (state.goal, state.target, state.page_query, state.page_result),
            ("change_active_video", None, "Prueba de Mochi", "Prueba de Mochi"),
        )
        self.assertEqual(
            [(step.scope, step.operation, step.depends_on) for step in state.steps],
            [("web_page", "search_page", None), ("web_page", "select_result", "page_search")],
        )

    def test_put_media_in_site_becomes_a_search_and_exact_result_selection(self):
        state = plan_goal("Pon Moviendo caderas de Daddy Yanke en YouTube.")
        self.assertEqual(
            (state.goal, state.target, state.page_query, state.page_result),
            ("search_and_open_result", "YouTube", "Moviendo caderas de Daddy Yanke", "Moviendo caderas"),
        )
        self.assertEqual(
            [(step.operation, step.depends_on) for step in state.steps],
            [("browser_open", None), ("search_page", "open"), ("select_result", "page_search"), ("play_media", "select_result")],
        )

    def test_compound_site_media_command_preserves_every_dependency(self):
        state = plan_goal(
            "Abre Netflix en la cuenta de Google de Marian y entra en el perfil de Marian, "
            "busca la serie de Acaramelados y pon el capitulo 7 y pausalo."
        )
        self.assertEqual(
            (state.goal, state.target, state.browser_profile, state.site_profile, state.page_query, state.page_result),
            ("site_content_sequence", "Netflix", "Marian", "Marian", "Acaramelados", "Acaramelados"),
        )
        self.assertEqual(
            [(step.operation, step.value, step.depends_on) for step in state.steps],
            [
                ("browser_open", "Marian", None),
                ("select_site_profile", "Marian", "open"),
                ("search_page", "Acaramelados", "select_site_profile"),
                ("select_result", "Acaramelados", "search_page"),
                ("select_episode", "Capitulo 7", "select_result"),
                ("play_media", "Reproducir", "select_episode"),
                ("pause_media", "Pausa", "play_media"),
            ],
        )

    def test_compound_media_command_selects_season_before_episode(self):
        state = plan_goal(
            "Abre Netflix y busca la serie de Acaramelados, elige temporada 3 "
            "y pon el capitulo 2."
        )
        self.assertEqual(state.target, "Netflix")
        self.assertEqual(
            [(step.operation, step.value, step.depends_on) for step in state.steps],
            [
                ("browser_open", None, None),
                ("search_page", "Acaramelados", "open"),
                ("select_result", "Acaramelados", "search_page"),
                ("select_season", "Temporada 3", "select_result"),
                ("select_episode", "Capitulo 2", "select_season"),
                ("play_media", "Reproducir", "select_episode"),
            ],
        )

    def test_spoken_ordinals_are_normalized_for_season_and_episode(self):
        state = plan_goal(
            "Abre Netflix y busca The Office, pon el primer capitulo de la segunda temporada."
        )

        self.assertEqual(state.step("select_season").value, "Temporada 2")
        self.assertEqual(state.step("select_episode").value, "Capitulo 1")

    def test_seek_then_stop_word_keeps_both_ordered_controls(self):
        state = plan_goal(
            "Abre Netflix y busca el trailer del GTA VI, pon el minuto 15 y luego paralo."
        )

        operations = [(step.operation, step.value, step.depends_on) for step in state.steps]
        self.assertIn(("seek_media", "900", "play_media"), operations)
        self.assertIn(("pause_media", "Pausa", "seek_media"), operations)

    def test_search_site_first_keeps_seek_and_pause_clauses(self):
        state = plan_goal(
            "busca el trailer del GTA VI en Netflix, pon el minuto 15 y luego paralo"
        )

        self.assertEqual(
            (state.goal, state.target, state.page_query, state.page_result),
            ("site_content_sequence", "Netflix", "GTA VI", "GTA VI"),
        )
        self.assertEqual(
            [(step.operation, step.value, step.depends_on) for step in state.steps],
            [
                ("browser_open", None, None),
                ("search_page", "GTA VI", "open"),
                ("select_result", "GTA VI", "search_page"),
                ("play_media", "Reproducir", "select_result"),
                ("seek_media", "900", "play_media"),
                ("pause_media", "Pausa", "seek_media"),
            ],
        )

    def test_search_site_first_does_not_absorb_episode_clause_into_site_name(self):
        state = plan_goal(
            "busca The Office en Netflix y pon el primer capitulo de la segunda temporada"
        )

        self.assertEqual((state.goal, state.target, state.page_query), (
            "site_content_sequence", "Netflix", "The Office",
        ))
        self.assertEqual(state.step("select_season").value, "Temporada 2")
        self.assertEqual(state.step("select_episode").value, "Capitulo 1")

    def test_profile_clause_before_search_is_not_lost(self):
        state = plan_goal(
            "abre netflix, selecciona el perfil de Isaias, busca el trailer del GTA VI y reproducelo"
        )

        self.assertEqual(state.site_profile, "Isaias")
        self.assertEqual(state.step("select_site_profile").depends_on, "open")
        self.assertEqual(state.step("search_page").depends_on, "select_site_profile")

    def test_episode_continuation_reuses_the_current_page_and_preserves_pause(self):
        state = plan_goal("pon el capitulo 7 y pausalo")

        self.assertEqual((state.goal, state.target), ("current_page_episode", None))
        self.assertEqual(
            [(step.operation, step.value, step.depends_on) for step in state.steps],
            [
                ("select_episode", "Capitulo 7", None),
                ("play_media", "Reproducir", "select_episode"),
                ("pause_media", "Pausa", "play_media"),
            ],
        )
        self.assertEqual(
            MochiAgentLoop._next_named_page_operation(state),
            "select_episode",
        )

    def test_english_episode_continuation_is_scoped_to_the_current_page(self):
        state = plan_goal("play episode 3 and pause it")

        self.assertEqual((state.goal, state.target), ("current_page_episode", None))
        self.assertEqual(state.step("select_episode").value, "Episode 3")
        self.assertIsNone(state.step("select_episode").depends_on)
        self.assertEqual(state.step("pause_media").depends_on, "play_media")

    def test_player_positions_and_navigation_stay_in_current_tab_unless_a_site_is_named(self):
        seek = plan_goal("Pon el minuto 5:30")
        advance = plan_goal("Avanza 30 segundos")
        advance_formal = plan_goal("Adelante 5 minutos")
        rewind = plan_goal("Retroceda 5 segundos")
        previous = plan_goal("Vuelve al episodio anterior")
        site_seek = plan_goal("Abre Netflix y pon el minuto 3")

        self.assertEqual((seek.goal, seek.target, [(step.operation, step.value, step.depends_on) for step in seek.steps]), (
            "player_control", None, [("seek_media", "330", None)]
        ))
        self.assertEqual([(step.operation, step.value) for step in advance.steps], [("adjust_media_position", "30")])
        self.assertEqual([(step.operation, step.value) for step in advance_formal.steps], [("adjust_media_position", "300")])
        self.assertEqual([(step.operation, step.value) for step in rewind.steps], [("adjust_media_position", "-5")])
        self.assertEqual([(step.operation, step.value) for step in previous.steps], [("previous_media", None)])
        self.assertEqual((site_seek.goal, site_seek.target), ("site_player_control", "Netflix"))
        self.assertEqual(
            [(step.operation, step.value, step.depends_on) for step in site_seek.steps],
            [("browser_open", None, None), ("seek_media", "180", "browser_open")],
        )

    def test_start_from_beginning_seeks_before_confirming_playback(self):
        state = plan_goal("Empieza desde el inicio")
        self.assertEqual(
            [(step.operation, step.value, step.depends_on) for step in state.steps],
            [("seek_media", "0", None), ("play_media", "Reproducir", "seek_media")],
        )

    def test_seek_and_play_summary_reports_both_verified_outcomes(self):
        state = plan_goal("pon el video desde el inicio")
        state.mark_completed("seek_media")
        state.mark_completed("play_media")

        summary = MochiAgentLoop._goal_summary(state, "")

        self.assertIn("posición", summary)
        self.assertIn("reproduciéndose", summary)

    def test_visible_and_recommended_video_requests_stay_on_the_current_page(self):
        visible = plan_goal("Reproduce el 3 video en pantalla")
        recommended = plan_goal("Reproduce el primer video recomendado")
        self.assertEqual((visible.goal, visible.target), ("select_visible_video", None))
        self.assertEqual(
            [(step.operation, step.value, step.depends_on) for step in visible.steps],
            [("select_visible_video", "visible:3", None), ("play_media", "Reproducir", "select_visible_video")],
        )
        self.assertEqual(
            [(step.operation, step.value) for step in recommended.steps],
            [("select_visible_video", "recommended:1"), ("play_media", "Reproducir")],
        )

    def test_local_file_search_does_not_become_a_web_search(self):
        state = plan_goal("Busca un archivo llamado presupuesto en mis vacaciones.")
        self.assertEqual((state.goal, state.target), ("local_file_search", "presupuesto en mis vacaciones"))
        self.assertEqual([(step.scope, step.operation) for step in state.steps], [("filesystem", "find_files")])

    def test_local_file_title_with_en_is_not_mistaken_for_a_website(self):
        state = plan_goal("Busca un archivo llamado que hacer en mis vacaciones")
        self.assertEqual((state.goal, state.target), ("local_file_search", "que hacer en mis vacaciones"))
        self.assertEqual([(step.scope, step.operation) for step in state.steps], [("filesystem", "find_files")])

    def test_local_file_open_uses_the_file_tool_and_never_chrome(self):
        state = plan_goal("Abrí el archivo qué hacer en mis vacaciones.")
        self.assertEqual((state.goal, state.target), ("local_file_open", "qué hacer en mis vacaciones"))
        self.assertEqual([(step.scope, step.operation) for step in state.steps], [("filesystem", "open_file")])

    def test_direct_local_requests_are_planned_for_single_tool_completion(self):
        games = plan_goal("Mostrame los juegos instalados")
        launch = plan_goal("Abrí el juego dead as disco")
        system = plan_goal("Decime las especificaciones de mi computadora")
        self.assertEqual(games.steps[0].operation, "list_games")
        self.assertEqual((launch.target, launch.steps[0].operation), ("dead as disco", "launch_game"))
        self.assertEqual(system.steps[0].operation, "get_system_info")

    def test_bare_dotted_game_name_is_planned_as_a_local_game(self):
        state = plan_goal("Abre R.E.P.O.")
        self.assertEqual((state.target, state.steps[0].operation), ("R.E.P.O", "launch_game"))

    def test_bare_unknown_open_name_uses_the_generic_target_resolver(self):
        state = plan_goal("Abre repo")
        self.assertEqual((state.goal, state.target, state.steps[0].operation), ("open_named_target", "repo", "open_target"))

    def test_sensitive_local_reads_require_an_explicit_request(self):
        clipboard = plan_goal("Decime qué hay en el portapapeles")
        screenshot = plan_goal("Tomá una captura de pantalla")
        self.assertEqual(clipboard.steps[0].operation, "read_clipboard")
        self.assertEqual(screenshot.steps[0].operation, "capture_screenshot")

    def test_explicit_clipboard_write_and_notification_are_local_actions(self):
        clipboard = plan_goal("Copiá hola al portapapeles")
        notification = plan_goal("Mostrá una notificación de prueba")
        self.assertEqual(clipboard.steps[0].operation, "write_clipboard")
        self.assertEqual(notification.steps[0].operation, "show_notification")

    def test_browser_and_site_profiles_have_different_scopes(self):
        state = plan_goal("Abre Netflix con la cuenta de Google de Marian y utiliza el perfil Isaías en Netflix.")
        self.assertEqual(state.browser_profile, "Marian")
        self.assertEqual(state.site_profile, "Isaías")
        self.assertEqual([(step.scope, step.operation) for step in state.steps], [("browser", "browser_open"), ("web_page", "select_site_profile")])

    def test_implicit_browser_and_explicit_site_profiles_are_separate(self):
        state = plan_goal("Abre Netflix con Marian y en Netflix como Isaías.")
        self.assertEqual((state.browser_profile, state.site_profile), ("Marian", "Isaías"))

    def test_simple_known_web_open_is_planned_and_search_is_explicit(self):
        self.assertEqual(plan_goal("Abre Netflix.").steps[0].operation, "browser_open")
        self.assertEqual(plan_goal("Abre Netflix con el perfil Marian.").browser_profile, "Marian")
        site_only = plan_goal("En Netflix usa el perfil Isaías.")
        self.assertEqual(site_only.steps[0].scope, "web_page")
        search = plan_goal("Busca Isaías en Google.")
        self.assertTrue(search.explicit_search)
        self.assertEqual(search.steps[0].operation, "browser_search")

    def test_site_profile_is_blocked_after_open_without_searching_it(self):
        registry = ToolRegistry()
        registry.register("browser_open", "web", {"type": "object", "properties": {"target": {"type": "string"}}}, lambda **_kwargs: {"ok": True, "message": "opened"})
        registry.register("browser_search", "search", {"type": "object", "properties": {"query": {"type": "string"}}}, lambda **_kwargs: {"ok": True, "message": "should not run"})
        router = ScriptedRouter([
            BrainResult(True, tool_calls=[{"function": {"name": "browser_open", "arguments": {"target": "Netflix", "profile": "Marian"}}}]),
            BrainResult(True, tool_calls=[{"function": {"name": "browser_search", "arguments": {"query": "Isaías"}}}]),
            BrainResult(True, text="Listo"),
        ])
        result = MochiAgentLoop(router=router, max_steps=4).run("Abre Netflix con la cuenta de Google de Marian y luego elije el perfil de Isaías en Netflix.", registry)
        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["goal_state"]["browser_profile"], "Marian")
        self.assertEqual(result["goal_state"]["site_profile"], "Isaías")
        self.assertEqual(result["actions"][1]["result"]["error"], "invalid_scope_action")
        self.assertIn("todavía no puedo seleccionar el perfil Isaías", result["text"])

    def test_browser_profile_is_not_replaced_with_site_profile(self):
        registry = ToolRegistry()
        registry.register("browser_open", "web", {"type": "object", "properties": {}}, lambda **_kwargs: {"ok": True})
        router = ScriptedRouter([
            BrainResult(True, tool_calls=[{"function": {"name": "browser_open", "arguments": {"target": "Netflix", "profile": "Isaías"}}}]),
            BrainResult(True, text=""),
        ])
        result = MochiAgentLoop(router=router, max_steps=3).run("Abre Netflix con Marian y en Netflix como Isaías.", registry)
        self.assertEqual(result["actions"][0]["result"]["error"], "invalid_scope_argument")

    def test_sequential_os_actions_have_dependencies(self):
        state = plan_goal("Abre Chrome y luego minimízalo.")
        self.assertEqual([(step.operation, step.depends_on) for step in state.steps], [("open_application", None), ("control_window", "open_app")])
        self.assertEqual(state.steps[1].value, "minimize")

    def test_router_keeps_simple_requests_fast_and_flags_compound_ones(self):
        router = BrainRouter.__new__(BrainRouter)
        self.assertFalse(router.should_use_deep_for("Abre Netflix."))
        self.assertTrue(router.should_use_deep_for("Abre Netflix y luego elige un perfil dentro de Netflix."))

    def test_browser_profile_only_is_completed(self):
        registry = ToolRegistry()
        registry.register("browser_open", "web", {"type": "object", "properties": {}}, lambda **_kwargs: {"ok": True})
        router = ScriptedRouter([
            BrainResult(True, tool_calls=[{"function": {"name": "browser_open", "arguments": {"target": "Netflix", "profile": "Marian"}}}]),
            BrainResult(True, text=""),
        ])
        result = MochiAgentLoop(router=router).run("Abre Netflix con Marian.", registry)
        self.assertEqual(result["status"], "completed")

    def test_site_only_goal_is_blocked_without_page_capability(self):
        router = ScriptedRouter([BrainResult(True, text="No puedo hacerlo.")])
        result = MochiAgentLoop(router=router).run("En Netflix selecciona Isaías.", ToolRegistry())
        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "blocked")
        self.assertIn("todavía no está disponible", result["text"])

    def test_completed_sequential_actions_have_completed_global_status(self):
        registry = ToolRegistry()
        registry.register("open_application", "app", {"type": "object", "properties": {}}, lambda **_kwargs: {"ok": True})
        registry.register("control_window", "window", {"type": "object", "properties": {}}, lambda **_kwargs: {"ok": True})
        router = ScriptedRouter([
            BrainResult(True, tool_calls=[{"function": {"name": "open_application", "arguments": {"application": "Chrome"}}}]),
            BrainResult(True, tool_calls=[{"function": {"name": "control_window", "arguments": {"window": "Chrome", "action": "minimize"}}}]),
            BrainResult(True, text="Listo."),
        ])
        result = MochiAgentLoop(router=router).run("Abre Chrome y luego minimízalo.", registry)
        self.assertEqual(result["status"], "completed")

    def test_progress_followed_by_failure_is_partial(self):
        registry = ToolRegistry()
        registry.register("open_application", "app", {"type": "object", "properties": {}}, lambda **_kwargs: {"ok": True})
        registry.register("control_window", "window", {"type": "object", "properties": {}}, lambda **_kwargs: {"ok": False, "message": "window not found"})
        router = ScriptedRouter([
            BrainResult(True, tool_calls=[{"function": {"name": "open_application", "arguments": {"application": "Chrome"}}}]),
            BrainResult(True, tool_calls=[{"function": {"name": "control_window", "arguments": {"window": "Chrome", "action": "minimize"}}}]),
            BrainResult(True, text=""),
        ])
        result = MochiAgentLoop(router=router).run("Abre Chrome y luego minimízalo.", registry)
        self.assertEqual(result["status"], "partial")

    def test_no_completed_steps_is_not_completed(self):
        registry = ToolRegistry()
        registry.register("open_application", "app", {"type": "object", "properties": {}}, lambda **_kwargs: {"ok": False, "message": "not found"})
        router = ScriptedRouter([
            BrainResult(True, tool_calls=[{"function": {"name": "open_application", "arguments": {"application": "Chrome"}}}]),
            BrainResult(True, text=""),
        ])
        result = MochiAgentLoop(router=router).run("Abre Chrome y luego minimízalo.", registry)
        self.assertEqual(result["status"], "failed")


if __name__ == "__main__":
    unittest.main()
