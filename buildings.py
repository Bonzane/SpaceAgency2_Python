from enum import Enum, IntEnum
import random
from vessels import Vessel

class BuildingType(IntEnum):
    UNDEFINED = 0
    EARTH_HQ = 1
    EARTH_LAUNCHPAD = 2
    NETWORK_TOWER = 3
    STORAGE_FACIITY = 4
    MINING_RIG = 5
    RECEPTION_DISH = 6
    PARTICLE_COLLIDER = 7
    INTEFEROMETER = 8
    CHEMICAL_LAB = 9
    MOON_HQ = 10
    DATA_CENTER = 11
    REFUELING_STATION = 12
    MARS_HQ = 13


class Building:
    def __init__(self, type, shared, position_angle, base, agency):
        self.type = type
        self.shared = shared
        self.position_angle = position_angle
        self.construction_progress = 0
        self.constructed = False
        self.level = 1
        #GET DEFAULT DATA ABOUT THIS TYPE OF BUILDING
        self.default_data = self.shared.buildings_by_id.get(int(type), {})
        self.attributes = self.default_data.get("attributes", {})
        self.unlocks = self.attributes.get("buildinglevel_unlocks", {})
        self.planet_id = base
        manager = shared.chunk_manager
        if not manager:
            raise ValueError("Chunk manager is not initialized.")
        self.chunk = manager.get_chunk_from_object_id(base)
        if not self.chunk:
            raise ValueError(f"Chunk not found for base ID {base}.")
        self.planet_instance = self.chunk.get_object_by_id(base)
        self.agency = agency



        self.construction_time = self.default_data.get("build_time", 0)
        pass

    def _refuel_vessel(self, v, amount: float) -> float:
            """
            Try to add `amount` units of propellant/fuel to vessel `v`.
            Returns how much was actually added (0 if nothing to refuel).
            Supports multiple common field names to avoid tight coupling.
            """
            # (field, capacity_field) candidates
            candidates = [
                ("liquid_fuel_kg", "liquid_fuel_capacity_kg")
            ]
            for f, cap in candidates:
                if hasattr(v, f):
                    cur = float(getattr(v, f) or 0.0)
                    capv = float(getattr(v, cap, 0.0) or 0.0)
                    if capv <= 0.0 or cur >= capv:
                        return 0.0
                    new_val = min(capv, cur + float(amount))
                    setattr(v, f, new_val)
                    return new_val - cur
            return 0.0


    def update(self):
        if not self.constructed:
            self.construction_progress += 1
            if self.construction_progress >= self.construction_time:
                self.constructed = True
                self.construction_progress = 0

        if self.constructed:
            self.do_building_effects()


    def do_building_effects(self):
        match(self.type):
            case BuildingType.EARTH_HQ:
                self.agency.ensure_min_astronauts_on_planet(planet_id=2, min_count=3)
            case BuildingType.MINING_RIG:
                mining_odds = random.randrange(0, 1000)
                if mining_odds < (50 * self.level):
                    resource_map = getattr(self.planet_instance, "resource_map", {}) or {}
                    if resource_map:
                        resources = list(resource_map.keys())
                        weights = list(resource_map.values())
                        mined_resource = random.choices(resources, weights=weights, k=1)[0]
                        inv = self.agency.base_inventories.get(self.planet_id, {})
                        total = sum(inv.values())
                        cap = self.agency.base_inventory_capacities.get(self.planet_id, 0)
                        if total < cap:
                            inv[mined_resource] = inv.get(mined_resource, 0) + 1
                            self.agency.base_inventories[self.planet_id] = inv
            case BuildingType.REFUELING_STATION:
                if not self.constructed:
                    return

                # Units per second from your JSON; scale by level if you want:
                base_rate = 10.0
                if base_rate <= 0.0:
                    return

                add_amt = base_rate * (float(self.level))
                if add_amt <= 0.0:
                    return

                planet_id = int(self.planet_id)
                # print("Refueling station on planet: ", planet_id)
                # Refuel landed vessels that are on THIS planet (vessel-owned state only).
                for v in list(self.agency.vessels):
                    try:
                        if not isinstance(v, Vessel):
                            continue
                        if not getattr(v, "landed", False):
                            continue
                        # print("Refueling vessel")
                        hp = getattr(v, "last_landed_body_id",  getattr(v, "home_planet", None))
                        if hp is None or hp != int(planet_id):
                            # print("Wrong Planet: Vessel is on ", hp)
                            continue  # not on this planet
                        # print("Same Planet")
                        # Current-stage tank interface (keeps gameplay consistent):
                        cur = float(v._current_stage_fuel())
                        cap = float(v._current_stage_capacity())
                        if cap <= 0.0 or cur >= cap:
                            continue

                        put = min(add_amt, cap - cur)
                        if put <= 0.0:
                            continue
                        print(f"Refueling {put} units")
                        v._set_current_stage_fuel(cur + put)
                        v.liquid_fuel_kg = v._current_stage_fuel()  # mirror legacy flat field
                        v.mass = v.calculate_mass(self.shared.component_data)
                    except Exception:
                        continue


    def get_income_from_building(self):
        income = 0
        if self.constructed:
            income = self.attributes.get("base_income", 0)
            for level in self.unlocks:
                if self.level >= int(level):
                    income += self.unlocks[level].get("add_base_income", 0)
        return income

    #RETURNS A LIST OF BUILDINGS THAT THIS BUILDING HAS UNLOCKED AT ITS CURRENT LEVEL
    def get_building_unlocks(self):
        unlocked_buildings = []
        if self.constructed:
            # Refresh unlock data from shared definitions to pick up newly added unlocks
            unlocks = self.shared.buildings_by_id.get(int(self.type), {}).get("attributes", {}).get("buildinglevel_unlocks", {}) or self.unlocks
            for level, effects in unlocks.items():
                try:
                    lvl_req = int(level)
                except Exception:
                    continue
                if self.level >= lvl_req and isinstance(effects, dict):
                    unlocked_buildings.extend(effects.get("unlock_buildings", []))
        return unlocked_buildings
            
    # SAME BUT FOR COMPONENTS
    def get_component_unlocks(self):
        unlocked_components = []
        if self.constructed:
            # Refresh unlock data from shared definitions to pick up newly added unlocks
            unlocks = self.shared.buildings_by_id.get(int(self.type), {}).get("attributes", {}).get("buildinglevel_unlocks", {}) or self.unlocks
            for level, effects in unlocks.items():
                try:
                    lvl_req = int(level)
                except Exception:
                    continue
                if self.level >= lvl_req and isinstance(effects, dict):
                    unlocked_components.extend(effects.get("unlock_components", []))
        return unlocked_components
            
        

    def to_json(self):            
        return {
            "type": int(self.type),
            "constructed": self.constructed,
            "level": self.level,
            "construction_progress": self.construction_progress, 
            "position_angle" : self.position_angle
        }
