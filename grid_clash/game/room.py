"""
Room management
"""
from game.player import Player

class Room:
    def __init__(self, name, room_id, host_id):
        self.id = room_id
        self.name = name
        self.host_id = host_id
        self.players = {}  # player_id -> Player
        self.max_players = 16
        self.is_game_started = False

    def add_player(self, player_id, player_name, color_index=None):  # ADD color_index parameter
        if len(self.players) >= self.max_players:
            return False
        
        if color_index is None:
            # Assign next available color (original behavior)
            used_colors = [p.color_index for p in self.players.values()]
            color_index = 0
            while color_index in used_colors and color_index < len(used_colors):
                color_index += 1
        
        self.players[player_id] = Player(player_id, player_name, color_index)
        return True

    def remove_player(self, player_id):
        if player_id in self.players:
            del self.players[player_id]
            # If host leaves, assign new host
            if player_id == self.host_id and self.players:
                self.host_id = next(iter(self.players.keys()))

    def can_start_game(self):
        return len(self.players) >= 3 and not self.is_game_started

    def start_game(self):
        if self.can_start_game():
            self.is_game_started = True
            return True
        return False

    def get_player_count(self):
        return len(self.players)

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'host_id': self.host_id,
            'players': {pid: player.to_dict() for pid, player in self.players.items()},
            'player_count': self.get_player_count(),
            'is_game_started': self.is_game_started
        }