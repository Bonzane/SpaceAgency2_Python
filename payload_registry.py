# payload_registry.py
from abc import ABC, abstractmethod
from vessel_components import Components
import math
from utils import shortest_delta_deg

class PayloadBehavior(ABC):
    def __init__(self, vessel): self.vessel = vessel
    def on_attach(self): pass
    def on_detach(self): pass
    def on_tick(self, dt: float): pass

class CommsSatellite(PayloadBehavior):
    def on_tick(self, dt: float):
        v = self.vessel
        if v.stage != 0: return
        agency = v.shared.agencies.get(v.agency_id)
        if not agency: return
        income = float(v.stats["income"]["base"])
        agency.distribute_money(income / v.shared.tickrate)

class SpaceTelescope(PayloadBehavior):
    def on_tick(self, dt: float):
        v = self.vessel
        if v.stage != 0: return
        half_fov = float(v.stats["telescope"]["fov_deg"]) * 0.5
        # optional: use max_rate_deg_s when steering toward v.telescope_rcs_angle
        # ... your existing target-in-FOV scan logic ...

REGISTRY = {
  Components.COMMUNICATIONS_SATELLITE: CommsSatellite,
  Components.SPACE_TELESCOPE: SpaceTelescope,
}

def make_payload_behavior(vessel):
    cls = REGISTRY.get(vessel.payload)
    return cls(vessel) if cls else None
