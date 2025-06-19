# Running this file with Python should set up your server :) 
# Run it with ./run.sh. to create the virtual environment and run the server.

#   You're talking a look at the code behind space agency 2!
#   If you're just trying to change game settings, it's easier to 
#   Just edit config.txt. Editing the code here is great for 
#   creating mods and community contributions, but keep in mind
#   that this will affect all of your games, and harmful edits
#   may result in being rejected for or losing your Hyder trust key.

import socket
import threading
import server
from textual.app import App, ComposeResult
from textual.widgets import Header, Footer, Static
from textual.widget import Widget

print("Server Starting")

# Parse the config.txt file
server_settings = {}
game_defaults = {}
with open("config.txt", "r") as config_file:
    for line in config_file:
        if line.strip() and not line.startswith("🚀"):
            key, value = line.split(" ", 1)
            if key.startswith("server_settings"):
                server_settings[key] = value.strip()
            elif key.startswith("game_defaults"):
                game_defaults[key] = value.strip()
            else:
                print(f"Unknown config key: {key}")


# Construct the UI
from textual.app import App, ComposeResult
from textual.widgets import Header, Footer, Static
from textual.containers import Container, Horizontal

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

# Uncomment this to enable the user friendly gui
# app = ServerApp()
# app.run()

# Attempt to contact the listing server
http_client = server.HttpClient()
listing_domain = server_settings.get("listing_domain", "https://commsat.org/api/healthcheck")
print("This is the listing domain: " + listing_domain)
listing_server_status = http_client.listing_healthcheck(listing_domain)


# Initialize TCP and UDP servers
control_port = int(server_settings.get("control_port", 9001))
control_server = server.ControlServer(control_port)


#activate servers
control_server.activate()



