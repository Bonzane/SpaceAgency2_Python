
#This is a server-side player. A session tracks this game object to their
#networking info. 

class Player:
    def __init__(self, session, steamID):
        print("👤 A NEW PLAYER has joined your game!")
        self.tracked_object = None
        self.x = 0
        self.y = 0
        self.money = 0
        self.steamID = steamID
        self.session = session
        self.player = None
        self.galaxy = 1
        self.system = 1
        self.agency_id = 0


    def update_location(self):
       if(tracked_object != none): 
            self.x = tracked_object.x
            self.y = tracked_object.y
