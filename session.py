import asyncio
from packet_types import PacketType
from agency import Agency

# A session connects a TCP socket to a server-side player

class Session:
    def __init__(self, reader, writer, control_server):
        self.reader = reader
        self.writer = writer
        self.control_server = control_server
        self.temp_id = None
        self.steam_id = None
        self.remote_ip = writer.get_extra_info('peername')[0]
        self.keepalive_task = None
        self.alive = True
        self.validated = False
        self.keepalive = 0
        self.udp_port = None #Streaming server will discover this. It's assigned by the clients OS. 
        self.player = None

    async def start(self):
        self.assign_temp_id()
        await self.send_welcome()


        try:
            while self.alive:
                await self.read_and_process_packet()
        except asyncio.CancelledError:
            pass
        finally:
            await self.close()

    def assign_temp_id(self):
        self.temp_id = self.control_server.get_next_temp_id()

    async def send_welcome(self):
        print(f"[+] Connection from {self.remote_ip}, assigned ID {self.temp_id}")

    async def read_and_process_packet(self):
        header = await self.reader.readexactly(2)
        function_code = int.from_bytes(header, 'little')


        # 0x0000    -   The client is telling us their steam info. 
        #               We will keep their steam id64 associated with this session :)
        if function_code == PacketType.REQUEST_STEAM_INFO:
            payload = await self.reader.readexactly(8)
            self.steam_id = int.from_bytes(payload, 'little')
            print(f"Steam ID for {self.remote_ip} received: {self.steam_id}")
            await self.control_server.register_player(self)
            await self.control_server.tell_everyone_player_joined(self.steam_id)
            await asyncio.sleep(0.25)
            await self.control_server.tell_session_info_about_everyone(self)

        elif function_code == PacketType.TCP_SERVER_TELL_CONNECTED_PLAYERS:
            print("Got a 1????????????")
            rest = await self.reader.read(32)
            raw_packet = header + rest
            print(f"⚠️ Unknown function code: {function_code}")
            print(f"🧾 Raw packet bytes: {raw_packet.hex()}")

        # 0x0002    -   The client sent a chat message, and the TCP server will need to relay
        elif function_code == PacketType.CHAT_MESSAGE_RELAY: 
            msg_type = await self.reader.readexactly(1)
            message = await self.reader.readuntil(b'\x00')
            decoded = message[:-1].decode()
            print(f"{self.remote_ip} says: \"{decoded}\"")
            #TCP RELAY
            packet = bytearray()
            packet += PacketType.CHAT_MESSAGE_RELAY.to_bytes(2, 'little')  # Function code
            packet += msg_type                                             # Message type
            packet += self.steam_id.to_bytes(8, 'little')                  # From who
            packet += message                                              # Original message
            await self.control_server.broadcast(packet)


        # 0x0004    -   Keep alive packets show that the client is connected even if they're being lame and boring
        elif function_code == PacketType.KEEPALIVE:
            #todo - implement keepalive
            pass

        # 0x0007    -   Request list of agencies
        elif function_code == PacketType.INFO_ABOUT_AGENCIES:
            pass


        # 0x000C    -   Request list of agencies
        elif function_code == PacketType.LIST_OF_AGENCIES:
            await self.control_server.send_list_of_agencies_to_session(self)  

        elif function_code == PacketType.CREATE_AGENCY: 
            is_public = bool(int.from_bytes(await self.reader.readexactly(1), 'little'))
            name_bytes = bytearray()
            while True:
                b = await self.reader.readexactly(1)
                if b == b'\x00':
                    break
                name_bytes += b
            agency_name = name_bytes.decode()
            print(f"🌐 Client requested to create agency: '{agency_name}', public={is_public}")
            exists = self.control_server.agency_with_name_exists(agency_name)
            ec = 1 if exists else 0
            if not exists:
                new_agency = Agency(agency_name)
                new_agency.is_public = is_public
                new_agency.add_player(self.steam_id)
                new_agency.manually_set_id(self.control_server.shared.get_next_agency_id())
                self.control_server.shared.agencies[new_agency.id64] = new_agency

                # Assign agency to player
                player = self.control_server.get_player_by_steamid(self.steam_id)
                if player:
                    player.agency_id = new_agency.id64
                print(f"✅ Agency '{agency_name}' created with ID {new_agency.id64}")
                await self.control_server.tell_session_info_about_everyone(self)

            # Send response
            packet = bytearray()
            packet += PacketType.CREATE_AGENCY.to_bytes(2, 'little')
            packet.append(ec)
            await self.send(packet)

        else:
            print(f"🔴 Unknown function code: {function_code}")
            self.alive = False    

    async def send(self, data: bytes):
        try:
            self.writer.write(data)
            await self.writer.drain()
        except Exception as e:
            print(f"Send failed: {e}")
            self.alive = False
     
                
    async def close(self):
        print(f"[-] Closing session for {self.remote_ip}")
        self.writer.close()
        await self.writer.wait_closed()
        if self.keepalive_task:
            self.keepalive_task.cancel()
        if self.steam_id:
            await self.control_server.tell_everyone_player_left(self.steam_id)
