from ESP_config import *
import logging, argparse, socket, select, sys


# ====== Server Implementation ======
class ESPServerProtocol:
    def __init__(self, host="0.0.0.0", port=9999):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind((host, port))
        self.sock.setblocking(False)
        self.fragment_manager = FragmentManager()
        self.metrics_logger = MetricsLogger()
        
        self.tasks = {
            "broadcast_updates": {"interval": UPDATES_INTERVAL, "last": 0.0, "func": self.send_updates_to_all},
            "retransmit": {"interval": 0.2, "last": 0.0, "func": self.retransmit},
            "fragment_cleanup": {"interval": 1.0, "last": 0.0, "func": self.fragment_manager.cleanup},
        }        
        
        self.next_player_id = 1
        self.players: Dict[int, PlayerRoomInfo] = {}  # player_id -> PlayerRoomInfo
        self.addr_to_player: Dict[Tuple[str, int], int] = {}  # addr -> player_id
        
        self.next_room_id = 1
        self.rooms: Dict[int, Room] = {}  # room_id -> Room
        
        self.pkt_id = 1
        self.seq: Dict[int, int] = {} 
        self.unacked_packets = {}  # (seq, player_id) -> {'packet': bytes, 'last_sent': time.time_ns(), 'msg_type': int, 'sent_count':int}

    # Datagram Protocol Methods
    def run(self, duration=None):
        try:
            log("[SERVER] Running (Ctrl+C to stop)")
            start = time.time()
            while True:
                if duration and (time.time() - start) >= duration:
                    log("[SERVER] Test duration ended, server stopped")
                    try:
                        self.sock.close()
                    except Exception:
                        pass
                    sys.exit(0)
                    
                # wait for readability up to 0.001ms
                rlist, _, _ = select.select([self.sock], [], [], 0.001)

                if rlist:
                    self.handle_recv()
                
                now = time.time()
                for name, t in self.tasks.items():
                    if now - t["last"] >= t["interval"]:
                        try:
                            t["func"]()
                        except Exception as e:
                            log(f"[SERVER] {name} error:", e)
                        t["last"] = now
                
        except KeyboardInterrupt:
            log("[SERVER] Stopping by user (Ctrl+C).")
            try:
                self.sock.close()
            except Exception:
                pass
            sys.exit(0)
        
    def handle_recv(self):
        while True:
            try:
                data, addr = self.sock.recvfrom(65536)
            except BlockingIOError:
                return
            except Exception as e:
                log("[SERVER] recv error:", e)
                return
            
            pkt = parse_packet(data)
            if pkt is None:
                return

            frag_result = self.fragment_manager.add_fragment(addr, pkt['pkt_id'], pkt['seq'], pkt['payload_len'], pkt['payload'])
            if frag_result is None:
                return # waiting for more fragments
            
            (seq_keys, payload) = frag_result
            pkt['payload'] = payload
            pkt['seq_keys'] = seq_keys
            
            t = pkt['msg_type']
            if t == MESSAGE_TYPES['INIT']:
                self.handle_init(pkt, addr)
            elif t == MESSAGE_TYPES['CREATE_ROOM']:
                self.handle_create_room(pkt, addr)
            elif t == MESSAGE_TYPES['JOIN_ROOM']:
                self.handle_join_room(pkt, addr)
            elif t == MESSAGE_TYPES['LEAVE_ROOM']:
                self.handle_leave_room(pkt, addr)
            elif t == MESSAGE_TYPES['LIST_ROOMS']:
                self.handle_list_rooms(pkt, addr)
            elif t == MESSAGE_TYPES['EVENT']:
                self.handle_event(pkt, addr)
            elif t == MESSAGE_TYPES['UPDATES_ACK']:
                self.handle_updates_ack(pkt, addr)
            elif t == MESSAGE_TYPES['SNAPSHOT_ACK']:
                self.handle_snapshot_ack(pkt, addr)
            elif t == MESSAGE_TYPES['DISCONNECT']:
                self.handle_disconnect(pkt, addr)
            else:
                # ignore clients won't send INIT_ACK, CREATE_ACK, JOIN_ACK, LIST_ROOMS_ACK, SNAPSHOT or unknown message type
                pass
                    
    # === Send helpers ===
    def send(self, msg_type, address, payload=b'', ack=False, repeat=1):
        if ack:
            repeat = 1
            
        if repeat < 1:
            return False
        
        player_id = self.addr_to_player.get(address)

        if player_id is None:
            return False

        if self.seq.get(player_id) is None:
            return False
        
        snapshot_id = 0
        if self.players.get(player_id) is not None and self.rooms.get(self.players.get(player_id).room_id) is not None:
            snapshot_id = self.rooms.get(self.players.get(player_id).room_id).snapshot_id
        pkts = build_packet(msg_type, self.pkt_id, self.seq[player_id], payload, snapshot_id)

        for p in pkts:
            for i in range(repeat):
                try:
                    self.sock.sendto(p, address)
                except Exception:
                    pass
            if ack:
                # Save for potential retransmit
                self.unacked_packets[(self.seq[player_id], player_id)] = {
                    'packet': p,
                    'last_sent': time.time_ns(),
                    'msg_type': msg_type,
                    'sent_count': 0
                }
            
            if msg_type == MESSAGE_TYPES['UPDATES']:
                self.metrics_logger.log_snapshot(
                    client_id=player_id,
                    snapshot_id=snapshot_id,
                    seq_num=self.seq[player_id],
                    server_time=parse_packet(p)['timestamp'],
                    grid=self.rooms.get(self.players.get(player_id).room_id).grid,
                )
                
            self.seq[player_id] += 1
        
        return True
    
    def ack_packet(self, key):
        if key not in self.unacked_packets:
            return False # duplicates
        
        self.unacked_packets.pop(key, None)
        return True
    
    # Handlers
    def handle_init(self, pkt, addr):
        self.players[self.next_player_id] = PlayerRoomInfo(address=addr, room_id=0, player_local_id=0)
        self.addr_to_player[addr] = self.next_player_id
        self.seq[self.next_player_id] = 1
        for seq_key in pkt['seq_keys']:
            payload = build_init_ack_payload(seq_key, self.next_player_id)
            if not self.send(MESSAGE_TYPES['INIT_ACK'], addr, payload):
                return
        log(f"Connected player {self.next_player_id} from {addr}")
        self.next_player_id += 1
        self.pkt_id += 1
        
    def handle_create_room(self, pkt, addr):
        room_name = parse_create_room_payload(pkt['payload'])
        if room_name is None:
            return
        
        room_id = self.next_room_id
        self.rooms[room_id] = Room(room_id=room_id, name=room_name)
        for seq_key in pkt['seq_keys']:
            payload = build_create_ack_payload(seq_key, room_id)
            if not self.send(MESSAGE_TYPES['CREATE_ACK'], addr, payload):
                return
                
        log(f"Created room {room_id} named '{room_name}'")
        self.next_room_id += 1
        self.pkt_id += 1
        
    def handle_join_room(self, pkt, addr):
        room_id = parse_join_room_payload(pkt['payload'])
        if room_id is None:
            return
        
        room = self.rooms.get(room_id)
        if room is None:
            return
        
        # find player_id for addr
        player_id = self.addr_to_player.get(addr)
        if player_id is None:
            return
        
        seq = self.seq.get(player_id)
        if seq is None:
            return
            
        # assign local id
        used_ids = set(room.players.keys())
        for local_id in range(1, REQUIRED_ROOM_PLAYERS + 1):
            if local_id not in used_ids:
                break
        else:
            return
        
        color = (random.randint(50,255), random.randint(50,255), random.randint(50,255))
        while any(p.color == color for p in room.players.values()):
            color = (random.randint(50,255), random.randint(50,255), random.randint(50,255))
            
        room.players[local_id] = RoomPlayer(global_id=player_id, color=color)
        self.players[player_id].room_id = room_id
        self.players[player_id].player_local_id = local_id
        
        
        players = {lid: (p.global_id, p.color) for lid, p in room.players.items()}
        sent = False
        for ld, player in room.players.items():            
            player_info = self.players.get(player.global_id) 
            if player_info is None:
                continue
            
            address = player_info.address
            seq_keys = [pkt['seq_keys'][0]]
            if ld == local_id:
                seq_keys = pkt['seq_keys']
                
            for seq_key in seq_keys:
                payload = build_join_ack_payload(seq_key, room_id, ld, players)
                if not self.send(MESSAGE_TYPES['JOIN_ACK'], address, payload, False, REDUNDANT_K_PACKETS):
                    break
                sent = True
                
        if sent:
            log(f"Player {player_id} joined room {room_id} as local id {local_id}")
            self.pkt_id += 1
    
    def handle_leave_room(self, pkt, addr):
        # find player_id for addr
        player_id = self.addr_to_player.get(addr)
        if player_id is None:
            return
        
        seq = self.seq.get(player_id)
        if seq is None:
            return
        
        room_id = self.players.get(player_id).room_id
        if room_id is None:
            return
        
        room = self.rooms.get(room_id)
        if room is None:
            return
        
        local_id = self.players[player_id].player_local_id
        if local_id in room.players:
            del room.players[local_id]
        
        self.players[player_id].room_id = 0
        self.players[player_id].player_local_id = 0
        
        players = {lid: (p.global_id, p.color) for lid, p in room.players.items()}
        sent = False   
        for ld, player in room.players.items():            
            player_info = self.players.get(player.global_id) 
            if player_info is None:
                continue
            
            address = player_info.address
            seq_keys = [pkt['seq_keys'][0]]
            if ld == local_id:
                seq_keys = pkt['seq_keys']
    
            for seq_key in seq_keys:
                payload = build_leave_ack_payload(seq_key, players)
                if not self.send(MESSAGE_TYPES['LEAVE_ACK'], address, payload, False, REDUNDANT_K_PACKETS):
                    break
                sent = True
        
        if sent:
            log(f"Player {player_id} left room {room_id}")
            self.pkt_id += 1
        
    def handle_list_rooms(self, pkt, addr):
        player_id = self.addr_to_player.get(addr)
        if player_id is None:
            return
        
        rooms_info = {room_id: (len(room.players), room.name) for room_id, room in self.rooms.items()}
        
        for seq_key in pkt['seq_keys']:
            payload = build_list_rooms_ack_payload(seq_key, rooms_info)
            if not self.send(MESSAGE_TYPES['LIST_ROOMS_ACK'], addr, payload):
                return
        
        log(f"Sent room list to {addr}")
        self.pkt_id += 1
        
    def handle_event(self, pkt, addr):
        player_id = self.addr_to_player.get(addr)
        if player_id is None:
            return
        
        ev = parse_event_payload(pkt['payload'])
        if ev is None:
            return
        event_type, room_id, player_local_id, cell_idx = ev
        
        room = self.rooms.get(room_id)
        if room is None:
            return
        
        if room.players.get(player_local_id) is None:
            return
        
        sent = False
        if len(room.players) < REQUIRED_ROOM_PLAYERS:
            player_info = self.players.get(player_id) 
            if player_info is None:
                return
            
            address = player_info.address
            payload = build_event_payload(event_type, room_id, 0, cell_idx)
            if not self.send(MESSAGE_TYPES['EVENT'], address, payload, False, REDUNDANT_K_PACKETS):
                return
            
            log(f"Sent event to {address}")
        else:
            self.update_cell(event_type, room, player_local_id, cell_idx)
            for ld, player in room.players.items():
                player_info = self.players.get(player.global_id) 
                if player_info is None:
                    continue
                
                address = player_info.address
                if not self.send(MESSAGE_TYPES['EVENT'], address, pkt['payload'], False, REDUNDANT_K_PACKETS):
                    continue
                sent = True
                log(f"Sent event to {address}")
        if sent:
            self.pkt_id += 1
        
    def update_cell(self, event_type, room, player_local_id, cell_idx):
        
        if not (0 <= cell_idx < TOTAL_CELLS):
            return
        
        if event_type == EVENT_TYPES['CELL_ACQUISITION']:
            if room.grid[cell_idx] != 0:
                return
            room.grid[cell_idx] = player_local_id
            
        room.snapshot_id += 1
        room.updates.append((event_type, player_local_id, cell_idx))

    def handle_updates_ack(self, pkt, addr):
        # find player_id for addr
        player_id = self.addr_to_player.get(addr)
        if player_id is None:
            return
        
        if self.players.get(player_id) is None or self.rooms.get(self.players.get(player_id).room_id) is None:
            return
        
        seq = parse_updates_ack_payload(pkt["payload"])
        if not seq:
            return
        
        key = (seq, player_id)
        
        if key in self.unacked_packets:
            pkt = parse_packet(self.unacked_packets[key]['packet'])
            if pkt is None:
                return
        
        if not self.ack_packet(key): # remove from buffer for this player
            return 
        
        room = self.rooms.get(self.players.get(player_id).room_id)
        required_updates_count = room.snapshot_id - pkt['snapshot_id']
        if required_updates_count > LAST_K_UPDATES:
            payload = build_snapshot_payload(room.grid)
            self.send(MESSAGE_TYPES['SNAPSHOT'], addr, payload=payload, ack = True)
        elif required_updates_count > 0:
            payload = build_updates_payload(list(room.updates)[-required_updates_count:])
            self.send(MESSAGE_TYPES['UPDATES'], addr, payload=payload, ack = True)

    def handle_snapshot_ack(self, pkt, addr):
        # find player_id for addr
        player_id = self.addr_to_player.get(addr)
        if player_id is None:
            return
        
        if self.players.get(player_id) is None or self.rooms.get(self.players.get(player_id).room_id) is None:
            return
        
        seq = parse_snapshot_ack_payload(pkt["payload"])
        if not seq:
            return
        
        seq = pkt['seq']
        key = (seq, player_id)
        
        if key in self.unacked_packets:
            pkt = parse_packet(self.unacked_packets[key]['packet'])
            if pkt is None:
                return
             
        if not self.ack_packet(key): # remove from buffer for this player
            return 
        
        room = self.rooms.get(self.players.get(player_id).room_id)
        snapshot_id = room.snapshot_id
        if pkt['snapshot_id'] < snapshot_id:
            payload = build_snapshot_payload(room.grid)
            self.send(MESSAGE_TYPES['SNAPSHOT'], addr, payload=payload, ack = True)

    def handle_disconnect(self, pkt, addr):
        player_id = self.addr_to_player.get(addr)
        if player_id:
            self.cleanup_player(player_id)
            log(f"Player {player_id} disconnected gracefully")

    # Helper Methods
    def send_updates_to_all(self):
        sent = False
        for room in self.rooms.values():
            payload = build_updates_payload(list(room.updates)[-REDUNDANT_K_UPDATES:])
            if len(room.players) < REQUIRED_ROOM_PLAYERS:
                continue
            for player in room.players.values():
                seq = self.seq.get(player.global_id)
                if seq is None:
                    continue
                
                if not self.send(MESSAGE_TYPES['UPDATES'], self.players[player.global_id].address, payload=payload, ack = True):
                    continue                
                log(f"Updates Sent Player ID:{player.global_id}, Seq_num:{self.seq[player.global_id]}")
                sent = True
                
        if sent:
            self.pkt_id += 1
        
    def cleanup_player(self, player_id: int):
        player = self.players.get(player_id)
        if not player:
            return

        # --- 1. Remove from room ---
        room_id = self.players.get(player_id).room_id
        if room_id and room_id in self.rooms:
            room = self.rooms[room_id]
            local_id = player.player_local_id
            if local_id in room.players:
                del room.players[local_id]
            log(f"Removed player {player_id} (local id {local_id}) from room {room_id}")

        # --- 2. Remove mapping ---
        addr = player.address
        self.addr_to_player.pop(addr, None)

        # --- 3. Clear network-related state ---
        self.fragment_manager.fragments = {k: v for k, v in self.fragment_manager.fragments.items() if k[0] != player_id}

        # --- 4. Remove player object ---
        del self.players[player_id]

        log(f"✅ Cleaned up player {player_id}")

    def retransmit(self):
        now = time.time_ns()
        for (seq, player_id), entry in list(self.unacked_packets.items()):
            if entry['sent_count'] >= MAX_TRANSMISSION_RETRIES:
                del self.unacked_packets[(seq, player_id)]
                continue
            if now - entry['last_sent'] > int(RETRANS_TIMEOUT * 1e9):
                pkt_bytes = entry['packet']
                addr = self.players[player_id].address
                try:
                    self.sock.sendto(pkt_bytes, addr)
                except Exception:
                    pass
                entry['last_sent'] = now
                entry['sent_count'] += 1

            
if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--rate", type=float, default=20.0, help="Snapshot rate (Hz)")
    parser.add_argument("--duration", type=int, help="Run duration (seconds). Omit for continuous run.")
    parser.add_argument("--log", type=str, help="Log file path", required=False)
    args = parser.parse_args()
    
    if  args.log:
        logging.basicConfig(filename=args.log, level=logging.INFO, format="%(asctime)s %(message)s")

    server = ESPServerProtocol()
    server.run(args.duration)
