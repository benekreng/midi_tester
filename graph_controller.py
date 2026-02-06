import math
from typing import Dict, List, Tuple, Union, Optional

SeriesData = Union[Tuple[List[float], List[float]], Dict[str, Tuple[List[float], List[float]]]]

class PlotController:
    def __init__(self, name: str, default_window_min: float = 5.0):
        self.name = name
        self.paused = False
        self.zoom_enabled = False
        self.follow_live = True
        self.x_window_min = default_window_min
        self.x_scroll = 1.0

        self.y_mode = "StdDev"  # StdDev | Percentile | Manual
        self.y_window_mode = "All"  # All | Last N
        self.y_last_n = 1000
        self.y_k = 2.0
        self.y_p_low = 5.0
        self.y_p_high = 95.0
        self.y_min = 0.0
        self.y_max = 100.0

        self._paused_data: Optional[SeriesData] = None
        self._last_view_x = (0.0, 1.0)
        self._last_view_y = (0.0, 1.0)

    def set_pause(self, value: bool):
        self.paused = bool(value)
        if not self.paused:
            self._paused_data = None

    def set_zoom(self, enabled: bool):
        self.zoom_enabled = bool(enabled)
        if self.zoom_enabled:
            self.set_pause(True)
            self.follow_live = False
        else:
            self.set_pause(False)
            self.follow_live = True
            self.x_scroll = 1.0

    def update(self, data: SeriesData, force: bool = False):
<<<<<<< ours
        if self.paused and not force:
=======
        if self.paused:
>>>>>>> theirs
            if self._paused_data is None:
                self._paused_data = self._snapshot(data)
            data = self._paused_data
        elif not self.paused:
            self._paused_data = None

        view_data, x_min, x_max = self._apply_x_view(data)
        y_min, y_max = self._compute_y_range(view_data)

        self._last_view_x = (x_min, x_max)
        self._last_view_y = (y_min, y_max)
        return view_data, (x_min, x_max), (y_min, y_max)

    def _snapshot(self, data: SeriesData) -> SeriesData:
        if isinstance(data, dict):
            snap = {}
            for key, (x, y) in data.items():
                snap[key] = (list(x), list(y))
            return snap
        return (list(data[0]), list(data[1]))

    def _apply_x_view(self, data: SeriesData):
        x_min, x_max = self._data_x_range(data)
        if x_max <= x_min:
            x_min, x_max = 0.0, 1.0

        if self.zoom_enabled and self.x_window_min > 0.0:
            window = min(self.x_window_min, max(0.000001, x_max - x_min))
            if self.follow_live and not self.paused:
                x_max = x_max
                x_min = x_max - window
            else:
                span = max(0.0, (x_max - x_min) - window)
                start = x_min + span * max(0.0, min(1.0, self.x_scroll))
                x_min = start
                x_max = start + window
        else:
            if self.paused:
                x_min, x_max = self._last_view_x

        return self._filter_by_x(data, x_min, x_max), x_min, x_max

    def _filter_by_x(self, data: SeriesData, x_min: float, x_max: float):
        if isinstance(data, dict):
            filtered = {}
            for key, (x, y) in data.items():
                fx, fy = self._filter_series(x, y, x_min, x_max)
                filtered[key] = (fx, fy)
            return filtered
        fx, fy = self._filter_series(data[0], data[1], x_min, x_max)
        return (fx, fy)

    def _filter_series(self, x: List[float], y: List[float], x_min: float, x_max: float):
        if not x or not y:
            return [], []
        fx, fy = [], []
        for xi, yi in zip(x, y):
            if x_min <= xi <= x_max:
                fx.append(xi)
                fy.append(yi)
        return fx, fy

    def _data_x_range(self, data: SeriesData):
        x_vals = []
        if isinstance(data, dict):
            for x, _ in data.values():
                x_vals.extend(x)
        else:
            x_vals = list(data[0])
        if not x_vals:
            return 0.0, 1.0
        return min(x_vals), max(x_vals)

    def _collect_y_values(self, data: SeriesData):
        points = []
        if isinstance(data, dict):
            for x, y in data.values():
                points.extend(zip(x, y))
        else:
            points.extend(zip(data[0], data[1]))

        if not points:
            return []
        points.sort(key=lambda p: p[0])

        if self.y_window_mode == "Last N":
            points = points[-max(1, int(self.y_last_n)) :]
        return [p[1] for p in points]

    def _compute_y_range(self, data: SeriesData):
        y_vals = self._collect_y_values(data)
        if not y_vals:
            return self._last_view_y

        if self.y_mode == "Manual":
            y_min = float(self.y_min)
            y_max = float(self.y_max)
        elif self.y_mode == "Percentile":
            y_min, y_max = self._percentile_range(y_vals)
        else:
            y_min, y_max = self._stddev_range(y_vals)

        if y_max <= y_min:
            y_max = y_min + 1.0
        return y_min, y_max

    def _stddev_range(self, y_vals: List[float]):
        mean = sum(y_vals) / len(y_vals)
        var = sum((v - mean) ** 2 for v in y_vals) / max(1, (len(y_vals) - 1))
        std = math.sqrt(var)
        k = max(0.1, float(self.y_k))
        return mean - k * std, mean + k * std

    def _percentile_range(self, y_vals: List[float]):
        sorted_vals = sorted(y_vals)
        n = len(sorted_vals)
        low = max(0.0, min(100.0, float(self.y_p_low)))
        high = max(0.0, min(100.0, float(self.y_p_high)))
        if high < low:
            low, high = high, low

        low_idx = int((low / 100.0) * (n - 1))
        high_idx = int((high / 100.0) * (n - 1))
        return sorted_vals[low_idx], sorted_vals[high_idx]
