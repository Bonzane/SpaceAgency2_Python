import math
import pickle
from pathlib import Path
from typing import List, Union
from gameobjects import Sun, Earth, PhysicsObject, ObjectType, GameObject
import numpy as np
from physics import G
from packet_types import DataGramPacketType
import struct
from session import Session
from vessels import Vessel
from regions import maybe_update_vessel_region
from utils import ambient_temp_simple


def is_planet(o):
    return hasattr(o, "check_in_region") or hasattr(o, "check_in_region_sq")



class Chunk:
    def __init__(self, galaxy: int, system: int, filepath: Union[str, Path], managed_by):
        self.galaxy = galaxy
        self.system = system
        self.path = Path(filepath)
        self.objects: List[GameObject] = []
        self.id_to_object = {}
        self.ready = False
        self.manager = managed_by

        print(f"🌌 Chunk created for galaxy {galaxy}, system {system}. File: {self.path}")
        self.deserialize_chunk()
        self.ready = True

    def is_ready(self) -> bool:
        return self.ready

    def add_object(self, obj: GameObject):
        self.objects.append(obj)
        self.id_to_object[obj.object_id] = obj
        self.manager.register_object(obj.object_id, self.galaxy, self.system)

    def signed_to_unsigned64(self, value: int) -> int:
        return value % (1 << 64)

    def update_objects(self, dt=1.0):
        # Filter physics-enabled objects
        physics_objects = [obj for obj in self.objects if hasattr(obj, "mass")]
        n = len(physics_objects)
        if n < 2:
            return

        pos = np.array([obj.position for obj in physics_objects])
        vel = np.array([obj.velocity for obj in physics_objects])
        mass = np.array([obj.mass for obj in physics_objects])
        forces = np.zeros((n, 2))

        # Tracking info for vessels
        vessel_max_pull = {}

        # Compute gravitational forces
        for i in range(n):
            for j in range(i + 1, n):
                obj_i = physics_objects[i]
                obj_j = physics_objects[j]

                diff = pos[j] - pos[i]
                raw_dist_sq = np.dot(diff, diff)
                raw_dist = math.sqrt(raw_dist_sq)


                # Case: i is vessel, j is planet
                if isinstance(obj_i, Vessel) and is_planet(obj_j):
                    new_region = obj_j.check_in_region(raw_dist)
                    maybe_update_vessel_region(self.manager.shared, obj_i, obj_j, new_region)

                # Case: j is vessel, i is planet
                if isinstance(obj_j, Vessel) and is_planet(obj_i):
                    new_region = obj_i.check_in_region(raw_dist)
                    maybe_update_vessel_region(self.manager.shared, obj_j, obj_i, new_region)


                # Get radii of both objects (default to 0 if not defined)
                radius_i = getattr(obj_i, "radius_km", 0)
                radius_j = getattr(obj_j, "radius_km", 0)

                # Apply gravity with softening
                softening_km = 0.8 * max(radius_i, radius_j)  # KM

                # Distance measured from surfaces (never negative)
                sep_from_surfaces = max(0.0, raw_dist - (radius_i + radius_j))
                # Add softening so max pull happens at surface contact
                effective_sep = sep_from_surfaces + softening_km

                # Guard direction (avoid div-by-zero when objects coincide)
                if raw_dist > 0:
                    direction = diff / raw_dist
                else:
                    direction = np.zeros(2, dtype=float)

                force_mag = G * mass[i] * mass[j] / (effective_sep ** 2)
                force = force_mag * direction

                # Only apply the force if neither object is within the other's radius
                if raw_dist >= max(radius_i, radius_j) * 1.15:
                    forces[i] += force
                    forces[j] -= force

                # Still track the strongest pull for vessels, regardless of radius
                if isinstance(obj_i, Vessel):
                    if obj_i.object_id not in vessel_max_pull or force_mag > vessel_max_pull[obj_i.object_id][1]:
                        vessel_max_pull[obj_i.object_id] = (obj_j, force_mag)

                if isinstance(obj_j, Vessel):
                    if obj_j.object_id not in vessel_max_pull or force_mag > vessel_max_pull[obj_j.object_id][1]:
                        vessel_max_pull[obj_j.object_id] = (obj_i, force_mag)


        for vessel_id, (pulling_obj, strength) in vessel_max_pull.items():
            vessel = self.id_to_object.get(vessel_id)
            if vessel:
                vessel.strongest_gravity_source = pulling_obj
                vessel.strongest_gravity_force = strength

        for obj in physics_objects:
            ox, oy = getattr(obj, "position", (0.0, 0.0))
            dist_km = math.hypot(ox, oy)  # distance to (0, 0)
            space_temp = ambient_temp_simple(dist_km)

            if isinstance(obj, Vessel) and obj.home_planet:
                # Distance from vessel to planet center
                px, py = obj.home_planet.position
                vessel_dist = math.hypot(ox - px, oy - py)
                alt_km = obj.altitude

                # If inside atmosphere, interpolate temp
                atm_height = obj.home_planet.atmosphere_km
                if alt_km <= atm_height:
                    surface_temp = getattr(obj.home_planet, "surface_temp", 288.15)  # K
                    t = max(0.0, min(1.0, alt_km / atm_height))  # 0 = ground, 1 = edge of atm
                    obj.ambient_temp_K = surface_temp * (1 - t) + space_temp * t
                    continue  # skip setting space temp

            # default: space ambient
            obj.ambient_temp_K = space_temp


        # Call do_update() on all objects (physics and non-physics)
        chunkpacket = bytearray()
        chunkpacket.append(DataGramPacketType.OBJECT_STREAM)
        chunkpacket += struct.pack('<H',  self.manager.shared.udp_server.objstream_seq)
        self.manager.shared.udp_server.objstream_seq += 1
        if(self.manager.shared.udp_server.objstream_seq > 65534):
            self.manager.shared.udp_server.objstream_seq = 0
        chunkpacket += struct.pack('<H', len(self.objects))
        MAX_ACCEL = 1e3  # km/s² — adjust based on your physics scale

        for i, obj in enumerate(physics_objects):
            fx, fy = forces[i]
            if obj.mass <= 0:
                continue  # Skip massless or broken objects

            ax = fx / obj.mass
            ay = fy / obj.mass
            acc_mag = math.hypot(ax, ay)

            # Clamp acceleration if needed
            if acc_mag > MAX_ACCEL:
                scale = MAX_ACCEL / acc_mag
                ax *= scale
                ay *= scale

            acc = (ax, ay)
            obj.do_update(dt, acc)

        for obj in self.objects:
            if obj not in physics_objects:
                obj.do_update(dt, (0.0, 0.0))

            obj_x, obj_y = getattr(obj, "position", (0, 0))
            obj_vx, obj_vy = getattr(obj, "velocity", (0, 0))
            chunkpacket += struct.pack(
                '<QQQfff',
                obj.object_id,
                self.signed_to_unsigned64(int(obj_x)),
                self.signed_to_unsigned64(int(obj_y)),
                float(obj_vx),
                float(obj_vy),
                obj.rotation
            )

        for player in self.manager.shared.players.values():
            if player.galaxy == self.galaxy and player.system == self.system:
                session = player.session
                if session and session.udp_port and session.alive:
                    addr = (session.remote_ip, session.udp_port)
                    # Demeter's law doesn't apply to python and we all know it
                    self.manager.shared.udp_server.transport.sendto(chunkpacket, addr)


                        


    def serialize_chunk(self):
        print(f"💾 Serializing chunk to {self.path}")
        with open(self.path, 'wb') as f:
            pickle.dump(self.objects, f)

    def deserialize_chunk(self):
        if not self.path.exists():
            print(f"⚠️ No chunk file found at {self.path}.")
            return

        try:
            with open(self.path, 'rb') as f:
                objs = pickle.load(f)
                for obj in objs:
                    self.add_object(obj)
        except Exception as e:
            print(f"❌ Failed to load chunk: {e}")
            self.objects = []


    def get_object_by_id(self, obj_id: int) -> Union[PhysicsObject, None]:
        return self.id_to_object.get(obj_id)

