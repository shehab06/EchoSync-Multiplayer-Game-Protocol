"""
Game grid logic
"""
import pygame
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import *
from ui.colors import Colors

class GameGrid:
    def __init__(self, grid_size=GRID_SIZE):
        self.grid_size = grid_size
        self.cell_size = CELL_SIZE
        self.margin = MARGIN
        self.grid = [0] * (grid_size * grid_size)  # 0 = empty, >0 = player_id
        self.scores = {}  # player_id -> score
        
    def reset(self):
        self.grid = [0] * (self.grid_size * self.grid_size)
        self.scores.clear()

    def claim_cell(self, x, y, player_id):
        """Claim a cell for a player"""
        if not (0 <= x < self.grid_size and 0 <= y < self.grid_size):
            return False
        
        index = y * self.grid_size + x
        if self.grid[index] == 0:  # Cell is empty
            self.grid[index] = player_id
            # Update scores
            if player_id not in self.scores:
                self.scores[player_id] = 0
            self.scores[player_id] += 1
            return True
        return False

    def get_winner(self):
        """Get the player with the highest score"""
        if not self.scores:
            return None
        return max(self.scores.items(), key=lambda x: x[1])

    def is_full(self):
        return all(cell != 0 for cell in self.grid)

    def draw(self, surface, offset_x, offset_y, player_colors):
        """Draw the grid on the surface"""
        for y in range(self.grid_size):
            for x in range(self.grid_size):
                index = y * self.grid_size + x
                cell_rect = pygame.Rect(
                    offset_x + x * (self.cell_size + self.margin),
                    offset_y + y * (self.cell_size + self.margin),
                    self.cell_size, self.cell_size
                )
                
                # Cell color based on owner
                owner_id = self.grid[index]
                if owner_id == 0:
                    color = Colors.GRID_EMPTY
                else:
                    color = player_colors.get(owner_id, Colors.GRID_EMPTY)
                
                pygame.draw.rect(surface, color, cell_rect, border_radius=4)
                pygame.draw.rect(surface, Colors.GRID_BORDER, cell_rect, 1, border_radius=4)