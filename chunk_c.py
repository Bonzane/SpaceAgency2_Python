import math
import pickle
from pathlib import Path
from typing import List, Union
from gameobjects import Sun, Earth, PhysicsObject, ObjectType, GameObject
import numpy as np
from physics import G
from packet_types import DataGramPacketType
import struct
from vessels import Vessel, VesselState
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

        # --- Rehydrate runtime-only references for vessels
        if isinstance(obj, Vessel):
            # live refs
            obj.shared = self.manager.shared
            obj.home_chunk = self

            # robustly relink the vessel's home_planet to THIS chunk's instance
            pid = None
            hp = getattr(obj, "home_planet", None)
            if hp is not None:
                pid = getattr(hp, "object_id", None)
            if not pid:
                pid = getattr(obj, "launchpad_planet_id", None)  # fallback

            if pid:
                same_chunk_planet = self.get_object_by_id(int(pid))
                if same_chunk_planet is not None:
                    obj.home_planet = same_chunk_planet  # ensure pointer matches this chunk

            cid = int(getattr(obj, "controlled_by", 0) or 0)
            if cid:
                p = self.manager.shared.players.get(cid)
                if not p or not p.session or not p.session.alive:
                    obj.controlled_by = 0
                    obj.control_state[VesselState.FORWARD_THRUSTER_ON] = False
                    obj.control_state[VesselState.REVERSE_THRUSTER_ON] = False
                    obj.control_state[VesselState.CCW_THRUST_ON] = False
                    obj.control_state[VesselState.CW_THRUST_ON] = False

            # derived/calculated state that depends on shared/component_data
            try:
                obj.calculate_vessel_stats()
            except Exception as e:
                print(f"⚠️ Vessel {obj.object_id} calculate_vessel_stats failed: {e}")

    def signed_to_unsigned64(self, value: int) -> int:
        return value % (1 << 64)

    def update_objects(self, dt=1.0):
        # ===== choose array backend (GPU if available) =====
        try:
            import cupy as xp   # GPU
            on_gpu = True
        except Exception:
            import numpy as xp  # CPU
            on_gpu = False

        # Filter physics-enabled objects
        physics_objects = [obj for obj in self.objects if hasattr(obj, "mass")]
        n = len(physics_objects)
        if n < 2:
            return

        # --- classify objects ---
        from gameobjects import ObjectType
        def is_asteroid(o):
            return getattr(o, "object_type", None) == ObjectType.ASTEROID_BELT_ASTEROID

        def is_massive(o):
            # Sun + major planets (you can include Jupiter, Saturn, etc.)
            return getattr(o, "object_type", None) in (
                ObjectType.SUN, ObjectType.MERCURY, ObjectType.VENUS, ObjectType.EARTH,
                ObjectType.MARS, ObjectType.JUPITER, ObjectType.SATURN, ObjectType.URANUS,
                ObjectType.NEPTUNE
            )

        asteroid_idx = [i for i, o in enumerate(physics_objects) if is_asteroid(o)]
        massive_idx  = [i for i, o in enumerate(physics_objects) if is_massive(o)]
        non_ast_idx  = [i for i in range(n) if i not in asteroid_idx]

        # --- numpy (or cupy) views ---
        pos_np  = np.array([obj.position for obj in physics_objects], dtype=np.float64)
        vel_np  = np.array([obj.velocity for obj in physics_objects], dtype=np.float64)
        mass_np = np.array([obj.mass     for obj in physics_objects], dtype=np.float64)

        # Work arrays in chosen backend
        pos  = xp.asarray(pos_np)
        vel  = xp.asarray(vel_np)
        mass = xp.asarray(mass_np)

        # ====== 1) Forces/updates for non-asteroids (small set, keep your logic) ======
        # We’ll reuse your pairwise loop but only for the “non-asteroid” subset to keep cost low.
        if len(non_ast_idx) > 1:
            forces = np.zeros((len(non_ast_idx), 2), dtype=np.float64)
            vessel_max_pull = {}

            # Pairwise among non-asteroids only
            for ii, i in enumerate(non_ast_idx):
                for jj, j in enumerate(non_ast_idx):
                    if j <= i:
                        continue
                    obj_i = physics_objects[i]
                    obj_j = physics_objects[j]

                    diff = pos_np[j] - pos_np[i]
                    raw_dist = float(np.hypot(diff[0], diff[1]))
                    raw_dist_sq = raw_dist * raw_dist

                    # Region tracking for vessels vs planets
                    from vessels import Vessel
                    if isinstance(obj_i, Vessel) and is_planet(obj_j):
                        new_region = obj_j.check_in_region(raw_dist)
                        maybe_update_vessel_region(self.manager.shared, obj_i, obj_j, new_region)
                    if isinstance(obj_j, Vessel) and is_planet(obj_i):
                        new_region = obj_i.check_in_region(raw_dist)
                        maybe_update_vessel_region(self.manager.shared, obj_j, obj_i, new_region)

                    radius_i = getattr(obj_i, "radius_km", 0.0)
                    radius_j = getattr(obj_j, "radius_km", 0.0)
                    softening_km = 0.8 * max(radius_i, radius_j)

                    sep_from_surfaces = max(0.0, raw_dist - (radius_i + radius_j))
                    effective_sep = sep_from_surfaces + softening_km
                    if raw_dist > 0:
                        direction = diff / raw_dist
                    else:
                        direction = np.zeros(2, dtype=np.float64)

                    from physics import G
                    force_mag = G * mass_np[i] * mass_np[j] / (effective_sep ** 2)
                    force_vec = force_mag * direction

                    if raw_dist >= max(radius_i, radius_j) * 1.15:
                        forces[ii] += force_vec
                        # add opposite to j
                        kk = non_ast_idx.index(j)
                        forces[kk] -= force_vec

                    # Track strongest pull for vessels
                    if isinstance(obj_i, Vessel):
                        if obj_i.object_id not in vessel_max_pull or force_mag > vessel_max_pull[obj_i.object_id][1]:
                            vessel_max_pull[obj_i.object_id] = (physics_objects[j], force_mag)
                    if isinstance(obj_j, Vessel):
                        if obj_j.object_id not in vessel_max_pull or force_mag > vessel_max_pull[obj_j.object_id][1]:
                            vessel_max_pull[obj_j.object_id] = (physics_objects[i], force_mag)

            # Assign strongest pull back
            for vessel_id, (pulling_obj, strength) in vessel_max_pull.items():
                vessel = self.id_to_object.get(vessel_id)
                if vessel:
                    vessel.strongest_gravity_source = pulling_obj
                    vessel.strongest_gravity_force = strength

            # Integrate non-asteroids with clamped acceleration
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
                    ax *= scale; ay *= scale
                obj.do_update(dt, (ax, ay))

        # ====== 2) Vectorized asteroid update (Sun + massive bodies only) ======
        if asteroid_idx:
            a_pos = pos[asteroid_idx]                # (Na, 2)
            a_vel = vel[asteroid_idx]                # (Na, 2)
            m_pos = pos[massive_idx] if massive_idx else xp.zeros((0, 2), dtype=pos.dtype)  # (Nm, 2)
            m_mass = mass[massive_idx] if massive_idx else xp.zeros((0,), dtype=mass.dtype) # (Nm, )

            if m_pos.shape[0] > 0:
                # a_pos[:,None,:] -> (Na,1,2), m_pos[None,:,:] -> (1,Nm,2)
                diff = m_pos[None, :, :] - a_pos[:, None, :]         # (Na, Nm, 2)
                r2 = xp.sum(diff * diff, axis=2) + 1e-12             # (Na, Nm)
                r = xp.sqrt(r2)
                # softening using a constant (asteroids are tiny)
                soft = xp.asarray(500.0, dtype=r.dtype)               # km, tweakable
                eff = r + soft
                # G * M / r^3 term
                from physics import G
                inv_r3 = 1.0 / xp.maximum(eff * r2, 1e-12)
                # (Na, Nm, 1) * (Na, Nm, 2)
                a_per_body = (G * m_mass[None, :, None]) * diff * inv_r3[:, :, None]
                a_total = xp.sum(a_per_body, axis=1)                  # (Na, 2)
            else:
                a_total = xp.zeros_like(a_pos)

            # Clamp accel
            MAX_ACCEL = 1e3
            acc_mag = xp.sqrt(a_total[:, 0] ** 2 + a_total[:, 1] ** 2)
            scale = xp.minimum(1.0, MAX_ACCEL / xp.maximum(acc_mag, 1e-9))
            a_total = a_total * scale[:, None]

            # Semi-implicit Euler
            a_vel = a_vel + a_total * dt
            a_pos = a_pos + a_vel * dt

            # Write back to backend arrays
            vel[asteroid_idx] = a_vel
            pos[asteroid_idx] = a_pos

            # Bring updated asteroid states back to Python/NumPy
            pos_np_updated = xp.asnumpy(pos) if on_gpu else pos
            vel_np_updated = xp.asnumpy(vel) if on_gpu else vel

            # Commit asteroid transforms to objects
            for i in asteroid_idx:
                physics_objects[i].velocity = (float(vel_np_updated[i, 0]), float(vel_np_updated[i, 1]))
                physics_objects[i].position = (float(pos_np_updated[i, 0]), float(pos_np_updated[i, 1]))

        # ====== 3) Ambient temps (same logic as before) ======
        for obj in physics_objects:
            ox, oy = getattr(obj, "position", (0.0, 0.0))
            dist_km = math.hypot(ox, oy)
            space_temp = ambient_temp_simple(dist_km)

            from vessels import Vessel
            if isinstance(obj, Vessel) and obj.home_planet:
                px, py = obj.home_planet.position
                alt_km = obj.altitude
                atm_height = obj.home_planet.atmosphere_km
                if alt_km <= atm_height:
                    surface_temp = getattr(obj.home_planet, "planet_surface_temp", 288.15)
                    t = max(0.0, min(1.0, alt_km / atm_height))
                    obj.ambient_temp_K = surface_temp * (1 - t) + space_temp * t
                    continue
            obj.ambient_temp_K = space_temp

        # ====== 4) Non-physics objects’ do_update ======
        for obj in self.objects:
            if obj not in physics_objects:
                obj.do_update(dt, (0.0, 0.0))

        # ====== 5) Stream packet build (unchanged) ======
        chunkpacket = bytearray()
        chunkpacket.append(DataGramPacketType.OBJECT_STREAM)
        chunkpacket += struct.pack('<H',  self.manager.shared.udp_server.objstream_seq)
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
                obj.rotation
            )

        for player in self.manager.shared.players.values():
            if player.galaxy == self.galaxy and player.system == self.system:
                session = player.session
                if session and session.udp_port and session.alive:
                    addr = (session.remote_ip, session.udp_port)
                    self.manager.shared.udp_server.transport.sendto(chunkpacket, addr)



    def serialize_chunk(self):
        print(f"💾 Serializing chunk to {self.path}")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, 'wb') as f:
            pickle.dump(self.objects, f, protocol=pickle.HIGHEST_PROTOCOL)

    def deserialize_chunk(self):
        if not self.path.exists():
            print(f"⚠️ No chunk file found at {self.path}.")
            return
        try:
            with open(self.path, 'rb') as f:
                objs = pickle.load(f)

            # DEBUG: show what actually came out of the pickle
            from collections import Counter
            type_counts = Counter(type(o).__name__ for o in objs)
            print(f"📦 Chunk {self.galaxy}:{self.system} loaded {len(objs)} objects: "
                + ", ".join(f"{k}={v}" for k,v in type_counts.items()))

            # Pass 1: add non-vessels first so planets are present
            for obj in objs:
                if not isinstance(obj, Vessel):
                    self.add_object(obj)

            # Pass 2: now add vessels; add_object rehydrates & rebuilds stats
            for obj in objs:
                if isinstance(obj, Vessel):
                    self.add_object(obj)

        except Exception as e:
            print(f"❌ Failed to load chunk: {e}")
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
        # Let the manager forget the mapping if it provides that API
        if hasattr(self.manager, "unregister_object"):
            try:
                self.manager.unregister_object(oid)
            except Exception:
                pass
