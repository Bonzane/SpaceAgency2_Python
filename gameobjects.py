from enum import Enum, IntEnum, auto
import pickle
from dataclasses import dataclass, field
import itertools
from typing import Tuple, Union
import numpy as np
import math
from physics import G


class ObjectType(IntEnum):
    UNDEFINED = 0
    SUN = 1
    EARTH = 2
    LUNA = 3
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
    BASIC_VESSEL = 43

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

def direction_between_degrees(p1, p2):
    dx = p2[0] - p1[0]
    dy = p2[1] - p1[1]
    angle_rad = math.atan2(dy, dx)
    angle_deg = math.degrees(angle_rad)
    return angle_deg


# ---------------- Base Object ----------------

@dataclass(unsafe_hash=True)
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
                
    def do_update(self, dt: float, acc: Tuple[float, float]):
        pass  # base class does nothing


# PhysicsObject extends GameObject and adds physics fields
@dataclass
class PhysicsObject(GameObject):
    position: Tuple[float, float] = (0.0, 0.0)
    velocity: Tuple[float, float] = (0.0, 0.0)
    mass: float = 1.0
    radius_km: float = 10.0  # Default for non-planetary objects

    def do_update(self, dt: float, acc: Tuple[float, float]):
        vx, vy = self.velocity
        ax, ay = acc

        # Semi-implicit Euler: update velocity first
        vx += ax * dt
        vy += ay * dt

        px, py = self.position
        px += vx * dt
        py += vy * dt

        self.velocity = (vx, vy)
        self.position = (px, py)



#Planet extends physicsobject lmaooooooo
@dataclass
class Planet(PhysicsObject):
    orbits: Union["Planet", None] = None  # Reference to the object this body orbits
    orbit_radius: float = 0.0             # Optional: used for orbit correction
    orbit_direction: int = 1              # 1 = counterclockwise, -1 = clockwise

    def do_update(self, dt: float, acc: Tuple[float, float]):
        if self.orbits:
            self.correct_orbit(dt)
        else:
            super().do_update(dt, acc)


    def correct_orbit(self, dt):
        if self.orbits is None:
            return

        cx, cy = self.orbits.position
        px, py = self.position

        dx = px - cx
        dy = py - cy
        r = np.sqrt(dx**2 + dy**2)
        self.orbit_radius = r  # optionally store

        # Normalize the direction perpendicular to the radius vector
        tangent = np.array([-dy, dx]) * self.orbit_direction
        tangent /= np.linalg.norm(tangent)

        # Circular orbital velocity
        v = np.sqrt(G * self.orbits.mass / r)

        self.velocity = (
            self.orbits.velocity[0] + tangent[0] * v,
            self.orbits.velocity[1] + tangent[1] * v,
        )

        # Optional: snap to exact orbit path
        self.position = (
            cx + dx / r * self.orbit_radius,
            cy + dy / r * self.orbit_radius
        )

class Sun(Planet):
    def __init__(self):
        super().__init__(
            object_type=ObjectType.SUN,
            position=(0.0, 0.0),
            velocity=(0.0, 0.0),
            mass=1.989e30,
            radius_km=695700.0  # Sun radius
        )

class Earth(Planet):
    def __init__(self):
        super().__init__(
            object_type=ObjectType.EARTH,
            position=(152_000_000.0, 0.0),      #km
            velocity=(0.0, -29.78),             #km/s
            mass=5.972e24,                      #kg
            radius_km=6371.0
        )

    def do_update(self, dt: float, acc: Tuple[float, float]):
        super().do_update(dt, acc)
        #print(
       #     f"[EARTH DEBUG] pos=({self.position[0]:,.2f}, {self.position[1]:,.2f}) km, "
       #     f"vel=({self.velocity[0]:.5f}, {self.velocity[1]:.5f}) km/s, "
       #     f"rotation={self.rotation:.2f}°"
       # )
        # Earth's axial rotation
        degrees_per_second = 360.0 / 86400.0  # degrees per second
        self.rotation += dt * degrees_per_second

        # Optional: wrap rotation between 0 and 360
        self.rotation %= 360.0

class Luna(Planet):
    def __init__(self, earth: Earth):
        moon_distance = 384_400.0  # km
        moon_mass = 7.342e22       # kg

        # Set initial position relative to Earth
        moon_x = earth.position[0] + moon_distance
        moon_y = earth.position[1]

        # Tangent vector for initial orbit
        dx = moon_x - earth.position[0]
        dy = moon_y - earth.position[1]
        r_vec = np.array([dx, dy])
        r = np.linalg.norm(r_vec)
        tangent = np.array([-dy, dx]) / r
        v_mag = np.sqrt(G * earth.mass / r)
        vx, vy = earth.velocity[0] + tangent[0] * v_mag, earth.velocity[1] + tangent[1] * v_mag

        super().__init__(
            object_type=ObjectType.LUNA,
            position=(moon_x, moon_y),
            velocity=(vx, vy),
            mass=moon_mass,
            orbits=earth,  # used only for rotation
            orbit_radius=moon_distance,
            radius_km=1737.0
        )

    def do_update(self, dt: float, acc: Tuple[float, float]):
        # Skip orbit correction and apply real physics
        PhysicsObject.do_update(self, dt, acc)

        # Update rotation to face the Earth (tidally locked)
        if self.orbits:
            self.rotation = direction_between_degrees(self.position, self.orbits.position)

      
