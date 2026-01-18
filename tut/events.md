# Server events

## A) When the server starts

1. `main.py` parses `config.txt` into server settings, game defaults, and game data paths.
2. `HttpClient` is created and a listing-server healthcheck is attempted.
3. `ServerMissionControl` is constructed:
   - Loads `game_desc.json` and builds component/building/resource tables.
   - Initializes global state (players, agencies, ports, rates).
4. `ControlServer` is created and `activate()` runs:
   - Marks server active.
   - Schedules background tasks: `loop_tasks()` (every-second updates, agency list broadcast) and `watch_game_desc()` (live reload).
5. Listing updates begin via `update_listing_server()` (posts every 10 seconds).
6. TCP server task starts (`ControlServer.start()`), accepting new sessions.
7. `StreamingServer` (UDP) is created and started:
   - Binds UDP socket.
   - Starts `_broadcast_loop()` (PLAYER_DETAILS_UDP at ~60 Hz).
8. `Game` is constructed in a worker thread:
   - Creates a `ChunkManager`, which starts tick and autosave threads.
   - Performs Big Bang if no save exists.
   - Loads the home chunk (galaxy 1, system 1).
   - Loads/synchronizes object id sequencing.
9. The asyncio loop awaits TCP server, UDP server, and listing update tasks.

## B) When a player joins

1. TCP connection arrives; `ControlServer.handle_client()` creates a `Session` and assigns a temp_id.
2. The session waits for packets in `read_and_process_packet()`.
3. Client sends `REQUEST_STEAM_INFO` (u64 steam_id).
4. Server registers or rebinds a `Player`:
   - Binds `session.player` and stores in `shared.players`.
   - Sends INFO_ABOUT_AGENCIES JSON snapshot to the joining session.
5. Server broadcasts `PLAYER_JOIN` and a `CHAT_MESSAGE_RELAY` (PLAYERJOIN).
6. Server sends `INFO_ABOUT_PLAYERS` to the joining session, then `GAME_JSON`.
7. Client sends UDP `LATENCY_LEARN_PORT`; UDP server associates the (ip, port) with the session and replies.
8. Ongoing periodic updates begin for the player:
   - `ControlServer.every_second()` sends `AGENCY_GAMESTATE` (if the player has an agency).
   - `ControlServer.send_list_of_agencies_every_30_seconds()` broadcasts `LIST_OF_AGENCIES`.
   - `StreamingServer._broadcast_loop()` sends `PLAYER_DETAILS_UDP` at ~60 Hz.
   - `ChunkManager` ticks physics and streams `OBJECT_STREAM` and `VESSEL_STREAM` over UDP.
