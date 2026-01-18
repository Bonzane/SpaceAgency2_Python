import asyncio
from dataclasses import dataclass, field
import struct
from typing import List, Dict, Tuple, Any, Union, Optional, Set
from physics import C_KM_S, G, AU_KM
from gameobjects import PhysicsObject, GameObject, ObjectType, JettisonedComponent
from enum import Enum, IntEnum
import math
from packet_types import DataGramPacketType
from vessel_components import Components
from utils import ambient_temp_simple, shortest_delta_deg, wrap_deg, _coerce_int_keys, _notify_player_udp
from payload_registry import make_payload_behavior
from modifiers import Op, Modifier, apply_modifiers, UPGRADES_BY_PAYLOAD   # and your UPGRADES dict (see below)
from upgrade_tree import UPGRADE_TREES_BY_PAYLOAD, UpgradeNode                # your UpgradeNode map


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
    SET_SYSTEM_STATE = 0x0B

class VesselState(IntEnum):
    FORWARD_THRUSTER_ON = 0x00
    REVERSE_THRUSTER_ON = 0x01
    CCW_THRUST_ON = 0x02
    CW_THRUST_ON = 0x03

class Systems(IntEnum):
    UNDEFINED = 0
    THERMAL_REGULATOR = 1
    MAGNETOMETER = 2
    ION_DRIVE = 3
    WARP_DRIVE = 4


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
    hull_integrity : float = 100.0
    armor : float = 0.0
    dry_mass : float = 0.0
    liquid_fuel_kg: float = 0.0
    liquid_fuel_capacity_kg: float = 0.0
    capable_forward_thrust: float = 0.0
    capable_reverse_thrust: float = 0.0
    power_capacity: float = 0.0
    solar_power: float = 0.0
    nuclear_power: float = 0.0
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
    altitude: float = 0.0 # Altitude above the home planet's surface
    z_velocity : float = 0.0
    landed: bool = True
    landed_angle_offset: float = 0.0
    strongest_gravity_force: float = 0.0
    strongest_gravity_source: Optional[GameObject] = None
    altitude_delta: float = 0.0
    last_forward_thrust_kN: float = 0.0
    regions_already_visited: List[int] = field(default_factory=list)
    region: int = 0  # Region ID for the vessel, used for proximity cues
    stage: int = 0
    num_stages: int = 0
    lifetime_revenue: int = 0
    _lifetime_revenue_carry: float = 0.0   # holds fractional income
    payload: int = 0
    maximum_operating_temperature_c: float = 100.0
    current_temperature_c: float = 20.0
    thermal_resistance: float = 100
    deployment_ready: bool = False
    unland_grace_time_s: float = 0.0
    seats_capacity: int = 0
    astronauts_onboard: List[int] = field(default_factory=list)
    cargo_capacity: int = 0
    cargo: Dict[int, int] = field(default_factory=dict)
    landing_progress: float = 0.0
    manned_mission_time_days: float = 0.0

    # --- navigation helpers ---
    def _direction_from_origin(self) -> Tuple[float, float]:
        x, y = self.position
        mag = math.hypot(x, y)
        if mag <= 0:
            return (1.0, 0.0)
        return (x / mag, y / mag)

    def _direction_to_point(self, pt: Tuple[float, float]) -> Tuple[float, float]:
        px, py = pt
        x, y = self.position
        dx, dy = (px - x), (py - y)
        mag = math.hypot(dx, dy)
        if mag <= 0:
            return (1.0, 0.0)
        return (dx / mag, dy / mag)

    #---Telescopes---
    telescope_rcs_angle: float = 0.0
    telescope_targets_in_sight: List[GameObject] = field(default_factory=list, repr=False)
    telescope_range_km: float = AU_KM
    telescope_fov_deg: float = 40.0

    #---Probes---
    planets_visited: List[int] = field(default_factory=list)

    #---Landers---
    last_landed_body_id: Optional[int] = None


    fuel_by_stage: Dict[int, float] = field(default_factory=dict, repr=False)
    capacity_by_stage: Dict[int, float] = field(default_factory=dict, repr=False)
    power_by_stage: Dict[int, float] = field(default_factory=dict, repr=False)
    power_capacity_by_stage: Dict[int, float] = field(default_factory=dict, repr=False)

    #--- Electrical Systems ---
    systems: Dict[Systems, ElectricalSystem] = field(default_factory=dict, repr=False)
    mag_push_accum: float = field(default=0.0, repr=False)
    max_warp: int = 0

    #Upgrades
    unlocked_by_payload: Dict[int, Set[int]] = field(default_factory=dict)  # payload_id -> set[upgrade_id]
    stats: Dict[str, Any] = field(default_factory=dict, repr=False)
    upgrade_tree_push_accum: float = field(default=0.0, repr=False)




    def __post_init__(self):
        # Defer: payload/shared/stage aren’t known yet
        self.payload_behavior = None
        if self.payload not in self.unlocked_by_payload:
            self.unlocked_by_payload[self.payload] = set()
        print("VESSEL SPAWNED")

    def _payload_attr(self, key: str, default=0.0) -> float:
        try:
            cd = (self.shared.component_data.get(int(self.payload), {}) or {})
            attrs = (cd.get("attributes", {}) or {})
            return float(attrs.get(key, default))
        except Exception:
            return float(default)

    def _ensure_payload_behavior(self):
        if not self.has_payload:
            self.payload_behavior = None
            return
        if (self.payload_behavior is None or
            getattr(self.payload_behavior, "payload_id", None) != int(self.payload)):
            self.payload_behavior = make_payload_behavior(self)


    def credit_income(self, amount: float) -> int:
        """Accumulate income; store whole units in lifetime_revenue, keep the rest in a carry bucket."""
        amt = float(amount)
        if amt <= 0.0:
            return self.lifetime_revenue
        self._lifetime_revenue_carry += amt

        whole = int(self._lifetime_revenue_carry)   # truncate toward 0
        if whole > 0:
            self.lifetime_revenue += whole
            self._lifetime_revenue_carry -= whole
        return self.lifetime_revenue
    
    def _payload_base_income(self) -> float:
        try:
            cd = (self.shared.component_data.get(int(self.payload), {}) or {})
            return float((cd.get("attributes", {}) or {}).get("payload_base_income", 0.0))
        except Exception:
            return 0.0


    def __getstate__(self):
        state = self.__dict__.copy()

        # runtime links (never pickle)
        state['shared'] = None
        state['home_chunk'] = None
        state['payload_behavior'] = None

        # store only a light reference to planet
        hp = state.get('home_planet')
        state['_home_planet_id'] = int(getattr(hp, 'object_id', 0) or 0)
        state['home_planet'] = None

        # ephemeral / recomputable
        state['telescope_targets_in_sight'] = []
        state['strongest_gravity_source'] = None
        state['mag_push_accum'] = 0.0
        state['_build_on_land_fired'] = False

        return state


    def __setstate__(self, state):
        self.__dict__.update(state)

        # re-init runtime fields; chunk will reattach
        self.shared = None
        self.home_chunk = None
        self.payload_behavior = None

        if getattr(self, 'telescope_targets_in_sight', None) is None:
            self.telescope_targets_in_sight = []
        self.strongest_gravity_source = None
        self.mag_push_accum = 0.0
        self.upgrade_tree_push_accum = float(getattr(self, 'upgrade_tree_push_accum', 0.0))
        self._build_on_land_fired = False
        self.landing_progress = 0.0

    def _build_base_stats(self) -> Dict[str, Any]:
        return {
            "thrust":   {"forward": float(self.capable_forward_thrust),
                        "reverse": float(self.capable_reverse_thrust)},
            "power":    {"capacity": float(self._attached_power_capacity()),
                        "solar": float(self.solar_power),
                        "nuclear": float(self.nuclear_power),
                        "draw_payload": 0.0},
            "thermal":  {"resistance": float(self.thermal_resistance),
                        "target_c": 20.0},
            "telescope": {"range_km": float(self.telescope_range_km),
                        "fov_deg": float(self.telescope_fov_deg),
                        "max_rate_deg_s": 5.0},
            "income":   {"base": self._payload_base_income()},   # <<— was 0.0
        }

    def _component_world_position(self, comp_xy: Tuple[float, float]) -> Tuple[float, float]:
        """
        Convert a component's local (x,y) into world (x,y), honoring vessel rotation and CoM.
        Assumes component coordinates are already in physics space (Y-up).
        NOTE: self.rotation is stored in screen/CW degrees, so negate it to get math/CCW.
        """
        cx, cy = comp_xy
        comx, comy = self.center_of_mass
        dx = cx - comx
        dy = cy - comy

        # Convert stored CW degrees to CCW radians for standard rotation matrix
        rot = math.radians(-self.rotation + 90)
        cos_t = math.cos(rot)
        sin_t = math.sin(rot)

        # rotate relative vector, then translate by vessel world position
        rx = dx * cos_t - dy * sin_t
        ry = dx * sin_t + dy * cos_t

        vx, vy = self.position
        return (vx + rx, vy + ry)


    def _resolve_chunk(self):
        """
        Prefer the chunk manager's id→chunk map (authoritative), fall back to home_chunk.
        """
        cm = getattr(self.shared, "chunk_manager", None)
        if cm and hasattr(cm, "get_chunk_from_object_id"):
            ch = cm.get_chunk_from_object_id(int(self.object_id))
            if ch is not None:
                return ch
        return getattr(self, "home_chunk", None)


    def _spawn_jettisoned_component(self, comp: AttachedVesselComponent, comp_index: int):
        chunk = self._resolve_chunk()
        if chunk is None:
            print(f"⚠️ spawn_jettisoned_component: no chunk for vessel id={int(self.object_id)}; aborting spawn.")
            return

        # --- read defs
        cd    = (self.shared.component_data.get(int(comp.id), {}) or {})
        attrs = (cd.get("attributes", {}) or {})
        mass_kg   = float(cd.get("mass", 1.0))
        radius_km = float(attrs.get("radius-km", 0.2))  # ~200m default

        # --- world placement
        world_pos = self._component_world_position((float(comp.x), float(comp.y)))

        # small outward push from vessel center toward component
        vx, vy = self.velocity
        dirx = world_pos[0] - self.position[0]
        diry = world_pos[1] - self.position[1]
        d = math.hypot(dirx, diry)
        if d > 0.0:
            ux, uy = (dirx / d, diry / d)
        else:
            ang = math.radians(self.rotation - 90.0)  # forward
            ux, uy = math.cos(ang), math.sin(ang)

        PUSH_KM_S = 0.1
        j_vel = (vx + ux * PUSH_KM_S, vy + uy * PUSH_KM_S)

        # --- build the physics object
        jc = JettisonedComponent(
            position=world_pos,
            velocity=j_vel,
            mass=mass_kg,
            radius_km=radius_km,
            component_index=int(comp_index),
            agency_id = int(self.agency_id)
        )
        jc.rotation = float(self.rotation)
        jc.component_id = int(getattr(comp, "id", 0))
        jc.agency_id = int(self.agency_id)


        # allow longer lifetime during debugging
        try:
            dbg_life = float(getattr(self.shared, "jettison_lifetime_s", 0.0))
            if dbg_life > 0.0:
                jc.lifetime = dbg_life
        except Exception:
            pass

        # normalize id type before registration to avoid lookup mismatches
        jc.object_id = int(jc.object_id)

        # --- pre-add log
        print(
            f"🧩 JC SPAWN "
            f"vessel={int(self.object_id)} comp_index={int(comp_index)} "
            f"oid={int(jc.object_id)} pos=({world_pos[0]:.3f},{world_pos[1]:.3f}) "
            f"vel=({j_vel[0]:.6f},{j_vel[1]:.6f}) mass={mass_kg:.3f}kg r={radius_km:.3f}km "
            f"life={getattr(jc,'lifetime',0.0):.2f}s "
            f"chunk={(chunk.galaxy, chunk.system)}"
        )

        # --- register in chunk
        try:
            chunk.add_object(jc)
        except Exception as e:
            print(f"❌ JC ADD FAILED oid={int(jc.object_id)} chunk={(chunk.galaxy, chunk.system)}: {e}")
            return

        # --- verify registration/mapping
        in_chunk = chunk.get_object_by_id(int(jc.object_id)) is not None
        mapped = None
        try:
            cm = getattr(chunk, "manager", None)
            if cm is not None:
                mapped = cm.object_id_to_chunk.get(int(jc.object_id))
        except Exception:
            pass

        if in_chunk:
            print(
                f"✅ JC REGISTERED oid={int(jc.object_id)} "
                f"in_chunk={in_chunk} map={mapped} "
                f"(objects_now={len(getattr(chunk,'objects',[]))})"
            )
        else:
            print(
                f"⚠️ JC NOT FOUND AFTER ADD oid={int(jc.object_id)} "
                f"chunk={(chunk.galaxy, chunk.system)} map={mapped}"
            )



    def _collect_active_modifiers(self) -> list:
        """Only modifiers for *this* payload and only when stage==0."""
        if self.stage != 0:
            return []

        pid = int(self.payload)
        unlocked = self.unlocked_by_payload.get(pid, set())
        bundles_for_payload = UPGRADES_BY_PAYLOAD.get(pid, {})
        mods = []
        for up_id in unlocked:
            mods.extend(bundles_for_payload.get(up_id, []))
        return mods

    def _apply_stats(self):
        base = self._build_base_stats()
        mods = self._collect_active_modifiers()
        self.stats = apply_modifiers(base, mods, self)

        # Mirror back to legacy fields used elsewhere:
        self.capable_forward_thrust = float(self.stats["thrust"]["forward"])
        self.capable_reverse_thrust = float(self.stats["thrust"]["reverse"])
        self.power_capacity         = float(self.stats["power"]["capacity"])
        self.telescope_fov_deg      = float(self.stats["telescope"]["fov_deg"])
        self.telescope_range_km     = float(self.stats["telescope"]["range_km"])

        # seat capacity from payload attributes (default 0)
        try:
            self.seats_capacity = int(self._payload_attr("seats", 0))
        except Exception:
            self.seats_capacity = 0

        # Clamp any excess occupants if payload changed
        if len(self.astronauts_onboard) > self.seats_capacity:
            self.astronauts_onboard = self.astronauts_onboard[: max(0, int(self.seats_capacity))]


    def _build_upgrades_dgram(self) -> bytes:
        if not self.has_payload or self.stage != 0:
            return None
        # 1) already-unlocked (as ints)
        unlocked = sorted(int(u) for u in self.current_payload_unlocked())

        # 2) which ones are purchasable right now (tier + prereqs + stage==0)
        unlockables = self.list_current_unlockables()

        # 3) attach prices for the ones that are True
        tree = self.current_payload_tree()
        purch = []
        for up_id, can in unlockables.items():
            if can and up_id in tree:
                cost = int(getattr(tree[up_id], "cost_money", 0))
                purch.append((int(up_id), cost))
        purch.sort(key=lambda t: t[0])

        # --- pack
        buf = bytearray()
        buf.append(int(DataGramPacketType.VESSEL_UPGRADE_TREE))  # define this enum value

        buf += struct.pack('<Q', int(self.object_id))
        buf += struct.pack('<H', len(unlocked))
        for up_id in unlocked:
            buf += struct.pack('<H', int(up_id))

        buf += struct.pack('<H', len(purch))
        for up_id, cost in purch:
            buf += struct.pack('<HQ', int(up_id), int(cost))

        return bytes(buf)

    def _broadcast_upgrade_tree_to_agency(self):
        pkt = self._build_upgrades_dgram()
        if not pkt:
            return
        udp = getattr(self.shared, "udp_server", None)
        if udp and getattr(udp, "transport", None):
            udp.send_udp_to_agency(int(self.agency_id), pkt)


    # --- helpers ---
    def _send_upgrade_tree_to_player(self, player_id: int) -> int:
        if not self.has_payload or self.stage != 0 or not player_id:
            return 0
        """Send the current upgrade tree UDP packet to a single player (by id)."""
        if not player_id:
            return 0
        shared = getattr(self, "shared", None)
        if not shared:
            return 0
        udp = getattr(shared, "udp_server", None)
        if not (udp and getattr(udp, "transport", None)):
            return 0
        p = shared.players.get(int(player_id))
        if not p:
            return 0
        s = getattr(p, "session", None)
        if not (s and s.alive and getattr(s, "udp_port", None)):
            return 0

        pkt = self._build_upgrades_dgram()
        addr = (s.remote_ip, s.udp_port)
        udp.transport.sendto(pkt, addr)
        return 1

    def _tick_upgrade_tree_push(self, real_dt: float):
        if not self.has_payload or not int(getattr(self, "controlled_by", 0)):
            self.upgrade_tree_push_accum = 0.0
            return
        self.upgrade_tree_push_accum += max(0.0, float(real_dt))
        if self.upgrade_tree_push_accum >= 1.0:
            self._send_upgrade_tree_to_player(int(self.controlled_by))
            self.upgrade_tree_push_accum = 0.0


    def apply_ion_drive(self, dt: float):
        """
        Continuous, control-agnostic forward thrust when the ion drive is active and powered.
        Thrust (kN) = systems[ION_DRIVE].amount * (power actually used / power requested).
        Draws power each tick; scales thrust down if there isn't enough power.
        """
        sys = self.systems.get(Systems.ION_DRIVE)
        if not sys or not sys.active or sys.amount <= 1e-9:
            return
        if self.power_capacity <= 0.0:
            return

        # How much power the drive wants this tick
        draw_per_sec = max(0.0, float(getattr(sys, "power_draw", 0.0) * 0.001))
        need = draw_per_sec * max(0.0, float(dt))
        self.rotation_velocity = self.rotation_velocity * 0.995
        # If there's no power budget, bail early
        if need <= 0.0:
            return

        # Try to pay for it; throttle thrust by the fraction we could afford
        before = self.power
        _ = self._draw_power(need)
        used = max(0.0, before - self.power)
        if used <= 0.0:
            return

        throttle = min(1.0, used / need)

        # Convert "amount" into forward thrust (kN), scaled by available power and global multiplier
        base_kN = float(sys.amount) * throttle
        mult = float(getattr(self.shared, "global_thrust_multiplier", 1.0))
        kN = base_kN * mult
        if kN <= 0.0:
            return

        # Apply pure forward thrust through the center of mass (no torque)
        local_point = self.center_of_mass  # acts like a force through CoM
        self.apply_thrust_at(local_point, direction_angle_deg=-90, thrust_kN=kN, dt=dt)

        # Feed altitude/lift model that keys off forward thrust
        self.last_forward_thrust_kN += kN


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
        self.nuclear_power = 0.0
        self.armor = 0.0
        self.aerodynamics = 0.0

        self.systems.clear()

        # reset thermal resistance
        base_tau = 100.0
        try:
            ag = getattr(self.shared, "agencies", {}).get(int(self.agency_id))
            if ag and hasattr(ag, "attributes"):
                base_tau += float(getattr(ag, "attributes", {}).get("thermal_resistance_bonus", 0.0) or 0.0)
        except Exception:
            pass
        attached_tau_bonus = 0.0 

        #cargo
        self.cargo_capacity = 0


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

            # per-stage thrust capability
            stage_forward[st] = stage_forward.get(st, 0.0) + float(attrs.get("forward-thrust", 0.0))
            stage_reverse[st] = stage_reverse.get(st, 0.0) + float(attrs.get("reverse-thrust", 0.0))

            total_mass += mass
            weighted_x += comp.x * mass
            weighted_y += comp.y * mass



            #FOR EVERY ATTACHED COMPONENT:
            if st <= self.stage:
                self.add_system(
                    Systems.THERMAL_REGULATOR,
                    float(attrs.get("thermal-regulation", 0.0)),
                    float(attrs.get("thermal-regulation-power-draw", 0.0)),
                    True
                )
                self.add_system(
                    Systems.MAGNETOMETER,
                    float(attrs.get("magnetometer", 0.0)),
                    float(attrs.get("magnetometer-power-draw", 0.0)),
                    False
                )
                self.add_system(
                    Systems.ION_DRIVE,
                    float(attrs.get("ion-drive", 0.0)),
                    float(attrs.get("ion-drive-power-draw", 0.0)),
                    False
                )
                self.add_system(
                    Systems.WARP_DRIVE,
                    float(attrs.get("warp-drive", 0.0)),
                    float(attrs.get("warp-drive-power-draw", 0.0)),
                    False
                )
                attached_tau_bonus += float(attrs.get("thermal-resistance", 0.0))
                self.solar_power += float(attrs.get("solar-power", 0.0))
                self.nuclear_power += float(attrs.get("nuclear-power", 0.0)) * 0.1
                self.armor += float(attrs.get("armor", 0.0))
                self.aerodynamics += float(attrs.get("aerodynamics", 0.0))
                self.cargo_capacity += int(attrs.get("cargo-capacity", 0) or 0)
                self.max_warp = max(
                    float(getattr(self, "max_warp", 0.0)),
                    float(attrs.get("max-warp", 0.0))
                )

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
        self.power = min(self._attached_power(), self.power_capacity)
        self._apply_stats()


    # -- WARP HELPERS ---
    def warp_to_speed_km_s(self, warp: float) -> float:
        """Invert your UI formula: speed = c * warp^(1/0.3)."""
        if warp <= 0.0:
            return 0.0
        return float(C_KM_S) * (float(warp) ** (1.0 / 0.3))

    def speed_to_warp(self, speed_km_s: float) -> float:
        """UI display helper based on your given formula (only meaningful > c)."""
        s = float(speed_km_s)
        if s <= float(C_KM_S):
            return 0.0
        return (s / float(C_KM_S)) ** 0.3

    def _has_powered_warp(self) -> bool:
        sys = self.systems.get(Systems.WARP_DRIVE)
        if not sys or not sys.active or sys.amount <= 0.0:
            return False
        # Must have decent charge
        if self.power <= 0.05 * max(1e-9, self.power_capacity):
            return False
        # Only when not landed and out of atmosphere
        if self.landed or not self.home_planet:
            return False
        try:
            if self.altitude < float(self.home_planet.atmosphere_km) - 1e-6:
                return False
        except Exception:
            pass
        return True

    def _warp_tau(self) -> float:
        # allow per-component override: attributes["warp-tau-s"]
        return float(self._payload_attr("warp-tau-s", 1000.0))

    def _check_chunk_transition(self) -> None:
        try:
            cm = getattr(self.shared, "chunk_manager", None)
            ch = getattr(self, "home_chunk", None)
            if not cm or not ch:
                return
            scale_km = getattr(ch, "km_per_unit", 1.0)
            r_units = math.hypot(self.position[0], self.position[1])
            r_km = r_units * scale_km

            # In a system chunk
            if ch.system > 0:
                if r_km > cm.system_exit_radius_km:
                    cm.transfer_to_starmap(self)
                return

            # In a galaxy starmap
            if ch.galaxy > 0 and ch.system == 0:
                # Try to enter a system if close to a point
                for p in cm.get_starmap_points(ch.galaxy):
                    px, py = float(p.get("x", 0.0)), float(p.get("y", 0.0))
                    dist = math.hypot(self.position[0] - px, self.position[1] - py)
                    if dist <= cm.starmap_entry_radius:
                        cm.transfer_to_system(self, ch.galaxy, int(p.get("id", 0)), (px, py))
                        return
                # Exit to universe if beyond galaxy boundary
                if r_units > cm.galaxy_boundary_radius:
                    cm.transfer_to_universe(self)
                return

            # In the universe map
            if ch.galaxy == 0:
                for p in cm.get_universe_points():
                    px, py = float(p.get("x", 0.0)), float(p.get("y", 0.0))
                    dist = math.hypot(self.position[0] - px, self.position[1] - py)
                    if dist <= cm.universe_entry_radius:
                        cm.transfer_to_galaxy(self, int(p.get("id", 1)), (px, py))
                        return
        except Exception as e:
            print(f"⚠️ chunk transition check failed: {e}")

    def _end_warp(self, sys: Optional[ElectricalSystem], reason: str = ""):
        """Restore pre-warp velocity, clear bonus, drop the engaged flag, and (optionally) flip the system off."""
        try:
            # Restore original velocity exactly as requested
            if getattr(self, "_warp_engaged", False):
                self.velocity = tuple(getattr(self, "_warp_saved_velocity", self.velocity))
        finally:
            self._warp_bonus_v = (0.0, 0.0)
            self._warp_engaged = False
            self._warp_active_this_tick = False
            if sys is not None:
                # auto-deactivate hardware so UI reflects the drop
                sys.active = False
        
    def apply_warp(self, dt: float):
        """
        Warp acts as a *speed bonus* layered on top of the vessel's pre-warp velocity.
        While engaged, we blend a forward-pointing bonus vector toward the max-warp target
        using the warp tau. When warp ends (any reason), we *restore* the original velocity.
        Warp ticks bypass the c clamp and don't use thrust, so no relativistic damping applies.
        """
        self._warp_active_this_tick = False

        sys = self.systems.get(Systems.WARP_DRIVE)
        if not sys or sys.amount <= 0.0:
            # If we had been in warp but hardware disappeared, restore.
            if getattr(self, "_warp_engaged", False):
                self._end_warp(sys=None, reason="no system")
            return
        if self.max_warp <= 0.0:
            if getattr(self, "_warp_engaged", False):
                self._end_warp(sys, "max_warp=0")
            return

        # Must be out of atmosphere and not landed (keeps your rules)
        if self.landed:
            if getattr(self, "_warp_engaged", False):
                self._end_warp(sys, "landed")
            return
        try:
            if self.home_planet and (self.altitude < float(self.home_planet.atmosphere_km) - 1e-6):
                if getattr(self, "_warp_engaged", False):
                    self._end_warp(sys, "in atmo")
                return
        except Exception:
            pass

        # Battery floor: auto-drop if state-of-charge is low
        cap = max(1e-9, float(self.power_capacity))
        soc = float(self.power) / cap if cap > 0 else 0.0
        SOC_CUTOFF = 0.05  # 5% -> turn warp off automatically
        if soc <= SOC_CUTOFF:
            if getattr(self, "_warp_engaged", False):
                self._end_warp(sys, "low battery")
            return

        # If the user turned the system off, fall out of warp and restore
        if not sys.active:
            if getattr(self, "_warp_engaged", False):
                self._end_warp(sys=None, reason="manually off")
            return

        # --- Power draw & throttle (per real tick) ---
        draw_per_sec = max(0.0, float(getattr(sys, "power_draw", 0.0))) * 0.01  # your 0.01 scaling
        need = draw_per_sec * max(0.0, float(dt))
        throttle = 1.0
        if need > 0.0:
            before = self.power
            _ = self._draw_power(need)          # may be partial
            used = max(0.0, before - self.power)
            if used <= 0.0:
                # no juice -> fall out of warp and restore
                if getattr(self, "_warp_engaged", False):
                    self._end_warp(sys, "no power")
                return
            throttle = min(1.0, used / need)

        # --- First engage: capture the *original* velocity we’ll restore later ---
        if not getattr(self, "_warp_engaged", False):
            self._warp_saved_velocity = tuple(self.velocity)
            self._warp_bonus_v = (0.0, 0.0)
            self._warp_engaged = True

        # --- Direction (your “strange math”): forward = (cos rot, -sin rot) ---
        ang = math.radians(self.rotation)
        fwd = (math.cos(ang), -math.sin(ang))

        # --- Target warp speed from max_warp, mapped to bonus magnitude ---
        target_speed = self.warp_to_speed_km_s(float(self.max_warp))
        if target_speed <= 0.0:
            # Nothing to do; treat as deactivation
            self._end_warp(sys, "zero target")
            return
        tvx, tvy = (fwd[0] * target_speed, fwd[1] * target_speed)

        # --- Smooth spin-up/spin-down of the *bonus* using warp tau & system amount ---
        base_tau = max(1e-3, float(self._warp_tau()))
        eff_tau  = max(1e-3, base_tau / max(1e-6, float(sys.amount)))
        # real-time scaling (not affected by gamespeed)
        scaled_dt = float(dt)
        alpha = (1.0 - math.exp(-scaled_dt / eff_tau)) * throttle
        if alpha <= 0.0:
            # still mark warp-active so we skip c-clamp this tick if engaged
            self._warp_active_this_tick = bool(self._warp_engaged)
            # Keep current composed velocity
            svx, svy = self._warp_saved_velocity
            bx, by   = self._warp_bonus_v
            self.velocity = (svx + bx, svy + by)
            return

        # Blend the *bonus* toward the forward target, independent of base velocity.
        bx, by = self._warp_bonus_v
        bx = bx + (tvx - bx) * alpha
        by = by + (tvy - by) * alpha
        self._warp_bonus_v = (bx, by)

        # Compose final velocity for physics this tick: original + bonus
        svx, svy = self._warp_saved_velocity
        self.velocity = (svx + bx, svy + by)

        # While warp is active this tick, skip the c clamp in do_update
        self._warp_active_this_tick = True

        # Optional: graceful auto-drop if throttle collapses (e.g., power starvation)
        # When the bonus is very small relative to target and throttle is tiny, disengage.
        if throttle < 0.05:
            # start spinning the bonus down smoothly by turning the system off;
            # _end_warp will restore the base instantly per design.
            self._end_warp(sys, "throttle too low")

    # ----------------------
    # Cargo helpers
    # ----------------------
    def cargo_total(self) -> int:
        """Total units of all resources currently onboard."""
        return sum(int(max(0, v)) for v in (self.cargo or {}).values())

    def cargo_free(self) -> int:
        """Free capacity left (never negative)."""
        cap = int(max(0, self.cargo_capacity))
        return max(0, cap - self.cargo_total())

    def get_cargo(self, resource_id: int) -> int:
        """How many units of a specific resource onboard."""
        try:
            rid = int(resource_id)
        except Exception:
            return 0
        return int(self.cargo.get(rid, 0))

    def add_cargo(self, resource_id: int, amount: int) -> int:
        """
        Try to add `amount` units of `resource_id`.
        Returns how many were actually added (clamped by free capacity).
        """
        try:
            rid = int(resource_id)
            amt = int(amount)
        except Exception:
            return 0
        if amt <= 0:
            return 0

        put = min(amt, self.cargo_free())
        if put <= 0:
            return 0

        self.cargo[rid] = int(self.cargo.get(rid, 0)) + put
        return put

    def remove_cargo(self, resource_id: int, amount: int) -> int:
        """
        Remove up to `amount` units of `resource_id`.
        Returns how many were actually removed.
        """
        try:
            rid = int(resource_id)
            amt = int(amount)
        except Exception:
            return 0
        if amt <= 0:
            return 0

        have = int(self.cargo.get(rid, 0))
        take = min(amt, have)
        if take <= 0:
            return 0

        newv = have - take
        if newv > 0:
            self.cargo[rid] = newv
        else:
            # keep dict tidy
            if rid in self.cargo:
                del self.cargo[rid]
        return take

    def try_add_cargo_bundle(self, bundle: Dict[int, int]) -> Dict[int, int]:
        """
        Add a set of resources {rid: amount}. Returns a dict of leftovers that
        did not fit (empty dict means all added).
        """
        leftovers: Dict[int, int] = {}
        # Normalize keys to int, values to int
        for k, v in (bundle or {}).items():
            try:
                rid = int(k)
                amt = int(v)
            except Exception:
                continue
            if amt <= 0:
                continue
            put = self.add_cargo(rid, amt)
            rem = amt - put
            if rem > 0:
                leftovers[rid] = rem
            if self.cargo_free() <= 0:
                # no more capacity; carry through any remaining items
                for kk, vv in bundle.items():
                    if kk == k:
                        continue
                    try:
                        rr = int(kk); aa = int(vv)
                    except Exception:
                        continue
                    if aa > 0:
                        leftovers[rr] = leftovers.get(rr, 0) + aa
                break
        return leftovers

    def try_remove_cargo_bundle(self, bundle: Dict[int, int]) -> Dict[int, int]:
        """
        Remove a set of resources {rid: amount}. Returns a dict of shortfalls
        (how many we couldn't remove).
        """
        short: Dict[int, int] = {}
        for k, v in (bundle or {}).items():
            try:
                rid = int(k)
                amt = int(v)
            except Exception:
                continue
            if amt <= 0:
                continue
            took = self.remove_cargo(rid, amt)
            if took < amt:
                short[rid] = (amt - took)
        return short

    def trim_cargo_to_capacity(self) -> int:
        """
        Optional utility: if total cargo exceeds capacity (e.g. after staging),
        discard excess deterministically. Returns how many units were trimmed.
        (You can replace this policy with 'spill to base' when landed.)
        """
        cap = int(max(0, self.cargo_capacity))
        tot = self.cargo_total()
        if tot <= cap:
            return 0
        to_trim = tot - cap

        # Remove from the largest stacks first (deterministic).
        items = sorted(self.cargo.items(), key=lambda kv: kv[1], reverse=True)
        for rid, cnt in items:
            if to_trim <= 0:
                break
            take = min(cnt, to_trim)
            left = cnt - take
            if left > 0:
                self.cargo[rid] = left
            else:
                del self.cargo[rid]
            to_trim -= take
        return tot - self.cargo_total()



    @property 
    def has_payload(self) -> bool:
        return self.payload != 0


    def can_unlock(self, upgrade_id: int) -> bool:
        tree = UPGRADE_TREES_BY_PAYLOAD.get(self.payload, {})
        node: UpgradeNode = tree.get(upgrade_id)
        if not node:
            return False  # upgrade doesn't exist for this payload
        unlocked = self.unlocked_by_payload.get(self.payload, set())
        return all(dep in unlocked for dep in node.requires)

    def unlock(self, upgrade_id: str) -> bool:
        if not self.can_unlock(upgrade_id): 
            return False
        self.unlocked_upgrades.add(upgrade_id)
        self._apply_stats()  # re-compute with new modifiers
        return True


    def calculate_mass(self, component_data_lookup: Dict[int, Dict]) -> float:
        dry = sum(component_data_lookup.get(comp.id, {}).get("mass", 0.0) for comp in self.components)
        attached_fuel = sum(v for s, v in self.fuel_by_stage.items() if s <= self.stage)
        return dry + attached_fuel

    def _iter_planets_in_same_system(self):
        """Yield Planet objects in our current chunk/system."""
        from gameobjects import Planet
        chunk = getattr(self, "home_chunk", None)
        if not chunk:
            return []
        objs = []

        # Be defensive about chunk storage structure
        for attr in ("objects", "objects_by_id", "id_to_object", "object_lookup"):
            container = getattr(chunk, attr, None)
            if isinstance(container, dict):
                objs.extend(container.values())
            elif isinstance(container, (list, tuple, set)):
                objs.extend(container)

        # Deduplicate just in case we appended from multiple containers
        seen = set()
        planets = []
        for o in objs:
            if o is None or not isinstance(o, Planet):
                continue
            oid = getattr(o, "object_id", id(o))
            if oid in seen:
                continue
            seen.add(oid)
            planets.append(o)
        return planets



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

        self.power = min(self._attached_power(), self._attached_power_capacity())
        return remaining <= 0.0

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
        self.power = max(0.0, min(self._attached_power(), self._attached_power_capacity()))




    
    def do_control(self, control):
        try:
            control = VesselControl(control)
        except Exception:
            return  # unknown control byte
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
                self.deploy_stage(force=True)

    def set_system_state(self, system_id, new_state):
        """
        Set the state of a system by its ID.
        """
        system = Systems(system_id)
        if system in self.systems:
            self.systems[system].active = new_state
            print(f"System {system.name} state set to {'active' if new_state else 'inactive'}")
        else:
            print(f"System {system.name} not found in vessel systems.")


    def _current_stage_capacity(self) -> float:
        return float(self.capacity_by_stage.get(self.stage, 0.0))

    def _current_stage_fuel(self) -> float:
        return float(self.fuel_by_stage.get(self.stage, 0.0))

    def _set_current_stage_fuel(self, value: float):
        cap = self._current_stage_capacity()
        self.fuel_by_stage[self.stage] = max(0.0, min(value, cap))

    def _notify_force_resolve(self):
        """Schedule tcp.broadcast_force_resolve(self) whether we're on the main loop or a worker thread."""
        tcp = getattr(self.shared, "tcp_server", None)
        if not tcp:
            return
        coro = tcp.broadcast_force_resolve(self)
        try:
            # If we're on the event loop thread:
            loop = asyncio.get_running_loop()
            loop.create_task(coro)
        except RuntimeError:
            # We're in a non-async thread. Use a reference to the main loop if you have it.
            main_loop = getattr(self.shared, "main_loop", None)
            if main_loop and main_loop.is_running():
                asyncio.run_coroutine_threadsafe(coro, main_loop)
            else:
                # As a last resort, just skip notifying instead of crashing.
                # (You can also queue this to a thread-safe queue your main loop drains.)
                print("⚠️ No running event loop found to notify force_resolve.")

    def deploy_stage(self, force: bool = False):
        prev = self.stage
        if self.stage <= 0:
            print("Can not deploy - already deployed!")
            return

        if not force and not self.deployment_ready:
            if self.stage == 1 and self.has_payload:
                print("Can not deploy to payload unless in space.")
            else:
                print("Can not deploy - not ready!")
            return

        prev_stage = self.stage
        self.stage -= 1
        self.deployment_ready = False

        # --- NEW: if we're dropping to the LAST stage (0), remember payload's world pos
        payload_world_pre = None
        if self.stage == 0:
            # find the payload component (stage 0) or by id as a fallback
            payload_comp = None
            for c in self.components:
                if int(getattr(c, "stage", 0)) == 0:
                    payload_comp = c
                    break
            if payload_comp is None:
                for c in self.components:
                    if int(getattr(c, "id", 0)) == int(self.payload):
                        payload_comp = c
                        break
            if payload_comp is not None:
                payload_world_pre = self._component_world_position(
                    (float(payload_comp.x), float(payload_comp.y))
                )

        # --- identify and spawn jettisoned parts for components no longer attached
        drop_count = 0
        try:
            comps_to_drop = [(idx, c) for idx, c in enumerate(self.components)
                            if int(getattr(c, "stage", 0)) > self.stage]
            drop_count = len(comps_to_drop)
            for idx, comp in comps_to_drop:
                self._spawn_jettisoned_component(comp, idx)
        except Exception as e:
            print(f"⚠️ spawn jettisoned components failed: {e}")
            drop_count = 0

        # Remove components that no longer belong (also recomputes stats & CoM)
        self.drop_components()

        if drop_count > 0:
            agency = getattr(self.shared, "agencies", {}).get(int(self.agency_id))
            if agency:
                agency.experience_points = int(getattr(agency, "experience_points", 0)) + drop_count
                udp = getattr(self.shared, "udp_server", None)
                if udp:
                    for _ in range(drop_count):
                        udp.send_xp_orb_to_agency(
                            agency_id=int(agency.id64),
                            point_type=3,
                            source_kind=0,
                            vessel_id=int(self.object_id),
                        )

        # --- NEW: after CoM updates (now == payload local center), snap origin to saved world pos
        if self.stage == 0 and payload_world_pre is not None:
            self.position = payload_world_pre
            # rotation/velocity unchanged; payload world pos remains continuous
            print(f"📍 Snapped vessel origin to payload center at ({payload_world_pre[0]:.3f}, {payload_world_pre[1]:.3f})")

        # Kill thrust on stage change
        self.control_state[VesselState.CCW_THRUST_ON] = False
        self.control_state[VesselState.CW_THRUST_ON] = False
        self.control_state[VesselState.REVERSE_THRUSTER_ON] = False
        self.control_state[VesselState.FORWARD_THRUSTER_ON] = False

        print(f"✅ Vessel {self.object_id} staged: {prev_stage} → {self.stage} (components={len(self.components)})")

        # Notify after snap so clients see the final, snapped position
        self._notify_force_resolve()
        if prev != self.stage:
            self._ensure_payload_behavior()
            if self.stage == 0 and self.payload_behavior:
                self.payload_behavior.on_attach()
            elif prev == 0 and self.payload_behavior:
                self.payload_behavior.on_detach()

        # Recompute (applies tier gates etc.)
        self._apply_stats()

        # Now send the tree once we’re fully settled at stage 0
        if self.stage == 0:
            self._broadcast_upgrade_tree_to_agency()



    def _auto_stage_if_empty(self):
        # Only stages that actually had tanks should auto-deploy
        if self.stage > 0 and self._current_stage_capacity() > 0.0 and self._current_stage_fuel() <= 0.0:
            print(f"⚙️ Auto-staging: fuel depleted at stage {self.stage}")
            self.deploy_stage(force=True)


    def drop_components(self):
        kept = []
        for comp in self.components:
            if int(getattr(comp, "stage", 0)) <= self.stage:
                kept.append(comp)
        self.components = kept

        # prune per-stage tanks that are no longer attached
        self.fuel_by_stage = {s: v for s, v in self.fuel_by_stage.items() if s <= self.stage}
        self.capacity_by_stage = {s: v for s, v in self.capacity_by_stage.items() if s <= self.stage}
        self.power_by_stage = {s:v for s,v in self.power_by_stage.items()      if s <= self.stage}  

        self.calculate_vessel_stats()


    def do_payload_mechanics(self, dt: float):
        if self.stage != 0 or not self.has_payload:
            return
        self._ensure_payload_behavior()
        #print(f"[Vessel] mech tick oid={self.object_id} stage={self.stage} "
        #    f"payload={self.payload} behavior={type(self.payload_behavior).__name__}")
        if self.payload_behavior:
            try:
                self.payload_behavior.on_tick(dt)
            except Exception as e:
                print(f"[Vessel] payload on_tick error: {e}")

    def _tick_landing_initiation(self, dt: float):
        if self.landed or not self.home_planet:
            self.landing_progress = 0.0
            return

        # "In space" = above the home planet's atmosphere ceiling
        atm = float(getattr(self.home_planet, "atmosphere_km", 0.0))
        in_space = self.altitude >= atm - 1e-6
        if not in_space:
            self.landing_progress = 0.0
            return

        # "Over the planet" = within the planet's radius (center distance <= R)
        px, py = self.home_planet.position
        x, y = self.position
        dist = math.hypot(x - px, y - py)
        R = float(getattr(self.home_planet, "radius_km", 0.0))

        if dist <= R + 1e-6:
            self.landing_progress = min(6.0, self.landing_progress + max(0.0, float(dt) / 1000))
        else:
            self.landing_progress = 0.0

        # Hit 6s → bump just inside atmo so normal landing logic takes over
        if self.landing_progress >= 6.0 and self.altitude >= atm - 1e-6:
            self.altitude = max(0.0, atm - 1.0)
            if self.z_velocity > 0.0:
                self.z_velocity = 0.0
            self.landing_progress = 6.0


    def _max_tier_for_current_payload(self) -> int:
        """Gate by agency attributes"""
        agency = self.shared.agencies.get(self.agency_id)
        if not agency:
            return 0
        # Communications satellite gate
        from vessel_components import Components
        if int(self.payload) == int(Components.COMMUNICATIONS_SATELLITE):
            return int(agency.attributes.get("satellite_max_upgrade_tier", 1))
        if int(self.payload) == int(Components.PROBE):
            return int(agency.attributes.get("probe_max_upgrade_tier", 1))
        # Other payload types can have their own attrs later
        return 999


    def check_deployment_ready(self):
        # default: not ready
        self.deployment_ready = False

        # Only care when we’re about to drop from stage 1 -> 0
        if not self.has_payload:
            return
        if self.stage != 1 or not self.home_planet:
            return

        # Payload rule from its component attributes
        wants_landed = bool(int(self._payload_attr("deploy-landed", 0)))

        if wants_landed:
            # Rovers, landers, etc. — must be landed
            self.deployment_ready = bool(self.landed)
        else:
            # Space payloads — must be at (≈) top of atmosphere and not landed
            atm = float(getattr(self.home_planet, "atmosphere_km", 0.0))
            self.deployment_ready = (not self.landed) and (self.altitude >= atm * 0.98)



    def _rel_speed_to(self, body) -> float:
        if not body:
            return float('inf')
        vx, vy = self.velocity
        pvx, pvy = body.velocity
        return math.hypot(vx - pvx, vy - pvy)

    def _surface_g_km_s2(self) -> float:
        # Prefer explicit property if your Planet has one
        g = getattr(self.home_planet, "surface_g_km_s2", None)
        if g is not None:
            return float(g)
        # Otherwise compute from mass & radius
        try:
            return G * float(self.home_planet.mass) / (float(self.home_planet.radius_km) ** 2)
        except Exception:
            return 0.0


    def _maybe_begin_landing_when_matching_velocity(self, prev_rel: float, post_rel: float):
        if self.landed or not self.home_planet:
            return

        # Only while actually thrusting forward/reverse
        if not (self.control_state.get(VesselState.FORWARD_THRUSTER_ON) or
                self.control_state.get(VesselState.REVERSE_THRUSTER_ON)):
            return

        atm = float(self.home_planet.atmosphere_km)

        # Only trigger from the top of the atmosphere
        if self.altitude < atm - 1e-6:
            return

        # NEW: only if we're actually near the planet — inside atmospheric radius (R + atm)
        hx, hy = self.home_planet.position
        x, y = self.position
        dist = math.hypot(x - hx, y - hy)
        if dist > float(self.home_planet.radius_km):
            return

        # If thrust reduced |v - planet.v|, begin descent
        if post_rel + 1e-6 < prev_rel:
            self.altitude = max(0.0, atm - 1.0)
            if self.z_velocity > 0.0:
                self.z_velocity = 0.0

    def _has_powered_magnetometer(self) -> bool:
        sys = self.systems.get(Systems.MAGNETOMETER)
        if not sys or not sys.active or sys.amount <= 0.0:
            return False
        if self.power <= 0.05 * self.power_capacity:
            return False
        # optional: only when fully deployed
        # if self.stage != 0: return False
        return True

    def _has_powered_ion_drive(self) -> bool:
        sys = self.systems.get(Systems.ION_DRIVE)
        if not sys or not sys.active or sys.amount <= 0.0:
            return False
        if self.power <= 0.05 * self.power_capacity:
            return False
        # optional: only when fully deployed
        # if self.stage != 0: return False
        return True

    def _compute_magnetometer_field(self):
        """Returns (samples, net_dir_deg, net_strength). samples = [(body_id, dir_deg, strength, flags)]."""
        try:
            planets = [p for p in self._iter_planets_in_same_system() if not getattr(p, "is_moon", False)]
        except Exception:
            planets = []
        if not planets:
            return [], 0.0, 0.0

        vx, vy = self.position
        MAX_RANGE_KM = AU_KM * 5.0     # tune for gameplay
        SCALE        = 1.0e10          # tune to map to 0..1 strengths

        contrib = []
        for p in planets:
            px, py = p.position
            dx, dy = px - vx, py - vy
            d = math.hypot(dx, dy)
            if d <= 0.0 or d > MAX_RANGE_KM:
                continue

            r_km = float(getattr(p, "radius_km", 3000.0))
            base_moment = (r_km ** 3) * (5.0 if getattr(p, "is_gas_giant", False) else 1.0)
            moment = float(getattr(p, "magnetic_moment", base_moment))

            strength = moment / (d ** 3)
            strength = max(0.0, min(1.0, strength * SCALE))
            if strength <= 1e-6:
                continue

            dir_deg = math.degrees(math.atan2(dy, dx))
            flags = (1 if getattr(p, "is_gas_giant", False) else 0) | (2 if getattr(p, "is_moon", False) else 0)
            ux, uy = (dx/d, dy/d)
            contrib.append((int(p.object_id), dir_deg, strength, flags, ux, uy))

        if not contrib:
            return [], 0.0, 0.0

        contrib.sort(key=lambda t: t[2], reverse=True)
        top = contrib[:3]

        nx = sum(s * ux for _id, _dir, s, _f, ux, _uy in top)
        ny = sum(s * uy for _id, _dir, s, _f, _ux, uy in top)
        net_len = math.hypot(nx, ny)
        net_dir_deg = math.degrees(math.atan2(ny, nx)) if net_len > 1e-9 else 0.0
        net_strength = max(0.0, min(1.0, net_len))

        samples = [(i, float(d), float(s), int(f)) for i, d, s, f, _ux, _uy in top]
        return samples, net_dir_deg, net_strength

    def _build_magnetometer_packet(self, net_dir_deg: float, net_strength: float, samples: list) -> bytes:
        pkt = bytearray()
        pkt.append(int(DataGramPacketType.MAGNETOMETER_FIELD))
        pkt += struct.pack('<Q', int(self.object_id))
        pkt += struct.pack('<ff', float(net_dir_deg), float(net_strength))
        pkt.append(min(len(samples), 255))
        for body_id, dir_deg, strength, flags in samples[:255]:
            pkt += struct.pack('<QffB', int(body_id), float(dir_deg), float(strength), int(flags) & 0xFF)
        return bytes(pkt)

    def _send_udp_to_controller(self, packet: bytes) -> bool:
        shared = getattr(self, "shared", None)
        if not (shared and getattr(shared, "udp_server", None) and shared.udp_server.transport):
            return False
        pid = int(getattr(self, "controlled_by", 0) or 0)
        if not pid:
            return False
        player = shared.players.get(pid)
        if not player:
            return False
        s = getattr(player, "session", None)
        if not (s and s.alive and getattr(s, "udp_port", None)):
            return False
        addr = (s.remote_ip, s.udp_port)
        shared.udp_server.transport.sendto(packet, addr)
        return True

    def _tick_magnetometer(self, real_dt: float):
        """Call every tick with real seconds (dt/gamespeed). Sends ~5Hz while powered & controlled."""
        if not self._has_powered_magnetometer():
            self.mag_push_accum = 0.0
            return
        # only the controller should receive it
        if not int(getattr(self, "controlled_by", 0)):
            self.mag_push_accum = 0.0
            return

        # throttle
        self.mag_push_accum += max(0.0, float(real_dt))
        PERIOD = 0.20  # ~5 Hz
        if self.mag_push_accum < PERIOD:
            return
        self.mag_push_accum = 0.0

        # pay instrument power (skip send if no juice)
        sys = self.systems.get(Systems.MAGNETOMETER)
        draw = max(0.0, float(getattr(sys, "power_draw", 0.0))) * PERIOD
        if draw > 0.0:
            before = self.power
            _ = self._draw_power(draw)
            used = max(0.0, before - self.power)
            if used < draw * 0.25:   # couldn’t cover at least 25% of the draw → skip this tick
                return

        samples, net_dir, net_str = self._compute_magnetometer_field()
        if not samples:
            return

        # Quest metric: magnetometer activated (once per vessel)
        if not getattr(self, "_magnetometer_quest_flag", False):
            try:
                agency = getattr(self.shared, "agencies", {}).get(int(self.agency_id))
                if agency and hasattr(agency, "record_quest_metric"):
                    agency.record_quest_metric("magnetometer_activated", 1)
                    self._magnetometer_quest_flag = True
            except Exception as e:
                print(f"⚠️ magnetometer quest update failed: {e}")

        pkt = self._build_magnetometer_packet(net_dir, net_str, samples)
        self._send_udp_to_controller(pkt)


    # Extended Physics
    def do_update(self, dt: float, acc: Tuple[float, float]):
        self.last_forward_thrust_kN = 0.0

        if not self.landed:
            self._maybe_rehome_to_strongest()

        # NEW: landing initiation tracking
        self._tick_landing_initiation(dt)

        _prev_rel = self._rel_speed_to(self.home_planet)
        self.apply_ion_drive(dt)
        self.apply_warp(dt)

        _prev_rel = self._rel_speed_to(self.home_planet)
        self.apply_ion_drive(dt)
        self.apply_warp(dt)
        # 1. Apply Thrust before physics updates
        if self.control_state.get(VesselState.FORWARD_THRUSTER_ON, False):
            self.apply_forward_thrust(dt)
        if self.control_state.get(VesselState.CCW_THRUST_ON, False):
            self.apply_ccw_thrust(dt)
        if self.control_state.get(VesselState.CW_THRUST_ON, False):
            self.apply_cw_thrust(dt)
        if self.control_state.get(VesselState.REVERSE_THRUSTER_ON, False):
            self.apply_reverse_thrust(dt)

       # OLD METHOD OF LANDING self._maybe_begin_landing_when_matching_velocity(_prev_rel, self._rel_speed_to(self.home_planet))


        # Check transition condition: should we launch?
        if self.landed:
            if self.should_unland():
                self.unland()
            else:
                self.stay_landed()
        else:
            self.update_altitude(dt)

        # Track manned mission time in in-game days while in space
        if self.landed:
            self.manned_mission_time_days = 0.0
        elif self.astronauts_onboard:
            self.manned_mission_time_days += max(0.0, float(dt)) / 86400.0

        # 2. Apply rotation
        self.rotation += self.rotation_velocity * dt

        if not self.landed:
            super().do_update(dt, acc)

            self.ground_influence(dt)

        #3 - Do Payload Mechanics
        self.do_payload_mechanics(dt)
        self.cool_towards_ambient(dt)
        self.take_temperature_damage(dt)
        self.check_deployment_ready()
        self.check_destroyed()
        
        scaled_dt = dt / max(1e-9, float(self.shared.gamespeed))
        self._tick_upgrade_tree_push(scaled_dt)
        self._tick_magnetometer(scaled_dt)

        #4 - Charge Power
        solar_eff = 0.0
        if self.solar_power > 0.0:
            solar_eff = self.solar_efficiency_from_distance(self.position)
            self._charge_power(self.solar_power * scaled_dt * solar_eff)

        if self.nuclear_power > 0.0:
            self._charge_power(self.nuclear_power * scaled_dt)

        # Clamp to speed of light (If no warp)
        vx, vy = self.velocity
        speed = math.hypot(vx, vy)
        C_CAP = 299_792.458

        if not getattr(self, "_warp_active_this_tick", False):
            if speed > C_CAP:
                scale = C_CAP / speed
                self.velocity = (vx * scale, vy * scale)

        # 4 -  Stream vessel data to clients
        chunkpacket = bytearray()
        chunkpacket.append(DataGramPacketType.VESSEL_STREAM)
        chunkpacket += struct.pack('<Q', self.object_id)
        chunkpacket += struct.pack('<Q', self.agency_id)
        chunkpacket += struct.pack('<Q', int(self.lifetime_revenue))
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
        chunkpacket += struct.pack('<f', self.landing_progress)
        # vertical rate (km/s); positive = ascending, negative = descending
        chunkpacket += struct.pack('<f', float(self.z_velocity))
        chunkpacket += struct.pack('<f', self.hull_integrity)
        chunkpacket += struct.pack('<f', self.liquid_fuel_kg)
        chunkpacket += struct.pack('<f', self.liquid_fuel_capacity_kg)
        chunkpacket += struct.pack('<H', self.cargo_capacity)
        chunkpacket += struct.pack('<f', self.power)
        chunkpacket += struct.pack('<f', self.power_capacity)
        chunkpacket += struct.pack('<f', solar_eff)
        chunkpacket += struct.pack('<f', self.maximum_operating_temperature_c)
        chunkpacket += struct.pack('<f', self.current_temperature_c)
        chunkpacket += struct.pack('<f', self.ambient_temp_K)
        chunkpacket += struct.pack('<H', self.stage)
        chunkpacket += struct.pack('<B', self.deployment_ready)
        chunkpacket += struct.pack('<f', self.planet_income_multiplier() )
        chunkpacket += struct.pack('<H', len(self.systems))
        for sys_type, sys in self.systems.items():
            chunkpacket += struct.pack('<H', int(sys_type))        # system type
            chunkpacket += struct.pack('<B', 1 if sys.active else 0)
        ids = getattr(self, "astronauts_onboard", [])
        cnt = min(255, len(ids))                      # u8 count
        chunkpacket += struct.pack('<B', cnt)
        for i in range(cnt):
            chunkpacket += struct.pack('<I', int(ids[i]) & 0xFFFFFFFF)
            
        for player in self.shared.players.values():
            session = player.session
            if session and session.udp_port and session.alive:
                addr = (session.remote_ip, session.udp_port)
                self.shared.udp_server.transport.sendto(chunkpacket, addr)

        #print(f"[DEBUG] Vessel {self.object_id} Velocity: vx={self.velocity[0]:.2f}, vy={self.velocity[1]:.2f}, Altitude: {self.altitude:.2f}")

        # --- Chunk transition checks ---
        self._check_chunk_transition()




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

    def current_payload_tree(self) -> Dict[int, UpgradeNode]:
        if not self.has_payload: 
            return {}
        return UPGRADE_TREES_BY_PAYLOAD.get(int(self.payload), {})


    def current_payload_unlocked(self) -> Set[int]:
        if not self.has_payload:
            return set()
        return self.unlocked_by_payload.setdefault(int(self.payload), set())

    def signal_vessel_destroyed(self):
        chunkpacket = bytearray()
        chunkpacket.append(DataGramPacketType.SIGNAL_DESTROY)
        chunkpacket += struct.pack('<Q', self.object_id)
        for player in self.shared.players.values():
            session = player.session
            if session and session.udp_port and session.alive:
                addr = (session.remote_ip, session.udp_port)
                self.shared.udp_server.transport.sendto(chunkpacket, addr)

    def planet_income_multiplier(self) -> float:
        """
        Return the per-planet cash multiplier for this vessel’s location.
        Uses the strongest gravity source if it's a planet; otherwise falls
        back to home_planet. If no base multiplier is set, returns 1.0.
        """
        try:
            from gameobjects import Planet
        except Exception:
            Planet = None

        # Pick the planet to look up
        src = getattr(self, "strongest_gravity_source", None)
        if Planet is None or not isinstance(src, Planet):
            return 1.0
        pid = int(getattr(src, "object_id", 0) or 0)
        if pid <= 0:
            return 1.0

        # Look up the agency-scoped multiplier
        agency = getattr(self.shared, "agencies", {}).get(int(self.agency_id))
        if not agency:
            return 1.0

        m = float(getattr(agency, "base_multipliers", {}).get(pid, 1.0))
        # sanity + NaN/inf guard
        if not (m > 0.0) or math.isinf(m) or math.isnan(m):
            return 1.0
        return m

    def can_unlock_current(self, upgrade_id: int) -> bool:
        if not self.has_payload or self.stage != 0:
            return False
        node = self.current_payload_tree().get(upgrade_id)
        if not node:
            return False
        have = self.current_payload_unlocked()
        return all(req in have for req in (node.requires or []))

    def unlock_current(self, upgrade_id: int) -> bool:
        if not self.can_unlock_current(upgrade_id):
            return False
        self.current_payload_unlocked().add(upgrade_id)
        self._apply_stats()
        return True

    def list_current_unlockables(self) -> Dict[int, bool]:
        if not self.has_payload or self.stage != 0:
            return {}
        tree = self.current_payload_tree()
        have = self.current_payload_unlocked()
        max_tier = self._max_tier_for_current_payload()
        out = {}
        for up_id, node in tree.items():
            if up_id in have:
                continue
            can = all(req in have for req in (getattr(node, "requires", []) or [])) \
                and int(getattr(node, "tier", 1)) <= int(max_tier)
            out[int(up_id)] = bool(can)
        return out

    def solar_efficiency_from_distance(self, pos=None, sun_pos=(0.0, 0.0), ref_dist_km=AU_KM):
        if pos is None:
            pos = self.position
        dx = pos[0] - sun_pos[0]
        dy = pos[1] - sun_pos[1]
        r  = max(1.0, math.hypot(dx, dy))
        eff = (ref_dist_km / r) ** 2
        return min(1.0, eff)                 # cap at 100%

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

        atm_height = max(1e-6, float(self.home_planet.atmosphere_km))
        in_atmo    = self.altitude < atm_height - 1e-6

        # Lift from forward thrust (unchanged)
        acc_proxy = self.last_forward_thrust_kN / max(self.mass, 1.0)
        alt_norm = max(0.0, min(1.0, self.altitude / atm_height))
        dens = 1.0 - alt_norm
        BASE = 0.5
        FADE_SHAPE = 2.0
        atmos_factor = BASE + (1.0 - BASE) * (dens ** FADE_SHAPE)
        THRUST_LIFT_ACCEL = 0.130 * (1.0 + 0.05 * float(getattr(self, "aerodynamics", 0.0)))
        a_up = (THRUST_LIFT_ACCEL * acc_proxy * atmos_factor) if in_atmo else 0.0
        MAX_SAFE_TOUCHDOWN = 1.2


        # Use the body’s own gravity near the surface (in-atmo only, to avoid double-counting)
        g_surface = self._surface_g_km_s2()
        a_down = (g_surface * 0.1) if in_atmo else 0.0

        # Vacuum descent damper: gently decay z-velocity toward 0 (tunable per body)
        if not in_atmo:
            tau = float(getattr(self.home_planet, "vacuum_descent_tau", 12.0))  # seconds
            if tau > 1e-6:
                self.z_velocity += (-self.z_velocity) * (1.0 - math.exp(-dt / tau))

        was_above_atm = self.altitude >= atm_height - 1e-6
        # Integrate vertical motion
        a_z = a_up - a_down
        self.z_velocity += a_z * dt
        self.altitude   += self.z_velocity * dt
        self.altitude_delta = self.z_velocity

        # Clamp at top of atmosphere
        if self.altitude >= atm_height:
            self.altitude = atm_height
            if self.z_velocity > 0.0:
                self.z_velocity = 0.0

        # Quest: first time reaching space (top of atmosphere) per vessel
        now_above = self.altitude >= atm_height - 1e-6
        if not was_above_atm and now_above and not getattr(self, "_quest_blastoff_done", False):
            try:
                agency = getattr(self.shared, "agencies", {}).get(int(self.agency_id))
                if agency and hasattr(agency, "record_quest_metric"):
                    agency.record_quest_metric("blastoff", 1)
                    self._quest_blastoff_done = True
            except Exception as e:
                print(f"⚠️ blastoff quest update failed: {e}")

        # --- Count down the takeoff grace window
        if getattr(self, "unland_grace_time_s", 0.0) > 0.0:
            self.unland_grace_time_s = max(0.0, self.unland_grace_time_s - dt)

        # Touchdown check
        if self.altitude <= 0.0:
            # clamp to ground
            self.altitude = 0.0

            # If we're within the grace period, don't land or explode yet.
            if getattr(self, "unland_grace_time_s", 0.0) > 0.0:
                # keep upward motion if any; kill tiny downward jitter
                if self.z_velocity < 0.0:
                    self.z_velocity = 0.0
                return

            # Only treat as an impact if actually descending.
            if self.z_velocity < 0.0:
                impact_speed = -self.z_velocity  # downward only, km/s
                self.z_velocity = 0.0

                if impact_speed > MAX_SAFE_TOUCHDOWN:
                    print(f"💥 Hard landing: impact {impact_speed:.3f} km/s -> destroy")
                    self.destroy()
                else:
                    self.land()
            else:
                # We're stationary or moving up at the ground plane:
                # do not force a land/destroy; let the next tick lift us cleanly.
                self.z_velocity = max(0.0, self.z_velocity)


            
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


    def _world_angle_from_planet(self, planet_rot_deg: float, offset_deg: float) -> float:
        """Convert screen-world planet rotation + offset -> math-world angle (CCW, +y up)."""
        # planet_rot is clockwise in screen coords; negate to get CCW
        return (-(planet_rot_deg + offset_deg)) % 360.0

    def _offset_from_world_angle(self, planet_rot_deg: float, world_ccw_deg: float) -> float:
        """Convert a math-world angle (from atan2) -> screen-world offset to store."""
        return (-(world_ccw_deg) - planet_rot_deg) % 360.0

    def _looks_like_planet(self, obj) -> bool:
        # Import inside the function to avoid any circular-import surprises
        from gameobjects import Planet, ObjectType
        if not isinstance(obj, Planet):
            return False

        # Optional: exclude bodies you never want to "land" on
        NON_LANDABLE = {
        }
        return getattr(obj, "object_type", None) not in NON_LANDABLE

    def _maybe_rehome_to_strongest(self):
        if self.landed:
            return
        src = self.strongest_gravity_source
        if not self._looks_like_planet(src):
            return
        if src is self.home_planet:
            return

        self.home_planet = src
        self.altitude = float(src.atmosphere_km)  # top of new atmo
        self.z_velocity = 0.0
        self.deployment_ready = False

    def _trigger_build_on_land_if_any(self):
        """
        If any component has attributes["build-on-land"] == [planet_name, building_type]
        and we just landed on that planet, auto-place the building (if missing)
        and unlock its blueprint for this agency. Fires only once per vessel.
        """
        if getattr(self, "_build_on_land_fired", False):
            return
        if not self.home_planet or not self.shared:
            return

        planet_name = str(getattr(self.home_planet, "name", "")).strip()
        if not planet_name:
            return

        # Find the first component declaring build-on-land
        target = None
        for comp in self.components:
            cd = (self.shared.component_data.get(comp.id, {}) or {})
            attrs = (cd.get("attributes", {}) or {})
            bol = attrs.get("build-on-land")
            if isinstance(bol, (list, tuple)) and len(bol) == 2:
                target = (str(bol[0]).strip(), int(bol[1]))
                break

        if not target:
            return

        wanted_name, building_type = target
        if planet_name.lower() != wanted_name.lower():
            return

        agency = self.shared.agencies.get(self.agency_id)
        if not agency:
            return

        base_id = int(getattr(self.home_planet, "object_id", 0))
        if base_id == 0:
            return

        # Already present?
        existing = False
        for b in agency.bases_to_buildings.get(base_id, []):
            if int(getattr(b, "type", -1)) == building_type:
                existing = True
                break

        if existing:
            self._build_on_land_fired = True
            return

        try:
            from buildings import Building, BuildingType

            # Place at the landing longitude (nice touch), mark constructed
            angle = float(getattr(self, "landed_angle_offset", 0.0))
            new_building = Building(BuildingType(int(building_type)), self.shared, angle, base_id, agency)
            new_building.constructed = True

            agency.add_building_to_base(base_id, new_building)

            # “Unlock” the blueprint for the agency for future use
            # Prefer an Agency API if you have one; otherwise keep a simple set.
            if hasattr(agency, "unlock_building_type") and callable(agency.unlock_building_type):
                agency.unlock_building_type(int(building_type))
            else:
                if not hasattr(agency, "unlocked_buildings") or agency.unlocked_buildings is None:
                    agency.unlocked_buildings = set()
                agency.unlocked_buildings.add(int(building_type))

            # Recompute any derived caps/bonuses
            if hasattr(agency, "update_attributes"):
                agency.update_attributes()

            # Notify the agency (async-safe scheduling)
            udp = getattr(self.shared, "udp_server", None)
            if udp:
                bname = (self.shared.buildings_by_id.get(int(building_type), {}) or {}).get("name", f"Building {building_type}")
                msg = f"{agency.name} established {bname} on {planet_name} (mission auto-build)"
                try:
                    loop = getattr(self.shared, "main_loop", None)
                    if loop and loop.is_running():
                        import asyncio
                        asyncio.run_coroutine_threadsafe(udp.notify_agency(agency.id64, 2, msg), loop)
                except Exception as e:
                    print(f"⚠️ notify_agency schedule failed: {e}")

            print(f"✅ Auto-built building {building_type} on {planet_name} for agency {agency.id64}")
        except Exception as e:
            print(f"⚠️ build-on-land mission hook failed: {e}")

        self._build_on_land_fired = True


    def land(self):
        self.landed = True
        self.altitude = 0.0
        self.z_velocity = 0.0
        self.rotation_velocity = 0.0
        self.velocity = self.home_planet.velocity
        self.landing_progress = 0.0

        # (removed) self._trigger_build_on_land_if_any()

        # ... existing math to set landed_angle_offset, position, rotation ...

        # ---- NEW: payload landing hook with previous body id ----
        prev_id = int(self.last_landed_body_id) if self.last_landed_body_id is not None else None
        self._ensure_payload_behavior()
        if self.payload_behavior and hasattr(self.payload_behavior, "on_land"):
            try:
                self.payload_behavior.on_land(self.home_planet, prev_body_id=prev_id)
            except Exception as e:
                print(f"[Vessel] payload on_land error: {e}")

        # mark visited & update “last landed on”
        pid = int(getattr(self.home_planet, "object_id", 0) or 0)
        if pid > 0 and pid not in set(int(x) for x in (self.planets_visited or [])):
            self.planets_visited.append(pid)
        self.last_landed_body_id = pid

        # Agency-level visited planets tracking
        try:
            agency = getattr(self.shared, "agencies", {}).get(int(self.agency_id))
            if agency is not None:
                if not hasattr(agency, "visited_planets") or agency.visited_planets is None:
                    agency.visited_planets = set()
                if pid > 0 and pid not in agency.visited_planets:
                    agency.visited_planets.add(pid)
        except Exception as e:
            print(f"⚠️ visited planet tracking failed: {e}")

        # Probe planet inspection tracking (per-probe max)
        try:
            from vessel_components import Components
        except Exception:
            Components = None
        try:
            if Components and int(getattr(self, "payload", 0)) == int(getattr(Components, "PROBE", 11)):
                if not hasattr(self, "inspected_planets") or self.inspected_planets is None:
                    self.inspected_planets = set()
                if pid > 0:
                    self.inspected_planets.add(pid)
                agency = getattr(self.shared, "agencies", {}).get(int(self.agency_id))
                if agency and hasattr(agency, "quest_counters"):
                    qc = agency.quest_counters
                    if not isinstance(qc, dict):
                        qc = {}
                    qc["max_probe_inspected_planets"] = max(
                        int(qc.get("max_probe_inspected_planets", 0)),
                        len(self.inspected_planets),
                    )
                    agency.quest_counters = qc
        except Exception as e:
            print(f"⚠️ probe inspection tracking failed: {e}")

        # quest metric: moon landings
        try:
            if bool(getattr(self.home_planet, "is_moon", False)):
                agency = getattr(self.shared, "agencies", {}).get(int(self.agency_id))
                if agency and hasattr(agency, "record_quest_metric"):
                    agency.record_quest_metric("moon_landings", 1)
                    # Rover-specific moon landing
                    try:
                        from vessel_components import Components
                    except Exception:
                        Components = None
                    if Components and int(getattr(self, "payload", 0)) == int(getattr(Components, "ROVER", 27)):
                        agency.record_quest_metric("rover_moon_landings", 1)
            # Mars rover landing
            try:
                pname = str(getattr(self.home_planet, "name", "")).strip().lower()
                if pname == "mars":
                    agency = getattr(self.shared, "agencies", {}).get(int(self.agency_id))
                    if agency and hasattr(agency, "record_quest_metric"):
                        from vessel_components import Components
                        if Components and int(getattr(self, "payload", 0)) == int(getattr(Components, "ROVER", 27)):
                            agency.record_quest_metric("rover_mars_landings", 1)
            except Exception:
                pass
        except Exception as e:
            print(f"⚠️ moon landing quest update failed: {e}")




    def stay_landed(self):
        self.velocity = self.home_planet.velocity
        self.z_velocity = 0.0
        self.rotation_velocity = 0.0
        self.altitude = 0.0

        R = float(self.home_planet.radius_km)
        world_deg = self._world_angle_from_planet(self.home_planet.rotation, self.landed_angle_offset)
        ang = math.radians(world_deg)

        cx, cy = self.home_planet.position
        self.position = (cx + R * math.cos(ang), cy + R * math.sin(ang))
        self.rotation = -world_deg





    def unland(self):
        self.landed = False
        self.altitude = 0.1
        self.z_velocity = 0.2
        self.unland_grace_time_s = 0.75
        self.landing_progress = 0.0

        # NEW: delegate takeoff/unland to payload behavior (optional)
        self._ensure_payload_behavior()
        if self.payload_behavior and hasattr(self.payload_behavior, "on_unland"):
            try:
                self.payload_behavior.on_unland(self.home_planet)
            except Exception as e:
                print(f"[Vessel] payload on_unland error: {e}")




    def _nozzle_local_point(self, component, pt):
        """Convert authored nozzle offset (screen Y-down) to physics local (Y-up)."""
        if isinstance(pt, (list, tuple)) and len(pt) >= 2:
            px = float(pt[0])
            py = float(pt[1])
            return (component.x + px, component.y - py)  # <-- flip Y
        return (component.x, component.y)

    def _burn_fuel(self, kg: float) -> bool:
        FUEL_EPS = 1e-6
        cur = self._current_stage_fuel()

        if kg <= 0:
            return True

        if cur <= FUEL_EPS:
            # snap to zero and auto-stage if this stage has tanks
            if self._current_stage_capacity() > 0.0:
                self._set_current_stage_fuel(0.0)
                self.liquid_fuel_kg = 0.0
                self.mass = self.calculate_mass(self.shared.component_data)
                self._auto_stage_if_empty()
            return False

        if cur < kg:
            # consume the remainder, zero the tank, and auto-stage
            self._set_current_stage_fuel(0.0)
            self.liquid_fuel_kg = 0.0
            self.mass = self.calculate_mass(self.shared.component_data)
            self._auto_stage_if_empty()
            return False

        # normal burn
        self._set_current_stage_fuel(cur - kg)
        self.liquid_fuel_kg = self._current_stage_fuel()
        self.mass = self.calculate_mass(self.shared.component_data)

        if self.liquid_fuel_kg <= FUEL_EPS and self._current_stage_capacity() > 0.0:
            self._set_current_stage_fuel(0.0)
            self.liquid_fuel_kg = 0.0
            self.mass = self.calculate_mass(self.shared.component_data)
            self._auto_stage_if_empty()

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




    def apply_thrust_at(self, local_point, direction_angle_deg, thrust_kN, dt):
        if thrust_kN <= 0 or self.mass <= 0:
            return
        # guard gamespeed
        gs = float(getattr(self.shared, "gamespeed", 1.0))
        scaled_dt = dt / max(1e-9, gs)

        thrust_N = thrust_kN * 1000.0   # multiplier already applied upstream

        angle_rad = math.radians(self.rotation + direction_angle_deg)
        fx = thrust_N * math.cos(angle_rad)
        fy = thrust_N * math.sin(angle_rad)

        dvx = (fx / self.mass) * scaled_dt
        dvy = (fy / self.mass) * scaled_dt
        vx, vy = self.velocity
        self.velocity = (vx - dvy, vy - dvx)   

        # --- Diminishing returns ---

        V_MAX  = C_KM_S * 0.99999
        V_90   = C_KM_S * 0.90
        dvx_raw = dvx
        dvy_raw = dvy

        speed = math.hypot(vx, vy)
        if speed > 0.0 and speed > V_90:
            # unit vector along current velocity
            ux, uy = vx / speed, vy / speed

            # split raw Δv into parallel and perpendicular components
            incr_par = dvx_raw * ux + dvy_raw * uy              # scalar Δspeed
            dv_par_x = incr_par * ux
            dv_par_y = incr_par * uy
            dv_perp_x = dvx_raw - dv_par_x
            dv_perp_y = dvy_raw - dv_par_y

            if incr_par > 0.0:  # only limit pushes that increase speed
                # 0 at 0.9c, 1 at V_MAX
                frac = (speed - V_90) / (V_MAX - V_90)
                if frac < 0.0: frac = 0.0
                if frac > 1.0: frac = 1.0

                # tunable steepness; crank this up if you want a harder wall
                p = 3.0
                damping = (1.0 - frac) ** p  # 1 at 0.9c → 0 at V_MAX

                headroom = max(0.0, V_MAX - speed)  # how much speed we can add at all
                allowed_incr = headroom * damping   # allowed Δspeed this tick

                if incr_par > allowed_incr:
                    # scale down only the parallel component to the allowed increment
                    scale = 0.0 if incr_par == 0.0 else (allowed_incr / incr_par)
                    dv_par_x *= scale
                    dv_par_y *= scale

                # recombine
                dvx_raw = dv_par_x + dv_perp_x
                dvy_raw = dv_par_y + dv_perp_y

                # apply (unchanged orientation math)
                self.velocity = (vx + dvx_raw, vy + dvy_raw)

        # safety: never exceed V_MAX due to numerics
        vx2, vy2 = self.velocity
        s2 = math.hypot(vx2, vy2)
        if s2 > V_MAX:
            k = V_MAX / s2
            self.velocity = (vx2 * k, vy2 * k)


        # --- Torque ---
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


    def take_temperature_damage(self, dt: float):
        temperature_difference = self.current_temperature_c - self.maximum_operating_temperature_c
        if temperature_difference > 0:
            # Calculate damage based on the temperature difference and time
            damage = temperature_difference * dt * 0.01
            self.hull_integrity -= damage

    def check_destroyed(self):
        if self.hull_integrity < 0:
            print(f"Vessel {self.object_id} destroyed due to hull integrity failure.")
            self.destroy()

    def destroy(self):
        """Destroy the vessel, removing it cleanly from controller, agency, and chunk."""
        print(f"Vessel {self.object_id} destroyed.")

        # 🔔 Notify everyone in the same chunk via UDP (send before removing from chunk)
        try:
            udp = getattr(self.shared, "udp_server", None)
            tcp = getattr(self.shared, "tcp_server", None)
            if udp and tcp and getattr(udp, "transport", None):
                # Build packet: [u8 opcode][u64 vessel_id]
                pkt = bytearray()
                pkt.append(DataGramPacketType.NOTIFY_VESSEL_DESTROYED)
                pkt += struct.pack('<Q', int(self.object_id))

                # Determine chunk coords
                chunk = getattr(self, "home_chunk", None)
                if chunk is None:
                    # fallback: resolve via chunk-manager index
                    cm = getattr(self.shared, "chunk_manager", None)
                    if cm:
                        chunk = cm.get_chunk_from_object_id(int(self.object_id))
                galaxy = getattr(chunk, "galaxy", None)
                system = getattr(chunk, "system", None)

                if galaxy is not None and system is not None:
                    for s in list(tcp.sessions):
                        if not s.alive:
                            continue
                        p = getattr(s, "player", None)
                        if not p:
                            continue
                        if getattr(p, "galaxy", None) == galaxy and getattr(p, "system", None) == system:
                            udp_port = getattr(s, "udp_port", None)
                            if udp_port:
                                addr = (s.remote_ip, udp_port)
                                udp.transport.sendto(pkt, addr)
        except Exception as e:
            print(f"⚠️ Failed to send NOTIFY_VESSEL_DESTROYED: {e}")

        # 1) Release control
        if self.controlled_by and self.controlled_by != 0:
            pid = self.controlled_by
            self.controlled_by = 0
            player = self.shared.players.get(pid)
            if player and getattr(player, "controlled_vessel_id", None) == self.object_id:
                player.controlled_vessel_id = -1
            # (optional) notify clients via TCP about control release

        # 2) Remove from agency
        agency = self.shared.agencies.get(self.agency_id)
        if agency is not None:
            try:
                stranded = len(getattr(self, "astronauts_onboard", []) or [])
                if stranded > 0 and hasattr(agency, "record_stat_counter"):
                    agency.record_stat_counter("stranded_astronauts", stranded)
            except Exception as e:
                print(f"⚠️ stranded astronaut stat update failed: {e}")
            agency.remove_vessel(self)

        # 3) Remove from chunk (and id map)
        if self.home_chunk is not None:
            self.home_chunk.remove_object(self)

        # 4) Guard against stray ticks
        self.mass = 0.0
        self.components.clear()

        self.signal_vessel_destroyed()


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

            # How aggressively the hardware can pull toward target.
            # Larger 'amount' -> faster (as you already had).
            tau_reg = max(1e-3, 60.0 / float(reg.amount))
            alpha_reg = 1.0 - math.exp(-dt / tau_reg)

            # 1) How much work do we *want* to do this tick, 0..1?
            #    - deadband: ignore tiny errors to avoid chatter.
            #    - gain: how quickly effort rises with |error|.
            deadband_c = 3                    # no work inside ±3C
            gain_per_deg = 1.0 / 60.0            # ~60°C error => full effort
            error_c = target_c - self.current_temperature_c
            err_mag = abs(error_c)
            if err_mag <= deadband_c:
                requested_effort = 0.0
            else:
                requested_effort = min(1.0, (err_mag - deadband_c) * gain_per_deg)

            # 2) Power needed scales with requested effort.
            #    Interpret reg.power_draw as the *max* draw at 100% effort.
            max_draw_per_sec = max(0.0, float(reg.power_draw))
            needed_power = max_draw_per_sec * requested_effort * dt

            # 3) Try to pay that power; compute what fraction we could actually afford.
            power_fraction = 1.0
            if needed_power > 0.0:
                before = self.power
                _ = self._draw_power(needed_power)   # may partially succeed
                used = max(0.0, before - self.power)
                power_fraction = 0.0 if needed_power <= 0.0 else min(1.0, used / needed_power)

            # 4) Apply cooling/heating scaled by *actual* effort we could power.
            actual_effort = requested_effort * power_fraction
            if actual_effort > 0.0:
                self.current_temperature_c += (error_c) * (alpha_reg * actual_effort)



from collections import deque
import heapq

def calculate_component_stages(components, connections, component_data_lookup, payload_index=None):
    """
    Stage assignment (cycle-safe):
      - Payload is stage 0.
      - stage-add      : added when ENTERING a node (that node belongs to the higher stage).
      - stage-pre-add  : added when LEAVING a node (that node stays lower; successors get bumped).
      - Stage for a node = MIN cumulative sum of (pre-add on edges traversed so far) + (add on the node).
      - Disconnected parts default to stage 1 (except payload which stays 0).
    """
    n = len(components)
    if n == 0:
        return []

    # --- find payload index (expects attributes['is-payload'])
    if payload_index is None:
        for i, comp in enumerate(components):
            attrs = (component_data_lookup.get(comp.id, {}) or {}).get("attributes", {}) or {}
            if attrs.get("is-payload"):
                payload_index = i
                break
        if payload_index is None:
            payload_index = 0

    # --- undirected adjacency (bounds-checked)
    adj = [[] for _ in range(n)]
    for a, b in connections:
        try:
            a = int(a); b = int(b)
        except Exception:
            continue
        if 0 <= a < n and 0 <= b < n and a != b:
            adj[a].append(b)
            adj[b].append(a)

    # --- helpers
    def stage_add(idx: int) -> int:
        cd = component_data_lookup.get(components[idx].id, {}) or {}
        attrs = cd.get("attributes", {}) or {}
        bump = int(attrs.get("stage-add", 0))
        if bump <= 0:
            typ = str(cd.get("type", "")).lower()
            # keep your inference for fairings/decouplers etc.
            if typ in ("fairing", "separator", "decoupler", "coupler"):
                bump = 1
        return max(0, bump)

    def stage_pre_add(idx: int) -> int:
        cd = component_data_lookup.get(components[idx].id, {}) or {}
        attrs = cd.get("attributes", {}) or {}
        pre = int(attrs.get("stage-pre-add", 0))
        return max(0, pre)

    # --- Dijkstra with edge weight: pre_add(u) + add(v)
    INF = 10**9
    dist = [INF] * n
    dist[payload_index] = 0
    heap = [(0, payload_index)]

    while heap:
        d, u = heapq.heappop(heap)
        if d != dist[u]:
            continue
        pu = stage_pre_add(u)
        for v in adj[u]:
            cand = d + pu + stage_add(v)
            if cand < dist[v]:
                dist[v] = cand
                heapq.heappush(heap, (cand, v))

    # --- produce stages; disconnected -> 1 (payload stays 0)
    stages = []
    for i in range(n):
        if dist[i] == INF:
            stages.append(0 if i == payload_index else 1)
        else:
            stages.append(int(dist[i]))
    return stages



def construct_vessel_from_request(shared, player, vessel_request_data) -> Vessel:
    # Grab the caller's Steam ID once, robustly (you use both names in code)
    steam_id = int(getattr(player, "steamID", getattr(player, "steam_id", 0)) or 0)

    try:
        # --- existing code starts here (unchanged logic) ---
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

        # --- accumulate required resources across all components ---
        required_resources: Dict[int, int] = {}

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

            # read and sum resource_cost per component
            rc = _coerce_int_keys(component_definition.get("resource_cost", {}))
            for r_id, amt in rc.items():
                try:
                    need = int(amt)
                except Exception:
                    continue
                if need <= 0:
                    continue
                required_resources[r_id] = required_resources.get(r_id, 0) + need

        # Money check
        if player.money < total_cost:
            raise ValueError(f"Insufficient funds: cost={total_cost}, player has={player.money}")

        # Resource availability check on the launch planet
        agency = shared.agencies.get(player.agency_id)
        if agency is None:
            raise ValueError(f"No agency found for player agency_id={player.agency_id}")

        if required_resources:
            base_inventories = getattr(agency, "base_inventories", None)
            if not isinstance(base_inventories, dict):
                raise ValueError("Agency has no base_inventories dictionary; cannot validate resources.")

            planet_inventory_raw = base_inventories.get(planet_id)
            if planet_inventory_raw is None:
                raise ValueError(f"No base inventory found on planet {planet_id}; cannot construct here.")

            planet_inventory = _coerce_int_keys(planet_inventory_raw)

            shortages = []
            for r_id, need in required_resources.items():
                have = int(planet_inventory.get(r_id, 0))
                if have < need:
                    shortages.append((r_id, need, have))

            if shortages:
                detail = ", ".join([f"{rid}: need {need}, have {have}" for rid, need, have in shortages])
                raise ValueError(f"Insufficient resources on planet {planet_id}: {detail}")

            # deduct resources
            for r_id, need in required_resources.items():
                planet_inventory[r_id] = int(planet_inventory.get(r_id, 0)) - need
            base_inventories[planet_id] = planet_inventory

        # Deduct money and create vessel
        player.money -= total_cost
        print("Creating vessel")
        payload_idx = detect_payload_index(components, component_data_lookup)

        # 1) Pull attributes and prepare helpers
        attrs_by_idx = []
        for comp in components:
            comp_def = component_data_lookup.get(comp.id, {}) or {}
            attrs_by_idx.append((comp_def.get("attributes", {}) or {}))

        def has_stage_add(idx: int) -> bool:
            return bool(attrs_by_idx[idx].get("stage-add"))

        # 2) Build adjacency from connections (pairs of component indices)
        from collections import defaultdict, deque
        adj = defaultdict(list)
        for a, b in connections:
            adj[a].append(b)
            adj[b].append(a)

        # 3) Root at your “center” (components[0]) and compute graph distances
        root = 0
        dist = {root: 0}
        dq = deque([root])
        while dq:
            u = dq.popleft()
            for v in adj.get(u, []):
                if v not in dist:
                    dist[v] = dist[u] + 1
                    dq.append(v)
        # Components not reached get a big distance so they lose ties predictably
        INF = 10**9
        for i in range(len(components)):
            if i not in dist:
                dist[i] = INF

        # 4) Decide which endpoints to suppress on each stage-add/stage-add edge
        suppress = set()
        for a, b in connections:
            if has_stage_add(a) and has_stage_add(b):
                da, db = dist[a], dist[b]
                if da == db:
                    # Tie: pick the lower index as the parent, suppress the other
                    parent, child = (a, b) if a < b else (b, a)
                elif da < db:
                    parent, child = a, b
                else:
                    parent, child = b, a
                suppress.add(child)

        # 5) Create a shallow, call-scoped copy of the lookup with masked flags
        #    (so we don’t mutate the global shared.component_data)
        masked_lookup = {}
        for comp in components:
            base_def = component_data_lookup.get(comp.id, {}) or {}
            # shallow copies
            new_def = dict(base_def)
            new_attrs = dict(new_def.get("attributes", {}) or {})
            # If this instance (by index) is suppressed, force stage-add False
            idx = components.index(comp)  # safe here; components are unique instances
            if idx in suppress and new_attrs.get("stage-add"):
                new_attrs["stage-add"] = False
            new_def["attributes"] = new_attrs
            masked_lookup[comp.id] = new_def

        # 6) Now compute stages using the masked lookup
        stages = calculate_component_stages(components, connections, masked_lookup, payload_idx)
        for comp, st in zip(components, stages):
            setattr(comp, "stage", st)

        # Quest: mark any vessel that includes strap-on boosters
        try:
            from vessel_components import Components
        except Exception:
            Components = None
        has_strap_on = False
        if Components:
            try:
                has_strap_on = any(int(getattr(comp, "id", 0)) == int(getattr(Components, "STRAP_ON_BOOSTER", 29)) for comp in components)
            except Exception:
                has_strap_on = False
        if has_strap_on and hasattr(agency, "record_quest_metric"):
            agency.record_quest_metric("strap_on_vessels", 1)


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
        vessel.shared = shared
        vessel.name = vessel_name
        vessel.launchpad_planet_id = planet_id
        vessel.stage, vessel.num_stages = max(stages), max(stages) + 1

        if payload_idx is not None:
            vessel.payload = components[payload_idx].id
        else:
            vessel.payload = 0  # or some sentinel meaning "no payload"

        vessel.calculate_vessel_stats()

        vessel._ensure_payload_behavior()


        # Add vessel to its agency
        agency = shared.agencies.get(player.agency_id)
        if agency is not None:
            agency.vessels.append(vessel)
            if hasattr(agency, "record_stat_counter"):
                agency.record_stat_counter("vessels_launched", 1)
            print(f"✅ Vessel {vessel.object_id} added to Agency {agency.id64}")
        else:
            print(f"⚠️ No agency found with ID {player.agency_id}, vessel not tracked.")

        chunk_key = (player.galaxy, player.system)
        chunk = shared.chunk_manager.loaded_chunks.get(chunk_key)
        vessel.home_chunk = chunk
        vessel.home_planet = chunk.get_object_by_id(planet_id)

        # Initialize launchpad lock
        vessel.landed = True
        vessel.altitude = 0.0
        vessel.landed_angle_offset = float(launchpad_angle)
        if vessel.home_planet:
            R = float(vessel.home_planet.radius_km)
            world_angle_deg = vessel.home_planet.rotation + vessel.landed_angle_offset
            ang = math.radians(world_angle_deg)
            cx, cy = vessel.home_planet.position

            vessel.position = (cx + R * math.cos(ang), cy + R * math.sin(ang))
            vessel.rotation = world_angle_deg
            vessel.rotation_velocity = 0.0
            vessel.velocity = vessel.home_planet.velocity

        if chunk is not None:
            chunk.add_object(vessel)
            print(f"Vessel {vessel.object_id} added to chunk {chunk_key}")
        else:
            print(f"⚠️ No chunk found for galaxy/system {chunk_key}, vessel not added to chunk.")

        # --- SUCCESS: notify only the requesting player (type 2) ---
        _notify_player_udp(shared, steam_id, 2, f"{vessel.name} successfully constructed. ")

        return vessel

    except Exception as e:
        # --- FAILURE: notify only the requesting player (type 1) ---
        _notify_player_udp(shared, steam_id, 1, f"Construction failed: {e}")
        raise

def detect_payload_index(components, component_data_lookup) -> Optional[int]:
    """
    Return the index of the component whose attributes['is-payload'] is truthy.
    If none exist, return None.
    """
    for i, comp in enumerate(components):
        attrs = (component_data_lookup.get(comp.id, {}) or {}).get("attributes", {}) or {}
        if attrs.get("is-payload"):
            return i
    return None
