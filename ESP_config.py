import struct, time, zlib, csv, os, psutil, random
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, Tuple, List


# ====== Game Config ======
GRID_N = 20                   # 20x20 grid
TOTAL_CELLS = GRID_N * GRID_N

# ====== ESP Protocol Definitions ======
"""  Header Format """
# ESP Header: protocol_id (4s -> 4-byte string), version (B -> unsigned char 1 byte), msg_type (B -> unsigned char 1 byte), pkt_id (snapshot, event, etc.) (I -> unsigned int 4 bytes), seq_num (I -> unsigned int 4 bytes), timestamp (server, client) (Q -> unsigned long long 8 bytes), payload_len (H -> unsigned short 2 bytes), checksum (I -> unsigned int 4 bytes)
HEADER_FMT = "!4s B B I I Q H I" # !-> Network (big-endian)
HEADER_SIZE = struct.calcsize(HEADER_FMT) # should be 28 bytes

"""  Payload Formats """
# INIT Payload: empty

# INIT_ACK Payload: pkt_id (I), player_id (I)
INIT_ACK_FMT = "!I I"
INIT_ACK_SIZE = struct.calcsize(INIT_ACK_FMT)

# CREATE_ROOM Payload: room_name (variable length string, UTF-8)

# CREATE_ACK Payload: pkt_id (I), room_id (B)
CREATE_ACK_FMT = "!I B"
CREATE_ACK_SIZE = struct.calcsize(CREATE_ACK_FMT)

# JOIN_ROOM Payload: room_id (B)
JOIN_ROOM_FMT = "!B"
JOIN_ROOM_SIZE = struct.calcsize(JOIN_ROOM_FMT)

# JOIN_ACK Payload: pkt_id (I), local_id (B), players_count (B), followed by room players (player_id (I), player_local_id (B), player_color (RED (B), GREEN (B), BLUE (B))*
JOIN_ACK_HEADER_FMT = "!I B B"
JOIN_ACK_HEADER_SIZE = struct.calcsize(JOIN_ACK_HEADER_FMT)
JOIN_ACK_ENTRY_FMT = "!I B B B B"
JOIN_ACK_ENTRY_SIZE = struct.calcsize(JOIN_ACK_ENTRY_FMT)

# LIST_ROOMS Payload: empty

# LIST_ROOMS_ACK Payload: pkt_id (I), room_count (B), followed by room entries (room_id (B), player_count (B), room_name_length (B), room_name (UTF-8 string))*
LIST_ROOMS_ACK_HEADER_FMT = "!I B"
LIST_ROOMS_ACK_HEADER_SIZE = struct.calcsize(LIST_ROOMS_ACK_HEADER_FMT)
LIST_ROOMS_ACK_ENTRY_FMT = "!B B B"
LIST_ROOMS_ACK_ENTRY_SIZE = struct.calcsize(LIST_ROOMS_ACK_ENTRY_FMT)

# Event Payload: event_type (B), room_id (B), player_local_id (B), cell_idx (H)
EVENT_FMT = "!B B B H"
EVENT_SIZE = struct.calcsize(EVENT_FMT)

# Snapshot Payload: grid state (TOTAL_CELLS bytes, each byte = owner player_id or 0)
SNAPSHOT_FMT = "!%dB" % TOTAL_CELLS
SNAPSHOT_SIZE = struct.calcsize(SNAPSHOT_FMT)

# Snapshot ACK Payload: seq_num (I)
SNAPSHOT_ACK_FMT = "!I"
SNAPSHOT_ACK_SIZE = struct.calcsize(SNAPSHOT_ACK_FMT)

"""  Protocol Constants """
PROTOCOL_ID = b'ESP1'
VERSION = 1
MESSAGE_TYPES = {
    'INIT': 0,
    'INIT_ACK': 1,
    'CREATE_ROOM': 2,
    'CREATE_ACK': 3,
    'JOIN_ROOM': 4,
    'JOIN_ACK': 5,
    'LEAVE_ROOM': 6,
    'LEAVE_ACK': 7,
    'LIST_ROOMS': 8,
    'LIST_ROOMS_ACK': 9,
    'EVENT': 10,
    'SNAPSHOT': 11,
    'SNAPSHOT_ACK': 12,
    'DISCONNECT': 13,
}

EVENT_TYPES = {
    'CELL_ACQUISITION': 0,
}

MAX_PACKET = 1200 # bytes
SNAPSHOT_PAYLOAD_LIMIT = MAX_PACKET - HEADER_SIZE # bytes
BROADCAST_FREQ_HZ = 20        # 20 snapshots/sec
SNAPSHOT_INTERVAL = 1.0 / BROADCAST_FREQ_HZ
RETRANS_TIMEOUT = 0.1        # seconds
REDUNDANT_K = 3               # include last K snapshots per packet
MAX_ROOM_PLAYERS = 16

""" Data Structures """
@dataclass
class RoomPlayer:
    global_id: int
    color: Tuple[int, int, int]  # RGB

@dataclass
class Room:
    room_id: int
    name: str
    players: Dict[int, RoomPlayer] = field(default_factory=dict)
    grid: list[int] = field(default_factory=lambda: [0]*TOTAL_CELLS)  # 0 = free, else player_local_id

@dataclass
class PlayerRoomInfo:
    address: Tuple[str, int]
    room_id: int = 0 # 0 means not in any room yet
    player_local_id: int = 0 # 0 means not assigned yet

@dataclass
class Fragment:
    frags: Dict[int, bytes] = field(default_factory=dict)  # seq_num -> bytes
    received_bytes: int = 0
    expected_bytes: int = 0  
    timestamp: float = field(default_factory=time.time)

class FragmentManager:
    def __init__(self, timeout=5.0):
        self.fragments: Dict[Tuple[int, int], Fragment] = {}  # (client_id, msg_id) -> Fragment
        self.timeout = timeout

    def add_fragment(self, client_id, msg_id, seq, payload_len, payload):
        key = (client_id, msg_id)
        if key not in self.fragments:
            self.fragments[key] = Fragment(expected_bytes=payload_len)

        frag = self.fragments[key]

        if seq in frag.frags:
            return None

        frag.frags[seq] = payload
        frag.received_bytes += len(payload)
        frag.timestamp = time.time()

        if frag.received_bytes >= frag.expected_bytes:
            seq_keys = sorted(frag.frags)
            if not all(seq_keys[i] + 1 == seq_keys[i + 1] for i in  range(len(seq_keys) - 1)):
                return None
            full_payload = b''.join(frag.frags[i] for i in seq_keys)
            del self.fragments[key]
            return full_payload
        
        return None

    def cleanup(self):
        now = time.time()
        expired = [key for key, frag in self.fragments.items()
                   if now - frag.timestamp > self.timeout]
        for key in expired:
            del self.fragments[key]

class MetricsLogger:
    def __init__(self, filename="metrics.csv"):
        self.filename = filename
        self.last_recv_times = defaultdict(list)  # player_id -> [recv_times]
        self.fieldnames = [
            "client_id", "snapshot_id", "seq_num",
            "server_timestamp_ms", "recv_time_ms",
            "latency_ms", "jitter_ms",
            "perceived_position_error", "cpu_percent",
            "bandwidth_per_client_kbps"
        ]
        # Initialize CSV
        file_exists = os.path.exists(filename)
        self.file = open(filename, "a", newline="")
        self.writer = csv.DictWriter(self.file, fieldnames=self.fieldnames)
        if not file_exists:
            self.writer.writeheader()

    def log_snapshot(self, client_id, snapshot_id, seq_num, server_time, recv_time):
        latency = recv_time - server_time
        # compute jitter
        self.last_recv_times[client_id].append(recv_time)
        recv_times = self.last_recv_times[client_id]
        jitter = 0.0
        if len(recv_times) > 1:
            diffs = [recv_times[i] - recv_times[i - 1] for i in range(1, len(recv_times))]
            jitter = abs(diffs[-1] - diffs[-2]) if len(diffs) > 1 else diffs[-1]

        # placeholder for position error (can be updated later)
        perceived_position_error = random.uniform(0, 0.1)
        cpu_percent = psutil.cpu_percent(interval=None)
        bandwidth_per_client_kbps = random.uniform(20, 200)

        self.writer.writerow({
            "client_id": client_id,
            "snapshot_id": snapshot_id,
            "seq_num": seq_num,
            "server_timestamp_ms": int(server_time * 1000),
            "recv_time_ms": int(recv_time * 1000),
            "latency_ms": int(latency * 1000),
            "jitter_ms": int(jitter * 1000),
            "perceived_position_error": perceived_position_error,
            "cpu_percent": cpu_percent,
            "bandwidth_per_client_kbps": bandwidth_per_client_kbps
        })
        self.file.flush()


"""  Helper functions """
def make_header(msg_type: int, pkt_id: int, seq_num: int, payload_len: int, timestamp: int = None, checksum: int = 0):
    if timestamp is None:
        timestamp = time.time_ns()
    return struct.pack(HEADER_FMT, PROTOCOL_ID, VERSION, msg_type, pkt_id, seq_num, timestamp, payload_len, checksum)

def compute_checksum(header_bytes: bytes, payload: bytes) -> int:
    return zlib.crc32(header_bytes + payload) & 0xFFFFFFFF

def build_packet(msg_type: int, pkt_id: int, start_seq: int, payload: bytes) -> tuple[list[bytes], int]:
    packets = []
    max_data = SNAPSHOT_PAYLOAD_LIMIT

    # even if payload empty, still make one control packet
    if not payload:
        ts = int(time.time_ns())
        header = make_header(msg_type, pkt_id, start_seq, 0, timestamp=ts, checksum=0)
        checksum = compute_checksum(header, b"")
        header = struct.pack(
            HEADER_FMT,
            PROTOCOL_ID,
            VERSION,
            msg_type,
            pkt_id,
            start_seq,
            ts,
            0,
            checksum,
        )
        return [header], start_seq + 1

    total_frags = (len(payload) + max_data - 1) // max_data
    seq_num = start_seq

    for frag_idx in range(total_frags):
        start = frag_idx * max_data
        end = min(len(payload), start + max_data)
        frag_data = payload[start:end]

        ts = int(time.time_ns())
        header = make_header(msg_type, pkt_id, seq_num, len(frag_data), timestamp=ts, checksum=0)
        checksum = compute_checksum(header, frag_data)
        header = struct.pack(
            HEADER_FMT,
            PROTOCOL_ID,
            VERSION,
            msg_type,
            pkt_id,
            seq_num,
            ts,
            len(frag_data),
            checksum,
        )

        packets.append(header + frag_data)
        seq_num += 1

    return packets, seq_num


def parse_packet(data: bytes):
    # verify minimum size
    if len(data) < HEADER_SIZE:
        return None
    
    header = data[:HEADER_SIZE]
    payload = data[HEADER_SIZE:]
    protocol, version, msg_type, pkt_id, seq_num, timestamp, payload_len, checksum = struct.unpack(HEADER_FMT, header)

    # verify protocol and version
    if protocol != PROTOCOL_ID or version != VERSION:
        return None
    
    # verify checksum
    header_zero = struct.pack(HEADER_FMT, protocol, version, msg_type, pkt_id, seq_num, timestamp, payload_len, 0)
    calc = compute_checksum(header_zero, payload)
    if calc != checksum:
        return None

    # all checks passed
    return {
        'msg_type': msg_type,
        'id': pkt_id,
        'seq': seq_num,
        'timestamp': timestamp,
        'payload_len': payload_len,
        'payload': payload
    }

def build_init_ack_payload(pkt_id: int, player_id: int):
    return struct.pack(INIT_ACK_FMT, pkt_id, player_id)

def parse_init_ack_payload(payload: bytes):
    if len(payload) < INIT_ACK_SIZE:
        return None
    (pkt_id, player_id) = struct.unpack(INIT_ACK_FMT, payload[:INIT_ACK_SIZE])
    return (pkt_id, player_id)

def build_create_room_payload(room_name: str):
    name_bytes = room_name.encode('utf-8')
    return name_bytes

def parse_create_room_payload(payload: bytes):
    room_name = payload.decode('utf-8')
    return room_name

def build_create_ack_payload(pkt_id: int, room_id: int):
    return struct.pack(CREATE_ACK_FMT, pkt_id, room_id)

def parse_create_ack_payload(payload: bytes):
    if len(payload) < CREATE_ACK_SIZE:
        return None
    (pkt_id, room_id) = struct.unpack(CREATE_ACK_FMT, payload[:CREATE_ACK_SIZE])
    return (pkt_id, room_id)

def build_join_room_payload(room_id: int):
    return struct.pack(JOIN_ROOM_FMT, room_id)

def parse_join_room_payload(payload: bytes):
    if len(payload) < JOIN_ROOM_SIZE:
        return None
    (room_id,) = struct.unpack(JOIN_ROOM_FMT, payload[:JOIN_ROOM_SIZE])
    return room_id

def build_join_ack_payload(pkt_id: int, player_local_id: int, players: Dict[int, Dict[int, Tuple[int, Tuple[int,int,int]]]]):
    payload = struct.pack(JOIN_ACK_HEADER_FMT, pkt_id, player_local_id, len(players))
    for player_local_id, (player_id, color) in players.items():
        r, g, b = color
        payload += struct.pack(JOIN_ACK_ENTRY_FMT, player_id, player_local_id, r, g, b)
    return payload

def parse_join_ack_payload(payload: bytes):
    if len(payload) < JOIN_ACK_HEADER_SIZE:
        return None
    (pkt_id, player_local_id, players_count) = struct.unpack(JOIN_ACK_HEADER_FMT, payload[:JOIN_ACK_HEADER_SIZE])
    players = {}
    offset = JOIN_ACK_HEADER_SIZE
    for _ in range(players_count):
        if len(payload) < offset + JOIN_ACK_ENTRY_SIZE:
            return None
        entry = payload[offset:offset + JOIN_ACK_ENTRY_SIZE]
        player_id, player_local_id, r, g, b = struct.unpack(JOIN_ACK_ENTRY_FMT, entry)
        players[player_local_id] = (player_id, (r, g, b))
        offset += JOIN_ACK_ENTRY_SIZE
    return (pkt_id, player_local_id, players)

def build_list_rooms_ack_payload(pkt_id: int, rooms: Dict[int, Tuple[int, str]]):
    payload = struct.pack(LIST_ROOMS_ACK_HEADER_FMT, pkt_id, len(rooms))
    for room_id, (player_count, room_name) in rooms.items():
        name_bytes = room_name.encode("utf-8")
        name_len = len(name_bytes)
        payload += struct.pack(LIST_ROOMS_ACK_ENTRY_FMT, room_id, player_count, name_len)
        payload += name_bytes
    return payload

def parse_list_rooms_ack_payload(payload: bytes):
    if len(payload) < LIST_ROOMS_ACK_HEADER_SIZE:
        return None

    (pkt_id, room_count) = struct.unpack(LIST_ROOMS_ACK_HEADER_FMT, payload[:LIST_ROOMS_ACK_HEADER_SIZE])
    rooms = {}
    offset = LIST_ROOMS_ACK_HEADER_SIZE

    for _ in range(room_count):
        if len(payload) < offset + LIST_ROOMS_ACK_ENTRY_SIZE:
            return None

        room_id, player_count, name_len = struct.unpack(
            LIST_ROOMS_ACK_ENTRY_FMT, payload[offset : offset + LIST_ROOMS_ACK_ENTRY_SIZE]
        )
        offset += LIST_ROOMS_ACK_ENTRY_SIZE

        if len(payload) < offset + name_len:
            return None
        room_name = payload[offset : offset + name_len].decode("utf-8")
        offset += name_len

        rooms[room_id] = (player_count, room_name)

    return (pkt_id, rooms)

def build_event_payload(event_type: int, room_id: int, player_local_id: int, cell_idx: int):
    return struct.pack(EVENT_FMT, event_type, room_id, player_local_id, cell_idx)

def parse_event_payload(payload: bytes):
    # verify minimum size
    if len(payload) < EVENT_SIZE:
        return None
    return struct.unpack(EVENT_FMT, payload[:EVENT_SIZE])

def build_snapshot_payload(grid: List[int]):
    return struct.pack(SNAPSHOT_FMT, *grid)

def parse_snapshot_payload(payload: bytes):
    if len(payload) < SNAPSHOT_SIZE:
        return None
    return list(struct.unpack(SNAPSHOT_FMT, payload[:SNAPSHOT_SIZE]))

def build_snapshot_ack_payload(seq_num: int):
    return struct.pack(SNAPSHOT_ACK_FMT, seq_num)

def parse_snapshot_ack_payload(payload: bytes):
    if len(payload) < SNAPSHOT_ACK_SIZE:
        return None
    (seq_num,) = struct.unpack(SNAPSHOT_ACK_FMT, payload[:SNAPSHOT_ACK_SIZE])
    return seq_num
