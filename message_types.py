import random
from dataclasses import dataclass
from typing import Optional

TYPE_NOTE = "note"
TYPE_CC = "cc"
TYPE_CC14 = "cc14"
TYPE_NRPN = "nrpn"
TYPE_PC = "pc"
TYPE_PB = "pb"

ALL_TYPES = [TYPE_NOTE, TYPE_CC, TYPE_CC14, TYPE_NRPN, TYPE_PC, TYPE_PB]

TYPE_RANGES = {
    TYPE_NOTE: {"number": (0, 127), "value": (1, 127)},
    TYPE_CC: {"number": (0, 127), "value": (0, 127)},
    TYPE_CC14: {"number": (0, 31), "value": (0, 16383)},
    TYPE_NRPN: {"number": (0, 16383), "value": (0, 16383)},
    TYPE_PC: {"number": (0, 127), "value": (0, 0)},
    TYPE_PB: {"number": (0, 0), "value": (0, 16383)},
}

PROBE_DEFAULTS = {
    TYPE_NOTE: {"number": 60, "value": 100},
    TYPE_CC: {"number": 74, "value": 64},
    TYPE_CC14: {"number": 1, "value": 8192},
    TYPE_NRPN: {"number": 1300, "value": 8192},
    TYPE_PC: {"number": 10, "value": 0},
    TYPE_PB: {"number": 0, "value": 8192},
}

LABELS = {
    TYPE_NOTE: "Note",
    TYPE_CC: "CC 7-bit",
    TYPE_CC14: "CC 14-bit",
    TYPE_NRPN: "NRPN",
    TYPE_PC: "Program Change",
    TYPE_PB: "Pitch Bend",
}

@dataclass(frozen=True)
class MessageSpec:
    mtype: str
    channel: int
    number: int
    value: int

    def identity(self):
        return (self.mtype, self.channel, self.number, self.value)

    def label(self):
        return f"{self.mtype} ch{self.channel + 1} n{self.number} v{self.value}"


def clamp(val, lo, hi):
    return max(lo, min(hi, val))


def build_spec(mtype, channel, number, value):
    ranges = TYPE_RANGES[mtype]
    number = clamp(int(number), ranges["number"][0], ranges["number"][1])
    value = clamp(int(value), ranges["value"][0], ranges["value"][1])
    return MessageSpec(mtype=mtype, channel=int(channel), number=number, value=value)


def default_spec(mtype, channel):
    defaults = PROBE_DEFAULTS[mtype]
    return build_spec(mtype, channel, defaults["number"], defaults["value"])


def random_spec(mtype, channel, rng=None, vary_number=True, vary_value=True,
                base_number: Optional[int] = None, base_value: Optional[int] = None):
    rng = rng or random
    ranges = TYPE_RANGES[mtype]

    if base_number is None:
        base_number = PROBE_DEFAULTS[mtype]["number"]
    if base_value is None:
        base_value = PROBE_DEFAULTS[mtype]["value"]

    if ranges["number"][0] == ranges["number"][1]:
        number = ranges["number"][0]
    elif vary_number:
        number = rng.randint(ranges["number"][0], ranges["number"][1])
    else:
        number = base_number

    if ranges["value"][0] == ranges["value"][1]:
        value = ranges["value"][0]
    elif vary_value:
        value = rng.randint(ranges["value"][0], ranges["value"][1])
    else:
        value = base_value

    return build_spec(mtype, channel, number, value)


def event_to_spec(event):
    etype = event.get('type')
    if etype == TYPE_NOTE:
        if event.get('velocity', 0) <= 0:
            return None
        return MessageSpec(TYPE_NOTE, event['channel'], event['note'], event['velocity'])
    if etype == TYPE_CC:
        return MessageSpec(TYPE_CC, event['channel'], event['cc'], event['value'])
    if etype == TYPE_CC14:
        return MessageSpec(TYPE_CC14, event['channel'], event['cc'], event['value'])
    if etype == TYPE_NRPN:
        return MessageSpec(TYPE_NRPN, event['channel'], event['nrpn'], event['value'])
    if etype == TYPE_PC:
        return MessageSpec(TYPE_PC, event['channel'], event['value'], 0)
    if etype == TYPE_PB:
        return MessageSpec(TYPE_PB, event['channel'], 0, event['value'])
    return None
