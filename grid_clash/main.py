"""
Grid Clash - Main Game
"""
import pygame
import sys
from config import *
from network.client import GridClashNetworkClient
from ui.screens import *

class GridClashGame:
    def __init__(self):
        pygame.init()
        self.screen = pygame.display.set_mode((WINDOW_WIDTH, WINDOW_HEIGHT))
        pygame.display.set_caption("Grid Clash")
        self.clock = pygame.time.Clock()
        
        # Network client with ESP protocol integration
        self.network_client = GridClashNetworkClient()
        self._setup_network_callbacks()
        
        # Game state
        self.current_screen = None
        self.current_room = None
        self.winner = None
        
        # Initialize screens
        self.screens = {
            "main_menu": MainMenuScreen(self),
            "create_room": CreateRoomScreen(self),
            "room_list": RoomListScreen(self),
            "lobby": LobbyScreen(self),
            "game": GameScreen(self),
            "game_over": GameOverScreen(self)
        }
        
        self.set_screen("main_menu")
        
    def _setup_network_callbacks(self):
        """Setup callbacks for network events"""
        self.network_client.on_room_list_update = self._on_room_list_update
        self.network_client.on_player_list_update = self._on_player_list_update
        self.network_client.on_grid_update = self._on_grid_update
        self.network_client.on_game_start = self._on_game_start
        
    def _on_room_list_update(self, rooms):
        """Called when room list is updated from server"""
        # Update room list screen if it's active
        if isinstance(self.current_screen, RoomListScreen):
            self.current_screen.rooms = rooms
            
    def _on_player_list_update(self, players):
        """Called when player list in current room changes (after JOIN_ACK)"""
        if players:
            player_info = self.network_client.get_player_info()
            room_id = player_info['room_id'] if player_info else None
            
            # If we don't have a current room OR we're joining a different room
            if not self.current_room or (self.current_room and self.current_room.id != room_id):
                # Create room with name from network (or placeholder)
                room_name = f"Room {room_id}" if room_id else "New Room"
                self.current_room = Room(room_name, room_id, player_info.get('local_id', 1))
                
                # Clear and recreate players with FIXED COLORS
                self.current_room.players.clear()
                for local_id, (global_id, color) in players.items():
                    player_name = "You" if local_id == player_info.get('local_id') else f"Player {local_id}"
                    
                    # Use FIXED colors based on local_id
                    color_index = local_id % len(PLAYER_COLORS)
                    self.current_room.players[local_id] = Player(local_id, player_name, color_index=color_index)
                
                # Transition to lobby if we're not already there
                if not isinstance(self.current_screen, LobbyScreen):
                    self.set_screen("lobby")
            else:
                # Update existing room players with FIXED COLORS
                player_info = self.network_client.get_player_info()
                self.current_room.players.clear()
                for local_id, (global_id, color) in players.items():
                    player_name = "You" if local_id == player_info.get('local_id') else f"Player {local_id}"
                    
                    # Use FIXED colors based on local_id
                    color_index = local_id % len(PLAYER_COLORS)
                    self.current_room.players[local_id] = Player(local_id, player_name, color_index=color_index)

    def _find_color_index(self, target_color):
        """Find the color index for a given RGB color"""
        from config import PLAYER_COLORS
        # Look for exact match first
        for i, color in enumerate(PLAYER_COLORS):
            if color == target_color:
                return i
        
        # If no exact match, find closest color
        closest_index = 0
        min_distance = float('inf')
        for i, color in enumerate(PLAYER_COLORS):
            # Calculate color distance (simple RGB Euclidean distance)
            distance = sum((c1 - c2) ** 2 for c1, c2 in zip(color, target_color))
            if distance < min_distance:
                min_distance = distance
                closest_index = i
        
        return closest_index
                
    def _on_grid_update(self, grid):
        """Called when grid state is updated"""
        # Update game screen if active
        if isinstance(self.current_screen, GameScreen) and self.current_room:
            self.current_screen.grid.grid = grid
            
    def _on_game_start(self):
        """Called when game should start (enough players joined)"""
        if isinstance(self.current_screen, LobbyScreen) and self.current_room:
            self.set_screen("game")
        
    def set_screen(self, screen_name):
        if screen_name in self.screens:
            self.current_screen = self.screens[screen_name]
            
            # Perform screen-specific setup
            if screen_name == "main_menu":
                # Connect to server when entering main menu
                if not self.network_client.connected:
                    self.network_client.connect()
            elif screen_name == "room_list":
                # Request room list when entering room list screen
                self.network_client.request_room_list()
                
    def run(self):
        running = True
        while running:
            # Handle events
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    running = False
                    self.network_client.disconnect()
                elif self.current_screen:
                    self.current_screen.handle_event(event)
            
            # Update network state (process incoming messages)
            if hasattr(self, 'network_client'):
                # This triggers the callbacks we set up
                self.network_client._process_network_events()
            
            # Update current screen
            if self.current_screen:
                self.current_screen.update()
            
            # Draw
            if self.current_screen:
                self.current_screen.draw(self.screen)
            
            pygame.display.flip()
            self.clock.tick(FPS)
            
        pygame.quit()
        sys.exit()

if __name__ == "__main__":
    game = GridClashGame()
    game.run()