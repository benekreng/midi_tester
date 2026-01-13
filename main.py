import dearpygui.dearpygui as dpg
from midi_backend import MidiBackend

# --- CONFIGURATION ---
# "num" for NRPN = (MSB * 128) + LSB
# "num" for CC14 = The MSB CC Number (e.g., 1 for Mod Wheel)
PARAMS = {
    # 1. Standard 7-bit CC
    "cutoff":    {"type": "cc",   "num": 74,   "label": "Cutoff (CC 74)",     "max": 127},
    "res":       {"type": "cc",   "num": 71,   "label": "Resonance (CC 71)",  "max": 127},
    
    # 2. 14-bit CC (High Res)
    # Uses CC 1 (MSB) and CC 33 (LSB) automatically
    "mod_hr":    {"type": "cc14", "num": 1,    "label": "Mod Wheel (CC1/33)", "max": 16383},
    
    # 3. NRPN (14-bit)
    # Example: MSB 10, LSB 20 -> 10*128 + 20 = 1300
    "decay":     {"type": "nrpn", "num": 1300, "label": "Decay (NRPN 10/20)", "max": 16383},
    
    # 4. Pitch Bend
    "pb":        {"type": "pb",   "num": 0,    "label": "Pitch Bend",         "max": 16383},
    
    # 5. Aftertouch (Channel Pressure)
    "at":        {"type": "at",   "num": 0,    "label": "Aftertouch",         "max": 127},
    
    # 6. Program Change
    "prog":      {"type": "pc",   "num": 0,    "label": "Program Change",     "max": 127},
}

midi = MidiBackend()

def main():
    dpg.create_context()
    
    # --- UTILS ---
    def get_selected_channel():
        val = dpg.get_value("channel_combo")
        if val == "Omni": return -1
        return int(val) - 1

    def log_midi(msg_str):
        dpg.add_text(msg_str, parent="log_group")
        # Keep log clean
        children = dpg.get_item_children("log_group", 1)
        if children and len(children) > 20:
            dpg.delete_item(children[0])

    # --- CALLBACKS (GUI -> MIDI) ---
    def refresh_ports_callback():
        dpg.configure_item("input_combo", items=midi.get_input_ports())
        dpg.configure_item("output_combo", items=midi.get_output_ports())

    def input_port_callback(sender, app_data):
        midi.connect_input(app_data)
        log_midi(f"Connected In: {app_data}")

    def output_port_callback(sender, app_data):
        midi.connect_output(app_data)
        log_midi(f"Connected Out: {app_data}")

    def control_callback(sender, app_data, user_data):
        """ USER INPUT -> MIDI OUT """
        param_key = user_data
        param_def = PARAMS[param_key]
        val = int(app_data)
        
        ch = get_selected_channel()
        target_ch = 0 if ch == -1 else ch

        dpg.set_value(f"val_{param_key}", str(val))
        
        p_type = param_def['type']
        
        if p_type == 'cc':
            midi.send_cc(target_ch, param_def['num'], val)
            log_midi(f"Sent CC{param_def['num']} val:{val}")
            
        elif p_type == 'cc14':
            midi.send_cc14(target_ch, param_def['num'], val)
            log_midi(f"Sent CC14 #{param_def['num']} val:{val}")
            
        elif p_type == 'nrpn':
            midi.send_nrpn(target_ch, param_def['num'], val)
            log_midi(f"Sent NRPN {param_def['num']} val:{val}")
            
        elif p_type == 'pb':
            midi.send_pitch_bend(target_ch, val)
            log_midi(f"Sent PB val:{val}")
            
        elif p_type == 'pc':
            midi.send_program_change(target_ch, val)
            log_midi(f"Sent PC val:{val}")
            
        elif p_type == 'at':
            midi.send_aftertouch(target_ch, val)
            log_midi(f"Sent AT val:{val}")

    # --- GUI LAYOUT ---
    dpg.create_viewport(title='Exotic MIDI Emulator', width=750, height=600)
    
    with dpg.window(tag="Primary Window"):
        
        # 1. Connections
        with dpg.collapsing_header(label="Connections", default_open=True):
            with dpg.group(horizontal=True):
                dpg.add_button(label="Refresh Ports", callback=refresh_ports_callback)
                dpg.add_text("Channel:")
                dpg.add_combo([str(i) for i in range(1, 17)] + ["Omni"], 
                              label="", default_value="1", width=80, tag="channel_combo")

            dpg.add_combo([], tag="input_combo", callback=input_port_callback, width=300)
            dpg.add_combo([], tag="output_combo", callback=output_port_callback, width=300)

        # 2. Controls
        dpg.add_spacer(height=20)
        dpg.add_text("Device Parameters (Double click knob to type)", color=(150, 255, 150))
        
        with dpg.group(horizontal=True):
            for key, p in PARAMS.items():
                with dpg.group():
                    # Check max value to determine if we need a big slider
                    max_v = p.get("max", 127)
                    
                    dpg.add_slider_int(tag=f"knob_{key}", 
                                       min_value=0, max_value=max_v, default_value=0,
                                       vertical=True, height=150, width=50,
                                       callback=control_callback, user_data=key)
                    
                    dpg.add_text(p['label'], wrap=80)
                    dpg.add_text("0", tag=f"val_{key}", color=(0, 255, 255))

        # 3. Note Monitor
        dpg.add_separator()
        with dpg.group(horizontal=True):
            dpg.add_text("Last Note:")
            dpg.add_text("-", tag="lbl_note", color=(255, 100, 100))
            dpg.add_spacer(width=20)
            dpg.add_text("Vel:")
            dpg.add_text("-", tag="lbl_vel", color=(255, 100, 100))
            dpg.add_spacer(width=20)
            dpg.add_text("Poly AT (Last):")
            dpg.add_text("-", tag="lbl_poly_at", color=(255, 100, 255))

        # 4. Log
        dpg.add_separator()
        dpg.add_text("MIDI Log:")
        with dpg.child_window(tag="log_window", height=150, autosize_x=True):
            dpg.add_group(tag="log_group")

    dpg.setup_dearpygui()
    dpg.show_viewport()
    dpg.set_primary_window("Primary Window", True)
    dpg.set_global_font_scale(1.25)
    
    refresh_ports_callback()

    # --- MAIN LOOP (MIDI POLL) ---
    while dpg.is_dearpygui_running():
        
        events = midi.poll_messages()
        selected_ch = get_selected_channel() 

        for e in events:
            # Check Channel (ignore filter if system message or if omni selected)
            if selected_ch != -1 and 'channel' in e and e['channel'] != selected_ch:
                continue

            # --- Feedback Loop Logic ---
            # We look for a matching parameter in PARAMS and update the slider
            
            matched = False
            
            # 1. Handle CC
            if e['type'] == 'cc':
                for key, p in PARAMS.items():
                    if p['type'] == 'cc' and p['num'] == e['cc']:
                        dpg.set_value(f"knob_{key}", e['value'])
                        dpg.set_value(f"val_{key}", str(e['value']))
                        matched = True

            # 2. Handle CC14
            elif e['type'] == 'cc14':
                for key, p in PARAMS.items():
                    if p['type'] == 'cc14' and p['num'] == e['cc']:
                        dpg.set_value(f"knob_{key}", e['value'])
                        dpg.set_value(f"val_{key}", str(e['value']))
                        matched = True

            # 3. Handle NRPN
            elif e['type'] == 'nrpn':
                for key, p in PARAMS.items():
                    if p['type'] == 'nrpn' and p['num'] == e['nrpn']:
                        dpg.set_value(f"knob_{key}", e['value'])
                        dpg.set_value(f"val_{key}", str(e['value']))
                        matched = True

            # 4. Handle Pitch Bend
            elif e['type'] == 'pb':
                # Update any PB sliders
                for key, p in PARAMS.items():
                    if p['type'] == 'pb':
                        dpg.set_value(f"knob_{key}", e['value'])
                        dpg.set_value(f"val_{key}", str(e['value']))
                        matched = True

            # 5. Handle Program Change
            elif e['type'] == 'pc':
                for key, p in PARAMS.items():
                    if p['type'] == 'pc':
                        dpg.set_value(f"knob_{key}", e['value'])
                        dpg.set_value(f"val_{key}", str(e['value']))
                        matched = True

            # 6. Handle Channel Aftertouch
            elif e['type'] == 'at':
                for key, p in PARAMS.items():
                    if p['type'] == 'at':
                        dpg.set_value(f"knob_{key}", e['value'])
                        dpg.set_value(f"val_{key}", str(e['value']))
                        matched = True

            # 7. Visual Monitors (Note / Poly AT)
            if e['type'] == 'note':
                dpg.set_value("lbl_note", str(e['note']))
                dpg.set_value("lbl_vel", str(e['velocity']))
                log_midi(f"Note {e['note']} v{e['velocity']}")
            
            elif e['type'] == 'poly_at':
                dpg.set_value("lbl_poly_at", f"N:{e['note']} V:{e['value']}")
                log_midi(f"PolyAT {e['note']} v{e['value']}")
                
            elif matched:
                log_midi(f"Recv {e['type'].upper()} val:{e.get('value')}")

        dpg.render_dearpygui_frame()

    dpg.destroy_context()

if __name__ == "__main__":
    main()