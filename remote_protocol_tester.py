import time
from collections import deque


# --- Protocol Constants ---
# Header bytes (without F0/F7 which mido handles)
OXI_HEADER = [0x00, 0x21, 0x5B, 0x02, 0x01]
REMOTE_CATEGORY = 0x06

# Message IDs
MSG_ENTER_REMOTE = 0x55
MSG_REMOTE_ACK = 0x53
MSG_EXIT_REMOTE = 0x00
MSG_LED = 0x01
MSG_OLED_FRAMEBUFFER = 0x02
MSG_OLED_LABELS = 0x03

MSG_NAMES = {
    MSG_ENTER_REMOTE: "ENTER_REMOTE",
    MSG_REMOTE_ACK: "REMOTE_ACK",
    MSG_EXIT_REMOTE: "EXIT_REMOTE",
    MSG_LED: "LED",
    MSG_OLED_FRAMEBUFFER: "OLED_FRAMEBUFFER",
    MSG_OLED_LABELS: "OLED_LABELS",
}


# --- 7-Bit Packing (per REMOTE_SYSEX_ENCODING.md) ---
# Groups of up to 7 data bytes. Each group is preceded by a top-bits byte
# carrying the MSB of each data byte. 7 data bytes -> 8 wire bytes.
# Grouping is over the ENTIRE payload, NOT per logical chunk.

def pack_7bit(payload):
    """Encode raw data bytes into 7-bit packed SysEx payload.

    For each group of up to 7 bytes, prepend a top-bits byte where
    bit N carries the MSB of data byte N in the group.
    """
    data = [int(v) & 0xFF for v in payload]
    packed = []
    idx = 0
    while idx < len(data):
        chunk = data[idx:idx + 7]
        top = 0
        body = []
        for i, b in enumerate(chunk):
            if b & 0x80:
                top |= (1 << i)
            body.append(b & 0x7F)
        packed.append(top)
        packed.extend(body)
        idx += 7
    return packed


def unpack_7bit(packed):
    """Decode 7-bit packed SysEx payload back to raw data bytes."""
    raw = []
    data = [int(v) & 0x7F for v in packed]
    idx = 0
    while idx < len(data):
        top = data[idx]
        idx += 1
        for bit in range(7):
            if idx >= len(data):
                break
            b = data[idx]
            idx += 1
            if top & (1 << bit):
                b |= 0x80
            raw.append(b)
    return raw


# --- Helpers ---

def _clamp(v, lo, hi):
    try:
        i = int(v)
    except (ValueError, TypeError):
        i = lo
    return max(lo, min(hi, i))


def _fmt_sysex(data_bytes, max_bytes=120):
    wrapped = [0xF0] + [int(b) & 0xFF for b in data_bytes] + [0xF7]
    if len(wrapped) <= max_bytes:
        return " ".join(f"{b:02X}" for b in wrapped)
    visible = wrapped[:max_bytes]
    extra = len(wrapped) - max_bytes
    return f"{' '.join(f'{b:02X}' for b in visible)} ... (+{extra} bytes)"


def _fixed_ascii(text, length):
    """Convert text to fixed-length ASCII byte list, padded with spaces."""
    src = str(text or "")
    out = []
    for ch in src:
        code = ord(ch)
        out.append(code if code < 128 else ord("?"))
        if len(out) >= length:
            break
    while len(out) < length:
        out.append(ord(" "))
    return out


class RemoteProtocolTester:
    def __init__(self, midi_backend):
        self.midi = midi_backend

        self.expected_channel = 0  # 0-15, -1 means omni

        self.log_lines = deque(maxlen=400)

        self.protocol_sysex_rx = 0
        self.other_sysex_rx = 0
        self.ack_count = 0
        self.encoder_cc_count = 0
        self.button_note_count = 0
        self.shift_note_count = 0

        self.last_tx = "-"
        self.last_rx = "-"
        self.last_event = "-"

        self.suite_running = False
        self.suite_steps = []
        self.suite_index = 0
        self.suite_results = []
        self.suite_manual_pending = False
        self._manual_decision = None

    # -------------------- Logging --------------------

    def _log(self, message):
        stamp = time.strftime("%H:%M:%S")
        self.log_lines.append(f"[{stamp}] {message}")

    def clear_logs(self):
        self.log_lines.clear()

    def reset_counters(self):
        self.protocol_sysex_rx = 0
        self.other_sysex_rx = 0
        self.ack_count = 0
        self.encoder_cc_count = 0
        self.button_note_count = 0
        self.shift_note_count = 0
        self.last_tx = "-"
        self.last_rx = "-"
        self.last_event = "-"

    def clear_all_activity(self):
        self.clear_logs()
        self.reset_counters()

    def get_log_lines(self):
        return list(self.log_lines)

    # -------------------- Message Building --------------------
    # Message structure (per encoding doc):
    #   F0  00 21 5B  02 01  06  [MSG_ID]  [7-bit packed payload]  F7
    #       Mfr ID    Prod   Cat  Msg       Encoded data

    def _build_message(self, msg_id, payload=None):
        """Build SysEx data bytes (without F0/F7, mido adds those).

        Category byte 0x06 is required for all remote mode messages.
        """
        data = list(OXI_HEADER)
        data.append(REMOTE_CATEGORY)
        data.append(int(msg_id) & 0x7F)
        if payload:
            data.extend(pack_7bit(payload))
        return data

    def _send_frame(self, data, label):
        if not self.midi.out_port:
            reason = "No MIDI output port connected"
            self._log(f"TX {label} FAILED: {reason}")
            return False, reason

        try:
            self.midi.send_sysex(data)
            hex_line = _fmt_sysex(data)
            self.last_tx = f"{label}: {hex_line}"
            self._log(f"TX {label}: {hex_line}")
            return True, "sent"
        except Exception as exc:
            msg = str(exc)
            self._log(f"TX {label} FAILED: {msg}")
            return False, msg

    # -------------------- Host -> Device Messages --------------------

    def send_enter_remote(self):
        """F0 00 21 5B 02 01 06 55 F7"""
        data = self._build_message(MSG_ENTER_REMOTE)
        return self._send_frame(data, "ENTER_REMOTE")

    def send_exit_remote(self):
        """F0 00 21 5B 02 01 06 00 F7"""
        data = self._build_message(MSG_EXIT_REMOTE)
        return self._send_frame(data, "EXIT_REMOTE")

    def send_led_particular(self, entries):
        """LED command: set individual LEDs with RGB values.

        Each entry is a 5-byte chunk: [encoder, led, R, G, B]
        R/G/B are 0-127 in the remote protocol.
        All chunks are concatenated into one payload before 7-bit packing.
        """
        payload = []
        for ent in entries:
            enc = _clamp(ent.get("encoder", 0), 0, 15)
            led = _clamp(ent.get("led", 0), 0, 15)
            r = _clamp(ent.get("r", 0), 0, 127)
            g = _clamp(ent.get("g", 0), 0, 127)
            b = _clamp(ent.get("b", 0), 0, 127)
            payload.extend([enc, led, r, g, b])
        data = self._build_message(MSG_LED, payload=payload)
        return self._send_frame(data, f"LED count={len(entries)}")

    def send_led_particular_demo(self):
        """Demo: set all 16 LEDs on encoder 0 with visible RGB gradient."""
        entries = []
        for led in range(16):
            entries.append({
                "encoder": 0,
                "led": led,
                "r": (led * 8) % 128,
                "g": (127 - (led * 8)) % 128,
                "b": (led * 4) % 128,
            })
        return self.send_led_particular(entries)

    def send_oled_labels(self, title, labels):
        """OLED Labels: 16-char title + 16 x 4-char labels = 80 raw bytes.

        80 bytes -> 92 wire bytes after 7-bit packing (80 + ceil(80/7) = 92).
        """
        clean_labels = list(labels[:16])
        while len(clean_labels) < 16:
            clean_labels.append("")

        payload = []
        payload.extend(_fixed_ascii(title, 16))
        for item in clean_labels:
            payload.extend(_fixed_ascii(item, 4))

        data = self._build_message(MSG_OLED_LABELS, payload=payload)
        return self._send_frame(data, "OLED_LABELS")

    def send_oled_labels_demo(self):
        labels = [f"K{idx:02d}" for idx in range(16)]
        return self.send_oled_labels("REMOTE TEST", labels)

    def _framebuffer_pattern(self, pattern):
        """Generate a 1024-byte framebuffer (128x64 pixels, 1bpp, SSD1306 format)."""
        name = str(pattern or "Checkerboard").strip().lower()
        buf = [0] * 1024

        if name == "all off":
            return buf

        if name == "all on":
            return [0xFF] * 1024

        if name == "vertical bars":
            for page in range(8):
                for col in range(128):
                    buf[page * 128 + col] = 0xFF if (col // 8) % 2 == 0 else 0x00
            return buf

        if name == "horizontal bars":
            for page in range(8):
                val = 0xFF if page % 2 == 0 else 0x00
                for col in range(128):
                    buf[page * 128 + col] = val
            return buf

        if name == "diagonal":
            for page in range(8):
                for col in range(128):
                    phase = (col + page * 3) % 8
                    buf[page * 128 + col] = 1 << phase
            return buf

        # Default: checkerboard
        for page in range(8):
            for col in range(128):
                buf[page * 128 + col] = 0xAA if (col + page) % 2 == 0 else 0x55
        return buf

    def send_oled_framebuffer(self, pattern="Checkerboard"):
        """OLED Framebuffer: 1024 raw bytes -> 1171 wire bytes after packing."""
        payload = self._framebuffer_pattern(pattern)
        data = self._build_message(MSG_OLED_FRAMEBUFFER, payload=payload)
        return self._send_frame(data, f"OLED_FRAMEBUFFER ({pattern})")

    def send_raw_hex(self, text):
        """Send arbitrary hex bytes as SysEx (F0/F7 stripped if present)."""
        raw = str(text or "")
        if not raw.strip():
            return False, "empty hex input"

        sanitized = (
            raw.replace(",", " ")
            .replace("0x", " ")
            .replace("0X", " ")
            .replace("\n", " ")
            .replace("\t", " ")
        )

        vals = []
        for token in sanitized.split():
            try:
                val = int(token, 16)
            except ValueError:
                return False, f"invalid hex token: {token}"
            if not (0 <= val <= 255):
                return False, f"byte out of range: {token}"
            vals.append(val)

        if not vals:
            return False, "no bytes parsed"

        if vals[0] == 0xF0:
            vals = vals[1:]
        if vals and vals[-1] == 0xF7:
            vals = vals[:-1]

        if not vals:
            return False, "no payload bytes after removing F0/F7"

        return self._send_frame(vals, "RAW")

    # -------------------- Inbound Event Processing --------------------

    def _channel_match(self, event_channel):
        if self.expected_channel == -1:
            return True
        return int(event_channel) == int(self.expected_channel)

    def _decode_protocol_sysex(self, data):
        """Decode incoming SysEx and check if it matches OXI remote protocol."""
        prefix_ok = len(data) >= len(OXI_HEADER) and list(data[:len(OXI_HEADER)]) == OXI_HEADER
        if not prefix_ok:
            return {
                "is_protocol": False,
                "summary": "NON_OXI_SYSEX",
                "is_ack": False,
            }

        body = list(data[len(OXI_HEADER):])
        if len(body) < 2:
            return {
                "is_protocol": False,
                "summary": "OXI_SHORT_BODY",
                "is_ack": False,
            }

        if body[0] != REMOTE_CATEGORY:
            return {
                "is_protocol": False,
                "summary": f"OXI_NON_REMOTE_CAT_{body[0]:02X}",
                "is_ack": False,
            }

        msg_id = body[1]
        payload = body[2:]
        summary = f"OXI_REMOTE cat=06 id={msg_id:02X} ({MSG_NAMES.get(msg_id, 'UNKNOWN')}) payload={len(payload)}B"
        is_ack = (msg_id == MSG_REMOTE_ACK)

        return {
            "is_protocol": True,
            "summary": summary,
            "is_ack": is_ack,
        }

    def handle_event(self, event):
        """Process an incoming MIDI event for the remote protocol monitor.

        Tracks: SysEx (protocol ACKs), encoder CCs (CC 1-16 on expected channel),
        button notes (notes 0-15), SHIFT note (note 16).
        """
        etype = event.get("type")

        if etype == "sysex":
            data = list(event.get("data", []))
            decoded = self._decode_protocol_sysex(data)
            line = _fmt_sysex(data)
            self.last_rx = line

            if decoded["is_protocol"]:
                self.protocol_sysex_rx += 1
                if decoded["is_ack"]:
                    self.ack_count += 1
                self.last_event = decoded["summary"]
                self._log(f"RX {decoded['summary']}: {line}")
            else:
                self.other_sysex_rx += 1
                self.last_event = decoded["summary"]
                self._log(f"RX {decoded['summary']}: {line}")
            return

        if etype == "cc":
            ch = event.get("channel")
            if ch is None or not self._channel_match(ch):
                return
            cc_num = int(event.get("cc", -1))
            cc_val = int(event.get("value", 0))
            if 1 <= cc_num <= 16:
                self.encoder_cc_count += 1
                self.last_event = f"ENCODER_CC enc={cc_num - 1} value={cc_val} ch={ch + 1}"
                self._log(f"RX {self.last_event}")
            return

        if etype == "note":
            ch = event.get("channel")
            if ch is None or not self._channel_match(ch):
                return
            note = int(event.get("note", -1))
            vel = int(event.get("velocity", 0))
            edge = "on" if vel > 0 else "off"
            if 0 <= note <= 15:
                self.button_note_count += 1
                self.last_event = f"BUTTON_NOTE note={note} {edge} ch={ch + 1}"
                self._log(f"RX {self.last_event}")
            elif note == 16:
                self.shift_note_count += 1
                self.last_event = f"SHIFT_NOTE note=16 {edge} ch={ch + 1}"
                self._log(f"RX {self.last_event}")
            return

    # -------------------- Automated Suite --------------------

    def _append_suite_result(self, name, passed, detail):
        self.suite_results.append({
            "name": name,
            "status": "PASS" if passed else "FAIL",
            "detail": detail,
        })

    def _current_step(self):
        if not self.suite_running:
            return None
        if self.suite_index < 0 or self.suite_index >= len(self.suite_steps):
            return None
        return self.suite_steps[self.suite_index]

    def _advance_suite(self):
        self.suite_manual_pending = False
        self._manual_decision = None
        self.suite_index += 1

        if self.suite_index >= len(self.suite_steps):
            self.suite_running = False
            self._log("SUITE completed")
            return

        self._begin_step(self.suite_steps[self.suite_index])

    def _begin_step(self, step):
        step["started_at"] = time.perf_counter()
        on_start = step.get("on_start")
        if on_start:
            on_start()

        action = step.get("action")
        if action:
            ok, msg = action()
            step["action_msg"] = msg
            if not ok:
                self._append_suite_result(step["name"], False, f"action failed: {msg}")
                self._advance_suite()
                return

        if step.get("manual", False):
            self.suite_manual_pending = True
            return

        if not step.get("check"):
            self._append_suite_result(step["name"], True, step.get("pass_msg", "sent"))
            self._advance_suite()

    def start_full_suite(self):
        if self.suite_running:
            return False, "suite already running"

        self.suite_results = []
        self.suite_steps = []
        self.suite_index = 0
        self.suite_manual_pending = False
        self._manual_decision = None

        baselines = {"ack": 0, "enc": 0, "btn": 0, "shift": 0}

        def mark_ack_base():
            baselines["ack"] = self.ack_count

        def mark_enc_base():
            baselines["enc"] = self.encoder_cc_count

        def mark_btn_base():
            baselines["btn"] = self.button_note_count

        def mark_shift_base():
            baselines["shift"] = self.shift_note_count

        self.suite_steps = [
            {
                "name": "Enter remote mode",
                "action": self.send_enter_remote,
                "on_start": mark_ack_base,
                "check": lambda: self.ack_count > baselines["ack"],
                "timeout_s": 3.0,
                "pass_msg": "ACK received",
                "fail_msg": "No enter ACK seen",
            },
            {
                "name": "LED batch",
                "action": self.send_led_particular_demo,
                "pass_msg": "Sent LED RGB batch",
            },
            {
                "name": "Verify LED visual",
                "manual": True,
                "timeout_s": 30.0,
                "prompt": "Confirm individual LEDs rendered correctly, then click Manual PASS/FAIL.",
            },
            {
                "name": "OLED labels",
                "action": self.send_oled_labels_demo,
                "pass_msg": "Sent title + 16 labels",
            },
            {
                "name": "Verify OLED labels",
                "manual": True,
                "timeout_s": 30.0,
                "prompt": "Confirm title + labels layout, then click Manual PASS/FAIL.",
            },
            {
                "name": "OLED framebuffer checkerboard",
                "action": lambda: self.send_oled_framebuffer("Checkerboard"),
                "pass_msg": "Sent 1024-byte framebuffer pattern",
            },
            {
                "name": "Verify OLED framebuffer",
                "manual": True,
                "timeout_s": 30.0,
                "prompt": "Confirm full-screen framebuffer update, then click Manual PASS/FAIL.",
            },
            {
                "name": "SHIFT note events",
                "on_start": mark_shift_base,
                "check": lambda: self.shift_note_count >= baselines["shift"] + 2,
                "timeout_s": 30.0,
                "pass_msg": "Received SHIFT note on/off",
                "fail_msg": "No SHIFT note on/off detected",
                "prompt": "Press and release SHIFT now.",
            },
            {
                "name": "Encoder CC events",
                "on_start": mark_enc_base,
                "check": lambda: self.encoder_cc_count > baselines["enc"],
                "timeout_s": 30.0,
                "pass_msg": "Received encoder CC",
                "fail_msg": "No encoder CC detected",
                "prompt": "Turn any encoder now.",
            },
            {
                "name": "Button note events",
                "on_start": mark_btn_base,
                "check": lambda: self.button_note_count >= baselines["btn"] + 2,
                "timeout_s": 30.0,
                "pass_msg": "Received button note on/off",
                "fail_msg": "No button note on/off detected",
                "prompt": "Press and release any encoder button now.",
            },
            {
                "name": "Exit remote mode",
                "action": self.send_exit_remote,
                "pass_msg": "Sent EXIT REMOTE",
            },
        ]

        self.suite_running = True
        self._begin_step(self.suite_steps[0])
        self._log("SUITE started")
        return True, "suite started"

    def stop_suite(self, reason="stopped"):
        if not self.suite_running:
            return False, "suite not running"
        self.suite_running = False
        self.suite_manual_pending = False
        self._manual_decision = None
        self._log(f"SUITE stopped: {reason}")
        return True, reason

    def mark_manual_step(self, passed):
        step = self._current_step()
        if not step or not step.get("manual"):
            return False, "no manual step active"
        self._manual_decision = bool(passed)
        return True, "recorded"

    def tick(self):
        step = self._current_step()
        if not step:
            return

        now = time.perf_counter()
        timeout_s = float(step.get("timeout_s", 0.0) or 0.0)

        if step.get("manual"):
            if self._manual_decision is not None:
                passed = bool(self._manual_decision)
                detail = "manual confirmation" if passed else "manual failure"
                self._append_suite_result(step["name"], passed, detail)
                self._advance_suite()
                return

            if timeout_s > 0 and (now - step.get("started_at", now)) >= timeout_s:
                self._append_suite_result(step["name"], False, "manual timeout")
                self._advance_suite()
            return

        check = step.get("check")
        if check and check():
            self._append_suite_result(step["name"], True, step.get("pass_msg", "ok"))
            self._advance_suite()
            return

        if timeout_s > 0 and (now - step.get("started_at", now)) >= timeout_s:
            self._append_suite_result(step["name"], False, step.get("fail_msg", "timeout"))
            self._advance_suite()

    # -------------------- Snapshot --------------------

    def get_status_snapshot(self):
        step = self._current_step()
        current_name = step["name"] if step else "-"
        current_prompt = step.get("prompt", "") if step else ""
        progress = f"{min(self.suite_index + (1 if self.suite_running else 0), len(self.suite_steps))}/{len(self.suite_steps)}"

        return {
            "expected_channel": self.expected_channel,
            "protocol_sysex_rx": self.protocol_sysex_rx,
            "other_sysex_rx": self.other_sysex_rx,
            "ack_count": self.ack_count,
            "encoder_cc_count": self.encoder_cc_count,
            "button_note_count": self.button_note_count,
            "shift_note_count": self.shift_note_count,
            "last_tx": self.last_tx,
            "last_rx": self.last_rx,
            "last_event": self.last_event,
            "suite_running": self.suite_running,
            "suite_progress": progress,
            "suite_current": current_name,
            "suite_prompt": current_prompt,
            "suite_manual_pending": self.suite_manual_pending,
            "suite_results": list(self.suite_results),
        }
