"""
Player management
"""
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import *

class Player:
    def __init__(self, player_id, name, color_index=0):
        self.id = player_id
        self.name = name
        self.color_index = color_index
        self.score = 0
        self.is_ready = False

    @property
    def color(self):
        return PLAYER_COLORS[self.color_index % len(PLAYER_COLORS)]

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'color_index': self.color_index,
            'score': self.score,
            'is_ready': self.is_ready
        }

    @classmethod
    def from_dict(cls, data):
        player = cls(data['id'], data['name'], data['color_index'])
        player.score = data['score']
        player.is_ready = data['is_ready']
        return player