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

def _coerce_int_keys(d: Dict[Any, Any]) -> Dict[int, Any]:
    """Helper: ensure resource ids are ints (handles json string keys)."""
    if not isinstance(d, dict):
        return {}
    out: Dict[int, Any] = {}
    for k, v in d.items():
        try:
            out[int(k)] = v
        except Exception:
            # ignore keys that can't be coerced
            continue
    return out

def _notify_player_udp(shared, steam_id: int, notif_kind: int, message: str):
    """
    Fire-and-forget UDP NOTIFICATION to a single player by Steam ID.
    Works when called from sync code by scheduling onto the main loop.
    """
    udp = getattr(shared, "udp_server", None)
    if not udp:
        return

    try:
        steam_id = int(steam_id)
    except Exception:
        return

    coro = udp.notify_steam_ids([steam_id], notif_kind, message)
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(coro)
    except RuntimeError:
        # Not on the loop thread — use the server's main loop if you keep a ref to it
        main_loop = getattr(shared, "main_loop", None)
        if main_loop and main_loop.is_running():
            asyncio.run_coroutine_threadsafe(coro, main_loop)