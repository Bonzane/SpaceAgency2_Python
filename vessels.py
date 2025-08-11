from dataclasses import dataclass, field
import struct
from typing import List, Dict, Tuple, Any, Union, Optional
from physics import G
from gameobjects import PhysicsObject, GameObject, ObjectType
from enum import Enum, IntEnum
import math
from packet_types import DataGramPacketType
from vessel_components import Components

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
    DETACH_STAGE = 0x09


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
    dry_mass : float = 0.0
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
    launchpad_planet_id: int = None
    launchpad_angle_offset: float = 0.0
    home_planet: Any = None  # Reference to the planet where the vessel was launched
    home_chunk: Any = None
    altitude = 0.0 # Altitude above the home planet's surface
    landed = True
    landed_angle_offset: float = 0.0
    strongest_gravity_force: float = 0.0
    strongest_gravity_source: Optional[GameObject] = None
    altitude_delta: float = 0.0
    last_forward_thrust_kN: float = 0.0
    regions_already_visited: List[int] = field(default_factory=list)
    region: int = 0  # Region ID for the vessel, used for proximity cues
    stage: int = 0
    payload: int = 0
    lifetime_revenue: int = 0




    def __post_init__(self):
        print("VESSEL SPAWNED")

    def calculate_vessel_stats(self):
        #calculate initial states
        component_data_lookup = self.shared.component_data
        total_mass = 0.0
        self.dry_mass = 0.0
        self.liquid_fuel_capacity_kg = 0.0
        self.liquid_fuel_kg = 0.0
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


    def do_payload_mechanics(self, dt: float):
        _payload_data = self.shared.component_data.get(self.payload, {})
        _payload_attributes = _payload_data.get("attributes", {})
        _payload_income_per_second = _payload_attributes.get("payload-base-income", 0)
        _agency = self.shared.agencies.get(self.agency_id)
        _tickrate = self.shared.tickrate
        # Make sure it's deployed
        if not self.stage == 0:
            return
        
        match self.payload:
            case Components.COMMUNICATIONS_SATELLITE:
                pass

        #Give income to the agency
        _agency.distribute_money(_payload_income_per_second / _tickrate)



    # Extended Physics
    def do_update(self, dt: float, acc: Tuple[float, float]):
        self.last_forward_thrust_kN = 0.0
        # 1. Apply Thrust before physics updates
        if self.control_state.get(VesselState.FORWARD_THRUSTER_ON, False):
            self.apply_forward_thrust(dt)
        if self.control_state.get(VesselState.CCW_THRUST_ON, False):
            self.apply_ccw_thrust(dt)
        if self.control_state.get(VesselState.CW_THRUST_ON, False):
            self.apply_cw_thrust(dt)
        if self.control_state.get(VesselState.REVERSE_THRUSTER_ON, False):
            self.apply_reverse_thrust(dt)

        # Check transition condition: should we launch?
        if self.landed:
            if self.should_unland():
                self.unland()
            else:
                self.stay_landed()
        else:
            self.update_altitude(dt)

        # 2. Apply rotation
        self.rotation += self.rotation_velocity * dt

        if not self.landed:
            super().do_update(dt, acc)

            self.ground_influence(dt)

        #3 - Do Payload Mechanics
        self.do_payload_mechanics(dt)

        # Clamp to speed of light (FOR NOW!)
        vx, vy = self.velocity
        speed = math.hypot(vx, vy)
        C_KM_S = 299_792.458
        if speed > C_KM_S:
            scale = C_KM_S / speed
            self.velocity = (vx * scale, vy * scale)

        # 4. Stream vessel data to clients
        chunkpacket = bytearray()
        chunkpacket.append(DataGramPacketType.VESSEL_STREAM)
        chunkpacket += struct.pack('<Q', self.object_id)
        chunkpacket += struct.pack('<B', int(self.control_state[VesselState.FORWARD_THRUSTER_ON]))
        chunkpacket += struct.pack('<B', int(self.control_state[VesselState.REVERSE_THRUSTER_ON]))
        chunkpacket += struct.pack('<B', int(self.control_state[VesselState.CCW_THRUST_ON]))
        chunkpacket += struct.pack('<B', int(self.control_state[VesselState.CW_THRUST_ON]))
        chunkpacket += struct.pack('<f', self.altitude)
        chunkpacket += struct.pack('<Q', self.home_planet.object_id)
        chunkpacket += struct.pack('<f', self.home_planet.atmosphere_km)
        chunkpacket += struct.pack('<Q',getattr(self.strongest_gravity_source, "object_id", 0))
        force = self.strongest_gravity_force
        if not isinstance(force, (int, float)) or math.isnan(force) or math.isinf(force):
            force = 0.0
        chunkpacket += struct.pack('<f', force)
        chunkpacket += struct.pack('<B', self.landed)


        for player in self.shared.players.values():
            session = player.session
            if session and session.udp_port and session.alive:
                addr = (session.remote_ip, session.udp_port)
                self.shared.udp_server.transport.sendto(chunkpacket, addr)

        #print(f"[DEBUG] Vessel {self.object_id} Velocity: vx={self.velocity[0]:.2f}, vy={self.velocity[1]:.2f}, Altitude: {self.altitude:.2f}")




        #
        #elif(self.flight_mode == FlightMode.ON_LAUNCHPAD):
            # On the launchpad, the vessel locks its position to its launchpad. When thrust is applied, 
            # it will first copy the velocity of the planet, then apply the thrust.
        #    self.position = (
        #        self.home_planet.position[0] + self.home_planet.radius_km * math.cos(math.radians(-self.launchpad_angle_offset - self.home_planet.rotation)),
        #        self.home_planet.position[1] + self.home_planet.radius_km * math.sin(math.radians(-self.launchpad_angle_offset - self.home_planet.rotation))
        #    )

        #    self.rotation = self.home_planet.rotation + self.launchpad_angle_offset
        #    self.velocity = (0.0, 0.0)
        #    self.rotation_velocity = 0.0

    def should_unland(self) -> bool:
        if not self.control_state.get(VesselState.FORWARD_THRUSTER_ON, False):
            return False
        return True

    def should_land(self) -> bool:
        dx = self.position[0] - self.home_planet.position[0]
        dy = self.position[1] - self.home_planet.position[1]
        dist = math.hypot(dx, dy)
        vx, vy = self.velocity
        speed = math.hypot(vx, vy)
        return dist <= self.home_planet.radius_km and speed < 0.05 and self.altitude <= 1.0

    def update_altitude(self, dt: float):
        if self.landed or not self.home_planet:
            return

        ALT_GAIN = 100.0
        ALT_EXP  = 1.0

        acc_proxy = self.last_forward_thrust_kN / max(self.mass, 1.0)

        atm_height = max(1e-6, float(self.home_planet.atmosphere_km))
        alt_norm   = max(0.0, min(1.0, self.altitude / atm_height))
        BASE = 0.1  # 20% of full effect at ground
        atmos_factor = BASE + (1.0 - BASE) * (alt_norm ** ALT_EXP)

        self.altitude_delta = ALT_GAIN * acc_proxy * atmos_factor
        self.altitude = min(
            self.altitude + self.altitude_delta * dt,
            self.home_planet.atmosphere_km
        )
            
    def _clamp01(self, x: float) -> float:
        return 0.0 if x < 0.0 else (1.0 if x > 1.0 else x)

    def _smoothstep(self, a: float, b: float, x: float) -> float:
        # 0 at x<=a, 1 at x>=b, C1 continuous in between
        if a == b:
            return 1.0 if x >= b else 0.0
        t = self._clamp01((x - a) / (b - a))
        return t * t * (3 - 2 * t)

    def ground_influence(self, dt: float):
        if not self.home_planet or dt <= 0.0:
            return

        atm = max(1e-6, float(self.home_planet.atmosphere_km))
        n = self._clamp01(self.altitude / atm)  # 0..1

        # --- Velocity matching ---
        VEL_TAU_GROUND = 0.15
        VEL_TAU_TOP    = 8.0
        VEL_SHAPE      = 0.8
        tau_v = VEL_TAU_GROUND + (VEL_TAU_TOP - VEL_TAU_GROUND) * (n ** VEL_SHAPE)

        # Fade velocity lock to ZERO near the top of the atmosphere.
        # Below ~90% it's fully active; between 90%..100% it eases out to 0.
        VEL_OFF_START = 0.90
        vel_gate = 1.0 - self._smoothstep(VEL_OFF_START, 1.0, n)  # 1→0 as n goes 0.90→1.0

        if vel_gate > 0.0:
            beta = (1.0 - math.exp(-dt / tau_v)) * vel_gate * 0.1  # dt-safe blend, then gated
            pvx, pvy = self.home_planet.velocity
            vx, vy   = self.velocity
            self.velocity = (vx + (pvx - vx) * beta, vy + (pvy - vy) * beta)

        # --- Position glue (unchanged idea; only low altitude) ---
        POS_FADE_END = 0.35
        pos_gate = self._smoothstep(0.0, POS_FADE_END, max(0.0, 1.0 - n))  # 1→0 as n goes 0→0.35

        if pos_gate > 0.0:
            POS_TAU_GROUND = 0.10
            POS_TAU_END    = 1.50
            POS_SHAPE      = 1.0
            tau_p = POS_TAU_GROUND + (POS_TAU_END - POS_TAU_GROUND) * (n ** POS_SHAPE)
            alpha = (1.0 - math.exp(-dt / tau_p)) * pos_gate * 0.1

            R = float(self.home_planet.radius_km)
            ang_deg = self.landed_angle_offset + self.home_planet.rotation
            ang = math.radians(ang_deg)
            px, py = self.home_planet.position
            tx = px + R * math.cos(-ang)
            ty = py + R * math.sin(-ang)

            x, y = self.position
            self.position = (x + (tx - x) * alpha, y + (ty - y) * alpha)





    def land(self):
        self.landed = True
        self.altitude = 0.0
        self.rotation_velocity = 0.0
        self.velocity = self.home_planet.velocity

        # Calculate angle from planet center to vessel at time of landing
        dx = self.position[0] - self.home_planet.position[0]
        dy = self.position[1] - self.home_planet.position[1]
        angle = math.degrees(math.atan2(dy, dx))

        # Store angle offset for surface lock
        self.landed_angle_offset = angle - self.home_planet.rotation

        # Reposition exactly on surface
        radius_km = self.home_planet.radius_km
        angle_rad = math.radians(self.landed_angle_offset + self.home_planet.rotation)
        self.position = (
            self.home_planet.position[0] + radius_km * math.cos(angle_rad),
            self.home_planet.position[1] + radius_km * math.sin(angle_rad)
        )

        # Match rotation to surface angle
        self.rotation = self.home_planet.rotation + self.landed_angle_offset

    def stay_landed(self):
        self.velocity = self.home_planet.velocity
        self.rotation_velocity = 0.0
        self.altitude = 0.0

        radius_km = self.home_planet.radius_km
        angle_deg = self.landed_angle_offset + self.home_planet.rotation
        angle_rad = math.radians(angle_deg)

        self.position = (
            self.home_planet.position[0] + radius_km * math.cos(-angle_rad),
            self.home_planet.position[1] + radius_km * math.sin(-angle_rad)
        )
        self.rotation = angle_deg



    def unland(self):
        self.landed = False
        self.altitude = 0.1  # start just above the ground



    def apply_forward_thrust(self, dt: float):
        total_kN = 0.0
        mult = getattr(self.shared, "global_thrust_multiplier", 1.0)

        for component in self.components:
            cd = self.shared.component_data.get(component.id, {})
            if not cd:
                continue

            kN = cd.get("attributes", {}).get("forward-thrust", 0.0)
            if kN <= 0:
                continue

            total_kN += kN * mult  # accumulate effective forward thrust

            local_point = (component.x, component.y)
            self.apply_thrust_at(local_point, direction_angle_deg=-90, thrust_kN=kN, dt=dt)

        self.last_forward_thrust_kN += total_kN

                


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

    def apply_reverse_thrust(self, dt: float):
        for component in self.components:
            component_data = self.shared.component_data.get(component.id, {})
            if not component_data:
                continue
            thrust_kN = component_data.get("attributes", {}).get("reverse-thrust", 0)
            thrust_direction = component_data.get("attributes", {}).get("reverse-thrust-direction", 0) + 180
            if thrust_kN > 0:
                local_point = (component.x, component.y)
                # Asset forward is -Y, world forward is +X, so compensate with -90°
                self.apply_thrust_at(local_point, direction_angle_deg=-90 + thrust_direction, thrust_kN=thrust_kN, dt=dt)


    def apply_thrust_at(self, local_point: Tuple[float, float], direction_angle_deg: float, thrust_kN: float, dt: float):
        if thrust_kN <= 0 or self.mass <= 0:
            return
        scaled_dt = dt / self.shared.gamespeed
        thrust = thrust_kN * 1000 * self.shared.global_thrust_multiplier

        angle_rad = math.radians(self.rotation + direction_angle_deg)
        fx = thrust* math.cos(angle_rad)
        fy = thrust * math.sin(angle_rad )

        dvx = (fx / self.mass) * scaled_dt
        dvy = (fy / self.mass) * scaled_dt
        vx, vy = self.velocity

        self.velocity = (vx - dvy, vy - dvx)

        local_dx = local_point[0] - self.center_of_mass[0]
        local_dy = local_point[1] - self.center_of_mass[1]

        rot_rad = math.radians(self.rotation)
        cos_theta = math.cos(rot_rad)
        sin_theta = math.sin(rot_rad)

        rel_x = local_dx * cos_theta - local_dy * sin_theta
        rel_y = local_dx * sin_theta + local_dy * cos_theta

        torque = rel_x * fy - rel_y * fx
        r_squared = rel_x ** 2 + rel_y ** 2

        if r_squared > 0:
            moment_of_inertia = self.mass * r_squared
            angular_acceleration = torque / moment_of_inertia
            self.rotation_velocity += math.degrees(angular_acceleration * scaled_dt)


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
    launchpad_building_type= launchpad_data.get("type", 2)
    launchpad_angle = launchpad_data.get("position_angle", 0)
    vessel_name = vessel_request_data.get("name", "Unnamed Vessel")
    highest_stage = 0


    #TODO: CALCULATE THE STAGE OF EACH COMPONENT TOO
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
    vessel.launchpad_planet_id = planet_id

    # Add vessel to its agency
    agency = shared.agencies.get(player.agency_id)
    if agency is not None:
        agency.vessels.append(vessel)
        print(f"✅ Vessel {vessel.object_id} added to Agency {agency.id64}")
    else:
        print(f"⚠️ No agency found with ID {player.agency_id}, vessel not tracked.")

    chunk_key = (player.galaxy, player.system)
    chunk = shared.chunk_manager.loaded_chunks.get(chunk_key)
    vessel.home_chunk = chunk
    vessel.home_planet = chunk.get_object_by_id(planet_id)
    #--Initialize launchpad lock--
    vessel.landed = True
    vessel.altitude = 0.0
    vessel.landed_angle_offset = float(launchpad_angle)
    if vessel.home_planet:
        R = float(vessel.home_planet.radius_km)
        # world angle = planet rotation + pad's local angle
        world_angle_deg = vessel.home_planet.rotation + vessel.landed_angle_offset
        ang = math.radians(world_angle_deg)
        cx, cy = vessel.home_planet.position

        vessel.position = (cx + R * math.cos(ang), cy + R * math.sin(ang))
        vessel.rotation = world_angle_deg              # face outward from the center
        vessel.rotation_velocity = 0.0
        vessel.velocity = vessel.home_planet.velocity  # move with the planet


    # --- end launchpad lock init ---
    if chunk is not None:
        chunk.add_object(vessel)
        print(f"Vessel {vessel.object_id} added to chunk {chunk_key}")
    else:
        print(f"⚠️ No chunk found for galaxy/system {chunk_key}, vessel not added to chunk.")



    return vessel