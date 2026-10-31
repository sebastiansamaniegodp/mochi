import json
import os
import time
from difflib import SequenceMatcher
from pathlib import Path

from discovery.games import (
    discover_games,
    is_playable_game,
    normalize_game_name
)
from linux_platform import system_open
from mochi_environment import environment


DEFAULT_GAME_CACHE_TTL_SECONDS = 600
GAME_CACHE_VERSION = 2


class GameRegistry:

    def __init__(
        self,
        cache_path=None,
        ttl_seconds=DEFAULT_GAME_CACHE_TTL_SECONDS,
        clock=time.time,
        discoverer=None,
    ):

        self.games = []
        self._loaded = False
        self._cache_path = (
            Path(cache_path)
            if cache_path is not None
            else environment.cache_dir / "games.json"
        )
        self._ttl_seconds = max(60, int(ttl_seconds))
        self._clock = clock
        self._discoverer = discoverer or discover_games
        self._cache_needs_rewrite = False


    def _ensure_loaded(self):
        """Discover game launchers only when a game tool actually needs it."""

        if not self._loaded:
            cached_games = self._read_cache()
            if cached_games is not None:
                self.games = cached_games
                self._loaded = True
                if self._cache_needs_rewrite:
                    self._write_cache()
            else:
                self.refresh()


    def _read_cache(self):
        try:
            with self._cache_path.open("r", encoding="utf-8") as file:
                record = json.load(file)
        except (OSError, ValueError, TypeError):
            return None

        if (
            not isinstance(record, dict)
            or record.get("version") not in {1, GAME_CACHE_VERSION}
            or float(record.get("expires_at", 0)) <= self._clock()
            or not isinstance(record.get("games"), list)
        ):
            return None

        games = self._canonicalize_games(record["games"])
        self._cache_needs_rewrite = (
            record.get("version") != GAME_CACHE_VERSION
            or games != record["games"]
        )
        return games


    @staticmethod
    def _canonicalize_games(games):
        """Keep persisted discovery data compatible with current name rules."""

        canonical_games = []
        for game in games:
            if not isinstance(game, dict) or not is_playable_game(game):
                continue
            normalized_name = normalize_game_name(game.get("name"))
            if not normalized_name:
                continue
            canonical_games.append({
                **game,
                "normalized_name": normalized_name,
            })
        return canonical_games


    def _write_cache(self):
        record = {
            "version": GAME_CACHE_VERSION,
            "expires_at": self._clock() + self._ttl_seconds,
            # Only installation metadata and launcher URIs are cached; no
            # account, browser, game-progress or other user content exists.
            "games": self.games,
        }
        try:
            self._cache_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self._cache_path.with_suffix(".tmp")
            with temporary.open("w", encoding="utf-8") as file:
                json.dump(record, file, ensure_ascii=False, separators=(",", ":"))
            temporary.replace(self._cache_path)
        except OSError:
            # A cache miss must never prevent game discovery or launch.
            pass


    def refresh(self):

        self.games = self._canonicalize_games(self._discoverer())
        self._write_cache()
        self._loaded = True


    # =====================================================
    # BUSCAR
    # =====================================================

    def find(self, query):

        self._ensure_loaded()

        query = normalize_game_name(
            query
        )

        if not query:
            return None


        # Exacta
        for game in self.games:

            if (
                game["normalized_name"]
                == query
            ):
                return game


        # Contenida
        matches = []

        for game in self.games:

            if query in game[
                "normalized_name"
            ]:

                matches.append(game)


        if len(matches) == 1:
            return matches[0]


        # Aproximada. Una coincidencia cercana debe destacar claramente sobre
        # las demás para que un pedido informal no abra el juego equivocado.
        ranked = sorted(
            (
                (
                    SequenceMatcher(
                        None,
                        query,
                        game["normalized_name"]
                    ).ratio(),
                    game,
                )
                for game in self.games
            ),
            key=lambda item: item[0],
            reverse=True,
        )
        best_score, best = ranked[0] if ranked else (0.0, None)
        runner_up_score = ranked[1][0] if len(ranked) > 1 else 0.0


        if (
            best is not None
            and best_score >= 0.82
            and (len(ranked) == 1 or best_score - runner_up_score >= 0.08)
        ):
            return best


        return None


    # =====================================================
    # EJECUTAR
    # =====================================================

    def launch(self, query):

        game = self.find(query)

        if not game:

            return (
                False,
                f"No encontré el juego {query}"
            )


        print("")
        print(
            "======================================"
        )

        print(
            f"JUEGO SOLICITADO: {query}"
        )

        print(
            f"ENCONTRADO: {game['name']}"
        )

        print(
            f"LAUNCHER: {game['launcher']}"
        )

        print(
            "======================================"
        )


        uri = game.get(
            "launch_uri"
        )


        if not uri:

            return (
                False,
                "Encontré el juego pero no sé cómo iniciarlo"
            )


        try:

            system_open(uri)

            return (
                True,
                f"Orden de inicio enviada a {game['name']}"
            )

        except Exception as e:

            print(
                "Error iniciando juego:",
                repr(e)
            )

            return (
                False,
                f"No pude abrir {game['name']}"
            )


    def list_names(self):

        self._ensure_loaded()

        return sorted(
            game["name"]
            for game in self.games
        )


game_registry = GameRegistry()
