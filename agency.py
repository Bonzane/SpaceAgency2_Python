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
    flag: int = 0
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
    discovered_planets: Set[int] = field(default_factory=set)
    research_points: int = 0
    exploration_points: int = 0
    publicity_points: int = 0
    experience_points: int = 0
    quest_state: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    quest_counters: Dict[str, int] = field(default_factory=dict)
    stat_counters: Dict[str, float] = field(default_factory=dict)
    steam_stat_state: Dict[str, float] = field(default_factory=dict)
    steam_achievement_state: Dict[str, bool] = field(default_factory=dict)
    visited_planets: Set[int] = field(default_factory=set)
    age_days: float = 0.0
    invited: Set[int] = field(default_factory=set)
    def __post_init__(self):
        default_building = Building(BuildingType.EARTH_HQ, self.shared, 7, 2, self)
        self.bases_to_buildings[2] = [default_building]
        self.attributes = copy.deepcopy(self.shared.agency_default_attributes)
        self.discovered_planets.add(EARTH_ID)
        self.discovered_planets.add(0)
        self.discovered_planets.add(3)
        self.visited_planets.add(EARTH_ID)
        if not hasattr(self, "invited") or self.invited is None:
            self.invited = set()

    # ---- Invites ----
    def add_invite(self, steam_id: int) -> None:
        if not hasattr(self, "invited") or self.invited is None:
            self.invited = set()
        try:
            self.invited.add(int(steam_id))
        except Exception:
            pass

    def is_invited(self, steam_id: int) -> bool:
        try:
            sid = int(steam_id)
        except Exception:
            return False
        return bool(getattr(self, "invited", set()) and sid in self.invited)

    def consume_invite(self, steam_id: int) -> None:
        try:
            sid = int(steam_id)
        except Exception:
            return
        if hasattr(self, "invited") and self.invited:
            self.invited.discard(sid)

    def _xp_curve(self) -> Dict[str, float | int]:
        gd = getattr(self.shared, "game_description", {}) or {}
        curve = gd.get("xp_level_curve", {}) if isinstance(gd, dict) else {}
        try:
            base = float(curve.get("base", 100.0))
        except Exception:
            base = 100.0
        try:
            growth = float(curve.get("growth", 1.15))
        except Exception:
            growth = 1.15
        try:
            cap = int(curve.get("cap", 0) or 0)
        except Exception:
            cap = 0
        if base <= 0:
            base = 100.0
        if growth <= 0.0:
            growth = 1.0
        return {"base": base, "growth": growth, "cap": cap}

    def _xp_need_for_next(self, level: int, curve: Dict[str, float | int]) -> int:
        base = float(curve["base"])
        growth = float(curve["growth"])
        lvl = max(1, int(level))
        need = base * (growth ** max(0, lvl - 1))
        return max(1, int(math.floor(need)))

    def _xp_level_progress(self, points: int) -> Dict[str, int]:
        pts = max(0, int(points))
        curve = self._xp_curve()
        cap = int(curve["cap"])
        level = 1
        into = pts
        while True:
            if cap and level >= cap:
                return {"level": level, "into": into, "next": 0}
            need = self._xp_need_for_next(level, curve)
            if into < need:
                return {"level": level, "into": into, "next": need}
            into -= need
            level += 1

    # === Quests ===
    def _get_quest_defs(self) -> List[Dict[str, Any]]:
        gd = getattr(self.shared, "game_description", {}) or {}
        quests = gd.get("quests", [])
        if not isinstance(quests, list):
            return []
        return [q for q in quests if isinstance(q, dict)]

    def _ensure_quest_state(self) -> Dict[str, Dict[str, Any]]:
        if not hasattr(self, "quest_state") or not isinstance(self.quest_state, dict):
            self.quest_state = {}
        return self.quest_state

    def _get_stat_defs(self) -> List[Dict[str, Any]]:
        stats = getattr(self.shared, "steam_stats_watchers", []) or []
        if not isinstance(stats, list):
            return []
        return [s for s in stats if isinstance(s, dict)]

    def _ensure_stat_state(self) -> Dict[str, float]:
        if not hasattr(self, "steam_stat_state") or not isinstance(self.steam_stat_state, dict):
            self.steam_stat_state = {}
        return self.steam_stat_state

    def _get_achievement_defs(self) -> List[Dict[str, Any]]:
        defs = getattr(self.shared, "steam_achievement_watchers", []) or []
        if not isinstance(defs, list):
            return []
        return [a for a in defs if isinstance(a, dict)]

    def _ensure_achievement_state(self) -> Dict[str, bool]:
        if not hasattr(self, "steam_achievement_state") or not isinstance(self.steam_achievement_state, dict):
            self.steam_achievement_state = {}
        return self.steam_achievement_state

    def _quest_metric_value(self, metric: str) -> int:
        key = str(metric or "").strip().lower()
        if key == "money":
            return int(self.get_money())
        if key == "vessels_built":
            return len(self.get_all_vessels())
        if key == "buildings_built":
            return len(self.get_all_buildings())
        if key == "planets_discovered":
            return len(getattr(self, "discovered_planets", []) or [])
        if key == "moon_landings":
            return int(getattr(self, "quest_counters", {}).get("moon_landings", 0))
        if key == "left_solar_system":
            for v in self.get_all_vessels():
                chunk = getattr(v, "home_chunk", None)
                gal = getattr(chunk, "galaxy", None)
                sys = getattr(chunk, "system", None)
                if gal == 0 or sys == 0:
                    return 1
            return 0
        if key == "astronauts":
            return len(getattr(self, "astronauts", {}) or {})
        if key == "rp":
            return int(getattr(self, "research_points", 0))
        if key == "ep":
            return int(getattr(self, "exploration_points", 0))
        if key == "pp":
            return int(getattr(self, "publicity_points", 0))
        if key == "xp":
            return int(getattr(self, "experience_points", 0))
        if key == "blastoff":
            qc = getattr(self, "quest_counters", {}) or {}
            return int(qc.get("blastoff", 0))
        if key == "strap_on_vessels":
            try:
                from vessel_components import Components
            except Exception:
                Components = None
            count = 0
            for v in self.get_all_vessels():
                try:
                    comps = getattr(v, "components", []) or []
                    if any(int(getattr(c, "id", 0)) == int(getattr(Components, "STRAP_ON_BOOSTER", 29)) for c in comps):
                        count += 1
                except Exception:
                    continue
            qc = getattr(self, "quest_counters", {}) or {}
            counter = int(qc.get("strap_on_vessels", 0))
            return int(max(counter, count))
        if key == "max_probe_inspected_planets":
            qc = getattr(self, "quest_counters", {}) or {}
            return int(qc.get("max_probe_inspected_planets", 0))
        if key == "magnetometer_activated":
            qc = getattr(self, "quest_counters", {}) or {}
            return int(qc.get("magnetometer_activated", 0))
        if key == "rover_moon_landings":
            qc = getattr(self, "quest_counters", {}) or {}
            return int(qc.get("rover_moon_landings", 0))
        if key == "rover_mars_landings":
            qc = getattr(self, "quest_counters", {}) or {}
            return int(qc.get("rover_mars_landings", 0))
        if key == "moon_rock_earth":
            qc = getattr(self, "quest_counters", {}) or {}
            return int(qc.get("moon_rock_earth", 0))
        if key == "agency_income_per_second":
            return int(getattr(self, "income_per_second", 0))
        if key == "satellites_in_orbit":
            try:
                from vessel_components import Components
            except Exception:
                Components = None
            count = 0
            for v in self.get_all_vessels():
                if Components and int(getattr(v, "payload", 0)) != int(Components.COMMUNICATIONS_SATELLITE):
                    continue
                if int(getattr(v, "stage", 1)) != 0:
                    continue
                if bool(getattr(v, "landed", False)):
                    continue
                count += 1
            return int(count)
        return 0

    def _steam_stat_metric_value(self, metric: str) -> float:
        key = str(metric or "").strip().lower()
        if key == "commsat_in_orbit":
            key = "satellites_in_orbit"
        if key == "satellites_in_orbit":
            try:
                from vessel_components import Components
            except Exception:
                Components = None
            count = 0
            for v in self.get_all_vessels():
                if Components and int(getattr(v, "payload", 0)) != int(Components.COMMUNICATIONS_SATELLITE):
                    continue
                if int(getattr(v, "stage", 1)) != 0:
                    continue
                if bool(getattr(v, "landed", False)):
                    continue
                home = getattr(v, "home_planet", None)
                atm = float(getattr(home, "atmosphere_km", 0.0)) if home else 0.0
                if float(getattr(v, "altitude", 0.0)) <= atm + 1e-6:
                    continue
                count += 1
            return float(count)
        if key == "stranded_astronauts":
            return float(getattr(self, "stat_counters", {}).get("stranded_astronauts", 0))
        if key == "longest_manned_mission_days":
            return float(getattr(self, "stat_counters", {}).get("longest_manned_mission_days", 0.0))
        if key == "oldest_agency_age_days":
            return float(getattr(self, "age_days", 0.0))
        if key == "max_satellite_level":
            return float(getattr(self, "stat_counters", {}).get("max_satellite_level", 0))
        if key == "speed_record_mach":
            return float(getattr(self, "stat_counters", {}).get("speed_record_mach", 0.0))
        if key == "cell_tower_level":
            return float(getattr(self, "stat_counters", {}).get("cell_tower_level", 0))
        if key == "vessels_launched":
            return float(getattr(self, "stat_counters", {}).get("vessels_launched", 0))
        if key == "planets_visited":
            return float(len(getattr(self, "visited_planets", set()) or set()))
        return 0.0

    def _achievement_metric_value(self, metric: str) -> float:
        return float(self._steam_stat_metric_value(metric))

    def _resolve_achievement_metric(self, metric: str, stat_name: str) -> str:
        raw_metric = str(metric or "").strip()
        raw_stat = str(stat_name or "").strip()
        if raw_metric and not raw_stat:
            return raw_metric
        if raw_metric and raw_stat:
            return raw_metric
        if not raw_metric and not raw_stat:
            return ""
        # If a stat name is provided, map it back to a metric when possible.
        stat_defs = self._get_stat_defs()
        by_stat = {str(s.get("stat_name", "")).strip(): str(s.get("metric", "")).strip() for s in stat_defs}
        if raw_stat in by_stat and by_stat[raw_stat]:
            return by_stat[raw_stat]
        # Accept stat-like metric (e.g., "stat_speed_record_mach") and map if known.
        if raw_stat in by_stat:
            return by_stat[raw_stat]
        return raw_stat

    def record_quest_metric(self, metric: str, delta: int = 1) -> None:
        key = str(metric or "").strip().lower()
        if not key:
            return
        if not hasattr(self, "quest_counters") or not isinstance(self.quest_counters, dict):
            self.quest_counters = {}
        cur = int(self.quest_counters.get(key, 0))
        self.quest_counters[key] = max(0, cur + int(delta))

    def update_quest_progress(self) -> List[Dict[str, Any]]:
        """
        Recompute quest progress and return any newly completed quest defs.
        """
        completed_now = []
        state = self._ensure_quest_state()
        for q in self._get_quest_defs():
            qid = str(q.get("id", "")).strip()
            if not qid:
                continue
            target = int(q.get("target", 0) or 0)
            metric = str(q.get("metric", "")).strip()
            progress = self._quest_metric_value(metric)

            entry = state.get(qid, {})
            # keep progress monotonic once recorded
            prev_progress = int(entry.get("progress", 0) or 0)
            entry["progress"] = max(prev_progress, int(progress))
            # once completed, stay completed
            previously_completed = bool(entry.get("completed", False))
            now_completed = (progress >= target) if target > 0 else True
            entry["completed"] = previously_completed or now_completed
            entry.setdefault("claimed", False)
            state[qid] = entry

            if entry["completed"] and not entry.get("claimed"):
                completed_now.append(q)
        return completed_now

    def update_steam_stats(self) -> List[tuple[str, float, Dict[str, Any]]]:
        """
        Return stat updates (stat_name, value, meta) for watchers whose values changed.
        """
        updates: List[tuple[str, float, Dict[str, Any]]] = []
        state = self._ensure_stat_state()
        for s in self._get_stat_defs():
            stat_name = str(s.get("stat_name", "")).strip()
            metric = str(s.get("metric", "")).strip()
            mode = str(s.get("mode", "set")).strip().lower()
            if not stat_name or not metric:
                continue
            value = int(self._steam_stat_metric_value(metric))
            last = state.get(stat_name)
            if mode == "max" and last is not None:
                value = max(value, int(float(last)))
            if last is None or value != float(last):
                updates.append((stat_name, value, s))
                state[stat_name] = value
        return updates

    def update_steam_achievements(self) -> List[Dict[str, Any]]:
        """
        Return achievement defs that are newly completed and not yet unlocked.
        """
        unlocked: List[Dict[str, Any]] = []
        state = self._ensure_achievement_state()
        for a in self._get_achievement_defs():
            aid = str(a.get("id", "")).strip()
            if not aid:
                continue
            metric = self._resolve_achievement_metric(
                a.get("metric", ""),
                a.get("stat_name", "") or a.get("progress_stat", ""),
            )
            target = float(a.get("target", 0) or 0)
            if not metric:
                continue
            progress = self._achievement_metric_value(metric)
            if progress < target:
                continue
            if state.get(aid):
                continue
            unlocked.append(a)
        return unlocked

    def mark_achievement_unlocked(self, achievement_id: str) -> None:
        state = self._ensure_achievement_state()
        aid = str(achievement_id or "").strip()
        if not aid:
            return
        state[aid] = True

    def mark_quest_claimed(self, quest_id: str) -> None:
        state = self._ensure_quest_state()
        qid = str(quest_id or "").strip()
        if not qid:
            return
        entry = state.get(qid, {})
        entry["claimed"] = True
        state[qid] = entry

    def record_stat_counter(self, stat_key: str, delta: float = 1.0) -> None:
        key = str(stat_key or "").strip().lower()
        if not key:
            return
        if not hasattr(self, "stat_counters") or not isinstance(self.stat_counters, dict):
            self.stat_counters = {}
        cur = float(self.stat_counters.get(key, 0.0))
        self.stat_counters[key] = max(0.0, cur + float(delta))

    def update_stat_records(self) -> None:
        """
        Update rolling record stats (max satellite level, speed record, mission time, cell tower).
        """
        if not hasattr(self, "stat_counters") or not isinstance(self.stat_counters, dict):
            self.stat_counters = {}

        # Longest manned mission time (days)
        max_manned = 0.0
        for v in self.get_all_vessels():
            max_manned = max(max_manned, float(getattr(v, "manned_mission_time_days", 0.0)))
        self.stat_counters["longest_manned_mission_days"] = max(
            float(self.stat_counters.get("longest_manned_mission_days", 0.0)),
            max_manned,
        )

        # Speed record (mach)
        MACH_KM_S = 0.343
        max_mach = 0.0
        for v in self.get_all_vessels():
            vx, vy = getattr(v, "velocity", (0.0, 0.0))
            speed = math.hypot(float(vx), float(vy))
            max_mach = max(max_mach, speed / MACH_KM_S if MACH_KM_S > 0 else 0.0)
        self.stat_counters["speed_record_mach"] = max(
            float(self.stat_counters.get("speed_record_mach", 0.0)),
            max_mach,
        )

        # Max satellite level (upgrade tier)
        max_sat_tier = 0
        try:
            from vessel_components import Components
            from upgrade_tree import UPGRADE_TREES_BY_PAYLOAD
        except Exception:
            Components = None
            UPGRADE_TREES_BY_PAYLOAD = {}
        for v in self.get_all_vessels():
            if Components and int(getattr(v, "payload", 0)) != int(Components.COMMUNICATIONS_SATELLITE):
                continue
            unlocked = getattr(v, "current_payload_unlocked", lambda: set())()
            tree = UPGRADE_TREES_BY_PAYLOAD.get(int(getattr(v, "payload", 0)), {})
            tier = 0
            for uid in unlocked:
                node = tree.get(int(uid))
                if node:
                    tier = max(tier, int(getattr(node, "tier", 0)))
            max_sat_tier = max(max_sat_tier, tier)
        self.stat_counters["max_satellite_level"] = max(
            float(self.stat_counters.get("max_satellite_level", 0)),
            max_sat_tier,
        )

        # Max cell tower level
        try:
            from buildings import BuildingType
        except Exception:
            BuildingType = None
        max_tower = 0
        for b in self.get_all_buildings():
            btype = getattr(b, "building_type", getattr(b, "type", None))
            if BuildingType and int(btype) != int(BuildingType.NETWORK_TOWER):
                continue
            max_tower = max(max_tower, int(getattr(b, "level", 1)))
        self.stat_counters["cell_tower_level"] = max(
            float(self.stat_counters.get("cell_tower_level", 0)),
            max_tower,
        )

    def quest_state_payload(self) -> Dict[str, Dict[str, Any]]:
        state = self._ensure_quest_state()
        payload = {}
        for q in self._get_quest_defs():
            qid = str(q.get("id", "")).strip()
            if not qid:
                continue
            entry = state.get(qid, {})
            payload[qid] = {
                "progress": int(entry.get("progress", 0)),
                "target": int(q.get("target", 0) or 0),
                "completed": bool(entry.get("completed", False)),
                "claimed": bool(entry.get("claimed", False)),
            }
        return payload

    def discover_planet(self, planet_id: int, notify: bool = True) -> bool:
        """Mark a planet as discovered for this agency (no base required)
        and optionally notify the agency."""
        try:
            pid = int(planet_id)
        except Exception:
            return False
        if pid <= 0:
            return False

        # Already discovered? No-op.
        if hasattr(self, "discovered_planets") and pid in self.discovered_planets:
            return False

        # Ensure the set exists
        if not hasattr(self, "discovered_planets") or self.discovered_planets is None:
            self.discovered_planets = set()

        self.discovered_planets.add(pid)

        if not notify:
            return True

        # Resolve planet name (best-effort from loaded chunks)
        name = f"Planet {pid}"
        try:
            cm = getattr(self.shared, "chunk_manager", None)
            if cm is not None:
                for ch in getattr(cm, "loaded_chunks", {}).values():
                    try:
                        obj = ch.get_object_by_id(pid)
                    except Exception:
                        obj = None
                    if obj is not None:
                        nm = getattr(obj, "name", None)
                        if nm:
                            name = str(nm)
                            break
        except Exception:
            pass

        # Send async toast: "Agency discovered <planet name>"
        try:
            udp = getattr(self.shared, "udp_server", None)
            loop = getattr(self.shared, "main_loop", None)
            if udp and loop and loop.is_running():
                import asyncio
                msg = f"Agency discovered {name}."
                asyncio.run_coroutine_threadsafe(udp.notify_agency(self.id64, 2, msg), loop)
        except Exception as e:
            print(f"⚠️ discover_planet notify failed: {e}")

        return True


    def has_discovered(self, planet_id: int) -> bool:
        try:
            return int(planet_id) in self.discovered_planets
        except Exception:
            return False

    def undiscover_planet(self, planet_id: int) -> bool:
        try:
            pid = int(planet_id)
        except Exception:
            return False
        if pid in self.discovered_planets:
            self.discovered_planets.remove(pid)
            return True
        return False

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
        # remember previous to detect changes that require vessel refresh
        prev_attrs = dict(getattr(self, "attributes", {}) or {})
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
            try:
                btype = self._type_to_int(getattr(b, "building_type", getattr(b, "type", 0)))
                fresh = self.shared.buildings_by_id.get(int(btype), {}).get("attributes", {}).get("buildinglevel_unlocks", {})
                if isinstance(fresh, dict) and fresh:
                    unlocks = fresh
            except Exception:
                pass

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
                # --- thermal resistance bonus ---
                add_tr = float(effects.get("add_thermal_resistance", 0.0))
                if add_tr:
                    attrs["thermal_resistance_bonus"] = attrs.get("thermal_resistance_bonus", 0.0) + add_tr
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

        # 5) If thermal bonus changed, refresh vessel stats once
        try:
            if attrs.get("thermal_resistance_bonus", 0.0) != prev_attrs.get("thermal_resistance_bonus", 0.0):
                for v in self.get_all_vessels():
                    try:
                        v.calculate_vessel_stats()
                    except Exception:
                        continue
        except Exception:
            pass

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
        rp_prog = self._xp_level_progress(self.research_points)
        ep_prog = self._xp_level_progress(self.exploration_points)
        pp_prog = self._xp_level_progress(self.publicity_points)
        xp_prog = self._xp_level_progress(self.experience_points)

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
            "invited": list(getattr(self, "invited", set()) or []),
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
            "astros_by_planet": astros_by_planet,
            "quest_state": self.quest_state_payload(),
            "discovered_planets": sorted(int(pid) for pid in self.discovered_planets),
            "rp": int(self.research_points),
            "ep": int(self.exploration_points),
            "pp": int(self.publicity_points),
            "xp": int(self.experience_points),
            "levels": {
                "rp": int(rp_prog["level"]),
                "ep": int(ep_prog["level"]),
                "pp": int(pp_prog["level"]),
                "xp": int(xp_prog["level"]),
            },
            "next_level": {
                "rp": int(rp_prog["next"]),
                "ep": int(ep_prog["next"]),
                "pp": int(pp_prog["next"]),
                "xp": int(xp_prog["next"]),
            },
            "progress": {
                "rp": int(rp_prog["into"]),
                "ep": int(ep_prog["into"]),
                "pp": int(pp_prog["into"]),
                "xp": int(xp_prog["into"]),
            },
            "flag": int(self.flag),
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
            "rp": int(self.research_points),
            "ep": int(self.exploration_points),
            "pp": int(self.publicity_points),
            "xp": int(self.experience_points),
        }
