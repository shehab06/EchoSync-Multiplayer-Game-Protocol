import time, random, socket, select

# === Copy shared protocol definitions ===
from ESP_config import *

# === Client ===
class ESPClientProtocol:
    def __init__(self, server_addr, metrices_id=None, send_init = True, islogging=False):
        self.server_addr = server_addr
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setblocking(False)
        metrices_id = metrices_id if metrices_id is not None else random.randint(1000,9999)
        self.fragment_manager = FragmentManager()
        self.islogging = islogging
        if self.islogging:
            self.metrics_logger = MetricsLogger(f"client_{metrices_id}_metrics.csv", server_mode=False)
        self.bytes_received = 0
        self.packets_received = 0
    
        self.tasks = {
            "retransmit": {"interval": 0.2, "last": 0.0, "func": self.retransmit},
            "check_pending_cells": {"interval": 0.2, "last": 0.0, "func": self.check_pending_cells},
            "fragment_cleanup": {"interval": 1.0, "last": 0.0, "func": self.fragment_manager.cleanup},
            "test":{"interval": 3.0, "last": 0.0, "func": self.test_behavior},
        } 
        
        self.rooms = {}
        self.player_id = None
        self.players = {}
        self.seq = 1
        self.pkt_id = 1
        self.room_id = None
        self.local_id = None
        self.grid = [0] * TOTAL_CELLS
        self.positions = {} # player_id -> (x,y)

        # === Reliability ===
        self.unacked_packets = {}   # seq -> {'packet': bytes, 'last_sent': time.time_ns(), 'msg_type': int, 'sent_count':int}
        self.snapshot_id = 0

        # === Cell ownership ===
        self.pending_cells = {}     # cell_idx -> timestamp when requested
        self.owned_cells = set()    # confirmed cells owned by this player
        if send_init:
            self.send_init()

    
    def run(self, duration=None, test=None):
        try:
            log(f"[Client] Running (Ctrl+C to stop)")
            start = time.time()
            while True:
                if duration and (time.time() - start) >= duration:
                    log(f"[Client] Test duration ended, client stopped")
                    self.disconnect()
                    break
                    
                # wait for readability up to 0.01ms
                rlist, _, _ = select.select([self.sock], [], [], 0.01)

                if rlist:
                    self.handle_recv()
                
                now = time.time()
                for name, t in self.tasks.items():
                    if name=="test" and test is None:
                        continue
                    
                    if now - t["last"] >= t["interval"]:
                        try:
                            if name=="test":
                                t["func"](test)
                            else:
                                t["func"]()
                        except Exception as e:
                            log(f"[Client] {name} error:", e)
                        t["last"] = now
                       
        except KeyboardInterrupt:
            log(f"[Client] Stopping by user (Ctrl+C).")
            self.disconnect()
            return
            
    
    def handle_recv(self):
        while True:
            try:
                data, addr = self.sock.recvfrom(65536)
                self.bytes_received += len(data)
                self.packets_received += 1
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
            msg_type = pkt['msg_type']

            if msg_type == MESSAGE_TYPES['INIT_ACK']:
                self.handle_init_ack(payload)
            elif msg_type == MESSAGE_TYPES['CREATE_ACK']:
                self.handle_create_ack(payload)
            elif msg_type == MESSAGE_TYPES['JOIN_ACK']:
                self.handle_join_ack(payload)
            elif msg_type == MESSAGE_TYPES['LEAVE_ACK']:
                self.handle_leave_ack(payload)
            elif msg_type == MESSAGE_TYPES['LIST_ROOMS_ACK']:
                self.handle_list_rooms_ack(payload)
            elif msg_type == MESSAGE_TYPES['EVENT']:
                self.handle_event(pkt)
            elif msg_type == MESSAGE_TYPES['UPDATES']:
                self.handle_updates(pkt)
            elif msg_type == MESSAGE_TYPES['SNAPSHOT']:
                self.handle_snapshot(pkt)
            else:
                log(f"[Client] Unknown msg type {msg_type}")

    # === Send helpers ===
    def send(self, msg_type, payload=b'', ack=True, repeat=1):
        if ack:
            repeat = 1
            
        if repeat < 1:
            return False
        
        pkts = build_packet(msg_type, self.pkt_id, self.seq, payload, self.snapshot_id)
        for p in pkts:
            for i in range(repeat):
                try:
                    self.sock.sendto(p, self.server_addr)
                except Exception:
                    pass
            if ack:
                # Save for potential retransmit
                self.unacked_packets[self.seq] = {
                    'packet': p,
                    'last_sent': time.time_ns(),
                    'msg_type': msg_type,
                    'sent_count': 0
                }
            
            self.seq += 1
        self.pkt_id += 1
        return True
        
    def ack_packet(self, seq):
        if seq not in self.unacked_packets:
            return False # duplicates
        
        self.unacked_packets.pop(seq, None)
        return True

    # === Message Senders ===
    def send_init(self):
        log(f"[Client] Sending INIT")
        self.send(MESSAGE_TYPES['INIT'])

    def send_create_room(self, name):
        if not isinstance(name, str):
            return
        payload = build_create_room_payload(name)
        log(f"[Client] Creating room: {name}")
        self.send(MESSAGE_TYPES['CREATE_ROOM'], payload)

    def send_join_room(self, room_id):
        if room_id < 1:
            return
        
        payload = build_join_room_payload(room_id)
        log(f"[Client] Joining room {room_id}")
        self.send(MESSAGE_TYPES['JOIN_ROOM'], payload)
        
    def send_leave_room(self):
        if self.room_id is None:
            return
        log(f"[Client] Leaving room {self.room_id}")
        self.send(MESSAGE_TYPES['LEAVE_ROOM'])

    def send_list_rooms(self):
        log(f"[Client] Requesting room list")
        self.send(MESSAGE_TYPES['LIST_ROOMS'])

    def request_cell(self, cell_idx):
        """Request ownership of a cell (set to pending)."""
        if cell_idx in self.pending_cells or self.grid[cell_idx] != 0:
            return  # already pending or owned

        payload = build_event_payload(EVENT_TYPES['CELL_ACQUISITION'], self.room_id, self.local_id, cell_idx)
        self.pending_cells[cell_idx] = time.time_ns()
        log(f"[Client] Cell {cell_idx} → PENDING (ownership requested)")
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
        log(f"[Client] Disconnecting...")
        self.send(MESSAGE_TYPES['DISCONNECT'])
            
        try:
            self.sock.close()
        except Exception:
            pass

    # === Handlers ===
    def handle_init_ack(self, payload):
        res = parse_init_ack_payload(payload)
        if res:
            seq, player_id = res
            if not self.ack_packet(seq):
                return
            self.player_id = player_id
            log(f"[Client] Got player_id = {self.player_id}")

    def handle_create_ack(self, payload):
        res = parse_create_ack_payload(payload)
        if res:
            seq, room_id = res
            if not self.ack_packet(seq):
                return
            self.room_id = room_id
            log(f"[Client] Room created -> id {self.room_id}")
            self.send_join_room(self.room_id)
    
    
    def handle_join_ack(self, payload):
        res = parse_join_ack_payload(payload)
        if res:
            seq, room_id, local_id, self.players = res # players should be updated even if it is not my ack
            if not self.ack_packet(seq):
                return
            
            # self.snapshot_id = 0 # reset snapshot_id when joining a room
            self.room_id = room_id
            self.local_id = local_id
            
            log(f"[Client] Joined room {self.room_id} as local id {self.local_id}")
            log(f"[Client] Room players: {self.players}")
            
    def handle_leave_ack(self, payload):
        res = parse_leave_ack_payload(payload)
        if res:
            seq, self.players = res
            if not self.ack_packet(seq):
                return
            log(f"[Client] Left room {self.room_id} as local id {self.local_id}")
            self.room_id = None
            self.players = {}
            self.local_id = None

    def handle_list_rooms_ack(self, payload):
        res = parse_list_rooms_ack_payload(payload)
        if res:
            seq, rooms = res
            if not self.ack_packet(seq):
                return
            log(f"[Client] Available Rooms:") 
            for rid, (count, name) in rooms.items():
                log(f" - {rid}: {name} ({count} players)")
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
            x = cell_idx % GRID_N
            y = cell_idx // GRID_N
            self.positions[player_local_id] = (x, y)
            owner = "you" if player_local_id == self.local_id else f"player {player_local_id}"
            log(f"[Client] Cell {cell_idx} CONFIRMED for {owner}")

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
                    log(f"[Client] Update #{self.snapshot_id} seq #{seq_key} received & ACKed")
                
            for seq_key in pkt['seq_keys']:    
                self.send_updates_ack(seq_key)
                recv_time = time.time_ns()
                if self.islogging:
                    self.metrics_logger.log_snapshot(
                        client_id=self.player_id,
                        snapshot_id=pkt['snapshot_id'],
                        seq_num=seq_key,
                        server_time=pkt['timestamp'],
                        recv_time=recv_time,
                        positions=self.positions if self.positions else "",
                        bytes_received=self.bytes_received,
                    )
            
    def handle_snapshot(self, pkt):
        payload = pkt['payload']
        grid = parse_snapshot_payload(payload)
        if grid:
            self.grid = grid
            self.snapshot_id = pkt['snapshot_id']
            for seq_key in pkt['seq_keys']:    
                self.send_snapshot_ack(seq_key)
                log(f"[Client] Snapshot #{self.snapshot_id} seq #{seq_key} received & ACKed")

    # === Background retransmit task ===
    def retransmit(self):
        now = time.time_ns()
        for seq, info in list(self.unacked_packets.items()):
            if info['sent_count'] >= MAX_TRANSMISSION_RETRIES:
                del self.unacked_packets[seq]
                log(f"[Client] Dropping packet seq={seq} after {MAX_TRANSMISSION_RETRIES} retries (no ACK)")
                continue
            
            if now - info['last_sent'] > int(RETRANS_TIMEOUT * 1e9):
                pkt_bytes = info['packet']
                try:
                    self.sock.sendto(pkt_bytes, self.server_addr)
                except Exception:
                    pass
                info['last_sent'] = now
                info['sent_count'] += 1
                log(f"[Client] resent packet seq={seq} ({info['sent_count']}/{MAX_TRANSMISSION_RETRIES})")
                    

    # === Background pending timeout cleanup ===
    def check_pending_cells(self):
        """Remove or retry pending cells that never got confirmed."""
        now = time.time_ns()
        for cell_idx, t0 in list(self.pending_cells.items()):
            if now - t0 > int(RETRANS_TIMEOUT * 1e9):
                log(f"[Client] Cell {cell_idx} pending too long → retrying request")
                del self.pending_cells[cell_idx]
                self.request_cell(cell_idx)
    
    
    def test_behavior(self, test):
        # Handle test 1 auto-join
        if test == 1 and self.rooms and not self.room_id:
            first_room = next(iter(self.rooms.keys()))
            log(f"[Client] Auto-joining room {first_room}")
            self.send_join_room(first_room)
        
        # only attempt when in room and has a local_id
        if self.room_id and self.local_id:
            cell = random.randint(0, TOTAL_CELLS - 1)
            self.request_cell(cell)    

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--metrics_id", type=int, help="Metrics ID for client", required=False)
    parser.add_argument("--test", type=int, help="Choose test sequence", required=False)
    parser.add_argument("--duration", type=int, help="Test duration in seconds", required=False)
    parser.add_argument("--log", type=str, help="Log file path", required=False)
    args = parser.parse_args()
    
    islogging = False
    if args.log:
        logging.basicConfig(filename=args.log, level=logging.INFO, format="%(asctime)s %(message)s")
        islogging = True
    
    client = ESPClientProtocol(("127.0.0.1", 9999), metrices_id=args.metrics_id, islogging=islogging)
    if args.test is not None:
        if args.test == 0:
            client.send_create_room(f"Room_{random.randint(100,999)}")
        elif args.test == 1:
            client.send_list_rooms()

    client.run(duration=args.duration, test=args.test)
    
