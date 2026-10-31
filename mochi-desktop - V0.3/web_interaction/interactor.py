"""Acciones UIA semánticas. No usa coordenadas."""
import time

from web_interaction.inspector import WebInspector


class WebInteractor:
    def __init__(self, inspector=None):
        self.inspector = inspector or WebInspector()

    # GetCurrentPattern is specified to return IUnknown.  comtypes therefore
    # does not automatically expose Invoke/Select/etc. on its result: callers
    # must ask COM for the generated interface for the requested pattern.
    _PATTERNS = {
        "invoke": ("UIA_InvokePatternId", "IUIAutomationInvokePattern"),
        "selection_item": ("UIA_SelectionItemPatternId", "IUIAutomationSelectionItemPattern"),
        "toggle": ("UIA_TogglePatternId", "IUIAutomationTogglePattern"),
        "value": ("UIA_ValuePatternId", "IUIAutomationValuePattern"),
        "range_value": ("UIA_RangeValuePatternId", "IUIAutomationRangeValuePattern"),
        "expand_collapse": ("UIA_ExpandCollapsePatternId", "IUIAutomationExpandCollapsePattern"),
        "scroll_item": ("UIA_ScrollItemPatternId", "IUIAutomationScrollItemPattern"),
        # Chromium can expose menu buttons through UIA's compatibility
        # pattern instead of InvokePattern.  It is still a semantic COM/UIA
        # operation: DoDefaultAction asks the provider to perform exactly the
        # default accessible action advertised for that element.
        "legacy_accessible": ("UIA_LegacyIAccessiblePatternId", "IUIAutomationLegacyIAccessiblePattern"),
    }

    def _pattern(self, element, pattern_name):
        """Return a typed UIA pattern, or None when it is unavailable.

        Keeping this conversion in one place makes the action code independent
        of comtypes' IUnknown return value and works with every generated UIA
        interface, not just InvokePattern.
        """
        pattern_id_name, interface_name = self._PATTERNS[pattern_name]
        try:
            raw_pattern = element.GetCurrentPattern(getattr(self.inspector._api, pattern_id_name))
            if raw_pattern is None:
                return None
            interface = getattr(self.inspector._api, interface_name)
            return raw_pattern.QueryInterface(interface)
        except Exception:
            return None

    def _activate(self, element):
        """Perform the provider's semantic activation without coordinates.

        UIA providers are not required to expose every clickable control as
        InvokePattern.  In particular, accessible menu buttons commonly use
        ExpandCollapsePattern, while older Chromium providers can surface
        only LegacyIAccessiblePattern.  These fallbacks are attempted only
        when a prior pattern is absent; an exception from an invoked pattern
        remains a failure so we never double-activate a possibly changed UI.
        """
        pattern = self._pattern(element, "invoke")
        if pattern is not None:
            pattern.Invoke()
            return "invoke"

        pattern = self._pattern(element, "selection_item")
        if pattern is not None:
            pattern.Select()
            return "selection_item"

        pattern = self._pattern(element, "expand_collapse")
        if pattern is not None:
            state = getattr(pattern, "CurrentExpandCollapseState", None)
            expanded = getattr(self.inspector._api, "ExpandCollapseState_Expanded", 1)
            leaf_node = getattr(self.inspector._api, "ExpandCollapseState_LeafNode", 3)
            # A leaf advertises the pattern but cannot be expanded.  Let its
            # legacy default action (if any) decide the semantic activation.
            if state != leaf_node:
                if state == expanded:
                    pattern.Collapse()
                else:
                    pattern.Expand()
                return "expand_collapse"

        pattern = self._pattern(element, "legacy_accessible")
        if pattern is not None:
            pattern.DoDefaultAction()
            return "legacy_accessible"

        return None

    @staticmethod
    def _risk(action, name):
        text = str(name).casefold()
        if any(word in text for word in ("enviar", "publicar", "comprar", "pagar", "eliminar", "borrar", "confirmar")):
            return "commit"
        return "input" if action == "type" else "selection" if action in {"select", "toggle"} else "navigation"

    @staticmethod
    def _submit_focused_element(element):
        """Submit the currently focused web control with the standard Enter key.

        UI Automation has no generic "submit form" pattern.  Focusing the
        inspected element and sending Enter is the accessible keyboard
        equivalent of activating the form, without screen coordinates or a
        browser-specific protocol.
        """
        import ctypes

        element.SetFocus()
        key_up = 0x0002
        virtual_key_enter = 0x0D
        ctypes.windll.user32.keybd_event(virtual_key_enter, 0, 0, 0)
        ctypes.windll.user32.keybd_event(virtual_key_enter, 0, key_up, 0)

    @staticmethod
    def _press_virtual_key(virtual_key, count=1):
        """Send a bounded keyboard action to the focused UIA control.

        This is a semantic fallback for providers which publish a writable
        RangeValuePattern but silently ignore SetValue (Chromium media
        sliders do this in some builds).  The element is focused through UIA;
        no coordinates or provider-specific browser shortcuts are used.
        """
        import ctypes

        key_up = 0x0002
        for _index in range(max(0, min(int(count), 2000))):
            ctypes.windll.user32.keybd_event(virtual_key, 0, 0, 0)
            ctypes.windll.user32.keybd_event(virtual_key, 0, key_up, 0)

    def act(self, element_id, action, value=None, snapshot_id=None):
        action = str(action or "").strip().lower()
        snapshot, item, error = self.inspector.store.resolve(element_id, snapshot_id)
        if error:
            return {"ok": False, "error": error, "stale_element": error == "stale_element", "message": "El elemento ya no es válido; inspeccioná la página nuevamente." if error == "stale_element" else "No encontré el elemento solicitado."}
        element = item["element"]
        activation_pattern = None
        range_strategy = None
        observed_range_value = None
        try:
            current_id = tuple(element.GetRuntimeId() or [])
            if current_id != item["runtime_id"]:
                self.inspector.store.mark_stale(snapshot.id)
                return {"ok": False, "error": "stale_element", "stale_element": True, "message": "El elemento cambió; inspeccioná nuevamente."}
            if action == "focus":
                element.SetFocus()
            elif action in {"click", "invoke"}:
                activation_pattern = self._activate(element)
                if activation_pattern is None:
                    return {"ok": False, "error": "pattern_unsupported", "message": "Ese control no admite una acción invocable segura."}
            elif action == "keyboard_activate":
                if item["role"] not in {"button", "link", "menuitem"}:
                    return {"ok": False, "error": "invalid_keyboard_target", "message": "La activación por teclado solo admite controles accionables."}
                element.SetFocus()
                virtual_key = 0x20 if item["role"] == "button" else 0x0D
                self._press_virtual_key(virtual_key)
                activation_pattern = "keyboard_space" if virtual_key == 0x20 else "keyboard_enter"
            elif action == "legacy_activate":
                if item["role"] not in {"button", "link", "menuitem"}:
                    return {"ok": False, "error": "invalid_legacy_target", "message": "La acción predeterminada solo admite controles accionables."}
                pattern = self._pattern(element, "legacy_accessible")
                if pattern is None:
                    return {"ok": False, "error": "pattern_unsupported", "message": "Ese control no publica una acción predeterminada accesible."}
                pattern.DoDefaultAction()
                activation_pattern = "legacy_accessible"
            elif action == "select":
                pattern = self._pattern(element, "selection_item")
                if pattern is None: return {"ok": False, "error": "pattern_unsupported", "message": "Ese control no admite selección."}
                pattern.Select()
            elif action == "toggle":
                pattern = self._pattern(element, "toggle")
                if pattern is None: return {"ok": False, "error": "pattern_unsupported", "message": "Ese control no admite alternar."}
                pattern.Toggle()
            elif action == "type":
                if value is None: return {"ok": False, "error": "value_required", "message": "Falta el texto a escribir."}
                pattern = self._pattern(element, "value")
                if pattern is not None:
                    pattern.SetValue(str(value))
                else:
                    pattern = self._pattern(element, "legacy_accessible")
                    if pattern is None: return {"ok": False, "error": "pattern_unsupported", "message": "Ese campo no permite escritura accesible."}
                    pattern.SetValue(str(value))
            elif action == "set_range":
                if value is None:
                    return {"ok": False, "error": "value_required", "message": "Falta el valor numérico del control deslizante."}
                try:
                    target_value = float(value)
                except (TypeError, ValueError):
                    return {"ok": False, "error": "invalid_range_value", "message": "El valor del control deslizante debe ser numérico."}
                pattern = self._pattern(element, "range_value")
                if pattern is None:
                    return {"ok": False, "error": "pattern_unsupported", "message": "Ese control no permite ajuste accesible por rango."}
                minimum = float(pattern.CurrentMinimum)
                maximum = float(pattern.CurrentMaximum)
                if not minimum <= target_value <= maximum:
                    return {"ok": False, "error": "range_value_out_of_bounds", "message": f"El valor debe estar entre {minimum:g} y {maximum:g}."}
                try:
                    before_value = float(pattern.CurrentValue)
                except (AttributeError, TypeError, ValueError):
                    before_value = None
                pattern.SetValue(target_value)
                range_strategy = "range_value"

                # A successful COM call does not guarantee that the provider
                # accepted the value.  If it remains unchanged, calibrate one
                # Arrow key against the focused range and use that observed
                # step to reach the closest representable value.
                if before_value is not None and abs(target_value - before_value) > 0.05:
                    time.sleep(0.06)
                    after_set = float(pattern.CurrentValue)
                    observed_range_value = after_set
                    if abs(after_set - before_value) <= 0.05:
                        element.SetFocus()
                        virtual_key = 0x27 if target_value > after_set else 0x25
                        self._press_virtual_key(virtual_key)
                        time.sleep(0.08)
                        calibrated_value = float(pattern.CurrentValue)
                        calibrated_step = calibrated_value - after_set
                        requested_direction = 1 if target_value > after_set else -1
                        if calibrated_step * requested_direction <= 0.05:
                            return {
                                "ok": False,
                                "error": "range_value_not_applied",
                                "message": "El proveedor no aplicó el valor ni respondió al ajuste accesible del control.",
                            }

                        remaining = target_value - calibrated_value
                        additional_presses = int(round(abs(remaining) / abs(calibrated_step)))
                        if additional_presses:
                            remaining_key = 0x27 if remaining > 0 else 0x25
                            self._press_virtual_key(remaining_key, additional_presses)
                            time.sleep(0.08)
                        observed_range_value = float(pattern.CurrentValue)
                        range_strategy = "focused_range_keyboard"
            elif action == "submit":
                if item["role"] not in {"textbox", "combobox"}:
                    return {"ok": False, "error": "invalid_submit_target", "message": "Submit solo puede ejecutarse sobre un campo de texto o cuadro combinado."}
                self._submit_focused_element(element)
            elif action == "scroll_into_view":
                pattern = self._pattern(element, "scroll_item")
                if pattern is None:
                    return {"ok": False, "error": "pattern_unsupported", "message": "Ese resultado no admite desplazamiento accesible."}
                pattern.ScrollIntoView()
            else:
                return {"ok": False, "error": "invalid_action", "message": "Acción web no válida."}
        except Exception as error:
            # A failed pattern call (including a failed COM cast) does not
            # prove that the UI changed.  Preserve the snapshot so the agent
            # may try another supported action.  We mark stale above only
            # after a runtime-id mismatch, which is concrete evidence that the
            # stored reference is no longer the inspected element.
            return {"ok": False, "error": "uia_action_failed", "message": str(error)}
        if action in {"click", "invoke", "keyboard_activate", "legacy_activate", "select", "toggle", "type", "set_range", "submit", "scroll_into_view"}:
            self.inspector.store.mark_stale(snapshot.id)
        result = {"ok": True, "message": "Acción web realizada; inspeccioná nuevamente para verificar el resultado.", "action": action, "element_name": item["name"], "element_role": item["role"], "risk": self._risk(action, item["name"]), "requires_observation": action != "focus"}
        if activation_pattern:
            result["activation_pattern"] = activation_pattern
        if range_strategy:
            result["range_strategy"] = range_strategy
        if observed_range_value is not None:
            result["observed_range_value"] = observed_range_value
        return result
