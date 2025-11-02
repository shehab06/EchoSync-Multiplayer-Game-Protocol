import asyncio, struct, time, random, zlib
from collections import defaultdict

# === Copy shared protocol definitions ===
# (You can import these from your shared module instead)
from ESP_config import * 

# === Client ===
class ESPClientProtocol:
    def __init__(self, loop, server_addr):
        self.loop = loop
        self.server_addr = server_addr
        self.transport = None

        self.player_id = None
        self.seq = 1
        self.pkt_id = 1
        self.room_id = None
        self.local_id = None
        self.grid = [0] * TOTAL_CELLS

    def connection_made(self, transport):
        self.transport = transport
        print(f"[Client] Connected to {self.server_addr}")
        self.send_init()

    def datagram_received(self, data, addr):
        pkt = parse_packet(data)
        if pkt is None:
            return

        msg_type = pkt['msg_type']
        payload = pkt['payload']

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
            self.handle_event(pkt)
        else:
            print(f"[Client] Unknown msg type {msg_type}")

    def connection_lost(self, exc):
        self.disconnect()
        print("Connection lost:", exc)

    def pause_writing(self):
        pass

    def resume_writing(self):
        pass
    
    def send(self, msg_type, payload=b''):
        pkts, seq_num = build_packet(
            msg_type, self.pkt_id, self.seq, payload
        )
        for p in pkts:
            self.transport.sendto(p, self.server_addr)
        self.seq = seq_num
        self.pkt_id += 1

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

    def send_list_rooms(self):
        print("[Client] Requesting room list")
        self.send(MESSAGE_TYPES['LIST_ROOMS'])

    def send_event(self, event_type, room_id, player_local_id, cell_idx):
        payload = build_event_payload(event_type, room_id, player_local_id, cell_idx)
        self.send(MESSAGE_TYPES['EVENT'], payload)

    def send_snapshot_ack(self, seq_num):
        payload = build_snapshot_ack_payload(seq_num)
        self.send(MESSAGE_TYPES['SNAPSHOT_ACK'], payload)

    def disconnect(self):
        print("[Client] Disconnecting...")
        self.send(MESSAGE_TYPES['DISCONNECT'])
        self.transport.close()

    # === Handlers ===
    def handle_init_ack(self, payload):
        res = parse_init_ack_payload(payload)
        if res:
            _, self.player_id = res
            print(f"[Client] Got player_id = {self.player_id}")
            # auto create a room for test
            self.send_create_room(f"Room_{random.randint(100,999)}")

    def handle_create_ack(self, payload):
        res = parse_create_ack_payload(payload)
        if res:
            _, self.room_id = res
            print(f"[Client] Room created -> id {self.room_id}")
            # join it
            self.send_join_room(self.room_id)

    def handle_join_ack(self, payload):
        res = parse_join_ack_payload(payload)
        if res:
            _, self.local_id, players = res
            print(f"[Client] Joined room {self.room_id} as local id {self.local_id}")
            print(f"[Client] Room players: {players}")

    def handle_list_rooms_ack(self, payload):
        res = parse_list_rooms_ack_payload(payload)
        if res:
            _, rooms = res
            print(f"[Client] Available Rooms:")
            for rid, (count, name) in rooms.items():
                print(f" - {rid}: {name} ({count} players)")

    def handle_snapshot(self, pkt):
        payload = pkt['payload']
        grid = parse_snapshot_payload(payload)
        if grid:
            self.grid = grid
            self.send_snapshot_ack(pkt['seq'])
            print(f"[Client] Snapshot #{pkt['seq']} received & ACKed")

    def handle_event(self, pkt):
        print(f"[Client] Event update received")

    def connection_lost(self, exc):
        print("[Client] Connection closed")


# === Runner ===
async def run_client(host="127.0.0.1", port=9999):
    loop = asyncio.get_event_loop()
    transport, protocol = await loop.create_datagram_endpoint(
        lambda: ESPClientProtocol(loop, (host, port)),
        remote_addr=(host, port)
    )

    try:
        while True:
            await asyncio.sleep(3)
            if protocol.room_id and protocol.local_id:
                cell = random.randint(0, TOTAL_CELLS-1)
                protocol.send_event(EVENT_TYPES['CELL_ACQUISITION'], protocol.room_id, protocol.local_id, cell)
                print(f"[Client] Sent event: acquire cell {cell}")
    except KeyboardInterrupt:
        protocol.disconnect()
        transport.close()


if __name__ == "__main__":
    asyncio.run(run_client())
    
