# upgrade_tree.py
from dataclasses import dataclass, field
from typing import Dict, List
from vessel_components import Components 
from enum import IntEnum

class T_UP(IntEnum):
    PING1        = 0x0000
    PING2        = 0x0001
    NETWORKING1  = 0x0002
    NETWORKING2  = 0x0003
    EXPOSURE1    = 0x0004
    FOCUS1       = 0x0005
    RESOLUTION1  = 0x0006
    ZOOM1        = 0x0007    
    FOCUS2       = 0x0008
    PLANET_IMAGE = 0x0009

@dataclass(frozen=True)
class UpgradeNode:
    id: int
    tier: int
    requires: List[int] = field(default_factory=list)  # ← avoid None
    cost_money: int = 0

# Key the inner dict by int (not str), since you’re using IntEnum keys
UPGRADE_TREES_BY_PAYLOAD: Dict[int, Dict[int, UpgradeNode]] = {
    int(Components.COMMUNICATIONS_SATELLITE): {
        T_UP.PING1:        UpgradeNode(T_UP.PING1,        1, [],                       5000),
        T_UP.NETWORKING1:  UpgradeNode(T_UP.NETWORKING1,  1, [],                      15000),
        T_UP.PING2:        UpgradeNode(T_UP.PING2,        2, [T_UP.PING1],            20000),
        T_UP.NETWORKING2:  UpgradeNode(T_UP.NETWORKING2,  2, [T_UP.NETWORKING1],      45000)
    },
    int(Components.SPACE_TELESCOPE): {
        T_UP.EXPOSURE1:    UpgradeNode(T_UP.EXPOSURE1,     1, [],                      15000),
        T_UP.FOCUS1:       UpgradeNode(T_UP.FOCUS1,        1, [],                      20000),
        T_UP.RESOLUTION1:  UpgradeNode(T_UP.RESOLUTION1,   1, [],                      25000),
        T_UP.ZOOM1:        UpgradeNode(T_UP.ZOOM1,         1, [T_UP.EXPOSURE1],        75000),
        T_UP.FOCUS2:       UpgradeNode(T_UP.FOCUS2,        1, [T_UP.FOCUS1],           35000),
        T_UP.PLANET_IMAGE:  UpgradeNode(T_UP.PLANET_IMAGE,   1, [T_UP.RESOLUTION1],    55000)
    }
}
