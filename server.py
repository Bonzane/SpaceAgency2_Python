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
import math
import random
from vessel_components import Components
from regions import Region
from gameobjects import ObjectType


class HttpClient:
    def __init__(self):
        timeout = aiohttp.ClientTimeout(total=8, connect=3, sock_read=5)
        self.session = aiohttp.ClientSession(timeout=timeout)

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


            # Force IPv4 host discovery
            def _ipv4_addr():
                import ipaddress, socket
                if getattr(shared_state, "use_manual_host", False) and getattr(shared_state, "manual_host", None):
                    return str(shared_state.manual_host)
                h = getattr(shared_state, "host", "") or ""
                try:
                    if h:
                        ipaddress.IPv4Address(h)
                        return h
                except Exception:
                    pass
                try:
                    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                    s.connect(("8.8.8.8", 80))
                    addr = s.getsockname()[0]
                    s.close()
                    return addr
                except Exception:
                    return "127.0.0.1"

            #A LOT OF STUFF HERE IS BS'ed. I HOPE I AM NOT DUMB ENOUGH TO
            #FORGET TO COME BACK TO THIS
            data = {
                "host": _ipv4_addr(),      # Force IPv4 address
                                        #  The official listing server ignores this, but if someone for some reason
                                        # wants to make their own listing server and allow you to create listings 
                                        #  from one computer for a server running somewhere else, they might choose to implement this.  
                "controlServerTCPPort" : shared_state.external_control_port ,
                "streamingServerUDPPort" : shared_state.external_streaming_port,
                "serverPublicName" : shared_state.server_public_name,
                "gameMode" : shared_state.game_mode,
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
        self.game_mode = "undefined"
        self.control_port = None
        self.streaming_port = None
        self.external_control_port = None
        self.external_streaming_port = None
        self.manual_host = None
        self.use_manual_host = False
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
        self.official_server = False
        self.steam_app_id = 0
        self.steam_publisher_key = ""
        self.steam_stats_watchers: list[dict] = []
        self.steam_achievement_watchers: list[dict] = []
        with open(self.game_desc_path, "r") as game_description_file:
            self.game_description = json.load(game_description_file)
            self.game_buildings_list = self.game_description.get("buildings")
            self.component_data = {
                comp["id"]: comp for comp in self.game_description["components"]
            }
            self.buildings_by_id = {b["id"]: b for b in self.game_buildings_list}
            self.agency_default_attributes = self.game_description.get("agency_default_attributes", {})
            self.game_resources = self.game_description.get("resources", [])

        try:
            with open("steam_stats_watchers.json", "r", encoding="utf-8") as f:
                stats = json.load(f)
            watchers = stats.get("steam_stats_watchers", [])
            if isinstance(watchers, list):
                self.steam_stats_watchers = [a for a in watchers if isinstance(a, dict)]
        except FileNotFoundError:
            self.steam_stats_watchers = []
        except Exception as e:
            print(f"⚠️ Failed to load steam_stats_watchers.json: {e}")
            self.steam_stats_watchers = []

        try:
            with open("achievement_watchers.json", "r", encoding="utf-8") as f:
                achievements = json.load(f)
            watchers = achievements.get("achievement_watchers", [])
            if isinstance(watchers, list):
                self.steam_achievement_watchers = [a for a in watchers if isinstance(a, dict)]
        except FileNotFoundError:
            self.steam_achievement_watchers = []
        except Exception as e:
            print(f"⚠️ Failed to load achievement_watchers.json: {e}")
            self.steam_achievement_watchers = []

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


    async def set_steam_stats(self, steam_id: int, stats: Dict[str, float | int]) -> bool:
        """
        Use Steam Web API to set stats. Only works on official servers.
        """
        if not self.official_server:
            print(f"⚠️ Steam stats blocked: official_server disabled (steam_id={steam_id})")
            return False
        if not self.steam_app_id:
            print(f"⚠️ Steam stats blocked: STEAM_APP_ID missing (steam_id={steam_id})")
            return False
        if not self.steam_publisher_key:
            print(f"⚠️ Steam stats blocked: STEAM_PUBLISHER_KEY missing (steam_id={steam_id})")
            return False
        if not stats:
            print(f"⚠️ Steam stats blocked: no stats provided (steam_id={steam_id})")
            return False

        def _post():
            url = "https://partner.steam-api.com/ISteamUserStats/SetUserStatsForGame/v1/"
            data = {
                "key": self.steam_publisher_key,
                "steamid": str(int(steam_id)),
                "appid": str(int(self.steam_app_id)),
            }
            i = 0
            for name, value in stats.items():
                data[f"name[{i}]"] = str(name)
                data[f"value[{i}]"] = str(value)
                i += 1
            data["count"] = str(i)
            return requests.post(url, data=data, timeout=5)

        try:
            resp = await asyncio.to_thread(_post)
        except Exception as e:
            print(f"⚠️ Steam stats request failed: steam_id={steam_id} stats={list(stats.keys())} err={e}")
            return False

        body = resp.text
        # Debug-only:
        # print(f"📨 Steam stats response: steam_id={steam_id} status={resp.status_code} stats={list(stats.keys())} body={body}")

        if resp.status_code != 200:
            return False

        try:
            payload = resp.json()
        except Exception as e:
            print(f"⚠️ Steam stats JSON parse failed: steam_id={steam_id} stats={list(stats.keys())} err={e}")
            return False

        result = None
        if isinstance(payload, dict):
            result = payload.get("response", {}).get("result")
        if result != 1:
            print(f"⚠️ Steam stats failed: steam_id={steam_id} stats={list(stats.keys())} payload={payload}")
            return False
        # Fetch back the current stats to verify what Steam has recorded.
        def _get():
            url = "https://partner.steam-api.com/ISteamUserStats/GetUserStatsForGame/v2/"
            params = {
                "key": self.steam_publisher_key,
                "steamid": str(int(steam_id)),
                "appid": str(int(self.steam_app_id)),
            }
            return requests.get(url, params=params, timeout=5)

        try:
            get_resp = await asyncio.to_thread(_get)
            print(
                "📨 Steam stats readback: "
                f"steam_id={steam_id} status={get_resp.status_code} body={get_resp.text}"
            )
        except Exception as e:
            print(f"⚠️ Steam stats readback failed: steam_id={steam_id} err={e}")
        return True

    async def set_steam_achievement(
        self,
        steam_id: int,
        achievement_name: str,
        stats: Dict[str, float | int] | None = None,
    ) -> bool:
        """
        Use Steam Web API to unlock an achievement for a user. Only works on official servers.
        """
        if not self.official_server:
            print(f"⚠️ Steam achievement blocked: official_server disabled (steam_id={steam_id})")
            return False
        if not self.steam_app_id:
            print(f"⚠️ Steam achievement blocked: STEAM_APP_ID missing (steam_id={steam_id})")
            return False
        if not self.steam_publisher_key:
            print(f"⚠️ Steam achievement blocked: STEAM_PUBLISHER_KEY missing (steam_id={steam_id})")
            return False
        if not achievement_name:
            print(f"⚠️ Steam achievement blocked: missing achievement name (steam_id={steam_id})")
            return False
        if not stats:
            print(f"⚠️ Steam achievement blocked: missing stats payload (steam_id={steam_id})")
            return False

        def _post_userstats():
            url = "https://partner.steam-api.com/ISteamUserStats/SetUserStatsForGame/v1/"
            data = {
                "key": self.steam_publisher_key,
                "steamid": str(int(steam_id)),
                "appid": str(int(self.steam_app_id)),
                "achievement_count": "1",
                "achievements[0][name]": str(achievement_name),
                "achievements[0][achieved]": "1",
                "achievements[0][unlocktime]": str(int(time.time())),
            }
            i = 0
            for name, value in (stats or {}).items():
                data[f"name[{i}]"] = str(name)
                data[f"value[{i}]"] = str(value)
                i += 1
            data["count"] = str(i)
            return requests.post(url, data=data, timeout=5)
        try:
            resp = await asyncio.to_thread(_post_userstats)
            print(
                "📨 Steam achievement response (userstats): "
                f"steam_id={steam_id} status={resp.status_code} achievement={achievement_name} body={resp.text}"
            )
        except Exception as e:
            print(f"⚠️ Steam achievement request failed: steam_id={steam_id} achievement={achievement_name} err={e}")
            return False

        if resp.status_code != 200:
            return False

        try:
            payload = resp.json()
        except Exception as e:
            print(f"⚠️ Steam achievement JSON parse failed: steam_id={steam_id} achievement={achievement_name} err={e}")
            return False

        result = None
        if isinstance(payload, dict):
            result = payload.get("response", {}).get("result")
        if result != 1:
            print(f"⚠️ Steam achievement failed: steam_id={steam_id} achievement={achievement_name} payload={payload}")
            return False
        # Fetch back the current stats/achievements to verify what Steam has recorded.
        def _get():
            url = "https://partner.steam-api.com/ISteamUserStats/GetUserStatsForGame/v2/"
            params = {
                "key": self.steam_publisher_key,
                "steamid": str(int(steam_id)),
                "appid": str(int(self.steam_app_id)),
            }
            return requests.get(url, params=params, timeout=5)

        try:
            get_resp = await asyncio.to_thread(_get)
            print(
                "📨 Steam achievement readback: "
                f"steam_id={steam_id} status={get_resp.status_code} body={get_resp.text}"
            )
        except Exception as e:
            print(f"⚠️ Steam achievement readback failed: steam_id={steam_id} err={e}")
        return True


    def get_next_agency_id(self):
        while self.next_available_agency_id in self.agencies:
            self.next_available_agency_id += 1
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

    def set_game_mode(self, mode: str):
        self.game_mode = mode

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
        self._network_orb_accum = 0.0

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
                # Advance agency age in in-game days
                sim_days = float(getattr(self.shared, "gamespeed", 0.0)) / 86400.0
                _agency.age_days = float(getattr(_agency, "age_days", 0.0)) + max(0.0, sim_days)
                # Update rolling record stats
                if hasattr(_agency, "update_stat_records"):
                    _agency.update_stat_records()
                #Update quests and award completion rewards
                completed = []
                if hasattr(_agency, "update_quest_progress"):
                    completed = _agency.update_quest_progress() or []
                if completed:
                    for q in completed:
                        qid = str(q.get("id", "")).strip()
                        rewards = q.get("rewards", {}) if isinstance(q, dict) else {}
                        rp = int(rewards.get("rp", 0) or 0)
                        ep = int(rewards.get("ep", 0) or 0)
                        pp = int(rewards.get("pp", 0) or 0)
                        xp = int(rewards.get("xp", 0) or 0)
                        if rp:
                            _agency.research_points += rp
                        if ep:
                            _agency.exploration_points += ep
                        if pp:
                            _agency.publicity_points += pp
                        if xp:
                            _agency.experience_points += xp

                        if qid and hasattr(_agency, "mark_quest_claimed"):
                            _agency.mark_quest_claimed(qid)

                        qname = str(q.get("name", qid or "Quest")) if isinstance(q, dict) else "Quest"
                        chat = self._build_chat_packet(ChatMessage.SERVERGENERAL, 0, f"Quest completed: {qname}")
                        try:
                            await self.broadcast_to_agency(_agency.id64, chat)
                        except Exception as e:
                            print(f"⚠️ Failed to send quest chat: {e}")

                # Update Steam stats (non-quest watchers)
                stat_updates = []
                if hasattr(_agency, "update_steam_stats"):
                    stat_updates = _agency.update_steam_stats() or []
                if stat_updates and getattr(self.shared, "official_server", False):
                    stats_by_name = {}
                    for stat_name, value, meta in stat_updates:
                        official_only = bool(meta.get("official_only", False)) if isinstance(meta, dict) else False
                        if official_only and not getattr(self.shared, "official_server", False):
                            continue
                        stats_by_name[str(stat_name)] = value
                    if stats_by_name:
                        ok = await self._set_steam_stats_for_agency(_agency, stats_by_name)
                        if not ok:
                            print("⚠️ Steam stats update failed for agency")

                # Update Steam achievements (official server only)
                ach_updates = []
                if hasattr(_agency, "update_steam_achievements"):
                    ach_updates = _agency.update_steam_achievements() or []
                if ach_updates and getattr(self.shared, "official_server", False):
                    filtered = []
                    for a in ach_updates:
                        official_only = bool(a.get("official_only", False)) if isinstance(a, dict) else False
                        if official_only and not getattr(self.shared, "official_server", False):
                            continue
                        filtered.append(a)
                    if filtered:
                        ok = await self._set_steam_achievements_for_agency(_agency, filtered)
                        if not ok:
                            print("⚠️ Steam achievement update failed for agency")
            # Network satellite orb drip (once per minute)
            self._network_orb_accum += 1.0
            if self._network_orb_accum >= 60.0:
                try:
                    await self._award_network_sat_orbs()
                except Exception as e:
                    print(f"⚠️ network orb award failed: {e}")
                self._network_orb_accum = 0.0


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

    async def _award_network_sat_orbs(self):
        """
        Once per minute, give RP or PP orbs for deployed comm satellites.
        """
        udp = getattr(self.shared, "udp_server", None)
        if not udp:
            return
        for ag in self.shared.agencies.values():
            for v in ag.get_all_vessels():
                try:
                    if int(getattr(v, "payload", 0)) != int(Components.COMMUNICATIONS_SATELLITE):
                        continue
                    if int(getattr(v, "stage", 1)) != 0:
                        continue
                    if bool(getattr(v, "landed", False)):
                        continue
                    point_type = random.choice([0, 2])  # 0=rp, 2=pp
                    udp.send_xp_orb_to_agency(
                        agency_id=int(getattr(ag, "id64", 0)),
                        point_type=point_type,
                        source_kind=0,
                        vessel_id=int(getattr(v, "object_id", 0)),
                    )
                except Exception as e:
                    print(f"⚠️ network orb skip: {e}")

    async def _set_steam_stats_for_agency(self, agency: Agency, stats: Dict[str, float | int]) -> bool:
        """
        Attempt to set Steam stats for all members. Returns True if any succeeded.
        """
        if not stats:
            return False
        ok_any = False
        for steam_id in getattr(agency, "members", []):
            try:
                ok = await self.shared.set_steam_stats(int(steam_id), stats)
            except Exception as e:
                print(f"⚠️ Steam stats failed for {steam_id}: {e}")
                ok = False
            ok_any = ok_any or ok
        return ok_any

    async def _set_steam_achievements_for_agency(self, agency: Agency, achievements: list[dict]) -> bool:
        """
        Attempt to unlock achievements for all members. Returns True if any succeeded.
        """
        if not achievements:
            return False
        metric_to_stat = {}
        stat_to_metric = {}
        for s in getattr(self.shared, "steam_stats_watchers", []):
            if not isinstance(s, dict):
                continue
            metric = str(s.get("metric", "")).strip()
            stat_name = str(s.get("stat_name", "")).strip()
            if metric and stat_name and metric not in metric_to_stat:
                metric_to_stat[metric] = stat_name
            if metric and stat_name and stat_name not in stat_to_metric:
                stat_to_metric[stat_name] = metric
        ok_any = False
        for a in achievements:
            ach_id = str(a.get("id", "")).strip()
            ach_name = str(a.get("steam_id", "") or a.get("name", "") or ach_id).strip()
            if not ach_name:
                continue
            metric = str(a.get("metric", "")).strip()
            stat_name = str(a.get("stat_name", "")).strip() or str(a.get("progress_stat", "")).strip()
            if not metric and stat_name:
                metric = stat_to_metric.get(stat_name, stat_name)
            if metric and metric in stat_to_metric:
                metric = stat_to_metric.get(metric, metric)
            stat_name = stat_name or metric_to_stat.get(metric, "")
            if not stat_name:
                print(f"⚠️ Steam achievement blocked: no stat mapping for {ach_name}")
                continue
            try:
                stat_value = int(getattr(agency, "_steam_stat_metric_value")(metric))
            except Exception:
                stat_value = 0
            stats_payload = {stat_name: stat_value}
            ok_for_achievement = False
            for steam_id in getattr(agency, "members", []):
                try:
                    ok = await self.shared.set_steam_achievement(
                        int(steam_id),
                        ach_name,
                        stats=stats_payload,
                    )
                except Exception as e:
                    print(f"⚠️ Steam achievement failed for {steam_id}: {e}")
                    ok = False
                ok_for_achievement = ok_for_achievement or ok
                ok_any = ok_any or ok
            if ok_for_achievement and hasattr(agency, "mark_achievement_unlocked"):
                agency.mark_achievement_unlocked(ach_id)
        return ok_any

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
        packet += struct.pack('<H',  vessel.seats_capacity)
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
        self._region_name_cache = {}

    def _region_display_name(self, region_id: int) -> str:
        # Friendly display names; fall back to enum name
        cache_key = int(region_id) & 0xFF
        if cache_key in self._region_name_cache:
            return self._region_name_cache[cache_key]
        friendly = {
            Region.UNDEFINED: "Space",
            Region.SPACE: "Space",
            Region.EARTH_CLOSE: "Near Earth",
            Region.EARTH_NEAR: "Near Earth",
            Region.EARTH_DISTANT: "Earth",
            Region.MOON_NEAR: "Near Moon",
            Region.MARS_CLOSE: "Near Mars",
            Region.MARS_NEAR: "Near Mars",
            Region.MARS_DISTANT: "Mars",
            Region.VENUS_CLOSE: "Near Venus",
            Region.VENUS_NEAR: "Near Venus",
            Region.VENUS_DISTANT: "Venus",
            Region.MERCURY_CLOSE: "Near Mercury",
            Region.MERCURY_NEAR: "Near Mercury",
            Region.MERCURY_DISTANT: "Mercury",
            Region.ASTEROID_BELT: "Asteroid Belt",
            Region.JUPITER_CLOSE: "Near Jupiter",
            Region.JUPITER_NEAR: "Near Jupiter",
            Region.JUPITER_DISTANT: "Jupiter",
            Region.SATURN_CLOSE: "Near Saturn",
            Region.SATURN_NEAR: "Near Saturn",
            Region.SATURN_DISTANT: "Saturn",
            Region.URANUS_CLOSE: "Near Uranus",
            Region.URANUS_NEAR: "Near Uranus",
            Region.URANUS_DISTANT: "Uranus",
            Region.NEPTUNE_CLOSE: "Near Neptune",
            Region.NEPTUNE_NEAR: "Near Neptune",
            Region.NEPTUNE_DISTANT: "Neptune",
            Region.TRANS_NEPTUNIAN: "Trans-Neptunian Space",
            Region.KUIPER_BELT: "Kuiper Belt",
            Region.TERMINATION_SHOCK: "Termination Shock",
            Region.HELIOSHEATH: "Heliosheath",
            Region.HELIOPAUSE: "Heliopause",
            Region.INTRASTELLAR_WINDLESS: "Intrastellar Windless Space",
            Region.INNER_OORT_CLOUD: "Inner Oort Cloud",
            Region.OUTER_OORT_CLOUD: "Outer Oort Cloud",
        }
        name = friendly.get(Region(cache_key), None) if cache_key in Region._value2member_map_ else None
        if name is None:
            try:
                name = Region(cache_key).name
            except Exception:
                name = "UNKNOWN_REGION"
        self._region_name_cache[cache_key] = name
        return name

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

        elif data[0] == DataGramPacketType.CAMERA_CONTEXT:
            # Client sends: [opcode][int64 x][int64 y]
            if len(data) < 1 + 16:
                print("⚠️ CAMERA_CONTEXT packet too short")
                return
            key = (ip, port)
            session = self.shared.udp_endpoint_to_session.get(key)
            if not session:
                print(f"❌ Unknown session for {key}")
                return
            player = getattr(session, "player", None)
            if not player:
                print(f"❌ CAMERA_CONTEXT: no player bound to session {getattr(session, 'temp_id', 0)}")
                return
            try:
                cam_x, cam_y = struct.unpack('<qq', data[1:17])
            except Exception:
                print("⚠️ CAMERA_CONTEXT failed to unpack coords")
                return

            region_id = int(Region.SPACE)
            # Find nearest planet in the player's current chunk
            chunk_key = (getattr(player, "galaxy", 1), getattr(player, "system", 1))
            chunk = self.shared.chunk_manager.loaded_chunks.get(chunk_key)
            if chunk:
                try:
                    nearest = None
                    nearest_dist = float("inf")
                    sun_pos = None
                    neptune_pos = None
                    region_candidates = []
                    for obj in getattr(chunk, "objects", []):
                        if not hasattr(obj, "check_in_region"):
                            continue
                        if getattr(obj, "object_type", None) == getattr(ObjectType, "SUN", None):
                            sun_pos = getattr(obj, "position", (0.0, 0.0))
                        if getattr(obj, "object_type", None) == getattr(ObjectType, "NEPTUNE", None):
                            neptune_pos = getattr(obj, "position", (0.0, 0.0))
                        ox, oy = getattr(obj, "position", (0.0, 0.0))
                        d = math.hypot(cam_x - ox, cam_y - oy)
                        try:
                            reg = obj.check_in_region(d)
                            if reg is not None:
                                region_candidates.append((d, int(reg)))
                        except Exception:
                            pass
                        if d < nearest_dist:
                            nearest_dist = d
                            nearest = obj
                    if region_candidates:
                        region_candidates.sort(key=lambda x: x[0])  # smallest distance wins
                        region_id = region_candidates[0][1]
                    elif nearest:
                        r = nearest.check_in_region(nearest_dist)
                        if r is None:
                            region_id = int(Region.SPACE)
                        else:
                            region_id = int(r)
                    # Solar-system special regions based on distance from the sun
                    if sun_pos and chunk_key == (1, 1):
                        sx, sy = sun_pos
                        cam_rad = math.hypot(cam_x - sx, cam_y - sy)
                        # Define band edges (km)
                        KUIPER_START = 4.5e9
                        KUIPER_END = 7.5e9
                        # Heliocentric bands (km)
                        TERM_START = 1.2566221139e10
                        TERM_END = 1.4062200846e10
                        HELIO_START = 1.4062200846e10
                        HELIO_END = 1.8193110279e10
                        PAUSE_CENTER = 1.8193110279e10
                        PAUSE_HALF_WIDTH = 0.01 * PAUSE_CENTER  # 1% band
                        WINDLESS_START = 1.8193110279e10
                        WINDLESS_END = 3.0e11  # up to Inner Oort start
                        INNER_OORT_START = 3.0e11
                        INNER_OORT_END = 1.5e13
                        OUTER_OORT_START = 1.5e13
                        OUTER_OORT_END = 2.0e13

                        # Priority: farthest first so outer bands override nearer ones
                        if OUTER_OORT_START < cam_rad <= OUTER_OORT_END:
                            region_id = int(Region.OUTER_OORT_CLOUD)
                        elif INNER_OORT_START < cam_rad <= INNER_OORT_END:
                            region_id = int(Region.INNER_OORT_CLOUD)
                        elif WINDLESS_START < cam_rad <= WINDLESS_END:
                            region_id = int(Region.INTRASTELLAR_WINDLESS)
                        elif (PAUSE_CENTER - PAUSE_HALF_WIDTH) <= cam_rad <= (PAUSE_CENTER + PAUSE_HALF_WIDTH):
                            region_id = int(Region.HELIOPAUSE)
                        elif HELIO_START <= cam_rad <= HELIO_END:
                            region_id = int(Region.HELIOSHEATH)
                        elif TERM_START <= cam_rad <= TERM_END:
                            region_id = int(Region.TERMINATION_SHOCK)
                        elif KUIPER_START <= cam_rad <= KUIPER_END:
                            region_id = int(Region.KUIPER_BELT)
                        elif neptune_pos:
                            nx, ny = neptune_pos
                            neptune_dist = math.hypot(nx - sx, ny - sy)
                            # Trans-Neptunian only in two slices: just beyond Neptune up to Kuiper start,
                            # or between Kuiper end and Termination Shock start.
                            if (neptune_dist < cam_rad < KUIPER_START) or (KUIPER_END < cam_rad < HELIO_START):
                                region_id = int(Region.TRANS_NEPTUNIAN)
                        # Safety: if we somehow still marked Trans-Neptunian but are past heliosphere bands, override.
                        if region_id == int(Region.TRANS_NEPTUNIAN) and cam_rad >= HELIO_START:
                            if HELIO_START <= cam_rad <= HELIO_END:
                                region_id = int(Region.HELIOSHEATH)
                            elif (PAUSE_CENTER - PAUSE_HALF_WIDTH) <= cam_rad <= (PAUSE_CENTER + PAUSE_HALF_WIDTH):
                                region_id = int(Region.HELIOPAUSE)
                            elif WINDLESS_START < cam_rad <= WINDLESS_END:
                                region_id = int(Region.INTRASTELLAR_WINDLESS)
                            elif INNER_OORT_START < cam_rad <= INNER_OORT_END:
                                region_id = int(Region.INNER_OORT_CLOUD)
                            elif OUTER_OORT_START < cam_rad <= OUTER_OORT_END:
                                region_id = int(Region.OUTER_OORT_CLOUD)
                except Exception as e:
                    print(f"⚠️ CAMERA_CONTEXT region calc failed: {e}")

            # In-game day: use agency age_days if available, else 0
            game_day = 0.0
            try:
                agency = self.control.shared.agencies.get(int(getattr(player, "agency_id", 0)))
                if agency is not None:
                    game_day = float(getattr(agency, "age_days", 0.0))
            except Exception:
                pass

            resp = bytearray()
            resp.append(DataGramPacketType.CAMERA_CONTEXT_REPLY)
            resp += struct.pack('<Bf', region_id & 0xFF, float(game_day))
            self.transport.sendto(resp, addr)

        elif data[0] == DataGramPacketType.REGION_NAME_REQUEST:
            # Client payload: u8 region_id
            if len(data) < 2:
                print("⚠️ REGION_NAME_REQUEST packet too short")
                return
            region_id = int(data[1])
            name = self._region_display_name(region_id)
            resp = bytearray()
            resp.append(DataGramPacketType.REGION_NAME_REQUEST)
            resp.append(region_id & 0xFF)
            resp += name.encode('utf-8') + b'\x00'
            self.transport.sendto(resp, addr)


        elif data[0] == DataGramPacketType.RESOLVE_VESSEL:
            if len(data) < 9:
                print("⚠️ Invalid RESOLVE_VESSEL packet length."); return
            vessel_id = int.from_bytes(data[1:9], 'little')
            key = (ip, port)
            session = self.shared.udp_endpoint_to_session.get(key)
            if not session:
                print(f"❌ Unknown session for {key}"); return
            player = session.player
            if not player:
                print(f"❌ No player bound to session {session.temp_id}"); return
            chunk_key = (player.galaxy, player.system)
            chunk = self.shared.chunk_manager.loaded_chunks.get(chunk_key)
            if not chunk:
                print(f"❌ Couldn't find chunk {chunk_key}"); return
            vessel = chunk.get_object_by_id(vessel_id)

            # Ensure cargo dict exists on the vessel
            if vessel:
                self._ensure_vessel_cargo(vessel)
            else:
                print(f"⚠️ RESOLVE_VESSEL: vessel {vessel_id} not found in chunk {chunk_key}")
                return

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

        elif data[0] == DataGramPacketType.BOARD_ASTRONAUT:
            # [1] opcode + [4] astronaut_id (u32) + [8] vessel_id (u64)
            if len(data) < 1 + 4 + 8:
                print("⚠️ BOARD_ASTRONAUT: packet too short")
                return

            astro_id  = int.from_bytes(data[1:5],  'little', signed=False)
            vessel_id = int.from_bytes(data[5:13], 'little', signed=False)

            key = (ip, port)
            session = self.shared.udp_endpoint_to_session.get(key)
            if not session or not session.alive:
                print(f"❌ Unknown or dead session for {key}")
                return
            player = getattr(session, "player", None)
            if not player:
                print(f"❌ No player bound to session {getattr(session, 'temp_id', 0)}")
                return

            # find vessel in player's current chunk
            chunk_key = (player.galaxy, player.system)
            chunk = self.shared.chunk_manager.loaded_chunks.get(chunk_key)
            if not chunk:
                self._udp_send_to_session(session, self.build_notification_packet(1, "Board failed: chunk not loaded"))
                return
            vessel = chunk.get_object_by_id(vessel_id)
            if not vessel:
                self._udp_send_to_session(session, self.build_notification_packet(1, "Board failed: vessel not found"))
                return

            # agency ownership check (change to require controller if you want)
            if getattr(vessel, "agency_id", None) != getattr(player, "agency_id", None):
                self._udp_send_to_session(session, self.build_notification_packet(1, "Board failed: not your agency's vessel"))
                return

            agency = self.shared.agencies.get(player.agency_id)
            if not agency:
                self._udp_send_to_session(session, self.build_notification_packet(1, "Board failed: agency not found"))
                return

            ok, reason = agency.move_astronaut_to_vessel(astro_id, vessel)
            if ok:
                name = getattr(agency.astronauts.get(astro_id), "name", f"Astronaut {astro_id}")
                self._udp_send_to_session(session, self.build_notification_packet(2, f"{name} boarded."))
                # UI will catch up via next agency gamestate tick
                print(f"🧑‍🚀 BOARD ok: astro={astro_id} -> vessel={vessel_id}")
            else:
                self._udp_send_to_session(session, self.build_notification_packet(1, f"Board failed: {reason}"))
                print(f"🧑‍🚀 BOARD fail({reason}): astro={astro_id} -> vessel={vessel_id}")

        elif data[0] == DataGramPacketType.UNBOARD_ASTRONAUT:
            # [1] opcode + [4] astronaut_id (u32) + [8] vessel_id (u64)
            if len(data) < 1 + 4 + 8:
                print("⚠️ UNBOARD_ASTRONAUT: packet too short")
                return

            astro_id  = int.from_bytes(data[1:5],  'little', signed=False)
            vessel_id = int.from_bytes(data[5:13], 'little', signed=False)

            key = (ip, port)
            session = self.shared.udp_endpoint_to_session.get(key)
            if not session or not session.alive:
                print(f"❌ Unknown or dead session for {key}")
                return
            player = getattr(session, "player", None)
            if not player:
                print(f"❌ No player bound to session {getattr(session, 'temp_id', 0)}")
                return

            chunk_key = (player.galaxy, player.system)
            chunk = self.shared.chunk_manager.loaded_chunks.get(chunk_key)
            if not chunk:
                self._udp_send_to_session(session, self.build_notification_packet(1, "Unboard failed: chunk not loaded"))
                return
            vessel = chunk.get_object_by_id(vessel_id)
            if not vessel:
                self._udp_send_to_session(session, self.build_notification_packet(1, "Unboard failed: vessel not found"))
                return

            if getattr(vessel, "agency_id", None) != getattr(player, "agency_id", None):
                self._udp_send_to_session(session, self.build_notification_packet(1, "Unboard failed: not your agency's vessel"))
                return

            agency = self.shared.agencies.get(player.agency_id)
            if not agency:
                self._udp_send_to_session(session, self.build_notification_packet(1, "Unboard failed: agency not found"))
                return

            ok, reason = agency.move_astronaut_off_vessel(astro_id, vessel)
            if ok:
                name = getattr(agency.astronauts.get(astro_id), "name", f"Astronaut {astro_id}")
                self._udp_send_to_session(session, self.build_notification_packet(2, f"{name} unboarded."))
                print(f"🧑‍🚀 UNBOARD ok: astro={astro_id} <- vessel={vessel_id}")
            else:
                self._udp_send_to_session(session, self.build_notification_packet(1, f"Unboard failed: {reason}"))
                print(f"🧑‍🚀 UNBOARD fail({reason}): astro={astro_id} <- vessel={vessel_id}")

        elif data[0] == DataGramPacketType.CHANGE_ASTRONAUT_SUIT:
            # [1] opcode + [4] astronaut_id (u32) + [2] suit_id (u16)
            if len(data) < 1 + 4 + 2:
                print("⚠️ CHANGE_ASTRONAUT_SUIT: packet too short")
                return

            astro_id = int.from_bytes(data[1:5], 'little', signed=False)
            suit_id  = int.from_bytes(data[5:7], 'little', signed=False)

            key = (ip, port)
            session = self.shared.udp_endpoint_to_session.get(key)
            if not session or not session.alive:
                print(f"❌ Unknown or dead session for {key}")
                return
            player = getattr(session, "player", None)
            if not player:
                print(f"❌ No player bound to session {getattr(session, 'temp_id', 0)}")
                return

            agency = self.shared.agencies.get(getattr(player, "agency_id", 0))
            if not agency:
                self._udp_send_to_session(session, self.build_notification_packet(1, "Suit change failed: agency not found"))
                return

            # ownership: astronaut must belong to this agency
            astro = agency.astronauts.get(int(astro_id))
            if not astro or int(getattr(astro, "agency_id", -1)) != int(agency.id64):
                self._udp_send_to_session(session, self.build_notification_packet(1, "Suit change failed: astronaut not found or not yours"))
                return

            # apply
            ok, reason = agency.set_astronaut_suit(astro_id, suit_id) if hasattr(agency, "set_astronaut_suit") else (True, "ok")
            if ok:
                # if no helper, set directly:
                if not hasattr(agency, "set_astronaut_suit"):
                    s = suit_id if suit_id >= 0 else 0
                    astro.suit_id = int(s)

                name = getattr(astro, "name", f"Astronaut {astro_id}")
                msg = f"{{{int(player.steamID)}}} changed {name}'s suit to {int(astro.suit_id)}"
                try:
                    chat_pkt = self._build_chat_packet(ChatMessage.SERVERGENERAL, 0, msg)
                    loop = asyncio.get_running_loop()
                    loop.create_task(self.broadcast_to_agency(agency.id64, chat_pkt))
                except Exception as e:
                    print(f"⚠️ Failed to send suit change chat: {e}")
                print(f"🧑‍🚀 Suit changed: astro={astro_id} -> suit={int(astro.suit_id)}")
                # UI will pick this up on the next agency gamestate tick
            else:
                self._udp_send_to_session(session, self.build_notification_packet(1, f"Suit change failed: {reason}"))
                print(f"🧑‍🚀 Suit change failed({reason}): astro={astro_id} -> suit={suit_id}")

        elif data[0] == DataGramPacketType.CHANGE_ASTRONAUT_NAME:
            # [1] opcode + [4] astronaut_id (u32) + cstring name
            if len(data) < 1 + 4 + 1:
                print("⚠️ CHANGE_ASTRONAUT_NAME: packet too short")
                return

            astro_id = int.from_bytes(data[1:5], 'little', signed=False)
            end = data.find(b'\x00', 5)
            if end == -1:
                print("⚠️ CHANGE_ASTRONAUT_NAME: missing null terminator")
                return
            raw_name = data[5:end].decode('utf-8', errors='replace')
            new_name = raw_name.strip()

            key = (ip, port)
            session = self.shared.udp_endpoint_to_session.get(key)
            if not session or not session.alive:
                print(f"❌ Unknown or dead session for {key}")
                return
            player = getattr(session, "player", None)
            if not player:
                print(f"❌ No player bound to session {getattr(session, 'temp_id', 0)}")
                return

            agency = self.shared.agencies.get(getattr(player, "agency_id", 0))
            if not agency:
                self._udp_send_to_session(session, self.build_notification_packet(1, "Name change failed: agency not found"))
                return

            astro = agency.astronauts.get(int(astro_id))
            if not astro or int(getattr(astro, "agency_id", -1)) != int(agency.id64):
                self._udp_send_to_session(session, self.build_notification_packet(1, "Name change failed: astronaut not found or not yours"))
                return

            if not new_name:
                self._udp_send_to_session(session, self.build_notification_packet(1, "Name change failed: name is empty"))
                return

            max_len = 32
            if len(new_name) > max_len:
                new_name = new_name[:max_len].rstrip()

            astro.name = new_name
            msg = f"{{{int(player.steamID)}}} renamed astronaut {astro_id} to {astro.name}"
            try:
                chat_pkt = self._build_chat_packet(ChatMessage.SERVERGENERAL, 0, msg)
                loop = asyncio.get_running_loop()
                loop.create_task(self.broadcast_to_agency(agency.id64, chat_pkt))
            except Exception as e:
                print(f"⚠️ Failed to send rename chat: {e}")
            print(f"🧑‍🚀 Name changed: astro={astro_id} -> {astro.name}")
            # UI will pick this up on the next agency gamestate tick

        elif data[0] == DataGramPacketType.UNBOARD_ASTRONAUT:
            # [1] opcode + [4] astronaut_id (u32) + [8] vessel_id (u64)
            if len(data) < 1 + 4 + 8:
                print("⚠️ UNBOARD_ASTRONAUT: packet too short")
                return

            astro_id  = int.from_bytes(data[1:5],  'little', signed=False)
            vessel_id = int.from_bytes(data[5:13], 'little', signed=False)

            key = (ip, port)
            session = self.shared.udp_endpoint_to_session.get(key)
            if not session or not session.alive:
                print(f"❌ Unknown or dead session for {key}")
                return
            player = getattr(session, "player", None)
            if not player:
                print(f"❌ No player bound to session {getattr(session, 'temp_id', 0)}")
                return

            chunk_key = (player.galaxy, player.system)
            chunk = self.shared.chunk_manager.loaded_chunks.get(chunk_key)
            if not chunk:
                self._udp_send_to_session(session, self.build_notification_packet(1, "Unboard failed: chunk not loaded"))
                return
            vessel = chunk.get_object_by_id(vessel_id)
            if not vessel:
                self._udp_send_to_session(session, self.build_notification_packet(1, "Unboard failed: vessel not found"))
                return

            if getattr(vessel, "agency_id", None) != getattr(player, "agency_id", None):
                self._udp_send_to_session(session, self.build_notification_packet(1, "Unboard failed: not your agency's vessel"))
                return

            agency = self.shared.agencies.get(player.agency_id)
            if not agency:
                self._udp_send_to_session(session, self.build_notification_packet(1, "Unboard failed: agency not found"))
                return

            ok, reason = agency.move_astronaut_off_vessel(astro_id, vessel)
            if ok:
                name = getattr(agency.astronauts.get(astro_id), "name", f"Astronaut {astro_id}")
                self._udp_send_to_session(session, self.build_notification_packet(2, f"{name} unboarded."))
                print(f"🧑‍🚀 UNBOARD ok: astro={astro_id} <- vessel={vessel_id}")
            else:
                self._udp_send_to_session(session, self.build_notification_packet(1, f"Unboard failed: {reason}"))
                print(f"🧑‍🚀 UNBOARD fail({reason}): astro={astro_id} <- vessel={vessel_id}")

        elif data[0] == DataGramPacketType.CHANGE_ASTRONAUT_SUIT:
            # [1] opcode + [4] astronaut_id (u32) + [2] suit_id (u16)
            if len(data) < 1 + 4 + 2:
                print("⚠️ CHANGE_ASTRONAUT_SUIT: packet too short")
                return

            astro_id = int.from_bytes(data[1:5], 'little', signed=False)
            suit_id  = int.from_bytes(data[5:7], 'little', signed=False)

            key = (ip, port)
            session = self.shared.udp_endpoint_to_session.get(key)
            if not session or not session.alive:
                print(f"❌ Unknown or dead session for {key}")
                return
            player = getattr(session, "player", None)
            if not player:
                print(f"❌ No player bound to session {getattr(session, 'temp_id', 0)}")
                return

            agency = self.shared.agencies.get(getattr(player, "agency_id", 0))
            if not agency:
                self._udp_send_to_session(session, self.build_notification_packet(1, "Suit change failed: agency not found"))
                return

            # ownership: astronaut must belong to this agency
            astro = agency.astronauts.get(int(astro_id))
            if not astro or int(getattr(astro, "agency_id", -1)) != int(agency.id64):
                self._udp_send_to_session(session, self.build_notification_packet(1, "Suit change failed: astronaut not found or not yours"))
                return

            # apply
            ok, reason = agency.set_astronaut_suit(astro_id, suit_id) if hasattr(agency, "set_astronaut_suit") else (True, "ok")
            if ok:
                # if no helper, set directly:
                if not hasattr(agency, "set_astronaut_suit"):
                    s = suit_id if suit_id >= 0 else 0
                    astro.suit_id = int(s)

                name = getattr(astro, "name", f"Astronaut {astro_id}")
                msg = f"{{{int(player.steamID)}}} changed {name}'s suit to {int(astro.suit_id)}"
                try:
                    chat_pkt = self.control_server._build_chat_packet(ChatMessage.SERVERGENERAL, 0, msg)
                    loop = asyncio.get_running_loop()
                    loop.create_task(self.control_server.broadcast_to_agency(agency.id64, chat_pkt))
                except Exception as e:
                    print(f"⚠️ Failed to send suit change chat: {e}")
                print(f"🧑‍🚀 Suit changed: astro={astro_id} -> suit={int(astro.suit_id)}")
                # UI will pick this up on the next agency gamestate tick
            else:
                self._udp_send_to_session(session, self.build_notification_packet(1, f"Suit change failed: {reason}"))
                print(f"🧑‍🚀 Suit change failed({reason}): astro={astro_id} -> suit={suit_id}")


        elif data[0] == DataGramPacketType.GET_JETTISON:
            # layout (request): [u8 opcode][u64 object_id]
            if len(data) < 1 + 8:
                print("⚠️ GET_JETTISON: packet too short")
                return

            asked_oid = int.from_bytes(data[1:9], 'little', signed=False)

            key = (ip, port)
            session = self.shared.udp_endpoint_to_session.get(key)
            if not session or not session.alive:
                print(f"❌ Unknown or dead session for {key}")
                return
            player = getattr(session, "player", None)
            if not player:
                print(f"❌ No player bound to session {getattr(session,'temp_id',0)}")
                return

            # try player’s current chunk first
            chunk_key = (player.galaxy, player.system)
            chunk = self.shared.chunk_manager.loaded_chunks.get(chunk_key)

            obj = None
            if chunk:
                obj = chunk.get_object_by_id(asked_oid)

            # fallback: global index → chunk
            if obj is None:
                cm = self.shared.chunk_manager
                c = cm.get_chunk_from_object_id(asked_oid)
                if c:
                    obj = c.get_object_by_id(asked_oid)

            comp_id = 0
            if obj is None:
                print(f"GET_JETTISON: oid={asked_oid} not found in chunk {chunk_key} (may have expired).")
            else:
                # validate it’s actually a jettisoned component
                if getattr(obj, "object_type", None) == ObjectType.JETTISONED_COMPONENT:
                    comp_id = int(getattr(obj, "component_id", 0))
                    if comp_id == 0:
                        # backwards-compat: if you only stored index earlier, you can’t reconstruct reliably here.
                        # keep 0 and log.
                        print(f"GET_JETTISON: oid={asked_oid} has no component_id (old spawn?).")
                else:
                    print(f"GET_JETTISON: oid={asked_oid} is not a jettisoned component (type={getattr(obj,'object_type',None)}).")

            # Build reply: [u8 opcode][u64 object_id][u16 component_id]
            resp = bytearray()
            resp.append(DataGramPacketType.GET_JETTISON)
            resp += struct.pack('<Q', asked_oid)
            resp += struct.pack('<H', comp_id & 0xFFFF)

            self.transport.sendto(resp, addr)

        elif data[0] == DataGramPacketType.CARGO_ADD:
            # [u8 opcode][u64 vessel_id][u64 planet_id][u16 n][n x (u32 rid, u32 amt)]
            if len(data) < 1 + 8 + 8 + 2:
                print("⚠️ CARGO_ADD: packet too short"); return

            vessel_id = int.from_bytes(data[1:9],  "little", signed=False)
            planet_id = int.from_bytes(data[9:17], "little", signed=False)
            n_pairs   = int.from_bytes(data[17:19], "little", signed=False)
            pairs, _  = self._extract_resource_pairs(data, 19, n_pairs)
            if pairs is None:
                print("⚠️ CARGO_ADD: pairs truncated"); return

            key = (ip, port)
            session = self.shared.udp_endpoint_to_session.get(key)
            if not session or not session.alive:
                print(f"❌ Unknown or dead session for {key}"); return
            player = getattr(session, "player", None)
            if not player:
                print(f"❌ No player bound to session {getattr(session, 'temp_id', 0)}"); return

            # resolve vessel via the player's current chunk (your standard access pattern)
            chunk_key = (player.galaxy, player.system)
            chunk = self.shared.chunk_manager.loaded_chunks.get(chunk_key)
            if not chunk:
                self._udp_send_to_session(session, self.build_notification_packet(1, "Cargo load failed: chunk not loaded")); return
            vessel = chunk.get_object_by_id(vessel_id)
            if not vessel:
                self._udp_send_to_session(session, self.build_notification_packet(1, "Cargo load failed: vessel not found")); return

            # ensure a cargo dict lives on the vessel (important for Vessel.__eq__ too)
            cargo = self._ensure_vessel_cargo(vessel)
            if cargo is None:
                self._udp_send_to_session(session, self.build_notification_packet(1, "Cargo load failed: cannot attach cargo to vessel")); return

            # must control the vessel (same rule you already use elsewhere)
            if int(getattr(vessel, "controlled_by", 0)) not in (int(getattr(player, "steamID", 0)), int(getattr(player, "steam_id", 0))):
                self._udp_send_to_session(session, self.build_notification_packet(1, "Cargo load failed: you must be controlling this vessel")); return

            # same-planet rule
            if not self._guess_landed_on_planet(vessel, int(planet_id), chunk):
                self._udp_send_to_session(session, self.build_notification_packet(1, "Cargo load failed: vessel must be landed on that planet")); return

            # base inventory for this planet
            agency, inv = self._get_agency_base_inventory(player, int(planet_id))
            if not agency or inv is None:
                self._udp_send_to_session(session, self.build_notification_packet(1, "Cargo load failed: base inventory unavailable")); return
            cap = int(getattr(vessel, "cargo_capacity", 0)) or 0
            used = sum(int(v) for v in cargo.values())
            space_left = max(0, cap - used) if cap > 0 else None  # None means unlimited
            total_loaded = 0
            for rid, want in pairs:
                want = int(want)
                if want <= 0:
                    continue
                have = int(inv.get(int(rid), 0))
                if have <= 0:
                    continue

                take = min(want, have)
                if space_left is not None:
                    take = min(take, space_left - total_loaded)

                if take <= 0:
                    continue

                cargo[int(rid)] = int(cargo.get(int(rid), 0)) + take
                inv[int(rid)] = have - take
                total_loaded += take

            self._udp_send_to_session(session, self.build_cargo_state_packet(vessel, int(planet_id)))
            if total_loaded == 0:
                self._udp_send_to_session(session, self.build_notification_packet(1, "Nothing loaded (no base stock)."))

        elif data[0] == DataGramPacketType.CARGO_REMOVE:
            # [u8 opcode][u64 vessel_id][u64 planet_id][u16 n][n x (u32 rid, u32 amt)]
            if len(data) < 1 + 8 + 8 + 2:
                print("⚠️ CARGO_REMOVE: packet too short"); return

            vessel_id = int.from_bytes(data[1:9],  "little", signed=False)
            planet_id = int.from_bytes(data[9:17], "little", signed=False)
            n_pairs   = int.from_bytes(data[17:19], "little", signed=False)
            pairs, _  = self._extract_resource_pairs(data, 19, n_pairs)
            if pairs is None:
                print("⚠️ CARGO_REMOVE: pairs truncated"); return

            key = (ip, port)
            session = self.shared.udp_endpoint_to_session.get(key)
            if not session or not session.alive:
                print(f"❌ Unknown or dead session for {key}"); return
            player = getattr(session, "player", None)
            if not player:
                print(f"❌ No player bound to session {getattr(session, 'temp_id', 0)}"); return

            chunk_key = (player.galaxy, player.system)
            chunk = self.shared.chunk_manager.loaded_chunks.get(chunk_key)
            if not chunk:
                self._udp_send_to_session(session, self.build_notification_packet(1, "Cargo unload failed: chunk not loaded")); return
            vessel = chunk.get_object_by_id(vessel_id)
            if not vessel:
                self._udp_send_to_session(session, self.build_notification_packet(1, "Cargo unload failed: vessel not found")); return

            cargo = self._ensure_vessel_cargo(vessel)
            if cargo is None:
                self._udp_send_to_session(session, self.build_notification_packet(1, "Cargo unload failed: cannot access vessel cargo")); return

            if int(getattr(vessel, "controlled_by", 0)) not in (int(getattr(player, "steamID", 0)), int(getattr(player, "steam_id", 0))):
                self._udp_send_to_session(session, self.build_notification_packet(1, "Cargo unload failed: you must be controlling this vessel")); return

            if not self._guess_landed_on_planet(vessel, int(planet_id), chunk):
                self._udp_send_to_session(session, self.build_notification_packet(1, "Cargo unload failed: vessel must be landed on that planet")); return

            agency, inv = self._get_agency_base_inventory(player, int(planet_id))
            if not agency or inv is None:
                self._udp_send_to_session(session, self.build_notification_packet(1, "Cargo unload failed: base inventory unavailable")); return

            total_unloaded = 0
            for rid, want in pairs:
                want = int(want)
                if want <= 0: continue
                have = int(cargo.get(int(rid), 0))
                if have <= 0: continue

                take = min(want, have)
                left = have - take
                if left > 0:
                    cargo[int(rid)] = left
                else:
                    cargo.pop(int(rid), None)

                inv[int(rid)] = int(inv.get(int(rid), 0)) + take
                total_unloaded += take

            # Quest: moon rock returned to Earth (resource id 15 to planet 2)
            try:
                if int(planet_id) == 2:
                    delivered = {int(rid): take for rid, _ in pairs if (take := min(int(cargo.get(int(rid), 0)), int(inv.get(int(rid), 0))))}
                    if int(delivered.get(15, 0)) > 0:
                        if agency and hasattr(agency, "record_quest_metric"):
                            agency.record_quest_metric("moon_rock_earth", 1)
            except Exception as e:
                print(f"⚠️ moon rock quest check failed: {e}")

            self._udp_send_to_session(session, self.build_cargo_state_packet(vessel, int(planet_id)))
            if total_unloaded == 0:
                self._udp_send_to_session(session, self.build_notification_packet(1, "Nothing unloaded (no cargo onboard)."))

        elif data[0] == DataGramPacketType.CARGO_STATE:
            # [u8 opcode][u64 vessel_id]
            if len(data) < 1 + 8:
                print("⚠️ CARGO_STATE: packet too short"); return

            vessel_id = int.from_bytes(data[1:9],  "little", signed=False)
            key = (ip, port)
            session = self.shared.udp_endpoint_to_session.get(key)
            if not session or not session.alive:
                print(f"❌ Unknown or dead session for {key}"); return
            player = getattr(session, "player", None)
            if not player:
                print(f"❌ No player bound to session {getattr(session, 'temp_id', 0)}"); return
            chunk_key = (player.galaxy, player.system)
            chunk = self.shared.chunk_manager.loaded_chunks.get(chunk_key)
            if not chunk:
                self._udp_send_to_session(session, self.build_notification_packet(1, "Cargo state failed: chunk not loaded")); return
            vessel = chunk.get_object_by_id(vessel_id)
            if not chunk:
                self._udp_send_to_session(session, self.build_notification_packet(1, "Could not find that vessel cargo state on the server end")); return
            
            self._udp_send_to_session(session, self.build_cargo_state_packet(vessel, 0))         


    # ---------- Cargo helpers ----------
    def _ensure_vessel_cargo(self, vessel):
        """Guarantee vessel.cargo exists as a dict[int,int]."""
        try:
            if not hasattr(vessel, "cargo") or vessel.cargo is None:
                vessel.cargo = {}
        except Exception:
            # Fallback if the class is restrictive (unlikely, but safe):
            try:
                object.__setattr__(vessel, "cargo", {})
            except Exception:
                return None
        return vessel.cargo

    def _guess_landed_on_planet(self, vessel, planet_id: int, chunk) -> bool:
        """
        Keep the same 'no assumptions' approach:
        prefer (landed + home_planet.object_id) if present; else just
        ensure the planet object exists in the same chunk.
        """
        try:
            if hasattr(vessel, "landed") and hasattr(vessel, "home_planet"):
                hp = getattr(vessel, "home_planet")
                hp_id = int(getattr(hp, "object_id", 0)) if hp else 0
                return bool(getattr(vessel, "landed")) and hp_id == int(planet_id)
        except Exception:
            pass
        try:
            return chunk.get_object_by_id(int(planet_id)) is not None
        except Exception:
            return False

    def build_cargo_state_packet(self, vessel, planet_id: int) -> bytes:
        """
        Snapshot using vessel.cargo (kept on the Vessel).
        Layout:
          u8  opcode = CARGO_STATE
          u64 vessel_id
          u64 planet_id
          u16 cap    (0 = unspecified)
          u16 used   (sum of amounts, u16 clamp)
          u16 n_items
          [n_items x (u32 rid, u32 amt)]
        """
        vc = getattr(vessel, "cargo", {}) or {}
        items = [(int(r), int(a)) for r, a in vc.items() if int(a) > 0]
        used = sum(a for _, a in items)

        pkt = bytearray()
        pkt.append(DataGramPacketType.CARGO_STATE)
        pkt += struct.pack("<QQ", int(getattr(vessel, "object_id", 0)), int(planet_id))
        pkt += struct.pack("<HHH", 0, min(int(used), 0xFFFF), min(len(items), 0xFFFF))
        for rid, amt in items:
            pkt += struct.pack("<II", rid & 0xFFFFFFFF, amt & 0xFFFFFFFF)
        return pkt


    def _find_vessel_for_session(self, session, vessel_id: int):
        """Find a vessel by id in the player's current chunk."""
        player = getattr(session, "player", None)
        if not player:
            return None
        chunk_key = (player.galaxy, player.system)
        chunk = self.shared.chunk_manager.loaded_chunks.get(chunk_key)
        if not chunk:
            return None
        return chunk.get_object_by_id(int(vessel_id))

    def _get_agency_base_inventory(self, player, planet_id: int):
        """Return (agency, base_inventory_dict) or (None, None). Coerces keys to int."""
        agency = self.shared.agencies.get(getattr(player, "agency_id", 0))
        if not agency:
            return None, None
        inv_all = getattr(agency, "base_inventories", None)
        if not isinstance(inv_all, dict):
            return agency, None
        from utils import _coerce_int_keys
        inv = inv_all.get(int(planet_id))
        if inv is None:
            return agency, None
        inv = _coerce_int_keys(inv)
        inv_all[int(planet_id)] = inv  # keep normalized
        return agency, inv

    def _extract_resource_pairs(self, data: bytes, offset: int, count: int):
        """Parse [count x (u32 id, u32 amt)] from data[offset:]. Returns (list[(id, amt)], new_offset) or (None, offset) on error."""
        need = count * (4 + 4)
        if len(data) < offset + need:
            return None, offset
        out = []
        for _ in range(count):
            rid = int.from_bytes(data[offset:offset+4], "little", signed=False); offset += 4
            amt = int.from_bytes(data[offset:offset+4], "little", signed=False); offset += 4
            if amt > 0:
                out.append((rid, amt))
        return out, offset

    def build_cargo_state_packet(self, vessel, planet_id: int) -> bytes:
        """
        Snapshot of vessel cargo after a transfer.
        Layout:
          u8  opcode = CARGO_STATE
          u64 vessel_id
          u64 planet_id
          u16 cap
          u16 used
          u16 n_items
          [n_items x (u32 rid, u32 amt)]
        """
        try:
            cap = int(max(0, getattr(vessel, "cargo_capacity", 0)))
            cargo = getattr(vessel, "cargo", {}) or {}
            used = sum(int(max(0, v)) for v in cargo.values())
            items = [(int(k), int(v)) for k, v in cargo.items() if int(v) > 0]
        except Exception:
            cap, used, items = 0, 0, []

        pkt = bytearray()
        pkt.append(DataGramPacketType.CARGO_STATE)
        pkt += struct.pack("<QQ", int(getattr(vessel, "object_id", 0)), int(planet_id))
        pkt += struct.pack("<HHH", cap & 0xFFFF, used & 0xFFFF, len(items) & 0xFFFF)
        for rid, amt in items:
            pkt += struct.pack("<II", rid & 0xFFFFFFFF, amt & 0xFFFFFFFF)
        return pkt

    def error_received(self, exc):
        print(f"⚠️ UDP error received: {exc}")

    def connection_lost(self, exc):
        print("🔌 UDP server closed.")

    async def _broadcast_loop(self):
        while True:
            self.send_player_details()
            await asyncio.sleep(1 / 60)  

    def build_resolve_vessel_packet(self, vessel):
        if vessel is None:
            print("⚠️ build_resolve_vessel_packet called with None vessel")
            return b""
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
        packet += struct.pack('<H',  vessel.seats_capacity)
        packet += struct.pack('<H', len(components))

        for comp in components:
            packet += struct.pack('<HhhHHH', comp.id, comp.x, comp.y, comp.stage, comp.paint1, comp.paint2)
        return packet   

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

    def build_xp_orb_packet(
        self,
        point_type: int,
        source_kind: int,
        vessel_id: int = 0,
        planet_id: int = 0,
        building_type: int = 0,
    ) -> bytes:
        """
        Layout:
        u8  opcode = XP_ORB
        u8  point_type (0=rp, 1=ep, 2=pp, 3=xp)
        u8  source_kind (0=vessel, 1=building)
        if vessel:
          u64 vessel_id
        else:
          u64 planet_id
          u16 building_type
        """
        pkt = bytearray()
        pkt.append(DataGramPacketType.XP_ORB)
        pkt.append(int(point_type) & 0xFF)
        pkt.append(int(source_kind) & 0xFF)
        if int(source_kind) == 0:
            pkt += struct.pack("<Q", int(vessel_id))
        else:
            pkt += struct.pack("<QH", int(planet_id), int(building_type))
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

    def send_xp_orb_to_agency(
        self,
        agency_id: int,
        point_type: int,
        source_kind: int,
        vessel_id: int = 0,
        planet_id: int = 0,
        building_type: int = 0,
    ) -> int:
        """
        One-shot XP orb event to all online players in an agency.
        """
        # Apply the points to the agency immediately
        agency = self.shared.agencies.get(agency_id) if hasattr(self.shared, "agencies") else None
        if agency:
            try:
                if point_type == 0:
                    agency.research_points = int(getattr(agency, "research_points", 0)) + 1
                elif point_type == 1:
                    agency.exploration_points = int(getattr(agency, "exploration_points", 0)) + 1
                elif point_type == 2:
                    agency.publicity_points = int(getattr(agency, "publicity_points", 0)) + 1
                elif point_type == 3:
                    agency.experience_points = int(getattr(agency, "experience_points", 0)) + 1
            except Exception as e:
                print(f"⚠️ Failed to apply orb to agency {agency_id}: {e}")

        pkt = self.build_xp_orb_packet(
            point_type=point_type,
            source_kind=source_kind,
            vessel_id=vessel_id,
            planet_id=planet_id,
            building_type=building_type,
        )
        sent = 0
        for s in self.control.sessions:
            if not s.alive:
                continue
            p = getattr(s, "player", None)
            if not p or getattr(p, "agency_id", None) != agency_id:
                continue
            sent += 1 if self._udp_send_to_session(s, pkt) else 0
        return sent

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
