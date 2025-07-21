from enum import Enum, IntEnum

class BuildingType(IntEnum):
    UNDEFINED = 0
    EARTH_HQ = 1
    EARTH_LAUNCHPAD = 2
    NETWORK_TOWER = 3


class Building:
    def __init__(self, type, shared, position_angle):
        self.type = type
        self.shared = shared
        self.position_angle = position_angle
        self.construction_progress = 0
        self.constructed = False
        self.level = 1
        #GET DEFAULT DATA ABOUT THIS TYPE OF BUILDING
        self.default_data = self.shared.buildings_by_id.get(type, {})
        self.attributes = self.default_data.get("attributes", {})
        self.unlocks = self.attributes.get("buildinglevel_unlocks", {})


        self.construction_time = self.default_data.get("build_time", 0)
        pass

    def update(self):
        if not self.constructed:
            self.construction_progress += 1
            if self.construction_progress >= self.construction_time:
                self.constructed = True
                self.construction_progress = 0

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
            "type": self.type,
            "constructed": self.constructed,
            "level": self.level,
            "construction_progress": self.construction_progress, 
            "position_angle" : self.position_angle
        }