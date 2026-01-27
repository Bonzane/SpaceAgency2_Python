#This file is all about game logic and file management

import asyncio
from collections import defaultdict
import os
import pathlib
import time
from datetime import datetime
from player import Player
from agency import Agency
import gameobjects
import pickle
import threading
import json
from utils import _coerce_int_keys

from chunk_manager import ChunkManager
from gameobjects import GameObject
from buildings import Building, BuildingType
from astronaut import Astronaut


class Game:
    def __init__(self, root, tickrate, simrate, shared):
        self.active = False
        self.base_path = pathlib.Path(root).resolve()
        self.universe_path = (self.base_path / "universe").resolve()
        self.simsec_per_tick = simrate / tickrate
        self.chunk_manager = ChunkManager(shared, self.universe_path, self)
        self.shared = shared
        self.playersdatafile = None
        self.agenciesdatafile = None

        self._meta_lock = threading.RLock()

        if not self.base_path.exists():
            self.base_path.mkdir(parents=True)
            print(f"Directory created: {self.base_path}")

        # ✅ Properly detect existing game files and avoid overwriting them
        if self._has_existing_game():
            print(f"🟢 Found existing game at {self.universe_path}")
            self.active = True
        else:
            print("No game files detected. Performing Big Bang...")
            if self.big_bang():
                self.active = True

        # Load the game if ready
        if self.active:
            self.load_game()
        else:
            print(f"The game failed to load. Check for errors. Sorry :(")

    async def _timer_broadcast_agency_list(self):
        while True:
            await self.broadcast_agency_list()
            await asyncio.sleep(30)


    def _has_existing_game(self) -> bool:
        """
        Consider the game 'existing' if any canonical save artifacts are present
        under the *universe* path, not the base path.
        """
        try:
            bb = (self.universe_path / "bigBang.txt")
            if bb.exists() and bb.stat().st_size > 0:
                return True

            # First home system chunk
            first_chunk = self.universe_path / "galaxies" / "1" / "systems" / "system_1.chunk"
            if first_chunk.exists() and first_chunk.stat().st_size > 0:
                return True

            # Meta JSON (new snapshot format)
            if (self.universe_path / "agencies.sa2.json").exists():
                return True
            if (self.universe_path / "players.sa2.json").exists():
                return True

            # Legacy placeholders (created by older Big Bang)
            if (self.universe_path / "agencies.sa2").exists():
                return True
            if (self.universe_path / "players.sa2").exists():
                return True

            # Galaxy/Universe maps
            if (self.universe_path / "intergalacticMap.sa2map").exists():
                return True
            if (self.universe_path / "galaxies" / "1" / "interstellarMap.sa2map").exists():
                return True

        except Exception as e:
            print(f"⚠️ Existing game detection error: {e}")
        return False



    def big_bang(self):
        print("🌌 ---------- BIG BANG ----------")
        print("🚀 Creating universe, please wait...")
        try:
            systems_dir = (self.universe_path / "galaxies" / "1" / "systems")
            systems_dir.mkdir(parents=True, exist_ok=True)

            bb = (self.universe_path / "bigBang.txt")
            if not bb.exists():
                with open(bb, "w") as f:
                    f.write(f"This universe was created on {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")

            # Legacy placeholder files: create if missing (harmless if unused)
            pfile = (self.universe_path / "players.sa2")
            afile = (self.universe_path / "agencies.sa2")
            if not pfile.exists():
                with open(pfile, "w") as f:
                    self.playersdatafile = f
            if not afile.exists():
                with open(afile, "w") as f:
                    self.agenciesdatafile = f

        except Exception as e:
            print("❌ Failed to create base directories. "
                  "(Does the server have permission to access your game path?)")
            print(f"Here's the error: {e}")
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


        GameObject.load_id_seq(self.universe_path)

        # Make sure it's strictly higher than any loaded object's id
        max_seen = 0
        for chunk in self.chunk_manager.loaded_chunks.values():
            objs = getattr(chunk, "objects", None)
            if isinstance(objs, dict):
                iterable = objs.values()
            else:
                iterable = objs or []
            for obj in iterable:
                try:
                    oid = int(getattr(obj, "object_id", 0))
                except Exception:
                    oid = 0
                if oid > max_seen:
                    max_seen = oid

        if max_seen + 1 > GameObject._next_id:
            GameObject.set_next_id(max_seen + 1)

        # Persist (keeps file correct even if we only bumped from scan)
        GameObject.save_id_seq(self.universe_path)
        print(f"🔢 Next object id = {GameObject._next_id}")

        
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

                astronauts_payload = {}
                for aid, a in agency.astronauts.items():
                    if hasattr(a, "to_json"):
                        astronauts_payload[int(aid)] = a.to_json()
                    else:
                        astronauts_payload[int(aid)] = {
                            "id32": int(getattr(a, "id32", aid)),
                            "name": str(getattr(a, "name", "Astronaut")),
                            "suit_id": int(getattr(a, "suit_id", 0)),
                            "appearance_id": int(getattr(a, "appearance_id", 0)),
                            "agency_id": int(getattr(a, "agency_id", agency.id64)),
                            "planet_id": (int(a.planet_id) if getattr(a, "planet_id", None) is not None else None),
                            "vessel_id": (int(a.vessel_id) if getattr(a, "vessel_id", None) is not None else None),
                            "level": int(getattr(a, "level", 1)),
                            "exp": float(getattr(a, "exp", 0.0)),
                        }

                astros_by_planet = {
                    int(pid): [int(aid) for aid in sorted(ids)]
                    for pid, ids in agency.planet_to_astronauts.items()
                }


                agencies_payload["agencies"].append({
                    "id64": int(agency.id64),
                    "name": agency.name,
                    "is_public": bool(agency.is_public),
                    "members": list(map(int, agency.members)),
                    "primarycolor": int(agency.primarycolor),
                    "secondarycolor": int(agency.secondarycolor),
                    "flag": int(agency.flag),
                    "income_per_second": int(getattr(agency, "income_per_second", 0)),
                    "base_inventories": agency.base_inventories,
                    "base_capacities": agency.base_inventory_capacities,
                    "vessels": [v.get_id() for v in agency.get_all_vessels()] if hasattr(agency, "get_all_vessels") else [],
                    "bases_to_buildings": bases,
                    "discovered_planets": sorted(int(pid) for pid in getattr(agency, "discovered_planets", set())),
                    "astronauts": astronauts_payload,
                    "astros_by_planet": astros_by_planet,
                    "astro_seq": int(getattr(agency, "_astro_seq", 0)),
                    "rp": int(getattr(agency, "research_points", 0)),
                    "ep": int(getattr(agency, "exploration_points", 0)),
                    "pp": int(getattr(agency, "publicity_points", 0)),
                    "xp": int(getattr(agency, "experience_points", 0)),
                    "quest_state": {
                        str(qid): {
                            "progress": int(state.get("progress", 0)),
                            "completed": bool(state.get("completed", False)),
                            "claimed": bool(state.get("claimed", False)),
                        }
                        for qid, state in getattr(agency, "quest_state", {}).items()
                        if isinstance(state, dict)
                    },
                    "quest_counters": {
                        str(k): int(v)
                        for k, v in getattr(agency, "quest_counters", {}).items()
                        if isinstance(k, str)
                    },
                    "stat_counters": {
                        str(k): float(v)
                        for k, v in getattr(agency, "stat_counters", {}).items()
                        if isinstance(k, str)
                    },
                    "visited_planets": sorted(int(pid) for pid in getattr(agency, "visited_planets", set()) or set()),
                    "age_days": float(getattr(agency, "age_days", 0.0)),

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
                    "terrain_planet_id": int(getattr(p, "terrain_planet_id", 0)),
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
                    agency.flag = int(a.get("flag", 0))
                    agency.income_per_second = int(a.get("income_per_second", 0))
                    try:
                        agency.invited = set(int(x) for x in a.get("invited", []) or [])
                    except Exception:
                        agency.invited = set()

                    agency.research_points    = int(a.get("rp", getattr(agency, "research_points", 0)))
                    agency.exploration_points = int(a.get("ep", getattr(agency, "exploration_points", 0)))
                    agency.publicity_points   = int(a.get("pp", getattr(agency, "publicity_points", 0)))
                    agency.experience_points  = int(a.get("xp", getattr(agency, "experience_points", 0)))

                    raw_quests = a.get("quest_state", {}) or {}
                    if isinstance(raw_quests, dict):
                        agency.quest_state = {
                            str(qid): {
                                "progress": int(qv.get("progress", 0)) if isinstance(qv, dict) else 0,
                                "completed": bool(qv.get("completed", False)) if isinstance(qv, dict) else False,
                                "claimed": bool(qv.get("claimed", False)) if isinstance(qv, dict) else False,
                            }
                            for qid, qv in raw_quests.items()
                        }
                    else:
                        agency.quest_state = {}

                    raw_counters = a.get("quest_counters", {}) or {}
                    if isinstance(raw_counters, dict):
                        agency.quest_counters = {
                            str(k): int(v) for k, v in raw_counters.items()
                        }
                    else:
                        agency.quest_counters = {}

                    raw_stats = a.get("stat_counters", {}) or {}
                    if isinstance(raw_stats, dict):
                        agency.stat_counters = {str(k): float(v) for k, v in raw_stats.items()}
                    else:
                        agency.stat_counters = {}

                    raw_visited = a.get("visited_planets", []) or []
                    try:
                        agency.visited_planets = set(int(pid) for pid in raw_visited)
                    except Exception:
                        agency.visited_planets = set()
                    agency.age_days = float(a.get("age_days", getattr(agency, "age_days", 0.0)))

                    raw_inv = a.get("base_inventories", {}) or {}
                    raw_disc = a.get("discovered_planets", []) or []
                    try:
                        agency.discovered_planets = set(int(pid) for pid in raw_disc)
                    except Exception:
                        agency.discovered_planets = set()
                    agency.base_inventories = {
                        int(pid): {int(rid): int(qty) for rid, qty in inv.items()}
                        for pid, inv in raw_inv.items()
                    }
                    agency.discovered_planets.add(2)

                    raw_caps = a.get("base_capacities", {}) or {}
                    agency.base_inventory_capacities = {int(pid): int(cap) for pid, cap in raw_caps.items()}


                    if not isinstance(agency.base_inventories, dict):
                        agency.base_inventories = {}
                    if not isinstance(agency.base_inventory_capacities, dict):
                        agency.base_inventory_capacities = {}


                    # Rebuild buildings
                    rebuilt = {}
                    bases_json = a.get("bases_to_buildings", {}) or {}
                    for base_id_str, buildings in bases_json.items():
                        base_id = int(base_id_str)  # this IS the planet id
                        rebuilt[base_id] = []
                        for bj in buildings:
                            try:
                                btype = int(bj.get("type", 0))
                                angle = float(bj.get("position_angle", 0.0))

                                try:
                                    type_for_ctor = BuildingType(btype)  # use enum when possible
                                except ValueError:
                                    type_for_ctor = btype                # fall back to raw int

                                b = Building(type_for_ctor, self.shared, angle, base_id, agency)

                                b.constructed = bool(bj.get("constructed", True))
                                b.level = int(bj.get("level", 1))
                                b.construction_progress = int(bj.get("construction_progress", 0))
                            except Exception as e:
                                print(f"⚠️ Could not rebuild a building on base {base_id}: {e}")
                                continue
                            rebuilt[base_id].append(b)

                    if rebuilt:
                        agency.bases_to_buildings = rebuilt

                    # Recompute attributes (storage capacity, unlocks, etc.)
                    agency.update_attributes()

                
                    #Reload astronauts
                    agency.astronauts = {}
                    agency.planet_to_astronauts = defaultdict(set)

                    astros_json = a.get("astronauts", {}) or {}
                    for aid_str, aj in astros_json.items():
                        try:
                            astro = Astronaut.from_json(aj)  # fixed version below
                        except Exception as e:
                            print(f"⚠️ Could not rebuild astronaut {aid_str}: {e}")
                            continue
                        # register with agency; this also places them into planet buckets
                        agency.add_astronaut(astro)

                    # Restore planet mapping explicitly if present (keeps order/consistency)
                    # but tolerate missing/extra IDs gracefully.
                    by_planet = a.get("astros_by_planet", {}) or {}
                    for pid_str, lst in by_planet.items():
                        pid = int(pid_str)
                        for aid in lst:
                            if aid in agency.astronauts:
                                agency.planet_to_astronauts[pid].add(int(aid))

                    # Restore sequence counter
                    agency._astro_seq = int(a.get("astro_seq", 0))


                print("✅ Loaded agencies")
            if self.shared.agencies:
                max_id = max(self.shared.agencies.keys())
                # Always move the counter higher than the highest loaded ID
                if max_id >= self.shared.next_available_agency_id:
                    self.shared.next_available_agency_id = max_id + 1
                print(f"🔢 Next agency ID set to {self.shared.next_available_agency_id}")

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
                    pl.terrain_planet_id = int(pj.get("terrain_planet_id", 0))
                    pl.agency_id = int(pj.get("agency_id", 0))
                    pl.controlled_vessel_id = int(pj.get("controlled_vessel_id", -1))

                print("✅ Loaded players")

                # Re-link vessels to agencies and reattach live refs
                from vessels import Vessel
                for ag in self.shared.agencies.values():
                    ag.vessels = []

                cm = self.chunk_manager
                for chunk in cm.loaded_chunks.values():
                    for obj in chunk.objects:
                        if isinstance(obj, Vessel):
                            ag = self.shared.agencies.get(int(getattr(obj, "agency_id", 0)))
                            if ag is not None:
                                ag.vessels.append(obj)
                            # reattach runtime refs
                            obj.shared = self.shared
                            obj.home_chunk = chunk
                            # (optional) ensure the CM index knows about this object_id → (galaxy, system)
                            cm.register_object(obj.object_id, chunk.galaxy, chunk.system)


    # ===== BIG BANG FUNCTIONS ====

    def spawn_asteroid_belt(self, count: int = 400):
        """
        Create 'count' asteroid-belt asteroids and return them as a list.
        Assumes Sun is at (0, 0) and uses gameobjects.AsteroidBeltAsteroid.
        """
        asteroids = []
        for _ in range(int(count)):
            try:
                ast = gameobjects.AsteroidBeltAsteroid()
                asteroids.append(ast)
            except Exception as e:
                print(f"⚠️ Failed to spawn an asteroid: {e}")
        print(f"🪨 Spawned {len(asteroids)} asteroid-belt asteroids")
        return asteroids

    def create_universe_galaxymap(self):
        chunk_path = (self.universe_path / "intergalacticMap.sa2map").resolve()
        if chunk_path.exists() and chunk_path.stat().st_size > 0:
            print(f"⚠️ Not overwriting existing Galaxy Map at {chunk_path}")
            return
        with open(chunk_path, "w") as file:
            file.write("0")
        print(f"✅ Created Universe Galaxy Map at {chunk_path}")


    def create_home_chunk(self):
        import os
        chunk_path = (self.universe_path / "galaxies" / "1" / "systems" / "system_1.chunk").resolve()
        print("🔧 Building Home Chunk")

        # Idempotent: never overwrite an existing chunk
        if chunk_path.exists() and chunk_path.stat().st_size > 0:
            print(f"⚠️ Not overwriting existing Home Chunk at {chunk_path}")
            print("✅ Created Home Chunk (skipped, already present)")
            return

        self.sun = gameobjects.Sun()
        print("📍 Added The Sun")
        self.earth = gameobjects.Earth()
        print("📍 Added Earth")
        self.chunk_manager.ensure_terrain_chunk(
            1,
            1,
            self.earth.object_id,
            planet_name=self.earth.name,
            terrain_data=self._planet_terrain_defaults(self.earth),
        )
        self.luna = gameobjects.Luna(self.earth)
        print("📍 Added The Moon")
        self.mercury = gameobjects.Mercury()
        self.venus = gameobjects.Venus()
        self.mars = gameobjects.Mars()
        self.phobos = gameobjects.Phobos(self.mars)
        self.deimos = gameobjects.Deimos(self.mars)
        self.jupiter = gameobjects.Jupiter()
        self.saturn = gameobjects.Saturn()
        self.uranus = gameobjects.Uranus()
        self.neptune = gameobjects.Neptune()
        print("📍 Added Other planets")

        belt_asteroids = self.spawn_asteroid_belt(count=0)

        def _attach(body, primary, enable=True):
            body.orbits = primary
            body.orbit_correction_enabled = enable
            body.init_orbit_from_state()  # sets target_a_km and orbit_direction from current state

        for p in (self.mercury, self.venus, self.earth, self.mars,
                  self.jupiter, self.saturn, self.uranus, self.neptune):
            _attach(p, self.sun)


        objects = [
            self.sun, self.earth, self.luna, self.mercury, self.venus,
            self.mars, self.jupiter, self.saturn, self.uranus, self.neptune,
            self.phobos, self.deimos,
            *belt_asteroids
        ]

        for obj in objects:
            if hasattr(obj, "discovered_by"):
                obj.discovered_by = "major tom"

        # Atomic write to avoid partial files
        tmp = chunk_path.with_suffix(".chunk.tmp")
        with open(tmp, "wb") as file:
            pickle.dump(
                objects,
                file
            )
            file.flush()
            os.fsync(file.fileno())
        os.replace(tmp, chunk_path)

        print(f"✅ Created Home Chunk at {chunk_path}")
        GameObject.save_id_seq(self.universe_path)

    def create_milkyway_starmap(self):
        chunk_path = (self.universe_path / "galaxies" / "1" / "interstellarMap.sa2map").resolve()
        if chunk_path.exists() and chunk_path.stat().st_size > 0:
            print(f"⚠️ Not overwriting existing Milky Way Starmap at {chunk_path}")
            return
        with open(chunk_path, "w") as file:
            file.write("0")
        print(f"✅ Created Milky Way Starmap at {chunk_path}")

    def _planet_terrain_defaults(self, planet):
        base = getattr(planet, "terrain_defaults", None)
        if not isinstance(base, dict):
            return None
        terrain = dict(base)
        seed = int(getattr(self.shared, "seed", 0))
        terrain["seed"] = seed
        return terrain
