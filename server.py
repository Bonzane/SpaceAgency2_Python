import requests 
import socket
import asyncio
from session import Session
from player import Player
from agency import Agency
import agency
from packet_types import PacketType, DataGramPacketType, ChatMessage
from typing import Iterable, Sequence, Set, Dict, Tuple
import aiohttp
import struct
import os, hashlib, copy, json
import time


class HttpClient:
    def __init__(self):
        self.session = aiohttp.ClientSession()

    def listing_healthcheck(self, url):
        print(f"Performing health check on listing server: {url}")
        try:
            response = requests.get(url)
            return response.status_code
        except requests.RequestException as e:
            print(f"Request failed: {e}")
            return 0
        
    async def send_status_update(self, url: str, data: dict):
        try:
            async with self.session.post(url +"/api/createlisting", json=data) as response:
                response_text = await response.text()
                return response.status, response_text
        except aiohttp.ClientError as e:
            print(f"Status update failed: {e}")
            raise
    async def close(self):
        await self.session.close()


async def update_listing_server(shared_state, http_client, to_url):
    while True:
        try:
            #GATHER RELEVANT INFO



            #A LOT OF STUFF HERE IS BS'ed. I HOPE I AM NOT DUMB ENOUGH TO
            #FORGET TO COME BACK TO THIS
            data = {
                "host": "0.0.0.0",      # <- BS, and just to be honest with you, this doesn't do anything. 
                                        #  The official listing server ignores this, but if someone for some reason
                                        # wants to make their own listing server and allow you to create listings 
                                        #  from one computer for a server running somewhere else, they might choose to implement this.  
                "controlServerTCPPort" : shared_state.external_control_port ,
                "streamingServerUDPPort" : shared_state.external_streaming_port,
                "serverPublicName" : shared_state.server_public_name,
                "passwordProtected" : 0,       # <- BS
                "maxConnections" : 100,      # <- BS
                "selfReportedStatus" : 0,      # <- BS
                "currentPlayers" : 0,      # <- BS
                "inGameDay" : 0,      # <- BS
                "timeOfLastPlayerJoin" : 0      # <- BS
            }

            print(f"🌐 Posting Listing")
            status_code, response_text = await http_client.send_status_update(to_url, data)
            print(f"Listing server responded with {status_code}: {response_text}")

        except Exception as e:
            print(f"⚠️ Failed to update listing server: {e}")

        await asyncio.sleep(10)  # Wait 10 seconds before next update   


# Global Server State
class ServerMissionControl:

    def __init__(self, admins):
        self.game_desc_path = "game_desc.json"
        self.admins = admins
        self.players: Dict[int, Player] = {}
        self.agencies: Dict[int, Agency] = {}
        self.server_public_name = None
        self.server_public_status = 1
        self.max_players = None
        self.host = "0.0.0.0"
        self.control_port = None
        self.streaming_port = None
        self.external_control_port = None
        self.external_streaming_port = None
        self.component_data = None
        self.next_available_agency_id = 5
        self.udp_server = None
        self.tcp_server = None
        self.udp_endpoint_to_session: Dict[Tuple[str, int], Session] = {}
        self.chunk_manager = None
        self.gamespeed = 2920
        self.tickrate = 60
        self.global_thrust_multiplier = 0.2
        self.player_starting_cash = int(200000)
        self.base_cash_per_second = 200
        self.game_description = None
        self.game_buildings_list = None
        self.buildings_by_id = None
        self.agency_default_attributes = None
        self.server_global_cash_multiplier = 1.0
        self.game = None
        self.game_resources = None
        self.resource_transfer_rates: dict[int, int] = {}
        self.resource_names: list[str] = []
        with open(self.game_desc_path, "r") as game_description_file:
            self.game_description = json.load(game_description_file)
            self.game_buildings_list = self.game_description.get("buildings")
            self.component_data = {
                comp["id"]: comp for comp in self.game_description["components"]
            }
            self.buildings_by_id = {b["id"]: b for b in self.game_buildings_list}
            self.agency_default_attributes = self.game_description.get("agency_default_attributes", {})
            self.game_resources = self.game_description.get("resources", [])

        for idx, item in enumerate(self.game_resources):
            name, rate = None, 0
            # support your canonical ["Name", rate] as well as a dict fallback
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                name = str(item[0])
                try:
                    rate = int(item[1])
                except (TypeError, ValueError):
                    rate = 0
            elif isinstance(item, dict):
                # optional compatibility if format ever changes
                name = str(item.get("name", f"Resource#{idx}"))
                try:
                    rate = int(item.get("rate", 0))
                except (TypeError, ValueError):
                    rate = 0
            else:
                name = f"Resource#{idx}"
                rate = 0

            self.resource_names.append(name)
            self.resource_transfer_rates[idx] = max(0, rate)

            self._reload_lock = asyncio.Lock()
            try:
                st = os.stat(self.game_desc_path)
                self._game_desc_mtime = st.st_mtime
            except FileNotFoundError:
                self._game_desc_mtime = 0.0
            self._game_desc_hash = self._hash_file(self.game_desc_path)

    def _hash_file(self, path: str) -> str:
        try:
            with open(path, "rb") as f:
                return hashlib.sha256(f.read()).hexdigest()
        except Exception:
            return ""
        
    async def watch_game_desc(self, interval: float = 2.0):
        """
        Periodically checks game_desc.json; if changed, reloads safely and
        updates dependent state live.
        """
        path = self.game_desc_path
        while True:
            try:
                st = os.stat(path)
                mtime = st.st_mtime
                if mtime != self._game_desc_mtime:
                    new_hash = self._hash_file(path)
                    # protect against quick-save tools that bump mtime without content change
                    if new_hash != self._game_desc_hash:
                        print("🔄 Detected change in game_desc.json; reloading...")
                        # Read & parse atomically under lock; only swap if parse succeeds
                        async with self._reload_lock:
                            with open(path, "r", encoding="utf-8") as f:
                                data = json.load(f)
                            # minimal validation
                            if "components" not in data or "buildings" not in data:
                                raise ValueError("game_desc.json missing 'components' or 'buildings'")
                            self._apply_game_desc(data)
                            self._recompute_after_reload()
                            self._game_desc_mtime = mtime
                            self._game_desc_hash = new_hash
                            print("✅ Live reload applied.")
            except Exception as e:
                # Never kill the loop; just log and keep the previous config
                print(f"⚠️ game_desc.json watch error: {e}")
            await asyncio.sleep(interval)


    def _apply_game_desc(self, data: dict):
        """
        Swap in new data-driven tables and recompute derived caches.
        This only runs after JSON has parsed successfully.
        """
        # 1) Swap the raw description and primary lookups
        self.game_description = data
        self.game_buildings_list = list(data.get("buildings", []))
        self.component_data = {int(c["id"]): c for c in data.get("components", [])}
        self.buildings_by_id = {int(b["id"]): b for b in self.game_buildings_list}
        self.agency_default_attributes = dict(data.get("agency_default_attributes", {}))
        self.game_resources = list(data.get("resources", []))

        # 2) Recompute resource names/rates (keeps your existing behavior)
        self.resource_names.clear()
        self.resource_transfer_rates.clear()
        for idx, item in enumerate(self.game_resources):
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                name = str(item[0]); 
                try: rate = int(item[1])
                except (TypeError, ValueError): rate = 0
            elif isinstance(item, dict):
                name = str(item.get("name", f"Resource#{idx}"))
                try: rate = int(item.get("rate", 0))
                except (TypeError, ValueError): rate = 0
            else:
                name, rate = f"Resource#{idx}", 0
            self.resource_names.append(name)
            self.resource_transfer_rates[idx] = max(0, rate)

    def _recompute_after_reload(self):
        """
        Touch anything that depends on component/building defs.
        """
        # Recompute vessel stats and push a FORCE_RESOLVE to clients
        try:
            cm = self.chunk_manager
            if cm and getattr(cm, "loaded_chunks", None):
                for _ck, chunk in list(cm.loaded_chunks.items()):
                    for obj in list(getattr(chunk, "objects", {}).values()):
                        # avoid import cycles; rely on duck-typing
                        if hasattr(obj, "calculate_vessel_stats"):
                            obj.calculate_vessel_stats()
                            # let clients refresh their copy
                            if hasattr(obj, "_notify_force_resolve"):
                                obj._notify_force_resolve()
        except Exception as e:
            print(f"⚠️ post-reload vessel recompute failed: {e}")

        # Let agencies rebuild any derived attributes
        try:
            for ag in self.agencies.values():
                if hasattr(ag, "update_attributes"):
                    ag.update_attributes()
        except Exception as e:
            print(f"⚠️ post-reload agency attr update failed: {e}")



    def get_next_agency_id(self):
        current = self.next_available_agency_id
        self.next_available_agency_id += 1
        return current

    def set_public_name(self, new_name):
        self.server_public_name = new_name
    
    def set_host(self, new_ip_addr):
        self.host = new_ip_addr

    def set_control_port(self, newport):
        self.control_port = newport
    
    def set_streaming_port(self, newport):
        self.streaming_port = newport

    def set_control_port_extern(self, newport):
        self.external_control_port = newport
    
    def set_streaming_port_extern(self, newport):
        self.external_streaming_port = newport

    def get_resource_rate(self, resource_type: int) -> int:
        try:
            return int(self.resource_transfer_rates.get(int(resource_type), 0))
        except Exception:
            return 0

    def get_resource_name(self, resource_type: int) -> str:
        try:
            return self.resource_names[int(resource_type)]
        except Exception:
            return f"Resource#{resource_type}"
        



#TCP Server
# This server handles TCP packets. That means it handles 
# all the data that must be received and must be received in order. 
# Think of things like players joining, chat messages, players leaving, activating thruters, etc. 
# It does NOT handle streaming the positions of objects. Instead, that is the Streaming Server, which uses UDP.
class ControlServer: 
    def __init__(self,missioncontrol, listens_on_port):
        self.shared = missioncontrol
        self.port = listens_on_port
        self.active = False #The server must be "activated"
        self.sessions: Set[Session] = set()
        self.next_available_temp_id = 0

    #Marks the server as active and starts accepting connections
    def activate(self):
        print(f"🟢 Control Server Activated on port {self.port}")
        self.active = True
        asyncio.create_task(self.loop_tasks())  
        asyncio.create_task(self.shared.watch_game_desc(interval=2.0))

    #Starts the async loop that handles clients
    async def start(self):
        server = await asyncio.start_server(self.handle_client, '0.0.0.0', self.port)
        async with server:
            await server.serve_forever()

    async def loop_tasks(self):

        asyncio.create_task(self.send_list_of_agencies_every_30_seconds())
        asyncio.create_task(self.every_second())

    async def send_list_of_agencies_every_30_seconds(self):
        while True:
            await self.send_list_of_agencies()
            await asyncio.sleep(30)

    async def every_second(self):

        while True:
            #Generate player base income
            for _player in self.shared.players.values():
                _player.gain_money()
            
            #Generate agency-wide income
            for _agency in self.shared.agencies.values():
                _agency.generate_agency_income()
                #Update Buildings and their build time
                for _building in _agency.get_all_buildings():
                    _building.update()
                #Update agency attributes
                _agency.update_attributes()


            #Send the agency gamestates
            for session in list(self.sessions):
                if not session.alive or not hasattr(session, "player") or session.player is None:
                    continue

                agency_id = session.player.agency_id
                agency = self.shared.agencies.get(agency_id)

                if agency:
                    try:
                        packet = agency.generate_gamestate_packet()
                        await session.send(packet)
                    except Exception as e:
                        print(f"⚠️ Failed to send agency gamestate to session {session.temp_id}: {e}")

            await asyncio.sleep(1)

    async def broadcast_to_agency(self, agency_id: int, data: bytes) -> int:
        targets = [
            s for s in self.sessions
            if s.alive and hasattr(s, "player") and s.player
            and getattr(s.player, "agency_id", None) == agency_id
        ]
        if not targets:
            return 0
        await asyncio.gather(*(s.send(data) for s in targets))
        return len(targets)

    def _build_info_about_agencies_blob(self) -> bytes:
        payload = {
            "type": "agencies",
            "agencies": [ag.to_json() for ag in self.shared.agencies.values()],
        }
        return json.dumps(payload, separators=(",", ":")).encode("utf-8")

    async def send_info_about_agencies_to_session(self, session):
        if not session.alive:
            return
        blob = self._build_info_about_agencies_blob()
        pkt = bytearray()
        pkt += PacketType.INFO_ABOUT_AGENCIES.to_bytes(2, "little")  # 0x0007
        pkt += struct.pack("<I", len(blob))                          # u32 length
        pkt += blob
        await session.send(pkt)

    async def broadcast_info_about_agencies(self):
        blob = self._build_info_about_agencies_blob()
        pkt = bytearray()
        pkt += PacketType.INFO_ABOUT_AGENCIES.to_bytes(2, "little")
        pkt += struct.pack("<I", len(blob))
        pkt += blob
        await self.broadcast(pkt)

    async def handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        session = Session(reader, writer, self)
        self.sessions.add(session)
        try:
            print(f"[+] New connection from {session.remote_ip}, assigned temp ID {session.temp_id}. Awaiting Validation.")
            await session.start()

        except Exception as e:
            print(f"Error in session: {e}")
        finally:
            self.sessions.discard(session)
            writer.close()
            await writer.wait_closed()

    # Sends data to all connected clients
    async def broadcast(self, data: bytes):
        alive_sessions = [s for s in self.sessions if s.alive]
        print(f"📡 Broadcasting packet to {len(alive_sessions)} alive session(s).")
        await asyncio.gather(*(s.send(data) for s in alive_sessions))

    
    async def tell_everyone_player_joined(self, steam_id: int):
        packet = bytearray()
        packet += PacketType.PLAYER_JOIN.to_bytes(2, 'little')  # 2-byte function code
        packet += steam_id.to_bytes(8, 'little')                 # 8-byte Steam ID
        await self.broadcast(packet)

        chat_pkt = self._build_chat_packet(ChatMessage.PLAYERJOIN, steam_id, " has joined the game")
        await self.broadcast(chat_pkt)

    async def tell_everyone_player_left(self, steam_id: int):
        packet = bytearray()
        packet += PacketType.PLAYER_LEAVE.to_bytes(2, 'little')
        packet += steam_id.to_bytes(8, 'little')
        await self.broadcast(packet)


    def get_player_by_steamid(self, steamid: int) -> Player:
        return self.shared.players.get(steamid)


    def agency_with_name_exists(self, name: str) -> bool:
        return any(a.name == name for a in self.shared.agencies.values())

    def count_sessions(self) -> int:
        return len(self.sessions)

    def get_next_temp_id(self) -> int:
        temp_id = self.next_available_temp_id
        self.next_available_temp_id += 1
        return temp_id

    async def register_player(self, session):
        steam_id = session.steam_id
        player = self.get_player_by_steamid(steam_id)
        if not player:
            player = Player(session, steam_id, self.shared)
            self.shared.players[steam_id] = player
        else:
            # 👇 IMPORTANT: re-bind the player to the new session
            player.session = session

        session.player = player
        await self.send_info_about_agencies_to_session(session)

    def _build_chat_packet(self, msg_type: ChatMessage, sender_steam_id: int, text: str) -> bytes:
        pkt = bytearray()
        pkt += PacketType.CHAT_MESSAGE_RELAY.to_bytes(2, "little")  # u16 opcode
        pkt.append(int(msg_type))                                    # u8 chat type
        pkt += sender_steam_id.to_bytes(8, "little")                 # u64 sender
        pkt += text.encode("utf-8") + b"\x00"                        # NUL-terminated
        return pkt



    async def tell_everyone_info_about_everyone(self): 
        print(f"👥 Broadcasting {len(self.sessions)} player's information")
        packet = bytearray()
        packet += PacketType.INFO_ABOUT_PLAYERS.to_bytes(2, 'little')
        sessions = [s for s in self.sessions if s.alive]
        packet.append(len(sessions))
        for session in sessions:
            player = self.get_player_by_steamid(session.steam_id)

            if player:
                packet += struct.pack('<Q', session.steam_id)         # u64 Steam ID
                packet.append(session.temp_id)                        # u8 Temp ID
                packet += struct.pack('<II', player.galaxy, player.system)  # u32 galaxy, u32 system
                packet += struct.pack('<Q', player.agency_id)         # u64 Agency ID
            else:
                packet += struct.pack('<Q', 0)  # u64 = 0 means invalid player

        print(f"📦 INFO_ABOUT_PLAYERS packet bytes: {packet.hex()}")
        await self.broadcast(packet)

    async def tell_session_info_about_everyone(self, session):
        if not session.alive:
            print(f"⚠️ Session {session.temp_id} is not alive. Skipping info broadcast.")
            return

        valid_sessions = [
            s for s in self.sessions
            if s.alive and self.get_player_by_steamid(s.steam_id) is not None
        ]

        print(f"👤 Sending INFO_ABOUT_PLAYERS to session {session.temp_id} ({session.remote_ip})")
        print(f"🧮 Valid player sessions to include: {len(valid_sessions)}")

        packet = bytearray()
        packet += PacketType.INFO_ABOUT_PLAYERS.to_bytes(2, 'little')  # u16 function code
        packet.append(len(valid_sessions))                             # u8 player count

        for s in valid_sessions:
            player = self.get_player_by_steamid(s.steam_id)

            packet += struct.pack('<Q', s.steam_id)                    # u64 Steam ID
            packet.append(s.temp_id)                                   # u8 Temp ID
            packet += struct.pack('<II', player.galaxy, player.system) # u32 galaxy, u32 system
            packet += struct.pack('<Q', player.agency_id)              # u64 Agency ID

            print(f"🧑 Player: {s.steam_id} | TempID: {s.temp_id} | Galaxy: {player.galaxy}, System: {player.system} | Agency: {player.agency_id}")

        await session.send(packet)
        print(f"📨 Sent INFO_ABOUT_PLAYERS packet ({len(packet)} bytes) to session {session.temp_id}")



    async def send_list_of_agencies(self):
        packet = bytearray()

        # Packet header (2-byte function code for LIST_OF_AGENCIES)
        packet += PacketType.LIST_OF_AGENCIES.to_bytes(2, 'little')

        # Number of agencies as uint16
        num_agencies = len(self.shared.agencies)
        packet += struct.pack('<H', num_agencies)

        for agency_id, agency in self.shared.agencies.items():
            if agency:
                # uint64 agency ID
                packet += struct.pack('<Q', agency.id64)

                # uint8 public flag
                packet.append(1 if agency.is_public else 0)
            else:
                # Invalid agency: 64-bit zero
                packet += struct.pack('<Q', 0)

        # Send to all connected sessions
        await self.broadcast(packet)

    async def send_list_of_agencies_to_session(self, session):
        packet = bytearray()

        # Packet header (2-byte function code for LIST_OF_AGENCIES)
        packet += PacketType.LIST_OF_AGENCIES.to_bytes(2, 'little')

        # Number of agencies as uint16
        num_agencies = len(self.shared.agencies)
        packet += struct.pack('<H', num_agencies)

        for agency_id, agency in self.shared.agencies.items():
            if agency:
                # uint64 agency ID
                packet += struct.pack('<Q', agency.id64)

                # uint8 public flag
                packet.append(1 if agency.is_public else 0)
            else:
                # Invalid agency: 64-bit zero
                packet += struct.pack('<Q', 0)

        # Send directly to the specified session
        await session.send(packet)

    def build_force_resolve_packet(self, vessel):
        """
        Same schema as RESOLVE_VESSEL_REPLY, but with a different opcode so the
        client knows this is a server-pushed refresh.
        """
        packet = bytearray()
        packet += struct.pack('<H', PacketType.FORCE_RESOLVE_VESSEL)
        packet += struct.pack('<Q', vessel.object_id)      # u64 vessel id

        name = vessel.name
        packet += name.encode('utf-8') + b'\x00'           # null-terminated name

        components = vessel.components
        packet += struct.pack('<H', vessel.num_stages)     # u16 num stages
        packet += struct.pack('<H', vessel.stage)
        packet += struct.pack('<H', len(components))       # u16 component count
        for comp in components:
            packet += struct.pack('<HhhHHH', comp.id, comp.x, comp.y, comp.stage, comp.paint1, comp.paint2)  # u16, i16, u16, u16, u16

        return packet

    async def broadcast_force_resolve(self, vessel, only_same_system=True):
        """
        Push a FORCE_RESOLVE to interested clients.
        Filter to players in same galaxy/system by default to save bandwidth.
        """
        packet = self.build_force_resolve_packet(vessel)

        if only_same_system:
            sessions = []
            for s in list(self.sessions):
                if not s.alive or not hasattr(s, "player") or s.player is None:
                    continue
                if (s.player.galaxy, s.player.system) == (
                        getattr(vessel.home_chunk, "galaxy", None),
                        getattr(vessel.home_chunk, "system", None),
                ):
                    sessions.append(s)
        else:
            sessions = [s for s in self.sessions if s.alive]

        await asyncio.gather(*(s.send(packet) for s in sessions))


class StreamingServer:
    def __init__(self,missioncontrol, listens_on_port, controlserver):
        self.shared = missioncontrol
        self.port = listens_on_port
        self.active = False #The server must be "activated"
        self.control = controlserver
        self.objstream_seq = 0

    def activate(self):
        print(f"🟢 Streaming Server Activated on port {self.port}")
        self.active = True


    async def start(self):
        loop = asyncio.get_running_loop()
        await loop.create_datagram_endpoint(
            lambda: self,
            local_addr=('0.0.0.0', self.port)
        )
        self.activate()
        # Start the periodic player updates
        asyncio.create_task(self._broadcast_loop())


    def connection_made(self, transport):
        self.transport = transport
        print("📡 UDP server is ready to stream data.")

    def datagram_received(self, data, addr):
        #print(f"📦 Received UDP from {addr} - Raw data: {data}")
        if not data:
            return

        ip, port = addr

        # Process a port-learn packet from client
        if data[0] == DataGramPacketType.LATENCY_LEARN_PORT:
            for session in self.control.sessions:
                if session.remote_ip == ip and session.alive:
                    if session.udp_port != port:
                        session.udp_port = port
                        key = (ip, port)
                        self.shared.udp_endpoint_to_session[key] = session
                        print(f"🔌 UDP port {port} learned for session {ip}")
                    response = bytearray()
                    response.append(DataGramPacketType.LATENCY_LEARN_PORT)
                    self.transport.sendto(response, addr)
                    break

        elif data[0] == DataGramPacketType.UDP_ASK_ABOUT_AGENCY:
            if len(data) < 9:
                print("⚠️ Invalid UDP_ASK_ABOUT_AGENCY packet length")
                return

            agency_id = int.from_bytes(data[1:9], 'little')
            print(f"📨 Client asked about agency: {agency_id}")

            agency = self.control.shared.agencies.get(agency_id)
            if agency:
                response = bytearray()
                response.append(DataGramPacketType.UDP_ASK_ABOUT_AGENCY)  # Function code
                response += agency_id.to_bytes(8, 'little')                # u64 agency ID
                response += agency.name.encode('utf-8') + b'\x00'          # null-terminated name
                response.append(1 if agency.is_public else 0)              # u8 public flag

                self.transport.sendto(response, addr)
                print(f"📡 Sent agency info about {agency_id} to {addr}")
            else:
                print(f"⚠️ No agency with ID {agency_id}")

        elif data[0] == DataGramPacketType.OBJECT_INQUIRY:
            key = (ip, port)
            session = self.shared.udp_endpoint_to_session.get(key)
            if not session:
                print(f"❌ Unknown session for {key}")
                return

            player = session.player
            if not player:
                print(f"❌ No player bound to session {session.temp_id}")
                return

            if len(data) < 3:
                print("⚠️ Inquiry packet too short.")
                return

            num_inquiries = int.from_bytes(data[1:3], 'little')
            print(f"🔍 Received object inquiry for {num_inquiries} objects from {addr}")

            expected_length = 1 + 2 + (8 * num_inquiries)
            if len(data) < expected_length:
                print(f"⚠️ Incomplete object inquiry packet: expected {expected_length} bytes, got {len(data)}")
                return

            # Extract object IDs (64-bit unsigned ints)
            object_ids = []
            offset = 3
            for _ in range(num_inquiries):
                obj_id = int.from_bytes(data[offset:offset + 8], 'little')
                object_ids.append(obj_id)
                offset += 8

            print(f"🆔 Client asked about object IDs: {object_ids}")

            # RESPONSE TO OBJECT INQUIRY

            chunk_key = (player.galaxy, player.system)
            chunk = self.shared.chunk_manager.loaded_chunks.get(chunk_key)

            if not chunk:
                print(f"⚠️ ERROR: COULDNT FIND CHUNK {chunk_key}")
                return

            response = bytearray()
            response.append(DataGramPacketType.OBJECT_INQUIRY)
            response += struct.pack('<H', len(object_ids))               # uint16: number of objects

            for object_id in object_ids:
                obj = chunk.get_object_by_id(object_id)
                if obj:
                    response += struct.pack('<QH', obj.object_id, obj.object_type)
                else:
                    print(f"Object {object_id} does not exist in that chunk.")

            addr = (session.remote_ip, session.udp_port)
            self.transport.sendto(response, addr)


        elif data[0] == DataGramPacketType.RESOLVE_VESSEL:
            if len(data) < 9:
                print("⚠️ Invalid RESOLVE_VESSEL packet length.")
                return   
            vessel_id = int.from_bytes(data[1:9], 'little')
            key = (ip, port)
            session = self.shared.udp_endpoint_to_session.get(key)
            if not session:
                print(f"❌ Unknown session for {key}")
                return
            player = session.player
            if not player:
                print(f"❌ No player bound to session {session.temp_id}")
                return
            chunk_key = (player.galaxy, player.system)
            chunk = self.shared.chunk_manager.loaded_chunks.get(chunk_key)
            if not chunk:
                print(f"❌ Couldn't find chunk {chunk_key}")
                return
            vessel = chunk.get_object_by_id(vessel_id)
            #Note - this will actually be sending the response as TCP with included JSON
            # even though it's a response to a UDP packet. 
            asyncio.create_task(session.send(self.build_resolve_vessel_packet(vessel)))

        elif data[0] == DataGramPacketType.REQUEST_VESSEL_TREE_UPGRADE:
            # need 1(opcode)+8(vessel id)+2(upgrade id)
            if len(data) < 11:
                print("⚠️ REQUEST_VESSEL_TREE_UPGRADE: packet too short")
                return

            vessel_id  = int.from_bytes(data[1:9], 'little')
            upgrade_id = int.from_bytes(data[9:11], 'little')

            key = (ip, port)
            session = self.shared.udp_endpoint_to_session.get(key)
            if not session or not session.alive:
                print(f"❌ Unknown or dead session for {key}")
                return
            player = getattr(session, "player", None)
            if not player:
                print(f"❌ No player bound to session {getattr(session, 'temp_id', 0)}")
                return

            # find vessel in the player's current chunk
            chunk_key = (player.galaxy, player.system)
            chunk = self.shared.chunk_manager.loaded_chunks.get(chunk_key)
            if not chunk:
                print(f"❌ Couldn't find chunk {chunk_key}")
                return
            vessel = chunk.get_object_by_id(vessel_id)
            if not vessel:
                self._udp_send_to_session(session, self.build_notification_packet(1, "Upgrade failed: vessel not found"))
                return

            # basic ownership/authority checks (tighten if you want stricter rules)
            if getattr(vessel, "agency_id", None) != getattr(player, "agency_id", None):
                self._udp_send_to_session(session, self.build_notification_packet(1, "Upgrade failed: not your agency's vessel"))
                return
            # Require controller to spend; alternatively allow anyone in agency:
            if getattr(vessel, "controlled_by", 0) not in (getattr(player, "steamID", 0), getattr(player, "steam_id", 0)):
                self._udp_send_to_session(session, self.build_notification_packet(1, "Upgrade failed: you must be controlling this vessel"))
                return

            # Verify the upgrade exists & is currently unlockable (tier, prereqs, stage==0)
            tree = vessel.current_payload_tree()
            node = tree.get(int(upgrade_id))
            if not node:
                self._udp_send_to_session(session, self.build_notification_packet(1, "Upgrade failed: invalid upgrade id"))
                return

            if not vessel.can_unlock_current(int(upgrade_id)):
                # can be prereqs/tier/stage gate
                self._udp_send_to_session(session, self.build_notification_packet(1, "Upgrade failed: requirements not met"))
                return

            # Cost check – charge the player (swap to agency if desired)
            cost = int(getattr(node, "cost_money", 0))
            if getattr(player, "money", 0) < cost:
                self._udp_send_to_session(session, self.build_notification_packet(1, "Upgrade failed: insufficient funds"))
                return

            # Deduct → attempt unlock → refund on failure (paranoia)
            player.money -= cost
            if not vessel.unlock_current(int(upgrade_id)):
                player.money += cost
                self._udp_send_to_session(session, self.build_notification_packet(1, "Upgrade failed: could not unlock"))
                return

            # Success: notify, push updated tree to the requester, and refresh money HUD
            self._udp_send_to_session(session, self.build_notification_packet(2, f"Upgrade purchased (#{upgrade_id}) for {cost}"))
            try:
                pkt = vessel._build_upgrades_dgram()
                self._udp_send_to_session(session, pkt)
            except Exception as e:
                print(f"⚠️ Failed to send updated upgrade tree: {e}")

            # (Optional) immediate money refresh; otherwise your 60Hz loop will cover it quickly.
            try:
                self.send_player_details()
            except Exception:
                pass




    def error_received(self, exc):
        print(f"⚠️ UDP error received: {exc}")

    def connection_lost(self, exc):
        print("🔌 UDP server closed.")

    async def _broadcast_loop(self):
        while True:
            self.send_player_details()
            await asyncio.sleep(1 / 60)  

    def build_resolve_vessel_packet(self, vessel):
        print("Sending vessel resolve packet")
        packet = bytearray()
        packet += struct.pack('<H', PacketType.RESOLVE_VESSEL_REPLY)
        #ID of instance being resolved
        packet += struct.pack('<Q', vessel.object_id) 
        name = vessel.name
        packet += name.encode('utf-8') + b'\x00'
        components = vessel.components
        packet += struct.pack('<H', vessel.num_stages)
        packet += struct.pack('<H', vessel.stage)
        packet += struct.pack('<H', len(components))

        for comp in components:
            packet += struct.pack('<HhhHHH', comp.id, comp.x, comp.y, comp.stage, comp.paint1, comp.paint2)
        return packet   

    def build_telescope_sight_packet(self, vessel):
        """
        Layout:
        u8   opcode = TELESCOPE_SIGHT
        u64  vessel_id
        u16  count
        [count x u64 object_id]
        """
        pkt = bytearray()
        pkt.append(DataGramPacketType.TELESCOPE_SIGHT)
        pkt += struct.pack('<Q', vessel.object_id)

        ids = [int(obj.object_id) for obj in getattr(vessel, "telescope_targets_in_sight", []) if obj is not None]
        pkt += struct.pack('<H', len(ids))
        for oid in ids:
            pkt += struct.pack('<Q', oid)
        return pkt


    # StreamingServer
    def send_udp_to_agency(self, agency_id: int, packet: bytes) -> int:
        """
        Send a raw UDP packet to all online players in the given agency.
        Returns the number of packets successfully sent.
        """
        sent = 0
        for s in self.control.sessions:
            if not s.alive:
                continue
            p = getattr(s, "player", None)
            if not p or getattr(p, "agency_id", None) != agency_id:
                continue
            sent += 1 if self._udp_send_to_session(s, packet) else 0
        return sent




    def send_player_details(self):
        packet = bytearray()
        packet.append(DataGramPacketType.PLAYER_DETAILS_UDP)  
        sessions = [s for s in self.control.sessions if s.alive]

        packet.append(len(sessions))  # number of players

        for session in sessions:
            temp_id = session.temp_id or 0
            player = self.control.get_player_by_steamid(session.steam_id)
            money = player.money if player else 0

            packet.append(temp_id)  # 1 byte
            packet += struct.pack('<Q', money)  # 8 bytes (uint64 little-endian)

        for session in sessions:
            if hasattr(session, "udp_port") and session.udp_port:
                addr = (session.remote_ip, session.udp_port)
                self.transport.sendto(packet, addr)






    def build_notification_packet(self, notif_kind: int, message: str) -> bytes:
        """
        Layout:
        u8  opcode = DataGramPacketType.NOTIFICATION
        u8  notif_kind 
        str utf-8 NUL-terminated message
        """
        pkt = bytearray()
        pkt.append(DataGramPacketType.NOTIFICATION)
        pkt.append(notif_kind & 0xFF)
        pkt += message.encode("utf-8") + b"\x00"
        return pkt

    def _udp_send_to_session(self, session, packet: bytes) -> bool:
        """
        Low-level helper. Returns True if we had an address to send to.
        """
        if not session.alive:
            return False
        udp_port = getattr(session, "udp_port", None)
        if not udp_port:
            # Client hasn't done LATENCY_LEARN_PORT yet.
            return False
        addr = (session.remote_ip, udp_port)
        self.transport.sendto(packet, addr)
        return True

    async def notify_sessions(self, sessions: Iterable["Session"], notif_kind: int, message: str) -> int:
        """
        Send a NOTIFICATION to the provided sessions. Returns the number of sends attempted.
        """
        pkt = self.build_notification_packet(notif_kind, message)
        sent = 0
        for s in sessions:
            sent += 1 if self._udp_send_to_session(s, pkt) else 0
        return sent

    async def notify_steam_ids(self, steam_ids: Sequence[int], notif_kind: int, message: str) -> int:
        """
        Convenience: target by Steam IDs.
        """
        idset = set(steam_ids)
        targets = [
            s for s in self.control.sessions
            if s.alive and s.steam_id in idset
        ]
        return await self.notify_sessions(targets, notif_kind, message)

    async def notify_agency(self, agency_id: int, notif_kind: int, message: str) -> int:
        """
        Convenience: target everyone in a specific agency.
        """
        targets = [
            s for s in self.control.sessions
            if s.alive and getattr(getattr(s, "player", None), "agency_id", None) == agency_id
        ]
        return await self.notify_sessions(targets, notif_kind, message)

    async def notify_same_system(self, galaxy: int, system: int, notif_kind: int, message: str) -> int:
        """
        Convenience: target all players in a given (galaxy, system).
        """
        targets = [
            s for s in self.control.sessions
            if s.alive and getattr(getattr(s, "player", None), "galaxy", None) == galaxy
               and getattr(getattr(s, "player", None), "system", None) == system
        ]
        return await self.notify_sessions(targets, notif_kind, message)



    def build_vessel_destroyed_packet(self, vessel_id: int) -> bytes:
        """
        Layout:
        u8   opcode = DataGramPacketType.NOTIFY_VESSEL_DESTROYED
        u64  vessel_id
        """
        pkt = bytearray()
        pkt.append(DataGramPacketType.NOTIFY_VESSEL_DESTROYED)
        pkt += struct.pack('<Q', int(vessel_id))
        return pkt

    def notify_vessel_destroyed(self, vessel) -> int:
        """
        Send NOTIFY_VESSEL_DESTROYED to all sessions whose players are
        in the same (galaxy, system) as the destroyed vessel.
        Returns number of packets actually sent.
        """
        # Find the chunk coordinates *before* the vessel is removed
        chunk = getattr(vessel, "home_chunk", None)
        if chunk is None:
            # Fallback: use the object-id → chunk index if available
            cm = getattr(self.shared, "chunk_manager", None)
            if cm:
                chunk = cm.get_chunk_from_object_id(int(vessel.object_id))
        if chunk is None:
            return 0

        galaxy = getattr(chunk, "galaxy", None)
        system = getattr(chunk, "system", None)
        pkt = self.build_vessel_destroyed_packet(int(vessel.object_id))

        sent = 0
        for s in self.control.sessions:
            if not s.alive:
                continue
            p = getattr(s, "player", None)
            if not p:
                continue
            if getattr(p, "galaxy", None) == galaxy and getattr(p, "system", None) == system:
                sent += 1 if self._udp_send_to_session(s, pkt) else 0
        return sent
