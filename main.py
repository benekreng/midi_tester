import dearpygui.dearpygui as dpg
from midi_backend import MidiBackend

# --- CONFIGURATION ---
PARAMS = {
    "cutoff":    {"type": "cc",   "num": 74,   "label": "Cutoff (CC 74)",     "max": 127},
    "res":       {"type": "cc",   "num": 71,   "label": "Resonance (CC 71)",  "max": 127},
    "mod_hr":    {"type": "cc14", "num": 1,    "label": "Mod Wheel (CC1/33)", "max": 16383},
    "decay":     {"type": "nrpn", "num": 1300, "label": "Decay (NRPN 1300)",  "max": 16383},
    "pb":        {"type": "pb",   "num": 0,    "label": "Pitch Bend",         "max": 16383},
    "at":        {"type": "at",   "num": 0,    "label": "Aftertouch",         "max": 127},
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
        children = dpg.get_item_children("log_group", 1)
        if children and len(children) > 20:
            dpg.delete_item(children[0])

    # --- CALLBACKS ---
    def refresh_ports_callback():
        # Only refresh if not in virtual mode
        if not dpg.get_value("virt_mode"):
            dpg.configure_item("input_combo", items=midi.get_input_ports())
            dpg.configure_item("output_combo", items=midi.get_output_ports())

    def input_port_callback(sender, app_data):
        midi.connect_input(app_data)
        log_midi(f"Connected In: {app_data}")

    def output_port_callback(sender, app_data):
        midi.connect_output(app_data)
        log_midi(f"Connected Out: {app_data}")
        
    def virtual_mode_callback(sender, app_data):
        # app_data is boolean (checked/unchecked)
        is_virtual = app_data
        
        success, msg = midi.toggle_virtual_mode(is_virtual)
        log_midi(msg)
        
        if is_virtual and success:
            dpg.hide_item("hw_connections")
            dpg.show_item("virt_status")
        else:
            dpg.show_item("hw_connections")
            dpg.hide_item("virt_status")
            refresh_ports_callback()
            # If failed, uncheck box
            if is_virtual and not success:
                dpg.set_value("virt_mode", False)

    def control_callback(sender, app_data, user_data):
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
    dpg.create_viewport(title='Exotic MIDI Emulator', width=750, height=650)
    
    with dpg.window(tag="Primary Window"):
        
        # 1. Connection Manager
        with dpg.collapsing_header(label="Connections", default_open=True):
            
            dpg.add_checkbox(label="Host Virtual Port (Behave as Device)", tag="virt_mode", callback=virtual_mode_callback)
            dpg.add_text("Device Name: 'Python Emulator'", tag="virt_status", show=False, color=(100, 255, 100))
            
            # Hardware Connections Group (Hidden when virtual mode is on)
            with dpg.group(tag="hw_connections"):
                dpg.add_spacer(height=5)
                with dpg.group(horizontal=True):
                    dpg.add_button(label="Refresh Hardware Ports", callback=refresh_ports_callback)
                    dpg.add_text("Channel:")
                    dpg.add_combo([str(i) for i in range(1, 17)] + ["Omni"], 
                                  label="", default_value="1", width=80, tag="channel_combo")

                dpg.add_text("Hardware Input:")
                dpg.add_combo([], tag="input_combo", callback=input_port_callback, width=300)
                dpg.add_text("Hardware Output:")
                dpg.add_combo([], tag="output_combo", callback=output_port_callback, width=300)

        # 2. Controls
        dpg.add_spacer(height=20)
        dpg.add_text("Parameters (Double click knob to type)", color=(150, 255, 150))
        
        with dpg.group(horizontal=True):
            for key, p in PARAMS.items():
                with dpg.group():
                    max_v = p.get("max", 127)
                    dpg.add_slider_int(tag=f"knob_{key}", 
                                       min_value=0, max_value=max_v, default_value=0,
                                       vertical=True, height=150, width=50,
                                       callback=control_callback, user_data=key)
                    dpg.add_text(p['label'], wrap=80)
                    dpg.add_text("0", tag=f"val_{key}", color=(0, 255, 255))

        # 3. Monitors
        dpg.add_separator()
        with dpg.group(horizontal=True):
            dpg.add_text("Last Note:")
            dpg.add_text("-", tag="lbl_note", color=(255, 100, 100))
            dpg.add_spacer(width=20)
            dpg.add_text("Vel:")
            dpg.add_text("-", tag="lbl_vel", color=(255, 100, 100))
            dpg.add_spacer(width=20)
            dpg.add_text("Poly AT:")
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

    # --- MAIN LOOP ---
    while dpg.is_dearpygui_running():
        
        events = midi.poll_messages()
        selected_ch = get_selected_channel() 

        for e in events:
            # Check Channel (ignore filter if system message or if omni selected)
            if selected_ch != -1 and 'channel' in e and e['channel'] != selected_ch:
                continue

            # --- Feedback Loop Logic ---
            matched = False
            
            # Helper to update GUI without callback loop
            def update_gui(param_type, param_id, val):
                for key, p in PARAMS.items():
                    # Handle both standard CC and CC14 via 'num'
                    p_id = p['num']
                    
                    if p['type'] == param_type and p_id == param_id:
                        dpg.set_value(f"knob_{key}", val)
                        dpg.set_value(f"val_{key}", str(val))
                        return True
                return False

            if e['type'] == 'cc':
                matched = update_gui('cc', e['cc'], e['value'])
            
            elif e['type'] == 'cc14':
                matched = update_gui('cc14', e['cc'], e['value'])

            elif e['type'] == 'nrpn':
                matched = update_gui('nrpn', e['nrpn'], e['value'])

            elif e['type'] == 'pb':
                 # Special case, PB has no ID number
                 for key, p in PARAMS.items():
                    if p['type'] == 'pb':
                        dpg.set_value(f"knob_{key}", e['value'])
                        dpg.set_value(f"val_{key}", str(e['value']))
                        matched = True

            elif e['type'] == 'pc':
                 for key, p in PARAMS.items():
                    if p['type'] == 'pc':
                        dpg.set_value(f"knob_{key}", e['value'])
                        dpg.set_value(f"val_{key}", str(e['value']))
                        matched = True
            
            elif e['type'] == 'at':
                 for key, p in PARAMS.items():
                    if p['type'] == 'at':
                        dpg.set_value(f"knob_{key}", e['value'])
                        dpg.set_value(f"val_{key}", str(e['value']))
                        matched = True

            # Monitors
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