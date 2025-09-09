from enum import Enum, IntEnum
import random

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
        if(self.constructed):
            for level in self.unlocks:
                if self.level >= int(level):
                    unlocked_buildings.extend(self.unlocks[level].get("unlock_buildings", []))
        return unlocked_buildings
            
    # SAME BUT FOR COMPONENTS
    def get_component_unlocks(self):
        unlocked_components = []
        if(self.constructed):
            for level in self.unlocks:
                if self.level >= int(level):
                    unlocked_components.extend(self.unlocks[level].get("unlock_components", []))
        return unlocked_components
            
        

    def to_json(self):            
        return {
            "type": int(self.type),
            "constructed": self.constructed,
            "level": self.level,
            "construction_progress": self.construction_progress, 
            "position_angle" : self.position_angle
        }