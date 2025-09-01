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
                    # notify just the requester (failure)
                    shared = getattr(player, "shared", None) or self.control_server.shared
                    if shared and shared.udp_server:
                        await shared.udp_server.notify_steam_ids([getattr(player, "steamID", self.steam_id)], 1,
                            "Construction failed: invalid player/agency")
                    return

                agency = self.control_server.shared.agencies[player.agency_id]
                shared = getattr(player, "shared", None) or self.control_server.shared
                udp = getattr(shared, "udp_server", None)

                building_data = shared.buildings_by_id.get(building_type)
                if not building_data:
                    print(f"❌ Invalid building type: {building_type}")
                    if udp:
                        await udp.notify_steam_ids([getattr(player, "steamID", self.steam_id)], 1,
                            f"Construction failed: invalid building type {building_type}")
                    return

                cost = building_data.get("cost", 0)
                if player.money < cost:
                    print(f"❌ Player {self.steam_id} cannot afford building (needs {cost}, has {player.money})")
                    if udp:
                        await udp.notify_steam_ids([getattr(player, "steamID", self.steam_id)], 1,
                            f"Construction failed: need {cost}, have {player.money}")
                    return

                # Deduct and build
                player.money -= cost
                new_building = Building(building_type, shared, position_angle, object_id, agency)
                agency.add_building_to_base(object_id, new_building)

                # Success notification to the entire agency (kind 2)
                if udp:
                    bname = shared.buildings_by_id.get(building_type, {}).get("name", f"Building {building_type}")

                    # Optional: try planet name via ChunkManager
                    planet_name = None
                    cm = getattr(shared, "chunk_manager", None)
                    if cm:
                        chunk = cm.get_chunk_from_object_id(object_id)
                        if chunk:
                            planet_obj = chunk.get_object_by_id(object_id)
                            planet_name = getattr(planet_obj, "name", None)

                    where = f" on {planet_name}" if planet_name else f" on planet {object_id}"
                    await udp.notify_agency(agency.id64, 2, f"{agency.name} started construction of {bname}{where}")

            except Exception as e:
                print(f"❌ Session Failed to process CONSTRUCT_BUILDING: {e}")
                # Best-effort failure notice to requester
                try:
                    shared = getattr(player, "shared", None) or self.control_server.shared
                    if shared and shared.udp_server:
                        await shared.udp_server.notify_steam_ids([getattr(player, "steamID", self.steam_id)], 1,
                            "Construction failed.")
                except Exception:
                    pass
            


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

        elif function_code == PacketType.LEAVE_AGENCY:
            # who is asking?
            player = self.control_server.get_player_by_steamid(self.steam_id)
            if not player:
                print("⚠️ LEAVE_AGENCY: no player bound to session")
                return

            shared = self.control_server.shared
            udp = getattr(shared, "udp_server", None)

            # find current agency
            ag = shared.agencies.get(getattr(player, "agency_id", 0))
            if not ag:
                print(f"⚠️ LEAVE_AGENCY: player {player.steamID} not in a valid agency")
                # polite feedback to the requester only
                if udp:
                    await udp.notify_steam_ids([int(player.steamID)], 0, "You are not in an agency.")
                return

            # remove membership
            try:
                ag.remove_player(int(player.steamID))
            except Exception as e:
                print(f"⚠️ LEAVE_AGENCY: failed to remove {player.steamID} from agency {ag.id64}: {e}")

            # clear player's agency
            player.agency_id = 0

            # craft the notification "{ID} left the agency"
            msg = f"{int(self.steam_id)} left the agency"

            # notify the leaver
            if udp:
                try:
                    await udp.notify_steam_ids([int(self.steam_id)], 0, msg)  # kind 0 = generic
                except Exception as e:
                    print(f"⚠️ notify_steam_ids(leaver) failed: {e}")

            # notify remaining members of that agency
            if udp:
                try:
                    await udp.notify_agency(int(ag.id64), 0, msg)  # leaver already removed, so they won't get this
                except Exception as e:
                    print(f"⚠️ notify_agency(remaining) failed: {e}")

            # update everyone’s view of player→agency mapping (your existing packet)
            try:
                await self.control_server.tell_everyone_player_joined(self.steam_id)  # optional: re-announce presence
                await asyncio.sleep(0.05)
                await self.control_server.tell_everyone_player_left(self.steam_id)    # optional: if you rely on join/leave
                await asyncio.sleep(0.05)
                await self.control_server.tell_everyone_info_about_everyone()         # authoritative refresh
            except Exception as e:
                print(f"⚠️ LEAVE_AGENCY: refresh broadcast failed: {e}")

            # If you implemented the JSON agencies snapshot/list, broadcast it too so UIs get the new roster.
            try:
                if hasattr(self.control_server, "broadcast_info_about_agencies"):
                    await self.control_server.broadcast_info_about_agencies()
                elif hasattr(self.control_server, "send_list_of_agencies"):
                    await self.control_server.send_list_of_agencies()  # legacy u16/u8 list
            except Exception as e:
                print(f"⚠️ LEAVE_AGENCY: agencies broadcast failed: {e}")


        elif function_code == PacketType.VESSEL_CONTROL:
            try:
                # Payload header: [u64 vessel_id][u8 action_key]
                hdr = await self.reader.readexactly(9)
                vessel_id = int.from_bytes(hdr[0:8], 'little')
                control_key = hdr[8]

                player = self.player
                shared = self.control_server.shared
                cm = shared.chunk_manager

                # Resolve chunk and vessel (try current chunk first, then cross-chunk)
                chunk_key = (player.galaxy, player.system)
                chunk = cm.loaded_chunks.get(chunk_key)
                vessel = chunk.get_object_by_id(vessel_id) if chunk else None
                if vessel is None:
                    host_chunk = cm.get_chunk_from_object_id(vessel_id)
                    if host_chunk:
                        vessel = host_chunk.get_object_by_id(vessel_id)

                # Helper: how many extra bytes follow this action?
                def _extra_len(key: int) -> int:
                    if key == int(VesselControl.SET_TELESCOPE_TARGET_ANGLE):
                        return 4  # float
                    if key == int(VesselControl.SET_SYSTEM_STATE):
                        return 3  # u16 + u8
                    return 0

                # Helper: drain any extra payload when we can't act, to keep stream in sync
                async def _drain_if_needed(key: int):
                    n = _extra_len(key)
                    if n:
                        try:
                            await self.reader.readexactly(n)
                        except asyncio.IncompleteReadError:
                            pass

                if vessel is None:
                    print(f"⚠️ VESSEL_CONTROL: vessel {vessel_id} not found (chunk_key={chunk_key})")
                    await _drain_if_needed(control_key)
                    return

                # REQUEST_CONTROL path
                if control_key == int(VesselControl.REQUEST_CONTROL):
                    print(f"🚀 Vessel Control Request for vessel {vessel_id} by player {player.steamID}")

                    # If player already controls another vessel, release it safely (even cross-chunk)
                    old_id = int(getattr(player, "controlled_vessel_id", -1))
                    if old_id != -1 and old_id != vessel_id:
                        old_chunk = cm.get_chunk_from_object_id(old_id)
                        old_v = old_chunk.get_object_by_id(old_id) if old_chunk else None
                        if old_v is not None:
                            old_v.controlled_by = 0
                        # Clear regardless (old might be gone/different system)
                        player.controlled_vessel_id = -1

                    # Claim if free or already ours
                    current_owner = int(getattr(vessel, "controlled_by", 0))
                    if current_owner in (0, int(player.steamID)):
                        vessel.controlled_by = int(player.steamID)
                        player.controlled_vessel_id = int(vessel.object_id)
                        print(f"✅ Player {player.steamID} gained control of vessel {vessel_id}")

                        # Relay over TCP to everyone (same as your pattern)
                        packet = bytearray()
                        packet += PacketType.VESSEL_CONTROL.to_bytes(2, 'little')
                        packet += vessel_id.to_bytes(8, 'little')
                        packet += int(self.steam_id).to_bytes(8, 'little')  # now controlled by
                        await self.control_server.broadcast(packet)
                    else:
                        # Already owned by someone else; notify just the requester if you like
                        udp = getattr(shared, "udp_server", None)
                        if udp:
                            await udp.notify_steam_ids([player.steamID], 1, "That vessel is already controlled.")

                    # No extra payload for REQUEST_CONTROL → done
                    return

                # Non-request actions require ownership
                if int(getattr(vessel, "controlled_by", 0)) != int(player.steamID):
                    print(f"🛑 Player {player.steamID} tried action {control_key} on vessel {vessel_id} without control")
                    await _drain_if_needed(control_key)
                    return

                # Apply the action
                vessel.do_control(control_key)

                # Read & apply optional payloads
                if control_key == int(VesselControl.SET_TELESCOPE_TARGET_ANGLE):
                    raw = await self.reader.readexactly(4)
                    angle_value = struct.unpack('<f', raw)[0]
                    vessel.telescope_rcs_angle = angle_value
                    print(f"🔭 Set telescope RCS angle to {angle_value}")

                elif control_key == int(VesselControl.SET_SYSTEM_STATE):
                    raw = await self.reader.readexactly(3)  # u16 + u8
                    system_id = struct.unpack('<H', raw[:2])[0]
                    new_state = raw[2]
                    vessel.set_system_state(system_id, new_state)

                # (Other actions like DEPLOY_STAGE etc. have no extra payload)

            except asyncio.IncompleteReadError:
                print("⚠️ VESSEL_CONTROL: client disconnected mid-read")
            except Exception as e:
                print(f"❌ VESSEL_CONTROL error: {e}")

        elif function_code == PacketType.JOIN_PUBLIC_AGENCY:
            try:
                # Payload: [u64 agency_id]
                target_agency_id = int.from_bytes(await self.reader.readexactly(8), "little")

                # Resolve player + shared bits
                player = self.control_server.get_player_by_steamid(self.steam_id)
                if not player:
                    print("⚠️ JOIN_AGENCY: no bound player for this session")
                    return

                shared = self.control_server.shared
                udp = getattr(shared, "udp_server", None)

                # Validate agency
                target_agency = shared.agencies.get(target_agency_id)
                if not target_agency:
                    print(f"❌ JOIN_AGENCY: agency {target_agency_id} does not exist")
                    if udp:
                        await udp.notify_steam_ids([int(player.steamID)], 1, "Join failed: agency does not exist.")
                    return

                if not bool(getattr(target_agency, "is_public", False)):
                    print(f"❌ JOIN_AGENCY: agency {target_agency_id} is private")
                    if udp:
                        await udp.notify_steam_ids([int(player.steamID)], 1, "Join failed: that agency is private.")
                    return

                # Already in that agency?
                if int(getattr(player, "agency_id", 0)) == int(target_agency_id):
                    if udp:
                        await udp.notify_steam_ids([int(player.steamID)], 0, "You are already in that agency.")
                    return

                # If currently in another agency, remove from it and notify that agency
                prev_agency_id = int(getattr(player, "agency_id", 0) or 0)
                if prev_agency_id in shared.agencies:
                    prev_agency = shared.agencies[prev_agency_id]
                    try:
                        prev_agency.remove_player(int(player.steamID))
                    except Exception as e:
                        print(f"⚠️ JOIN_AGENCY: failed to remove {player.steamID} from {prev_agency_id}: {e}")
                    player.agency_id = 0  # clear before joining new

                    # Notify leaver + previous members
                    if udp:
                        leave_msg = f"{int(self.steam_id)} left the agency"
                        try:
                            await udp.notify_steam_ids([int(player.steamID)], 0, leave_msg)
                        except Exception as e:
                            print(f"⚠️ notify_steam_ids (leave) failed: {e}")
                        try:
                            await udp.notify_agency(int(prev_agency.id64), 0, leave_msg)
                        except Exception as e:
                            print(f"⚠️ notify_agency (leave) failed: {e}")

                # Add to the new (public) agency
                try:
                    target_agency.add_player(int(player.steamID))
                except Exception as e:
                    print(f"❌ JOIN_AGENCY: add_player failed: {e}")
                    if udp:
                        await udp.notify_steam_ids([int(player.steamID)], 1, "Join failed: server error.")
                    return

                player.agency_id = int(target_agency.id64)

                # Notify success to the joiner and to the agency members
                if udp:
                    join_msg = f"{int(self.steam_id)} joined the agency"
                    try:
                        await udp.notify_steam_ids([int(player.steamID)], 0, join_msg)
                    except Exception as e:
                        print(f"⚠️ notify_steam_ids (join) failed: {e}")
                    try:
                        await udp.notify_agency(int(target_agency.id64), 0, join_msg)
                    except Exception as e:
                        print(f"⚠️ notify_agency (join) failed: {e}")

                # Refresh everyone’s player→agency mapping
                try:
                    await self.control_server.tell_everyone_info_about_everyone()
                except Exception as e:
                    print(f"⚠️ broadcast INFO_ABOUT_PLAYERS failed: {e}")

                # Refresh agency rosters/snapshot for UIs (use whichever you implemented)
                try:
                    if hasattr(self.control_server, "broadcast_info_about_agencies"):
                        await self.control_server.broadcast_info_about_agencies()
                    elif hasattr(self.control_server, "send_list_of_agencies"):
                        await self.control_server.send_list_of_agencies()
                except Exception as e:
                    print(f"⚠️ agencies refresh failed: {e}")

            except asyncio.IncompleteReadError:
                print("⚠️ JOIN_AGENCY: client disconnected mid-read")
            except Exception as e:
                print(f"❌ JOIN_AGENCY error: {e}")


        
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

                # --- FIX: get shared robustly (player.shared first, then control_server.shared) ---
                shared = getattr(player, "shared", None)
                if shared is None:
                    cs = getattr(self, "control_server", None)
                    shared = getattr(cs, "shared", None) if cs else None

                if shared and getattr(shared, "udp_server", None):
                    udp = shared.udp_server

                    # Building name (from game_desc)
                    bdef = shared.buildings_by_id.get(building_type, {}) if getattr(shared, "buildings_by_id", None) else {}
                    bname = bdef.get("name", f"Building {building_type}")

                    # Planet name via ChunkManager (object -> chunk)
                    planet_name = None
                    cm = getattr(shared, "chunk_manager", None)
                    if cm:
                        chunk = cm.get_chunk_from_object_id(planet_id)
                        if chunk:
                            planet_obj = chunk.get_object_by_id(planet_id)
                            planet_name = getattr(planet_obj, "name", None)

                    where = f" on {planet_name}" if planet_name else f" on planet {planet_id}"
                    msg = f"Upgraded {bname} to level {new_level}{where}"

                    # Send to all members of the agency (notif kind 2 = success)
                    try:
                        await udp.notify_agency(agency.id64, 2, msg)
                        # or fire-and-forget to avoid awaiting in the handler:
                        # asyncio.create_task(udp.notify_agency(agency.id64, 2, msg))
                    except Exception as e:
                        print(f"⚠️ notify_agency failed: {e}")
                else:
                    print("⚠️ shared or udp_server missing; skip agency notification.")

            else:
                print(f"❌ Upgrade failed ({reason}). Needed {cost}, player has {player.money}.")
                # Optional: notify just the requester (kind 1 = failure)
                # shared = getattr(player, "shared", None) or getattr(self.control_server, "shared", None)
                # if shared and getattr(shared, "udp_server", None):
                #     try:
                #         await shared.udp_server.notify_steam_ids([player.steamID], 1, f"Upgrade failed: {reason}")
                #     except Exception as e:
                #         print(f"⚠️ notify_steam_ids failed: {e}")




        elif function_code == PacketType.SELL_RESOURCE:
            resource_type = int.from_bytes(await self.reader.readexactly(2), 'little')
            count = int.from_bytes(await self.reader.readexactly(2), 'little')
            from_planet = int.from_bytes(await self.reader.readexactly(8), 'little')
            player, agency = self._get_player_and_agency()
            if not player or not agency:
                return
            if agency.sell_resource(player, from_planet, resource_type, count):
                print(f"✅ Sold {count} of resource type {resource_type} from planet {from_planet}. "
                      f"Player now has {player.money}.")
            else:
                print(f"❌ Sell failed.")
  

        elif function_code == PacketType.CRAFT_RESOURCES:
            # Read fields
            building_type = struct.unpack('<H', await self.reader.readexactly(2))[0]
            planet_id     = struct.unpack('<Q', await self.reader.readexactly(8))[0]
            recipe_name   = await self._read_cstring()

            player, agency = self._get_player_and_agency()
            if not player or not agency:
                return

            # Map building type -> crafting table name in game_desc.json
            facility_key = None
            try:
                from buildings import BuildingType
                if building_type == int(getattr(BuildingType, "CHEMICAL_LAB", -1)):
                    facility_key = "Chem Lab"
                elif hasattr(BuildingType, "COLLIDER") and building_type == int(BuildingType.COLLIDER):
                    facility_key = "Collider"
            except Exception:
                pass

            if facility_key is None:
                print(f"❌ Craft failed: unsupported building_type={building_type}")
                return

            # (Optional but sensible) Ensure the agency actually has that building on this planet
            has_building = True
            bases = getattr(agency, "bases_to_buildings", {})
            if isinstance(bases, dict):
                lst = bases.get(int(planet_id), [])
                has_building = any(int(getattr(b, "type", -1)) == int(building_type)
                                and getattr(b, "constructed", True)
                                for b in lst)
            if not has_building:
                print(f"❌ Craft failed: no {facility_key} on planet {planet_id}")
                return

            # Look up the recipe
            gd = self.control_server.shared.game_description or {}
            all_tables = gd.get("crafting_recipes", {})
            table = all_tables.get(facility_key, {})
            recipe = table.get(recipe_name)
            if not recipe:
                print(f"❌ Craft failed: unknown recipe '{recipe_name}' for {facility_key}")
                return

            inputs  = recipe.get("inputs", {})   # keys may be strings
            outputs = recipe.get("outputs", {})  # keys may be strings

            # Get the planet inventory
            base_inventories = getattr(agency, "base_inventories", None)
            if not isinstance(base_inventories, dict):
                print("❌ Craft failed: agency has no base_inventories dict")
                return

            planet_inv_raw = base_inventories.get(int(planet_id))
            if planet_inv_raw is None:
                print(f"❌ Craft failed: no inventory on planet {planet_id}")
                return

            # Coerce keys to ints (matches how you do it elsewhere)
            from utils import _coerce_int_keys
            inv = _coerce_int_keys(planet_inv_raw)

            # Check availability
            shortages = []
            for rid_s, need in inputs.items():
                rid = int(rid_s)
                need = int(need)
                have = int(inv.get(rid, 0))
                if have < need:
                    shortages.append((rid, need, have))

            if shortages:
                detail = ", ".join(f"{rid}: need {need}, have {have}" for rid, need, have in shortages)
                print(f"❌ Craft failed: insufficient resources ({detail})")
                return

            # Apply transaction: consume inputs, then produce outputs
            for rid_s, need in inputs.items():
                rid = int(rid_s)
                inv[rid] = int(inv.get(rid, 0)) - int(need)

            for rid_s, give in outputs.items():
                rid = int(rid_s)
                inv[rid] = int(inv.get(rid, 0)) + int(give)

            # Store back
            base_inventories[int(planet_id)] = inv

            print(f"✅ Crafted '{recipe_name}' at planet {planet_id} via {facility_key}.")

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

    async def _read_cstring(self, max_len: int = 256) -> str:
        data = await self.reader.readuntil(b'\x00')  # includes the NUL
        s = data[:-1]  # strip NUL
        if len(s) > max_len:
            s = s[:max_len]
        return s.decode('utf-8', errors='replace')

        
                    
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

