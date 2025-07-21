import requests 
import socket
import asyncio
from session import Session
from player import Player
from agency import Agency
import agency
from packet_types import PacketType, DataGramPacketType
from typing import Set, Dict, Tuple
import aiohttp
import struct
import json

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
        self.udp_endpoint_to_session: Dict[Tuple[str, int], Session] = {}
        self.chunk_manager = None
        self.gamespeed = 2920
        self.player_starting_cash = int(200000)
        self.base_cash_per_second = 200
        self.game_description = None
        self.game_buildings_list = None
        self.buildings_by_id = None
        self.agency_default_attributes = None
        self.server_global_cash_multiplier = 1.0
        with open("game_desc.json", "r") as game_description_file:
            self.game_description = json.load(game_description_file)
            self.game_buildings_list = self.game_description.get("buildings")
            self.component_data = {
                comp["id"]: comp for comp in self.game_description["components"]
            }
            self.buildings_by_id = {b["id"]: b for b in self.game_buildings_list}
            self.agency_default_attributes = self.game_description.get("agency_default_attributes", {})

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
        asyncio.create_task(self.loop_tasks())  # Start periodic tasks

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


class StreamingServer:
    def __init__(self,missioncontrol, listens_on_port, controlserver):
        self.shared = missioncontrol
        self.port = listens_on_port
        self.active = False #The server must be "activated"
        self.control = controlserver

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
        packet += struct.pack('<H', len(components))
        for comp in components:
            packet += struct.pack('<Hff', comp.id, comp.x, comp.y)
        return packet   


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
