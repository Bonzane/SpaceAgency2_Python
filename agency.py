from dataclasses import dataclass, field
import math
from typing import Dict, List, Any
import json
import struct
from packet_types import PacketType
from buildings import Building, BuildingType
import copy
from vessels import Vessel

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
    unlocked_components: set = field(default_factory=set)
    vessels: List[Vessel] = field(default_factory=list)
    income_per_second: int = 0
    base_inventories: Dict[int, Dict[int, int]] = field(default_factory=dict)
    base_inventory_capacities: Dict[int, int] = field(default_factory=dict)

    def __post_init__(self):
        default_building = Building(BuildingType.EARTH_HQ, self.shared, 7, 2, self)
        self.bases_to_buildings[2] = [default_building]
        self.bases_to_buildings[4] = []
        self.bases_to_buildings[5] = []
        self.bases_to_buildings[6] = []
        self.bases_to_buildings[7] = []
        self.bases_to_buildings[8] = []
        self.bases_to_buildings[9] = []
        self.bases_to_buildings[10] = []
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
    
    def add_vessel(self, vessel: Vessel) -> None:
        self.vessels.append(vessel)

    def get_all_vessels(self) -> List[Vessel]:
        return self.vessels
    
    # === Attributes ===
    def update_attributes(self) -> None:
        # 1) start from defaults
        attrs = dict(self.shared.agency_default_attributes)
        for base in self.bases_to_buildings:
            self.base_inventory_capacities[base] = 0

        # 2) fold in effects from each constructed building, up to its level
        for b in self.get_all_buildings():
            if not getattr(b, "constructed", False):
                continue
            unlocks = getattr(b, "unlocks", {}) or {}

            for lvl_str, effects in unlocks.items():
                try:
                    lvl_req = int(lvl_str)
                except ValueError:
                    continue

                if b.level < lvl_req:
                    continue
                if not isinstance(effects, dict):
                    continue

                if "add_satellite_income" in effects:
                    attrs["satellite_bonus_income"] = attrs.get("satellite_bonus_income", 0) + effects["add_satellite_income"]

                if "satellite_max_upgrade_tier" in effects:
                    if effects["satellite_max_upgrade_tier"] > attrs.get("satellite_max_upgrade_tier", 0):
                        attrs["satellite_max_upgrade_tier"] = effects["satellite_max_upgrade_tier"]

                if "add_base_storage" in effects:
                    planet = b.planet_id
                    self.base_inventory_capacities[planet] += effects["add_base_storage"]


        # 3) commit
        self.attributes = attrs



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

    #Distributes some amount of money to all agency members equally
    def distribute_money(self, amount) -> int:
        #Distribute the income to all members
        if self.get_member_count() > 0:
            income_per_member = math.ceil(amount / self.get_member_count())
            for id64 in self.members:
                if id64 in self.shared.players:
                    self.shared.players[id64].money += income_per_member

    

    def generate_agency_income(self) -> None:
        #This method generates the total income of the agency based on all buildings and vessels, then divides it by all members.
        income_from_buildings = 0
        for building in self.get_all_buildings():
            income_from_buildings += building.get_income_from_building()

        total_income = income_from_buildings
        total_income = int(total_income * self.shared.server_global_cash_multiplier)

        self.income_per_second = total_income
        #Distribute the income to all members
        if self.get_member_count() > 0:
            income_per_member = int(total_income // self.get_member_count())
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
        return list(self.unlocked_buildings)

    def get_all_unlocked_components(self) -> List[Any]:
        self.unlocked_components = set()
        for building_instance in self.get_all_buildings():
            self.unlocked_components.update(
                building_instance.get_component_unlocks()
            )
        return list(self.unlocked_components)


    def _type_to_int(self, t):
        """Handle enums or raw ints for building_type comparisons."""
        try:
            return int(getattr(t, "value", t))
        except Exception:
            return t

    def _find_building(self, planet_id: int, building_type: int):
        """Find the first matching building of a given type on a planet."""
        want = self._type_to_int(building_type)
        for b in self.bases_to_buildings.get(planet_id, []):
            bt = self._type_to_int(getattr(b, "building_type", getattr(b, "type", None)))
            if bt == want:
                return b
        return None

    def _calc_upgrade_cost(self, building_type: int, from_level: int, to_level: int) -> int:
        """
        Total cost to go from 'from_level' (current) up to and including 'to_level'.
        Supports either:
        - per-level table:  def["upgrade_costs"] (list or dict keyed by level as str)
        - or a growth formula off base 'cost' and optional 'upgrade_growth'
        """
        bdef = self.shared.buildings_by_id.get(building_type, {})  # from your shared game JSON
        base_cost = int(bdef.get("cost", 0))
        growth = float(bdef.get("upgrade_growth", 1.5))  # tweak default as you like

        total = 0
        costs_tbl = bdef.get("upgrade_costs")
        for lvl in range(from_level + 1, to_level + 1):
            step = None
            if isinstance(costs_tbl, dict):
                # levels stored as strings: {"2": 1500, "3": 4000, ...}
                step = costs_tbl.get(str(lvl))
            elif isinstance(costs_tbl, list):
                idx = lvl - 1
                if 0 <= idx < len(costs_tbl):
                    step = costs_tbl[idx]

            if step is None:
                # fallback formula (base * growth^(lvl-1))
                step = math.ceil(base_cost * (growth ** (lvl - 1)))

            total += int(step)

        return int(total)

    def try_upgrade_building(self, player, planet_id: int, building_type: int, to_level: int):
        # 1) find the building
        b = None
        for inst in self.bases_to_buildings.get(planet_id, []):
            if int(getattr(inst, "type", 0)) == int(building_type):
                b = inst
                break
        if not b:
            return False, "not_found", 0, 0

        if not b.constructed:
            return False, "not_constructed", 0, int(getattr(b, "level", 1))

        current = int(getattr(b, "level", 1))

        # 2) read costs table and infer max level
        bdef = self.shared.buildings_by_id.get(building_type, {}) or {}
        tbl = bdef.get("upgrade_costs") or {}
        # supports dict {"2":50000,...} or list [?, 50000, 100000, ...] (index = level-1)
        if isinstance(tbl, dict):
            max_level = max((int(k) for k in tbl.keys()), default=current)
            step_cost = lambda lvl: int(tbl.get(str(lvl), 0))
        elif isinstance(tbl, list):
            max_level = len(tbl) + 1  # list entries start at level 2 (idx = level-1)
            step_cost = lambda lvl: int(tbl[lvl - 1]) if 0 <= (lvl - 1) < len(tbl) else 0
        else:
            return False, "no_price_table", 0, current

        # 3) normalize target level
        target = to_level if to_level > current else current + 1
        if target > max_level:
            return False, "at_max_level", 0, current

        # 4) sum per-step costs (must exist; if any step is missing/0, fail)
        cost = 0
        for lvl in range(current + 1, target + 1):
            c = step_cost(lvl)
            if c <= 0:
                return False, "no_price_for_level", 0, current
            cost += c

        # 5) pay + apply
        if player.money < cost:
            return False, "insufficient_funds", cost, current

        player.money -= cost
        b.level = target
        if hasattr(b, "on_upgraded") and callable(b.on_upgraded):
            b.on_upgraded(current, target)
        self.update_attributes()

        return True, "ok", cost, target



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
            "mny_prsec": self.income_per_second,
            "buildable": self.get_all_unlocked_buildings(),
            "components": self.get_all_unlocked_components(),
            "vsls": [v.get_id() for v in self.get_all_vessels()],
            "base_capacities": self.base_inventory_capacities,
            "base_inventories": self.base_inventories,
        }

        payload = json.dumps(data, separators=(',', ':')).encode('utf-8')
        # [opcode:u16][length:u32][payload]
        return struct.pack('<HI', PacketType.AGENCY_GAMESTATE, len(payload)) + payload
