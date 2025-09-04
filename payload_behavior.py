# payload_behavior.py
from abc import ABC, abstractmethod

class PayloadBehavior(ABC):
    """One instance per vessel (holds per-vessel state)."""

    def __init__(self, vessel):
        self.vessel = vessel

    def on_attach(self): pass                 # when payload becomes current (stage==0)
    def on_detach(self): pass                 # when staging drops it
    def on_event(self, event, **data): pass   # generic event hook
    def on_tick(self, dt: float): pass        # per-tick behavior

# --- concrete strategies ---

class CommsSatellite(PayloadBehavior):
    def on_tick(self, dt: float):
        if self.vessel.stage != 0: return
        shared = self.vessel.shared
        agency = shared.agencies.get(self.vessel.agency_id)
        if not agency: return
        data = shared.component_data.get(self.vessel.payload, {})
        base = float(data.get("attributes", {}).get("payload_base_income", 0.0))
        bonus = float(agency.attributes.get("satellite_bonus_income", 0.0))
        agency.distribute_money((base + bonus) / shared.tickrate)

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
            delta = shortest_delta_deg(-v.rotation, to_target_deg)
            if abs(delta) <= half_fov:
                v.telescope_targets_in_sight.append(obj)

        # existing UDP packet send can remain as-is
        # (or emit an event and let a networking system send)
