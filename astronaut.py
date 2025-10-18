# astronaut.py
from dataclasses import dataclass, field
from typing import Optional, Dict, Any
import random

def _rand_u32_nonzero() -> int:
    while True:
        v = random.getrandbits(32)
        if v != 0:
            return v

def _rand_appearance() -> int:
    return random.randint(0, 12)

@dataclass
class Astronaut:
    id32: int = field(default_factory=_rand_u32_nonzero)
    name: str = "Astronaut"
    suit_id: int = 0
    # Make this optional so we can avoid overwriting loaded values
    appearance_id: Optional[int] = None

    agency_id: int = 0
    planet_id: Optional[int] = None
    vessel_id: Optional[int] = None

    level: int = 1
    exp: float = 0.0

    def __post_init__(self):
        self.suit_id = max(0, int(self.suit_id))
        if self.appearance_id is None:
            self.appearance_id = _rand_appearance()

    def exp_to_next(self) -> float:
        return 100.0 * max(1, int(self.level))

    def gain_exp(self, amount: float) -> int:
        if amount <= 0:
            return 0
        self.exp += float(amount)
        leveled = 0
        while self.exp >= self.exp_to_next():
            self.exp -= self.exp_to_next()
            self.level += 1
            leveled += 1
        return leveled

    def to_json(self) -> Dict[str, Any]:
        return {
            "id32": int(self.id32),
            "name": str(self.name),
            "suit_id": int(self.suit_id),
            "appearance_id": int(self.appearance_id) if self.appearance_id is not None else None,
            "agency_id": int(self.agency_id),
            "planet_id": int(self.planet_id) if self.planet_id is not None else None,
            "vessel_id": int(self.vessel_id) if self.vessel_id is not None else None,
            "level": int(self.level),
            "exp": float(self.exp),
        }

    @classmethod
    def from_json(cls, data: Dict[str, Any]) -> "Astronaut":
        return cls(
            id32=int(data.get("id32", 0)) or _rand_u32_nonzero(),
            name=str(data.get("name", "Astronaut")),
            suit_id=int(data.get("suit_id", 0)),
            appearance_id=(int(data["appearance_id"]) if data.get("appearance_id") is not None else None),
            agency_id=int(data.get("agency_id", 0)),
            planet_id=(int(data["planet_id"]) if data.get("planet_id") is not None else None),
            vessel_id=(int(data["vessel_id"]) if data.get("vessel_id") is not None else None),
            level=int(data.get("level", 1)),
            exp=float(data.get("exp", 0.0)),
        )
