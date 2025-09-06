# payload_registry.py
from vessel_components import Components
from payload_behavior import CommsSatellite, SpaceTelescope, PayloadBehavior

# Map *int* payload ids to behavior classes
REGISTRY = {
    int(Components.COMMUNICATIONS_SATELLITE): CommsSatellite,
    int(Components.SPACE_TELESCOPE):          SpaceTelescope,
}

def make_payload_behavior(vessel):
    pid = int(getattr(vessel, "payload", 0))
    cls = REGISTRY.get(pid)
    if cls is None:
        print(f"[Factory] Unknown payload id {pid}; using base PayloadBehavior.")
        b = PayloadBehavior(vessel)
        b.payload_id = pid
        return b
    b = cls(vessel)
    b.payload_id = pid
    print(f"[Factory] Created {cls.__name__} for payload {pid}")
    return b
