import dearpygui.dearpygui as dpg
from midi_backend import MidiBackend
from processor import MidiProcessor
from endurance_monitor import EnduranceLatencyMonitor
from gui import AppGui

def main():
    # 1. Setup Backend
    midi = MidiBackend()
    processor = MidiProcessor(midi)
    endurance = EnduranceLatencyMonitor(midi)
    
    # 2. Setup GUI
    dpg.create_context()
    gui = AppGui(midi, processor, endurance)
    gui.build()
    
    dpg.setup_dearpygui()
    dpg.show_viewport()
    dpg.set_primary_window("Primary Window", True)
    dpg.set_global_font_scale(1.25)
    
    # Initialize config
    processor.set_delay(200) # Sync with GUI default
    gui.refresh_ports_cb(None, None)

    # 3. Main Loop
    while dpg.is_dearpygui_running():
        
        # A. Poll Hardware
        raw_events = midi.poll_messages()
        
        # B. Process Logic (Feedback/Delay/Stress)
        # This step might modify events or schedule them to be sent back later
        processed_events = processor.process_incoming_events(raw_events)
        
        # C. Flush Scheduled Events (Delayed Feedback)
        processor.process_scheduled_events()

        # D. Update GUI with incoming data
        selected_ch = gui.get_selected_channel()

        # Endurance Monitor (probe scheduling + analysis)
        result = endurance.tick(processed_events, selected_ch)
        if endurance.consume_plot_dirty():
            gui.update_endurance_plot()
        if result:
            gui.update_endurance_metrics(result)
        gui.update_endurance_status()
        
        for e in processed_events:
            if selected_ch != -1 and 'channel' in e and e['channel'] != selected_ch:
                continue
                
            matched = False
            
            if e['type'] == 'cc':
                matched = gui.update_knob_from_midi('cc', e['cc'], e['value'])
            elif e['type'] == 'cc14':
                matched = gui.update_knob_from_midi('cc14', e['cc'], e['value'])
            elif e['type'] == 'nrpn':
                matched = gui.update_knob_from_midi('nrpn', e['nrpn'], e['value'])
            elif e['type'] in ['pb', 'pc', 'at']:
                matched = gui.update_knob_from_midi(e['type'], 0, e['value'])
            elif e['type'] == 'note':
                dpg.set_value("lbl_note", f"{e['note']} v{e['velocity']}")
                gui.log_midi(f"Note {e['note']}")
            
            if matched:
                gui.log_midi(f"Recv {e['type'].upper()} val:{e.get('value')}")

        dpg.render_dearpygui_frame()

    dpg.destroy_context()
    midi.close_ports()

if __name__ == "__main__":
    main()
