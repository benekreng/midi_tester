import random
import dearpygui.dearpygui as dpg

from message_types import (
    ALL_TYPES,
    LABELS,
    TYPE_NOTE,
    TYPE_CC,
    TYPE_CC14,
    TYPE_NRPN,
    TYPE_PC,
    TYPE_PB,
)

# --- PARAMETER CONFIG ---
PARAMS = {
    "cutoff":    {"type": "cc",   "num": 74,   "label": "Cutoff (CC 74)",     "max": 127},
    "res":       {"type": "cc",   "num": 71,   "label": "Resonance (CC 71)",  "max": 127},
    "mod_hr":    {"type": "cc14", "num": 1,    "label": "Mod Wheel (CC1/33)", "max": 16383},
    "decay":     {"type": "nrpn", "num": 1300, "label": "Decay (NRPN 1300)",  "max": 16383},
    "pb":        {"type": "pb",   "num": 0,    "label": "Pitch Bend",         "max": 16383},
    "at":        {"type": "at",   "num": 0,    "label": "Aftertouch",         "max": 127},
    "prog":      {"type": "pc",   "num": 0,    "label": "Program Change",     "max": 127},
}

class AppGui:
    def __init__(self, midi_backend, processor, endurance_monitor, fuzz_test, remote_tester, settings_store):
        self.midi = midi_backend
        self.processor = processor
        self.endurance = endurance_monitor
        self.fuzz = fuzz_test
        self.remote = remote_tester
        self.settings = settings_store
        self.log_items = []
        self._endurance_offset_labels = list(self.endurance.offset_labels)
        self._fuzz_missing_last = 0
        self._remote_log_last = 0
        self._remote_log_tail = ""
        self._remote_results_last = 0
        self._type_label_to_type = {LABELS[m]: m for m in ALL_TYPES}
        self._type_labels = [LABELS[m] for m in ALL_TYPES]
        self._applying_settings = False
        self._default_channel = "1"

    def log_midi(self, msg_str):
        dpg.add_text(msg_str, parent="log_group")
        # Keep log size manageable
        children = dpg.get_item_children("log_group", 1)
        if children and len(children) > 20:
            dpg.delete_item(children[0])

    def get_selected_channel(self):
        val = dpg.get_value("channel_combo")
        if val == "Omni": return -1
        return int(val) - 1

    def _get_remote_channel(self):
        val = dpg.get_value("remote_expected_channel")
        if val == "Omni":
            return -1
        return int(val) - 1

    # --- CALLBACKS ---
    def refresh_ports_cb(self, sender, app_data):
        if not dpg.get_value("virt_mode"):
            dpg.configure_item("input_combo", items=self.midi.get_input_ports())
            dpg.configure_item("output_combo", items=self.midi.get_output_ports())

    def virt_mode_cb(self, sender, app_data):
        is_virt = app_data
        success, msg = self.midi.toggle_virtual_mode(is_virt)
        self.log_midi(msg)
        self.settings.data["connections"]["virtual"] = bool(is_virt)
        self._save_settings()
        
        if is_virt and success:
            dpg.hide_item("hw_connections")
            dpg.show_item("virt_status")
        else:
            dpg.show_item("hw_connections")
            dpg.hide_item("virt_status")
            self.refresh_ports_cb(None, None)
            if is_virt and not success:
                dpg.set_value("virt_mode", False)

    def input_port_cb(self, sender, app_data):
        if self.midi.connect_input(app_data):
            self.settings.data["connections"]["input_port"] = app_data
            self._save_settings()

    def output_port_cb(self, sender, app_data):
        if self.midi.connect_output(app_data):
            self.settings.data["connections"]["output_port"] = app_data
            self._save_settings()

    def channel_combo_cb(self, sender, app_data):
        self.settings.data["connections"]["channel"] = app_data
        self._save_settings()

    def reset_main_cb(self, sender, app_data):
        self.settings.reset_section("connections")
        self.settings.reset_section("main")
        self.apply_settings(section="main")

    def reset_latency_cb(self, sender, app_data):
        self.settings.reset_section("latency")
        self.apply_settings(section="latency")

    def reset_fuzz_cb(self, sender, app_data):
        self.settings.reset_section("fuzz")
        self.apply_settings(section="fuzz")

    def reset_remote_cb(self, sender, app_data):
        self.settings.reset_section("remote")
        self.remote.stop_suite("reset")
        self.remote.stop_led_animation("reset")
        self.remote.clear_all_activity()
        self.apply_settings(section="remote")

    def _remote_action(self, action, label):
        ok, msg = action()
        state = "OK" if ok else "FAIL"
        self.log_midi(f"Remote {label}: {state} ({msg})")
        return ok

    def remote_expected_channel_cb(self, sender, app_data):
        self.remote.expected_channel = self._get_remote_channel()
        remote = self.settings.data.setdefault("remote", {})
        remote["expected_channel"] = str(app_data)
        self._save_settings()

    def remote_clear_activity_cb(self, sender, app_data):
        self.remote.clear_all_activity()
        self._remote_log_last = 0
        self._remote_log_tail = ""
        self._remote_results_last = 0
        self.log_midi("Remote activity cleared")

    def remote_send_enter_cb(self, sender, app_data):
        self._remote_action(self.remote.send_enter_remote, "ENTER_REMOTE")

    def remote_send_exit_cb(self, sender, app_data):
        self._remote_action(self.remote.send_exit_remote, "EXIT_REMOTE")

    def _parse_led_entries(self, text):
        entries = []
        raw = str(text or "").strip()
        if not raw:
            return entries, "empty entry list"

        for idx, chunk in enumerate(raw.split(";"), start=1):
            fields = [part.strip() for part in chunk.split(",") if part.strip() != ""]
            if len(fields) != 5:
                return [], f"entry {idx} must have 5 numbers: enc,led,r,g,b"
            try:
                enc, led, red, green, blue = [int(v, 0) for v in fields]
            except ValueError:
                return [], f"entry {idx} has invalid number"
            entries.append({
                "encoder": enc,
                "led": led,
                "r": red,
                "g": green,
                "b": blue,
            })
        return entries, "parsed"

    def remote_send_led_particular_cb(self, sender, app_data):
        entries, msg = self._parse_led_entries(dpg.get_value("remote_led_entries"))
        if not entries:
            self.log_midi(f"Remote LED: FAIL ({msg})")
            return
        self._remote_action(lambda: self.remote.send_led_particular(entries), "LED")

    def remote_send_led_demo_cb(self, sender, app_data):
        self._remote_action(self.remote.send_led_particular_demo, "LED_DEMO")

    def remote_send_led_ring_demo_cb(self, sender, app_data):
        self._remote_action(self.remote.send_led_ring_demo, "LED_RING_DEMO")

    def remote_led_anim_interval_cb(self, sender, app_data):
        remote = self.settings.data.setdefault("remote", {})
        remote["led_anim_interval_ms"] = int(app_data)
        self._save_settings()

    def remote_start_particular_anim_cb(self, sender, app_data):
        interval_ms = int(dpg.get_value("remote_led_anim_interval"))
        self._remote_action(
            lambda: self.remote.start_led_particular_animation(interval_ms),
            "LED_ANIM_PARTICULAR_START",
        )

    def remote_start_amount_anim_cb(self, sender, app_data):
        interval_ms = int(dpg.get_value("remote_led_anim_interval"))
        self._remote_action(
            lambda: self.remote.start_led_amount_animation(interval_ms),
            "LED_RING_AMOUNT_ANIM_START",
        )

    def remote_start_ring_anim_cb(self, sender, app_data):
        interval_ms = int(dpg.get_value("remote_led_anim_interval"))
        self._remote_action(
            lambda: self.remote.start_led_ring_animation(interval_ms),
            "LED_RING_COLOR_ANIM_START",
        )

    def remote_stop_anim_cb(self, sender, app_data):
        self._remote_action(lambda: self.remote.stop_led_animation("stopped by user"), "LED_ANIM_STOP")

    def remote_send_oled_labels_cb(self, sender, app_data):
        title = dpg.get_value("remote_oled_title")
        labels_text = str(dpg.get_value("remote_oled_labels") or "")
        labels = [part.strip() for part in labels_text.split(",")]
        self._remote_action(lambda: self.remote.send_oled_labels(title, labels), "OLED_LABELS")

    def remote_send_oled_labels_demo_cb(self, sender, app_data):
        self._remote_action(self.remote.send_oled_labels_demo, "OLED_LABELS_DEMO")

    def remote_send_oled_fb_cb(self, sender, app_data):
        pattern = dpg.get_value("remote_fb_pattern")
        self._remote_action(lambda: self.remote.send_oled_framebuffer(pattern), "OLED_FRAMEBUFFER")

    def remote_send_raw_hex_cb(self, sender, app_data):
        text = dpg.get_value("remote_raw_hex")
        self._remote_action(lambda: self.remote.send_raw_hex(text), "RAW_SYSEX")

    def remote_suite_start_cb(self, sender, app_data):
        self._remote_action(self.remote.start_full_suite, "SUITE_START")

    def remote_suite_stop_cb(self, sender, app_data):
        self._remote_action(lambda: self.remote.stop_suite("stopped by user"), "SUITE_STOP")

    def remote_suite_manual_pass_cb(self, sender, app_data):
        self._remote_action(lambda: self.remote.mark_manual_step(True), "SUITE_MANUAL_PASS")

    def remote_suite_manual_fail_cb(self, sender, app_data):
        self._remote_action(lambda: self.remote.mark_manual_step(False), "SUITE_MANUAL_FAIL")

    def feedback_mode_cb(self, sender, app_data):
        # app_data will be "None", "Immediate", or "Delayed" based on radio button
        mode = app_data.upper()
        self.processor.set_feedback_mode(mode)
        self.log_midi(f"Feedback Mode: {mode}")
        self.settings.data["main"]["feedback_mode"] = app_data
        self._save_settings()

    def delay_time_cb(self, sender, app_data):
        self.processor.set_delay(int(app_data))
        self.settings.data["main"]["delay_ms"] = int(app_data)
        self._save_settings()

    def knob_cb(self, sender, app_data, user_data):
        param_key = user_data
        param_def = PARAMS[param_key]
        val = int(app_data)
        
        ch = self.get_selected_channel()
        target_ch = 0 if ch == -1 else ch

        dpg.set_value(f"val_{param_key}", str(val))
        
        p_type = param_def['type']
        
        if p_type == 'cc':
            self.midi.send_cc(target_ch, param_def['num'], val)
        elif p_type == 'cc14':
            self.midi.send_cc14(target_ch, param_def['num'], val)
        elif p_type == 'nrpn':
            self.midi.send_nrpn(target_ch, param_def['num'], val)
        elif p_type == 'pb':
            self.midi.send_pitch_bend(target_ch, val)
        elif p_type == 'pc':
            self.midi.send_program_change(target_ch, val)
        elif p_type == 'at':
            self.midi.send_aftertouch(target_ch, val)
            
        self.log_midi(f"Sent {p_type.upper()} val:{val}")
    
    def send_test_notes_cb(self, sender, app_data):
        ch = self.get_selected_channel()
        target_ch = 0 if ch == -1 else ch
        
        # Note 0x55 (85) with velocity > 0 → marks midiOut1Passed
        self.midi.send_note(target_ch, 0x55, 100)
        self.log_midi(f"Sent Test Note 0x55 (85) v100 → midiOut1Passed")
        
        # Note 0x2A (42) with velocity > 0 → marks midiOut2Passed
        self.midi.send_note(target_ch, 0x2A, 100)
        self.log_midi(f"Sent Test Note 0x2A (42) v100 → midiOut2Passed")

    def endurance_toggle_cb(self, sender, app_data):
        self.endurance.set_enabled(app_data)
        self.update_endurance_status()
        if self.endurance.consume_plot_dirty():
            self.update_endurance_plot()
        self.settings.data["latency"]["enabled"] = bool(app_data)
        self._save_settings()

    def endurance_interval_cb(self, sender, app_data):
        self.endurance.set_interval(app_data)
        self.update_endurance_status()
        self.settings.data["latency"]["interval_s"] = float(app_data)
        self._save_settings()

    def endurance_notes_cb(self, sender, app_data):
        notes = [n.strip() for n in str(app_data).split(',')]
        self.endurance.set_probe_notes(notes)
        normalized = ", ".join(str(n) for n in self.endurance.probe_notes)
        if normalized != app_data:
            dpg.set_value("endurance_notes_input", normalized)
        self.update_endurance_status()
        self.update_endurance_offset_plot()
        self.settings.data["latency"]["probe_notes"] = normalized
        self._save_settings()

    def endurance_mod_toggle_cb(self, sender, app_data):
        self.endurance.set_modulation(enabled=app_data)
        self.update_endurance_status()
        self.settings.data["latency"]["mod_enabled"] = bool(app_data)
        self._save_settings()

    def endurance_mod_freq_cb(self, sender, app_data):
        self.endurance.set_modulation(freq_hz=app_data)
        self.update_endurance_status()
        self.settings.data["latency"]["mod_freq_hz"] = float(app_data)
        self._save_settings()

    def endurance_mod_depth_cb(self, sender, app_data):
        self.endurance.set_modulation(depth_ms=app_data)
        self.update_endurance_status()
        self.settings.data["latency"]["mod_depth_ms"] = float(app_data)
        self._save_settings()

    def endurance_types_cb(self, sender, app_data):
        selected = []
        for mtype in ALL_TYPES:
            tag = f"endurance_type_{mtype}"
            if dpg.does_item_exist(tag) and dpg.get_value(tag):
                selected.append(mtype)
        if not selected:
            selected = [TYPE_NOTE]
            dpg.set_value("endurance_type_note", True)
        self.endurance.set_probe_types(selected)
        self.update_endurance_status()
        self.update_endurance_offset_plot()
        self.settings.data["latency"]["probe_types"] = list(selected)
        self._save_settings()

    def endurance_randomize_types_cb(self, sender, app_data):
        count = int(dpg.get_value("endurance_random_count"))
        count = max(1, min(count, len(ALL_TYPES)))
        choices = random.sample(ALL_TYPES, count)
        for mtype in ALL_TYPES:
            dpg.set_value(f"endurance_type_{mtype}", mtype in choices)
        self.endurance_types_cb(None, None)
        self.settings.data["latency"]["random_type_count"] = count
        self._save_settings()

    def endurance_random_count_cb(self, sender, app_data):
        count = int(app_data)
        self.settings.data["latency"]["random_type_count"] = count
        self._save_settings()

    def endurance_clear_cb(self, sender, app_data):
        self.endurance.clear_results()
        self.update_endurance_plot()
        self.update_endurance_status()

    def fuzz_toggle_cb(self, sender, app_data):
        self.fuzz.set_enabled(app_data)
        self.update_fuzz_stats()
        if self.fuzz.consume_plot_dirty():
            self.update_fuzz_plot()
        self.settings.data["fuzz"]["enabled"] = bool(app_data)
        self._save_settings()

    def fuzz_mode_cb(self, sender, app_data):
        mode = app_data.lower()
        if mode == "single type":
            self.fuzz.generator.mode = "single"
            dpg.show_item("fuzz_single_group")
            dpg.hide_item("fuzz_mixed_group")
        elif mode == "mixed types":
            self.fuzz.generator.mode = "mixed"
            dpg.hide_item("fuzz_single_group")
            dpg.show_item("fuzz_mixed_group")
        else:
            self.fuzz.generator.mode = "chaos"
            dpg.hide_item("fuzz_single_group")
            dpg.hide_item("fuzz_mixed_group")
        self.update_fuzz_stats()
        self.settings.data["fuzz"]["mode"] = app_data
        self._save_settings()

    def fuzz_single_type_cb(self, sender, app_data):
        self.fuzz.generator.single_type = self._type_label_to_type.get(app_data, app_data)
        self.settings.data["fuzz"]["single_type"] = self.fuzz.generator.single_type
        self._save_settings()

    def fuzz_variation_cb(self, sender, app_data):
        self.fuzz.generator.vary_number = dpg.get_value("fuzz_vary_number")
        self.fuzz.generator.vary_value = dpg.get_value("fuzz_vary_value")
        self.fuzz.generator.randomize_channel = dpg.get_value("fuzz_random_channel")
        self.settings.data["fuzz"]["vary_number"] = bool(self.fuzz.generator.vary_number)
        self.settings.data["fuzz"]["vary_value"] = bool(self.fuzz.generator.vary_value)
        self.settings.data["fuzz"]["random_channel"] = bool(self.fuzz.generator.randomize_channel)
        self._save_settings()

    def fuzz_randomize_variation_cb(self, sender, app_data):
        vary_number = random.choice([True, False])
        vary_value = random.choice([True, False])
        random_channel = random.choice([True, False])
        dpg.set_value("fuzz_vary_number", vary_number)
        dpg.set_value("fuzz_vary_value", vary_value)
        dpg.set_value("fuzz_random_channel", random_channel)
        self.fuzz_variation_cb(None, None)

    def fuzz_mixed_types_cb(self, sender, app_data):
        selected = []
        for mtype in ALL_TYPES:
            tag = f"fuzz_type_{mtype}"
            if dpg.does_item_exist(tag) and dpg.get_value(tag):
                selected.append(mtype)
        if not selected:
            selected = [TYPE_NOTE]
            dpg.set_value("fuzz_type_note", True)
        self.fuzz.generator.allowed_types = selected
        self.settings.data["fuzz"]["allowed_types"] = list(selected)
        self._save_settings()

    def fuzz_timing_mode_cb(self, sender, app_data):
        mode = app_data.lower()
        if mode == "preset":
            dpg.show_item("fuzz_timing_preset_group")
            dpg.hide_item("fuzz_timing_full_group")
            preset = dpg.get_value("fuzz_preset_combo")
            intensity = dpg.get_value("fuzz_intensity_slider")
            self.fuzz.timing.set_preset(preset, intensity)
        else:
            dpg.hide_item("fuzz_timing_preset_group")
            dpg.show_item("fuzz_timing_full_group")
            self.fuzz_full_params_cb(None, None)
        self.settings.data["fuzz"]["timing_mode"] = app_data
        self._save_settings()

    def fuzz_preset_cb(self, sender, app_data):
        intensity = dpg.get_value("fuzz_intensity_slider")
        self.fuzz.timing.set_preset(app_data, intensity)
        self.settings.data["fuzz"]["preset"] = app_data
        self._save_settings()

    def fuzz_intensity_cb(self, sender, app_data):
        preset = dpg.get_value("fuzz_preset_combo")
        self.fuzz.timing.set_preset(preset, app_data)
        self.settings.data["fuzz"]["intensity"] = float(app_data)
        self._save_settings()

    def fuzz_full_params_cb(self, sender, app_data):
        base_rate = dpg.get_value("fuzz_base_rate")
        jitter = dpg.get_value("fuzz_jitter")
        burst_prob = dpg.get_value("fuzz_burst_prob")
        burst_min = dpg.get_value("fuzz_burst_min")
        burst_max = dpg.get_value("fuzz_burst_max")
        min_gap = dpg.get_value("fuzz_min_gap")
        max_gap = dpg.get_value("fuzz_max_gap")
        self.fuzz.timing.set_full(base_rate, jitter, burst_prob, burst_min, burst_max, min_gap, max_gap)
        self.settings.data["fuzz"]["base_rate"] = float(base_rate)
        self.settings.data["fuzz"]["jitter"] = float(jitter)
        self.settings.data["fuzz"]["burst_prob"] = float(burst_prob)
        self.settings.data["fuzz"]["burst_min"] = int(burst_min)
        self.settings.data["fuzz"]["burst_max"] = int(burst_max)
        self.settings.data["fuzz"]["min_gap"] = float(min_gap)
        self.settings.data["fuzz"]["max_gap"] = float(max_gap)
        self._save_settings()

    # --- BUILD GUI ---
    def build(self):
        dpg.create_viewport(title='Exotic MIDI Emulator & Stress Tester', width=1200, height=1000)
        
        with dpg.window(tag="Primary Window"):
            with dpg.tab_bar():
                with dpg.tab(label="Main"):
                    dpg.add_button(label="Reset Main Tab", callback=self.reset_main_cb)
                    # 1. Connection Header
                    with dpg.collapsing_header(label="Connections", default_open=True):
                        dpg.add_checkbox(label="Host Virtual Port", tag="virt_mode", callback=self.virt_mode_cb)
                        dpg.add_text("Device Name: 'Python Emulator'", tag="virt_status", show=False, color=(100, 255, 100))
                        
                        with dpg.group(tag="hw_connections"):
                            dpg.add_spacer(height=5)
                            with dpg.group(horizontal=True):
                                dpg.add_button(label="Refresh Ports", callback=self.refresh_ports_cb)
                                dpg.add_text("Ch:")
                                dpg.add_combo([str(i) for i in range(1, 17)] + ["Omni"], default_value="1", width=80,
                                              tag="channel_combo", callback=self.channel_combo_cb)
                            
                            dpg.add_text("Input:")
                            dpg.add_combo([], tag="input_combo", width=300, 
                                          callback=self.input_port_cb)
                            dpg.add_text("Output:")
                            dpg.add_combo([], tag="output_combo", width=300, 
                                          callback=self.output_port_cb)

                    # 2. Stress Test / Feedback Header (NEW)
                    with dpg.collapsing_header(label="Stress Test & Feedback", default_open=True):
                        with dpg.group(horizontal=True):
                            with dpg.group():
                                dpg.add_text("Feedback Mode:", color=(255, 200, 100))
                                dpg.add_radio_button(["None", "Immediate", "Delayed"], default_value="None", 
                                                     callback=self.feedback_mode_cb, horizontal=True, tag="feedback_mode")
                                # Removed invalid 'size' argument
                                dpg.add_text("(Immediate: Echoes inputs back instantly)")
                            
                            dpg.add_spacer(width=30)
                            with dpg.group():
                                dpg.add_text("Delay (ms):")
                                dpg.add_slider_int(min_value=1, max_value=2000, default_value=200, width=200,
                                                   callback=self.delay_time_cb, tag="delay_slider")
                                # Removed invalid 'size' argument
                                dpg.add_text("Simulates processing latency")
                        
                        dpg.add_spacer(height=10)
                        with dpg.group(horizontal=True):
                            dpg.add_button(label="Send Test Notes (0x55 & 0x2A)", callback=self.send_test_notes_cb)
                            dpg.add_text("Sends notes 85 & 42 with v100 for testing", color=(200, 200, 200))

                    # 3. Controls
                    dpg.add_spacer(height=20)
                    dpg.add_text("Parameters", color=(150, 255, 150))
                    with dpg.group(horizontal=True):
                        for key, p in PARAMS.items():
                            with dpg.group():
                                dpg.add_slider_int(tag=f"knob_{key}", 
                                                   min_value=0, max_value=p.get("max", 127), 
                                                   vertical=True, height=150, width=50,
                                                   callback=self.knob_cb, user_data=key)
                                dpg.add_text(p['label'], wrap=80)
                                dpg.add_text("0", tag=f"val_{key}", color=(0, 255, 255))

                    # 4. Monitors & Log
                    dpg.add_separator()
                    with dpg.group(horizontal=True):
                        dpg.add_text("Note/Vel:"); dpg.add_text("-", tag="lbl_note", color=(255,100,100))
                        dpg.add_spacer(width=20)
                        dpg.add_text("PolyAT:"); dpg.add_text("-", tag="lbl_poly_at", color=(255,100,255))
                    
                    dpg.add_separator()
                    with dpg.child_window(tag="log_window", height=150, autosize_x=True):
                        dpg.add_group(tag="log_group")

                with dpg.tab(label="Latency Test"):
                    dpg.add_text("Latency Response Monitor", color=(150, 200, 255))
                    dpg.add_spacer(height=4)
                    with dpg.group(horizontal=True):
                        dpg.add_checkbox(label="Enable Test", tag="endurance_enabled", callback=self.endurance_toggle_cb)
                        dpg.add_button(label="Clear Data", callback=self.endurance_clear_cb)
                        dpg.add_button(label="Reset Latency Tab", callback=self.reset_latency_cb)

                    dpg.add_slider_float(label="Base Interval (sec)", min_value=0.001, max_value=5.0,
                                         default_value=self.endurance.interval_s, width=220,
                                         callback=self.endurance_interval_cb, tag="endurance_interval", format="%.3f")
                    dpg.add_checkbox(label="Modulate Interval", tag="endurance_mod_enable",
                                     default_value=self.endurance.mod_enabled, callback=self.endurance_mod_toggle_cb)
                    dpg.add_slider_float(label="Modulation Frequency (Hz)", min_value=0.01, max_value=10.0,
                                         default_value=self.endurance.mod_freq_hz, width=220,
                                         callback=self.endurance_mod_freq_cb, tag="endurance_mod_freq", format="%.2f")
                    dpg.add_slider_float(label="Modulation Depth (ms)", min_value=0.0, max_value=2000.0,
                                         default_value=self.endurance.mod_depth_s * 1000.0, width=220,
                                         callback=self.endurance_mod_depth_cb, tag="endurance_mod_depth", format="%.1f")

                    dpg.add_text("Probe Message Types")
                    with dpg.group(horizontal=True):
                        for mtype in ALL_TYPES:
                            dpg.add_checkbox(label=LABELS[mtype], tag=f"endurance_type_{mtype}",
                                             default_value=(mtype in self.endurance.probe_types),
                                             callback=self.endurance_types_cb)

                    with dpg.group(horizontal=True):
                        dpg.add_slider_int(label="Random Type Count", min_value=1, max_value=len(ALL_TYPES),
                                           default_value=min(3, len(ALL_TYPES)), width=160,
                                           tag="endurance_random_count", callback=self.endurance_random_count_cb)
                        dpg.add_button(label="Randomize Types", callback=self.endurance_randomize_types_cb)

                    dpg.add_input_text(label="Probe Notes (CSV)", default_value=", ".join(str(n) for n in self.endurance.probe_notes),
                                       width=220, callback=self.endurance_notes_cb, tag="endurance_notes_input")

                    dpg.add_text("Status: Stopped", tag="endurance_status")
                    dpg.add_text("Duration: 0.0 min", tag="endurance_duration")
                    dpg.add_text("Last Round Trip: - ms", tag="endurance_last_rtt")
                    dpg.add_text("Last Inter-Event Dispersion: - ms", tag="endurance_last_spread")
                    dpg.add_text("Probes Sent: 0", tag="endurance_sent")
                    dpg.add_text("Missed Probes: 0", tag="endurance_missed")

                    dpg.add_spacer(height=10)
                    with dpg.plot(label="Inter-Event Dispersion Over Time", height=300, width=-1):
                        dpg.add_plot_legend()
                        dpg.add_plot_axis(dpg.mvXAxis, label="Test Duration (min)", tag="endurance_plot_x_axis")
                        dpg.add_plot_axis(dpg.mvYAxis, label="Inter-Event Dispersion (ms)", tag="endurance_plot_y_axis")
                        dpg.add_line_series([], [], label="Dispersion", parent="endurance_plot_y_axis", tag="endurance_plot_series")
                    with dpg.plot(label="Per-Message Offset", height=300, width=-1):
                        dpg.add_plot_legend()
                        dpg.add_plot_axis(dpg.mvXAxis, label="Test Duration (min)", tag="endurance_offset_x_axis")
                        dpg.add_plot_axis(dpg.mvYAxis, label="Offset From First (ms)", tag="endurance_offset_y_axis")
                        for label in self._endurance_offset_labels:
                            dpg.add_scatter_series([], [], label=label,
                                                   parent="endurance_offset_y_axis",
                                                   tag=self._endurance_series_tag(label))

                with dpg.tab(label="Fuzz Stress Test"):
                    dpg.add_text("Fuzz Stress Test", color=(255, 210, 150))
                    dpg.add_spacer(height=4)
                    with dpg.group(horizontal=True):
                        dpg.add_checkbox(label="Enable Fuzz", tag="fuzz_enabled", callback=self.fuzz_toggle_cb)
                        dpg.add_text("Variable timing + unique identities", color=(200, 200, 200))
                        dpg.add_button(label="Reset Fuzz Tab", callback=self.reset_fuzz_cb)

                    dpg.add_text("Message Mode")
                    dpg.add_radio_button(["Single Type", "Mixed Types", "Chaos"],
                                         default_value="Single Type", horizontal=True,
                                         callback=self.fuzz_mode_cb, tag="fuzz_mode")
                    dpg.add_checkbox(label="Randomize Channel", tag="fuzz_random_channel",
                                     default_value=False, callback=self.fuzz_variation_cb)

                    with dpg.group(tag="fuzz_single_group"):
                        dpg.add_combo(self._type_labels,
                                      default_value=LABELS[TYPE_CC], label="Type",
                                      callback=self.fuzz_single_type_cb, tag="fuzz_single_type")
                        with dpg.group(horizontal=True):
                            dpg.add_checkbox(label="Vary Number", tag="fuzz_vary_number", default_value=True, callback=self.fuzz_variation_cb)
                            dpg.add_checkbox(label="Vary Value", tag="fuzz_vary_value", default_value=True, callback=self.fuzz_variation_cb)
                        dpg.add_button(label="Randomize Variation", callback=self.fuzz_randomize_variation_cb)

                    with dpg.group(tag="fuzz_mixed_group", show=False):
                        dpg.add_text("Allowed Types")
                        with dpg.group(horizontal=True):
                            for mtype in ALL_TYPES:
                                dpg.add_checkbox(label=LABELS[mtype], tag=f"fuzz_type_{mtype}",
                                                 default_value=True, callback=self.fuzz_mixed_types_cb)

                    dpg.add_separator()
                    dpg.add_text("Timing Model")
                    dpg.add_radio_button(["Preset", "Full"], default_value="Preset",
                                         horizontal=True, callback=self.fuzz_timing_mode_cb, tag="fuzz_timing_mode")

                    with dpg.group(tag="fuzz_timing_preset_group"):
                        dpg.add_combo(["Steady", "Jitter", "Burst", "Chaos"], default_value="Steady",
                                      label="Preset", callback=self.fuzz_preset_cb, tag="fuzz_preset_combo")
                        dpg.add_slider_float(label="Intensity", min_value=0.0, max_value=1.0, default_value=0.5,
                                             callback=self.fuzz_intensity_cb, tag="fuzz_intensity_slider", format="%.2f")

                    with dpg.group(tag="fuzz_timing_full_group", show=False):
                        dpg.add_slider_float(label="Base Rate (msg/s)", min_value=1.0, max_value=200.0, default_value=20.0,
                                             callback=self.fuzz_full_params_cb, tag="fuzz_base_rate", format="%.1f")
                        dpg.add_slider_float(label="Jitter (0-1)", min_value=0.0, max_value=1.0, default_value=0.0,
                                             callback=self.fuzz_full_params_cb, tag="fuzz_jitter", format="%.2f")
                        dpg.add_slider_float(label="Burst Probability", min_value=0.0, max_value=1.0, default_value=0.0,
                                             callback=self.fuzz_full_params_cb, tag="fuzz_burst_prob", format="%.2f")
                        with dpg.group(horizontal=True):
                            dpg.add_slider_int(label="Burst Min", min_value=1, max_value=20, default_value=2,
                                               callback=self.fuzz_full_params_cb, tag="fuzz_burst_min")
                            dpg.add_slider_int(label="Burst Max", min_value=1, max_value=50, default_value=4,
                                               callback=self.fuzz_full_params_cb, tag="fuzz_burst_max")
                        with dpg.group(horizontal=True):
                            dpg.add_slider_float(label="Min Gap (ms)", min_value=0.0, max_value=1000.0, default_value=0.0,
                                                 callback=self.fuzz_full_params_cb, tag="fuzz_min_gap", format="%.1f")
                            dpg.add_slider_float(label="Max Gap (ms)", min_value=0.0, max_value=1000.0, default_value=1000.0,
                                                 callback=self.fuzz_full_params_cb, tag="fuzz_max_gap", format="%.1f")

                    dpg.add_separator()
                    dpg.add_text("Live Metrics")
                    dpg.add_text("Mean RTT: - ms", tag="fuzz_mean")
                    dpg.add_text("Std Dev RTT: - ms", tag="fuzz_std")
                    dpg.add_text("Min RTT: - ms", tag="fuzz_min")
                    dpg.add_text("Max RTT: - ms", tag="fuzz_max")
                    dpg.add_text("Last RTT: - ms", tag="fuzz_last")
                    dpg.add_text("Sent: 0  Received: 0  Pending: 0  Missing: 0", tag="fuzz_counts")

                    dpg.add_spacer(height=8)
                    with dpg.plot(label="RTT Over Time", height=260, width=-1):
                        dpg.add_plot_legend()
                        dpg.add_plot_axis(dpg.mvXAxis, label="Test Duration (min)", tag="fuzz_plot_x_axis")
                        dpg.add_plot_axis(dpg.mvYAxis, label="RTT (ms)", tag="fuzz_plot_y_axis")
                        dpg.add_scatter_series([], [], label="RTT", parent="fuzz_plot_y_axis", tag="fuzz_plot_series")
                        dpg.add_line_series([], [], label="Mean", parent="fuzz_plot_y_axis", tag="fuzz_plot_mean")
                        dpg.add_line_series([], [], label="+1σ", parent="fuzz_plot_y_axis", tag="fuzz_plot_std_hi")
                        dpg.add_line_series([], [], label="-1σ", parent="fuzz_plot_y_axis", tag="fuzz_plot_std_lo")

                    dpg.add_text("Missing Messages", color=(255, 180, 180))
                    with dpg.child_window(tag="fuzz_missing_window", height=120, autosize_x=True):
                        dpg.add_group(tag="fuzz_missing_group")

                with dpg.tab(label="Remote Test"):
                    dpg.add_text("OXI E16 Remote Mode Protocol Tester", color=(180, 230, 255))
                    dpg.add_spacer(height=4)
                    with dpg.group(horizontal=True):
                        dpg.add_button(label="Reset Remote Tab", callback=self.reset_remote_cb)
                        dpg.add_button(label="Clear Activity", callback=self.remote_clear_activity_cb)
                        dpg.add_text("RX Ch:")
                        dpg.add_combo(
                            [str(i) for i in range(1, 17)] + ["Omni"],
                            default_value="1",
                            width=90,
                            tag="remote_expected_channel",
                            callback=self.remote_expected_channel_cb,
                        )

                    with dpg.collapsing_header(label="Session + Full Suite", default_open=True):
                        with dpg.group(horizontal=True):
                            dpg.add_button(label="Enter Remote", callback=self.remote_send_enter_cb)
                            dpg.add_button(label="Exit Remote", callback=self.remote_send_exit_cb)
                            dpg.add_button(label="Start Full Suite", callback=self.remote_suite_start_cb)
                            dpg.add_button(label="Stop Suite", callback=self.remote_suite_stop_cb)
                            dpg.add_button(label="Manual PASS", callback=self.remote_suite_manual_pass_cb)
                            dpg.add_button(label="Manual FAIL", callback=self.remote_suite_manual_fail_cb)
                        dpg.add_text("Suite Progress: -", tag="remote_suite_progress")
                        dpg.add_text("Suite Step: -", tag="remote_suite_step")
                        dpg.add_text("Suite Prompt: -", tag="remote_suite_prompt", wrap=1000)
                        with dpg.child_window(tag="remote_results_window", height=120, autosize_x=True):
                            dpg.add_group(tag="remote_results_group")

                    with dpg.collapsing_header(label="Host -> Device Commands", default_open=True):
                        dpg.add_text("LED")
                        dpg.add_input_text(
                            label="Entries (enc,led,r,g,b; ...)",
                            tag="remote_led_entries",
                            width=-1,
                            default_value="0,0,127,0,0; 0,1,0,127,0; 0,2,0,0,127",
                        )
                        with dpg.group(horizontal=True):
                            dpg.add_button(label="Send LED", callback=self.remote_send_led_particular_cb)
                            dpg.add_button(label="Send LED Demo", callback=self.remote_send_led_demo_cb)
                            dpg.add_button(label="Send LED Ring Demo", callback=self.remote_send_led_ring_demo_cb)

                        dpg.add_separator()
                        dpg.add_text("LED Animations")
                        dpg.add_text("Ring modes use LED Ring (06 04): amount+bipolar or color sweep.", color=(180, 180, 180))
                        with dpg.group(horizontal=True):
                            dpg.add_slider_int(
                                label="Frame (ms)",
                                min_value=20,
                                max_value=1000,
                                default_value=120,
                                width=170,
                                tag="remote_led_anim_interval",
                                callback=self.remote_led_anim_interval_cb,
                            )
                            dpg.add_button(label="Start Particular Anim", callback=self.remote_start_particular_anim_cb)
                            dpg.add_button(label="Start Amount Anim", callback=self.remote_start_amount_anim_cb)
                            dpg.add_button(label="Start Ring Anim", callback=self.remote_start_ring_anim_cb)
                            dpg.add_button(label="Stop Anim", callback=self.remote_stop_anim_cb)
                        dpg.add_text("LED Animation: idle", tag="remote_led_anim_status")

                        dpg.add_separator()
                        dpg.add_text("OLED Labels")
                        dpg.add_input_text(
                            label="Title (16 chars)",
                            tag="remote_oled_title",
                            width=260,
                            default_value="REMOTE TEST",
                        )
                        dpg.add_input_text(
                            label="Labels CSV (16 items)",
                            tag="remote_oled_labels",
                            width=-1,
                            default_value="K00,K01,K02,K03,K04,K05,K06,K07,K08,K09,K10,K11,K12,K13,K14,K15",
                        )
                        with dpg.group(horizontal=True):
                            dpg.add_button(label="Send OLED Labels", callback=self.remote_send_oled_labels_cb)
                            dpg.add_button(label="Send OLED Labels Demo", callback=self.remote_send_oled_labels_demo_cb)

                        dpg.add_separator()
                        dpg.add_text("OLED Framebuffer")
                        with dpg.group(horizontal=True):
                            dpg.add_combo(
                                ["Checkerboard", "All Off", "All On", "Vertical Bars", "Horizontal Bars", "Diagonal"],
                                default_value="Checkerboard",
                                width=180,
                                tag="remote_fb_pattern",
                            )
                            dpg.add_button(label="Send OLED Framebuffer", callback=self.remote_send_oled_fb_cb)

                        dpg.add_separator()
                        dpg.add_text("Raw SysEx (hex bytes, optional F0/F7)")
                        dpg.add_input_text(
                            tag="remote_raw_hex",
                            multiline=True,
                            width=-1,
                            height=70,
                            default_value="F0 00 21 5B 02 01 06 55 F7",
                        )
                        dpg.add_button(label="Send Raw SysEx", callback=self.remote_send_raw_hex_cb)

                    with dpg.collapsing_header(label="Live Protocol Activity", default_open=True):
                        dpg.add_text("Protocol SysEx RX: 0", tag="remote_protocol_sysex_rx")
                        dpg.add_text("Other SysEx RX: 0", tag="remote_other_sysex_rx")
                        dpg.add_text("ACKs: 0", tag="remote_ack_count")
                        dpg.add_text("Encoder CCs: 0", tag="remote_encoder_cc_count")
                        dpg.add_text("Button notes: 0", tag="remote_button_note_count")
                        dpg.add_text("SHIFT notes: 0", tag="remote_shift_note_count")
                        dpg.add_text("Last TX: -", tag="remote_last_tx", wrap=1000)
                        dpg.add_text("Last RX: -", tag="remote_last_rx", wrap=1000)
                        dpg.add_text("Last Event: -", tag="remote_last_event", wrap=1000)
                        with dpg.child_window(tag="remote_log_window", height=180, autosize_x=True):
                            dpg.add_group(tag="remote_log_group")

    def update_endurance_plot(self):
        x_vals, y_vals = self.endurance.get_plot_data()
        dpg.set_value("endurance_plot_series", [x_vals, y_vals])
        if x_vals and y_vals:
            dpg.fit_axis_data("endurance_plot_x_axis")
            dpg.fit_axis_data("endurance_plot_y_axis")

    def _endurance_series_tag(self, label):
        safe = label.replace(" ", "_")
        return f"endurance_offset_{safe}"

    def apply_settings(self, section=None):
        self._applying_settings = True
        try:
            if section in (None, "main"):
                conn = self.settings.data.get("connections", {})
                main = self.settings.data.get("main", {})

                channel_val = conn.get("channel", self._default_channel)
                if dpg.does_item_exist("channel_combo"):
                    dpg.set_value("channel_combo", channel_val)
                    self.channel_combo_cb(None, channel_val)

                virt = bool(conn.get("virtual", False))
                dpg.set_value("virt_mode", virt)
                self.virt_mode_cb(None, virt)

                if not virt:
                    in_port = conn.get("input_port", "")
                    out_port = conn.get("output_port", "")
                    if in_port:
                        dpg.set_value("input_combo", in_port)
                        self.midi.connect_input(in_port)
                    if out_port:
                        dpg.set_value("output_combo", out_port)
                        self.midi.connect_output(out_port)

                feedback = main.get("feedback_mode", "None")
                if dpg.does_item_exist("feedback_mode"):
                    dpg.set_value("feedback_mode", feedback)
                    self.feedback_mode_cb(None, feedback)

                delay_ms = int(main.get("delay_ms", 200))
                if dpg.does_item_exist("delay_slider"):
                    dpg.set_value("delay_slider", delay_ms)
                    self.delay_time_cb(None, delay_ms)

            if section in (None, "latency"):
                latency = self.settings.data.get("latency", {})

                enabled = bool(latency.get("enabled", False))
                dpg.set_value("endurance_enabled", enabled)
                self.endurance_toggle_cb(None, enabled)

                interval_s = float(latency.get("interval_s", 5.0))
                dpg.set_value("endurance_interval", interval_s)
                self.endurance_interval_cb(None, interval_s)

                mod_enabled = bool(latency.get("mod_enabled", False))
                dpg.set_value("endurance_mod_enable", mod_enabled)
                self.endurance_mod_toggle_cb(None, mod_enabled)

                mod_freq = float(latency.get("mod_freq_hz", 0.5))
                dpg.set_value("endurance_mod_freq", mod_freq)
                self.endurance_mod_freq_cb(None, mod_freq)

                mod_depth = float(latency.get("mod_depth_ms", 0.0))
                dpg.set_value("endurance_mod_depth", mod_depth)
                self.endurance_mod_depth_cb(None, mod_depth)

                notes = latency.get("probe_notes", "60, 64, 67, 72")
                dpg.set_value("endurance_notes_input", notes)
                self.endurance_notes_cb(None, notes)

                type_list = latency.get("probe_types", ["note"])
                for mtype in ALL_TYPES:
                    dpg.set_value(f"endurance_type_{mtype}", mtype in type_list)
                self.endurance_types_cb(None, None)

                rand_count = int(latency.get("random_type_count", 3))
                dpg.set_value("endurance_random_count", rand_count)

            if section in (None, "fuzz"):
                fuzz = self.settings.data.get("fuzz", {})

                mode = fuzz.get("mode", "Single Type")
                dpg.set_value("fuzz_mode", mode)
                self.fuzz_mode_cb(None, mode)

                single_type = fuzz.get("single_type", "cc")
                dpg.set_value("fuzz_single_type", LABELS.get(single_type, LABELS[TYPE_CC]))
                self.fuzz_single_type_cb(None, LABELS.get(single_type, LABELS[TYPE_CC]))

                dpg.set_value("fuzz_vary_number", bool(fuzz.get("vary_number", True)))
                dpg.set_value("fuzz_vary_value", bool(fuzz.get("vary_value", True)))
                dpg.set_value("fuzz_random_channel", bool(fuzz.get("random_channel", False)))
                self.fuzz_variation_cb(None, None)

                allowed = fuzz.get("allowed_types", list(ALL_TYPES))
                for mtype in ALL_TYPES:
                    dpg.set_value(f"fuzz_type_{mtype}", mtype in allowed)
                self.fuzz_mixed_types_cb(None, None)

                timing_mode = fuzz.get("timing_mode", "Preset")
                dpg.set_value("fuzz_timing_mode", timing_mode)
                self.fuzz_timing_mode_cb(None, timing_mode)

                preset = fuzz.get("preset", "Steady")
                intensity = float(fuzz.get("intensity", 0.5))
                dpg.set_value("fuzz_preset_combo", preset)
                dpg.set_value("fuzz_intensity_slider", intensity)
                self.fuzz_preset_cb(None, preset)

                dpg.set_value("fuzz_base_rate", float(fuzz.get("base_rate", 20.0)))
                dpg.set_value("fuzz_jitter", float(fuzz.get("jitter", 0.0)))
                dpg.set_value("fuzz_burst_prob", float(fuzz.get("burst_prob", 0.0)))
                dpg.set_value("fuzz_burst_min", int(fuzz.get("burst_min", 2)))
                dpg.set_value("fuzz_burst_max", int(fuzz.get("burst_max", 4)))
                dpg.set_value("fuzz_min_gap", float(fuzz.get("min_gap", 0.0)))
                dpg.set_value("fuzz_max_gap", float(fuzz.get("max_gap", 1000.0)))
                self.fuzz_full_params_cb(None, None)

                enabled = bool(fuzz.get("enabled", False))
                dpg.set_value("fuzz_enabled", enabled)
                self.fuzz_toggle_cb(None, enabled)

            if section in (None, "remote"):
                remote = self.settings.data.get("remote", {})

                expected_channel = str(remote.get("expected_channel", self._default_channel))
                if dpg.does_item_exist("remote_expected_channel"):
                    dpg.set_value("remote_expected_channel", expected_channel)
                    self.remote_expected_channel_cb(None, expected_channel)

                interval_ms = int(remote.get("led_anim_interval_ms", 120))
                if dpg.does_item_exist("remote_led_anim_interval"):
                    dpg.set_value("remote_led_anim_interval", interval_ms)
                    self.remote_led_anim_interval_cb(None, interval_ms)
        finally:
            self._applying_settings = False

    def _save_settings(self):
        if self._applying_settings:
            return
        try:
            self.settings.save()
        except Exception:
            pass

    def update_endurance_offset_plot(self):
        self._ensure_endurance_offset_series()
        data = self.endurance.get_offset_plot_data()
        for label in self._endurance_offset_labels:
            series_tag = self._endurance_series_tag(label)
            if not dpg.does_item_exist(series_tag):
                continue
            xs, ys = data.get(label, ([], []))
            dpg.set_value(series_tag, [xs, ys])
        if self._endurance_offset_labels:
            dpg.fit_axis_data("endurance_offset_x_axis")
            dpg.fit_axis_data("endurance_offset_y_axis")

    def _ensure_endurance_offset_series(self):
        current = list(self.endurance.offset_labels)
        if current == self._endurance_offset_labels:
            return
        for label in self._endurance_offset_labels:
            tag = self._endurance_series_tag(label)
            if dpg.does_item_exist(tag):
                dpg.delete_item(tag)
        self._endurance_offset_labels = current
        for label in self._endurance_offset_labels:
            dpg.add_scatter_series([], [], label=label,
                                   parent="endurance_offset_y_axis",
                                   tag=self._endurance_series_tag(label))

    def update_endurance_metrics(self, result):
        if not result:
            return
        dpg.set_value("endurance_last_rtt", f"Last Round Trip: {result['round_trip_ms']:.3f} ms")
        dpg.set_value("endurance_last_spread", f"Last Inter-Event Dispersion: {result['spread_ms']:.3f} ms")

    def update_endurance_status(self):
        status = self.endurance.get_status()
        dpg.set_value("endurance_status", "Status: Running" if status['enabled'] else "Status: Stopped")
        dpg.set_value("endurance_duration", f"Duration: {status['elapsed_min']:.1f} min")
        dpg.set_value("endurance_sent", f"Probes Sent: {status['probes_sent']}")
        dpg.set_value("endurance_missed", f"Missed Probes: {status['missed_probes']}")
        last = status.get('last_result')
        if last:
            dpg.set_value("endurance_last_rtt", f"Last Round Trip: {last['round_trip_ms']:.3f} ms")
            dpg.set_value("endurance_last_spread", f"Last Inter-Event Dispersion: {last['spread_ms']:.3f} ms")
        else:
            dpg.set_value("endurance_last_rtt", "Last Round Trip: - ms")
            dpg.set_value("endurance_last_spread", "Last Inter-Event Dispersion: - ms")

    def update_fuzz_plot(self):
        x_vals, y_vals = self.fuzz.get_plot_data()
        dpg.set_value("fuzz_plot_series", [x_vals, y_vals])
        stats = self.fuzz.get_stats()
        if x_vals:
            x_min = min(x_vals)
            x_max = max(x_vals)
        else:
            x_min, x_max = 0.0, 1.0

        mean = stats.get('mean', 0.0) or 0.0
        std = stats.get('stddev', 0.0) or 0.0
        dpg.set_value("fuzz_plot_mean", [[x_min, x_max], [mean, mean]])
        dpg.set_value("fuzz_plot_std_hi", [[x_min, x_max], [mean + std, mean + std]])
        dpg.set_value("fuzz_plot_std_lo", [[x_min, x_max], [max(0.0, mean - std), max(0.0, mean - std)]])
        dpg.fit_axis_data("fuzz_plot_x_axis")
        dpg.fit_axis_data("fuzz_plot_y_axis")

    def update_fuzz_stats(self):
        stats = self.fuzz.get_stats()
        mean = stats.get('mean')
        std = stats.get('stddev')
        min_v = stats.get('min')
        max_v = stats.get('max')
        last = stats.get('last')
        dpg.set_value("fuzz_mean", f"Mean RTT: {mean:.3f} ms" if mean is not None else "Mean RTT: - ms")
        dpg.set_value("fuzz_std", f"Std Dev RTT: {std:.3f} ms" if std is not None else "Std Dev RTT: - ms")
        dpg.set_value("fuzz_min", f"Min RTT: {min_v:.3f} ms" if min_v is not None else "Min RTT: - ms")
        dpg.set_value("fuzz_max", f"Max RTT: {max_v:.3f} ms" if max_v is not None else "Max RTT: - ms")
        dpg.set_value("fuzz_last", f"Last RTT: {last:.3f} ms" if last is not None else "Last RTT: - ms")

        counts = (
            f"Sent: {stats.get('sent', 0)}  Received: {stats.get('received', 0)}  "
            f"Pending: {stats.get('pending', 0)}  Missing: {stats.get('missing', 0)}"
        )
        dpg.set_value("fuzz_counts", counts)

    def update_fuzz_missing_log(self):
        missing = self.fuzz.get_missing_log()
        if len(missing) == self._fuzz_missing_last:
            return
        dpg.delete_item("fuzz_missing_group", children_only=True)
        for item in missing:
            dpg.add_text(item, parent="fuzz_missing_group")
        self._fuzz_missing_last = len(missing)

    def update_remote_status(self):
        if not dpg.does_item_exist("remote_protocol_sysex_rx"):
            return

        snap = self.remote.get_status_snapshot()
        dpg.set_value("remote_protocol_sysex_rx", f"Protocol SysEx RX: {snap['protocol_sysex_rx']}")
        dpg.set_value("remote_other_sysex_rx", f"Other SysEx RX: {snap['other_sysex_rx']}")
        dpg.set_value("remote_ack_count", f"ACKs: {snap['ack_count']}")
        dpg.set_value("remote_encoder_cc_count", f"Encoder CCs: {snap['encoder_cc_count']}")
        dpg.set_value("remote_button_note_count", f"Button notes: {snap['button_note_count']}")
        dpg.set_value("remote_shift_note_count", f"SHIFT notes: {snap['shift_note_count']}")
        dpg.set_value("remote_last_tx", f"Last TX: {snap['last_tx']}")
        dpg.set_value("remote_last_rx", f"Last RX: {snap['last_rx']}")
        dpg.set_value("remote_last_event", f"Last Event: {snap['last_event']}")

        suite_state = "running" if snap["suite_running"] else "idle"
        dpg.set_value("remote_suite_progress", f"Suite Progress: {snap['suite_progress']} ({suite_state})")
        dpg.set_value("remote_suite_step", f"Suite Step: {snap['suite_current']}")
        prompt = snap["suite_prompt"] or "-"
        if snap["suite_manual_pending"]:
            prompt = f"Manual action required: {prompt}"
        dpg.set_value("remote_suite_prompt", f"Suite Prompt: {prompt}")

        if snap.get("led_anim_running"):
            mode = snap.get("led_anim_mode", "?")
            frames = snap.get("led_anim_frames", 0)
            interval_ms = snap.get("led_anim_interval_ms", 0)
            anim_text = f"LED Animation: running ({mode}) frame={frames} @ {interval_ms}ms"
        else:
            anim_text = "LED Animation: idle"
        dpg.set_value("remote_led_anim_status", anim_text)

        results = snap.get("suite_results", [])
        if len(results) != self._remote_results_last:
            dpg.delete_item("remote_results_group", children_only=True)
            for result in results:
                status = result.get("status", "FAIL")
                name = result.get("name", "step")
                detail = result.get("detail", "")
                color = (140, 255, 140) if status == "PASS" else (255, 150, 150)
                dpg.add_text(f"{status}: {name} ({detail})", parent="remote_results_group", color=color)
            self._remote_results_last = len(results)

        logs = self.remote.get_log_lines()
        tail = logs[-1] if logs else ""
        if len(logs) != self._remote_log_last or tail != self._remote_log_tail:
            dpg.delete_item("remote_log_group", children_only=True)
            for line in logs:
                dpg.add_text(line, parent="remote_log_group")
            self._remote_log_last = len(logs)
            self._remote_log_tail = tail

    def update_knob_from_midi(self, param_type, param_id, val):
        """Updates GUI knob without triggering the callback loop"""
        for key, p in PARAMS.items():
            if p['type'] == param_type and p['num'] == param_id:
                dpg.set_value(f"knob_{key}", val)
                dpg.set_value(f"val_{key}", str(val))
                return True
            # Special handling for PB/AT/PC which don't have IDs
            if p['type'] == param_type and param_type in ['pb', 'pc', 'at']:
                dpg.set_value(f"knob_{key}", val)
                dpg.set_value(f"val_{key}", str(val))
                return True
        return False
