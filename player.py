
#This is a server-side player. A session tracks this game object to their
#networking info. 

class Player:
    def __init__(self, session):
        self.tracked_object = None
        self.x = 0
        self.y = 0

    def update_location(self):
       if(tracked_object != none): 
            self.x = tracked_object.x
            self.y = tracked_object.y
