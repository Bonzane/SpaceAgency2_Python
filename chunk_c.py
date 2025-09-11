import math
import pickle
import struct
from pathlib import Path
from typing import List, Union

import numpy as np

from gameobjects import Sun, Earth, PhysicsObject, ObjectType, GameObject
from physics import G
from packet_types import DataGramPacketType
from vessels import Vessel, VesselState
from regions import maybe_update_vessel_region
from utils import ambient_temp_simple
import os


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

        # Cache backend selection for the lifetime of the process
        try:
            import cupy as xp  # GPU
            self.xp = xp
            self.on_gpu = True
            print("✅ GPU acceleration enabled using CuPy")
        except Exception:
            import numpy as xp  # CPU
            self.xp = xp
            self.on_gpu = False
            print("⚠️ Falling back to CPU (NumPy only)")

    def is_ready(self) -> bool:
        return self.ready

    def add_object(self, obj: GameObject):
        self.objects.append(obj)
        self.id_to_object[obj.object_id] = obj
        self.manager.register_object(obj.object_id, self.galaxy, self.system)

        if isinstance(obj, Vessel):
            obj.shared = self.manager.shared
            obj.home_chunk = self

            # relink planet reference
            pid = None
            hp = getattr(obj, "home_planet", None)
            if hp is not None:
                pid = getattr(hp, "object_id", None)
            if not pid:
                pid = getattr(obj, "launchpad_planet_id", None)
            if pid:
                same_chunk_planet = self.get_object_by_id(int(pid))
                if same_chunk_planet is not None:
                    obj.home_planet = same_chunk_planet

            cid = int(getattr(obj, "controlled_by", 0) or 0)
            if cid:
                p = self.manager.shared.players.get(cid)
                if not p or not p.session or not p.session.alive:
                    obj.controlled_by = 0
                    obj.control_state[VesselState.FORWARD_THRUSTER_ON] = False
                    obj.control_state[VesselState.REVERSE_THRUSTER_ON] = False
                    obj.control_state[VesselState.CCW_THRUST_ON] = False
                    obj.control_state[VesselState.CW_THRUST_ON] = False

            try:
                obj.calculate_vessel_stats()
            except Exception as e:
                print(f"⚠️ Vessel {obj.object_id} calculate_vessel_stats failed: {e}")

    def signed_to_unsigned64(self, value: int) -> int:
        return value % (1 << 64)

    def update_objects(self, dt=1.0, debug_backend=False):
        xp = self.xp
        on_gpu = self.on_gpu
        if debug_backend:
            print(f"🔧 Physics update running on {'GPU' if on_gpu else 'CPU'}")

        physics_objects = [obj for obj in self.objects if hasattr(obj, "mass")]
        n = len(physics_objects)
        if n < 2:
            return

        # classify
        def is_asteroid(o):
            return getattr(o, "object_type", None) == ObjectType.ASTEROID_BELT_ASTEROID

        def is_massive(o):
            return getattr(o, "object_type", None) in (
                ObjectType.SUN,
                ObjectType.MERCURY,
                ObjectType.VENUS,
                ObjectType.EARTH,
                ObjectType.MARS,
                ObjectType.JUPITER,
                ObjectType.SATURN,
                ObjectType.URANUS,
                ObjectType.NEPTUNE,
            )

        asteroid_idx = [i for i, o in enumerate(physics_objects) if is_asteroid(o)]
        massive_idx = [i for i, o in enumerate(physics_objects) if is_massive(o)]
        non_ast_idx = [i for i in range(n) if i not in asteroid_idx]

        # numpy (or cupy) views
        pos_np = np.array([obj.position for obj in physics_objects], dtype=np.float64)
        vel_np = np.array([obj.velocity for obj in physics_objects], dtype=np.float64)
        mass_np = np.array([obj.mass for obj in physics_objects], dtype=np.float64)

        pos = xp.asarray(pos_np)
        vel = xp.asarray(vel_np)
        mass = xp.asarray(mass_np)

        # 1) pairwise forces (non-asteroids)
        if len(non_ast_idx) > 1:
            forces = np.zeros((len(non_ast_idx), 2), dtype=np.float64)
            vessel_max_pull = {}

            for ii, i in enumerate(non_ast_idx):
                for jj, j in enumerate(non_ast_idx):
                    if j <= i:
                        continue

                    obj_i = physics_objects[i]
                    obj_j = physics_objects[j]

                    diff = pos_np[j] - pos_np[i]
                    raw_dist = float(np.hypot(diff[0], diff[1]))

                    from vessels import Vessel
                    if isinstance(obj_i, Vessel) and is_planet(obj_j):
                        maybe_update_vessel_region(self.manager.shared, obj_i, obj_j, obj_j.check_in_region(raw_dist))
                    if isinstance(obj_j, Vessel) and is_planet(obj_i):
                        maybe_update_vessel_region(self.manager.shared, obj_j, obj_i, obj_i.check_in_region(raw_dist))

                    radius_i = getattr(obj_i, "radius_km", 0.0)
                    radius_j = getattr(obj_j, "radius_km", 0.0)
                    softening_km = 0.8 * max(radius_i, radius_j)
                    sep_from_surfaces = max(0.0, raw_dist - (radius_i + radius_j))
                    effective_sep = sep_from_surfaces + softening_km

                    if raw_dist > 0:
                        direction = diff / raw_dist
                    else:
                        direction = np.zeros(2, dtype=np.float64)

                    force_mag = G * mass_np[i] * mass_np[j] / (effective_sep ** 2)
                    force_vec = force_mag * direction

                    if raw_dist >= max(radius_i, radius_j) * 1.15:
                        forces[ii] += force_vec
                        kk = non_ast_idx.index(j)
                        forces[kk] -= force_vec

                    if isinstance(obj_i, Vessel):
                        if obj_i.object_id not in vessel_max_pull or force_mag > vessel_max_pull[obj_i.object_id][1]:
                            vessel_max_pull[obj_i.object_id] = (physics_objects[j], force_mag)
                    if isinstance(obj_j, Vessel):
                        if obj_j.object_id not in vessel_max_pull or force_mag > vessel_max_pull[obj_j.object_id][1]:
                            vessel_max_pull[obj_j.object_id] = (physics_objects[i], force_mag)

            for vessel_id, (pulling_obj, strength) in vessel_max_pull.items():
                vessel = self.id_to_object.get(vessel_id)
                if vessel:
                    vessel.strongest_gravity_source = pulling_obj
                    vessel.strongest_gravity_force = strength

            MAX_ACCEL = 1e3
            for idx_local, i in enumerate(non_ast_idx):
                obj = physics_objects[i]
                if obj.mass <= 0:
                    continue
                fx, fy = forces[idx_local]
                ax, ay = fx / mass_np[i], fy / mass_np[i]
                acc_mag = math.hypot(ax, ay)
                if acc_mag > MAX_ACCEL:
                    scale = MAX_ACCEL / acc_mag
                    ax *= scale
                    ay *= scale
                obj.do_update(dt, (ax, ay))

        # 2) asteroid updates (vectorized, GPU when possible)
        if asteroid_idx:
            a_pos = pos[asteroid_idx]
            a_vel = vel[asteroid_idx]
            m_pos = pos[massive_idx] if massive_idx else xp.zeros((0, 2), dtype=pos.dtype)
            m_mass = mass[massive_idx] if massive_idx else xp.zeros((0,), dtype=mass.dtype)

            if m_pos.shape[0] > 0:
                diff = m_pos[None, :, :] - a_pos[:, None, :]
                r2 = xp.sum(diff * diff, axis=2) + 1e-12
                r = xp.sqrt(r2)
                soft = xp.asarray(500.0, dtype=r.dtype)
                eff = r + soft
                inv_r3 = 1.0 / xp.maximum(eff * r2, 1e-12)
                a_per_body = (G * m_mass[None, :, None]) * diff * inv_r3[:, :, None]
                a_total = xp.sum(a_per_body, axis=1)
            else:
                a_total = xp.zeros_like(a_pos)

            MAX_ACCEL = 1e3
            acc_mag = xp.sqrt(a_total[:, 0] ** 2 + a_total[:, 1] ** 2)
            scale = xp.minimum(1.0, MAX_ACCEL / xp.maximum(acc_mag, 1e-9))
            a_total = a_total * scale[:, None]

            a_vel = a_vel + a_total * dt
            a_pos = a_pos + a_vel * dt

            vel[asteroid_idx] = a_vel
            pos[asteroid_idx] = a_pos

            pos_np_updated = xp.asnumpy(pos) if on_gpu else pos
            vel_np_updated = xp.asnumpy(vel) if on_gpu else vel

            for i in asteroid_idx:
                physics_objects[i].velocity = (
                    float(vel_np_updated[i, 0]),
                    float(vel_np_updated[i, 1]),
                )
                physics_objects[i].position = (
                    float(pos_np_updated[i, 0]),
                    float(pos_np_updated[i, 1]),
                )

        # 3) ambient temps
        for obj in physics_objects:
            ox, oy = getattr(obj, "position", (0.0, 0.0))
            dist_km = math.hypot(ox, oy)
            space_temp = ambient_temp_simple(dist_km)

            if isinstance(obj, Vessel) and obj.home_planet:
                alt_km = obj.altitude
                atm_height = obj.home_planet.atmosphere_km
                if alt_km <= atm_height:
                    surface_temp = getattr(obj.home_planet, "planet_surface_temp", 288.15)
                    t = max(0.0, min(1.0, alt_km / atm_height))
                    obj.ambient_temp_K = surface_temp * (1 - t) + space_temp * t
                    continue
            obj.ambient_temp_K = space_temp

        # 4) non-physics updates
        for obj in self.objects:
            if obj not in physics_objects:
                obj.do_update(dt, (0.0, 0.0))

        # 5) stream packet
        chunkpacket = bytearray()
        chunkpacket.append(DataGramPacketType.OBJECT_STREAM)
        chunkpacket += struct.pack('<H', self.manager.shared.udp_server.objstream_seq)
        self.manager.shared.udp_server.objstream_seq += 1
        if self.manager.shared.udp_server.objstream_seq > 65534:
            self.manager.shared.udp_server.objstream_seq = 0

        chunkpacket += struct.pack('<H', len(self.objects))
        for obj in self.objects:
            obj_x, obj_y = getattr(obj, "position", (0, 0))
            obj_vx, obj_vy = getattr(obj, "velocity", (0, 0))
            chunkpacket += struct.pack(
                '<QQQfff',
                obj.object_id,
                self.signed_to_unsigned64(int(obj_x)),
                self.signed_to_unsigned64(int(obj_y)),
                float(obj_vx),
                float(obj_vy),
                obj.rotation,
            )

        for player in self.manager.shared.players.values():
            if player.galaxy == self.galaxy and player.system == self.system:
                session = player.session
                if session and session.udp_port and session.alive:
                    addr = (session.remote_ip, session.udp_port)
                    self.manager.shared.udp_server.transport.sendto(chunkpacket, addr)

    def serialize_chunk(self):
        print(f"💾 Serializing chunk {self.galaxy}:{self.system} with {len(self.objects)} objects")
        objs_to_write = []
        skipped = []

        for idx, obj in enumerate(list(self.objects)):
            try:
                # Probe picklability — if this passes, include the *object* (not the bytes)
                pickle.dumps(obj, protocol=pickle.HIGHEST_PROTOCOL)
                objs_to_write.append(obj)
            except Exception as e:
                skipped.append((idx, type(obj).__name__, getattr(obj, 'object_id', '?'), str(e)))

        tmp = self.path.with_suffix('.chunk.tmp')
        with open(tmp, 'wb') as f:
            pickle.dump(objs_to_write, f, protocol=pickle.HIGHEST_PROTOCOL)
            f.flush(); os.fsync(f.fileno())
        os.replace(tmp, self.path)

        print(f"💾 Wrote {len(objs_to_write)} objects to {self.path.name} (skipped {len(skipped)})")
        for idx, typ, oid, err in skipped:
            print(f"   ❌ [{idx}] {typ} id={oid} not saved: {err}")


    def deserialize_chunk(self):
        if not self.path.exists():
            print(f"⚠️ No chunk file found at {self.path}.")
            return

        try:
            with open(self.path, 'rb') as f:
                objs = pickle.load(f)

            from collections import Counter
            type_counts = Counter(type(o).__name__ for o in objs)
            print(
                f"📦 Chunk {self.galaxy}:{self.system} loaded {len(objs)} objects "
                f"from {self.path.name}: "
                + ", ".join(f"{k}={v}" for k, v in type_counts.items())
            )

            # Pass 1: non-vessels
            print("🔎 Pass 1: Adding non-vessel objects...")
            for idx, obj in enumerate(objs):
                if not isinstance(obj, Vessel):
                    try:
                        print(
                            f"   ➕ [{idx}] {type(obj).__name__} "
                            f"(id={getattr(obj, 'object_id', '?')})"
                        )
                        self.add_object(obj)
                    except Exception as e:
                        print(f"   ❌ [{idx}] Failed to add {type(obj).__name__}: {e}")

            # Pass 2: vessels (attach runtime links + planet by id)
            print("🔎 Pass 2: Adding vessels...")
            for idx, obj in enumerate(objs):
                if isinstance(obj, Vessel):
                    try:
                        print(
                            f"   🚀 [{idx}] Vessel "
                            f"(id={getattr(obj, 'object_id', '?')}, "
                            f"name={getattr(obj, 'name', '?')}, "
                            f"agency_id={getattr(obj, 'agency_id', '?')})"
                        )

                        # Reattach runtime references before add
                        obj.shared = getattr(self, 'shared', None)
                        obj.home_chunk = self

                        # Reattach planet if we serialized only the id
                        pid = int(getattr(obj, "_home_planet_id", 0) or 0)
                        if getattr(obj, "home_planet", None) is None and pid:
                            hp = self.get_object_by_id(pid)
                            if hp:
                                obj.home_planet = hp
                                print(f"      ↪ reattached home_planet id={pid}")
                            else:
                                print(f"      ⚠️ home_planet id={pid} not found in chunk")

                        # Now add to chunk (this should also link id maps)
                        self.add_object(obj)

                        # Finish vessel runtime rebuild
                        if obj.shared is not None:
                            try:
                                obj.calculate_vessel_stats()
                            except Exception as e:
                                print(f"      ⚠️ stats rebuild failed: {e}")
                        obj._ensure_payload_behavior()

                    except Exception as e:
                        print(f"   ❌ [{idx}] Failed to add Vessel id={getattr(obj,'object_id','?')}: {e}")

            # Summary
            print(
                f"✅ After deserialization: {len(self.objects)} objects in chunk "
                f"(id_to_object has {len(self.id_to_object)} entries)"
            )
            vessels_now = [o for o in self.objects if isinstance(o, Vessel)]
            print(f"   🚀 Vessel count after load: {len(vessels_now)}")
            for v in vessels_now:
                print(
                    f"      • Vessel id={v.object_id}, name={getattr(v, 'name', '?')}, "
                    f"agency={getattr(v, 'agency_id', '?')}, home_planet="
                    f"{getattr(getattr(v, 'home_planet', None), 'object_id', None)}"
                )

        except Exception as e:
            print(f"❌ Failed to load chunk {self.path}: {e}")
            self.objects = []
            self.id_to_object.clear()




    def get_object_by_id(self, obj_id: int) -> Union[PhysicsObject, None]:
        return self.id_to_object.get(obj_id)

    def remove_object(self, obj_or_id):
        oid = getattr(obj_or_id, "object_id", obj_or_id)
        inst = self.id_to_object.pop(oid, None)
        if inst is not None:
            try:
                self.objects.remove(inst)
            except ValueError:
                pass
        if hasattr(self.manager, "unregister_object"):
            try:
                self.manager.unregister_object(oid)
            except Exception:
                pass
