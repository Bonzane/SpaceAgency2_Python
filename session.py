import asyncio
from packet_types import ChatMessage, PacketType
from agency import Agency
import json
from buildings import Building
from vessels import Vessel, AttachedVesselComponent, construct_vessel_from_request, VesselControl
import struct

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

    async def send_game_json_packet(self):
        gamedesc = self.control_server.shared.game_description
        payload = json.dumps(gamedesc, separators=(',', ':')).encode('utf-8')  # minified JSON
        packet = bytearray()
        packet += PacketType.GAME_JSON.to_bytes(2, 'little')     # u16 opcode
        packet += struct.pack('<I', len(payload))                # u32 length (little-endian)
        packet += payload                                        # bytes
        await self.send(packet)

    async def send_welcome(self):
        print(f"[+] Connection from {self.remote_ip}, assigned ID {self.temp_id}")


    def _get_player_and_agency(self):
        player = self.player or self.control_server.get_player_by_steamid(self.steam_id)
        if not player:
            print("⚠️ No player bound to this session"); return None, None
        agency = self.control_server.shared.agencies.get(player.agency_id)
        if not agency:
            print(f"⚠️ Player {player.steam_id} has no valid agency"); return player, None
        return player, agency

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
            await asyncio.sleep(0.25)
            await self.send_game_json_packet()

        elif function_code == PacketType.TCP_SERVER_TELL_CONNECTED_PLAYERS:
            print("Got a 1????????????")
            rest = await self.reader.read(32)
            raw_packet = header + rest
            print(f"⚠️ Unknown function code: {function_code}")
            print(f"🧾 Raw packet bytes: {raw_packet.hex()}")

        # 0x0002    -   The client sent a chat message, and the TCP server will need to relay
        elif function_code == PacketType.CHAT_MESSAGE_RELAY:
            msg_type_raw = await self.reader.readexactly(1)
            message      = await self.reader.readuntil(b'\x00')

            # Decode + coerce enum
            decoded = message[:-1].decode(errors="replace")
            try:
                msg_type = ChatMessage(int.from_bytes(msg_type_raw, "little"))
            except ValueError:
                print(f"⚠️ Unknown chat message type byte={msg_type_raw!r}; dropping")
                return

            sender_agency_id = getattr(getattr(self, "player", None), "agency_id", None)
            print(f"{self.remote_ip} says ({msg_type.name}): \"{decoded}\" (agency={sender_agency_id})")

            # Rebuild relay packet exactly as clients expect
            pkt = bytearray()
            pkt += PacketType.CHAT_MESSAGE_RELAY.to_bytes(2, "little")
            pkt += msg_type_raw
            pkt += self.steam_id.to_bytes(8, "little")
            pkt += message  # includes trailing NUL

            match msg_type:
                case ChatMessage.GLOBAL:
                    await self.control_server.broadcast(pkt)

                case ChatMessage.AGENCY:
                    if sender_agency_id is None:
                        print("⚠️ Sender has no agency; dropping agency chat")
                        return
                    sent = await self.control_server.broadcast_to_agency(sender_agency_id, pkt)
                    print(f"🏢 Agency chat relayed to {sent} live session(s) in agency {sender_agency_id}")

                case _:
                    # Not handled yet; ignore silently (or log if you want)
                    pass



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
                new_agency = Agency(agency_name, self.control_server.shared)
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
            if not self.alive:
                self.alive = True
            await self.send(packet)

        elif function_code == PacketType.CONSTRUCT_BUILDING:
            try:
                payload = await self.reader.readexactly(12)
                object_id = int.from_bytes(payload[0:8], 'little')
                building_type = int.from_bytes(payload[8:10], 'little')
                position_angle = int.from_bytes(payload[10:12], 'little')
                print(f"🏗️ Construct Building Request:")
                print(f"   - Planet Object ID: {object_id}")
                print(f"   - Building Type: {building_type}")
                print(f"   - Position Angle: {position_angle}")
                player = self.control_server.get_player_by_steamid(self.steam_id)
                if player is None or player.agency_id not in self.control_server.shared.agencies:
                    print("⚠️ Invalid player or agency")
                    return   
                agency = self.control_server.shared.agencies[player.agency_id]
                building_data = self.control_server.shared.buildings_by_id.get(building_type)
                if not building_data:
                    print(f"❌ Invalid building type: {building_type}")
                    return

                cost = building_data.get("cost", 0)
                if player.money < cost:
                    print(f"❌ Player {self.steam_id} cannot afford building (needs {cost}, has {player.money})")
                    return 
                player.money -= cost
                new_building = Building(building_type, self.control_server.shared, position_angle, object_id, agency)
                agency.add_building_to_base(object_id, new_building)

        
            except Exception as e:
                print(f"❌ Session Failed to process CONSTRUCT_BUILDING: {e}")           


        elif function_code == PacketType.CONSTRUCT_VESSEL:
            try:
                raw_json_bytes = await self.reader.readuntil(b'\x00')
                raw_json = raw_json_bytes[:-1].decode('utf-8')  # remove the null terminator
                vessel_request_data = json.loads(raw_json)
                print("🛠️ Received CONSTRUCT_VESSEL JSON:")
                print(json.dumps(vessel_request_data, indent=4))

                # GET THE PLAYER AND AGENCY THAT WANT TO CONSTRUCT THIS VESSEL
                player = self.control_server.get_player_by_steamid(self.steam_id)
                if player is None or player.agency_id not in self.control_server.shared.agencies:
                    print("⚠️ Invalid player or agency")
                    return

                agency = self.control_server.shared.agencies[player.agency_id]
                # Construct the vessel from the request data
                vessel = construct_vessel_from_request(self.control_server.shared, player, vessel_request_data)
                vessel.calculate_vessel_stats()

            except Exception as e:
                print(f"❌ Failed to process CONSTRUCT_VESSEL: {e}")

        elif function_code == PacketType.VESSEL_CONTROL:
            print("received a vessel control")
            #If it's anything other than request_control, they must be already controlling the vessel.
            vesselID = await self.reader.readexactly(8)
            vessel_id = int.from_bytes(vesselID, 'little')
            _player = self.player
            chunk_key = (_player.galaxy, _player.system)
            chunk = self.control_server.shared.chunk_manager.loaded_chunks.get(chunk_key)
            vessel = chunk.get_object_by_id(vessel_id)
            control_bytes = await self.reader.readexactly(1)
            control_key = int.from_bytes(control_bytes, 'little')
            if(control_key == VesselControl.REQUEST_CONTROL):
                print(f"🚀 Vessel Control Request for vessel {vessel_id} by player {_player.steamID}")
                #If the vessel is free to be controlled, take control of it
                if(vessel.controlled_by == 0):
                    vessel.controlled_by = _player.steamID
                    if(_player.controlled_vessel_id != -1):
                        #Release control of that vessel
                        old_vessel = chunk.get_object_by_id(_player.controlled_vessel_id)
                        old_vessel.controlled_by = 0
                    #Take control of the new vessel
                    _player.controlled_vessel_id = vessel.object_id
                    print(f"✅ Player {_player.steamID} gained control of vessel {vessel_id}")

                #TCP RELAY
                packet = bytearray()
                packet += PacketType.VESSEL_CONTROL.to_bytes(2, 'little')  # Function code
                packet += vessel_id.to_bytes(8, 'little')                     # Vessel ID
                packet += self.steam_id.to_bytes(8, 'little')                  # Now controlled by
                await self.control_server.broadcast(packet)

            #Now check if the vessel is controlled by that player
            else:
                if(vessel.controlled_by == _player.steamID):
                    vessel.do_control(control_key)
                    if( control_key == VesselControl.SET_TELESCOPE_TARGET_ANGLE):
                        angle = await self.reader.readexactly(4)
                        angle_value = struct.unpack('<f', angle)[0]
                        print(f"Set telescope rcs angle to {angle_value}")
                        vessel.telescope_rcs_angle = angle_value
                

        
        elif function_code == PacketType.UPGRADE_BUILDING:
            planet_id = int.from_bytes(await self.reader.readexactly(8), 'little')
            building_type = int.from_bytes(await self.reader.readexactly(2), 'little')
            to_level = int.from_bytes(await self.reader.readexactly(2), 'little')

            player, agency = self._get_player_and_agency()
            if not player or not agency:
                return

            ok, reason, cost, new_level = agency.try_upgrade_building(player, planet_id, building_type, to_level)
            if ok:
                print(f"✅ Upgraded building {building_type} on planet {planet_id} "
                    f"from L? to L{new_level} for {cost}. Player now has {player.money}.")
            else:
                print(f"❌ Upgrade failed ({reason}). Needed {cost}, player has {player.money}.")

    



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
        self.alive = False

        self.control_server.sessions.discard(self)

        if self.steam_id in self.control_server.shared.players:
            player = self.control_server.shared.players[self.steam_id]
            if player.session == self:
                player.session = None

        # Remove UDP mapping if it matches
        if self.udp_port:
            key = (self.remote_ip, self.udp_port)
            if self.control_server.shared.udp_endpoint_to_session.get(key) == self:
                del self.control_server.shared.udp_endpoint_to_session[key]

        if self.keepalive_task:
            self.keepalive_task.cancel()

        if self.steam_id:
            await self.control_server.tell_everyone_player_left(self.steam_id)

        try:
            self.writer.close()
            await self.writer.wait_closed()
        except Exception as e:
            print(f"⚠️ Error closing session socket: {e}")

