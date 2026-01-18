from enum import IntEnum
from vessels import *
from utils import *


class Region(IntEnum):
    UNDEFINED = 0
    EARTH_CLOSE = 1
    EARTH_NEAR = 2
    EARTH_DISTANT = 3
    MOON_NEAR = 4
    SPACE = 5
    MARS_CLOSE = 6
    MARS_NEAR = 7
    MARS_DISTANT = 8
    VENUS_CLOSE = 9
    VENUS_NEAR = 10
    VENUS_DISTANT = 11
    MERCURY_CLOSE = 12
    MERCURY_NEAR = 13
    MERCURY_DISTANT = 14
    ASTEROID_BELT = 15
    JUPITER_CLOSE = 16
    JUPITER_NEAR = 17
    JUPITER_DISTANT = 18
    SATURN_CLOSE = 19
    SATURN_NEAR = 20
    SATURN_DISTANT = 21
    URANUS_CLOSE = 22
    URANUS_NEAR = 23
    URANUS_DISTANT = 24
    NEPTUNE_CLOSE = 25
    NEPTUNE_NEAR = 26
    NEPTUNE_DISTANT = 27
    TRANS_NEPTUNIAN = 28
    KUIPER_BELT = 29
    TERMINATION_SHOCK = 30
    HELIOSHEATH = 31
    HELIOPAUSE = 32
    INTRASTELLAR_WINDLESS = 33
    INNER_OORT_CLOUD = 34
    OUTER_OORT_CLOUD = 35


def maybe_update_vessel_region(shared, vessel, planet, new_region):
    old_region = vessel.region
    if new_region == old_region:
        return

    # Update current region
    vessel.region = new_region

    # First-time enter of a region?
    if new_region is not None and new_region not in vessel.regions_already_visited:
        vessel.regions_already_visited.append(new_region)

        # Suppress audio cues for the planet the vessel departed from (launch/last landed body)
        try:
            origin_pid = int(
                (getattr(vessel, "last_landed_body_id", None)
                 or getattr(vessel, "launchpad_planet_id", None)
                 or 0)
            )
        except Exception:
            origin_pid = 0
        target_pid = int(getattr(planet, "object_id", 0) or 0)
        if origin_pid and target_pid and origin_pid == target_pid:
            return

        send_audio_cue_to_controller(shared, vessel, new_region)
