
#This is a server-side player. A session tracks this game object to their
#networking info. 

class Player:
    def __init__(self, session, steamID, shared):
        print("👤 A NEW PLAYER has joined your game!")
        self.tracked_object = None
        self.x = 0
        self.y = 0
        self.money = int(shared.player_starting_cash)
        self.steamID = steamID
        self.session = session
        self.player = None
        self.galaxy = 1
        self.system = 1
        self.agency_id = 0
        self.shared = shared
        self.controlled_vessel_id = -1


    def update_location(self):
       if(self.tracked_object != 0): 
            self.x = self.tracked_object.x
            self.y = self.tracked_object.y

    def gain_money(self):
        gain = self.shared.base_cash_per_second

        self.money = self.money + gain

