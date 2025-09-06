# payload_behavior.py
from abc import ABC, abstractmethod
import math
from upgrade_tree import T_UP


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
        print(f"[CommsSat] enter tick: oid={getattr(v,'object_id',None)} stage={v.stage} "
            f"agency_id={v.agency_id} home_chunk={bool(v.home_chunk)}")
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
    def on_tick(self, dt: float):
        v = self.vessel
        if v.stage != 0: return
        pid = v.controlled_by
        if not pid: return

        v.telescope_targets_in_sight.clear()
        candidates = list(v._iter_planets_in_same_system()) or []
        if not candidates: return

        half_fov = max(0.0, float(v.telescope_fov_deg) * 0.5)
        rx, ry = v.position
        for obj in candidates:
            dx, dy = obj.position[0]-rx, obj.position[1]-ry
            dist_km = (dx*dx + dy*dy) ** 0.5
            if dist_km > float(v.telescope_range_km): continue
            to_target_deg = math.degrees(math.atan2(dy, dx))
            from utils import shortest_delta_deg
            delta = shortest_delta_deg(-v.rotation, to_target_deg)
            if abs(delta) <= half_fov:
                v.telescope_targets_in_sight.append(obj)