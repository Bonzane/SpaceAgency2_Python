# payload_behavior.py
from abc import ABC, abstractmethod
import math
from upgrade_tree import T_UP
from physics import AU_KM
from utils import shortest_delta_deg
from vessels import *
from packet_types import DataGramPacketType
import struct

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
