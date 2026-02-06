import time
import queue
import mido

class MidiBackend:
    def __init__(self):
        self.in_port = None
        self.out_port = None
        self.virtual_mode = False
        self._use_callback = False
        self._raw_queue = queue.Queue(maxsize=10000)
        self._dropped_msgs = 0
        
        # --- PARSER STATE ---
        self._nrpn_msb = -1
        self._nrpn_lsb = -1
        self._nrpn_val_msb = -1
        
        self._cc14_msb_num = -1
        self._cc14_msb_val = 0

    def get_input_ports(self):
        return mido.get_input_names()

    def get_output_ports(self):
        return mido.get_output_names()

    def close_ports(self):
        if self.in_port: 
            self.in_port.close()
            self.in_port = None
        if self.out_port: 
            self.out_port.close()
            self.out_port = None
        self._use_callback = False
        self._raw_queue = queue.Queue(maxsize=10000)
        self._dropped_msgs = 0

    def toggle_virtual_mode(self, state):
        self.close_ports()
        self.virtual_mode = state
        
        if state:
            try:
                # On macOS/Linux this works natively. On Windows, requires LoopMIDI usually.
                self.in_port = mido.open_input('Python Emulator', virtual=True, callback=self._on_midi_message)
                self._use_callback = True
                self.out_port = mido.open_output('Python Emulator', virtual=True)
                return True, "Created Virtual Port: 'Python Emulator'"
            except NotImplementedError:
                self.virtual_mode = False
                return False, "Virtual ports not supported on this OS/Driver"
            except Exception as e:
                self.virtual_mode = False
                return False, f"Error creating virtual port: {e}"
        return True, "Switched to Physical Mode"

    def connect_input(self, port_name):
        if self.virtual_mode: return False
        if self.in_port: self.in_port.close()
        try:
            self.in_port = mido.open_input(port_name, callback=self._on_midi_message)
            self._use_callback = True
            return True
        except Exception as e:
            print(f"Error input: {e}")
            self._use_callback = False
            return False

    def connect_output(self, port_name):
        if self.virtual_mode: return False
        if self.out_port: self.out_port.close()
        try:
            self.out_port = mido.open_output(port_name)
            return True
        except Exception as e:
            print(f"Error output: {e}")
            return False

    # --- SEND FUNCTIONS ---

    def send_cc(self, channel, cc, value):
        if self.out_port:
            self.out_port.send(mido.Message('control_change', channel=channel, control=cc, value=value))

    def send_cc14(self, channel, cc, value):
        if self.out_port:
            msb = (value >> 7) & 0x7F
            lsb = value & 0x7F
            self.out_port.send(mido.Message('control_change', channel=channel, control=cc, value=msb))
            self.out_port.send(mido.Message('control_change', channel=channel, control=cc + 32, value=lsb))

    def send_nrpn(self, channel, nrpn_num, value):
        if self.out_port:
            param_msb = (nrpn_num >> 7) & 0x7F
            param_lsb = nrpn_num & 0x7F
            val_msb = (value >> 7) & 0x7F
            val_lsb = value & 0x7F

            self.out_port.send(mido.Message('control_change', channel=channel, control=99, value=param_msb))
            self.out_port.send(mido.Message('control_change', channel=channel, control=98, value=param_lsb))
            self.out_port.send(mido.Message('control_change', channel=channel, control=6, value=val_msb))
            self.out_port.send(mido.Message('control_change', channel=channel, control=38, value=val_lsb))

    def send_pitch_bend(self, channel, value):
        if self.out_port:
            mido_val = value - 8192
            self.out_port.send(mido.Message('pitchwheel', channel=channel, pitch=mido_val))

    def send_program_change(self, channel, program):
        if self.out_port:
            self.out_port.send(mido.Message('program_change', channel=channel, program=program))

    def send_aftertouch(self, channel, value):
        if self.out_port:
            self.out_port.send(mido.Message('aftertouch', channel=channel, value=value))
    
    def send_note(self, channel, note, velocity):
        if self.out_port:
            self.out_port.send(mido.Message('note_on', channel=channel, note=note, velocity=velocity))
            
    def send_event_struct(self, e):
        """Helper to send a structured event dict (from poll_messages) back out."""
        try:
            ch = e['channel']
            if e['type'] == 'note':
                self.send_note(ch, e['note'], e['velocity'])
            elif e['type'] == 'cc':
                self.send_cc(ch, e['cc'], e['value'])
            elif e['type'] == 'cc14':
                self.send_cc14(ch, e['cc'], e['value'])
            elif e['type'] == 'nrpn':
                self.send_nrpn(ch, e['nrpn'], e['value'])
            elif e['type'] == 'pb':
                self.send_pitch_bend(ch, e['value'])
            elif e['type'] == 'pc':
                self.send_program_change(ch, e['value'])
            elif e['type'] == 'at':
                self.send_aftertouch(ch, e['value'])
        except Exception as err:
            print(f"Failed to retransmit: {err}")

    def _on_midi_message(self, msg):
        ts = time.perf_counter()
        try:
            self._raw_queue.put_nowait((msg, ts))
        except queue.Full:
            self._dropped_msgs += 1

    # --- RECEIVE LOOP ---

    def poll_messages(self):
        if not self.in_port: return []
        
        events = []
        if self._use_callback:
            while True:
                try:
                    msg, ts = self._raw_queue.get_nowait()
                except queue.Empty:
                    break
                events.extend(self._parse_message(msg, ts))
        else:
            for msg in self.in_port.iter_pending():
                ts = time.perf_counter()
                events.extend(self._parse_message(msg, ts))

        return events

    def _parse_message(self, msg, ts):
        events = []
        if msg.type == 'note_on' or msg.type == 'note_off':
            events.append({
                'type': 'note',
                'channel': msg.channel,
                'note': msg.note,
                'velocity': msg.velocity if msg.type == 'note_on' else 0,
                'timestamp': ts,
            })

        elif msg.type == 'pitchwheel':
            raw_val = msg.pitch + 8192
            events.append({'type': 'pb', 'channel': msg.channel, 'value': raw_val, 'timestamp': ts})

        elif msg.type == 'program_change':
            events.append({'type': 'pc', 'channel': msg.channel, 'value': msg.program, 'timestamp': ts})

        elif msg.type == 'aftertouch':
            events.append({'type': 'at', 'channel': msg.channel, 'value': msg.value, 'timestamp': ts})

        elif msg.type == 'polytouch':
            events.append({'type': 'poly_at', 'channel': msg.channel, 'note': msg.note, 'value': msg.value, 'timestamp': ts})

        elif msg.type == 'control_change':
            ccNum = msg.control
            ccVal = msg.value

            # NRPN Logic
            if ccNum == 99:
                self._nrpn_msb = ccVal
                return events
            if ccNum == 98:
                self._nrpn_lsb = ccVal
                return events
            if ccNum == 100 or ccNum == 101:
                self._nrpn_msb = -1
                self._nrpn_lsb = -1
            
            if self._nrpn_msb != -1 and self._nrpn_lsb != -1:
                if ccNum == 6:
                    self._nrpn_val_msb = ccVal
                    nrpn_num = (self._nrpn_msb << 7) | self._nrpn_lsb
                    val14 = (ccVal << 7)
                    events.append({'type': 'nrpn', 'channel': msg.channel, 'nrpn': nrpn_num, 'value': val14, 'timestamp': ts})
                    return events
                if ccNum == 38:
                    msb = 0 if self._nrpn_val_msb == -1 else self._nrpn_val_msb
                    nrpn_num = (self._nrpn_msb << 7) | self._nrpn_lsb
                    val14 = (msb << 7) | ccVal
                    events.append({'type': 'nrpn', 'channel': msg.channel, 'nrpn': nrpn_num, 'value': val14, 'timestamp': ts})
                    return events

            # CC14 Logic
            if 32 <= ccNum <= 63 and self._cc14_msb_num == (ccNum - 32):
                val14 = (self._cc14_msb_val << 7) | ccVal
                events.append({'type': 'cc14', 'channel': msg.channel, 'cc': self._cc14_msb_num, 'value': val14, 'timestamp': ts})
                self._cc14_msb_num = -1
                return events

            if ccNum <= 31:
                self._cc14_msb_num = ccNum
                self._cc14_msb_val = ccVal
            
            events.append({'type': 'cc', 'channel': msg.channel, 'cc': ccNum, 'value': ccVal, 'timestamp': ts})

        return events
