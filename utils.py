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
