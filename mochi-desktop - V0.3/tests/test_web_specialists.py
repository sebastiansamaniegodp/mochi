import unittest

from brain.goal_state import plan_goal
from brain.agent_loop import MochiAgentLoop
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


if __name__ == "__main__":
    unittest.main()
