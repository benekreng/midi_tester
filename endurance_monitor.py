import time
from collections import deque

class EnduranceLatencyMonitor:
    def __init__(self, midi_backend, interval_s=0.1, probe_notes=None, velocity=100,
                 note_length_ms=50, max_points=20000):
        self.midi = midi_backend

        self.enabled = False
        self.interval_s = float(interval_s)
        self.probe_notes = list(probe_notes) if probe_notes else [60, 64, 67, 72]
        self.velocity = int(velocity)
        self.note_length_s = max(0.0, note_length_ms / 1000.0)
        self.max_points = max_points

        self.start_time = None
        self.next_probe_time = None
        self.active_probe = None
        self.note_off_schedule = deque()

        self.results_x = deque(maxlen=max_points)
        self.results_y = deque(maxlen=max_points)
        self.last_result = None
        self.missed_probes = 0
        self.probes_sent = 0
        self._plot_dirty = False
        self._new_result = False

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

    def stop(self):
        self.enabled = False
        self.active_probe = None
        self.note_off_schedule.clear()

    def clear_results(self):
        self.results_x.clear()
        self.results_y.clear()
        self.last_result = None
        self.missed_probes = 0
        self.probes_sent = 0
        self._plot_dirty = True
        self._new_result = False

    def set_interval(self, seconds):
        # Allow intervals from 1ms (0.001s) to 1000ms (1.0s)
        self.interval_s = max(0.001, min(1.0, float(seconds)))

    def set_probe_notes(self, notes):
        clean = []
        for n in notes:
            try:
                val = int(n)
            except (ValueError, TypeError):
                continue
            if 0 <= val <= 127 and val not in clean:
                clean.append(val)
        if clean:
            self.probe_notes = clean

    def get_timeout_s(self):
        # Keep timeout comfortably under interval to avoid overlap
        # For very fast intervals (< 10ms), use 80% of interval
        # Otherwise use at least 5ms but no more than 80% of interval
        return max(0.005, min(2.0, self.interval_s * 0.8))

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
            # If we're late, consider the previous probe missed
            self._finalize_probe(complete=False)

        channel = 0 if selected_channel == -1 else int(selected_channel)
        self._send_probe(now, channel)
        self.next_probe_time = now + self.interval_s

    def _send_probe(self, now, channel):
        expected = set(self.probe_notes)
        if not expected:
            return

        for note in expected:
            self.midi.send_note(channel, note, self.velocity)
            if self.note_length_s > 0:
                self.note_off_schedule.append((now + self.note_length_s, note, channel))

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
        expected = probe['expected']

        for e in events:
            if e.get('type') != 'note':
                continue
            if e.get('velocity', 0) <= 0:
                continue
            if e.get('channel') != probe['channel']:
                continue

            note = e.get('note')
            if note not in expected:
                continue
            if note in probe['received']:
                continue

            event_time = e.get('timestamp', now)
            probe['received'][note] = event_time

            if len(probe['received']) == len(expected):
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

        # Debug logging for 0 spread cases
        if spread_ms < 0.001:
            print(f"DEBUG: Very low spread {spread_ms:.6f}ms - Times: {sorted(recv_times)}")
            time_diffs = [f"{(recv_times[i] - recv_times[i-1])*1e6:.3f}µs" 
                          for i in range(1, len(recv_times))]
            print(f"  Time differences: {', '.join(time_diffs)}")

        self.results_x.append(elapsed_min)
        self.results_y.append(spread_ms)
        self.last_result = {
            'round_trip_ms': round_trip_ms,
            'spread_ms': spread_ms,
            'elapsed_min': elapsed_min,
        }
        self._plot_dirty = True
        self._new_result = True

    def get_plot_data(self):
        return list(self.results_x), list(self.results_y)

    def consume_plot_dirty(self):
        if self._plot_dirty:
            self._plot_dirty = False
            return True
        return False

    def get_status(self):
        now = time.perf_counter()
        elapsed_min = 0.0
        if self.enabled and self.start_time:
            elapsed_min = (now - self.start_time) / 60.0
        return {
            'enabled': self.enabled,
            'elapsed_min': elapsed_min,
            'interval_s': self.interval_s,
            'probe_notes': list(self.probe_notes),
            'probes_sent': self.probes_sent,
            'missed_probes': self.missed_probes,
            'last_result': self.last_result,
        }
