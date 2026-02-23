import json
import os
import copy

DEFAULT_SETTINGS = {
    "connections": {
        "virtual": False,
        "input_port": "",
        "output_port": "",
        "channel": "1",
    },
    "main": {
        "feedback_mode": "None",
        "delay_ms": 200,
    },
    "latency": {
        "interval_s": 5.0,
        "mod_enabled": False,
        "mod_freq_hz": 0.5,
        "mod_depth_ms": 0.0,
        "probe_notes": "60, 64, 67, 72",
        "probe_types": ["note"],
        "random_type_count": 3,
    },
    "fuzz": {
        "enabled": False,
        "mode": "Single Type",
        "single_type": "cc",
        "vary_number": True,
        "vary_value": True,
        "random_channel": False,
        "allowed_types": ["note", "cc", "cc14", "nrpn", "pc", "pb"],
        "timing_mode": "Preset",
        "preset": "Steady",
        "intensity": 0.5,
        "base_rate": 20.0,
        "jitter": 0.0,
        "burst_prob": 0.0,
        "burst_min": 2,
        "burst_max": 4,
        "min_gap": 0.0,
        "max_gap": 1000.0,
    },
    "remote": {
        "expected_channel": "1",
        "led_anim_interval_ms": 120,
    },
}


def _deep_update(dst, src):
    for key, val in src.items():
        if isinstance(val, dict) and isinstance(dst.get(key), dict):
            _deep_update(dst[key], val)
        else:
            dst[key] = val


class SettingsStore:
    def __init__(self, path="settings.json"):
        self.path = path
        self.data = copy.deepcopy(DEFAULT_SETTINGS)

    def load(self):
        if not os.path.exists(self.path):
            return
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                incoming = json.load(fh)
            if isinstance(incoming, dict):
                _deep_update(self.data, incoming)
        except Exception:
            # If load fails, keep defaults
            pass

    def save(self):
        tmp_path = f"{self.path}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as fh:
            json.dump(self.data, fh, indent=2, sort_keys=True)
        os.replace(tmp_path, self.path)

    def reset_section(self, section):
        if section in DEFAULT_SETTINGS:
            self.data[section] = copy.deepcopy(DEFAULT_SETTINGS[section])
            self.save()
