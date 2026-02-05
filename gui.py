import dearpygui.dearpygui as dpg

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
    def __init__(self, midi_backend, processor, endurance_monitor):
        self.midi = midi_backend
        self.processor = processor
        self.endurance = endurance_monitor
        self.log_items = []

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

    # --- CALLBACKS ---
    def refresh_ports_cb(self, sender, app_data):
        if not dpg.get_value("virt_mode"):
            dpg.configure_item("input_combo", items=self.midi.get_input_ports())
            dpg.configure_item("output_combo", items=self.midi.get_output_ports())

    def virt_mode_cb(self, sender, app_data):
        is_virt = app_data
        success, msg = self.midi.toggle_virtual_mode(is_virt)
        self.log_midi(msg)
        
        if is_virt and success:
            dpg.hide_item("hw_connections")
            dpg.show_item("virt_status")
        else:
            dpg.show_item("hw_connections")
            dpg.hide_item("virt_status")
            self.refresh_ports_cb(None, None)
            if is_virt and not success:
                dpg.set_value("virt_mode", False)

    def feedback_mode_cb(self, sender, app_data):
        # app_data will be "None", "Immediate", or "Delayed" based on radio button
        mode = app_data.upper()
        self.processor.set_feedback_mode(mode)
        self.log_midi(f"Feedback Mode: {mode}")

    def delay_time_cb(self, sender, app_data):
        self.processor.set_delay(int(app_data))

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

    def endurance_interval_cb(self, sender, app_data):
        # Convert milliseconds to seconds
        self.endurance.set_interval(app_data / 1000.0)
        self.update_endurance_status()

    def endurance_notes_cb(self, sender, app_data):
        notes = [n.strip() for n in str(app_data).split(',')]
        self.endurance.set_probe_notes(notes)
        normalized = ", ".join(str(n) for n in self.endurance.probe_notes)
        if normalized != app_data:
            dpg.set_value("endurance_notes_input", normalized)
        self.update_endurance_status()

    def endurance_clear_cb(self, sender, app_data):
        self.endurance.clear_results()
        self.update_endurance_plot()
        self.update_endurance_status()

    # --- BUILD GUI ---
    def build(self):
        dpg.create_viewport(title='Exotic MIDI Emulator & Stress Tester', width=800, height=750)
        
        with dpg.window(tag="Primary Window"):
            with dpg.tab_bar():
                with dpg.tab(label="Main"):
                    # 1. Connection Header
                    with dpg.collapsing_header(label="Connections", default_open=True):
                        dpg.add_checkbox(label="Host Virtual Port", tag="virt_mode", callback=self.virt_mode_cb)
                        dpg.add_text("Device Name: 'Python Emulator'", tag="virt_status", show=False, color=(100, 255, 100))
                        
                        with dpg.group(tag="hw_connections"):
                            dpg.add_spacer(height=5)
                            with dpg.group(horizontal=True):
                                dpg.add_button(label="Refresh Ports", callback=self.refresh_ports_cb)
                                dpg.add_text("Ch:")
                                dpg.add_combo([str(i) for i in range(1, 17)] + ["Omni"], default_value="1", width=80, tag="channel_combo")
                            
                            dpg.add_text("Input:")
                            dpg.add_combo([], tag="input_combo", width=300, 
                                          callback=lambda s, a: self.midi.connect_input(a))
                            dpg.add_text("Output:")
                            dpg.add_combo([], tag="output_combo", width=300, 
                                          callback=lambda s, a: self.midi.connect_output(a))

                    # 2. Stress Test / Feedback Header (NEW)
                    with dpg.collapsing_header(label="Stress Test & Feedback", default_open=True):
                        with dpg.group(horizontal=True):
                            with dpg.group():
                                dpg.add_text("Feedback Mode:", color=(255, 200, 100))
                                dpg.add_radio_button(["None", "Immediate", "Delayed"], default_value="None", 
                                                     callback=self.feedback_mode_cb, horizontal=True)
                                # Removed invalid 'size' argument
                                dpg.add_text("(Immediate: Echoes inputs back instantly)")
                            
                            dpg.add_spacer(width=30)
                            with dpg.group():
                                dpg.add_text("Delay (ms):")
                                dpg.add_slider_int(min_value=1, max_value=2000, default_value=200, width=200,
                                                   callback=self.delay_time_cb)
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

                with dpg.tab(label="Endurance Test"):
                    dpg.add_text("Endurance Latency Monitor", color=(150, 200, 255))
                    dpg.add_spacer(height=4)
                    with dpg.group(horizontal=True):
                        dpg.add_checkbox(label="Enable Test", tag="endurance_enabled", callback=self.endurance_toggle_cb)
                        dpg.add_button(label="Clear Data", callback=self.endurance_clear_cb)

                    dpg.add_slider_int(label="Probe Interval (ms)", min_value=1, max_value=1000,
                                       default_value=int(self.endurance.interval_s * 1000), width=220,
                                       callback=self.endurance_interval_cb, tag="endurance_interval")
                    dpg.add_input_text(label="Probe Notes (CSV)", default_value=", ".join(str(n) for n in self.endurance.probe_notes),
                                       width=220, callback=self.endurance_notes_cb, tag="endurance_notes_input")

                    dpg.add_text("Status: Stopped", tag="endurance_status")
                    dpg.add_text("Duration: 0.0 min", tag="endurance_duration")
                    dpg.add_text("Last Round Trip: - ms", tag="endurance_last_rtt")
                    dpg.add_text("Last Spread: - ms", tag="endurance_last_spread")
                    dpg.add_text("Probes Sent: 0", tag="endurance_sent")
                    dpg.add_text("Missed Probes: 0", tag="endurance_missed")

                    dpg.add_spacer(height=10)
                    with dpg.plot(label="Chord Spread Over Time", height=300, width=-1):
                        dpg.add_plot_legend()
                        dpg.add_plot_axis(dpg.mvXAxis, label="Test Duration (min)", tag="endurance_plot_x_axis")
                        dpg.add_plot_axis(dpg.mvYAxis, label="Chord Spread (ms)", tag="endurance_plot_y_axis")
                        dpg.add_line_series([], [], label="Spread", parent="endurance_plot_y_axis", tag="endurance_plot_series")

    def update_endurance_plot(self):
        x_vals, y_vals = self.endurance.get_plot_data()
        dpg.set_value("endurance_plot_series", [x_vals, y_vals])
        if x_vals and y_vals:
            dpg.fit_axis_data("endurance_plot_x_axis")
            dpg.fit_axis_data("endurance_plot_y_axis")

    def update_endurance_metrics(self, result):
        if not result:
            return
        dpg.set_value("endurance_last_rtt", f"Last Round Trip: {result['round_trip_ms']:.3f} ms")
        dpg.set_value("endurance_last_spread", f"Last Spread: {result['spread_ms']:.3f} ms")

    def update_endurance_status(self):
        status = self.endurance.get_status()
        dpg.set_value("endurance_status", "Status: Running" if status['enabled'] else "Status: Stopped")
        dpg.set_value("endurance_duration", f"Duration: {status['elapsed_min']:.1f} min")
        dpg.set_value("endurance_sent", f"Probes Sent: {status['probes_sent']}")
        dpg.set_value("endurance_missed", f"Missed Probes: {status['missed_probes']}")
        last = status.get('last_result')
        if last:
            dpg.set_value("endurance_last_rtt", f"Last Round Trip: {last['round_trip_ms']:.3f} ms")
            dpg.set_value("endurance_last_spread", f"Last Spread: {last['spread_ms']:.3f} ms")
        else:
            dpg.set_value("endurance_last_rtt", "Last Round Trip: - ms")
            dpg.set_value("endurance_last_spread", "Last Spread: - ms")

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
