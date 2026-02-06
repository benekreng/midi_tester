import random

class TimingModel:
    def __init__(self):
        self.mode = "preset"  # preset | full
        self.preset = "Steady"
        self.intensity = 0.5

        self.base_rate = 20.0  # messages per second
        self.jitter_pct = 0.0
        self.burst_prob = 0.0
        self.burst_size_min = 2
        self.burst_size_max = 4
        self.min_gap_ms = 0.0
        self.max_gap_ms = 1000.0

        self._burst_remaining = 0

    def set_preset(self, name, intensity):
        self.mode = "preset"
        self.preset = name
        self.intensity = max(0.0, min(1.0, float(intensity)))

    def set_full(self, base_rate, jitter_pct, burst_prob, burst_size_min, burst_size_max, min_gap_ms, max_gap_ms):
        self.mode = "full"
        self.base_rate = max(0.1, float(base_rate))
        self.jitter_pct = max(0.0, min(1.0, float(jitter_pct)))
        self.burst_prob = max(0.0, min(1.0, float(burst_prob)))
        self.burst_size_min = max(1, int(burst_size_min))
        self.burst_size_max = max(self.burst_size_min, int(burst_size_max))
        self.min_gap_ms = max(0.0, float(min_gap_ms))
        self.max_gap_ms = max(self.min_gap_ms, float(max_gap_ms))

    def next_delay_s(self):
        if self.mode == "full":
            return self._next_delay_full()
        return self._next_delay_preset()

    def _start_burst(self, min_size, max_size):
        size = random.randint(min_size, max_size)
        self._burst_remaining = max(0, size - 1)

    def _next_delay_full(self):
        base_interval = 1.0 / self.base_rate
        if self._burst_remaining > 0:
            self._burst_remaining -= 1
            return max(self.min_gap_ms / 1000.0, 0.0)

        if random.random() < self.burst_prob:
            self._start_burst(self.burst_size_min, self.burst_size_max)
            return max(self.min_gap_ms / 1000.0, 0.0)

        jitter = (random.uniform(-1.0, 1.0) * self.jitter_pct) * base_interval
        delay = base_interval + jitter
        delay = max(delay, self.min_gap_ms / 1000.0)
        delay = min(delay, self.max_gap_ms / 1000.0)
        return max(0.0, delay)

    def _next_delay_preset(self):
        intensity = self.intensity
        preset = self.preset

        if preset == "Steady":
            base_rate = 5.0 + 45.0 * intensity
            jitter_pct = 0.0
            burst_prob = 0.0
            burst_size = (2, 3)
            min_gap_ms = 0.0
        elif preset == "Jitter":
            base_rate = 5.0 + 45.0 * intensity
            jitter_pct = 0.1 + 0.7 * intensity
            burst_prob = 0.0
            burst_size = (2, 4)
            min_gap_ms = 0.0
        elif preset == "Burst":
            base_rate = 5.0 + 25.0 * intensity
            jitter_pct = 0.05 + 0.2 * intensity
            burst_prob = 0.05 + 0.45 * intensity
            burst_size = (2, 2 + int(8 * intensity))
            min_gap_ms = 0.0
        else:  # Chaos
            base_rate = 10.0 + 60.0 * intensity
            jitter_pct = 0.2 + 0.8 * intensity
            burst_prob = 0.1 + 0.6 * intensity
            burst_size = (2, 4 + int(10 * intensity))
            min_gap_ms = 0.0

        base_interval = 1.0 / base_rate
        if self._burst_remaining > 0:
            self._burst_remaining -= 1
            return max(min_gap_ms / 1000.0, 0.0)

        if random.random() < burst_prob:
            self._start_burst(burst_size[0], burst_size[1])
            return max(min_gap_ms / 1000.0, 0.0)

        jitter = (random.uniform(-1.0, 1.0) * jitter_pct) * base_interval
        delay = max(0.0, base_interval + jitter)
        return delay
