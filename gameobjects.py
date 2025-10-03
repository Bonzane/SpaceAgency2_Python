from enum import Enum, IntEnum, auto
import pickle
from dataclasses import dataclass, field
import itertools
from typing import Optional, Tuple, Union, Dict, List
import numpy as np
import math
from physics import G
import bisect
from regions import Region
from resources import Resource
import random
from pathlib import Path
import os

ID_SEQ_FILENAME = "object_id.seq"


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
    JETTISONED_COMPONENT = 44

# Asteroid belt rough bounds (in km). 
ASTEROID_BELT_INNER_KM = 300_000_000.0
ASTEROID_BELT_OUTER_KM = 480_000_000.0


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

    @classmethod
    def set_next_id(cls, n: int) -> int:
        cls._next_id = max(1, int(n))
        return cls._next_id
    
    @classmethod
    def load_id_seq(cls, universe_path: Union[str, Path]) -> int:
        """Load next-id from disk if present; return the resulting _next_id."""
        p = Path(universe_path) / ID_SEQ_FILENAME
        try:
            n = int(p.read_text(encoding="utf-8").strip())
            return cls.set_next_id(n)
        except Exception:
            # No file or bad contents — leave as-is
            return cls._next_id   

    @classmethod
    def save_id_seq(cls, universe_path: Union[str, Path]) -> None:
        """Atomically save current _next_id to disk."""
        p = Path(universe_path) / ID_SEQ_FILENAME
        tmp = p.with_suffix(p.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(str(cls._next_id))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, p)

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
    ambient_temp_K: float = 2.7

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


# Jettisoned Components
class JettisonedComponent(PhysicsObject):
    def __init__(
        self,
        position: Tuple[float, float],
        velocity: Tuple[float, float],
        mass: float,
        radius_km: float,
        component_index: int,
        component_id: int = 0
    ):
        super().__init__(
            object_type=ObjectType.JETTISONED_COMPONENT,
            position=position,
            velocity=velocity,
            mass=mass,
            radius_km=radius_km
        )

        self.component_index = component_index
        self.component_id = component_id
        self.age = 0.0
        # make lifetime configurable for debugging
        self.lifetime = 400.0


        # debug flags to avoid log spam
        self._warned_50 = False
        self._warned_80 = False

        print(f"🧩 JettisonedComponent CREATED id={int(self.object_id)} "
              f"comp_index={int(component_index)} pos={tuple(map(float, position))} "
              f"vel={tuple(map(float, velocity))} mass={float(mass)}kg r={float(radius_km)}km "
              f"lifetime={self.lifetime:.2f}s")

    def do_update(self, dt: float, acc: Tuple[float, float]):
        super().do_update(dt, acc)
        self.age += dt * 0.01
        # milestone logs without spamming every tick
        frac = self.age / max(1e-9, self.lifetime)
        if not self._warned_50 and frac >= 0.50:
            self._warned_50 = True
            print(f"⏱️ JC id={int(self.object_id)} reached 50% lifetime (age={self.age:.2f}/{self.lifetime:.2f}s)")
        if not self._warned_80 and frac >= 0.80:
            self._warned_80 = True
            print(f"⏱️ JC id={int(self.object_id)} reached 80% lifetime (age={self.age:.2f}/{self.lifetime:.2f}s)")

    @property
    def expired(self) -> bool:
        return self.age >= self.lifetime




#Planet extends physicsobject lmaooooooo
@dataclass
class Planet(PhysicsObject):
    orbits: Union["Planet", None] = None  # Reference to the object this body orbits
    orbit_radius: float = 0.0             # Optional: used for orbit correction
    orbit_direction: int = 1              # 1 = counterclockwise, -1 = clockwise
    atmosphere_km: float = 10000
    atmosphere_density: float = 1.0
    name: str = "Unnamed Planet"

    regions_km: Dict[int, float] = field(default_factory=dict)
    _region_edges: List[float] = field(default_factory=list, init=False, repr=False)
    _region_ids:   List[int]   = field(default_factory=list, init=False, repr=False)

    # Resource map.
    resource_map: Dict[int, float] = field(default_factory=dict, init=False, repr=False)

    planet_surface_temp: float = 20.0

    is_gas_giant: bool = False
    is_star: bool = False
    is_moon: bool = False

    def set_regions(self, regions: Dict[int, float]) -> None:
        self.regions_km = dict(regions)
        items: List[Tuple[int, float]] = sorted(self.regions_km.items(), key=lambda kv: kv[1])  # small→large
        self._region_ids   = [rid for rid, _ in items]
        self._region_edges = [mx  for _,  mx in items]


    def set_resources(self, resource_map: Dict[int, float]) -> None:
        self.resource_map = dict(resource_map)

    def set_temperature(self, temperature: float) -> None:
        self.planet_surface_temp = temperature

    def check_in_region(self, distance_km: float) -> Optional[int]:
        if not self._region_edges:
            return None
        i = bisect.bisect_left(self._region_edges, distance_km)
        return self._region_ids[i] if i < len(self._region_ids) else None

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
            radius_km=695700.0,
            atmosphere_km=1_000_000.0,
            atmosphere_density=1.0,
            name="The Sun"
        )
        self.is_star = True
        self.set_temperature(5778.0)

    def do_update(self, dt: float, acc: Tuple[float, float]):
        # Integrate normally
        super().do_update(dt, acc)

        # Clamp to a sphere of radius 1000 km around the origin
        px, py = self.position
        r2 = px*px + py*py
        limit = 1000.0
        if r2 > limit * limit:
            r = math.sqrt(r2)
            nx, ny = px / r, py / r  # outward normal

            # Remove outward radial velocity so we don't immediately re-escape
            vx, vy = self.velocity
            v_rad = vx * nx + vy * ny    # scalar radial speed
            if v_rad > 0.0:
                vx -= v_rad * nx
                vy -= v_rad * ny
                self.velocity = (vx, vy)

            # Snap onto the boundary
            self.position = (nx * limit, ny * limit)


class Earth(Planet):
    def __init__(self):
        super().__init__(
            object_type=ObjectType.EARTH,
            position=(152_000_000.0, 0.0),      #km
            velocity=(0.0, -29.78),             #km/s
            mass=5.972e24,                      #kg
            radius_km=6371.0, 
            atmosphere_km=10000.0,              #km*10
            atmosphere_density=1.0,
            name="Earth"
        )
        self.set_resources({
            Resource.METAL: 500,
            Resource.OIL: 300,
            Resource.URANIUM: 50,
            Resource.SILICON: 100,
            Resource.WATER: 1000,
            Resource.GOLD: 20,
            Resource.DIAMOND: 10, 
            Resource.PLUTONIUM: 3, 
            Resource.XENON: 5,
            Resource.BERILLYUM: 30,
            Resource.OBSIDIAN_SHARD: 1

        })

        self.set_temperature(288.0)

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

class Mars(Planet):
    def __init__(self):
        super().__init__(
            object_type=ObjectType.MARS,
            position=(0.0, 241_520_000.0),      #km
            velocity=(24.1, 0.0),             #km/s
            mass=6.41693e23,                      #kg
            radius_km = 3389.5, 
            atmosphere_km=8000.0,              #km*10
            atmosphere_density=0.6,
            name="Mars"
        )
        self.set_regions({
            Region.MARS_CLOSE: 30_000,
            Region.MARS_NEAR: 300_000,
            Region.MARS_DISTANT: 1_000_000
        })

        self.set_temperature(210.0)


    def do_update(self, dt: float, acc: Tuple[float, float]):
        super().do_update(dt, acc)
        degrees_per_second = 360.0 / 88642.0  # degrees per second degrees / seconds in day)
        self.rotation += dt * degrees_per_second
        self.rotation %= 360.0

class Venus(Planet):
    def __init__(self):
        super().__init__(
            object_type=ObjectType.VENUS,
            position=(-67_225_000, 0.0),      #km
            velocity=(0.0, 35.02),             #km/s
            mass=4.867e24,                      #kg
            radius_km = 3389.5, 
            atmosphere_km=10000.0,              #km*10
            atmosphere_density=2.0,
            name="Venus"
        )

        self.set_temperature(737.0)

    def do_update(self, dt: float, acc: Tuple[float, float]):
        super().do_update(dt, acc)
        degrees_per_second = 360.0 / 20_995_200.0  # degrees per second degrees / seconds in day)
        self.rotation += dt * degrees_per_second
        self.rotation %= 360.0

class Mercury(Planet):
    def __init__(self):
        super().__init__(
            object_type=ObjectType.MERCURY,
            position=(0.0, -35_863_000),      #km
            velocity=(-47.36, 0.0),             #km/s
            mass=3.285e23,                      #kg
            radius_km = 2439.7, 
            atmosphere_km=5000.0,              #km*10
            atmosphere_density=0.5,
            name="Mercury"
        )

        self.set_temperature(440.0)

    def do_update(self, dt: float, acc: Tuple[float, float]):
        super().do_update(dt, acc)
        degrees_per_second = 360.0 / 15_181_440.0  # degrees per second degrees / seconds in day)
        self.rotation += dt * degrees_per_second
        self.rotation %= 360.0   

class Jupiter(Planet):
    def __init__(self):
        super().__init__(
            object_type=ObjectType.JUPITER,
            position=(778_000_000.0, 0.0),      #km
            velocity=(0.0, -13.07),             #km/s
            mass=1.898e27,                      #kg
            radius_km = 69911.0, 
            atmosphere_km=20000.0,              #km*10
            atmosphere_density=2.0,
            name="Jupiter"
        )
        self.set_regions({
            Region.JUPITER_CLOSE: 1_000_000,
            Region.JUPITER_NEAR: 30_000_000,
            Region.JUPITER_DISTANT: 300_000_000
        })

        self.set_temperature(165.0)
        self.is_gas_giant = True

    def do_update(self, dt: float, acc: Tuple[float, float]):
        super().do_update(dt, acc)
        degrees_per_second = 360.0 / 35_430.0  # degrees per second degrees / seconds in day)
        self.rotation += dt * degrees_per_second
        self.rotation %= 360.0   

class Saturn(Planet):
    def __init__(self):
        super().__init__(
            object_type=ObjectType.SATURN,
            position=(0.0, -888_650_000),      #km
            velocity=(-9.69, 0.0),             #km/s
            mass=5.685e26,                      #kg
            radius_km = 58232.0, 
            atmosphere_km=15000.0,              #km*10
            atmosphere_density=1.5,
            name="Saturn"
        )
        self.set_regions({
            Region.SATURN_CLOSE: 1_000_000,
            Region.SATURN_NEAR: 40_000_000,
            Region.SATURN_DISTANT: 400_000_000
        })

        self.set_temperature(134.0)
        self.is_gas_giant = True

    def do_update(self, dt: float, acc: Tuple[float, float]):
        super().do_update(dt, acc)
        degrees_per_second = 360.0 / 37_988.0  # degrees per second degrees / seconds in day)
        self.rotation += dt * degrees_per_second
        self.rotation %= 360.0   

class Uranus(Planet):
    def __init__(self):
        super().__init__(
            object_type=ObjectType.URANUS,
            position=(-2_918_400_000.0, 0.0),      #km
            velocity=(0.0, 6.8),             #km/s
            mass = 8.681e25,                      #kg
            radius_km = 25362.0, 
            atmosphere_km=12000.0,              #km*10
            atmosphere_density=1.5,
            name="Uranus"
        )
        self.set_regions({
            Region.URANUS_CLOSE: 5_000_000,
            Region.URANUS_NEAR: 80_000_000,
            Region.URANUS_DISTANT: 800_000_000
        })

        self.set_temperature(76.0)
        self.is_gas_giant = True

    def do_update(self, dt: float, acc: Tuple[float, float]):
        super().do_update(dt, acc)
        degrees_per_second = 360.0 / 62_092.0  # degrees per second degrees / seconds in day)
        self.rotation -= dt * degrees_per_second
        self.rotation %= 360.0

class Neptune(Planet):
    def __init__(self):
        super().__init__(
            object_type=ObjectType.NEPTUNE,
            position=(0.0, 4_470_800_000.0),      #km
            velocity=(5.45, 0.0),             #km/s
            mass = 1.0241e26,                      #kg
            radius_km = 24622.0, 
            atmosphere_km=11000.0,              #km*10
            atmosphere_density=1.4,
            name="Neptune"
        )
        self.set_regions({
            Region.NEPTUNE_CLOSE: 2_000_000,
            Region.NEPTUNE_NEAR: 100_000_000,
            Region.NEPTUNE_DISTANT: 1_000_000_000
        })

        self.set_temperature(72.0)
        self.is_gas_giant = True

    def do_update(self, dt: float, acc: Tuple[float, float]):
        super().do_update(dt, acc)
        degrees_per_second = 360.0 / 57_996.0  # degrees per second degrees / seconds in day)
        self.rotation += dt * degrees_per_second
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
            radius_km=1737.0, 
            atmosphere_km=1000.0,
            atmosphere_density=0.5,
            name="Luna"
        )

        self.set_temperature(220.0)
        self.is_moon = True
        self.set_regions({
            Region.MOON_NEAR: 50_000
        })

    def do_update(self, dt: float, acc: Tuple[float, float]):
        # Skip orbit correction and apply real physics
        PhysicsObject.do_update(self, dt, acc)

        # Update rotation to face the Earth (tidally locked)
        if self.orbits:
            self.rotation = direction_between_degrees(self.position, self.orbits.position)

      

@dataclass
class AsteroidBeltAsteroid(PhysicsObject):
    def __init__(self):
        # --- Hardcoded belt parameters ---
        SUN_MASS = 1.989e30              # kg
        BELT_INNER = 300_000_000.0       # km
        BELT_OUTER = 550_000_000.0       # km
        DENSITY = 2500.0                 # kg/m^3
        MIN_RADIUS_KM = 0.5
        MAX_RADIUS_KM = 30.0
        NOISE = 0.02                     # km/s jitter

        # Pick asteroid radius
        radius_km = random.uniform(MIN_RADIUS_KM, MAX_RADIUS_KM)

        # Estimate mass from volume * density
        r_m = radius_km * 1000.0
        volume = (4/3) * math.pi * (r_m**3)
        mass = DENSITY * volume

        # Random orbit distance + angle
        r = random.uniform(BELT_INNER, BELT_OUTER)
        theta = random.uniform(0.0, 2.0 * math.pi)
        px = r * math.cos(theta)
        py = r * math.sin(theta)

        # Circular orbital speed around the Sun
        v = math.sqrt(G * SUN_MASS / r) + random.uniform(-NOISE, NOISE)

        # Tangential unit vector (CCW)
        tx, ty = -math.sin(theta), math.cos(theta)
        vx, vy = tx * v, ty * v

        super().__init__(
            object_type=ObjectType.ASTEROID_BELT_ASTEROID,
            position=(px, py),
            velocity=(vx, vy),
            mass=mass,
            radius_km=radius_km,
        )