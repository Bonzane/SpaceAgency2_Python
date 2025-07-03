from enum import Enum, IntEnum

class BuildingType(IntEnum):
    UNDEFINED = 0
    EARTH_HQ = 1
    EARTH_LAUNCHPAD = 2
    NETWORK_TOWER = 3


class Building:
    def __init__(self, type, shared):
        self.type = type
        self.shared = shared
        self.construction_progress = 0
        self.constructed = False
        self.level = 1
        #GET DEFAULT DATA ABOUT THIS TYPE OF BUILDING
        self.default_data = self.shared.game_buildings_details.get(type, {})
        self.construction_time = self.default_data.get("construction_time", 0)
        pass

    def update(self):
        if not self.constructed:
            self.construction_progress += 1
            if self.construction_progress >= self.construction_time:
                self.constructed = True
                self.construction_progress = 0

    def to_json(self):
        return {
            "type": self.type,
            "constructed": self.constructed,
            "level": self.level,
            "construction_progress": self.construction_progress
        }