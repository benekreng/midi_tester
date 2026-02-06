import time
import math
from collections import deque

from message_types import (
    TYPE_NOTE,
    TYPE_CC,
    TYPE_CC14,
    TYPE_NRPN,
    TYPE_PC,
    TYPE_PB,
    PROBE_DEFAULTS,
    random_spec,
    event_to_spec,
)

class EnduranceLatencyMonitor:
    def __init__(self, midi_backend, interval_s=5.0, probe_notes=None, velocity=100,
                 note_length_ms=50, max_points=20000):
        self.midi = midi_backend

        self.enabled = False
        self.min_interval_s = 0.001
        self.max_interval_s = 5.0
        self.interval_s = float(interval_s)
        self.probe_notes = list(probe_notes) if probe_notes else [60, 64, 67, 72]
        self.velocity = int(velocity)
        self.note_length_s = max(0.0, note_length_ms / 1000.0)
        self.max_points = max_points
        self.probe_types = [TYPE_NOTE]
        self.mod_enabled = False
        self.mod_freq_hz = 0.5
        self.mod_depth_s = 0.0

        self.start_time = None
        self.next_probe_time = None
        self.active_probe = None
        self.note_off_schedule = deque()

        self.results_x = deque(maxlen=max_points)
        self.results_y = deque(maxlen=max_points)
        self.offsets_x = {}
        self.offsets_y = {}
        self.offset_labels = []
        self.last_result = None
        self.missed_probes = 0
        self.probes_sent = 0
        self._plot_dirty = False
        self._new_result = False
        self._init_offset_buffers()

    def set_enabled(self, enabled):
        if enabled and not self.enabled:
            self.start()
        elif not enabled and self.enabled:
            self.stop()

    def start(self):
        now = time.perf_counter()
        self.enabled = True
        self.start_time = now
        self.next_probe_time = now
        self.active_probe = None
        self.note_off_schedule.clear()
        self.results_x.clear()
        self.results_y.clear()
        self.last_result = None
        self.missed_probes = 0
        self.probes_sent = 0
        self._plot_dirty = True
        self._new_result = False
        self._init_offset_buffers()

    def stop(self):
        self.enabled = False
        self.active_probe = None
        self.note_off_schedule.clear()

    def clear_results(self):
        self.results_x.clear()
        self.results_y.clear()
        self._init_offset_buffers()
        self.last_result = None
        self.missed_probes = 0
        self.probes_sent = 0
        self._plot_dirty = True
        self._new_result = False

    def set_interval(self, seconds):
        self.interval_s = max(self.min_interval_s, min(self.max_interval_s, float(seconds)))
        self._reschedule_next()

    def set_modulation(self, enabled=None, freq_hz=None, depth_ms=None):
        if enabled is not None:
            self.mod_enabled = bool(enabled)
        if freq_hz is not None:
            self.mod_freq_hz = max(0.0, float(freq_hz))
        if depth_ms is not None:
            self.mod_depth_s = max(0.0, float(depth_ms) / 1000.0)
        self._reschedule_next()

    def set_probe_notes(self, notes):
        clean = []
        for n in notes:
            try:
                val = int(n)
            except (ValueError, TypeError):
                continue
            if 0 <= val <= 127 and val not in clean:
                clean.append(val)
        if clean and clean != self.probe_notes:
            self.probe_notes = clean
            self.active_probe = None
            self.note_off_schedule.clear()
            self.clear_results()

    def set_probe_types(self, types):
        clean = []
        for t in types:
            if t and t not in clean:
                clean.append(t)
        if clean and clean != self.probe_types:
            self.probe_types = clean
            self.active_probe = None
            self.note_off_schedule.clear()
            self.clear_results()

    def get_timeout_s(self):
        return max(self.min_interval_s, min(2.0, self.interval_s * 0.8))

    def tick(self, events, selected_channel):
        if not self.enabled:
            return None

        now = time.perf_counter()
        self._flush_note_offs(now)
        self._process_events(events, now)
        self._check_timeout(now)
        self._maybe_send_probe(now, selected_channel)

        if self._new_result:
            self._new_result = False
            return self.last_result
        return None

    def _flush_note_offs(self, now):
        while self.note_off_schedule and self.note_off_schedule[0][0] <= now:
            _, note, channel = self.note_off_schedule.popleft()
            self.midi.send_note(channel, note, 0)

    def _maybe_send_probe(self, now, selected_channel):
        if self.next_probe_time is None:
            self.next_probe_time = now

        if now < self.next_probe_time:
            return

        if self.active_probe:
            self._finalize_probe(complete=False)

        channel = 0 if selected_channel == -1 else int(selected_channel)
        self._send_probe(now, channel)
        self.next_probe_time = now + self._current_interval(now)

    def _send_probe(self, now, channel):
        expected = {}
        for mtype in self.probe_types:
            if mtype == TYPE_NOTE:
                for note in self.probe_notes:
                    spec = random_spec(
                        TYPE_NOTE,
                        channel,
                        vary_number=False,
                        vary_value=False,
                        base_number=note,
                        base_value=self.velocity,
                    )
                    expected[spec.identity()] = self._label_for_spec(spec)
                    self.midi.send_note(channel, spec.number, spec.value)
                    if self.note_length_s > 0:
                        self.note_off_schedule.append((now + self.note_length_s, spec.number, channel))
            else:
                spec = random_spec(
                    mtype,
                    channel,
                    vary_number=False,
                    vary_value=False,
                    base_number=PROBE_DEFAULTS[mtype]["number"],
                    base_value=PROBE_DEFAULTS[mtype]["value"],
                )
                expected[spec.identity()] = self._label_for_spec(spec)
                self._send_spec(spec)

        if not expected:
            return

        self.active_probe = {
            'send_time': now,
            'deadline': now + self.get_timeout_s(),
            'channel': channel,
            'expected': expected,
            'received': {},
        }
        self.probes_sent += 1

    def _process_events(self, events, now):
        if not self.active_probe:
            return

        probe = self.active_probe

        for e in events:
            spec = event_to_spec(e)
            if not spec:
                continue
            identity = spec.identity()
            if identity not in probe['expected']:
                continue
            label = probe['expected'][identity]
            if label in probe['received']:
                continue

            event_time = e.get('timestamp', now)
            probe['received'][label] = event_time

            if len(probe['received']) == len(probe['expected']):
                self._finalize_probe(complete=True)
                break

    def _check_timeout(self, now):
        if not self.active_probe:
            return
        if now >= self.active_probe['deadline']:
            self._finalize_probe(complete=False)

    def _finalize_probe(self, complete):
        probe = self.active_probe
        self.active_probe = None

        if not probe:
            return

        if not complete or not probe['received']:
            self.missed_probes += 1
            return

        recv_times = list(probe['received'].values())
        first_time = min(recv_times)
        last_time = max(recv_times)

        round_trip_ms = max(0.0, (first_time - probe['send_time']) * 1000.0)
        spread_ms = max(0.0, (last_time - first_time) * 1000.0)
        elapsed_min = (probe['send_time'] - self.start_time) / 60.0 if self.start_time else 0.0

        self.results_x.append(elapsed_min)
        self.results_y.append(spread_ms)
        for label, t in probe['received'].items():
            offset_ms = max(0.0, (t - first_time) * 1000.0)
            if label not in self.offsets_x:
                self.offsets_x[label] = deque(maxlen=self.max_points)
                self.offsets_y[label] = deque(maxlen=self.max_points)
            self.offsets_x[label].append(elapsed_min)
            self.offsets_y[label].append(offset_ms)
        self.last_result = {
            'round_trip_ms': round_trip_ms,
            'spread_ms': spread_ms,
            'elapsed_min': elapsed_min,
        }
        self._plot_dirty = True
        self._new_result = True

    def get_plot_data(self):
        return list(self.results_x), list(self.results_y)

    def get_offset_plot_data(self):
        data = {}
        for label in self.offset_labels:
            xs = list(self.offsets_x.get(label, []))
            ys = list(self.offsets_y.get(label, []))
            data[label] = (xs, ys)
        return data

    def consume_plot_dirty(self):
        if self._plot_dirty:
            self._plot_dirty = False
            return True
        return False

    def _init_offset_buffers(self):
        self.offset_labels = self._build_offset_labels()
        self.offsets_x = {label: deque(maxlen=self.max_points) for label in self.offset_labels}
        self.offsets_y = {label: deque(maxlen=self.max_points) for label in self.offset_labels}

    def _current_interval(self, now):
        base = self.interval_s
        if not self.mod_enabled or self.mod_freq_hz <= 0.0 or self.mod_depth_s <= 0.0:
            return base
        phase_t = 0.0 if self.start_time is None else (now - self.start_time)
        mod = self.mod_depth_s * math.sin(2.0 * math.pi * self.mod_freq_hz * phase_t)
        interval = base + mod
        interval = max(self.min_interval_s, min(self.max_interval_s, interval))
        return interval

    def _reschedule_next(self):
        if not self.enabled:
            return
        now = time.perf_counter()
        self.next_probe_time = now + self._current_interval(now)

    def _build_offset_labels(self):
        labels = []
        for mtype in self.probe_types:
            if mtype == TYPE_NOTE:
                for note in self.probe_notes:
                    labels.append(f"Note {note}")
            elif mtype == TYPE_CC:
                labels.append(f"CC {PROBE_DEFAULTS[mtype]['number']}")
            elif mtype == TYPE_CC14:
                labels.append(f"CC14 {PROBE_DEFAULTS[mtype]['number']}")
            elif mtype == TYPE_NRPN:
                labels.append(f"NRPN {PROBE_DEFAULTS[mtype]['number']}")
            elif mtype == TYPE_PC:
                labels.append(f"PC {PROBE_DEFAULTS[mtype]['number']}")
            elif mtype == TYPE_PB:
                labels.append("PB")
        return labels

    def _label_for_spec(self, spec):
        if spec.mtype == TYPE_NOTE:
            return f"Note {spec.number}"
        if spec.mtype == TYPE_CC:
            return f"CC {spec.number}"
        if spec.mtype == TYPE_CC14:
            return f"CC14 {spec.number}"
        if spec.mtype == TYPE_NRPN:
            return f"NRPN {spec.number}"
        if spec.mtype == TYPE_PC:
            return f"PC {spec.number}"
        if spec.mtype == TYPE_PB:
            return "PB"
        return spec.mtype

    def _send_spec(self, spec):
        ch = spec.channel
        if spec.mtype == TYPE_CC:
            self.midi.send_cc(ch, spec.number, spec.value)
        elif spec.mtype == TYPE_CC14:
            self.midi.send_cc14(ch, spec.number, spec.value)
        elif spec.mtype == TYPE_NRPN:
            self.midi.send_nrpn(ch, spec.number, spec.value)
        elif spec.mtype == TYPE_PC:
            self.midi.send_program_change(ch, spec.number)
        elif spec.mtype == TYPE_PB:
            self.midi.send_pitch_bend(ch, spec.value)

    def get_status(self):
        now = time.perf_counter()
        elapsed_min = 0.0
        if self.enabled and self.start_time:
            elapsed_min = (now - self.start_time) / 60.0
        return {
            'enabled': self.enabled,
            'elapsed_min': elapsed_min,
            'interval_s': self.interval_s,
            'mod_enabled': self.mod_enabled,
            'mod_freq_hz': self.mod_freq_hz,
            'mod_depth_s': self.mod_depth_s,
            'probe_notes': list(self.probe_notes),
            'probe_types': list(self.probe_types),
            'probes_sent': self.probes_sent,
            'missed_probes': self.missed_probes,
            'last_result': self.last_result,
        }
