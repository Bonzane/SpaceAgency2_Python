from vessels import *
from packet_types import *


def get_controller_session(shared, vessel):
    controller_id = getattr(vessel, "controlled_by", 0)
    if not controller_id:
        return None
    # Sessions live on the TCP control server
    for s in shared.tcp_server.sessions:
        if not s.alive:
            continue
        if s.steam_id == controller_id and getattr(s, "udp_port", None):
            return s
    return None

def send_audio_cue_to_controller(shared, vessel, region_id: int) -> bool:
    session = get_controller_session(shared, vessel)
    if not session:
        return False
    pkt = bytearray()
    pkt.append(DataGramPacketType.REGION_CUE)  # define this enum value
    pkt += struct.pack('<QI', vessel.object_id, int(region_id))
    addr = (session.remote_ip, session.udp_port)
    shared.udp_server.transport.sendto(pkt, addr)
    return True

def ambient_temp_simple(distance_km: float) -> float:
    base_temp = 2.7  # K
    scale_distance = 1_000_000  # 1 million km
    heat_at_1Mkm = 3_300  # tuned so Mercury ~ 440K
    dist_units = max(distance_km / scale_distance, 1.0)
    return base_temp + heat_at_1Mkm / (dist_units ** 0.5)


def wrap_deg(a: float) -> float:
    """0..360 wrap."""
    return (a % 360.0 + 360.0) % 360.0

def shortest_delta_deg(current: float, target: float) -> float:
    """
    Signed shortest delta (target - current) in degrees, in [-180, 180].
    """
    c = wrap_deg(current)
    t = wrap_deg(target)
    d = ((t - c + 180.0) % 360.0) - 180.0
    return d