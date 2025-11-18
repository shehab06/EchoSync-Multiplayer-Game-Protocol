"""
UI Screens for different game states
"""
import pygame
import os
import sys

# Add the parent directory to the path so we can import from config
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import *
from ui.widgets import Button, TextInput, draw_text, centered_rect
from game.grid import GameGrid
from game.room import Room
from ui.colors import Colors
from game.player import Player

class Screen:
    def __init__(self, game):
        self.game = game
        
    def handle_event(self, event):
        pass
        
    def update(self):
        pass
        
    def draw(self, surface):
        pass

class MainMenuScreen(Screen):
    def __init__(self, game):
        super().__init__(game)
        self.buttons = []
        
    def update(self):
        cx, cy = WINDOW_WIDTH // 2, WINDOW_HEIGHT // 2
        self.buttons = [
            Button(centered_rect(cx, cy - 20, 320, 60), "Create Room", self.create_room),
            Button(centered_rect(cx, cy + 100, 320, 60), "Join Room", self.join_room)
        ]
        
    def draw(self, surface):
        surface.fill(Colors.BACKGROUND)
        cx, cy = WINDOW_WIDTH // 2, WINDOW_HEIGHT // 2
        
        # Title
        draw_text(surface, "Grid Clash", 72, (cx, cy - 180), Colors.TEXT_ACCENT, center=True)
        
        # Buttons
        for button in self.buttons:
            button.update(pygame.mouse.get_pos(), pygame.mouse.get_pressed()[0])
            button.draw(surface)
            
    def create_room(self):
        self.game.set_screen("create_room")
        
    def join_room(self):
        self.game.set_screen("room_list")

class CreateRoomScreen(Screen):
    def __init__(self, game):
        super().__init__(game)
        self.text_input = TextInput(centered_rect(WINDOW_WIDTH//2, WINDOW_HEIGHT//2 - 50, 480, 48), "Enter room name")
        self.buttons = []
        # REMOVE creation_sent flag - we don't need it
        
    def handle_event(self, event):
        self.text_input.handle_event(event)
        
    def update(self):
        cx, cy = WINDOW_WIDTH // 2, WINDOW_HEIGHT // 2
        self.buttons = [
            Button(centered_rect(cx - 100, cy + 40, 180, 48), "Create", self.create_room),
            Button(centered_rect(cx + 100, cy + 40, 180, 48), "Back", self.back)
        ]
        
    def draw(self, surface):
        surface.fill(Colors.BACKGROUND)
        cx, cy = WINDOW_WIDTH // 2, WINDOW_HEIGHT // 2
        
        draw_text(surface, "Create Room", 48, (cx, cy - 150), Colors.TEXT_ACCENT, center=True)
        
        self.text_input.draw(surface)
        
        for button in self.buttons:
            button.update(pygame.mouse.get_pos(), pygame.mouse.get_pressed()[0])
            button.draw(surface)
            
    def create_room(self):
        room_name = self.text_input.text.strip()
        if room_name:
            # Use network client to create room
            if self.game.network_client.create_room(room_name):
                # Room creation initiated - wait for JOIN_ACK callback
                # The callback in main.py will handle the screen transition
                pass
            else:
                print("Failed to create room")
                
    def back(self):
        self.game.set_screen("main_menu")

class RoomListScreen(Screen):
    def __init__(self, game):
        super().__init__(game)
        self.rooms = []
        self.buttons = []
        
    def update(self):
        # Rooms are populated via network callbacks from main.py
        cx, cy = WINDOW_WIDTH // 2, WINDOW_HEIGHT // 2
        self.buttons = []
        
        # Room buttons using network data - FIXED LAMBDA
        for i, room_data in enumerate(self.rooms[:MAX_ROOMS]):
            room_rect = pygame.Rect(cx - 240, cy - 150 + i * 68, 400, 56)
            join_btn_rect = pygame.Rect(room_rect.right + 10, room_rect.y, 80, 56)
            
            # FIX: Create join handler with specific room_id - SIMPLER APPROACH
            room_id = room_data['id']
            self.buttons.append(Button(join_btn_rect, "Join", 
                                     lambda r=room_id: self.join_room(r)))
        
        # Back button
        self.buttons.append(Button(centered_rect(cx, cy + 300, 180, 48), "Back", self.back))
        
    def draw(self, surface):
        surface.fill(Colors.BACKGROUND)
        cx, cy = WINDOW_WIDTH // 2, WINDOW_HEIGHT // 2
        
        draw_text(surface, "Available Rooms", 48, (cx, cy - 250), Colors.TEXT_ACCENT, center=True)
        
        # Draw room list from network data
        for i, room_data in enumerate(self.rooms[:MAX_ROOMS]):
            room_rect = pygame.Rect(cx - 240, cy - 150 + i * 68, 400, 56)
            pygame.draw.rect(surface, Colors.PANEL, room_rect, border_radius=8)
            pygame.draw.rect(surface, Colors.GRID_BORDER, room_rect, 1, border_radius=8)
            
            room_text = f"{room_data['name']} ({room_data['player_count']} players)"
            draw_text(surface, room_text, 22, (room_rect.x + 16, room_rect.centery - 12))
        
        # Draw buttons
        for button in self.buttons:
            button.update(pygame.mouse.get_pos(), pygame.mouse.get_pressed()[0])
            button.draw(surface)
            
    def join_room(self, room_id):
        print(f"Joining room ID: {room_id}")  # DEBUG
        # Use network client to join room with correct room_id
        if self.game.network_client.join_room(room_id):
            # Room join initiated - wait for JOIN_ACK callback
            pass
        else:
            print("Failed to send join room request")
        
    def back(self):
        self.game.set_screen("main_menu")

class LobbyScreen(Screen):
    def __init__(self, game):
        super().__init__(game)
        self.buttons = []
        self.players_copy = {}  # Simple thread-safe copy
        
    def update(self):
        # Create thread-safe copy of players for drawing
        if self.game.current_room and hasattr(self.game.current_room, 'players'):
            self.players_copy = self.game.current_room.players.copy()
            
        cx, cy = WINDOW_WIDTH // 2, WINDOW_HEIGHT // 2
        
        # Check if we have enough players from cached data
        can_start = len(self.players_copy) >= MIN_PLAYERS
            
        self.buttons = [
            Button(centered_rect(cx, WINDOW_HEIGHT - 120, 200, 52), "Start Game", 
                  self.start_game, can_start),
            Button(centered_rect(cx, WINDOW_HEIGHT - 55, 200, 44), "Leave Lobby", self.leave_lobby)
        ]
        
    def draw(self, surface):
        surface.fill(Colors.BACKGROUND)
        
        room = self.game.current_room
        if not room:
            return
            
        cx, cy = WINDOW_WIDTH // 2, WINDOW_HEIGHT // 2
        
        # Get player info from network
        player_info = self.game.network_client.get_player_info()
        local_id = player_info['local_id'] if player_info else None
        
        # Room info
        draw_text(surface, f"Lobby: {room.name}", 36, (cx, 80), Colors.TEXT_ACCENT, center=True)
        draw_text(surface, f"Players: {len(self.players_copy)}/16", 24, (cx, 120), Colors.TEXT, center=True)
        
        # Player list from THREAD-SAFE COPY
        start_y = 180
        for i, (player_local_id, player) in enumerate(self.players_copy.items()):
            player_rect = pygame.Rect(cx - 200, start_y + i * 70, 400, 60)
            pygame.draw.rect(surface, Colors.PANEL, player_rect, border_radius=8)
            pygame.draw.rect(surface, Colors.GRID_BORDER, player_rect, 1, border_radius=8)
            
            # Player color indicator
            color_circle = pygame.Rect(player_rect.x + 15, player_rect.centery - 15, 30, 30)
            pygame.draw.circle(surface, player.color, color_circle.center, 15)
            
            # Player name
            is_you = (local_id is not None and player_local_id == local_id)
            name_text = f"{player.name} {'(You)' if is_you else ''}"
            draw_text(surface, name_text, 22, (player_rect.x + 60, player_rect.centery - 8))
            
            # Host indicator (simplified - first player is host)
            if i == 0:
                draw_text(surface, "Host", 18, (player_rect.right - 50, player_rect.centery - 8), Colors.TEXT_ACCENT)
        
        # Buttons
        for button in self.buttons:
            button.update(pygame.mouse.get_pos(), pygame.mouse.get_pressed()[0])
            button.draw(surface)
            
        # Start game hint
        if len(self.players_copy) < MIN_PLAYERS:
            draw_text(surface, f"Need at least {MIN_PLAYERS} players to start", 18, 
                     (cx, WINDOW_HEIGHT - 25), (150, 150, 150), center=True)
            
    def start_game(self):
        if len(self.players_copy) >= MIN_PLAYERS:
            self.game.set_screen("game")
            
    def leave_lobby(self):
        self.game.network_client.leave_room()
        self.game.current_room = None
        self.players_copy = {}  # Clear copy
        self.game.set_screen("main_menu")

class GameScreen(Screen):
    def __init__(self, game):
        super().__init__(game)
        self.grid = GameGrid()
        self.buttons = []
        self.players_copy = {}  # Simple thread-safe copy
        
    def handle_event(self, event):
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            self.handle_click(*event.pos)
            
    def update(self):
        # Update grid from network data
        network_grid = self.game.network_client.get_grid_state()
        if network_grid:
            self.grid.grid = network_grid
            
        # Create thread-safe copy of players for drawing
        if self.game.current_room and hasattr(self.game.current_room, 'players'):
            self.players_copy = self.game.current_room.players.copy()
            
        self.buttons = [
            Button(pygame.Rect(WINDOW_WIDTH - 120, 20, 100, 40), "Menu", self.show_menu)
        ]
        
    def draw(self, surface):
        surface.fill(Colors.BACKGROUND)
        
        room = self.game.current_room
        if not room:
            return
            
        # Draw grid
        grid_size_px = GRID_SIZE * (CELL_SIZE + MARGIN)
        grid_offset_x = 50
        grid_offset_y = 50
        
        # Get player colors from THREAD-SAFE COPY
        player_colors = {}
        for player_local_id, player in self.players_copy.items():
            player_colors[player_local_id] = player.color
            
        self.grid.draw(surface, grid_offset_x, grid_offset_y, player_colors)
        
        # Draw leaderboard panel
        panel_rect = pygame.Rect(WINDOW_WIDTH - 300, 0, 300, WINDOW_HEIGHT)
        pygame.draw.rect(surface, Colors.PANEL, panel_rect)
        pygame.draw.line(surface, Colors.GRID_BORDER, (WINDOW_WIDTH - 300, 0), (WINDOW_WIDTH - 300, WINDOW_HEIGHT), 2)
        
        # Leaderboard content
        draw_text(surface, room.name, 24, (WINDOW_WIDTH - 150, 30), Colors.TEXT_ACCENT, center=True)
        draw_text(surface, f"Players: {len(self.players_copy)}", 18, (WINDOW_WIDTH - 150, 60), Colors.TEXT, center=True)
        
        # Player scores (calculate from grid)
        start_y = 100
        scores = {}
        for cell_owner in self.grid.grid:
            if cell_owner > 0:
                scores[cell_owner] = scores.get(cell_owner, 0) + 1
        
        # Get player info from network
        player_info = self.game.network_client.get_player_info()
        local_id = player_info['local_id'] if player_info else None
        
        # Use THREAD-SAFE COPY for display
        for i, (player_local_id, player) in enumerate(self.players_copy.items()):
            player_y = start_y + i * 50
            
            # Player color
            pygame.draw.circle(surface, player.color, (WINDOW_WIDTH - 270, player_y), 10)
            
            # Player name
            is_you = (local_id is not None and player_local_id == local_id)
            name_text = f"{player.name} {'(You)' if is_you else ''}"
            draw_text(surface, name_text, 18, (WINDOW_WIDTH - 250, player_y - 8))
            
            # Player score
            score = scores.get(player_local_id, 0)
            draw_text(surface, f"{score} cells", 16, (WINDOW_WIDTH - 250, player_y + 12), (100, 100, 100))
        
        # WARNING: Show red text if not enough players
        if len(self.players_copy) < MIN_PLAYERS:
            needed = MIN_PLAYERS - len(self.players_copy)
            warning_text = f"Need {needed} more player{'s' if needed > 1 else ''} to start"
            draw_text(surface, warning_text, 20, (WINDOW_WIDTH // 2, WINDOW_HEIGHT - 30), 
                     (255, 50, 50), center=True)  # Red color
            
        # Check for game end (grid full)
        if self.grid.is_full():
            winner_id, winner_score = self.grid.get_winner()
            if winner_id and winner_id in self.players_copy:
                self.game.winner = self.players_copy[winner_id]
                self.game.set_screen("game_over")
        
        # Draw buttons
        for button in self.buttons:
            button.update(pygame.mouse.get_pos(), pygame.mouse.get_pressed()[0])
            button.draw(surface)
            
    def handle_click(self, x, y):
        # Convert screen coordinates to grid coordinates
        grid_offset_x, grid_offset_y = 50, 50
        grid_x = (x - grid_offset_x) // (CELL_SIZE + MARGIN)
        grid_y = (y - grid_offset_y) // (CELL_SIZE + MARGIN)
        
        if 0 <= grid_x < GRID_SIZE and 0 <= grid_y < GRID_SIZE:
            # Send cell claim via network
            self.game.network_client.claim_cell(grid_x, grid_y)
                
    def show_menu(self):
        # Leave the room before going to main menu
        self.game.network_client.leave_room()
        self.game.current_room = None
        self.game.set_screen("main_menu")

class GameOverScreen(Screen):
    def __init__(self, game):
        super().__init__(game)
        self.buttons = []
        
    def update(self):
        self.buttons = [
            Button(centered_rect(WINDOW_WIDTH//2, WINDOW_HEIGHT//2 + 100, 200, 52), "Main Menu", self.main_menu)
        ]
        
    def draw(self, surface):
        surface.fill(Colors.BACKGROUND)
        cx, cy = WINDOW_WIDTH // 2, WINDOW_HEIGHT // 2
        
        if self.game.winner:
            draw_text(surface, "Game Over!", 48, (cx, cy - 80), Colors.TEXT_ACCENT, center=True)
            draw_text(surface, f"Winner:", 36, (cx, cy - 20), Colors.TEXT, center=True)
            
            # Winner color and name
            pygame.draw.circle(surface, self.game.winner.color, (cx, cy + 30), 20)
            draw_text(surface, self.game.winner.name, 32, (cx, cy + 80), Colors.TEXT_ACCENT, center=True)
        else:
            draw_text(surface, "Game Over!", 48, (cx, cy), Colors.TEXT_ACCENT, center=True)
        
        for button in self.buttons:
            button.update(pygame.mouse.get_pos(), pygame.mouse.get_pressed()[0])
            button.draw(surface)
            
    def main_menu(self):
        # Leave room when going to main menu
        self.game.network_client.leave_room()
        self.game.current_room = None
        self.game.winner = None
        self.game.set_screen("main_menu")