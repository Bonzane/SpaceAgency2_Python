from dataclasses import dataclass, field
import math
from typing import Dict, List, Any, Set, Optional
import json
import struct
from upgrade_tree import T_UP
from vessel_components import Components
from packet_types import PacketType
from buildings import Building, BuildingType
import copy
from vessels import Vessel
from collections import defaultdict
from astronaut import Astronaut

EARTH_ID = 2

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
    base_multipliers: Dict[int, float] = field(default_factory=dict)
    astronauts: Dict[int, Astronaut] = field(default_factory=dict)
    planet_to_astronauts: Dict[int, Set[int]] = field(default_factory=lambda: defaultdict(set))
    _astro_seq: int = 0
    def __post_init__(self):
        default_building = Building(BuildingType.EARTH_HQ, self.shared, 7, 2, self)
        self.bases_to_buildings[2] = [default_building]
        self.bases_to_buildings[1] = []
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

    def sell_resource(self, player, from_planet: int, resource_type: int, count: int) -> bool:
        """
        Sell `count` units of `resource_type` from the agency's base inventory at `from_planet`.
        - Decrements base inventory if sufficient quantity exists.
        - Credits player's money by count * transfer_rate (from shared.game_desc resources).
        - Returns True on success, False otherwise.
        """
        # Basic validation
        try:
            rt = int(resource_type)
            cnt = int(count)
            pid = int(from_planet)
        except Exception:
            return False
        if cnt <= 0:
            return False
        if player is None:
            return False
        # (Optional) ensure the player belongs to this agency
        if getattr(player, "steamID", None) not in self.members:
            # Not strictly necessary since caller passes agency, but it's safer.
            return False

        # Resolve rate (price per unit)
        rate = int(getattr(self.shared, "resource_transfer_rates", {}).get(rt, 0))
        if rate <= 0:
            # Not sellable or worthless
            return False

        # Ensure the planet inventory exists and has enough
        inv = self.base_inventories.setdefault(pid, {})  # {resource_type:int -> qty:int}
        have = int(inv.get(rt, 0))
        if have < cnt:
            return False

        # Perform the sale
        inv[rt] = have - cnt
        if inv[rt] <= 0:
            # keep things tidy
            inv.pop(rt, None)

        # Credit player (optionally scale by global cash multiplier)
        total_value = rate * cnt
        # If you want to respect the global multiplier (used for incomes), apply it here:
        total_value = int(total_value * float(getattr(self.shared, "server_global_cash_multiplier", 1.0)))

        player.money += total_value

        # (Optional) telemetry / logging
        try:
            rname = self.shared.game_desc["resources"][rt][0]
        except Exception:
            rname = f"Resource#{rt}"
        print(f"✅ Agency {self.id64} sold {cnt}x {rname} (rt={rt}) from planet {pid} "
              f"for {total_value}. Player {getattr(player, 'steamID', '?')} money={player.money}")
        return True


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
    
    def remove_vessel(self, vessel_or_id) -> None:
        vid = getattr(vessel_or_id, "object_id", vessel_or_id)
        self.vessels = [v for v in self.vessels if getattr(v, "object_id", None) != vid]
    
    # === Attributes ===

    def recompute_networking_multipliers(self) -> None:
        """Rebuild per-planet multipliers from deployed comm sats with NETWORKING."""
        self.base_multipliers.clear()

        for sat in list(self.vessels):
            try:
                if int(getattr(sat, "payload", 0)) != int(Components.COMMUNICATIONS_SATELLITE):
                    continue
                if int(getattr(sat, "stage", 1)) != 0:
                    continue  # not deployed

                unlocked = sat.current_payload_unlocked()
                if int(T_UP.NETWORKING2) in unlocked:
                    pct = 0.02
                elif int(T_UP.NETWORKING1) in unlocked:
                    pct = 0.01
                else:
                    continue

                planets = list(sat._iter_planets_in_same_system())
                if not planets:
                    continue

                sx, sy = sat.position
                nearest = min(
                    planets,
                    key=lambda p: math.hypot(p.position[0]-sx, p.position[1]-sy)
                )

                r = float(getattr(nearest, "radius_km", 0.0))
                if r <= 0.0:
                    continue
                dist = math.hypot(nearest.position[0]-sx, nearest.position[1]-sy)
                if dist > r * 4.0:  # within 2x diameter
                    continue

                pid = int(getattr(nearest, "object_id", 0))
                if pid == 0:
                    continue

                # additive stacking: 1.0 base + 0.01/0.02 per qualifying sat
                self.base_multipliers[pid] = self.base_multipliers.get(pid, 1.0) + pct

                # Optional safety cap to avoid runaway stacking:
                # self.base_multipliers[pid] = min(self.base_multipliers[pid], 2.0)

            except Exception:
                continue

    def planet_multiplier_for(self, planet_id: int) -> float:
        return float(self.base_multipliers.get(int(planet_id or 0), 1.0))
    
    def update_attributes(self) -> None:
        # 1) start from defaults
        attrs = dict(self.shared.agency_default_attributes)

        # Rebuild capacities from scratch each tick (based on built buildings)
        self.base_inventory_capacities = {}

        # Seed capacity keys for every planet we currently track a base on
        for base_planet_id in self.bases_to_buildings.keys():
            self.base_inventory_capacities[base_planet_id] = 0
            # keep the inventories dict consistent too
            self.base_inventories.setdefault(base_planet_id, {})

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
                if b.level < lvl_req or not isinstance(effects, dict):
                    continue

                # --- attribute bonuses ---
                add_sat = int(effects.get("add_satellite_income", 0))
                if add_sat:
                    attrs["satellite_bonus_income"] = attrs.get("satellite_bonus_income", 0) + add_sat

                max_tier = effects.get("satellite_max_upgrade_tier")
                if isinstance(max_tier, int) and max_tier > attrs.get("satellite_max_upgrade_tier", 0):
                    attrs["satellite_max_upgrade_tier"] = max_tier
                max_tier = effects.get("probe_max_upgrade_tier")
                if isinstance(max_tier, int) and max_tier > attrs.get("probe_max_upgrade_tier", 0):
                    attrs["probe_max_upgrade_tier"] = max_tier
                # --- per-planet storage capacity ---
                add_storage = int(effects.get("add_base_storage", 0))
                if add_storage:
                    planet = int(getattr(b, "planet_id", 0))
                    # make sure both dicts have the planet key before incrementing
                    self.base_inventories.setdefault(planet, {})
                    self.base_inventory_capacities[planet] = self.base_inventory_capacities.get(planet, 0) + add_storage

        # 3) commit
        self.attributes = attrs

        #4) Also do the planet networking multiplier
        self.recompute_networking_multipliers()

    def ensure_min_astronauts_on_planet(self, planet_id: int, min_count: int = 3) -> int:
        """
        Guarantee there are at least `min_count` astronauts living on `planet_id`
        for this agency. Returns how many were spawned (0 if already satisfied).
        """
        planet_id = int(planet_id)
        have = len(self.planet_to_astronauts.get(planet_id, set()))
        spawned = 0
        while have < min_count:
            name = f"Astronaut {self._astro_seq}"
            self._astro_seq += 1
            self.create_astronaut(name=name, planet_id=planet_id)
            have += 1
            spawned += 1
        if spawned:
            print(f"👩‍🚀 Agency {self.id64}: spawned {spawned} astronaut(s) on planet {planet_id}")
        return spawned

    def create_astronaut(self, name: str, planet_id: Optional[int] = None, suit_id: int = 0,
                        appearance_id: Optional[int] = None) -> Astronaut:
        a = Astronaut(
            name=name,
            suit_id=int(suit_id),
            appearance_id=(appearance_id if appearance_id is not None else None),
            agency_id=int(getattr(self, "id64", 0)),
            planet_id=planet_id,
            vessel_id=None,
        )
        self.astronauts[a.id32] = a
        if planet_id is not None:
            self.planet_to_astronauts[int(planet_id)].add(a.id32)
        return a

    def add_astronaut(self, astro: Astronaut) -> None:
        """Register an externally-constructed astronaut with this agency."""
        astro.agency_id = int(getattr(self, "id64", 0))
        self.astronauts[astro.id32] = astro
        if astro.planet_id is not None:
            self.planet_to_astronauts[int(astro.planet_id)].add(astro.id32)

    def move_astronaut_to_planet(self, astro_id: int, planet_id: Optional[int]) -> bool:
        a = self.astronauts.get(int(astro_id))
        if not a:
            return False
        # Remove from prior planet bucket
        if a.planet_id is not None:
            self.planet_to_astronauts[int(a.planet_id)].discard(a.id32)
        # Clear vessel if any (moving to planet)
        a.vessel_id = None
        a.planet_id = int(planet_id) if planet_id is not None else None
        if a.planet_id is not None:
            self.planet_to_astronauts[int(a.planet_id)].add(a.id32)
        return True

    def remove_astronaut(self, astro_id: int) -> bool:
        a = self.astronauts.pop(int(astro_id), None)
        if not a:
            return False
        if a.planet_id is not None:
            self.planet_to_astronauts[int(a.planet_id)].discard(a.id32)
        return True

    def get_astronauts_on_planet(self, planet_id: int) -> List[Astronaut]:
        return [self.astronauts[aid] for aid in self.planet_to_astronauts.get(int(planet_id), set())
                if aid in self.astronauts]

    # --- Seat & placement utilities ---

    def _ensure_seat_list(self, vessel) -> list[int]:
        """Make sure vessel has a list to track seated astronauts."""
        if not hasattr(vessel, "seated_astronauts") or not isinstance(vessel.seated_astronauts, list):
            vessel.seated_astronauts = []
        return vessel.seated_astronauts

    def seats_total_for(self, vessel) -> int:
        return int(getattr(vessel, "seats_capacity", 0))

    def seats_free_for(self, vessel) -> int:
        seated = self._ensure_seat_list(vessel)
        return max(0, self.seats_total_for(vessel) - len(seated))

    def get_vessel_planet_id(self, vessel) -> Optional[int]:
        """Determine the planet the vessel is 'on' when landed."""
        src = getattr(vessel, "strongest_gravity_source", None) or getattr(vessel, "home_planet", None)
        if not src:
            return None
        pid = int(getattr(src, "object_id", 0))
        return pid if pid > 0 else None

    def get_astronauts_in_vessel(self, vessel) -> list[Astronaut]:
        ids = self._ensure_seat_list(vessel)
        return [self.astronauts[aid] for aid in ids if aid in self.astronauts]

    def set_astronaut_suit(self, astro_id: int, suit_id: int) -> tuple[bool, str]:
        a = self.astronauts.get(int(astro_id))
        if not a:
            return False, "astronaut_not_found"
        s = int(suit_id)
        if s < 0:
            s = 0
        a.suit_id = s
        return True, "ok"

    # --- Core moves used by your UDP handlers ---
    def _vessel_landed_planet_id(self, vessel) -> Optional[int]:
        if not getattr(vessel, "landed", 0):
            return None
        pid = getattr(getattr(vessel, "strongest_gravity_source", None), "object_id", None)
        if pid is None and getattr(vessel, "home_planet", None) is not None:
            pid = getattr(vessel.home_planet, "object_id", None)
        return int(pid) if pid is not None else None

    def move_astronaut_to_vessel(self, astro_id: int, vessel) -> tuple[bool, str]:
        astro_id = int(astro_id)
        a = self.astronauts.get(astro_id)
        if not a:
            return False, "astronaut_not_found"

        if not getattr(vessel, "landed", 0):
            return False, "vessel_not_landed"

        pid = self._vessel_landed_planet_id(vessel)
        if pid is None:
            return False, "no_landing_planet"

        # must be on that planet and not already on a vessel
        if a.planet_id != pid or a.vessel_id is not None:
            return False, "astronaut_not_on_this_planet"

        seats = int(getattr(vessel, "seats_capacity", 0))
        if seats <= 0:
            return False, "no_seats"

        lst = getattr(vessel, "astronauts_onboard", None)
        if lst is None:
            vessel.astronauts_onboard = lst = []

        if astro_id in lst:
            return False, "already_onboard"
        if len(lst) >= seats:
            return False, "seats_full"

        # move: planet -> vessel
        self.planet_to_astronauts[pid].discard(astro_id)
        a.planet_id = None
        a.vessel_id = int(getattr(vessel, "object_id", 0))
        lst.append(astro_id)
        return True, "ok"

    def move_astronaut_off_vessel(self, astro_id: int, vessel) -> tuple[bool, str]:
        astro_id = int(astro_id)
        a = self.astronauts.get(astro_id)
        if not a:
            return False, "astronaut_not_found"

        if not getattr(vessel, "landed", 0):
            return False, "vessel_not_landed"

        lst = getattr(vessel, "astronauts_onboard", None)
        if lst is None:
            vessel.astronauts_onboard = lst = []

        vid = int(getattr(vessel, "object_id", 0))
        if a.vessel_id != vid and astro_id not in lst:
            return False, "not_on_this_vessel"

        pid = self._vessel_landed_planet_id(vessel)
        if pid is None:
            return False, "no_landing_planet"

        # move: vessel -> planet
        try:
            lst.remove(astro_id)
        except ValueError:
            pass
        a.vessel_id = None
        a.planet_id = pid
        self.planet_to_astronauts[pid].add(astro_id)
        return True, "ok"

    # --- Nice-to-haves (safe cleanup) ---

    def disembark_all_to_planet(self, vessel) -> int:
        """If a vessel is landed or being destroyed, move everyone aboard to the planet."""
        pid = self.get_vessel_planet_id(vessel)
        if pid is None:
            # fallback: drop to Earth if unknown
            pid = EARTH_ID
        seated = self._ensure_seat_list(vessel)
        moved = 0
        for aid in list(seated):
            a = self.astronauts.get(aid)
            if not a:
                seated.remove(aid)
                continue
            a.vessel_id = None
            a.planet_id = pid
            self.planet_to_astronauts[pid].add(aid)
            seated.remove(aid)
            moved += 1
        return moved



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

        base_mults_diff = {
            int(pid): round(float(mult), 4)
            for pid, mult in self.base_multipliers.items()
            if abs(float(mult) - 1.0) > 1e-9
        }

         # --- Astronauts: id64 -> astronaut json ---
        astronauts_serialized = {}
        for aid, a in self.astronauts.items():
            if hasattr(a, "to_json"):
                astronauts_serialized[int(aid)] = a.to_json()
            else:
                astronauts_serialized[int(aid)] = {
                    "id": int(getattr(a, "id64", aid)),
                    "name": getattr(a, "name", "Unnamed"),
                    "suit": int(getattr(a, "suit_id", 0)),
                    "appearance": int(getattr(a, "appearance_id", 0)),
                    "planet": (int(a.planet_id) if getattr(a, "planet_id", None) is not None else None),
                    "vessel": (int(a.vessel_id) if getattr(a, "vessel_id", None) is not None else None),
                    "agency": int(getattr(a, "agency_id", getattr(self, "id64", 0))),
                }

        # --- Planet -> [astronaut ids] (sets -> lists) ---
        astros_by_planet = {
            int(pid): [int(aid) for aid in sorted(aids) if aid in self.astronauts]
            for pid, aids in self.planet_to_astronauts.items()
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
            "base_multipliers": base_mults_diff,
            "astronauts": astronauts_serialized,    
            "astros_by_planet": astros_by_planet
        }

        payload = json.dumps(data, separators=(',', ':')).encode('utf-8')
        # [opcode:u16][length:u32][payload]
        return struct.pack('<HI', PacketType.AGENCY_GAMESTATE, len(payload)) + payload

    def to_json(self) -> dict:
        # Minimal snapshot: id, name, public, members (steam IDs only)
        return {
            "id": int(self.id64),
            "name": self.name,
            "public": bool(self.is_public),
            "members": [int(sid) for sid in self.members],  # steam IDs only
        }

