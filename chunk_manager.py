import threading
import time
from pathlib import Path
from typing import Dict, Tuple
from chunk_c import Chunk 

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

