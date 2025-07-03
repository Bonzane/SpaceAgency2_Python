from dataclasses import dataclass, field
from typing import Dict, List, Any
import json
import struct
from packet_types import PacketType
from buildings import Building, BuildingType
import copy

@dataclass
class Agency:
    name: str
    shared: Any = field(repr=False) 
    id64: int = 0
    is_public: bool = True
    members: List[int] = field(default_factory=list)
    bases_to_buildings: Dict[int, List[Any]] = field(default_factory=dict)
    total_money: int = 0
    primarycolor: int = 0
    secondarycolor: int = 0
    unlocked_buildings: set = field(default_factory=set)
    income_per_second: int = 0

    def __post_init__(self):
        default_building = Building(BuildingType.EARTH_HQ, self.shared)
        self.bases_to_buildings[2] = [default_building]
        self.attributes = copy.deepcopy(self.shared.agency_default_attributes)



    # === Membership Methods ===
    def add_player(self, steam_id: int) -> None:
        if steam_id not in self.members:
            self.members.append(steam_id)

    def remove_player(self, steam_id: int) -> None:
        if steam_id in self.members:
            self.members.remove(steam_id)

    def list_players(self) -> None:
        for id64 in self.members:
            print(f"Player: {id64}")

    def get_member_count(self) -> int:
        return len(self.members)

    def get_all_players(self) -> List[Any]:
        return [
            self.shared.players[id64]
            for id64 in self.members
            if id64 in self.shared.players
        ]

    # === Identity / State Methods ===
    def set_name(self, name: str) -> None:
        self.name = name

    def get_name(self) -> str:
        return self.name

    def manually_set_id(self, new_id: int) -> None:
        self.id64 = new_id

    def get_id64(self) -> int:
        return self.id64

    def set_public(self, is_public: bool) -> None:
        self.is_public = is_public

    def get_public(self) -> bool:
        return self.is_public


    # === Money / Data ===
    # This one is just for retreiving the total money. This does NOT generate income. 
    # For that use generate_agency_income()
    def get_money(self) -> int:
        self.total_money = sum(
            self.shared.players[id64].money
            for id64 in self.members
            if id64 in self.shared.players
        )
        return self.total_money
    

    def generate_agency_income(self) -> None:
        #This method generates the total income of the agency based on all buildings and vessels, then divides it by all members.
        income_from_buildings = 0
        for building in self.get_all_buildings():
            income_from_buildings += building.get_income_from_building()

        total_income = income_from_buildings
        total_income = total_income * self.shared.server_global_cash_multiplier

        self.income_per_second = total_income
        #Distribute the income to all members
        if self.get_member_count() > 0:
            income_per_member = total_income // self.get_member_count()
            for id64 in self.members:
                if id64 in self.shared.players:
                    self.shared.players[id64].money += income_per_member


    def set_base_buildings(self, base_id: int, buildings: List[Any]) -> None:
        self.bases_to_buildings[base_id] = buildings

    def add_building_to_base(self, base_id: int, building: Any) -> None:
        self.bases_to_buildings.setdefault(base_id, []).append(building)

    #Gets a list of all buildings currently built by the agency
    def get_all_buildings(self) -> List[Building]:
        all_buildings = []
        for buildings in self.bases_to_buildings.values():
            all_buildings.extend(buildings)
        return all_buildings

    #Gets all buildings that are unlocked by the agency, built or not
    def get_all_unlocked_buildings(self) -> List[Any]:
        self.unlocked_buildings = set()
        for building_instance in self.get_all_buildings():
            self.unlocked_buildings.update(
                building_instance.get_building_unlocks()
            )

    # === Serialization ===

    def generate_gamestate_packet(self) -> bytes:
        bases_serialized = {
            base_id: [building.to_json() for building in buildings]
            for base_id, buildings in self.bases_to_buildings.items()
        }
            
        data = {
            "id": self.id64,
            "mbrs": self.members,
            "mny": self.get_money(),
            "bases": bases_serialized, 
            "mny_prsec" : self.income_per_second
        }
        json_str = json.dumps(data)
        json_bytes = json_str.encode('utf-8')
        header = struct.pack("<H", PacketType.AGENCY_GAMESTATE)
        return header + json_bytes
