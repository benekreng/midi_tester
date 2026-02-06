import time
import random
import heapq
from collections import deque

from message_types import (
    ALL_TYPES,
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
from timing_model import TimingModel

class RunningStats:
    def __init__(self):
        self.count = 0
        self.mean = 0.0
        self.m2 = 0.0
        self.min_val = None
        self.max_val = None

    def reset(self):
        self.count = 0
        self.mean = 0.0
        self.m2 = 0.0
        self.min_val = None
        self.max_val = None

    def update(self, value):
        self.count += 1
        delta = value - self.mean
        self.mean += delta / self.count
        delta2 = value - self.mean
        self.m2 += delta * delta2
        if self.min_val is None or value < self.min_val:
            self.min_val = value
        if self.max_val is None or value > self.max_val:
            self.max_val = value

    @property
    def variance(self):
        if self.count < 2:
            return 0.0
        return self.m2 / (self.count - 1)

    @property
    def stddev(self):
        return self.variance ** 0.5


class FuzzAnalyzer:
    def __init__(self, timeout_s=2.0, max_points=20000, max_missing=200):
        self.timeout_s = float(timeout_s)
        self.max_points = max_points
        self.max_missing = max_missing

        self.pending = {}
        self.deadlines = []
        self.results_x = deque(maxlen=max_points)
        self.results_y = deque(maxlen=max_points)
        self.missing_log = deque(maxlen=max_missing)
        self.stats = RunningStats()
        self.start_time = None
        self.last_latency_ms = None
        self.missed_count = 0
        self.received_count = 0
        self.sent_count = 0
        self._plot_dirty = False
        self._new_result = False

    def start(self):
        self.start_time = time.perf_counter()
        self.pending.clear()
        self.deadlines.clear()
        self.results_x.clear()
        self.results_y.clear()
        self.missing_log.clear()
        self.stats.reset()
        self.last_latency_ms = None
        self.missed_count = 0
        self.received_count = 0
        self.sent_count = 0
        self._plot_dirty = True
        self._new_result = False

    def stop(self):
        self.pending.clear()
        self.deadlines.clear()

    def register_sent(self, spec, send_time):
        if self.start_time is None:
            self.start_time = send_time
        identity = spec.identity()
        self.pending[identity] = (send_time, spec)
        deadline = send_time + self.timeout_s
        heapq.heappush(self.deadlines, (deadline, identity))
        self.sent_count += 1

    def process_events(self, events, now):
        for e in events:
            spec = event_to_spec(e)
            if not spec:
                continue
            identity = spec.identity()
            if identity not in self.pending:
                continue
            send_time, _ = self.pending.pop(identity)
            event_time = e.get('timestamp', now)
            latency_ms = max(0.0, (event_time - send_time) * 1000.0)
            elapsed_min = (send_time - self.start_time) / 60.0 if self.start_time else 0.0

            self.results_x.append(elapsed_min)
            self.results_y.append(latency_ms)
            self.stats.update(latency_ms)
            self.last_latency_ms = latency_ms
            self.received_count += 1
            self._plot_dirty = True
            self._new_result = True

    def check_timeouts(self, now):
        while self.deadlines and self.deadlines[0][0] <= now:
            _, identity = heapq.heappop(self.deadlines)
            if identity not in self.pending:
                continue
            send_time, spec = self.pending.pop(identity)
            self.missed_count += 1
            label = f"{spec.label()} @ {send_time:.3f}s"
            self.missing_log.append(label)

    def get_plot_data(self):
        return list(self.results_x), list(self.results_y)

    def get_stats(self):
        if self.stats.count == 0:
            mean = None
            stddev = None
            min_val = None
            max_val = None
            last = None
        else:
            mean = self.stats.mean
            stddev = self.stats.stddev
            min_val = self.stats.min_val
            max_val = self.stats.max_val
            last = self.last_latency_ms
        return {
            'mean': mean,
            'stddev': stddev,
            'min': min_val,
            'max': max_val,
            'last': last,
            'sent': self.sent_count,
            'received': self.received_count,
            'missing': self.missed_count,
            'pending': len(self.pending),
        }

    def consume_plot_dirty(self):
        if self._plot_dirty:
            self._plot_dirty = False
            return True
        return False

    def consume_new_result(self):
        if self._new_result:
            self._new_result = False
            return True
        return False


class FuzzGenerator:
    def __init__(self, midi_backend, timing_model: TimingModel, note_length_ms=100, max_send_per_tick=50):
        self.midi = midi_backend
        self.timing = timing_model
        self.note_length_s = max(0.0, note_length_ms / 1000.0)
        self.max_send_per_tick = max_send_per_tick

        self.enabled = False
        self.mode = "single"  # single | mixed | chaos
        self.single_type = TYPE_CC
        self.allowed_types = list(ALL_TYPES)
        self.vary_number = True
        self.vary_value = True
        self.randomize_channel = False

        self.next_send_time = None
        self.note_off_schedule = deque()
        self.sent_count = 0
        self._rng = random.Random()

    def start(self):
        now = time.perf_counter()
        self.enabled = True
        self.next_send_time = now
        self.note_off_schedule.clear()
        self.sent_count = 0

    def stop(self):
        self.enabled = False
        self.next_send_time = None
        self.note_off_schedule.clear()

    def tick(self, now, selected_channel, analyzer: FuzzAnalyzer):
        if not self.enabled:
            return

        self._flush_note_offs(now)

        if self.next_send_time is None:
            self.next_send_time = now

        send_count = 0
        while now >= self.next_send_time and send_count < self.max_send_per_tick:
            spec = self._generate_unique_spec(selected_channel, analyzer)
            if not spec:
                break
            self._send_spec(spec, now)
            analyzer.register_sent(spec, now)
            self.sent_count += 1
            send_count += 1
            self.next_send_time = now + self.timing.next_delay_s()

    def _flush_note_offs(self, now):
        while self.note_off_schedule and self.note_off_schedule[0][0] <= now:
            _, note, channel = self.note_off_schedule.popleft()
            self.midi.send_note(channel, note, 0)

    def _send_spec(self, spec, now):
        ch = spec.channel
        if spec.mtype == TYPE_NOTE:
            self.midi.send_note(ch, spec.number, spec.value)
            if self.note_length_s > 0:
                self.note_off_schedule.append((now + self.note_length_s, spec.number, ch))
        elif spec.mtype == TYPE_CC:
            self.midi.send_cc(ch, spec.number, spec.value)
        elif spec.mtype == TYPE_CC14:
            self.midi.send_cc14(ch, spec.number, spec.value)
        elif spec.mtype == TYPE_NRPN:
            self.midi.send_nrpn(ch, spec.number, spec.value)
        elif spec.mtype == TYPE_PC:
            self.midi.send_program_change(ch, spec.number)
        elif spec.mtype == TYPE_PB:
            self.midi.send_pitch_bend(ch, spec.value)

    def _generate_unique_spec(self, selected_channel, analyzer: FuzzAnalyzer):
        attempts = 0
        while attempts < 50:
            spec = self._generate_spec(selected_channel)
            if spec.identity() not in analyzer.pending:
                return spec
            attempts += 1
        return None

    def _generate_spec(self, selected_channel):
        if self.mode == "single":
            mtype = self.single_type
        elif self.mode == "mixed":
            mtype = self._rng.choice(self.allowed_types) if self.allowed_types else self.single_type
        else:
            mtype = self._rng.choice(ALL_TYPES)

        if self.randomize_channel:
            channel = self._rng.randint(0, 15)
        else:
            channel = 0 if selected_channel == -1 else int(selected_channel)

        if self.mode == "single":
            return random_spec(
                mtype,
                channel,
                rng=self._rng,
                vary_number=self.vary_number,
                vary_value=self.vary_value,
                base_number=PROBE_DEFAULTS[mtype]["number"],
                base_value=PROBE_DEFAULTS[mtype]["value"],
            )

        return random_spec(mtype, channel, rng=self._rng, vary_number=True, vary_value=True)


class FuzzTest:
    def __init__(self, midi_backend, timeout_s=2.0):
        self.timing = TimingModel()
        self.analyzer = FuzzAnalyzer(timeout_s=timeout_s)
        self.generator = FuzzGenerator(midi_backend, self.timing)

    def set_enabled(self, enabled):
        if enabled and not self.generator.enabled:
            self.analyzer.start()
            self.generator.start()
        elif not enabled and self.generator.enabled:
            self.generator.stop()
            self.analyzer.stop()

    def tick(self, events, selected_channel):
        now = time.perf_counter()
        if self.generator.enabled:
            self.analyzer.process_events(events, now)
            self.analyzer.check_timeouts(now)
            self.generator.tick(now, selected_channel, self.analyzer)

    def get_plot_data(self):
        return self.analyzer.get_plot_data()

    def get_stats(self):
        return self.analyzer.get_stats()

    def consume_plot_dirty(self):
        return self.analyzer.consume_plot_dirty()

    def get_missing_log(self):
        return list(self.analyzer.missing_log)
