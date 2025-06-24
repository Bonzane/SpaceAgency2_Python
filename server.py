import requests 
import socket
import asyncio
from session import Session
from player import Player
from agency import Agency
import agency
import packet_types
from typing import Set, Dict
import aiohttp

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
                "streamingServerUDPPort" : 9002,      # <- BS
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
            print(f"[+] New connection from {session.remote_ip}, assigned temp ID {session.temporary_id}. Awaiting Validation.")
            await session.start()
        except Exception as e:
            print(f"Error in session: {e}")
        finally:
            self.sessions.discard(session)
            writer.close()
            await writer.wait_closed()

    # Sends data to all connected clients
    async def broadcast(self, data: bytes):
        await asyncio.gather(*(s.send(data) for s in self.sessions))
    
    def tell_everyone_player_joined(self, id64: int):
        packet = bytearray()
        packet += (packet_types.PLAYER_JOIN).to_bytes(2, 'little')
        packet += id64.to_bytes(8, 'little')
        self.broadcast(packet)

    def get_player_by_steamid(self, steamid: int):
        return self.shared.players.get(steamid)

    def agency_with_name_exists(self, name: str) -> bool:
        return any(a.name == name for a in self.shared.agencies.values())

    def count_sessions(self) -> int:
        return len(self.sessions)

    def get_next_temp_id(self) -> int:
        temp_id = self.next_available_temp_id
        self.next_available_temp_id += 1
        return temp_id

        