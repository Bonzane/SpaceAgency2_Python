import requests 
import socket
import asyncio
from session import Session
from player import Player
from agency import Agency
import agency
from packet_types import PacketType, DataGramPacketType
from typing import Set, Dict
import aiohttp
import struct

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

    #Starts the async loop that handles clients
    async def start(self):
        server = await asyncio.start_server(self.handle_client, '0.0.0.0', self.port)
        async with server:
            await server.serve_forever()


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
        await asyncio.gather(*(s.send(data) for s in self.sessions if s.alive))

    
    async def tell_everyone_player_joined(self, steam_id: int):
        packet = bytearray()
        packet += PacketType.PLAYER_JOIN.to_bytes(2, 'little')  # 2-byte function code
        packet += steam_id.to_bytes(8, 'little')                 # 8-byte Steam ID
        await self.broadcast(packet)
        await self.tell_everyone_info_about_everyone()

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
        if self.get_player_by_steamid(steam_id) is None:
            player = Player(session, steam_id)
            self.shared.players[steam_id] = player
        else:
            player = self.get_player_by_steamid(steam_id)
        
        session.player = player

        await self.tell_everyone_player_joined(steam_id)
        await self.tell_everyone_info_about_everyone()


    async def tell_everyone_info_about_everyone(self): 
        print("👥 Broadcasting player information")
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

        await self.broadcast(packet)


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
            #print(f"🔌  Attempting port discovery")
            for session in self.control.sessions:
                if session.remote_ip == ip:
                    if(session.udp_port != port):
                        session.udp_port = port
                        print(f"🔌 UDP port {port} learned for session {ip}")
                    response = bytearray()
                    response.append(DataGramPacketType.LATENCY_LEARN_PORT)
                    self.transport.sendto(response, addr)

    def error_received(self, exc):
        print(f"⚠️ UDP error received: {exc}")

    def connection_lost(self, exc):
        print("🔌 UDP server closed.")

    async def _broadcast_loop(self):
        while True:
            self.send_player_details()
            await asyncio.sleep(1 / 60)  

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
