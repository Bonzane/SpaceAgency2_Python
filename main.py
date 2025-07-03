# Running this file with Python should set up your server :) 
# Run it with ./run.sh. to create the virtual environment and run the server.

#   You're looking at the code behind Space Agency 2!
#   If you're just trying to change game settings, it's easier to 
#   just edit config.txt. Editing the code here is great for 
#   creating mods and community contributions, but keep in mind
#   that this will affect all of your games, and harmful edits
#   may result in being rejected for or losing your Hyder trust key.

import asyncio
import server
from textual.app import App, ComposeResult
from textual.widgets import Header, Footer, Static
from textual.containers import Horizontal
import os
from game import Game

print("Server Starting")

# Parse the config.txt file
server_settings = {}
game_defaults = {}
game_data = {}
with open("config.txt", "r") as config_file:
    for line in config_file:
        if line.strip() and not line.startswith("🚀"):
            key, value = line.split(" ", 1)
            if key.startswith("server_settings."):
                server_settings[key[len("server_settings."):]] = value.strip()
            elif key.startswith("game_defaults."):
                game_defaults[key[len("game_defaults."):]] = value.strip()
            elif key.startswith("game."):
                game_data[key[len("game."):]] = value.strip()
            else:
                print(f"Unknown config key: {key}")

# Construct the UI
class ServerApp(App):
    BINDINGS = [
        ("ctrl+c", "quit", "Quit")
    ]

    def compose(self) -> ComposeResult:
        yield Header()
        yield Footer()
        yield Horizontal(
            Static("Admin Tools", id="admin-tools", classes="column"),
            Static("Event Log", id="event-log", classes="column"),
        )

    CSS = """
    Horizontal {
        height: 1fr;
    }
    .column {
        width: 1fr;
        border: solid gray;
        padding: 1 2;
        margin: 1;
        height: 100%;
    }
    #admin-tools {
        background: #222244;
        color: white;
    }
    #event-log {
        background: #222222;
        color: #00ff00;
    }
    """

# Uncomment this to enable the user friendly GUI
# app = ServerApp()
# app.run()

async def main():
    # Attempt to contact the listing server
    http_client = server.HttpClient()
    listing_domain = server_settings.get("listing_domain", "https://list.commsat.org")
    print("This is the listing domain: " + listing_domain)
    listing_server_status = http_client.listing_healthcheck(listing_domain)

    # Create the mission control
    admins = server_settings.get("administrators", [])
    missioncontrol = server.ServerMissionControl(admins)
    missioncontrol.set_public_name(server_settings.get("server_name", "Commsat"))

    # Start the posting loop - This task sends the listing server information about your server,
    # So that everyone can find your game! 
    status_update_task = asyncio.create_task(server.update_listing_server(missioncontrol, http_client, listing_domain))


    # Initialize TCP server
    control_port = int(server_settings.get("control_port", 9001))
    missioncontrol.set_control_port(control_port)
    missioncontrol.set_control_port_extern(int(server_settings.get("tcp_control_port_external", 9001)))
    missioncontrol.set_streaming_port_extern(int(server_settings.get("udp_streaming_port_external", 9001)))
    control_server = server.ControlServer(missioncontrol, control_port)
    control_server.activate()

    # Start TCP server task
    tcp_task = asyncio.create_task(control_server.start())

    #Initialize UDP server
    streaming_port = int(server_settings.get("streaming_port", 9002))
    udp_server = server.StreamingServer(missioncontrol, streaming_port, control_server)
    udp_task = asyncio.create_task(udp_server.start())
    udp_server.activate()
    missioncontrol.udp_server = udp_server
    # game_loop_task = asyncio.create_task(game_loop(missioncontrol))

    # Load the Game
    game_files = game_data.get("path", "gameData/default")
    #Create the game, it will automatically big bang if necessary
    tickrate = int(server_settings.get("tickrate", 60))
    gamespeed = float(game_defaults.get("gamespeed_multiplier", 1.0) * 2920)
    missioncontrol.gamespeed = gamespeed
    missioncontrol.player_starting_cash = int(game_defaults.get("playerstartcash", 200000))
    missioncontrol.base_cash_per_second = int(game_defaults.get("basecashpersecond", 200))
    game = Game(game_files, tickrate, gamespeed, missioncontrol)




    # Wait on all tasks
    try: 
        await asyncio.gather(tcp_task, status_update_task, udp_task)
    finally:
        await http_client.close()

if __name__ == "__main__":
    asyncio.run(main())



