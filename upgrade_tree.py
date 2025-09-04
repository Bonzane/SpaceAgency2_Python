# upgrade_tree.py
from dataclasses import dataclass
from typing import Dict, List
from vessel_components import Components  # your enum

@dataclass(frozen=True)
class UpgradeNode:
    id: str
    requires: List[str] = None
    cost_money: int = 0
    cost_items: Dict[int, int] = None

# Trees are keyed by payload component id; values are {upgrade_id -> node}
UPGRADE_TREES_BY_PAYLOAD: Dict[int, Dict[str, UpgradeNode]] = {
    int(Components.COMMUNICATIONS_SATELLITE): {
        "high_gain":    UpgradeNode("high_gain",    requires=[],                cost_money=2000),
        "xband_amp":    UpgradeNode("xband_amp",    requires=["high_gain"],     cost_money=3500),
        "ka_array":     UpgradeNode("ka_array",     requires=["xband_amp"],     cost_money=5000),
    },
    int(Components.SPACE_TELESCOPE): {
        "fine_rcs":     UpgradeNode("fine_rcs",     requires=[],                cost_money=3000),
        "wide_fov":     UpgradeNode("wide_fov",     requires=[],                cost_money=2500),
        "deep_cooler":  UpgradeNode("deep_cooler",  requires=["fine_rcs"],      cost_money=6000),
    },
    # int(Components.MOON_LANDER): {...}
    # int(Components.PROBE): {...}
}
