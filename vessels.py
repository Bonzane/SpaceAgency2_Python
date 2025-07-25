from dataclasses import dataclass, field
import struct
from typing import List, Dict, Tuple, Any
from physics import G
from gameobjects import PhysicsObject, GameObject, ObjectType
from enum import Enum, IntEnum
import math
from packet_types import DataGramPacketType

class VesselControl(IntEnum):
    FORWARD_THRUST_ENGAGE = 0x00
    FORWARD_THRUST_DISENGAGE = 0x01
    REVERSE_THRUST_ENGAGE = 0x02
    REVERSE_THRUST_DISENGAGE = 0x03
    CCW_THRUST_ENGAGE = 0x04
    CCW_THRUST_DISENGAGE = 0x05
    CW_THRUST_ENGAGE = 0x06
    CW_THRUST_DISENGAGE = 0x07
    REQUEST_CONTROL = 0x08

class VesselState(IntEnum):
    FORWARD_THRUSTER_ON = 0x00
    REVERSE_THRUSTER_ON = 0x01
    CCW_THRUST_ON = 0x02
    CW_THRUST_ON = 0x03

@dataclass
class AttachedVesselComponent:
    id: int
    x: float
    y: float

@dataclass
class Vessel(PhysicsObject):
    components: List[AttachedVesselComponent] = field(default_factory=list)
    constructed_by: int = 0
    agency_id: int = 0
    shared: Any = field(default=None, repr=False, init=False)
    name : str = "Unnamed Vessel"
    mass : float = 0.0
    liquid_fuel_kg: float = 0.0
    liquid_fuel_capacity_kg: float = 0.0
    capable_forward_thrust: float = 0.0
    capable_reverse_thrust: float = 0.0
    object_type: ObjectType = ObjectType.BASIC_VESSEL
    center_of_mass: Tuple[float, float] = field(default=(0.0, 0.0), init=False)
    controlled_by: int = 0 
    control_state: Dict[VesselState, bool] = field(default_factory=lambda: {
        VesselState.FORWARD_THRUSTER_ON: False,
        VesselState.REVERSE_THRUSTER_ON: False,
        VesselState.CCW_THRUST_ON: False,
        VesselState.CW_THRUST_ON: False
    })
    rotation_velocity: float = 0.0  


    def __post_init__(self):
        print("VESSEL SPAWNED")

    def calculate_vessel_stats(self):
        #calculate initial states
        component_data_lookup = self.shared.component_data
        total_mass = 0.0
        weighted_x = 0.0
        weighted_y = 0.0
        for component in self.components:
            component_data = component_data_lookup.get(component.id, {})
            if component_data:
                mass = component_data.get("mass", 0)
                attributes = component_data.get("attributes", {})
                self.liquid_fuel_capacity_kg += attributes.get("liquid-fuel", 0)
                self.capable_forward_thrust += attributes.get("forward-thrust", 0)
                self.capable_reverse_thrust += attributes.get("reverse-thrust", 0)
                total_mass += mass
                weighted_x += component.x * mass
                weighted_y += component.y * mass

        self.liquid_fuel_kg = self.liquid_fuel_capacity_kg
        self.mass = total_mass + self.liquid_fuel_kg
        if total_mass > 0:
            self.center_of_mass = (weighted_x / total_mass, weighted_y / total_mass)

    def calculate_mass(self, component_data_lookup: Dict[int, Dict]) -> float:
        """Sum the mass of all components using the shared data."""
        return sum(component_data_lookup.get(comp.id, {}).get("mass", 0) for comp in self.components) + self.liquid_fuel_kg



    def validate_structure(self, component_data_lookup: Dict[int, Dict]) -> bool:
        """Basic structure validation — stub for now."""
        # Later, check snap points and logical connectivity
        return True
    
    def get_id(self) -> int:
        return self.object_id
    
    def do_control(self, control):
        match control:
            case VesselControl.FORWARD_THRUST_ENGAGE:
                self.control_state[VesselState.FORWARD_THRUSTER_ON] = True
            case VesselControl.FORWARD_THRUST_DISENGAGE:
                self.control_state[VesselState.FORWARD_THRUSTER_ON] = False
            case VesselControl.REVERSE_THRUST_ENGAGE:
                self.control_state[VesselState.REVERSE_THRUSTER_ON] = True
            case VesselControl.REVERSE_THRUST_DISENGAGE:
                self.control_state[VesselState.REVERSE_THRUSTER_ON] = False
            case VesselControl.CCW_THRUST_ENGAGE:
                self.control_state[VesselState.CCW_THRUST_ON] = True
            case VesselControl.CCW_THRUST_DISENGAGE:
                self.control_state[VesselState.CCW_THRUST_ON] = False
            case VesselControl.CW_THRUST_ENGAGE:
                self.control_state[VesselState.CW_THRUST_ON] = True
            case VesselControl.CW_THRUST_DISENGAGE:
                self.control_state[VesselState.CW_THRUST_ON] = False

    #Extended Physics
    def do_update(self, dt: float, acc: Tuple[float, float]):
        # 1. Apply Thrust before physics updates
        if self.control_state.get(VesselState.FORWARD_THRUSTER_ON, False):
            self.apply_forward_thrust(dt)
            print("thrusting forward")
        if self.control_state.get(VesselState.CCW_THRUST_ON, False):
            self.apply_ccw_thrust(dt)
            print("thrusting ccw")
        if self.control_state.get(VesselState.CW_THRUST_ON, False):
            self.apply_cw_thrust(dt)
            print("thrusting cw")

        self.rotation += self.rotation_velocity * dt

        #2. Call Base do update
        super().do_update(dt, acc)

        #3. Stream info to the players in the chunk
        chunkpacket = bytearray()
        chunkpacket.append(DataGramPacketType.VESSEL_STREAM)
        chunkpacket += struct.pack('<Q', self.object_id)
        chunkpacket += struct.pack('<B', int(self.control_state[VesselState.FORWARD_THRUSTER_ON]))
        chunkpacket += struct.pack('<B', int(self.control_state[VesselState.REVERSE_THRUSTER_ON]))
        chunkpacket += struct.pack('<B', int(self.control_state[VesselState.CCW_THRUST_ON]))
        chunkpacket += struct.pack('<B', int(self.control_state[VesselState.CW_THRUST_ON]))

        for player in self.shared.players.values():
            session = player.session
            if session and session.udp_port and session.alive:
                addr = (session.remote_ip, session.udp_port)
                self.shared.udp_server.transport.sendto(chunkpacket, addr)



    def apply_forward_thrust(self, dt: float):
        for component in self.components:
            component_data = self.shared.component_data.get(component.id, {})
            if not component_data:
                continue

            thrust_kN = component_data.get("attributes", {}).get("forward-thrust", 0)
            if thrust_kN > 0:
                local_point = (component.x, component.y)
                # Asset forward is -Y, world forward is +X, so compensate with -90°
                self.apply_thrust_at(local_point, direction_angle_deg=-90, thrust_kN=thrust_kN, dt=dt)

    def apply_ccw_thrust(self, dt: float):
        for component in self.components:
            component_data = self.shared.component_data.get(component.id, {})
            if not component_data:
                continue
            thrust_kN = component_data.get("attributes", {}).get("ccw-thrust", 0) * 0.1
            thrust_direction = component_data.get("attributes", {}).get("ccw-thrust-direction", 0) 
            if thrust_kN > 0:
                local_point = (component.x, component.y)
                # Asset forward is -Y, world forward is +X, so compensate with -90°
                self.apply_thrust_at(local_point, direction_angle_deg=-90 + thrust_direction, thrust_kN=thrust_kN, dt=dt)

    def apply_cw_thrust(self, dt: float):
        for component in self.components:
            component_data = self.shared.component_data.get(component.id, {})
            if not component_data:
                continue
            thrust_kN = component_data.get("attributes", {}).get("cw-thrust", 0) * 0.1
            thrust_direction = component_data.get("attributes", {}).get("cw-thrust-direction", 0)
            if thrust_kN > 0:
                local_point = (component.x, component.y)
                # Asset forward is -Y, world forward is +X, so compensate with -90°
                self.apply_thrust_at(local_point, direction_angle_deg=-90 + thrust_direction, thrust_kN=thrust_kN, dt=dt)


    def apply_thrust_at(self, local_point: Tuple[float, float], direction_angle_deg: float, thrust_kN: float, dt: float):
        if thrust_kN <= 0 or self.mass <= 0:
            return

        scaled_dt = dt / self.shared.gamespeed
        thrust_N = thrust_kN * 1000

        # Convert thrust direction to world space
        angle_rad = math.radians(self.rotation + direction_angle_deg)
        fx = thrust_N * math.cos(angle_rad)
        fy = thrust_N * math.sin(angle_rad)

        # Step 1: Apply linear force
        dvx = (fx / self.mass) * scaled_dt
        dvy = (fy / self.mass) * scaled_dt
        vx, vy = self.velocity
        self.velocity = (vx - dvy, vy - dvx)

        # Step 2: Calculate torque from offset
        local_dx = local_point[0] - self.center_of_mass[0]
        local_dy = local_point[1] - self.center_of_mass[1]

        # Rotate offset to world space using vessel rotation
        rot_rad = math.radians(self.rotation)
        cos_theta = math.cos(rot_rad)
        sin_theta = math.sin(rot_rad)

        rel_x = local_dx * cos_theta - local_dy * sin_theta
        rel_y = local_dx * sin_theta + local_dy * cos_theta

        # Torque = r × F (2D cross product)
        torque = rel_x * fy - rel_y * fx
        r_squared = rel_x ** 2 + rel_y ** 2

        if r_squared > 0:
            moment_of_inertia = self.mass * r_squared  # Approximate
            angular_acceleration = torque / moment_of_inertia
            self.rotation_velocity -= math.degrees(angular_acceleration * scaled_dt)



    def apply_thrust(self, dt: float, thrust_kN: float, angle_offset: float = 0.0):
        scaled_dt = dt / self.shared.gamespeed
        print(f"Over seconds: {scaled_dt}")
        if thrust_kN <= 0 or self.mass <= 0:
            return

        # Convert kN to N (1 kN = 1000 N)
        thrust_N = thrust_kN * 1000
        print(f"applying {thrust_N} units of thrust")
        # Compute acceleration (a = F / m)
        acceleration = thrust_N / self.mass

        # Determine global thrust direction in radians
        angle_deg = self.rotation + angle_offset
        angle_rad = math.radians(angle_deg)

        # Calculate delta velocity
        dvx = acceleration * math.cos(angle_rad) * scaled_dt
        dvy = acceleration * math.sin(angle_rad) * scaled_dt

        # Apply to velocity
        vx, vy = self.velocity
        self.velocity = (vx + dvx, vy + dvy)

def construct_vessel_from_request(shared, player, vessel_request_data) -> Vessel:
    #GET A REFERENCE TO THE COMPONENT DATA
    component_data_lookup = shared.component_data
    components = []
    total_cost = 0
    planet_id = vessel_request_data.get("planet", 2)
    print(f"Constructing vessel for planet ID: {planet_id}")
    launchpad_data = vessel_request_data.get("launchpad_data", {})
    vessel_name = vessel_request_data.get("name", "Unnamed Vessel")

    for component in vessel_request_data["vessel_data"]:
        comp_id = int(component["id"])
        placement_x = int(component["x"]) - 2500
        placement_y = int(component["y"])  - 2500 
        
        component_definition = component_data_lookup.get(comp_id)
        if component_definition is None:
            raise ValueError(f"Invalid component ID: {comp_id}")
        
        total_cost += component_definition.get("cost", 0)
        components.append(AttachedVesselComponent(id=comp_id, x=placement_x, y=placement_y))
    if player.money < total_cost:
        raise ValueError(f"Insufficient funds: cost={total_cost}, player has={player.money}")
    
    #Subtract money
    player.money -= total_cost
    print("Creating vessel")
    #Create the vessel
    center_component = components[0]
    vessel = Vessel(
        object_type=ObjectType.BASIC_VESSEL,
        components=components,
        constructed_by=player.steamID,
        agency_id=player.agency_id,
        position=(152_000_000.0, 0.0),
        velocity=(0, 0),
        mass=1000.0
    )
    vessel.shared=shared
    vessel.name = vessel_name

    # Add vessel to its agency
    agency = shared.agencies.get(player.agency_id)
    if agency is not None:
        agency.vessels.append(vessel)
        print(f"✅ Vessel {vessel.object_id} added to Agency {agency.id64}")
    else:
        print(f"⚠️ No agency found with ID {player.agency_id}, vessel not tracked.")

    chunk_key = (player.galaxy, player.system)
    chunk = shared.chunk_manager.loaded_chunks.get(chunk_key)

    if chunk is not None:
        chunk.add_object(vessel)
        print(f"Vessel {vessel.object_id} added to chunk {chunk_key}")
    else:
        print(f"⚠️ No chunk found for galaxy/system {chunk_key}, vessel not added to chunk.")



    return vessel