# payload_behavior.py
from abc import ABC, abstractmethod
import math
from upgrade_tree import T_UP
from physics import AU_KM
from utils import shortest_delta_deg
from vessels import *
from packet_types import DataGramPacketType
import struct
from gameobjects import Planet
import random



class PayloadBehavior(ABC):
    """One instance per vessel (holds per-vessel state)."""

    def __init__(self, vessel):
        self.vessel = vessel

    def on_attach(self): pass                 # when payload becomes current (stage==0)
    def on_detach(self): pass                 # when staging drops it
    def on_event(self, event, **data): pass   # generic event hook
    def on_tick(self, dt: float): pass        # per-tick behavior

# --- concrete strategies ---
def _get_agency_attr(agency, key: str, default):
    # Support either an attributes dict or flat fields on the Agency
    attrs = getattr(agency, "attributes", None)
    if isinstance(attrs, dict) and key in attrs:
        return attrs.get(key, default)
    return getattr(agency, key, default)



class CommsSatellite(PayloadBehavior):
    def on_attach(self):
        self.vessel._apply_stats()

    def _ping_radius(self) -> float:
        """Return current ping radius from unlocked PING upgrades."""
        unlocked = self.vessel.current_payload_unlocked()
        if int(T_UP.PING2) in unlocked:
            return 5000.0
        if int(T_UP.PING1) in unlocked:
            return 3000.0
        return 0.0

    def _nearby_friendly_vessels(self, radius_km: float):
        """Yield friendly, deployed vessels in the same system within radius."""
        v = self.vessel
        agency = v.shared.agencies.get(v.agency_id)
        if not agency or not v.home_chunk:
            return []
        vx, vy = v.position
        out = []
        for other in getattr(agency, "vessels", []):
            if other is v:
                continue
            if getattr(other, "home_chunk", None) is not v.home_chunk:
                continue
            if getattr(other, "stage", 1) != 0:  # only double deployed payloads
                continue
            ox, oy = other.position
            if math.hypot(ox - vx, oy - vy) <= radius_km:
                out.append(other)
        return out

    def on_tick(self, dt: float):
        v = self.vessel
        #print(f"[CommsSat] enter tick: oid={getattr(v,'object_id',None)} stage={v.stage} "
         
        #   f"agency_id={v.agency_id} home_chunk={bool(v.home_chunk)}")
        if v.stage != 0:
            print("[CommsSat] abort: stage != 0")
            return

        agency = v.shared.agencies.get(v.agency_id)
        if not agency:
            print("[CommsSat] abort: agency not found")
            return

        gs = float(getattr(v.shared, "gamespeed", 1.0))
        seconds = dt / max(1e-9, gs)

        # Satellite's own income tick
        base_income_sat = float(v.stats.get("income", {}).get("base") or v._payload_base_income())
        sat_bonus       = float(_get_agency_attr(agency, "satellite_bonus_income", 0.0))
        global_mult     = float(_get_agency_attr(agency, "global_cash_multiplier", 1.0))
        regional_mult = v.planet_income_multiplier()
        payout = (base_income_sat + sat_bonus) * global_mult * seconds * regional_mult
        if payout > 0:
            agency.distribute_money(payout)
            v.credit_income(payout)

        # PING: add extra payout equal to base income of each nearby friendly vessel
        radius = self._ping_radius()
        if radius <= 0.0:
            return

        extra_total = 0.0
        for other in self._nearby_friendly_vessels(radius):
            base_other = float(other._payload_base_income())
            if base_other > 0.0:
                extra_total += base_other

        if extra_total > 0.0:
            extra_payout = extra_total * global_mult * seconds
            agency.distribute_money(extra_payout)
            v.credit_income(extra_payout)

class SpaceTelescope(PayloadBehavior):

    def _discover_targets(self, objs):
        v = self.vessel
        shared = getattr(v, "shared", None)
        if not shared:
            return 0
        agency = shared.agencies.get(v.agency_id)
        if not agency:
            return 0

        new = 0
        for o in objs:
            if not self._is_discoverable(o):   # ← skip moons
                continue
            pid = int(getattr(o, "object_id", 0) or 0)
            if pid > 0 and agency.discover_planet(pid):
                new += 1
        return new

    
    def _is_discoverable(self, o) -> bool:
        # Only discover *planets* that are not moons
        return isinstance(o, Planet) and not bool(getattr(o, "is_moon", False))


    def _apply_rcs_pointing(self, seconds: float):
        v = self.vessel
        current_aim = -float(v.rotation)
        target_aim  = float(getattr(v, "telescope_rcs_angle", 0.0))
        delta = shortest_delta_deg(current_aim, target_aim)
        if abs(delta) <= 1e-6:
            return
        max_rate = float(v.stats.get("telescope", {}).get("max_rate_deg_s", 5.0))
        step = max(-max_rate * seconds, min(max_rate * seconds, delta))
        v.rotation -= step

    def _send_sight_to_controller(self):
        v = self.vessel
        pid = int(getattr(v, "controlled_by", 0) or 0)
        if not pid:
            return 0
        shared = getattr(v, "shared", None)
        udp = getattr(shared, "udp_server", None)
        if not (udp and getattr(udp, "transport", None)):
            return 0
        player = shared.players.get(pid)
        sess = getattr(player, "session", None)
        if not (sess and sess.alive and getattr(sess, "udp_port", None)):
            return 0

        ids = [int(getattr(o, "object_id", 0)) for o in v.telescope_targets_in_sight]
        buf = bytearray()
        buf.append(int(DataGramPacketType.TELESCOPE_SIGHT))
        buf += struct.pack("<Q", int(v.object_id))
        buf += struct.pack("<f", v.telescope_fov_deg)
        buf += struct.pack("<H", len(ids))
        for oid in ids:
            buf += struct.pack("<Q", oid)

        udp.transport.sendto(buf, (sess.remote_ip, sess.udp_port))
        return 1

    def on_tick(self, dt: float):
        v = self.vessel
        if v.stage != 0:
            return

        shared = getattr(v, "shared", None)
        if not shared:
            return
        agency = shared.agencies.get(v.agency_id)
        if not agency:
            return

        # Real seconds, not sim seconds
        gs = float(getattr(shared, "gamespeed", 1.0))
        seconds = dt / max(1e-9, gs)

        # (1) Base income (+Resolution)
        unlocked = v.current_payload_unlocked()
        base_income = float(v.stats.get("income", {}).get("base") or v._payload_base_income() or 0.0)
        if int(T_UP.RESOLUTION1) in unlocked:
            base_income += 100.0
        global_mult  = float(getattr(agency, "global_cash_multiplier", 1.0))
        regional_mult = v.planet_income_multiplier()
        payout = base_income * global_mult * regional_mult * seconds
        if payout > 0.0:
            agency.distribute_money(payout)
            v.credit_income(payout)

        # (2) RCS pointing (no fuel)
        self._apply_rcs_pointing(seconds)

        # (3) Compute current sight list
        v.telescope_targets_in_sight.clear()
        candidates = [
            o for o in (list(v._iter_planets_in_same_system()) or [])
            if isinstance(o, Planet) and not bool(getattr(o, "is_moon", False))
        ]
        if not candidates:
            # reset push state when nothing to show
            self._last_ids = None
            self._sight_push_accum = 0.0
            return

        # FOV upgrades
        fov_deg = float(v.telescope_fov_deg)
        if int(T_UP.FOCUS1) in unlocked:
            fov_deg += 7.0
        if hasattr(T_UP, "FOCUS2") and int(T_UP.FOCUS2) in unlocked:
            fov_deg += 13.0
        half_fov = max(0.0, fov_deg * 0.5)

        # Range upgrades
        range_km = float(v.telescope_range_km)
        if int(T_UP.EXPOSURE1) in unlocked:
            range_km += float(AU_KM)
        if hasattr(T_UP, "ZOOM1") and int(T_UP.ZOOM1) in unlocked:
            range_km += float(AU_KM) * 3.5
        if hasattr(T_UP, "ZOOM2") and int(T_UP.ZOOM2) in unlocked:
            range_km += float(AU_KM) * 10.0

        rx, ry = v.position
        aim_deg = -float(v.rotation)

        for obj in candidates:
            try:
                dx = float(obj.position[0]) - float(rx)
                dy = float(obj.position[1]) - float(ry)
                dist_km = math.hypot(dx, dy)
                if dist_km > range_km:
                    continue
                to_target_deg = math.degrees(math.atan2(dy, dx))
                if abs(shortest_delta_deg(aim_deg, to_target_deg)) <= half_fov:
                    v.telescope_targets_in_sight.append(obj)
            except Exception:
                continue

        # (3b) DISCOVERY: mark all sighted *non-moon* bodies as discovered
        if hasattr(agency, "discover_planet") and callable(getattr(agency, "discover_planet")):
            for o in v.telescope_targets_in_sight:
                if not self._is_discoverable(o):   # ← skip moons
                    continue
                pid = int(getattr(o, "object_id", 0) or 0)
                if pid > 0:
                    try:
                        agency.discover_planet(pid)
                    except Exception:
                        pass


        # (4) PLANET_IMAGE bonus: +$100 per target currently in sight (scaled)
        if hasattr(T_UP, "PLANET_IMAGE") and int(T_UP.PLANET_IMAGE) in unlocked:
            n = len(v.telescope_targets_in_sight)
            if n > 0:
                bonus = 100.0 * n * global_mult * regional_mult * seconds
                agency.distribute_money(bonus)
                v.credit_income(bonus)

        # (5) Push sight to controller only when controlled, throttled, and on change
        controlled = bool(int(getattr(v, "controlled_by", 0) or 0))
        ids_now = tuple(sorted(int(getattr(o, "object_id", 0)) for o in v.telescope_targets_in_sight))
        if controlled:
            self._sight_push_accum = getattr(self, "_sight_push_accum", 0.0) + seconds
            changed = ids_now != getattr(self, "_last_ids", None)
            if changed or self._sight_push_accum >= 0.25:  # ~4 Hz
                self._last_ids = ids_now
                self._sight_push_accum = 0.0
                self._send_sight_to_controller()
        else:
            # reset so the next time a player takes control, it pushes immediately
            self._last_ids = None
            self._sight_push_accum = 0.0



class Probe(PayloadBehavior):
    """
    Base income * (# unique non-moon planets visited).
    Visit = within N× radius of strongest gravity source (N=4, Flyby1->6, Flyby2->10).
    Perijove: ×2 income while within 4×R of a gas giant.
    AACS:     ×2 income if pointing within 5° of home planet.
    """

    def _maybe_discover(self, body):
        """Discover any body we meaningfully observed/inspected."""
        if not isinstance(body, Planet):
            return False
        v = self.vessel
        shared = getattr(v, "shared", None)
        if not shared:
            return False
        agency = shared.agencies.get(v.agency_id)
        if not agency:
            return False
        pid = int(getattr(body, "object_id", 0) or 0)
        return agency.discover_planet(pid) if pid > 0 else False
    
    def on_attach(self):
            """When the probe becomes active (stage==0), immediately inspect its home planet once."""
            if getattr(self, "_did_initial_inspect", False):
                return
            v = self.vessel

            # Prefer explicit home_planet; else try launchpad_planet_id
            planet = getattr(v, "home_planet", None)
            if planet is None:
                pid = int(getattr(v, "launchpad_planet_id", 0) or 0)
                if pid and getattr(v, "home_chunk", None):
                    planet = v.home_chunk.get_object_by_id(pid)
                # (Optional) last-resort: scan all loaded chunks
                if planet is None:
                    try:
                        from gameobjects import Planet
                        for ch in getattr(v.shared.chunk_manager, "loaded_chunks", {}).values():
                            obj = ch.get_object_by_id(pid)
                            if isinstance(obj, Planet):
                                planet = obj
                                break
                    except Exception:
                        pass

            # Apply your existing probe rules: only non-moons
            from gameobjects import Planet
            if isinstance(planet, Planet) and not bool(getattr(planet, "is_moon", False)):
                pid = int(getattr(planet, "object_id", 0) or 0)
                if pid and pid not in v.planets_visited:
                    v.planets_visited.append(pid)
                    self._notify_agency_visit(planet)

            self._did_initial_inspect = True

    def _visit_threshold_multiplier(self) -> float:
        """4× by default; 6× with FLYBY1; 10× with FLYBY2 (takes precedence)."""
        v = self.vessel
        unlocked = v.current_payload_unlocked()
        if int(T_UP.FLYBY2) in unlocked:
            return 10.0
        if int(T_UP.FLYBY1) in unlocked:
            return 6.0
        return 4.0

    def _maybe_mark_visit(self):
        v = self.vessel
        src = getattr(v, "strongest_gravity_source", None)
        if not isinstance(src, Planet):
            return

        # NEW: discovery is independent of 'visited' (so moons get discovered too)
        self._maybe_discover(src)

        # Keep your *visited* rules planet-only (skip moons for income):
        if bool(getattr(src, "is_moon", False)):
            return

        dx = float(v.position[0]) - float(src.position[0])
        dy = float(v.position[1]) - float(src.position[1])
        dist_km = math.hypot(dx, dy)
        R = float(getattr(src, "radius_km", 0.0))
        if R <= 0.0:
            return

        thresh = self._visit_threshold_multiplier() * R
        if dist_km > thresh:
            return

        pid = int(getattr(src, "object_id", 0))
        if pid <= 0:
            return

        if pid not in v.planets_visited:
            v.planets_visited.append(pid)
            self._notify_agency_visit(src)

    def _notify_agency_visit(self, planet):
        v = self.vessel
        shared = getattr(v, "shared", None)
        if not shared:
            return
        udp = getattr(shared, "udp_server", None)
        if not udp:
            return

        agency = shared.agencies.get(v.agency_id)
        if not agency:
            return

        name = getattr(planet, "name", f"Planet {getattr(planet, 'object_id', 0)}")
        msg = f"Probe inspected {name}."
        try:
            loop = getattr(shared, "main_loop", None)
            if loop and loop.is_running():
                import asyncio
                asyncio.run_coroutine_threadsafe(udp.notify_agency(agency.id64, 2, msg), loop)
        except Exception as e:
            print(f"⚠️ probe notify failed: {e}")

    def _perijove_multiplier(self) -> float:
        """×2 if PERIJOVE is unlocked and we are within 4×R of a gas giant."""
        v = self.vessel
        unlocked = v.current_payload_unlocked()
        if int(T_UP.PERIJOVE) not in unlocked:
            return 1.0

        src = getattr(v, "strongest_gravity_source", None)
        if not isinstance(src, Planet):
            return 1.0
        if not bool(getattr(src, "is_gas_giant", False)):
            return 1.0

        R = float(getattr(src, "radius_km", 0.0))
        if R <= 0.0:
            return 1.0

        dx = float(v.position[0]) - float(src.position[0])
        dy = float(v.position[1]) - float(src.position[1])
        dist_km = math.hypot(dx, dy)
        return 1.3 if dist_km <= 4.0 * R else 1.0

    def _aacs_multiplier(self) -> float:
        """×2 if AACS unlocked and pointing within 5° of home planet."""
        v = self.vessel
        unlocked = v.current_payload_unlocked()
        if int(T_UP.AACS) not in unlocked:
            return 1.0
        home = getattr(v, "home_planet", None)
        if not isinstance(home, Planet):
            return 1.0

        # Aim is -v.rotation (same convention as telescope)
        aim_deg = -float(v.rotation)
        dx = float(home.position[0]) - float(v.position[0])
        dy = float(home.position[1]) - float(v.position[1])
        to_home_deg = math.degrees(math.atan2(dy, dx))
        delta = shortest_delta_deg(aim_deg, to_home_deg)
        return 1.4 if abs(delta) <= 5.0 else 1.0

    def on_tick(self, dt: float):
        v = self.vessel
        if v.stage != 0:
            return

        # 1) Check for new visit (strongest puller only, no moons)
        self._maybe_mark_visit()

        # 2) Income payout
        agency = v.shared.agencies.get(v.agency_id)
        if not agency:
            return

        gs = float(getattr(v.shared, "gamespeed", 1.0))
        seconds = dt / max(1e-9, gs)

        base_income = float(v.stats.get("income", {}).get("base") or v._payload_base_income() or 0.0)
        if base_income <= 0.0:
            return

        # Only count unique visits (should already be unique, but guard anyway)
        visited_count = len(set(v.planets_visited))
        if visited_count <= 0:
            return

        global_mult  = float(getattr(agency, "global_cash_multiplier", 1.0))
        regional_mult = v.planet_income_multiplier()

        # Apply situational multipliers
        situational_mult = self._perijove_multiplier() * self._aacs_multiplier()

        payout = base_income * visited_count * situational_mult * global_mult * regional_mult * seconds
        if payout > 0.0:
            agency.distribute_money(payout)
            v.credit_income(payout)


SUN_RADIUS_KM = 696_340.0


class SolarOrbiter(PayloadBehavior):
    """
    Income scales with proximity to the Sun via an aggressive curve:
      - multiplier = 20 at the Sun's radius
      - decays exponentially to 1 by 0.5 AU
      - decays below 1 beyond 0.5 AU toward 0
      - clamped to [0, 20]
    """

    # Tunables (per AU)
    K_NEAR = 5.0   # curvature inside 0.5 AU (20 -> 1)
    K_FAR  = 2.0   # curvature beyond 0.5 AU (1 -> 0)

    def _solar_mult_from_distance_au(self, r_au: float) -> float:
        """Return the proximity multiplier in [0, 20] given distance in AU."""
        # Protect against being *inside* the photosphere:
        sun_radius_au = max(1e-9, float(SUN_RADIUS_KM) / float(AU_KM))
        r = max(r_au, sun_radius_au)

        r1 = 0.5  # AU where multiplier should be exactly 1

        if r <= r1:
            # Exponential that maps r = sun_radius_au -> 20, and r = 0.5 AU -> 1
            # m(r) = 1 + 19 * [exp(-k*(r - R)) - exp(-k*(r1 - R))] / [1 - exp(-k*(r1 - R))]
            k = self.K_NEAR
            num = math.exp(-k * (r - sun_radius_au)) - math.exp(-k * (r1 - sun_radius_au))
            den = 1.0 - math.exp(-k * (r1 - sun_radius_au))
            m = 1.0 + 19.0 * (num / max(1e-12, den))
        else:
            # Past 0.5 AU, decay below 1 toward 0
            k = self.K_FAR
            m = math.exp(-k * (r - r1))

        # Clamp
        if m < 0.0: m = 0.0
        if m > 20.0: m = 20.0
        return m

    def on_tick(self, dt: float):
        v = self.vessel
        if v.stage != 0:
            return

        agency = v.shared.agencies.get(v.agency_id)
        if not agency:
            return

        # Real seconds
        gs = float(getattr(v.shared, "gamespeed", 1.0))
        seconds = dt / max(1e-9, gs)

        base_income = float(v.stats.get("income", {}).get("base") or v._payload_base_income() or 0.0)
        if base_income <= 0.0:
            return

        # Distance to Sun assumed from origin (0,0) like your solar helper
        rx, ry = v.position
        dist_km = math.hypot(rx, ry)
        r_au = dist_km / float(AU_KM)

        mult = self._solar_mult_from_distance_au(r_au)  # 0..20

        if mult <= 0.0:
            return

        global_mult  = float(getattr(agency, "global_cash_multiplier", 1.0))
        regional_mult = v.planet_income_multiplier()  # typically 1.0 here

        payout = base_income * mult * global_mult * regional_mult * seconds
        if payout > 0.0:
            agency.distribute_money(payout)
            v.credit_income(payout)


class LunarLander:
    """
    - Trains astronauts onboard each real second (xp rate via attributes["training-xp-rate"], default 0.1).
    - Generates $10 * sum(levels) per real second.
    - Awards +200 XP to each astronaut when landing on a *moon* if the vessel's
      previous landing body was different (trip-based, prevents farm by bounce-landing).
    """
    def __init__(self, vessel):
        self.vessel = vessel
        self.payload_id = int(getattr(vessel, "payload", 0))

    def on_attach(self):
        pass

    def on_detach(self):
        pass

    def on_unland(self, planet):
        pass

    def on_land(self, planet, prev_body_id=None):
        v = self.vessel
        shared = getattr(v, "shared", None)
        if not (planet and shared):
            return

        # Moved-from-Vessel mission hook (auto-build if declared on a component)
        self._maybe_build_on_land(planet)

        # Trip-based XP: only when landing on a moon and previous landing was a different body
        try:
            is_moon = bool(getattr(planet, "is_moon", False))
            cur_id  = int(getattr(planet, "object_id", 0) or 0)
            prev_id = int(prev_body_id) if prev_body_id is not None else None
        except Exception:
            is_moon, cur_id, prev_id = False, 0, None

        if is_moon and cur_id > 0 and (prev_id is None or prev_id != cur_id):
            self._award_trip_xp(amount=200.0)
            self._notify_agency(f"{v.name}: astronauts gained +200 XP for completing a trip and landing on {getattr(planet,'name','a moon')}!")

    def on_tick(self, dt: float):
        v = self.vessel
        if v.stage != 0:
            return
        shared = getattr(v, "shared", None)
        if not shared:
            return

        gs = float(getattr(shared, "gamespeed", 1.0))
        real_dt = dt / max(1e-9, gs)

        astronauts = getattr(shared, "astronauts", None)
        if astronauts is None:
            setattr(shared, "astronauts", {})
            astronauts = shared.astronauts

        try:
            xp_rate = float(v._payload_attr("training-xp-rate", 0.1))
        except Exception:
            xp_rate = 0.1

        total_levels = 0
        for aid in getattr(v, "astronauts_onboard", []):
            astro = astronauts.get(int(aid))
            if not astro:
                continue
            if hasattr(astro, "gain_exp"):
                astro.gain_exp(xp_rate * real_dt)
            lvl = int(getattr(astro, "level", 1))
            total_levels += max(1, lvl)

        if total_levels > 0:
            v.credit_income(10.0 * total_levels * real_dt)

    # ---------- helpers ----------
    def _award_trip_xp(self, amount: float):
        v = self.vessel
        shared = getattr(v, "shared", None)
        if not shared:
            return
        astronauts = getattr(shared, "astronauts", None) or {}
        for aid in getattr(v, "astronauts_onboard", []):
            a = astronauts.get(int(aid))
            if a and hasattr(a, "gain_exp"):
                a.gain_exp(float(amount))

    def _notify_agency(self, msg: str):
        v = self.vessel
        shared = getattr(v, "shared", None)
        if not shared:
            return
        try:
            udp = getattr(shared, "udp_server", None)
            if not udp:
                return
            agency = getattr(shared, "agencies", {}).get(int(v.agency_id))
            if not agency:
                return
            loop = getattr(shared, "main_loop", None)
            if loop and loop.is_running():
                import asyncio
                asyncio.run_coroutine_threadsafe(udp.notify_agency(agency.id64, 2, msg), loop)
        except Exception as e:
            print(f"⚠️ notify_agency schedule failed: {e}")

    def _maybe_build_on_land(self, planet):
        v = self.vessel
        if getattr(v, "_build_on_land_fired", False):
            return
        if not planet or not getattr(v, "shared", None):
            return

        planet_name = str(getattr(planet, "name", "")).strip()
        if not planet_name:
            return

        target = None
        for comp in v.components:
            cd = (v.shared.component_data.get(comp.id, {}) or {})
            attrs = (cd.get("attributes", {}) or {})
            bol = attrs.get("build-on-land")
            if isinstance(bol, (list, tuple)) and len(bol) == 2:
                target = (str(bol[0]).strip(), int(bol[1]))
                break
        if not target:
            return

        wanted_name, building_type = target
        if planet_name.lower() != wanted_name.lower():
            return

        agency = v.shared.agencies.get(v.agency_id)
        if not agency:
            return

        base_id = int(getattr(planet, "object_id", 0))
        if base_id == 0:
            return

        for b in agency.bases_to_buildings.get(base_id, []):
            if int(getattr(b, "type", -1)) == building_type:
                v._build_on_land_fired = True
                return

        try:
            from buildings import Building, BuildingType
            angle = float(getattr(v, "landed_angle_offset", 0.0))
            new_building = Building(BuildingType(int(building_type)), v.shared, angle, base_id, agency)
            new_building.constructed = True
            agency.add_building_to_base(base_id, new_building)

            if hasattr(agency, "unlock_building_type") and callable(agency.unlock_building_type):
                agency.unlock_building_type(int(building_type))
            else:
                if not hasattr(agency, "unlocked_buildings") or agency.unlocked_buildings is None:
                    agency.unlocked_buildings = set()
                agency.unlocked_buildings.add(int(building_type))

            if hasattr(agency, "update_attributes"):
                agency.update_attributes()

            udp = getattr(v.shared, "udp_server", None)
            if udp:
                bname = (v.shared.buildings_by_id.get(int(building_type), {}) or {}).get("name", f"Building {building_type}")
                self._notify_agency(f"{agency.name} established {bname} on {planet_name} (mission auto-build)")

            print(f"✅ Auto-built building {building_type} on {planet_name} for agency {agency.id64}")
        except Exception as e:
            print(f"⚠️ build-on-land mission hook failed: {e}")

        v._build_on_land_fired = True



class SpaceShuttle:
    """
    - Trains astronauts onboard each real second (xp rate via attributes["training-xp-rate"], default 0.1).
    - Generates $10 * sum(levels) per real second.
    - Awards +200 XP to each astronaut when landing on a *moon* if the vessel's
      previous landing body was different (trip-based, prevents farm by bounce-landing).
    """
    def __init__(self, vessel):
        self.vessel = vessel
        self.payload_id = int(getattr(vessel, "payload", 0))

    def on_attach(self):
        pass

    def on_detach(self):
        pass

    def on_unland(self, planet):
        pass

    def on_land(self, planet, prev_body_id=None):
        v = self.vessel
        shared = getattr(v, "shared", None)
        if not (planet and shared):
            return

        # Moved-from-Vessel mission hook (auto-build if declared on a component)
        self._maybe_build_on_land(planet)

        # Trip-based XP: only when landing on a moon and previous landing was a different body
        try:
            is_moon = bool(getattr(planet, "is_moon", False))
            cur_id  = int(getattr(planet, "object_id", 0) or 0)
            prev_id = int(prev_body_id) if prev_body_id is not None else None
        except Exception:
            is_moon, cur_id, prev_id = False, 0, None

        if is_moon and cur_id > 0 and (prev_id is None or prev_id != cur_id):
            self._award_trip_xp(amount=200.0)
            self._notify_agency(f"{v.name}: astronauts gained +200 XP for completing a trip and landing on {getattr(planet,'name','a moon')}!")

    def on_tick(self, dt: float):
        v = self.vessel
        if v.stage != 0:
            return
        shared = getattr(v, "shared", None)
        if not shared:
            return

        gs = float(getattr(shared, "gamespeed", 1.0))
        real_dt = dt / max(1e-9, gs)

        astronauts = getattr(shared, "astronauts", None)
        if astronauts is None:
            setattr(shared, "astronauts", {})
            astronauts = shared.astronauts

        try:
            xp_rate = float(v._payload_attr("training-xp-rate", 0.1))
        except Exception:
            xp_rate = 0.1

        total_levels = 0
        for aid in getattr(v, "astronauts_onboard", []):
            astro = astronauts.get(int(aid))
            if not astro:
                continue
            if hasattr(astro, "gain_exp"):
                astro.gain_exp(xp_rate * real_dt)
            lvl = int(getattr(astro, "level", 1))
            total_levels += max(1, lvl)

        if total_levels > 0:
            v.credit_income(10.0 * total_levels * real_dt)

    # ---------- helpers ----------
    def _award_trip_xp(self, amount: float):
        v = self.vessel
        shared = getattr(v, "shared", None)
        if not shared:
            return
        astronauts = getattr(shared, "astronauts", None) or {}
        for aid in getattr(v, "astronauts_onboard", []):
            a = astronauts.get(int(aid))
            if a and hasattr(a, "gain_exp"):
                a.gain_exp(float(amount))

    def _notify_agency(self, msg: str):
        v = self.vessel
        shared = getattr(v, "shared", None)
        if not shared:
            return
        try:
            udp = getattr(shared, "udp_server", None)
            if not udp:
                return
            agency = getattr(shared, "agencies", {}).get(int(v.agency_id))
            if not agency:
                return
            loop = getattr(shared, "main_loop", None)
            if loop and loop.is_running():
                import asyncio
                asyncio.run_coroutine_threadsafe(udp.notify_agency(agency.id64, 2, msg), loop)
        except Exception as e:
            print(f"⚠️ notify_agency schedule failed: {e}")

    def _maybe_build_on_land(self, planet):
        v = self.vessel
        if getattr(v, "_build_on_land_fired", False):
            return
        if not planet or not getattr(v, "shared", None):
            return

        planet_name = str(getattr(planet, "name", "")).strip()
        if not planet_name:
            return

        target = None
        for comp in v.components:
            cd = (v.shared.component_data.get(comp.id, {}) or {})
            attrs = (cd.get("attributes", {}) or {})
            bol = attrs.get("build-on-land")
            if isinstance(bol, (list, tuple)) and len(bol) == 2:
                target = (str(bol[0]).strip(), int(bol[1]))
                break
        if not target:
            return

        wanted_name, building_type = target
        if planet_name.lower() != wanted_name.lower():
            return

        agency = v.shared.agencies.get(v.agency_id)
        if not agency:
            return

        base_id = int(getattr(planet, "object_id", 0))
        if base_id == 0:
            return

        for b in agency.bases_to_buildings.get(base_id, []):
            if int(getattr(b, "type", -1)) == building_type:
                v._build_on_land_fired = True
                return

        try:
            from buildings import Building, BuildingType
            angle = float(getattr(v, "landed_angle_offset", 0.0))
            new_building = Building(BuildingType(int(building_type)), v.shared, angle, base_id, agency)
            new_building.constructed = True
            agency.add_building_to_base(base_id, new_building)

            if hasattr(agency, "unlock_building_type") and callable(agency.unlock_building_type):
                agency.unlock_building_type(int(building_type))
            else:
                if not hasattr(agency, "unlocked_buildings") or agency.unlocked_buildings is None:
                    agency.unlocked_buildings = set()
                agency.unlocked_buildings.add(int(building_type))

            if hasattr(agency, "update_attributes"):
                agency.update_attributes()

            udp = getattr(v.shared, "udp_server", None)
            if udp:
                bname = (v.shared.buildings_by_id.get(int(building_type), {}) or {}).get("name", f"Building {building_type}")
                self._notify_agency(f"{agency.name} established {bname} on {planet_name} (mission auto-build)")

            print(f"✅ Auto-built building {building_type} on {planet_name} for agency {agency.id64}")
        except Exception as e:
            print(f"⚠️ build-on-land mission hook failed: {e}")

        v._build_on_land_fired = True


class Rover(PayloadBehavior):
    """
    While landed, periodically (≈1Hz real time) roll for a mined resource using the
    planet's resource_map weights, and add +1 to the rover's cargo, clamped by
    vessel.cargo_capacity. Sends a CARGO_STATE snapshot to the controller on change.
    """
    def __init__(self, vessel):
        super().__init__(vessel)
        self._accum = 0.0
        self._notified_full = False  # avoid spamming a "full" notice

    def on_attach(self):
        # ensure cargo dict exists
        if not hasattr(self.vessel, "cargo") or self.vessel.cargo is None:
            self.vessel.cargo = {}

    def on_tick(self, dt: float):
        v = self.vessel
        # must be active payload, actually landed, and have a planet
        if v.stage != 0 or not bool(getattr(v, "landed", False)):
            self._accum = 0.0
            self._notified_full = False
            return
        planet = getattr(v, "home_planet", None)
        if planet is None:
            # fallback: if you track landed planet id separately
            pid = int(getattr(v, "launchpad_planet_id", 0) or 0)
            if pid and getattr(v, "home_chunk", None):
                planet = v.home_chunk.get_object_by_id(pid)

        if planet is None:
            print("No planet for rover")
            return

        # convert to real seconds
        seconds = dt
        self._accum += seconds
        if self._accum < 10.0:
            return
        # run roughly once per second, even if we accumulated multiple seconds
        self._accum -= 10.0

        # 1) get the planet's resource map
        resource_map = getattr(planet, "resource_map", {}) or {}
        if not resource_map:
            #print("No recource map for rover")
            return

        # 2) check cargo capacity
        cargo = getattr(v, "cargo", {}) or {}
        cap   = int(max(0, getattr(v, "cargo_capacity", 0)))
        used  = sum(int(max(0, a)) for a in cargo.values())
        if cap > 0 and used >= cap:
            # optional: one-time notification to controller
            return

        # 3) emulate Mining Rig odds (50 per 'level' ~= 5% at level 1) once per sec
        #    If you want rover-specific tuning, add a rover_level or attribute.
        mining_odds = random.randrange(0, 2000)
        if mining_odds <= 1:
            # 4) weighted choice from resource_map
            try:
                resources = list(resource_map.keys())
                weights   = [float(resource_map[r]) for r in resources]
                mined_resource = random.choices(resources, weights=weights, k=1)[0]
                rid = int(mined_resource)
            except Exception:
                return

            # 5) add +1 to cargo, capped by capacity
            add_amt = 1
            if cap > 0:
                add_amt = min(add_amt, max(0, cap - used))
            if add_amt <= 0:
                self._notify_controller_once("Rover cargo is full.")
                return

            cargo[rid] = int(cargo.get(rid, 0)) + add_amt
            v.cargo = cargo  # keep reference consistent

            # 6) push a CARGO_STATE snapshot to the controller (nice but optional)
            self._send_cargo_state(planet_id=int(getattr(planet, "object_id", 0)))
            self._notified_full = False  # cargo changed; allow future full notice again

            if not v.landed or not getattr(v, "home_planet", None):
                return

            # Real seconds, not sim seconds
            gs = float(getattr(v.shared, "gamespeed", 1.0))
            seconds = dt / max(1e-9, gs)

        # 1) Resolve desired speed: deg/s wins; else convert km/s → deg/s; else default.
        try:
            km_per_sec = float(v._payload_attr("rover-km-per-sec", 1.0))
        except Exception:
            km_per_sec = 1.0
        km_per_sec = km_per_sec * 0.1
        if not math.isnan(km_per_sec):
            R = float(getattr(v.home_planet, "radius_km", 1000.0))
            circ = 2.0 * math.pi * max(1e-6, R)
            deg_per_sec = (km_per_sec / circ) * 360.0
        else:
            # default ~0.5°/min ≈ 0.008333°/s
            deg_per_sec = 0.008333

        # 2) Optional: allow player to flip direction with attitude keys while landed
        direction = 1.0
        delta_deg = deg_per_sec * direction * seconds

        # 3) Apply and wrap (use your existing wrap helper if you prefer)
        try:
            from utils import wrap_deg
            v.landed_angle_offset = wrap_deg(v.landed_angle_offset + delta_deg)
        except Exception:
            v.landed_angle_offset = (v.landed_angle_offset + delta_deg) % 360.0

    # ---------- helpers ----------
    def _send_cargo_state(self, planet_id: int):
        try:
            shared = getattr(self.vessel, "shared", None)
            udp    = getattr(shared, "udp_server", None)
            if not (shared and udp and getattr(udp, "transport", None)):
                return
            pkt = udp.build_cargo_state_packet(self.vessel, planet_id)
            # send to controller if any; else skip
            pid = int(getattr(self.vessel, "controlled_by", 0) or 0)
            if pid:
                player = shared.players.get(pid)
                sess = getattr(player, "session", None)
                if sess and sess.alive and getattr(sess, "udp_port", None):
                    udp._udp_send_to_session(sess, pkt)
        except Exception:
            pass

    def _notify_controller_once(self, message: str):
        if self._notified_full:
            return
        try:
            shared = getattr(self.vessel, "shared", None)
            udp    = getattr(shared, "udp_server", None)
            if not (shared and udp and getattr(udp, "transport", None)):
                return
            pid = int(getattr(self.vessel, "controlled_by", 0) or 0)
            if not pid:
                return
            player = shared.players.get(pid)
            sess = getattr(player, "session", None)
            if not (sess and sess.alive and getattr(sess, "udp_port", None)):
                return
            pkt = udp.build_notification_packet(1, message)
            udp._udp_send_to_session(sess, pkt)
            self._notified_full = True
        except Exception:
            pass