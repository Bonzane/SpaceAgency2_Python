

class Agency:
    def __init__(self, name, initial_members=None):
        self.members = list(initial_members) if initial_members else []
        self.name = name
        self.id64 = 0
        self.is_public = True
        self.total_money = 0

    def add_player(self, steam_id):
        self.members.append(steam_id)

    def remove_player(self, steam_id):
        self.members = [id64 for id64 in self.members if id64 != steam_id]

    def get_money(self, players_by_id):
        self.total_money = sum(players_by_id[id64].money for id64 in self.members if id64 in players_by_id)
        return self.total_money

    def set_name(self, name):
        self.agencyname = name

    def manually_set_id(self, new_id):
        self.id64 = new_id

    def set_public(self, is_public):
        self.is_public = is_public

    def get_public(self):
        return self.is_public

    def get_member_count(self):
        return len(self.members)

    def get_id64(self):
        return self.id64

    def get_name(self):
        return self.agencyname

    def list_players(self):
        for id64 in self.members:
            print(f"Player: {id64}")

    def set_id(newid):
        self.id64 = newid