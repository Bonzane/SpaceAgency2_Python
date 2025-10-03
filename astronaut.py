# astronaut.py
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Dict, Any
import secrets
import random

def _rand_u32_nonzero() -> int:
    # cryptographically-strong, 64-bit, non-zero
    while True:
        v = random.getrandbits(32)
        if v != 0:
            return v

def _rand_appearance() -> int:
    return random.randint(0, 12)

@dataclass
class Astronaut:
    """
    A single astronaut entity that can live on a planet or ride in a vessel.
    """
    id32: int = field(default_factory=_rand_u32_nonzero)       # globally unique (u64)
    name: str = "Astronaut"
    suit_id: int = 0                                   # cosmetic suit variant
    appearance_id: int = 0 

    # Ownership / placement context (optional)
    agency_id: int = 0
    planet_id: Optional[int] = None
    vessel_id: Optional[int] = None

    level: int = 1
    exp: float = 0.0

    def __post_init__(self):
        self.suit_id = max(0, int(self.suit_id))
        self.appearance_id =_rand_appearance()

    def exp_to_next(self) -> float:
        # Simple curve: 100 * current_level
        return 100.0 * max(1, int(self.level))

    def gain_exp(self, amount: float) -> int:
        """
        Add XP (can be fractional). Returns number of levels gained this call.
        """
        if amount <= 0:
            return 0
        self.exp += float(amount)
        leveled = 0
        # loop in case we pass multiple thresholds
        while self.exp >= self.exp_to_next():
            need = self.exp_to_next()
            self.exp -= need
            self.level += 1
            leveled += 1
        return leveled


    # --- Serialization helpers (optional) ---
    def to_json(self) -> Dict[str, Any]:
        return {
            "id32": int(self.id32),
            "name": str(self.name),
            "suit_id": int(self.suit_id),
            "appearance_id": int(self.appearance_id),
            "agency_id": int(self.agency_id),
            "planet_id": int(self.planet_id) if self.planet_id is not None else None,
            "vessel_id": int(self.vessel_id) if self.vessel_id is not None else None,
            "level": int(self.level),
            "exp": float(self.exp),
        }

    @classmethod
    def from_json(cls, data: Dict[str, Any]) -> "Astronaut":
        return cls(
            id64=int(data.get("id32", 0)),
            name=str(data.get("name", "Astronaut")),
            suit_id=int(data.get("suit_id", 0)),
            appearance_id=int(data.get("appearance_id", _rand_appearance())),
            agency_id=int(data.get("agency_id", 0)),
            planet_id=(int(data["planet_id"]) if data.get("planet_id") is not None else None),
            vessel_id=(int(data["vessel_id"]) if data.get("vessel_id") is not None else None),
        )
