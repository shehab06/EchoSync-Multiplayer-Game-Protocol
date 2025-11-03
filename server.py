from ESP_config import *
import asyncio, logging, argparse

# ====== Server Implementation ======
class ESPServerProtocol:
    def __init__(self, loop):
        self.loop = loop
        self.transport = None
        self.fragment_manager = FragmentManager()
        self.metrics_logger = MetricsLogger()
        
        self.next_player_id = 1
        self.players: Dict[int, PlayerRoomInfo] = {}  # player_id -> PlayerRoomInfo
        self.addr_to_player: Dict[Tuple[str, int], int] = {}  # addr -> player_id
        
        self.next_room_id = 1
        self.rooms: Dict[int, Room] = {}  # room_id -> Room
        
        self.pkt_id = 1
        self.seq: Dict[int, int] = {} 
        self.unacked_packets = {}  # (seq, player_id) -> {'packet': bytes, 'last_sent': time.time_ns(), 'msg_type': int, 'sent_count':int}

    # Datagram Protocol Methods
    def connection_made(self, transport):
        self.transport = transport
        print("Server listening")

    def datagram_received(self, data, addr):
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
    
    def connection_lost(self, exc):
        print("Connection lost:", exc)

    def pause_writing(self):
        pass

    def resume_writing(self):
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
        
        pkts, seq_num = build_packet(msg_type, self.pkt_id, self.seq[player_id], payload, snapshot_id)
        for p in pkts:
            for i in range(repeat):
                self.transport.sendto(p, address)
            if ack:
                # Save for potential retransmit
                self.unacked_packets[(self.seq[player_id], player_id)] = {
                    'packet': p,
                    'last_sent': time.time_ns(),
                    'msg_type': msg_type,
                    'sent_count': 0
                }
        self.seq[player_id] = seq_num
        
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
        print(f"Connected player {self.next_player_id} from {addr}")
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
                
        print(f"Created room {room_id} named '{room_name}'")
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
        for local_id in range(1, MAX_ROOM_PLAYERS + 1):
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
            
        print(f"Player {player_id} joined room {room_id} as local id {local_id}")
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
            
        print(f"Player {player_id} left room {room_id}")
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
        
        print(f"Sent room list to {addr}")
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
        
        self.update_cell(event_type, room, player_local_id, cell_idx)
        
        for ld, player in room.players.items():
            player_info = self.players.get(player.global_id) 
            if player_info is None:
                continue
            
            address = player_info.address
            if not self.send(MESSAGE_TYPES['EVENT'], address, pkt['payload'], False, REDUNDANT_K_PACKETS):
                return
            
            print(f"Sent event to {address}")
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
            recv_time = time.time_ns()
            self.metrics_logger.log_snapshot(
                client_id=player_id,
                snapshot_id=pkt['snapshot_id'],
                seq_num=seq,
                server_time=pkt['timestamp'],
                recv_time=recv_time
            )
        
        if not self.ack_packet(key): # remove from buffer for this player
            return 
        
        room = self.rooms.get(self.players.get(player_id).room_id)
        required_updates_count = room.snapshot_id - pkt['snapshot_id']
        if required_updates_count > LAST_K_UPDATES:
            payload = build_snapshot_payload(room.grid)
            self.send(MESSAGE_TYPES['SNAPSHOT'], addr, payload=payload, ack = True)
        elif required_updates_count > 0:
            payload = build_updates_payload(list(room.updates)[-required_updates_count])
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
            recv_time = time.time_ns()
            self.metrics_logger.log_snapshot(
                client_id=player_id,
                snapshot_id=pkt['snapshot_id'],
                seq_num=seq,
                server_time=pkt['timestamp'],
                recv_time=recv_time
            )
             
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
            print(f"Player {player_id} disconnected gracefully")

    # Helper Methods
    def send_updates_to_all(self):
        for room in self.rooms.values():
            payload = build_updates_payload(list(room.updates)[-REDUNDANT_K_UPDATES:])
            
            for player in room.players.values():
                seq = self.seq.get(player.global_id)
                if seq is None:
                    continue
                
                if not self.send(MESSAGE_TYPES['UPDATES'], self.players[player.global_id].address, payload=payload, ack = True):
                    continue                
                print(f"Update Sent Player ID:{player.global_id}, Seq_num:{self.seq[player.global_id]}")
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
            print(f"Removed player {player_id} (local id {local_id}) from room {room_id}")

        # --- 2. Remove mapping ---
        addr = player.address
        self.addr_to_player.pop(addr, None)

        # --- 3. Clear network-related state ---
        self.fragment_manager.fragments = {k: v for k, v in self.fragment_manager.fragments.items() if k[0] != player_id}

        # --- 4. Remove player object ---
        del self.players[player_id]

        print(f"✅ Cleaned up player {player_id}")

    # Async Methods
    async def periodic_updates(self):
        while True:
            self.send_updates_to_all()
            await asyncio.sleep(SNAPSHOT_INTERVAL)

    async def periodic_retransmit(self):
        while True:
            now = time.time_ns()
            for (seq, player_id), entry in list(self.unacked_packets.items()):
                if entry['sent_count'] > MAX_TRANSMISSION_RETRIES:
                    del self.unacked_packets[(seq, player_id)]
                    continue
                if now - entry['last_sent'] > RETRANS_TIMEOUT:
                    pkt_bytes = entry['packet']
                    addr = self.players[player_id].address
                    self.transport.sendto(pkt_bytes, addr)
                    entry['last_sent'] = now
                    entry['sent_count'] += 1
                    
            await asyncio.sleep(0.2)
    
    async def cleanup_fragments_periodically(self):
        while True:
            self.fragment_manager.cleanup()
            await asyncio.sleep(1.0)
            

async def run_server(host='127.0.0.1', port=9999):
    loop = asyncio.get_event_loop()
    print("Starting server...")
    transport, proto = await loop.create_datagram_endpoint(lambda: ESPServerProtocol(loop), local_addr=(host, port))
    # spawn snapshot broadcaster and retransmit loop
    loop.create_task(proto.periodic_updates())
    loop.create_task(proto.periodic_retransmit())
    loop.create_task(proto.cleanup_fragments_periodically())
    
    # server runs forever
    return transport, proto

if __name__ == "__main__":
    import argparse, asyncio, logging

    parser = argparse.ArgumentParser()
    parser.add_argument("--clients", nargs="+", help="List of client addresses host:port", required=False)
    parser.add_argument("--rate", type=float, default=20.0, help="Snapshot rate (Hz)")
    parser.add_argument("--duration", type=int, help="Run duration (seconds). Omit for continuous run.")
    parser.add_argument("--log", type=str, help="Log file path", required=False)
    args = parser.parse_args()
    
    if  args.log:
        logging.basicConfig(filename=args.log, level=logging.INFO, format="%(asctime)s %(message)s")
    print(f"[SERVER] Logging to {args.log}")
    print(f"[SERVER] Clients: {args.clients or 'None (waiting for clients)'}")

    loop = asyncio.get_event_loop()
    transport, proto = loop.run_until_complete(run_server())

    # Only stop if duration was given
    if args.duration:
        async def stop_after():
            await asyncio.sleep(args.duration)
            transport.close()
            loop.stop()
            print("[SERVER] Test duration ended, server stopped")

        loop.create_task(stop_after())
        print(f"[SERVER] Running for {args.duration} seconds...")
    else:
        print("[SERVER] Running in continuous mode (Ctrl+C to stop)")

    try:
        loop.run_forever()
    except KeyboardInterrupt:
        print("\n[SERVER] Stopped by user.")
        transport.close()
        loop.stop()
