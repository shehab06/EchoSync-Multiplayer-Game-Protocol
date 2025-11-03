import asyncio, struct, time, random, zlib
from collections import defaultdict

# === Copy shared protocol definitions ===
from ESP_config import *

# === Client ===
class ESPClientProtocol:
    def __init__(self, loop, server_addr, ui = None):
        self.loop = loop
        self.server_addr = server_addr
        self.transport = None
        self.fragment_manager = FragmentManager()
        
        self.ui = ui
        self.player_id = None
        self.players = {}
        self.seq = 1
        self.pkt_id = 1
        self.room_id = None
        self.local_id = None
        self.grid = [0] * TOTAL_CELLS

        # === Reliability ===
        self.unacked_packets = {}   # seq -> {'packet': bytes, 'time': time.time(), 'msg_type': int}
        self.resend_count = defaultdict(int)

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

        frag_result = self.fragment_manager.add_fragment(addr, pkt['id'], pkt['seq'], pkt['payload_len'], pkt['payload'])
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
        elif msg_type == MESSAGE_TYPES['SNAPSHOT']:
            self.handle_snapshot(pkt)
        elif msg_type == MESSAGE_TYPES['EVENT']:
            self.handle_event(payload)
        elif msg_type == MESSAGE_TYPES['SNAPSHOT_ACK']:
            self.handle_snapshot_ack(payload)
        else:
            print(f"[Client] Unknown msg type {msg_type}")

    def connection_lost(self, exc):
        print("[Client] Connection closed:", exc)

    # === Send helpers ===
    def send(self, msg_type, payload=b'', ack=True):
        pkts, seq_num = build_packet(msg_type, self.pkt_id, self.seq, payload)
        for p in pkts:
            self.transport.sendto(p, self.server_addr)
            if ack:
                # Save for potential retransmit
                self.unacked_packets[self.seq] = {
                    'packet': p,
                    'time': time.time_ns(),
                    'msg_type': msg_type
                }
                self.resend_count[self.seq] = 0
        self.seq = seq_num
        self.pkt_id += 1
        
    def ack_packet(self, seq):
        if seq not in self.unacked_packets:
            return False # duplicates
        
        del self.unacked_packets[seq]
        del self.resend_count[seq]
        return True

    # === Message Senders ===
    def send_init(self):
        print("[Client] Sending INIT")
        self.send(MESSAGE_TYPES['INIT'])

    def send_create_room(self, name):
        payload = build_create_room_payload(name)
        print(f"[Client] Creating room: {name}")
        self.send(MESSAGE_TYPES['CREATE_ROOM'], payload)

    def send_join_room(self, room_id):
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

        payload = build_event_payload(EVENT_TYPES['CELL_ACQUISITION'],
                                      self.room_id, self.local_id, cell_idx)
        self.pending_cells[cell_idx] = time.time()
        print(f"[Client] Cell {cell_idx} → PENDING (ownership requested)")
        self.send(MESSAGE_TYPES['EVENT'], payload)

    def send_snapshot_ack(self, seq_num):
        payload = build_snapshot_ack_payload(seq_num)
        self.send(MESSAGE_TYPES['SNAPSHOT_ACK'], payload)

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
            self.send_create_room(f"Room_{random.randint(100,999)}")

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

            """
            if self.ui:
                self.ui.update_room_list(rooms)
            """

    def handle_snapshot(self, pkt):
        payload = pkt['payload']
        grid = parse_snapshot_payload(payload)
        if grid:
            self.grid = grid
            self.send_snapshot_ack(pkt['seq'])
            print(f"[Client] Snapshot #{pkt['seq']} received & ACKed")

    def handle_snapshot_ack(self, payload):
        seq = parse_snapshot_ack_payload(payload)
        if seq in self.unacked_packets:
            del self.unacked_packets[seq]
            del self.resend_count[seq]
            print(f"[Client] ACK received for seq {seq}, removed from buffer")

    def handle_event(self, payload):
        ev = parse_event_payload(payload)
        if not ev:
            return
        event_type, room_id, player_local_id, cell_idx = ev

        if event_type == EVENT_TYPES['CELL_ACQUISITION']:
            if cell_idx in self.pending_cells:
                del self.pending_cells[cell_idx]
            if player_local_id == self.local_id:
                self.owned_cells.add(cell_idx)
            self.grid[cell_idx] = player_local_id
            owner = "you" if player_local_id == self.local_id else f"player {player_local_id}"
            print(f"[Client] Cell {cell_idx} CONFIRMED for {owner}")

    # === Background resend task ===
    async def resend_unacked(self, timeout=2.0):
        while True:
            now = time.time()
            to_resend = []
            for seq, info in list(self.unacked_packets.items()):
                if now - info['time'] > timeout:
                    to_resend.append(seq)

            for seq in to_resend:
                self.resend_count[seq] += 1
                if self.resend_count[seq] <= REDUNDANT_K_PACKETS:
                    p = self.unacked_packets[seq]['packet']
                    self.transport.sendto(p, self.server_addr)
                    self.unacked_packets[seq]['time'] = now
                    print(f"[Client] (K-redundant) resent packet seq={seq} ({self.resend_count[seq]}/{REDUNDANT_K_PACKETS})")
                else:
                    print(f"[Client] Dropping packet seq={seq} after {REDUNDANT_K_PACKETS} retries (no ACK)")
                    del self.unacked_packets[seq]
                    del self.resend_count[seq]

            await asyncio.sleep(0.5)

    # === Background pending timeout cleanup ===
    async def check_pending_cells(self, timeout=5.0):
        """Remove or retry pending cells that never got confirmed."""
        while True:
            now = time.time()
            for cell_idx, t0 in list(self.pending_cells.items()):
                if now - t0 > timeout:
                    print(f"[Client] Cell {cell_idx} pending too long → retrying request")
                    del self.pending_cells[cell_idx]
                    self.request_cell(cell_idx)
            await asyncio.sleep(1)


# === Runner ===
async def run_client(host="127.0.0.1", port=9999):
    loop = asyncio.get_event_loop()
    transport, protocol = await loop.create_datagram_endpoint(
        lambda: ESPClientProtocol(loop, (host, port)),
        remote_addr=(host, port)
    )

    # Start background tasks
    loop.create_task(protocol.resend_unacked())
    loop.create_task(protocol.check_pending_cells())

    try:
        while True:
            await asyncio.sleep(3)
            if protocol.room_id and protocol.local_id:
                cell = random.randint(0, TOTAL_CELLS - 1)
                protocol.request_cell(cell)
    except KeyboardInterrupt:
        protocol.disconnect()
        transport.close()


if __name__ == "__main__":
    asyncio.run(run_client())
