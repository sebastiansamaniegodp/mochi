import unittest
from unittest.mock import patch

from brain.goal_state import plan_goal, plan_web_media
from brain.agent_loop import MochiAgentLoop
from brain.brain_provider import BrainResult
from brain.tool_registry import ToolRegistry
from brain.web_specialists import parse_specialized_web_command


class ProviderSpecialistPlanningTests(unittest.TestCase):
    """Natural, compound utterances that previously lost later clauses."""

    def test_twenty_varied_commands_keep_their_primary_contract(self):
        cases = [
            (
                "Abrime YouTube, entra al último video publicado por Kurzgesagt y después pausalo",
                "YouTube", "channel_video", {"select_channel", "select_sort", "play_media", "pause_media"},
            ),
            (
                "En YouTube buscá tutorial de Rust ownership en español, elegí el tercer video, adelantá 45 segundos y baja el volumen 5 por ciento",
                "YouTube", "site_content_sequence", {"search_page", "select_visible_video", "adjust_media_position", "adjust_volume"},
            ),
            (
                "Mostrame cuatro videos sobre eclipse solar en YouTube y no reproduzcas ninguno",
                "YouTube", "search_in_page", {"search_page", "collect_results"},
            ),
            (
                "Entra al canal de Veritasium en YouTube sin abrir ningún video",
                "YouTube", "open_channel", {"select_channel"},
            ),
            (
                "Abre en YouTube el video más antiguo de QuantumFracture y reproducelo",
                "YouTube", "channel_video", {"select_sort", "select_visible_video", "play_media"},
            ),
            (
                "Cambia el video actual de YouTube por cómo funciona un compilador y empieza desde el inicio",
                "YouTube", "site_content_sequence", {"search_page", "select_visible_video", "seek_media"},
            ),
            (
                "Pon Docker compose tutorial for beginners en YouTube",
                "YouTube", "search_and_open_result", {"search_page", "select_result", "play_media"},
            ),
            (
                "En YouTube reproduce el primer video recomendado y luego pausalo",
                "YouTube", "select_visible_video", {"select_visible_video", "play_media", "pause_media"},
            ),
            (
                "En YouTube que ya está abierto, busca historia de Linux y reproduce el segundo video en pantalla",
                "YouTube", "site_content_sequence", {"search_page", "select_visible_video", "play_media"},
            ),
            (
                "Busca el último video que subió Nate Gentile a YouTube, adelanta dos minutos y pausa",
                "YouTube", "channel_video", {"select_channel", "adjust_media_position", "pause_media"},
            ),
            (
                "Abre Netflix con la cuenta de Google de Marian, entra al perfil de Isaías, busca Dark, temporada dos, episodio tres, desde el inicio y pausalo",
                "Netflix", "site_content_sequence", {"select_site_profile", "select_season", "select_episode", "seek_media", "pause_media"},
            ),
            (
                "En Netflix cambia del perfil de Isaías al de Marian y busca The Crown, pon el segundo episodio de la tercera temporada",
                "Netflix", "site_content_sequence", {"select_site_profile", "search_page", "select_season", "select_episode"},
            ),
            (
                "En Netflix que ya está abierto busca Stranger Things y reproduce el episodio cuatro",
                "Netflix", "site_content_sequence", {"search_page", "select_episode", "play_media"},
            ),
            (
                "En Netflix retrocede treinta segundos, pasa al episodio siguiente y pausalo",
                "Netflix", "site_player_control", {"adjust_media_position", "next_media", "pause_media"},
            ),
            (
                "Mostrame tres resultados de documentales de naturaleza en Netflix sin reproducir",
                "Netflix", "search_in_page", {"search_page", "collect_results"},
            ),
            (
                "Abre Netflix con Isaias, busca el avance de Formula 1 y pon el minuto diez",
                "Netflix", "site_content_sequence", {"search_page", "play_media", "seek_media"},
            ),
            (
                "En Netflix busca Lupin temporada uno episodio dos, reproducelo y activa subtitulos",
                "Netflix", "site_content_sequence", {"select_season", "select_episode", "play_media", "subtitles"},
            ),
            (
                "Netflix busca Black Mirror, quinta temporada, primer capitulo y ponelo desde el comienzo",
                "Netflix", "site_content_sequence", {"select_season", "select_episode", "seek_media"},
            ),
            (
                "En Netflix selecciona el perfil Niños y busca Pokémon",
                "Netflix", "search_in_page", {"select_site_profile", "search_page"},
            ),
            (
                "En Netflix pausa, reanuda y vuelve a pausar",
                "Netflix", "site_player_control", {"pause_media", "play_media"},
            ),
        ]
        self.assertEqual(len(cases), 20)
        for command, target, goal, required in cases:
            with self.subTest(command=command):
                state = plan_goal(command)
                self.assertEqual((state.target, state.goal), (target, goal))
                operations = {step.operation for step in state.steps}
                self.assertTrue(required.issubset(operations), (required, operations))

    def test_word_numbers_and_every_media_clause_preserve_order(self):
        state = plan_goal("En Netflix pausa, reanuda, avanza dos minutos y vuelve a pausar")
        media = [
            (step.id, step.operation, step.value, step.depends_on)
            for step in state.steps
        ]
        self.assertEqual(media, [
            ("pause_media", "pause_media", "Pausa", None),
            ("play_media", "play_media", "Reproducir", "pause_media"),
            ("adjust_media_position", "adjust_media_position", "120", "play_media"),
            ("pause_media_2", "pause_media", "Pausa", "adjust_media_position"),
        ])

    def test_provider_context_scopes_a_followup_but_does_not_swallow_local_work(self):
        followup = plan_goal("adelanta cinco minutos y pausalo", context_provider="YouTube")
        self.assertEqual((followup.target, followup.goal), ("YouTube", "site_player_control"))
        self.assertIsNone(followup.step("browser_open"))
        self.assertEqual(
            [step.operation for step in followup.steps],
            ["adjust_media_position", "pause_media"],
        )

        local = plan_goal("abre la calculadora", context_provider="YouTube")
        self.assertNotEqual(local.target, "YouTube")
        self.assertNotEqual(local.goal, "site_player_control")

        local_file = plan_goal(
            "busca un video en mis archivos llamado cumpleaños",
            context_provider="YouTube",
        )
        self.assertEqual(local_file.goal, "local_file_search")

    def test_explicit_youtube_video_search_is_not_mistaken_for_a_local_file(self):
        state = plan_goal(
            "Busca el video mas reciente del canal Dot CSV en YouTube, "
            "reproducelo y despues pausalo"
        )

        self.assertEqual(state.target, "YouTube")
        self.assertEqual(state.channel_name, "dot csv")
        self.assertEqual(state.result_order, "newest")
        self.assertIsNone(state.step("find_files"))
        self.assertEqual(
            [step.operation for step in state.steps[-2:]],
            ["play_media", "pause_media"],
        )

        oldest = plan_goal(
            "Busca el video mas antiguo del canal QuantumFracture en YouTube y reproducelo"
        )
        self.assertEqual(oldest.channel_name, "quantumfracture")
        self.assertEqual(oldest.result_order, "oldest")

    def test_voseo_and_mochi_prefix_still_play_the_named_media(self):
        for command in (
            "Mochi, poné Bohemian Rhapsody en YouTube",
            "reproducí Bohemian Rhapsody en YouTube",
            "Mochi ¿me podés poner Bohemian Rhapsody en YouTube?",
            "poneme Bohemian Rhapsody en YouTube porfa",
        ):
            with self.subTest(command=command):
                state = plan_goal(command)
                self.assertEqual(state.page_query, "Bohemian Rhapsody")
                self.assertEqual(state.page_result, "Bohemian Rhapsody")
                self.assertEqual(
                    [step.operation for step in state.steps],
                    ["browser_open", "search_page", "select_result", "play_media"],
                )

    def test_exact_channel_handle_beats_localized_prefix_and_duplicate_links(self):
        observation = {"elements": [
            {"id": "localized", "role": "link", "name": "Veritasium en español Verificada @VeritasiumES 2 M suscriptores"},
            {"id": "main", "role": "link", "name": "Veritasium Verificada @veritasium 21 M suscriptores"},
            {"id": "nav", "role": "link", "name": "Ir al canal Veritasium"},
            {"id": "label", "role": "link", "name": "Veritasium"},
        ]}

        selected = MochiAgentLoop()._named_channel_result(observation, "Veritasium")
        self.assertEqual(selected["id"], "main")

    def test_youtube_and_netflix_vocabulary_remain_isolated(self):
        youtube = parse_specialized_web_command(
            "En YouTube abre el primer video recomendado"
        )
        netflix = parse_specialized_web_command(
            "En Netflix pon el primer episodio de la segunda temporada"
        )
        self.assertEqual((youtube.result_scope, youtube.result_index), ("recommended", 1))
        self.assertIsNone(youtube.episode)
        self.assertEqual((netflix.season, netflix.episode), (2, 1))
        self.assertIsNone(netflix.result_scope)

    def test_channel_list_does_not_open_a_video(self):
        state = plan_goal(
            "Dime los últimos tres videos de Dot CSV en YouTube sin reproducirlos"
        )
        self.assertEqual((state.channel_name, state.result_order), ("dot csv", "newest"))
        self.assertIsNotNone(state.step("collect_results"))
        self.assertIsNone(state.step("select_visible_video"))
        self.assertIsNone(state.step("play_media"))

    def test_confirmed_volume_change_advances_the_following_media_step(self):
        state = plan_goal(
            "En YouTube baja el volumen cinco por ciento y pausalo"
        )
        registry = ToolRegistry()
        registry.register(
            "control_media", "media", {"type": "object", "properties": {}},
            lambda **_kwargs: {
                "ok": True,
                "state_confirmed": True,
                "message": "Volumen al 17%",
            },
        )
        records = MochiAgentLoop()._fast_adjust_volume(registry, state)
        self.assertTrue(records)
        self.assertEqual(state.step("adjust_volume").status, "completed")
        self.assertEqual(
            MochiAgentLoop._next_named_page_operation(state),
            "pause_media",
        )

    def test_repeated_operation_lookup_consumes_each_occurrence(self):
        state = plan_goal("En Netflix pausa, reanuda y vuelve a pausar")
        self.assertEqual(MochiAgentLoop._next_named_page_step(state).id, "pause_media")
        state.mark_completed("pause_media")
        self.assertEqual(MochiAgentLoop._next_named_page_step(state).id, "play_media")
        state.mark_completed("play_media")
        self.assertEqual(MochiAgentLoop._next_named_page_step(state).id, "pause_media_2")
        state.mark_completed("pause_media")
        self.assertEqual(state.status, "completed")

    def test_cast_and_autoplay_buttons_are_not_transport_play_controls(self):
        self.assertTrue(MochiAgentLoop._is_play_media_control("Reproducir (k)"))
        self.assertFalse(MochiAgentLoop._is_play_media_control("Reproducir en el televisor"))
        self.assertFalse(MochiAgentLoop._is_play_media_control("Reproducción automática activada"))

    def test_spoken_player_clock_wins_over_a_stale_range_value(self):
        earlier = {"elements": [
            {"id": "clock", "role": "button", "name": "0 Minutos 2 Segundos de 17 Minutos 41 Segundos", "offscreen": False},
            {"id": "seek", "role": "slider", "name": "Control deslizante", "range": {"minimum": 0, "maximum": 1061, "value": 2}, "offscreen": False},
        ]}
        later = {"elements": [
            {"id": "clock", "role": "button", "name": "0 Minutos 5 Segundos de 17 Minutos 41 Segundos", "offscreen": False},
            {"id": "seek", "role": "slider", "name": "Control deslizante", "range": {"minimum": 0, "maximum": 1061, "value": 2}, "offscreen": False},
        ]}

        self.assertTrue(MochiAgentLoop._media_timeline_progressed(earlier, later))
        self.assertFalse(MochiAgentLoop._media_timeline_stable(earlier, later))

    def test_player_page_duration_links_are_recommendations_without_section_metadata(self):
        observation = {"elements": [
            {"id": "toggle", "role": "button", "name": "Botón de reproducción (k)", "offscreen": False},
            {"id": "channel", "role": "link", "name": "QuantumFracture", "offscreen": False},
            {"id": "one", "role": "link", "name": "Una recomendación 20 minutos", "offscreen": False},
            {"id": "two", "role": "link", "name": "Otra recomendación 8 minutos", "offscreen": False},
        ]}

        cards = MochiAgentLoop()._visible_video_cards(observation, "recommended")
        self.assertEqual([item["id"] for item in cards], ["one", "two"])

        state = plan_goal("Abre el primer video recomendado y pausalo")
        self.assertIsNone(state.target)
        self.assertIsNotNone(state.step("pause_media"))

    def test_autocomplete_suggestion_is_not_a_video_card(self):
        # Lo que publica Chromium en Linux con el desplegable abierto.
        observation = {"elements": [
            {"id": "s1", "role": "button", "name": "Linkin Park", "offscreen": False, "container": "linkin park"},
            {"id": "s2", "role": "button", "name": "linkin park numb", "offscreen": False, "container": "linkin park numb"},
            {"id": "v1", "role": "link", "name": "Naruto - Ending 1 | Wind 1 minute, 41 seconds", "offscreen": False},
            {"id": "box", "role": "combobox", "name": "Search", "value": "Linkin Park", "offscreen": False},
        ]}

        cards = MochiAgentLoop()._visible_video_cards(observation, "visible")
        self.assertEqual([item["id"] for item in cards], ["v1"])

    def _play_any_result_state(self):
        state = plan_goal("busca linkin park en youtube y reproducelo")
        self.assertEqual(state.goal, "search_and_play_any_result")
        state.mark_completed("browser_open")
        state.mark_completed("search_page")
        return state

    def test_first_video_below_the_channel_card_is_scrolled_into_view(self):
        channel_only = {"ok": True, "snapshot_id": "s1", "query_matched": True, "page": {"title": "linkin park - YouTube"}, "elements": [
            {"id": "e1", "role": "link", "name": "Linkin Park @LinkinPark • 25.4M subscribers 1.2K videos", "offscreen": False},
            {"id": "e2", "role": "link", "name": "Mix", "offscreen": False},
            {"id": "e3", "role": "combobox", "name": "Search", "value": "linkin park", "offscreen": False},
        ]}
        hidden = {"ok": True, "snapshot_id": "s2", "query_matched": True, "page": {"title": "linkin park - YouTube"}, "elements": [
            {"id": "e1", "role": "link", "name": "Numb [Official Music Video] - Linkin Park 3 minutes, 8 seconds", "offscreen": True},
            {"id": "e2", "role": "link", "name": "In The End - Linkin Park 3 minutes, 39 seconds", "offscreen": True},
        ]}
        scrolled = {"ok": True, "snapshot_id": "s3", "query_matched": True, "page": {"title": "linkin park - YouTube"}, "elements": [
            {"id": "e7", "role": "link", "name": "Numb [Official Music Video] - Linkin Park 3 minutes, 8 seconds", "offscreen": False},
        ]}
        after_click = {"ok": True, "snapshot_id": "s4", "page": {"title": "Numb - YouTube"}, "elements": []}
        inspected = []

        def inspect(**kwargs):
            inspected.append(kwargs.get("query"))
            if kwargs.get("query"):
                return hidden if len([q for q in inspected if q]) == 1 else scrolled
            return after_click if len(inspected) > 8 else channel_only

        acted = []
        registry = ToolRegistry()
        registry.register("web_inspect", "inspect", {"type": "object", "properties": {"query": {"type": "string"}}}, inspect)
        registry.register("web_act", "act", {"type": "object", "properties": {}}, lambda **kwargs: acted.append(kwargs) or {"ok": True})

        with patch("brain.agent_loop.time.sleep"):
            MochiAgentLoop()._fast_visible_video_selection(registry, self._play_any_result_state())

        self.assertEqual(acted, [
            {"element_id": "e1", "action": "scroll_into_view", "snapshot_id": "s2"},
            {"element_id": "e7", "action": "click", "snapshot_id": "s3"},
        ])

    def test_visible_first_video_is_clicked_without_searching_below(self):
        results = {"ok": True, "snapshot_id": "s1", "page": {"title": "linkin park - YouTube"}, "elements": [
            {"id": "e1", "role": "link", "name": "Numb [Official Music Video] - Linkin Park 3 minutes, 8 seconds", "offscreen": False},
            {"id": "e2", "role": "combobox", "name": "Search", "value": "linkin park", "offscreen": False},
        ]}
        inspected, acted = [], []
        registry = ToolRegistry()
        registry.register("web_inspect", "inspect", {"type": "object", "properties": {"query": {"type": "string"}}}, lambda **kwargs: inspected.append(kwargs) or results)
        registry.register("web_act", "act", {"type": "object", "properties": {}}, lambda **kwargs: acted.append(kwargs) or {"ok": True})

        with patch("brain.agent_loop.time.sleep"):
            MochiAgentLoop()._fast_visible_video_selection(registry, self._play_any_result_state())

        self.assertEqual(acted, [{"element_id": "e1", "action": "click", "snapshot_id": "s1"}])
        self.assertFalse(any(kwargs.get("query") for kwargs in inspected))

    def test_selected_search_filter_is_not_mistaken_for_a_channel_page(self):
        observation = {
            "ok": True,
            "page": {"title": "Dot CSV - YouTube"},
            "elements": [
                {"role": "tab", "name": "Todo", "selected": True},
                {"role": "tab", "name": "Videos", "selected": False},
                {"role": "link", "name": "Playlists"},
            ],
        }
        self.assertFalse(
            MochiAgentLoop()._observation_is_requested_channel(
                observation, "Dot CSV"
            )
        )

    def test_option_word_without_pending_list_cannot_become_visible_video(self):
        loop = MochiAgentLoop()
        state = plan_goal("Elige la primera opción", context_provider="YouTube")
        self.assertEqual(state.goal, "select_visible_video")
        self.assertEqual(loop._requested_option_index("Elige la primera opción"), 1)
        # The run-level guard combines these two facts and returns
        # missing_pending_page_choice; a bare visible-video command uses the
        # word video/recommended instead of option.


class PlayWebMediaTests(unittest.TestCase):
    """El LLM interpreta los pedidos abiertos; el plan verificado los ejecuta."""

    def test_open_ended_requests_are_left_to_the_llm(self):
        for command in (
            "mochi podes poner una cancion en youtube",
            "pone una cancion",
            "poné música de Soda Stereo",
            "poneme algo en YouTube",
            "quiero escuchar Queen en YouTube",
        ):
            with self.subTest(command=command):
                state = plan_goal(command)
                self.assertEqual(state.goal, "user_request")
                self.assertEqual(state.steps, [])

    def test_concrete_requests_and_controls_keep_their_plans(self):
        self.assertEqual(plan_goal("poné el volumen al 50").goal, "local_direct_action")
        self.assertEqual(plan_goal("abrí youtube").goal, "open_destination")
        self.assertEqual(plan_goal("Mochi, poné Bohemian Rhapsody en YouTube").goal, "search_and_open_result")

    def test_plan_with_and_without_title(self):
        named = plan_web_media("YouTube", "Bohemian Rhapsody Queen", "Bohemian Rhapsody")
        self.assertEqual((named.goal, named.page_query, named.page_result), ("search_and_open_result", "Bohemian Rhapsody Queen", "Bohemian Rhapsody"))
        anything = plan_web_media(None, "música rock")
        self.assertEqual((anything.goal, anything.target), ("search_and_play_any_result", "YouTube"))
        self.assertEqual(
            [step.operation for step in anything.steps],
            ["browser_open", "search_page", "select_visible_video", "play_media"],
        )
        self.assertIsNone(plan_web_media("YouTube", "  "))

    def test_llm_call_runs_the_verified_plan_without_more_turns(self):
        registry = ToolRegistry()
        for name in ("play_web_media", "web_inspect", "web_act"):
            registry.register(name, name, {"type": "object", "properties": {}}, lambda **_kwargs: {"ok": False})

        class Router:
            last_mode = "fast"
            calls = 0

            def generate(self, **_kwargs):
                Router.calls += 1
                return BrainResult(True, tool_calls=[{"function": {"name": "play_web_media", "arguments": {"query": "musica", "site": "YouTube"}}}])

            def should_use_deep_for(self, _command):
                return False

        planned = []

        def microplan(_self, _registry, goal_state):
            planned.append(goal_state)
            for step in goal_state.steps:
                goal_state.mark_completed(step.operation)
            return [("browser_open", {"target": "YouTube"}, {"ok": True}, None)]

        with patch.object(MochiAgentLoop, "_run_structured_web_microplan", microplan):
            result = MochiAgentLoop(router=Router(), max_steps=4).run("poné una canción", registry)

        self.assertEqual(Router.calls, 1)
        self.assertEqual(result["status"], "completed")
        self.assertEqual((planned[0].goal, planned[0].page_query), ("search_and_play_any_result", "musica"))
        self.assertEqual([item["tool"] for item in result["actions"]], ["browser_open"])


if __name__ == "__main__":
    unittest.main()
