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

        agency = v.shared.agencies.get(v.agency_id)
        if not agency:
            return

        gs = float(getattr(v.shared, "gamespeed", 1.0))
        seconds = dt / max(1e-9, gs)

        # (1) base income (+Resolution)
        unlocked = v.current_payload_unlocked()
        base_income = float(v.stats.get("income", {}).get("base") or v._payload_base_income())
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

        # (3) always compute sight (even if not controlled)
        v.telescope_targets_in_sight.clear()
        candidates = list(v._iter_planets_in_same_system()) or []
        if not candidates:
            # nothing to scan, also clear last ids so a later control session will push
            self._last_ids = None
            self._sight_push_accum = 0.0
            return

        # FOV with upgrades (your latest spec uses degree bumps)
        fov_deg = float(v.telescope_fov_deg)
        if int(T_UP.FOCUS1) in unlocked:
            fov_deg += 7.0
        if hasattr(T_UP, "FOCUS2") and int(T_UP.FOCUS2) in unlocked:
            fov_deg += 13.0
        half_fov = max(0.0, fov_deg * 0.5)

        # Range with upgrades
        range_km = float(v.telescope_range_km)
        if int(T_UP.EXPOSURE1) in unlocked:
            range_km += float(AU_KM)
        if hasattr(T_UP, "ZOOM1") and int(T_UP.ZOOM1) in unlocked:
            range_km += float(AU_KM) * 3.5

        rx, ry = v.position
        aim_deg = -float(v.rotation)

        for obj in candidates:
            dx, dy = obj.position[0] - rx, obj.position[1] - ry
            dist_km = math.hypot(dx, dy)
            if dist_km > range_km:
                continue
            to_target_deg = math.degrees(math.atan2(dy, dx))
            delta = shortest_delta_deg(aim_deg, to_target_deg)
            if abs(delta) <= half_fov:
                v.telescope_targets_in_sight.append(obj)

        # (4) PLANET_IMAGE: +$100 per target currently in sight (scaled like other income)
        if hasattr(T_UP, "PLANET_IMAGE") and int(T_UP.PLANET_IMAGE) in unlocked:
            n = len(v.telescope_targets_in_sight)
            if n > 0:
                bonus = 100.0 * n * global_mult * regional_mult * seconds
                agency.distribute_money(bonus)
                v.credit_income(bonus)

        # (5) send sight only if controlled; throttle & only on change
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
            # reset so first control push sends immediately
            self._last_ids = None
            self._sight_push_accum = 0.0



class Probe(PayloadBehavior):
    """
    Base income * (# unique non-moon planets visited).
    Visit = within N× radius of strongest gravity source (N=4, Flyby1->6, Flyby2->10).
    Perijove: ×2 income while within 4×R of a gas giant.
    AACS:     ×2 income if pointing within 5° of home planet.
    """

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

        # Do not count moons as 'planets' for probes.
        if bool(getattr(src, "is_moon", False)):
            return

        # distance to current strongest gravity source
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

        # first time close enough: record + notify
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
