"""
ESP Protocol Client Integration for Grid Clash
"""
import time
import random
from typing import Dict, List, Tuple, Optional, Callable
import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from ESP_config import *
from client import ESPClientProtocol

class GridClashNetworkClient:
    def __init__(self, server_addr=("127.0.0.1", 9999)):
        self.server_addr = server_addr
        self.connected = False
        self.esp_client = None
        self.room_list = []
        self.current_room = None
        self.players = {}
        self.grid = [0] * (20 * 20)  # 20x20 grid
        
        # Callbacks for UI updates
        self.on_room_list_update: Optional[Callable] = None
        self.on_player_list_update: Optional[Callable] = None  
        self.on_grid_update: Optional[Callable] = None
        self.on_game_start: Optional[Callable] = None
        
    def connect(self):
        """Initialize connection to ESP server"""
        try:
            # Import and initialize the ESP client
            self.esp_client = ESPClientProtocol(self.server_addr, send_init=True)
            self.connected = True
            
            # Start background processing
            self._start_network_loop()
            return True
        except Exception as e:
            print(f"Failed to connect: {e}")
            return False
            
    def _start_network_loop(self):
        """Start background network processing"""
        import threading
        def network_loop():
            while self.connected:
                self._process_network_events()
                time.sleep(0.01)  # 10ms delay
                
        self.network_thread = threading.Thread(target=network_loop, daemon=True)
        self.network_thread.start()
        
    def _process_network_events(self):
        """Process incoming network events and update game state"""
        if not self.esp_client:
            return
        
        # CRITICAL: Actually process incoming messages from the ESP client
        self.esp_client.handle_recv()
        
        # Update room list if available
        if hasattr(self.esp_client, 'rooms') and self.esp_client.rooms:
            new_rooms = []
            for room_id, (player_count, room_name) in self.esp_client.rooms.items():
                new_rooms.append({
                    'id': room_id,
                    'name': room_name,
                    'player_count': player_count
                })
            
            if new_rooms != self.room_list:
                self.room_list = new_rooms
                if self.on_room_list_update:
                    self.on_room_list_update(self.room_list)
        
        # Update players if in a room
        if (self.esp_client.room_id and hasattr(self.esp_client, 'players')):
            # Check if players changed (including when players leave)
            if self.esp_client.players != self.players:
                self.players = self.esp_client.players.copy()
                if self.on_player_list_update:
                    self.on_player_list_update(self.players)
                
        # Update grid state
        if hasattr(self.esp_client, 'grid') and self.esp_client.grid != self.grid:
            self.grid = self.esp_client.grid.copy()
            if self.on_grid_update:
                self.on_grid_update(self.grid)
                
        # Check if we just joined a room (room_id exists but no current room)
        if (self.esp_client.room_id and not self.current_room and 
            hasattr(self.esp_client, 'players') and self.esp_client.players):
            # This means we successfully joined a room via network
            if self.on_game_start:
                self.on_game_start()
    
    def create_room(self, room_name: str) -> bool:
        """Create a new room"""
        if not self.esp_client or not self.connected:
            return False
            
        self.esp_client.send_create_room(room_name)
        return True
        
    def join_room(self, room_id: int) -> bool:
        print(f"[DEBUG] GridClashNetworkClient.join_room({room_id}) called")
        if not self.esp_client or not self.connected:
            print(f"[DEBUG] join_room failed: esp_client={self.esp_client}, connected={self.connected}")
            return False
            
        print(f"[DEBUG] Calling esp_client.send_join_room({room_id})")
        self.esp_client.send_join_room(room_id)
        return True
        
    def leave_room(self) -> bool:
        """Leave current room"""
        if not self.esp_client or not self.esp_client.room_id:
            return False
            
        self.esp_client.send_leave_room()
        self.current_room = None
        return True
        
    def request_room_list(self) -> bool:
        """Request updated room list from server"""
        if not self.esp_client or not self.connected:
            return False
            
        self.esp_client.send_list_rooms()
        return True
        
    def claim_cell(self, cell_x: int, cell_y: int) -> bool:
        """Claim a cell in the grid"""
        if not self.esp_client or not self.esp_client.room_id:
            return False
            
        cell_idx = cell_y * 20 + cell_x
        self.esp_client.request_cell(cell_idx)
        return True
        
    def disconnect(self):
        """Disconnect from server"""
        self.connected = False
        if self.esp_client:
            self.esp_client.disconnect()
            
    def get_player_info(self):
        """Get current player information"""
        if not self.esp_client:
            return None
            
        return {
            'player_id': getattr(self.esp_client, 'player_id', None),
            'local_id': getattr(self.esp_client, 'local_id', None),
            'room_id': getattr(self.esp_client, 'room_id', None)
        }
        
    def get_grid_state(self):
        """Get current grid state"""
        return self.grid.copy() if hasattr(self, 'grid') else [0] * 400
        
    def get_room_players(self):
        """Get players in current room"""
        return self.players.copy() if hasattr(self, 'players') else {}