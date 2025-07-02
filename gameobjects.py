from enum import Enum, IntEnum, auto
import pickle
from dataclasses import dataclass, field
import itertools
from typing import Tuple


class ObjectType(IntEnum):
    UNDEFINED = 0
    SUN = 1
    EARTH = 2
    MOON = 3
    MARS = 4
    VENUS = 5
    MERCURY = 6
    JUPITER = 7
    SATURN = 8
    URANUS = 9
    NEPTUNE = 10
    PLUTO = 11
    CERES = 12
    MAKEMAKE = 13
    ERIS = 14
    HAUMEA = 15
    VOYAGER_1 = 16
    VOYAGER_2 = 17
    PIONEER_10 = 18
    NEW_HORIZONS = 19
    KUIPER_BELT_ASTEROID = 20
    ASTEROID_BELT_ASTEROID = 21
    COMET = 22
    HALLEY_COMET = 23
    HALE_BOPP_COMET = 24
    GANYMEDE = 25
    TITAN = 26
    EUROPA = 27
    IO = 28
    CALLISTO = 29
    TRITON = 30
    ENCELADUS = 31
    PHOBOS = 32
    DEIMOS = 33
    MONOLITH = 34
    ICE_CHUNK = 35
    PROXIMA_CENTAURI = 36
    PROXIMA_CENTAURI_B = 37
    GAIA_BH1_STAR = 38
    GAIA_BH1_BLACKHOLE = 39
    SAGITTARIUS_A_BLACKHOLE = 40
    PROCEDURAL_ROCKY_PLANET = 41
    PROCEDURAL_GAS_GIANT = 42

# ID GEN
_object_id_counter = itertools.count(1)

# ---------------- Physics Data ----------------

@dataclass
class Vector2D:
    x: float
    y: float


@dataclass
class PhysicsData:
    position: Vector2D = field(default_factory=lambda: Vector2D(0.0, 0.0))
    velocity: Vector2D = field(default_factory=lambda: Vector2D(0.0, 0.0))
    angular_velocity: float = 0.0
    mass_kg: float = 1.0


# ---------------- Base Object ----------------

@dataclass
class GameObject:
    object_type: ObjectType
    object_id: int = field(default_factory=lambda: GameObject.get_next_id())
    _next_id: int = 1
    rotation: float = 0

    @classmethod
    def get_next_id(cls):
        obj_id = cls._next_id
        cls._next_id += 1
        return obj_id

    def serialize(self, filepath: str):
        with open(filepath, "wb") as f:
            pickle.dump(self, f)

    @classmethod
    def deserialize(cls, filepath: str):
        with open(filepath, "rb") as f:
            return pickle.load(f)


# PhysicsObject extends GameObject and adds physics fields
@dataclass
class PhysicsObject(GameObject):
    position: Tuple[float, float] = (0.0, 0.0)
    velocity: Tuple[float, float] = (0.0, 0.0)
    mass: float = 1.0



#Planet extends physicsobject lmaooooooo
@dataclass
class Planet(PhysicsObject):
    pass

class Sun(Planet):
    def __init__(self):
        super().__init__(
            object_type=ObjectType.SUN,
            position=(0.0, 0.0),
            velocity=(0.0, 0.0),
            mass=1.989e30,
        )

class Earth(Planet):
    def __init__(self):
        super().__init__(
            object_type=ObjectType.EARTH,
            position=(152_000_000.0, 0.0),      #km
            velocity=(0.0, -29.78),             #km/s
            mass=5.972e24,                      #kg
        )