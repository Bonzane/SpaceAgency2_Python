#This file is all about game logic and file management

import os
import pathlib
import time
from datetime import datetime
from player import Player
from agency import Agency
import gameobjects
import pickle

from chunk_manager import ChunkManager


class Game:
    def __init__(self, root, tickrate, simrate):
        self.active = False
        self.base_path = pathlib.Path(root)
        self.universe_path = self.base_path / "universe"
        self.chunk_manager = ChunkManager(self.universe_path, self)
        self.simsec_per_tick = simrate / tickrate


        if not self.base_path.exists():
            self.base_path.mkdir(parents=True)
            print(f"Directory created: {self.base_path}")

        # Perform big bang if needed
        if not (self.base_path / "bigBang.txt").exists():
            print("No game files detected. Performing Big Bang...")
            if self.big_bang():
                self.active = True
        else: 
            self.active = True

        #Load the game if the files are ready, otherwise apologize and beg for forgiveness. 
        if self.active: 
            self.load_game()    
        else:   
            print(f"The game failed to load. Check for errors. Sorry :(")

    def big_bang(self):
        print("🌌 ---------- BIG BANG ----------")
        print("🚀 Creating universe, please wait...")
        try:
            (self.universe_path / "galaxies" / "1" / "systems").mkdir(parents=True, exist_ok=True)
        except Exception as e:
            print(f"❌ Failed to create base directories. (Does the server have permission to access your game path?)\nHere's the error: {e}")
            return False
        print("✅ Created Galaxies Directory")
        print("✅ Created Milky-Way Root Directory")
        print("✅ Created Milky-Way Systems Directory")

        self.create_universe_galaxymap()
        self.create_milkyway_starmap()
        self.create_home_chunk()

        return True

    def load_game(self):
        self.chunk_manager.load_chunk(1,1)

    def create_universe_galaxymap(self):
        chunk_path = self.universe_path / "intergalacticMap.sa2map" 
        with open(chunk_path, "w") as file:
            file.write("0")
  

        print("✅ Created Universe Galaxy Map")

    def create_home_chunk(self):
        chunk_path = self.universe_path / "galaxies" / "1" / "systems" / "system_1.chunk"
        print("🔧 Building Home Chunk")
        sun = gameobjects.Sun()
        print("📍 Added The Sun")
        earth = gameobjects.Earth()
        print("📍 Added Earth")
        with open(chunk_path, "wb") as file:
            pickle.dump([sun, earth], file)


        print("✅ Created Home Chunk")


    def create_milkyway_starmap(self):
        chunk_path = self.universe_path / "galaxies" / "1" / "interstellarMap.sa2map"
        with open(chunk_path, "w") as file:
            file.write("0")

        print("✅ Created Milky Way Starmap")





