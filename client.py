import asyncio, struct, time, random, zlib
from collections import defaultdict

# === Copy shared protocol definitions ===
from ESP_config import *

# === Client ===
class ESPClientProtocol:
    def __init__(self, loop, server_addr):
        self.loop = loop
        self.server_addr = server_addr
        self.transport = None
        self.fragment_manager = FragmentManager()
        
        self.rooms = {}
        self.player_id = None
        self.players = {}
        self.seq = 1
        self.pkt_id = 1
        self.room_id = None
        self.local_id = None
        self.grid = [0] * TOTAL_CELLS

        # === Reliability ===
        self.unacked_packets = {}   # seq -> {'packet': bytes, 'last_sent': time.time_ns(), 'msg_type': int, 'sent_count':int}
        self.snapshot_id = 0

        # === Cell ownership ===
        self.pending_cells = {}     # cell_idx -> timestamp when requested
        self.owned_cells = set()    # confirmed cells owned by this player

    # === Connection lifecycle ===
    def connection_made(self, transport):
        self.transport = transport
        print(f"[Client] Connected to {self.server_addr}")
        self.send_init()

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
        msg_type = pkt['msg_type']

        if msg_type == MESSAGE_TYPES['INIT_ACK']:
            self.handle_init_ack(payload)
        elif msg_type == MESSAGE_TYPES['CREATE_ACK']:
            self.handle_create_ack(payload)
        elif msg_type == MESSAGE_TYPES['JOIN_ACK']:
            self.handle_join_ack(payload)
        elif msg_type == MESSAGE_TYPES['LIST_ROOMS_ACK']:
            self.handle_list_rooms_ack(payload)
        elif msg_type == MESSAGE_TYPES['EVENT']:
            self.handle_event(pkt)
        elif msg_type == MESSAGE_TYPES['UPDATES']:
            self.handle_updates(pkt)
        elif msg_type == MESSAGE_TYPES['SNAPSHOT']:
            self.handle_snapshot(pkt)
        else:
            print(f"[Client] Unknown msg type {msg_type}")

    def connection_lost(self, exc):
        print("[Client] Connection closed:", exc)

    # === Send helpers ===
    def send(self, msg_type, payload=b'', ack=True, repeat=1):
        if ack:
            repeat = 1
            
        if repeat < 1:
            return False
        
        pkts, seq_num = build_packet(msg_type, self.pkt_id, self.seq, payload, self.snapshot_id)
        for p in pkts:
            for i in range(repeat):
                self.transport.sendto(p, self.server_addr)
            if ack:
                # Save for potential retransmit
                self.unacked_packets[self.seq] = {
                    'packet': p,
                    'last_sent': time.time_ns(),
                    'msg_type': msg_type,
                    'sent_count': 0
                }
        self.seq = seq_num
        self.pkt_id += 1
        return True
        
    def ack_packet(self, seq):
        if seq not in self.unacked_packets:
            return False # duplicates
        
        self.unacked_packets.pop(seq, None)
        return True

    # === Message Senders ===
    def send_init(self):
        print("[Client] Sending INIT")
        self.send(MESSAGE_TYPES['INIT'])

    def send_create_room(self, name):
        if not isinstance(name, str):
            return
        payload = build_create_room_payload(name)
        print(f"[Client] Creating room: {name}")
        self.send(MESSAGE_TYPES['CREATE_ROOM'], payload)

    def send_join_room(self, room_id):
        if room_id < 1:
            return
        
        payload = build_join_room_payload(room_id)
        print(f"[Client] Joining room {room_id}")
        self.send(MESSAGE_TYPES['JOIN_ROOM'], payload)
        
    def send_leave_room(self):
        if self.room_id is None:
            return
        print(f"[Client] Leaving room {self.room_id}")
        self.send(MESSAGE_TYPES['LEAVE_ROOM'])

    def send_list_rooms(self):
        print("[Client] Requesting room list")
        self.send(MESSAGE_TYPES['LIST_ROOMS'])

    def request_cell(self, cell_idx):
        """Request ownership of a cell (set to pending)."""
        if cell_idx in self.pending_cells or self.grid[cell_idx] != 0:
            return  # already pending or owned

        payload = build_event_payload(EVENT_TYPES['CELL_ACQUISITION'], self.room_id, self.local_id, cell_idx)
        self.pending_cells[cell_idx] = time.time_ns()
        print(f"[Client] Cell {cell_idx} → PENDING (ownership requested)")
        self.send(MESSAGE_TYPES['EVENT'], payload, False)
        
    def send_updates_ack(self, seq_num):
        if seq_num < 1:
            return
        payload = build_updates_ack_payload(seq_num)
        self.send(MESSAGE_TYPES['UPDATES_ACK'], payload, False)

    def send_snapshot_ack(self, seq_num):
        if seq_num < 1:
            return
        payload = build_snapshot_ack_payload(seq_num)
        self.send(MESSAGE_TYPES['SNAPSHOT_ACK'], payload, False)

    def disconnect(self):
        print("[Client] Disconnecting...")
        self.send(MESSAGE_TYPES['DISCONNECT'])
        if self.transport:
            self.transport.close()

    # === Handlers ===
    def handle_init_ack(self, payload):
        res = parse_init_ack_payload(payload)
        if res:
            seq, player_id = res
            if not self.ack_packet(seq):
                return
            self.player_id = player_id
            print(f"[Client] Got player_id = {self.player_id}")

    def handle_create_ack(self, payload):
        res = parse_create_ack_payload(payload)
        if res:
            seq, room_id = res
            if not self.ack_packet(seq):
                return
            self.room_id = room_id
            print(f"[Client] Room created -> id {self.room_id}")
            self.send_join_room(self.room_id)
    
    
    def handle_join_ack(self, payload):
        res = parse_join_ack_payload(payload)
        if res:
            seq, room_id, local_id, self.players = res # players should be updated even if it is not my ack
            if not self.ack_packet(seq):
                return
            self.room_id = room_id
            self.local_id = local_id
            
            print(f"[Client] Joined room {self.room_id} as local id {self.local_id}")
            print(f"[Client] Room players: {self.players}")
            
    def handle_leave_ack(self, payload):
        res = parse_join_ack_payload(payload)
        if res:
            seq, self.players = res
            if not self.ack_packet(seq):
                return
            print(f"[Client] Left room {self.room_id} as local id {self.local_id}")
            self.room_id = None
            self.players = {}
            self.local_id = None

    def handle_list_rooms_ack(self, payload):
        res = parse_list_rooms_ack_payload(payload)
        if res:
            seq, rooms = res
            if not self.ack_packet(seq):
                return
            print(f"[Client] Available Rooms:") 
            for rid, (count, name) in rooms.items():
                print(f" - {rid}: {name} ({count} players)")
            self.rooms = rooms
            """
            if self.ui:
                self.ui.update_room_list(rooms)
            """
            
    def update_cell(self, event_type, player_local_id, cell_idx):
        if cell_idx < 0 or cell_idx >= TOTAL_CELLS:
            return
        
        if event_type == EVENT_TYPES['CELL_ACQUISITION']:
            if cell_idx in self.pending_cells:
                del self.pending_cells[cell_idx]
            
            if player_local_id == 0:
                return
                
            if player_local_id == self.local_id:
                self.owned_cells.add(cell_idx)
            
            self.grid[cell_idx] = player_local_id
            owner = "you" if player_local_id == self.local_id else f"player {player_local_id}"
            print(f"[Client] Cell {cell_idx} CONFIRMED for {owner}")

    def handle_event(self, pkt):
        payload = pkt['payload']
        ev = parse_event_payload(payload)
        if not ev:
            return
        event_type, room_id, player_local_id, cell_idx = ev
        self.update_cell(event_type, player_local_id, cell_idx)
        self.snapshot_id = pkt['snapshot_id']

    def handle_updates(self, pkt):
        payload = pkt['payload']
        updates = parse_updates_payload(payload)
        if updates:
            required_updates_count = pkt['snapshot_id'] - self.snapshot_id
            if required_updates_count > 0 and required_updates_count <= len(updates):
                for update in list(updates)[-required_updates_count:]:
                    event_type, player_local_id, cell_idx = update
                    self.update_cell(event_type, player_local_id, cell_idx)
                    
                self.snapshot_id = pkt['snapshot_id']
                for seq_key in pkt['seq_keys']: 
                    print(f"[Client] Update #{self.snapshot_id} seq #{seq_key} received & ACKed")
                
            for seq_key in pkt['seq_keys']:    
                self.send_updates_ack(seq_key)
            
    def handle_snapshot(self, pkt):
        payload = pkt['payload']
        grid = parse_snapshot_payload(payload)
        if grid:
            self.grid = grid
            self.snapshot_id = pkt['snapshot_id']
            for seq_key in pkt['seq_keys']:    
                self.send_snapshot_ack(seq_key)
                print(f"[Client] Snapshot #{self.snapshot_id} seq #{seq_key} received & ACKed")

    # === Background resend task ===
    async def resend_unacked(self):
        while True:
            now = time.time_ns()
            for seq, info in list(self.unacked_packets.items()):
                if info['sent_count'] >= MAX_TRANSMISSION_RETRIES:
                    del self.unacked_packets[seq]
                    print(f"[Client] Dropping packet seq={seq} after {MAX_TRANSMISSION_RETRIES} retries (no ACK)")
                    continue
                
                if now - info['last_sent'] > int(RETRANS_TIMEOUT * 1e9):
                    pkt_bytes = info['packet']
                    self.transport.sendto(pkt_bytes, self.server_addr)
                    info['last_sent'] = now
                    info['sent_count'] += 1
                    print(f"[Client] resent packet seq={seq} ({info['sent_count']}/{MAX_TRANSMISSION_RETRIES})")
                    
            await asyncio.sleep(0.5)

    # === Background pending timeout cleanup ===
    async def check_pending_cells(self):
        """Remove or retry pending cells that never got confirmed."""
        while True:
            now = time.time_ns()
            for cell_idx, t0 in list(self.pending_cells.items()):
                if now - t0 > int(RETRANS_TIMEOUT * 1e9):
                    print(f"[Client] Cell {cell_idx} pending too long → retrying request")
                    del self.pending_cells[cell_idx]
                    self.request_cell(cell_idx)
            await asyncio.sleep(1)


def test_create(protocol):
    protocol.send_create_room(f"Room_{random.randint(100,999)}")
    
async def test_list(protocol):
    protocol.send_list_rooms()
    while not protocol.rooms:
        await asyncio.sleep(0.5)
    
    room = 0
    for room_id, (num_of_players, room_name) in protocol.rooms.items():
        print(f"[Client] Room ID:{room_id}, Room Name:{room_name}, Num of Players: {num_of_players}")
        room = room_id
    protocol.send_join_room(room)
    
async def run_test(protocol, test, duration=None):
    start_time = time.time()

    if test == 0:
        test_create(protocol)
    elif test == 1:
        await test_list(protocol)
        
    while True:
        if duration and (time.time() - start_time) >= float(duration):
            print(f"[Client] Test duration {duration}s ended.")
            break
        
        await asyncio.sleep(3)
        if protocol.room_id and protocol.local_id:
            cell = random.randint(0, TOTAL_CELLS - 1)
            protocol.request_cell(cell)
        
# === Runner ===
async def run_client(test, duration=None, host="127.0.0.1", port=9999):
    loop = asyncio.get_event_loop()
    transport, protocol = await loop.create_datagram_endpoint(
        lambda: ESPClientProtocol(loop, (host, port)),
        remote_addr=(host, port)
    )

    # Start background tasks
    loop.create_task(protocol.resend_unacked())
    loop.create_task(protocol.check_pending_cells())

    try:
        if test is not None:
            await run_test(protocol, test, duration)
        else:
            while True:
                await asyncio.sleep(3)
    except KeyboardInterrupt:
        protocol.disconnect()
        transport.close()

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--test", type=int, help="Choose test sequence", required=False)
    parser.add_argument("--duration", type=int, help="Choose test sequence", required=False)
    args = parser.parse_args()
    asyncio.run(run_client(test=args.test, duration=args.duration))
