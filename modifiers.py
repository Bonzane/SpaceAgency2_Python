# modifiers.py
from dataclasses import dataclass
from enum import Enum, auto
from typing import Callable, Optional, Dict, List, Any

class Op(Enum):
    ADD = auto()
    MUL = auto()
    SET = auto()

@dataclass(frozen=True)
class Modifier:
    path: str                            # e.g., "telescope.fov_deg", "power.capacity", "income.base"
    op: Op
    value: float
    predicate: Optional[Callable[[Any], bool]] = None  # callable(vessel) -> bool

def apply_modifiers(base: dict, mods: List[Modifier], vessel) -> dict:
    out = dict(base)
    for m in mods:
        if m.predicate and not m.predicate(vessel):
            continue
        ref = out
        *parts, leaf = m.path.split(".")
        for p in parts:
            ref = ref.setdefault(p, {})
        cur = ref.get(leaf, 0.0)
        if m.op == Op.ADD: cur = cur + m.value
        elif m.op == Op.MUL: cur = cur * m.value
        elif m.op == Op.SET: cur = m.value
        ref[leaf] = cur
    return out

# Only active when stage==0 anyway, but you can keep a predicate for clarity:
only_when_payload_deployed = lambda v: v.stage == 0

from vessel_components import Components
UPGRADES_BY_PAYLOAD: Dict[int, Dict[str, List[Modifier]]] = {
    int(Components.COMMUNICATIONS_SATELLITE): {
        "high_gain": [
            Modifier("income.base", Op.ADD, 2.0, only_when_payload_deployed),
        ],
        "xband_amp": [
            Modifier("income.base", Op.ADD, 3.0, only_when_payload_deployed),
            Modifier("power.draw_payload", Op.ADD, 0.1, only_when_payload_deployed),
        ],
        "ka_array": [
            Modifier("income.base", Op.ADD, 5.0, only_when_payload_deployed),
        ],
    },
    int(Components.SPACE_TELESCOPE): {
        "fine_rcs": [
            Modifier("telescope.max_rate_deg_s", Op.MUL, 1.5, only_when_payload_deployed),
        ],
        "wide_fov": [
            Modifier("telescope.fov_deg", Op.ADD, 15.0, only_when_payload_deployed),
        ],
        "deep_cooler": [
            Modifier("thermal.resistance", Op.MUL, 1.2, only_when_payload_deployed),
            # maybe better SNR → more income from discoveries if you model that later
        ],
    },
}
