import asyncio
from packet_types import PacketType

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
            print(f"Steam ID received: {self.steam_id}")
            #await self.control_server.register_player(self)   

        # 0x0002    -   The client sent a chat message, and the TCP server will need to relay
        elif function_code == PacketType.CHAT_MESSAGE_RELAY: 
            msg_type = await self.reader.readexactly(1)
            message = await self.reader.readuntil(b'\x00')
            print(f"Chat: {message[:-1].decode()}")
            #Todo - TCP server relay

        # 0x0004    -   Keep alive packets show that the client is connected even if they're being lame and boring
        elif function_code == PacketType.KEEPALIVE:
            #todo - implement keepalive
            pass

        else:
            print(f"🔴 Unknown function code: {function_code}")
            self.alive = False         
                
    async def close(self):
        print(f"[-] Closing session for {self.remote_ip}")
        self.writer.close()
        await self.writer.wait_closed()
        if self.keepalive_task:
            self.keepalive_task.cancel()
