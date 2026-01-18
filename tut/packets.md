# Packets

Conventions
- All integer fields are little-endian unless noted.
- TCP packets use a 2-byte PacketType opcode first.
- UDP packets use a 1-byte DataGramPacketType opcode first.
- Strings are UTF-8 and NUL-terminated (cstring) when noted.

## TCP packets (PacketType)

### REQUEST_STEAM_INFO (0x0000) — client -> server
Payload: u64 steam_id.
Server action: registers/rebinds Player, sends agency snapshot, broadcasts PLAYER_JOIN, sends INFO_ABOUT_PLAYERS and GAME_JSON.

### TCP_SERVER_TELL_CONNECTED_PLAYERS (0x0001) — client -> server
Payload: 32 bytes (read and ignored). Logged as unknown.

### CHAT_MESSAGE_RELAY (0x0002) — client -> server; server -> clients
Client payload: u8 msg_type + cstring message.
Server relay payload: u16 opcode + u8 msg_type + u64 sender_steam_id + cstring message.
Routing: GLOBAL to all sessions; AGENCY to same agency; others ignored.

### KEEPALIVE (0x0003) — client -> server
Payload: none. Currently ignored.

### PLAYER_JOIN (0x0004) — server -> clients
Payload: u64 steam_id.
Sent when a validated session joins.

### PLAYER_LEAVE (0x0005) — server -> clients
Payload: u64 steam_id.
Sent when a session closes.

### INFO_ABOUT_PLAYERS (0x0006) — server -> clients
Payload: u8 count, then per player:
- u64 steam_id
- u8 temp_id
- u32 galaxy
- u32 system
- u64 agency_id
Sent on join and via refresh broadcasts.

### INFO_ABOUT_AGENCIES (0x0007) — server -> clients
Payload: u32 json_len + json blob.
JSON includes a list of agencies and members (see `ControlServer._build_info_about_agencies_blob`).

### CREATE_AGENCY (0x0008) — client -> server; server -> client
Client payload: u8 is_public + cstring name.
Server response payload: u8 error_code (0 = ok, 1 = name exists).

### AGENCY_DETAILS (0x0009) — not implemented
Defined in enum but no send/receive logic.

### PLAYER_DETAILS (0x000A) — not implemented
Defined in enum but no send/receive logic (UDP uses PLAYER_DETAILS_UDP instead).

### LEAVE_AGENCY (0x000B) — client -> server
Payload: none. Removes player from current agency and sends notifications.

### LIST_OF_AGENCIES (0x000C) — client -> server; server -> clients
Server payload: u16 count, then per agency:
- u64 agency_id
- u8 is_public
Sent on request and every 30s to all sessions.

### JOIN_PUBLIC_AGENCY (0x000D) — client -> server
Payload: u64 agency_id. Validates public agency and moves player.

### AGENCY_GAMESTATE (0x000E) — server -> client
Payload: u32 json_len + json blob (see `Agency.generate_gamestate_packet`).
Sent every second to each session with a valid agency.
Includes `quest_state` in the JSON: per-quest progress/target/completed/claimed keyed by quest id.

### GAME_JSON (0x000F) — server -> client
Payload: u32 json_len + `game_desc.json` contents (minified).
Sent after REQUEST_STEAM_INFO.

### CONSTRUCT_BUILDING (0x0010) — client -> server
Payload: u64 planet_object_id + u16 building_type + u16 position_angle.
Server applies cost and spawns a Building, notifies via UDP.

### CONSTRUCT_VESSEL (0x0011) — client -> server
Payload: cstring JSON vessel request.
Server constructs the vessel if valid (no direct TCP reply).

### RESOLVE_VESSEL_REPLY (0x0012) — server -> client
Payload layout (same as FORCE_RESOLVE_VESSEL):
- u64 vessel_id
- cstring name
- u16 num_stages
- u16 stage
- u16 seats_capacity
- u16 component_count
- [component_count x (u16 comp_id, i16 x, i16 y, u16 stage, u16 paint1, u16 paint2)]
Sent over TCP in response to UDP RESOLVE_VESSEL.

### VESSEL_CONTROL (0x0013) — client -> server; server -> clients
Client payload: u64 vessel_id + u8 action_key + optional payload:
- SET_TELESCOPE_TARGET_ANGLE: f32 angle
- SET_SYSTEM_STATE: u16 system_id + u8 state
Server broadcast payload: u64 vessel_id + u64 controller_steam_id (on REQUEST_CONTROL).

### FORCE_RESOLVE_VESSEL (0x0014) — server -> clients
Payload: same layout as RESOLVE_VESSEL_REPLY.
Sent when components/building data reload or vessel stats change.

### UPGRADE_BUILDING (0x0015) — client -> server
Payload: u64 planet_id + u16 building_type + u16 target_level.

### SELL_RESOURCE (0x0016) — client -> server
Payload: u16 resource_type + u16 count + u64 from_planet_id.

### CRAFT_RESOURCES (0x0017) — client -> server
Payload: u16 building_type + u64 planet_id + cstring recipe_name.

## UDP packets (DataGramPacketType)

### LATENCY_LEARN_PORT (0x00) — client -> server; server -> client
Client payload: opcode only. Server binds (ip, port) to session and replies with opcode only.

### PLAYER_DETAILS_UDP (0x01) — server -> clients
Payload: u8 count, then per player:
- u8 temp_id
- u64 money
Sent at ~60 Hz.

### UDP_ASK_ABOUT_AGENCY (0x02) — client -> server; server -> client
Client payload: u64 agency_id.
Server response: u64 agency_id + cstring name + u8 is_public.

### OBJECT_STREAM (0x03) — server -> clients
Payload:
- u16 seq
- u16 object_count
- [object_count x (u64 object_id, u64 pos_x_signed, u64 pos_y_signed, f32 vx, f32 vy, f32 rotation)]
Sent every tick to players in the same (galaxy, system).

### OBJECT_INQUIRY (0x04) — client -> server; server -> client
Client payload: u16 count + [count x u64 object_id].
Server response: u16 count + [count x (u64 object_id, u16 object_type)].

### RESOLVE_VESSEL (0x05) — client -> server
Client payload: u64 vessel_id.
Response is TCP RESOLVE_VESSEL_REPLY.

### VESSEL_STREAM (0x06) — server -> clients
Payload:
- u64 vessel_id
- u64 agency_id
- u64 lifetime_revenue
- u8 forward_thrust_on
- u8 reverse_thrust_on
- u8 ccw_thrust_on
- u8 cw_thrust_on
- f32 altitude
- u64 home_planet_id
- f32 home_planet_atmosphere_km
- u64 strongest_gravity_source_id
- f32 strongest_gravity_force
- u8 landed
- f32 landing_progress
- f32 z_velocity (km/s; negative = descending)
- f32 hull_integrity
- f32 liquid_fuel_kg
- f32 liquid_fuel_capacity_kg
- u16 cargo_capacity
- f32 power
- f32 power_capacity
- f32 solar_charging_efficiency
- f32 max_operating_temp_c
- f32 current_temp_c
- f32 ambient_temp_k
- u16 stage
- u8 deployment_ready
- f32 planet_income_multiplier
- u16 system_count
- [system_count x (u16 system_type, u8 active)]
- u8 astronaut_count
- [astronaut_count x u32 astronaut_id]
Sent every physics tick to all UDP sessions.

### REGION_CUE (0x07) — server -> client
Payload: u64 vessel_id + u32 region_id.
Used to trigger audio cues for a vessel controller.

### TELESCOPE_SIGHT (0x08) — server -> client
Payload (as sent by payload behavior):
- u64 vessel_id
- f32 fov_deg
- u16 count
- [count x u64 object_id]
Note: `StreamingServer.build_telescope_sight_packet` exists but is unused and omits fov_deg.

### NOTIFICATION (0x09) — server -> client(s)
Payload: u8 kind + cstring message.
Kinds observed: 0 = generic, 1 = failure, 2 = success.

### NOTIFY_VESSEL_DESTROYED (0x0A) — server -> clients
Payload: u64 vessel_id.
Sent to players in the same (galaxy, system) when a vessel is destroyed.

### VESSEL_UPGRADE_TREE (0x0B) — server -> client(s)
Payload:
- u64 vessel_id
- u16 unlocked_count + [unlocked_count x u16 upgrade_id]
- u16 purch_count + [purch_count x (u16 upgrade_id, u64 cost)]
Sent to controller or agency as upgrade tree updates.

### REQUEST_VESSEL_TREE_UPGRADE (0x0C) — client -> server
Payload: u64 vessel_id + u16 upgrade_id.
Server replies with NOTIFICATION, VESSEL_UPGRADE_TREE, and PLAYER_DETAILS_UDP.

### BOARD_ASTRONAUT (0x0D) — client -> server
Payload: u32 astronaut_id + u64 vessel_id.
Server replies via NOTIFICATION.

### UNBOARD_ASTRONAUT (0x0E) — client -> server
Payload: u32 astronaut_id + u64 vessel_id.
Server replies via NOTIFICATION.

### CHANGE_ASTRONAUT_SUIT (0x0F) — client -> server
Payload: u32 astronaut_id + u16 suit_id.
Server replies via NOTIFICATION.

### CHANGE_ASTRONAUT_NAME (0x17) — client -> server
Payload: u32 astronaut_id + cstring new_name.
Server replies via NOTIFICATION. Name is trimmed to 32 characters, and the client should expect updates in AGENCY_GAMESTATE.

### MAGNETOMETER_FIELD (0x10) — server -> client
Payload:
- u64 vessel_id
- f32 net_dir_deg
- f32 net_strength
- u8 sample_count
- [sample_count x (u64 body_id, f32 dir_deg, f32 strength, u8 flags)]
Sent at ~5 Hz while controlled and powered.

### GET_JETTISON (0x11) — client -> server; server -> client
Client payload: u64 object_id.
Server response: u64 object_id + u16 component_id (0 if unknown).

### CARGO_ADD (0x12) — client -> server
Payload: u64 vessel_id + u64 planet_id + u16 n + [n x (u32 resource_id, u32 amount)].
Server replies with CARGO_STATE and optional NOTIFICATION.

### CARGO_REMOVE (0x13) — client -> server
Payload: u64 vessel_id + u64 planet_id + u16 n + [n x (u32 resource_id, u32 amount)].
Server replies with CARGO_STATE and optional NOTIFICATION.

### CARGO_STATE (0x14) — client -> server; server -> client
Client payload: u64 vessel_id.
Server response:
- u64 vessel_id
- u64 planet_id
- u16 cap
- u16 used
- u16 item_count
- [item_count x (u32 resource_id, u32 amount)]

### SIGNAL_DESTROY (0x15) — server -> clients
Payload: u64 object_id.
Used to signal destruction/expiry of objects (vessels or jettisoned parts).

### XP_ORB (0x16) — server -> clients
Payload:
- u8 point_type (0=rp, 1=ep, 2=pp, 3=xp)
- u8 source_kind (0=vessel, 1=building)
- if source_kind == 0:
  - u64 vessel_id
- if source_kind == 1:
  - u64 planet_id
  - u16 building_type
One-shot event to spawn client-side XP orbs.

### CHANGE_ASTRONAUT_NAME (0x17) — client -> server
Payload: u32 astronaut_id + cstring new_name.
Server replies via NOTIFICATION. Name is trimmed to 32 characters, and the client should expect updates in AGENCY_GAMESTATE.

### CAMERA_CONTEXT (0x18) — client -> server
Payload: i64 cam_x + i64 cam_y (world coordinates).
Purpose: lets the server classify the camera position into a region and return the current in-game day. Client should rate-limit (~0.5–1.0s) to avoid spam.

### CAMERA_CONTEXT_REPLY (0x19) — server -> client
Payload: u8 region_id + f32 game_day.
Region id uses the Region enum (e.g., SPACE, EARTH_NEAR, MOON_NEAR, etc.). `game_day` reflects the agency’s `age_days`.

### REGION_NAME_REQUEST (0x20) — client -> server; server -> client
Client payload: u8 region_id (previously unseen).
Server response: u8 region_id + cstring region_name (enum name; "UNKNOWN_REGION" if unmapped).
Use when the client encounters a new region and wants the human-readable label.
