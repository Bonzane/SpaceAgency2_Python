import threading
import time
from pathlib import Path
from typing import Dict, Tuple
from chunk_c import Chunk 
import json

class ChunkManager:
    def __init__(self, shared, root_directory: Path, game, tickrate: int = 60):
        print("🧠The chunkmanager has awoken 👀")
        self.root = Path(root_directory)
        self.loaded_chunks: Dict[Tuple[int, int], Chunk] = {}
        self.tickrate = tickrate 
        self.game = game
        self.shared = shared
        shared.chunk_manager = self
        self.object_id_to_chunk: Dict[int, Tuple[int, int]] = {}
        self._lock = threading.RLock()

        # Chunk scales (km per unit) by level
        self.system_scale_km_per_unit = 1.0
        self.starmap_scale_km_per_unit = 1.0e6
        self.universe_scale_km_per_unit = 1.0e9

        # Transition radii (km or scaled units as noted)
        self.system_exit_radius_km = 2.0e13  # leave system to galaxy starmap
        self.starmap_entry_radius = 1.0e10   # starmap units: enter system point
        self.galaxy_boundary_radius = 5.0e11 # starmap units: leave galaxy to universe
        self.universe_entry_radius = 1.0e11  # universe units: enter galaxy from universe

        self._start_threads()


    def load_chunk(self, galaxy: int, system: int):
        key = (galaxy, system)
        if key in self.loaded_chunks:
            print(f"🌀 Chunk {key} already loaded.")
            return

        filepath = self._get_chunk_path(galaxy, system)
        chunk = Chunk(galaxy, system, filepath, self)
        self.loaded_chunks[key] = chunk
        print(f"✅ Chunk {key} loaded.")

    def unload_chunk(self, galaxy: int, system: int):
        key = (galaxy, system)
        if key in self.loaded_chunks:
            print(f"🧹 Unloading chunk {key}")
            self.loaded_chunks[key].serialize_chunk()
            del self.loaded_chunks[key]

    def is_chunk_loaded(self, galaxy: int, system: int) -> bool:
        return (galaxy, system) in self.loaded_chunks

    def scale_for(self, galaxy: int, system: int) -> float:
        """Return km per unit for the chunk level."""
        if galaxy == 0:
            return self.universe_scale_km_per_unit
        if system == 0:
            return self.starmap_scale_km_per_unit
        return self.system_scale_km_per_unit

    def _get_chunk_path(self, galaxy: int, system: int) -> Path:
        if galaxy == 0:
            return self.root / "intergalacticMap.sa2map"
        elif system == 0:
            return self.root / "galaxies" / str(galaxy) / "interstellarMap.sa2map"
        else:
            return self.root / "galaxies" / str(galaxy) / "systems" / f"system_{system}.chunk"

    def _start_threads(self):
        threading.Thread(target=self._tick_loop, daemon=True).start()
        threading.Thread(target=self._autosave_loop, daemon=True).start()

    def _tick_loop(self):
        while True:
            start = time.time()
            with self._lock:
                for chunk in self.loaded_chunks.values():
                    if chunk.is_ready():
                        chunk.update_objects(self.game.simsec_per_tick)
            elapsed = time.time() - start
            delay = max(0, 1 / self.tickrate - elapsed)
            time.sleep(delay)

    def _autosave_loop(self):
        while True:
            time.sleep(60)  # Autosave interval
            print("💾 Autosaving all chunks + meta...")
            self.serialize_all_chunks()
            # Ask Game to save players/agencies too (atomic JSON)
            try:
                self.game.save_meta()     # NEW
            except Exception as e:
                print(f"⚠️ Meta save failed: {e}")



    def serialize_all_chunks(self):
        with self._lock:
            for chunk in self.loaded_chunks.values():
                try:
                    chunk.serialize_chunk()
                except Exception as e:
                    print(f"❌ Failed to serialize chunk {chunk.galaxy, chunk.system}: {e}")


    def how_many_chunks_loaded(self) -> int:
        return len(self.loaded_chunks)
    

    def register_object(self, object_id, galaxy, system):
        self.object_id_to_chunk[object_id] = (galaxy, system)

    def unregister_object(self, object_id: int) -> bool:
        """Forget which chunk an object_id lives in. Returns True if it was present."""
        with self._lock:
            return self.object_id_to_chunk.pop(object_id, None) is not None

    def get_chunk_from_object_id(self, object_id):
        with self._lock:
            chunk_coords = self.object_id_to_chunk.get(object_id)
        return self.loaded_chunks.get(chunk_coords) if chunk_coords else None

    # ------------- Map point persistence (sa2map) ----------------
    def _load_points(self, path: Path) -> list:
        if not path.exists() or path.stat().st_size == 0:
            return []
        try:
            import json
            with open(path, "r") as f:
                data = json.load(f)
            pts = data.get("points", [])
            if isinstance(pts, list):
                return pts
        except Exception:
            pass
        return []

    def _save_points(self, path: Path, points: list) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        import json, os
        tmp = path.with_suffix(path.suffix + ".tmp")
        with open(tmp, "w") as f:
            json.dump({"points": points}, f)
        os.replace(tmp, path)

    def _ensure_starmap_points(self, galaxy: int) -> list:
        path = self._get_chunk_path(galaxy, 0)
        pts = self._load_points(path)
        if pts:
            return pts
        pts = [{"id": 1, "name": "Home System", "x": 0.0, "y": 0.0}]
        self._save_points(path, pts)
        return pts

    def _ensure_universe_points(self) -> list:
        path = self._get_chunk_path(0, 0)
        pts = self._load_points(path)
        if pts:
            return pts
        pts = [{"id": 1, "name": "Milky Way", "x": 0.0, "y": 0.0}]
        self._save_points(path, pts)
        return pts

    def _add_system_point_if_missing(self, galaxy: int, system_id: int) -> None:
        pts = self._ensure_starmap_points(galaxy)
        if any(int(p.get("id", 0)) == int(system_id) for p in pts):
            return
        import math
        r = 1.0e10 + system_id * 1.0e9
        ang = system_id * 0.31
        x = r * math.cos(ang)
        y = r * math.sin(ang)
        pts.append({"id": int(system_id), "name": f"System {system_id}", "x": x, "y": y})
        self._save_points(self._get_chunk_path(galaxy, 0), pts)

    def _add_galaxy_point_if_missing(self, galaxy: int) -> None:
        pts = self._ensure_universe_points()
        if any(int(p.get("id", 0)) == int(galaxy) for p in pts):
            return
        import math
        r = 1.0e11 + galaxy * 5.0e10
        ang = galaxy * 0.17
        x = r * math.cos(ang)
        y = r * math.sin(ang)
        pts.append({"id": int(galaxy), "name": f"Galaxy {galaxy}", "x": x, "y": y})
        self._save_points(self._get_chunk_path(0, 0), pts)

    def get_starmap_points(self, galaxy: int) -> list:
        return self._ensure_starmap_points(galaxy)

    def get_universe_points(self) -> list:
        return self._ensure_universe_points()

    # ------------- Vessel migration ----------------
    def _remove_from_chunk(self, vessel):
        try:
            ch = getattr(vessel, "home_chunk", None)
            if ch:
                ch.remove_object(vessel)
        except Exception:
            pass

    def _add_to_chunk(self, vessel, galaxy: int, system: int):
        self.load_chunk(galaxy, system)
        ch = self.loaded_chunks.get((galaxy, system))
        if ch:
            ch.add_object(vessel)
        return ch

    def transfer_to_starmap(self, vessel):
        ch = getattr(vessel, "home_chunk", None)
        if not ch or ch.system <= 0:
            return
        galaxy = ch.galaxy
        system = ch.system
        self._add_system_point_if_missing(galaxy, system)
        dirx, diry = vessel._direction_from_origin()
        spawn_units = 2.0e13 / self.starmap_scale_km_per_unit
        vessel.position = (dirx * spawn_units, diry * spawn_units)
        vessel.velocity = (vessel.velocity[0] / self.starmap_scale_km_per_unit,
                           vessel.velocity[1] / self.starmap_scale_km_per_unit)
        vessel.home_planet = None
        self._remove_from_chunk(vessel)
        new_ch = self._add_to_chunk(vessel, galaxy, 0)
        if new_ch:
            vessel.home_chunk = new_ch
            # move controller if any
            pid = int(getattr(vessel, "controlled_by", 0) or 0)
            if pid in self.shared.players:
                p = self.shared.players[pid]
                p.galaxy = galaxy
                p.system = 0

    def transfer_to_system(self, vessel, galaxy: int, target_system: int, target_point: tuple):
        dirx, diry = vessel._direction_to_point(target_point)
        spawn_km = 2.0e13  # system scale km
        vessel.position = (dirx * spawn_km, diry * spawn_km)
        vessel.velocity = (vessel.velocity[0] * self.starmap_scale_km_per_unit,
                           vessel.velocity[1] * self.starmap_scale_km_per_unit)
        vessel.home_planet = None
        self._remove_from_chunk(vessel)
        new_ch = self._add_to_chunk(vessel, galaxy, target_system)
        if new_ch:
            vessel.home_chunk = new_ch
            pid = int(getattr(vessel, "controlled_by", 0) or 0)
            if pid in self.shared.players:
                p = self.shared.players[pid]
                p.galaxy = galaxy
                p.system = target_system

    def transfer_to_universe(self, vessel):
        ch = getattr(vessel, "home_chunk", None)
        if not ch or ch.system != 0 or ch.galaxy <= 0:
            return
        galaxy = ch.galaxy
        self._add_galaxy_point_if_missing(galaxy)
        dirx, diry = vessel._direction_from_origin()
        spawn_units = 2.0e13 / self.universe_scale_km_per_unit
        vessel.position = (dirx * spawn_units, diry * spawn_units)
        vessel.velocity = (vessel.velocity[0] / self.universe_scale_km_per_unit,
                           vessel.velocity[1] / self.universe_scale_km_per_unit)
        vessel.home_planet = None
        self._remove_from_chunk(vessel)
        new_ch = self._add_to_chunk(vessel, 0, 0)
        if new_ch:
            vessel.home_chunk = new_ch
            pid = int(getattr(vessel, "controlled_by", 0) or 0)
            if pid in self.shared.players:
                p = self.shared.players[pid]
                p.galaxy = 0
                p.system = 0

    def transfer_to_galaxy(self, vessel, galaxy: int, target_point: tuple):
        dirx, diry = vessel._direction_to_point(target_point)
        spawn_units = 2.0e13 / self.starmap_scale_km_per_unit
        vessel.position = (dirx * spawn_units, diry * spawn_units)
        vessel.velocity = (vessel.velocity[0] * self.universe_scale_km_per_unit,
                           vessel.velocity[1] * self.universe_scale_km_per_unit)
        vessel.home_planet = None
        self._ensure_starmap_points(galaxy)
        self._remove_from_chunk(vessel)
        new_ch = self._add_to_chunk(vessel, galaxy, 0)
        if new_ch:
            vessel.home_chunk = new_ch
            pid = int(getattr(vessel, "controlled_by", 0) or 0)
            if pid in self.shared.players:
                p = self.shared.players[pid]
                p.galaxy = galaxy
                p.system = 0
