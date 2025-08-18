import asyncio
from dataclasses import dataclass, field
import struct
from typing import List, Dict, Tuple, Any, Union, Optional
from physics import G
from gameobjects import PhysicsObject, GameObject, ObjectType
from enum import Enum, IntEnum
import math
from packet_types import DataGramPacketType
from vessel_components import Components
from utils import ambient_temp_simple, shortest_delta_deg, wrap_deg

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
    DEPLOY_STAGE = 0x09
    SET_TELESCOPE_TARGET_ANGLE = 0x0A

class VesselState(IntEnum):
    FORWARD_THRUSTER_ON = 0x00
    REVERSE_THRUSTER_ON = 0x01
    CCW_THRUST_ON = 0x02
    CW_THRUST_ON = 0x03

class Systems(IntEnum):
    UNDEFINED = 0
    THERMAL_REGULATOR = 1

@dataclass
class AttachedVesselComponent:
    id: int
    x: float
    y: float
    paint1 : int
    paint2 : int

@dataclass
class ElectricalSystem:
    type: Systems
    amount: float = 0.0
    power_draw: float = 0.0
    active: bool = True


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
    power_capacity: float = 0.0
    solar_power: float = 0.0
    power: float = 0.0
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
    num_stages: int = 0
    payload: int = 0
    lifetime_revenue: int = 0
    maximum_operating_tempterature_c: float = 100.0
    current_temperature_c: float = 20.0
    thermal_resistance: float = 100
    deployment_ready: bool = False
    #---Telescopes---
    has_telescope_rcs: bool = False
    telescope_rcs_angle: float = 0.0
    telescope_targets: List[GameObject] = field(default_factory=list, repr=False)
    telescope_targets_in_sight: List[GameObject] = field(default_factory=list, repr=False)

    fuel_by_stage: Dict[int, float] = field(default_factory=dict, repr=False)
    capacity_by_stage: Dict[int, float] = field(default_factory=dict, repr=False)
    power_by_stage: Dict[int, float] = field(default_factory=dict, repr=False)
    power_capacity_by_stage: Dict[int, float] = field(default_factory=dict, repr=False)

    #--- Electrical Systems ---
    systems: Dict[Systems, ElectricalSystem] = field(default_factory=dict, repr=False)




    def __post_init__(self):
        print("VESSEL SPAWNED")

    def calculate_vessel_stats(self):
        component_data_lookup = self.shared.component_data

        # recompute capacities and thrust caps per stage
        self.capacity_by_stage.clear()
        self.power_capacity_by_stage.clear()
        stage_forward = {}
        stage_reverse = {}

        total_mass = 0.0
        self.dry_mass = 0.0
        weighted_x = 0.0
        weighted_y = 0.0
        self.solar_power = 0.0

        self.systems.clear()

        # reset thermal resistance
        base_tau = 100.0             
        attached_tau_bonus = 0.0 


        # if stage not yet set, infer from components
        if not self.components:
            self.mass = 0.0
            self.center_of_mass = (0.0, 0.0)
            return
        inferred_stage = max(getattr(c, "stage", 0) for c in self.components)
        if not isinstance(self.stage, int):
            self.stage = inferred_stage
        else:
            self.stage = max(self.stage, inferred_stage) if self.stage < 0 else self.stage

        for comp in self.components:
            cd = component_data_lookup.get(comp.id, {})
            if not cd:
                continue
            mass = cd.get("mass", 0.0)
            attrs = cd.get("attributes", {})
            st = int(getattr(comp, "stage", 0))


            # per-stage fuel capacity
            cap = float(attrs.get("liquid-fuel", 0.0))
            power_cap = float(attrs.get("power-capacity", 0.0))
            if cap > 0:
                self.capacity_by_stage[st] = self.capacity_by_stage.get(st, 0.0) + cap
            if power_cap > 0:
                self.power_capacity_by_stage[st] = self.power_capacity_by_stage.get(st, 0.0) + power_cap
            self.solar_power += float(attrs.get("solar-power", 0.0))

            # per-stage thrust capability
            stage_forward[st] = stage_forward.get(st, 0.0) + float(attrs.get("forward-thrust", 0.0))
            stage_reverse[st] = stage_reverse.get(st, 0.0) + float(attrs.get("reverse-thrust", 0.0))

            total_mass += mass
            weighted_x += comp.x * mass
            weighted_y += comp.y * mass

            if(attrs.get("telescope-rcs", False)):
                self.has_telescope_rcs = True

            if(comp.id == Components.SPACE_TELESCOPE):
                game = self.shared.game
                self.telescope_targets = [game.sun, game.luna, game.mars, game.venus]

            #FOR EVERY ATTACHED COMPONENT:
            if st <= self.stage:
                self.add_system(
                    Systems.THERMAL_REGULATOR,
                    float(attrs.get("thermal-regulation", 0.0)),
                    float(attrs.get("thermal-regulation-power-draw", 0.0)),
                    True
                )

                attached_tau_bonus += float(attrs.get("thermal-resistance", 0.0))

        # set thermal resistance based on attached components
        self.thermal_resistance = max(1e-3, base_tau + attached_tau_bonus)

        # initialize fuel pools if empty (fill each stage to capacity)
        if not self.fuel_by_stage:
            for st, cap in self.capacity_by_stage.items():
                self.fuel_by_stage[st] = cap

        # initialize power stores if empty (start full per stage)
        if not self.power_by_stage:
            for st, cap in self.power_capacity_by_stage.items():
                self.power_by_stage[st] = cap
        else:
            # ensure keys exist & clamp to capacity
            for st, cap in self.power_capacity_by_stage.items():
                self.power_by_stage[st] = min(self.power_by_stage.get(st, 0.0), cap)

        # expose "capable_*" for the current stage only (so UI/logic stays intuitive)
        self.capable_forward_thrust = float(stage_forward.get(self.stage, 0.0))
        self.capable_reverse_thrust = float(stage_reverse.get(self.stage, 0.0))

        # mass = dry components + fuel that is still attached (stages ≤ current stage)
        attached_fuel = sum(v for s, v in self.fuel_by_stage.items() if s <= self.stage)
        self.mass = total_mass + attached_fuel

        if total_mass > 0.0:
            self.center_of_mass = (weighted_x / total_mass, weighted_y / total_mass)

        # keep the legacy flat numbers reflecting the CURRENT stage (for telemetry/packets)
        self.liquid_fuel_capacity_kg = self._current_stage_capacity()
        self.liquid_fuel_kg = self._current_stage_fuel()

        self.power_capacity = self._attached_power_capacity()
        self.power = self._attached_power()


    def calculate_mass(self, component_data_lookup: Dict[int, Dict]) -> float:
        dry = sum(component_data_lookup.get(comp.id, {}).get("mass", 0.0) for comp in self.components)
        attached_fuel = sum(v for s, v in self.fuel_by_stage.items() if s <= self.stage)
        return dry + attached_fuel


    def add_system(self, sys_type: Systems, amount: float, draw: float, active: bool = True):
        if amount <= 0.0:
            return  

        existing = self.systems.get(sys_type)
        if existing:
            existing.amount += amount
            existing.power_draw += draw
            existing.active = existing.active or active
        else:
            self.systems[sys_type] = ElectricalSystem(
                type=sys_type,
                amount=amount,
                power_draw=draw,
                active=active,
            )





    def validate_structure(self, component_data_lookup: Dict[int, Dict]) -> bool:
        """Basic structure validation — stub for now."""
        # Later, check snap points and logical connectivity
        return True
    
    def get_id(self) -> int:
        return self.object_id


    # --- Power (pooled across attached stages) ---

    def _attached_power_capacity(self) -> float:
        """Sum of capacities for all attached stages (s <= current)."""
        return sum(cap for s, cap in self.power_capacity_by_stage.items() if s <= self.stage)

    def _attached_power(self) -> float:
        """Sum of charge for all attached stages (s <= current)."""
        return sum(p for s, p in self.power_by_stage.items() if s <= self.stage)

    def _draw_power(self, amount: float) -> bool:
        """
        Consume from the attached pool, drawing stage-by-stage starting
        with the CURRENT stage, then descending (stage-1, stage-2, ...).
        """
        if amount <= 0:
            return True

        remaining = amount
        # consume from current stage down to stage 0
        for s in range(int(self.stage), -1, -1):
            cur = self.power_by_stage.get(s, 0.0)
            if cur <= 0.0:
                continue
            take = cur if cur <= remaining else remaining
            self.power_by_stage[s] = cur - take
            remaining -= take
            if remaining <= 0.0:
                break

        if remaining > 0.0:
            # not enough total power
            # (undo is optional; we keep partial draw for realism)
            self.power = self._attached_power()
            return False

        self.power = self._attached_power()
        return True

    def _charge_power(self, amount: float):
        """
        Add charge into the attached pool. Default strategy:
        fill the CURRENT stage first, then descend to lower stages.
        """
        if amount <= 0:
            return
        remaining = amount
        for s in range(int(self.stage), -1, -1):
            cap = self.power_capacity_by_stage.get(s, 0.0)
            cur = self.power_by_stage.get(s, 0.0)
            room = max(0.0, cap - cur)
            if room <= 0.0:
                continue
            put = room if room <= remaining else remaining
            self.power_by_stage[s] = cur + put
            remaining -= put
            if remaining <= 0.0:
                break
        self.power = self._attached_power()

    
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
            case VesselControl.DEPLOY_STAGE:
                self.deploy_stage()

    def _current_stage_capacity(self) -> float:
        return float(self.capacity_by_stage.get(self.stage, 0.0))

    def _current_stage_fuel(self) -> float:
        return float(self.fuel_by_stage.get(self.stage, 0.0))

    def _set_current_stage_fuel(self, value: float):
        cap = self._current_stage_capacity()
        self.fuel_by_stage[self.stage] = max(0.0, min(value, cap))

    def deploy_stage(self):
        if self.stage <= 0:
            print("Can not deploy - already deployed!")
            return
        if not self.deployment_ready:
            print("Can not deploy - not ready!")
            return

        prev_stage = self.stage
        self.stage -= 1
        self.deployment_ready = False

        # Remove components that no longer belong
        self.drop_components()

        self.control_state[VesselState.CCW_THRUST_ON] = False
        self.control_state[VesselState.CW_THRUST_ON] = False
        self.control_state[VesselState.REVERSE_THRUSTER_ON] = False
        self.control_state[VesselState.FORWARD_THRUSTER_ON] = False

        print(f"✅ Vessel {self.object_id} staged: {prev_stage} → {self.stage} (components={len(self.components)})")

        tcp = self.shared.tcp_server
        if tcp is not None:
            asyncio.create_task(tcp.broadcast_force_resolve(self))


    def drop_components(self):
        kept = []
        for comp in self.components:
            if int(getattr(comp, "stage", 0)) <= self.stage:
                kept.append(comp)
        self.components = kept

        # prune per-stage tanks that are no longer attached
        self.fuel_by_stage = {s: v for s, v in self.fuel_by_stage.items() if s <= self.stage}
        self.capacity_by_stage = {s: v for s, v in self.capacity_by_stage.items() if s <= self.stage}

        self.calculate_vessel_stats()


    def do_payload_mechanics(self, dt: float):
        _payload_data = self.shared.component_data.get(self.payload, {})
        _payload_attributes = _payload_data.get("attributes", {})
        _payload_income_per_second = _payload_attributes.get("payload_base_income", 0)
        _agency = self.shared.agencies.get(self.agency_id)
        _tickrate = self.shared.tickrate
        # Make sure it's deployed
        if not self.stage == 0:
            return
        
        match self.payload:
            case Components.COMMUNICATIONS_SATELLITE:
                _payload_income_per_second += _agency.attributes.get("satellite_bonus_income", 0)
            case Components.SPACE_TELESCOPE:
                controlling_player_id = self.controlled_by
                if controlling_player_id == 0:
                    return
                controlling_player = self.shared.players.get(controlling_player_id, None)
                if not controlling_player:
                    return
                sess = getattr(controlling_player, "session", None)
                if not sess or not sess.alive:
                    return
                self.telescope_targets_in_sight.clear()
                for obj in self.telescope_targets:
                    if obj is None:
                        continue
                    # Direction from vessel to target
                    dx = obj.position[0] - self.position[0]
                    dy = obj.position[1] - self.position[1]
                    if dx == 0.0 and dy == 0.0:
                        continue  # same position; undefined direction

                    to_target_deg = math.degrees(math.atan2(dy, dx))
                    # Smallest signed delta (uses your helper)
                    delta = shortest_delta_deg(-self.rotation, to_target_deg)

                    if abs(delta) < 30.0:  # strictly less than 30°
                        self.telescope_targets_in_sight.append(obj)

                if sess and sess.alive and getattr(sess, "udp_port", None):
                    addr = (sess.remote_ip, sess.udp_port)
                    packet = self.shared.udp_server.build_telescope_sight_packet(self)
                    self.shared.udp_server.transport.sendto(packet, addr)



        if self.has_telescope_rcs:
            # Rotate slowly toward target angle (shortest arc), no momentum/fuel use
            # Use the same real-time scaling you use elsewhere:
            scaled_dt = dt / getattr(self.shared, "gamespeed", 1.0)

            max_rate = 5.0  # tweakable
            max_step = max_rate * scaled_dt  # degrees we can turn this tick

            delta = shortest_delta_deg(self.rotation, self.telescope_rcs_angle)

            self.rotation_velocity = 0.0

            if abs(delta) <= max_step:
                # Snap when close enough
                self.rotation = wrap_deg(self.rotation + delta)
            else:
                # Step toward target by max_step in the correct direction
                self.rotation = wrap_deg(self.rotation + (max_step if delta > 0.0 else -max_step))


        #Give income to the agency
        _agency.distribute_money(_payload_income_per_second / _tickrate)


    def check_deployment_ready(self):
        self.deployment_ready = False
        if(self.stage == 1 and self.altitude >= (self.home_planet.atmosphere_km * .98)):
            self.deployment_ready = True


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
        self.cool_towards_ambient(dt)
        self.check_deployment_ready()

        #4 - Charge Power
        if self.solar_power > 0.0:
            self._charge_power(self.solar_power * dt)

        # Clamp to speed of light (FOR NOW!)
        vx, vy = self.velocity
        speed = math.hypot(vx, vy)
        C_KM_S = 299_792.458
        if speed > C_KM_S:
            scale = C_KM_S / speed
            self.velocity = (vx * scale, vy * scale)

        # 4 -  Stream vessel data to clients
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
        chunkpacket += struct.pack('<f', self.liquid_fuel_kg)
        chunkpacket += struct.pack('<f', self.liquid_fuel_capacity_kg)
        chunkpacket += struct.pack('<f', self.power_capacity)
        chunkpacket += struct.pack('<f', self.power)
        chunkpacket += struct.pack('<f', self.maximum_operating_tempterature_c)
        chunkpacket += struct.pack('<f', self.current_temperature_c)
        chunkpacket += struct.pack('<f', self.ambient_temp_K)
        chunkpacket += struct.pack('<H', self.stage)
        chunkpacket += struct.pack('<B', self.deployment_ready)

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


    def _nozzle_local_point(self, component, pt):
        """Convert authored nozzle offset (screen Y-down) to physics local (Y-up)."""
        if isinstance(pt, (list, tuple)) and len(pt) >= 2:
            px = float(pt[0])
            py = float(pt[1])
            return (component.x + px, component.y - py)  # <-- flip Y
        return (component.x, component.y)

    def _burn_fuel(self, kg: float) -> bool:
        cur = self._current_stage_fuel()
        if kg <= 0 or cur < kg:
            return False
        self._set_current_stage_fuel(cur - kg)
        self.liquid_fuel_kg = self._current_stage_fuel()
        self.mass = self.calculate_mass(self.shared.component_data)
        return True

    def apply_forward_thrust(self, dt: float):
        total_kN = 0.0
        mult = getattr(self.shared, "global_thrust_multiplier", 1.0)

        for component in self.components:
            if getattr(component, "stage", 0) != self.stage:
                continue
            cd = self.shared.component_data.get(component.id, {}) or {}
            attrs = cd.get("attributes", {}) or {}

            kN = float(attrs.get("forward-thrust", 0.0))
            if kN <= 0.0:
                continue

            fuelConsumption = float(attrs.get("forward-fuel-consumption", 0.0)) * 0.003
            if fuelConsumption > 0.0:
                fuel_needed = fuelConsumption * dt
                if self._burn_fuel(fuel_needed):
                    heat = float(attrs.get("forward-fire-heat", 1.0)) * 0.001
                    self.current_temperature_c += heat * dt
                else:
                    kN = 0.0
                    self.control_state[VesselState.FORWARD_THRUSTER_ON] = False

            pt = attrs.get("forward-fire-output-point")
            local_point = self._nozzle_local_point(component, pt)

            eff_kN = kN * mult
            if eff_kN > 0.0:
                # Asset forward is -Y → -90°
                self.apply_thrust_at(local_point, direction_angle_deg=-90, thrust_kN=eff_kN, dt=dt)
                total_kN += eff_kN

        self.last_forward_thrust_kN += total_kN


    def apply_ccw_thrust(self, dt: float):
        mult = getattr(self.shared, "global_thrust_multiplier", 1.0)
        attn = getattr(self.shared, "attitude_thrust_scale", 0.1)

        for component in self.components:
            if getattr(component, "stage", 0) != self.stage:
                continue
            cd = self.shared.component_data.get(component.id, {}) or {}
            attrs = cd.get("attributes", {}) or {}

            base_kN   = float(attrs.get("ccw-thrust", 0.0))
            thrust_kN = base_kN * attn
            thrust_dir = float(attrs.get("ccw-thrust-direction", 0.0))

            fuelConsumption = float(attrs.get("ccw-fuel-consumption", 0.0)) * 0.003
            if fuelConsumption > 0.0:
                fuel_needed = fuelConsumption * dt
                if self._burn_fuel(fuel_needed):
                    heat = float(attrs.get("ccw-fire-heat", 1.0)) * 0.001
                    self.current_temperature_c += heat * dt
                else:
                    thrust_kN = 0.0
                    self.control_state[VesselState.CCW_THRUST_ON] = False

            if thrust_kN <= 0.0:
                continue

            pt = attrs.get("ccw-fire-output-point")
            local_point = self._nozzle_local_point(component, pt)

            self.apply_thrust_at(local_point,
                                direction_angle_deg=-90 + thrust_dir,
                                thrust_kN=thrust_kN * mult,
                                dt=dt)

    def apply_cw_thrust(self, dt: float):
        mult = getattr(self.shared, "global_thrust_multiplier", 1.0)
        attn = getattr(self.shared, "attitude_thrust_scale", 0.1)

        for component in self.components:
            if getattr(component, "stage", 0) != self.stage:
                continue
            cd = self.shared.component_data.get(component.id, {}) or {}
            attrs = cd.get("attributes", {}) or {}

            base_kN   = float(attrs.get("cw-thrust", 0.0))
            thrust_kN = base_kN * attn
            thrust_dir = float(attrs.get("cw-thrust-direction", 0.0))

            fuelConsumption = float(attrs.get("cw-fuel-consumption", 0.0)) * 0.003
            if fuelConsumption > 0.0:
                fuel_needed = fuelConsumption * dt
                if self._burn_fuel(fuel_needed):
                    heat = float(attrs.get("cw-fire-heat", 1.0)) * 0.001
                    self.current_temperature_c += heat * dt
                else:
                    thrust_kN = 0.0
                    self.control_state[VesselState.CW_THRUST_ON] = False

            if thrust_kN <= 0.0:
                continue

            pt = attrs.get("cw-fire-output-point")
            local_point = self._nozzle_local_point(component, pt)

            self.apply_thrust_at(local_point,
                                direction_angle_deg=-90 + thrust_dir,
                                thrust_kN=thrust_kN * mult,
                                dt=dt)


    def apply_reverse_thrust(self, dt: float):
        mult = getattr(self.shared, "global_thrust_multiplier", 1.0)

        for component in self.components:
            if getattr(component, "stage", 0) != self.stage:
                continue
            cd = self.shared.component_data.get(component.id, {}) or {}
            attrs = cd.get("attributes", {}) or {}

            thrust_kN = float(attrs.get("reverse-thrust", 0.0))
            if thrust_kN <= 0.0:
                continue

            fuelConsumption = float(attrs.get("reverse-fuel-consumption", 0.0)) * 0.003
            if fuelConsumption > 0.0:
                fuel_needed = fuelConsumption * dt
                if self._burn_fuel(fuel_needed):
                    heat = float(attrs.get("reverse-fire-heat", 1.0)) * 0.001
                    self.current_temperature_c += heat * dt
                else:
                    thrust_kN = 0.0
                    self.control_state[VesselState.REVERSE_THRUSTER_ON] = False

            if thrust_kN <= 0.0:
                continue

            pt = attrs.get("reverse-fire-output-point")
            local_point = self._nozzle_local_point(component, pt)

            thrust_dir = float(attrs.get("reverse-thrust-direction", 0.0)) + 180.0
            self.apply_thrust_at(local_point,
                                direction_angle_deg=-90 + thrust_dir,
                                thrust_kN=thrust_kN * mult,
                                dt=dt)




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


    def cool_towards_ambient(self, dt: float):
        # Convert server dt to “real seconds”
        scaled_dt = dt / max(1e-9, float(getattr(self.shared, "gamespeed", 1.0)))

        # Passive ambient coupling
        ambient_c = float(getattr(self, "ambient_temp_K", 2.7)) - 273.15
        tau = max(1e-3, float(getattr(self, "thermal_resistance", 100.0)))

        # Stronger convection in atmosphere (same idea you had)
        if self.home_planet is not None:
            dx = self.position[0] - self.home_planet.position[0]
            dy = self.position[1] - self.home_planet.position[1]
            alt_km = math.hypot(dx, dy) - float(self.home_planet.radius_km)
            if alt_km <= float(self.home_planet.atmosphere_km):
                tau *= 0.25

        # Passive exponential move toward ambient
        alpha_passive = 1.0 - math.exp(-scaled_dt / tau)
        self.current_temperature_c += (ambient_c - self.current_temperature_c) * alpha_passive

        # --- Active thermal regulation (toward 20 °C), consumes power ---
        reg = self.systems.get(Systems.THERMAL_REGULATOR)
        if reg and reg.active and (reg.amount > 0.0):
            target_c = 20.0

            # Convert 'amount' into an effective time constant (tweakable knob):
            # larger amount -> faster pull to 20C. Example: tau_reg = 60s / amount
            tau_reg = max(1e-3, 60.0 / float(reg.amount))
            alpha_reg = 1.0 - math.exp(-scaled_dt / tau_reg)

            # Draw power for this tick; scale effect if power is insufficient
            needed_power = max(0.0, float(reg.power_draw)) * scaled_dt
            power_fraction = 1.0
            if needed_power > 0.0:
                before = self.power
                ok = self._draw_power(needed_power)
                used = max(0.0, before - self.power)
                power_fraction = min(1.0, used / needed_power) if needed_power > 0 else 1.0

            # Apply regulation scaled by available power
            if power_fraction > 0.0:
                self.current_temperature_c += (target_c - self.current_temperature_c) * (alpha_reg * power_fraction)



from collections import deque

def calculate_component_stages(components, connections, component_data_lookup, payload_index=None):
    """
    Stage assignment:
      - Start at payload = stage 0.
      - When traversing from a node to a neighbor, the neighbor's stage is:
            neighbor_stage = current_stage + neighbor_attrs.get("stage-add", 0)
        i.e., entering a separator bumps the stage for the separator itself and everything after it.
      - If multiple paths reach a node, keep the highest stage discovered (so any path that crosses
        a separator will raise the node's final stage).
      - Any completely disconnected parts default to stage 1.
    """
    n = len(components)
    stages = [-1] * n  # -1 means unvisited

    # Find payload index if not provided
    if payload_index is None:
        for i, comp in enumerate(components):
            cd = component_data_lookup.get(comp.id, {})
            attrs = cd.get("attributes", {})
            if attrs.get("payload_base_income") is not None or attrs.get("is_payload") or cd.get("type") == "payload":
                payload_index = i
                break
        if payload_index is None:
            payload_index = 0  # fallback

    # Build adjacency
    adj = [[] for _ in range(n)]
    for a, b in connections:
        a = int(a); b = int(b)
        if 0 <= a < n and 0 <= b < n:
            adj[a].append(b)
            adj[b].append(a)

    # BFS from payload
    queue = deque()
    stages[payload_index] = 0
    queue.append(payload_index)

    while queue:
        cur = queue.popleft()
        cur_stage = stages[cur]

        for nb in adj[cur]:
            # Stage bump is applied when ENTERING the neighbor
            nb_data = component_data_lookup.get(components[nb].id, {}) or {}
            nb_attrs = nb_data.get("attributes", {}) or {}
            bump = int(nb_attrs.get("stage-add", 0))  # e.g., separator = 1, others = 0
            cand_stage = cur_stage + bump

            # If unseen or we discovered a higher stage via another path, update
            if stages[nb] == -1 or cand_stage > stages[nb]:
                stages[nb] = cand_stage
                queue.append(nb)

    # Any unconnected bits default to stage 1 (legacy behavior)
    for i in range(n):
        if stages[i] == -1:
            stages[i] = 1

    return stages


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
    connections = [(int(a), int(b)) for a, b in vessel_request_data.get("connections", [])]
    payload_idx = None

    highest_stage = 0

    for component in vessel_request_data["vessel_data"]:
        comp_id = int(component["id"])
        placement_x = int(component["x"]) - 2500
        placement_y = int(component["y"])  - 2500 
        _paint_1 = int(component.get("paint_1") or 0)
        _paint_2 = int(component.get("paint_2") or 0)
        
        component_definition = component_data_lookup.get(comp_id)
        if component_definition is None:
            raise ValueError(f"Invalid component ID: {comp_id}")
        
        total_cost += component_definition.get("cost", 0)
        components.append(AttachedVesselComponent(id=comp_id, x=placement_x, y=placement_y, paint1=_paint_1, paint2=_paint_2))
        
    if player.money < total_cost:
        raise ValueError(f"Insufficient funds: cost={total_cost}, player has={player.money}")
    
    #Subtract money
    player.money -= total_cost
    print("Creating vessel")

    payload_idx = detect_payload_index(components, component_data_lookup)
    stages = calculate_component_stages(components, connections, component_data_lookup, payload_idx)
    #Create the vessel
    for comp, st in zip(components, stages):
        setattr(comp, "stage", st)

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
    vessel.stage, vessel.num_stages = max(stages), max(stages) + 1
    vessel.payload = components[payload_idx].id

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

def detect_payload_index(components, component_data_lookup):
    """
    Finds the index of the payload component by looking for 'is-payload' == truthy
    in the component's attributes.
    """
    for i, comp in enumerate(components):
        comp_data = component_data_lookup.get(comp.id, {})
        attrs = comp_data.get("attributes", {})
        if attrs.get("is-payload"):  # Any truthy value counts
            return i
    return None