# Running this file with Python should set up your server :)

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
class ServerApp(App):
    def compose(self):
        yield Header()
        yield Static("Welcome to Textual!", id="welcome-text")
        yield Footer()

app = ServerApp()
app.run()

# Attempt to contact the listing server


# Boot up the live server



