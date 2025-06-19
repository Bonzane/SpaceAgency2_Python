import requests 
import socket
import asyncio

class HttpClient:
    def listing_healthcheck(self, url):
        print(f"Performing health check on listing server: {url}")
        try:
            response = requests.get(url)
            return response.status_code
        except requests.RequestException as e:
            print(f"Request failed: {e}")
            return 0


#TCP Server
# This server handles TCP packets. That means it handles 
# all the data that must be received and must be received in order. 
# Think of things like players joining, chat messages, players leaving, activating thruters, etc. 
# It does NOT handle streaming the positions of objects. Instead, that is the Streaming Server, which uses UDP.
class ControlServer: 
    def __init__(self, listens_on_port):
        self.port = listens_on_port
        self.active = False #The server must be "activated"

    def activate(self):
        print(f"🟢 Control Server Activated on port {self.port}")
        self.active = True




        