from ESP_config import *
import time, asyncio, random, logging, argparse
from collections import defaultdict
from typing import Dict, Tuple

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
        self.player_room: Dict[int, int] = {}  # player_id -> room_id
        
        self.next_id = 1
        self.next_seq: Dict[int, int] = {}
        
        self.snapshot_buffer = {}  # (seq, player_id) -> {'packet':bytes, 'last_sent':time, 'sent_count':int}
        self.acked_snapshots = defaultdict(set)  # player_id -> set(seq)

    # Datagram Protocol Methods
    def connection_made(self, transport):
        self.transport = transport
        print("Server listening")

    def datagram_received(self, data, addr):
        pkt = parse_packet(data)
        if pkt is None:
            return

        payload = self.fragment_manager.add_fragment(addr, pkt['id'], pkt['seq'], pkt['payload_len'], pkt['payload'])
        if payload is None:
            return  # waiting for more fragments
        pkt['payload'] = payload
        
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
    
    # Handlers
    def handle_init(self, pkt, addr):
        self.players[self.next_player_id] = PlayerRoomInfo(address=addr, room_id=0, player_local_id=0)
        self.addr_to_player[addr] = self.next_player_id
        self.next_seq[self.next_player_id] = 1
        payload = build_init_ack_payload(pkt['id'], self.next_player_id)
        pkts, seq_num = build_packet(MESSAGE_TYPES['INIT_ACK'], pkt_id=self.next_id, start_seq=self.next_seq[self.next_player_id], payload=payload)
        for p in pkts:
            self.transport.sendto(p, addr)
        print(f"Connected player {self.next_player_id} from {addr}")
        self.next_seq[self.next_player_id] = seq_num
        self.next_player_id += 1
        self.next_id += 1
        
    def handle_create_room(self, pkt, addr):
        
        player_id = self.addr_to_player.get(addr)
        if player_id is None:
            return
        
        next_seq = self.next_seq.get(player_id)
        if next_seq is None:
            return
        
        room_name = parse_create_room_payload(pkt['payload'])
        if room_name is None:
            return
        
        room_id = self.next_room_id
        self.rooms[room_id] = Room(room_id=room_id, name=room_name)
        payload = build_create_ack_payload(pkt['id'], room_id)
        pkts, seq_num = build_packet(MESSAGE_TYPES['CREATE_ACK'], pkt_id=self.next_id, start_seq=next_seq, payload=payload)
        for p in pkts:
            self.transport.sendto(p, addr)
        print(f"Created room {room_id} named '{room_name}'")
        self.next_seq[player_id] = seq_num
        self.next_room_id += 1
        self.next_id += 1
        
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
        
        next_seq = self.next_seq.get(player_id)
        if next_seq is None:
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
        
        self.player_room[player_id] = room_id
        
        payload = build_join_ack_payload(pkt['id'], local_id, {lid: (p.global_id, p.color) for lid, p in room.players.items()})
        pkts, seq_num = build_packet(MESSAGE_TYPES['JOIN_ACK'], pkt_id=self.next_id, start_seq=next_seq, payload=payload)
        for p in pkts:
            self.transport.sendto(p, addr)
        print(f"Player {player_id} joined room {room_id} as local id {local_id}")
        self.next_seq[player_id] = seq_num
        self.next_id += 1
    
    def handle_leave_room(self, pkt, addr):
        # find player_id for addr
        player_id = self.addr_to_player.get(addr)
        if player_id is None:
            return
        
        next_seq = self.next_seq.get(player_id)
        if next_seq is None:
            return
        
        room_id = self.player_room.get(player_id)
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
        del self.player_room[player_id]
        
        payload = b''  # empty payload for LEAVE_ACK
        pkts, seq_num = build_packet(MESSAGE_TYPES['LEAVE_ACK'], pkt_id=self.next_id, start_seq=next_seq, payload=payload)
        for p in pkts:
            self.transport.sendto(p, addr)
        print(f"Player {player_id} left room {room_id}")
        self.next_seq[player_id] = seq_num
        self.next_id += 1
        
    def handle_list_rooms(self, pkt, addr):
        player_id = self.addr_to_player.get(addr)
        if player_id is None:
            return
        
        next_seq = self.next_seq.get(player_id)
        if next_seq is None:
            return
        
        rooms_info = {room_id: (len(room.players), room.name) for room_id, room in self.rooms.items()}
        payload = build_list_rooms_ack_payload(pkt['id'], rooms_info)
        pkts, seq_num = build_packet(MESSAGE_TYPES['LIST_ROOMS_ACK'], pkt_id=self.next_id, start_seq=next_seq, payload=payload)
        for p in pkts:
            self.transport.sendto(p, addr)
        print(f"Sent room list to {addr}")
        self.next_seq[player_id] = seq_num
        self.next_id += 1
        
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
        
        if event_type == EVENT_TYPES['CELL_ACQUISITION']:
            self.handle_cell_acquisition(room, player_local_id, cell_idx)
            
        
        for addr, pid in self.addr_to_player.items():
            next_seq = self.next_seq.get(pid)
            if next_seq is None:
                return
            
            pkts, seq_num = build_packet(MESSAGE_TYPES['EVENT'], pkt_id=self.next_id, start_seq=next_seq, payload=b'')
            for p in pkts:
                for i in range(REDUNDANT_K):
                    self.transport.sendto(p, addr)
            self.next_seq[pid] = seq_num
        self.next_id += 1
        
    def handle_cell_acquisition(self, room, player_local_id, cell_idx):
        if 0 <= cell_idx < TOTAL_CELLS and room.grid[cell_idx] == 0:
            room.grid[cell_idx] = player_local_id

    def handle_snapshot_ack(self, pkt, addr):
        # find player_id for addr
        player_id = self.addr_to_player.get(addr)
        if player_id is None:
            return
        
        seq = pkt['seq']
        self.acked_snapshots[player_id].add(seq)
        
        # remove from buffer for this player
        key = (seq, player_id)
        if key in self.snapshot_buffer:
            pkt = parse_packet(self.snapshot_buffer[key]['packet'])
            recv_time = time.time_ns()
            self.metrics_logger.log_snapshot(
                client_id=player_id,
                snapshot_id=pkt['id'],
                seq_num=seq,
                server_time=pkt['timestamp'],
                recv_time=recv_time
            )
            del self.snapshot_buffer[key]

    def handle_disconnect(self, pkt, addr):
        player_id = self.addr_to_player.get(addr)
        if player_id:
            self.cleanup_player(player_id)
            print(f"Player {player_id} disconnected gracefully")

    # Helper Methods
    def send_snapshot_to_all(self):
        for room in self.rooms.values():
            payload = build_snapshot_payload(room.grid)
            
            for player in room.players.values():
                
                next_seq = self.next_seq.get(player.global_id)
                if next_seq is None:
                    continue
                
                pkts, seq_num = build_packet(MESSAGE_TYPES['SNAPSHOT'], self.next_id, start_seq=next_seq, payload=payload)
                addr = self.players[player.global_id].address
                for p in pkts:
                    now = time.time_ns()
                    self.snapshot_buffer[(self.next_seq[player.global_id], player.global_id)] = {
                        'packet': p,
                        'last_sent': now,
                        'sent_count': 1
                    }
                    self.transport.sendto(p, addr)
                    print(f"Snapshot Semt Player ID:{player.global_id}, Seq_num:{self.next_seq[player.global_id]}")
                self.next_seq[player.global_id] = seq_num
        self.next_id += 1
        
    def clear_player_acked_snapshots(self, player_id: int) -> None:
        self.acked_snapshots.pop(player_id, None)
        keys_to_remove = [k for k in self.snapshot_buffer.keys() if k[1] == player_id]
        for k in keys_to_remove:
            del self.snapshot_buffer[k]
            
    def cleanup_acked_snapshots_keep_last_n(self, keep_last_n: int = 100) -> None:
        for player_id, seq_set in list(self.acked_snapshots.items()):
            if len(seq_set) <= keep_last_n:
                continue
            seqs_sorted = sorted(seq_set)
            to_keep = set(seqs_sorted[-keep_last_n:])
            self.acked_snapshots[player_id] = to_keep
  
    def cleanup_player(self, player_id: int):
        player = self.players.get(player_id)
        if not player:
            return

        # --- 1. Remove from room ---
        room_id = self.player_room.get(player_id)
        if room_id and room_id in self.rooms:
            room = self.rooms[room_id]
            local_id = player.player_local_id
            if local_id in room.players:
                del room.players[local_id]
            print(f"Removed player {player_id} (local id {local_id}) from room {room_id}")

        # --- 2. Remove mapping ---
        addr = player.address
        self.addr_to_player.pop(addr, None)
        self.player_room.pop(player_id, None)

        # --- 3. Clear network-related state ---
        self.clear_player_acked_snapshots(player_id)
        self.fragment_manager.fragments = {k: v for k, v in self.fragment_manager.fragments.items() if k[0] != player_id}

        # --- 4. Remove player object ---
        del self.players[player_id]

        print(f"✅ Cleaned up player {player_id}")

    # Async Methods
    async def periodic_snapshots(self):
        while True:
            self.send_snapshot_to_all()
            await asyncio.sleep(SNAPSHOT_INTERVAL)

    async def periodic_retransmit(self):
        while True:
            now = time.time_ns()
            for (seq, player_id), entry in list(self.snapshot_buffer.items()):
                if now - entry['last_sent'] > RETRANS_TIMEOUT:
                    pkt_bytes = entry['packet']
                    addr = self.players[player_id].address
                    if seq not in self.acked_snapshots[player_id]:
                        self.transport.sendto(pkt_bytes, addr)
                    entry['last_sent'] = now
                    entry['sent_count'] += 1
            await asyncio.sleep(0.2)
    
    async def cleanup_fragments_periodically(self):
        while True:
            self.fragment_manager.cleanup()
            await asyncio.sleep(1.0)
            
    async def periodic_acked_snapshots_cleanup(self, keep_last_n: int = 100, interval: float = 60.0):
        while True:
            self.cleanup_acked_snapshots_keep_last_n(keep_last_n)
            await asyncio.sleep(interval)

async def run_server(host='127.0.0.1', port=9999):
    loop = asyncio.get_event_loop()
    print("Starting server...")
    transport, proto = await loop.create_datagram_endpoint(lambda: ESPServerProtocol(loop), local_addr=(host, port))
    # spawn snapshot broadcaster and retransmit loop
    loop.create_task(proto.periodic_snapshots())
    loop.create_task(proto.periodic_retransmit())
    loop.create_task(proto.cleanup_fragments_periodically())
    loop.create_task(proto.periodic_acked_snapshots_cleanup())
    
    # server runs forever
    return transport, proto

if __name__ == "__main__":
    import argparse, asyncio, logging

    parser = argparse.ArgumentParser()
    parser.add_argument("--clients", nargs="+", help="List of client addresses host:port", required=False)
    parser.add_argument("--rate", type=float, default=20.0, help="Snapshot rate (Hz)")
    parser.add_argument("--duration", type=int, help="Run duration (seconds). Omit for continuous run.")
    parser.add_argument("--log", type=str, default="server.log", help="Log file path")
    args = parser.parse_args()

    logging.basicConfig(filename=args.log, level=logging.INFO, format="%(asctime)s %(message)s")
    print(f"[SERVER] Logging to {args.log}")
    print(f"[SERVER] Clients: {args.clients or 'None (waiting for clients)'}")

    loop = asyncio.get_event_loop()
    transport, proto = loop.run_until_complete(run_server())

    # Start periodic tasks
    loop.create_task(proto.periodic_snapshots())
    loop.create_task(proto.periodic_retransmit())
    loop.create_task(proto.cleanup_fragments_periodically())
    loop.create_task(proto.periodic_acked_snapshots_cleanup())

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
