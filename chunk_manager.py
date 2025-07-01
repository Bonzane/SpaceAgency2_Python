import threading
import time
from pathlib import Path
from typing import Dict, Tuple
from chunk import Chunk 

class ChunkManager:
    def __init__(self, root_directory: Path, game, tickrate: int = 60):
        print("🧠The chunkmanager has awoken 👀")
        self.root = Path(root_directory)
        self.loaded_chunks: Dict[Tuple[int, int], Chunk] = {}
        self.tickrate = tickrate 
        self.game = game
        self._start_threads()


    def load_chunk(self, galaxy: int, system: int):
        key = (galaxy, system)
        if key in self.loaded_chunks:
            print(f"🌀 Chunk {key} already loaded.")
            return

        filepath = self._get_chunk_path(galaxy, system)
        chunk = Chunk(galaxy, system, filepath)
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
            for chunk in self.loaded_chunks.values():
                if chunk.is_ready():
                    chunk.update_objects(self.game.simsec_per_tick)
            elapsed = time.time() - start
            delay = max(0, 1 / self.tickrate - elapsed)
            time.sleep(delay)

    def _autosave_loop(self):
        while True:
            time.sleep(60)  # Autosave interval
            print("💾 Autosaving all chunks...")
            self.serialize_all_chunks()

    def serialize_all_chunks(self):
        for chunk in self.loaded_chunks.values():
            chunk.serialize_chunk()

    def how_many_chunks_loaded(self) -> int:
        return len(self.loaded_chunks)
