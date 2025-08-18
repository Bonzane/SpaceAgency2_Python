#This file is all about game logic and file management

import os
import pathlib
import time
from datetime import datetime
from player import Player
from agency import Agency
import gameobjects
import pickle
import threading

from chunk_manager import ChunkManager


class Game:
    def __init__(self, root, tickrate, simrate, shared):
        self.active = False
        self.base_path = pathlib.Path(root)
        self.universe_path = self.base_path / "universe"
        self.chunk_manager = ChunkManager(shared, self.universe_path, self)
        self.simsec_per_tick = simrate / tickrate
        self.shared = shared
        self.playersdatafile = None
        self.agenciesdatafile = None

        self._meta_lock = threading.RLock()

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

    async def _timer_broadcast_agency_list(self):
        while True:
            await self.broadcast_agency_list()
            await asyncio.sleep(30)



    def big_bang(self):
        print("🌌 ---------- BIG BANG ----------")
        print("🚀 Creating universe, please wait...")
        try:
            (self.universe_path / "galaxies" / "1" / "systems").mkdir(parents=True, exist_ok=True)
            with open(self.universe_path / "bigBang.txt", "w") as f:
                f.write(f"This universe was created on {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            with open(self.universe_path / "players.sa2", "w") as f:
                self.playersdatafile = f
            with open(self.universe_path / "agencies.sa2", "w") as f:
                self.agenciesdatafile = f
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
        # 1) Core
        self.chunk_manager.load_chunk(1,1)
        # 2) Agencies + Players
        try:
            self.load_meta()
        except Exception as e:
            print(f"⚠️ Failed to load meta (players/agencies). Starting fresh. Error: {e}")

    # ====== META FUNCTIONS ====
    def _atomic_write(self, path: pathlib.Path, data_bytes: bytes):
        tmp = path.with_suffix(path.suffix + ".tmp")
        with open(tmp, "wb") as f:
            f.write(data_bytes)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)

    def save_meta(self):
        """
        Save agencies then players (JSON), atomically. Use only thin snapshots.
        """
        with self._meta_lock:
            # Agencies first
            agencies_path = self.universe_path / "agencies.sa2.json"
            players_path  = self.universe_path / "players.sa2.json"

            # --- Agencies snapshot ---
            agencies_payload = {
                "version": 1,
                "saved_at": datetime.utcnow().isoformat() + "Z",
                "agencies": []
            }

            for ag_id, agency in getattr(self.shared, "agencies", {}).items():
                # buildings snapshot via to_json() if available
                bases = {}
                for base_id, buildings in agency.bases_to_buildings.items():
                    bases[str(base_id)] = []
                    for b in buildings:
                        if hasattr(b, "to_json") and callable(b.to_json):
                            bases[str(base_id)].append(b.to_json())
                        else:
                            # minimal fallback
                            bases[str(base_id)].append({
                                "type": int(getattr(b, "type", 0)),
                                "level": int(getattr(b, "level", 1)),
                                "constructed": bool(getattr(b, "constructed", True)),
                                "planet_id": int(getattr(b, "planet_id", 0)),
                            })

                agencies_payload["agencies"].append({
                    "id64": int(agency.id64),
                    "name": agency.name,
                    "is_public": bool(agency.is_public),
                    "members": list(map(int, agency.members)),
                    "primarycolor": int(agency.primarycolor),
                    "secondarycolor": int(agency.secondarycolor),
                    "income_per_second": int(getattr(agency, "income_per_second", 0)),
                    "base_inventories": agency.base_inventories,
                    "base_capacities": agency.base_inventory_capacities,
                    "vessels": [v.get_id() for v in agency.get_all_vessels()] if hasattr(agency, "get_all_vessels") else [],
                    "bases_to_buildings": bases,
                })

            self._atomic_write(agencies_path, json.dumps(agencies_payload, separators=(',',':')).encode('utf-8'))

            # --- Players snapshot ---
            players_payload = {
                "version": 1,
                "saved_at": datetime.utcnow().isoformat() + "Z",
                "players": []
            }

            for steamID, p in getattr(self.shared, "players", {}).items():
                players_payload["players"].append({
                    "steamID": int(p.steamID),
                    "x": float(p.x),
                    "y": float(p.y),
                    "money": int(p.money),
                    "galaxy": int(getattr(p, "galaxy", 1)),
                    "system": int(getattr(p, "system", 1)),
                    "agency_id": int(getattr(p, "agency_id", 0)),
                    "controlled_vessel_id": int(getattr(p, "controlled_vessel_id", -1)),
                })

            self._atomic_write(players_path, json.dumps(players_payload, separators=(',',':')).encode('utf-8'))

            print("✅ Saved agencies & players (atomic JSON)")

    def load_meta(self):
        """
        Load agencies then players from JSON. If an agency already exists in memory,
        update it; otherwise create a new one. Players are recreated as thin objects
        and reattached to shared.
        """
        with self._meta_lock:
            agencies_path = self.universe_path / "agencies.sa2.json"
            players_path  = self.universe_path / "players.sa2.json"

            # --- Agencies ---
            if agencies_path.exists():
                with open(agencies_path, "rb") as f:
                    data = json.loads(f.read().decode("utf-8"))

                self.shared.agencies = getattr(self.shared, "agencies", {})

                for a in data.get("agencies", []):
                    aid = int(a["id64"])
                    if aid in self.shared.agencies:
                        agency = self.shared.agencies[aid]
                    else:
                        agency = Agency(name=a["name"], shared=self.shared)
                        agency.manually_set_id(aid)
                        self.shared.agencies[aid] = agency

                    agency.set_name(a["name"])
                    agency.set_public(bool(a.get("is_public", True)))
                    agency.members = list(map(int, a.get("members", [])))
                    agency.primarycolor = int(a.get("primarycolor", 0))
                    agency.secondarycolor = int(a.get("secondarycolor", 0))
                    agency.income_per_second = int(a.get("income_per_second", 0))
                    agency.base_inventories = a.get("base_inventories", {})
                    agency.base_inventory_capacities = a.get("base_capacities", {})

                    # Rebuild buildings
                    rebuilt = {}
                    bases_json = a.get("bases_to_buildings", {})
                    for base_id_str, buildings in bases_json.items():
                        base_id = int(base_id_str)
                        rebuilt[base_id] = []
                        for bj in buildings:
                            # Prefer a classmethod if you have it:
                            # b = Building.from_json(bj, self.shared, agency)
                            # Fallback: minimal constructor using your Building signature
                            # NOTE: Adjust to your actual Building API.
                            try:
                                from buildings import Building, BuildingType
                                if hasattr(Building, "from_json") and callable(Building.from_json):
                                    b = Building.from_json(bj, self.shared, agency)
                                else:
                                    btype = bj.get("type")
                                    lvl   = bj.get("level", 1)
                                    planet= bj.get("planet_id", 0)
                                    b = Building(BuildingType(btype), self.shared, planet, lvl, agency)
                                    b.constructed = bool(bj.get("constructed", True))
                                    if "level" in bj:
                                        b.level = int(lvl)
                            except Exception as e:
                                print(f"⚠️ Could not rebuild a building on base {base_id}: {e}")
                                continue
                            rebuilt[base_id].append(b)

                    if rebuilt:
                        agency.bases_to_buildings = rebuilt
                    agency.update_attributes()

                print("✅ Loaded agencies")

            # --- Players ---
            if players_path.exists():
                with open(players_path, "rb") as f:
                    data = json.loads(f.read().decode("utf-8"))

                self.shared.players = getattr(self.shared, "players", {})

                for pj in data.get("players", []):
                    steamID = int(pj["steamID"])
                    if steamID in self.shared.players:
                        pl = self.shared.players[steamID]
                    else:
                        # session isn't persisted; pass None and reattach later as clients connect
                        pl = Player(session=None, steamID=steamID, shared=self.shared)
                        self.shared.players[steamID] = pl

                    pl.x = float(pj.get("x", 0))
                    pl.y = float(pj.get("y", 0))
                    pl.money = int(pj.get("money", pl.money))
                    pl.galaxy = int(pj.get("galaxy", 1))
                    pl.system = int(pj.get("system", 1))
                    pl.agency_id = int(pj.get("agency_id", 0))
                    pl.controlled_vessel_id = int(pj.get("controlled_vessel_id", -1))

                print("✅ Loaded players")


    # ===== BIG BANG FUNCTIONS ====

    def create_universe_galaxymap(self):
        chunk_path = self.universe_path / "intergalacticMap.sa2map" 
        with open(chunk_path, "w") as file:
            file.write("0")
  

        print("✅ Created Universe Galaxy Map")

    def create_home_chunk(self):
        chunk_path = self.universe_path / "galaxies" / "1" / "systems" / "system_1.chunk"
        print("🔧 Building Home Chunk")
        self.sun = gameobjects.Sun()
        print("📍 Added The Sun")
        self.earth = gameobjects.Earth()
        print("📍 Added Earth")
        self.luna = gameobjects.Luna(self.earth)
        print("📍 Added The Moon")
        self.mercury = gameobjects.Mercury()
        self.venus = gameobjects.Venus()
        self.mars = gameobjects.Mars()
        self.jupiter = gameobjects.Jupiter()
        self.saturn = gameobjects.Saturn()
        self.uranus = gameobjects.Uranus()
        self.neptune = gameobjects.Neptune()
        print("📍 Added Other planets")

        with open(chunk_path, "wb") as file:
            pickle.dump([self.sun, self.earth, self.luna, self.mercury, self.venus, self.mars, self.jupiter, self.saturn, self.uranus, self.neptune], file)


        print("✅ Created Home Chunk")


    def create_milkyway_starmap(self):
        chunk_path = self.universe_path / "galaxies" / "1" / "interstellarMap.sa2map"
        with open(chunk_path, "w") as file:
            file.write("0")

        print("✅ Created Milky Way Starmap")





