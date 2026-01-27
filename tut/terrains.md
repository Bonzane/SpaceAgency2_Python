# Terrain Chunks

Terrain chunks are 2D surface layers for planets. They do not participate in the main physics simulation and are stored as JSON files alongside the system chunk. Objects in a terrain chunk may also exist in the parent system chunk (tracked via `shared_object_ids`).

## Entering a terrain chunk (client flow)
1) Client sends TCP `ENTER_TERRAIN` (0x0019) with `u64 planet_id` + `u64 last_hash`.
2) Server validates:
   - player has a valid agency,
   - the planet object is loaded and in the same galaxy/system as the player,
   - the planet is discovered by that agency,
   - the agency has at least one astronaut on the planet or a landed vessel on the planet.
3) On success, server sets `player.terrain_planet_id`, ensures the terrain chunk exists, and replies with the terrain JSON blob (unless `last_hash` matches the current terrain hash).
4) Server broadcasts `INFO_ABOUT_PLAYERS` so other clients can see the player's terrain location.

On failure, the server replies with an error code and no JSON payload.

### ENTER_TERRAIN_REPLY payload
- `u8 error_code` (0 = ok)
- `u64 planet_id`
- `u64 terrain_hash`
- `u32 json_len` (0 on error or hash match)
- `json_len` bytes of terrain JSON (only when `error_code == 0`)

Error codes:
- 0: ok
- 1: no_player_or_agency
- 2: planet_not_loaded
- 3: not_in_system
- 4: not_a_planet
- 5: not_discovered
- 6: no_presence

## Exiting a terrain chunk
1) Client sends TCP `EXIT_TERRAIN` (0x001B) with no payload.
2) Server clears `player.terrain_planet_id` and broadcasts `INFO_ABOUT_PLAYERS`.
3) Server replies with `EXIT_TERRAIN_REPLY`.

### EXIT_TERRAIN_REPLY payload
- `u8 error_code` (0 = ok)
- `u64 planet_id` (previous terrain planet, or 0 on failure)

Error codes:
- 0: ok
- 1: no_player_or_agency
- 2: not_in_terrain

## Player location updates
`INFO_ABOUT_PLAYERS` now includes `u64 terrain_planet_id` after the `system` field. Use `0` for "not on terrain."

The server still streams system `OBJECT_STREAM` packets to players in the same (galaxy, system). Clients should ignore or deprioritize system object updates while in terrain mode.

## Terrain chunk file location
`universe/galaxies/{g}/systems/system_{s}/terrains/planet_{planet_id}.terrain`

## Terrain JSON schema
Top-level fields:
- `version` (int)
- `galaxy` (int)
- `system` (int)
- `planet_id` (int)
- `planet_name` (string)
- `shared_object_ids` (array of u64)
- `entities` (array)

Each entity:
```json
{
  "id": 123,
  "kind": "astronaut",
  "x": 10.5,
  "y": -4.0,
  "data": { "any": "extra fields" }
}
```
