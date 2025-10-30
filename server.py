"""
Grid Clash authoritative UDP server.

Features:
- UDP transport with binary header per spec.
- Broadcasts redundant snapshots (last K) at BROADCAST_FREQ_HZ.
- Accepts ACQUIRE_REQUEST events from clients and resolves conflicts.
- Sends EVENT messages for acquisition results and retransmits them until clients ACK the event (selective reliability).
- Maintains grid state and logs simple metrics.
Usage: python server.py
"""

import asyncio
import struct
import time
import zlib
import json
from collections import deque, defaultdict, namedtuple
from dataclasses import dataclass, field
from typing import Dict, Tuple, Deque, List

# -----------------------------
# Protocol constants / header
# -----------------------------
PROTOCOL_ID = b'GSS1'  # 4 bytes ASCII
VERSION = 1
MSG_SNAPSHOT = 1
MSG_EVENT = 2            # server -> clients: event results (e.g., CELL_ACQUIRED)
MSG_ACK = 3              # client -> server: ack snapshot or ack event (payload defines)
MSG_REGISTER = 4         # client -> server: register with player_id
MSG_ACQUIRE_REQ = 5      # client -> server: acquire request (treated as EVENT inbound)

# Header: protocol_id (4s -> 4-byte string), version (B -> unsigned char 1 byte), msg_type (B -> unsigned char 1 byte), snapshot_id (I -> unsigned int 4 bytes), seq_num (I -> unsigned int 4 bytes), server_timestamp (Q -> unsigned long long 8 bytes), payload_len (H -> unsigned short 2 bytes), checksum (I -> unsigned int 4 bytes)
HEADER_FMT = "!4s B B I I Q H I" # !-> Network (big-endian)
HEADER_SIZE = struct.calcsize(HEADER_FMT) # should be 28 bytes
MAX_PACKET = 1200 # bytes
SNAPSHOT_PAYLOAD_LIMIT = MAX_PACKET - HEADER_SIZE

# Config
BROADCAST_FREQ_HZ = 20        # 20 snapshots/sec
REDUNDANT_K = 3               # include last K snapshots per packet
GRID_N = 20                   # 20x20 grid
TOTAL_CELLS = GRID_N * GRID_N
MAX_CLIENTS = 16

# Event / cell formats
# ACQUIRE_REQUEST (client->server): event_id (I), player_id (I), cell_idx (I), client_ts_ms (Q)
ACQUIRE_REQ_FMT = "!I I I Q"
ACQUIRE_REQ_SIZE = struct.calcsize(ACQUIRE_REQ_FMT)

# EVENT result (server->clients): event_id (I), cell_idx (I), owner_player_id (I), server_ts_ms (Q)
EVENT_RESULT_FMT = "!I I I Q"
EVENT_RESULT_SIZE = struct.calcsize(EVENT_RESULT_FMT)

# snapshot payload encoding:
# We'll send delta snapshots: list of changed cells since last full snapshot.
# For each snapshot block in payload: meta_len(4) + meta_json + num_changes(2) + repeated (cell_idx (I), owner (I))
CELL_CHANGE_FMT = "!I I"
CELL_CHANGE_SIZE = struct.calcsize(CELL_CHANGE_FMT)

@dataclass
class ClientInfo:
    addr: Tuple[str, int]
    player_id: int = 0
    last_ack_snapshot: int = 0
    last_ack_event: int = 0      # up to which event id they acked (or highest acked)
    last_seq: int = 0

@dataclass
class Snapshot:
    snapshot_id: int
    seq_num: int
    timestamp_ms: int
    # represent changes as list of tuples (cell_idx, owner_player_id)
    changes: List[Tuple[int, int]]

@dataclass
class PendingEvent:
    event_id: int
    cell_idx: int
    owner_player_id: int
    server_ts_ms: int
    # track which clients have acked
    acked_by: set = field(default_factory=set)
    last_sent_ms: float = 0.0
    send_count: int = 0

class GridServerProtocol:
    def __init__(self, host='0.0.0.0', port=9999):
        self.host = host
        self.port = port
        self.transport = None
        self.loop = asyncio.get_event_loop()

        self.clients: Dict[Tuple[str,int], ClientInfo] = {}
        self.player_addrs: Dict[int, Tuple[str,int]] = {}

        self.grid = [0] * TOTAL_CELLS  # 0 = unclaimed, else player_id
        self.next_snapshot_id = 1
        self.next_seq = 1
        self.snapshots: Deque[Snapshot] = deque(maxlen=1000)

        self.next_event_id = 1
        self.pending_events: Dict[int, PendingEvent] = {}  # event_id -> PendingEvent

        # metrics
        self.packets_sent = 0
        self.packets_recv = 0
        self.start_time = time.time()

    def start(self):
        print(f"Starting GridServer on {self.host}:{self.port}")
        listen = self.loop.create_datagram_endpoint(lambda: self, local_addr=(self.host, self.port))
        self.transport, _ = self.loop.run_until_complete(listen)
        self.loop.create_task(self._broadcast_loop())
        self.loop.create_task(self._retransmit_loop())
        try:
            self.loop.run_forever()
        except KeyboardInterrupt:
            print("Shutting down")
        finally:
            self.transport.close()

    # DatagramProtocol callbacks
    def connection_made(self, transport):
        self.transport = transport
        print("UDP socket ready")

    def datagram_received(self, data, addr):
        self.packets_recv += 1
        if len(data) < HEADER_SIZE:
            print("Undersized packet from", addr); return
        header = struct.unpack(HEADER_FMT, data[:HEADER_SIZE])
        protocol_id, version, msg_type, snapshot_id, seq_num, ts, payload_len, checksum = header
        if protocol_id != PROTOCOL_ID:
            # ignore non-matching protocol
            return
        payload = data[HEADER_SIZE:HEADER_SIZE+payload_len]
        computed = zlib.crc32(data[:HEADER_SIZE-4] + payload) & 0xffffffff
        if computed != checksum:
            print("Checksum mismatch from", addr); return

        if msg_type == MSG_REGISTER:
            self._handle_register(payload, addr)
        elif msg_type == MSG_ACQUIRE_REQ:
            self._handle_acquire_request(payload, addr)
        elif msg_type == MSG_ACK:
            self._handle_ack(payload, addr)
        else:
            print("Unknown msg_type", msg_type, "from", addr)

    def _handle_register(self, payload: bytes, addr):
        # payload is JSON {"player_id": int}
        try:
            info = json.loads(payload.decode())
            pid = int(info.get("player_id"))
        except Exception as e:
            print("Bad register payload", e); return
        if addr not in self.clients:
            self.clients[addr] = ClientInfo(addr=addr, player_id=pid)
            self.player_addrs[pid] = addr
            print(f"Registered player {pid} from {addr}. total clients={len(self.clients)}")
        else:
            self.clients[addr].player_id = pid
            self.player_addrs[pid] = addr
            print(f"Re-registered player {pid} from {addr}")

    def _handle_acquire_request(self, payload: bytes, addr):
        # parse ACQUIRE_REQ_FMT
        if len(payload) < ACQUIRE_REQ_SIZE:
            return
        self.packets_recv += 0
        event_id, player_id, cell_idx, client_ts_ms = struct.unpack(ACQUIRE_REQ_FMT, payload[:ACQUIRE_REQ_SIZE])
        # defensive: clamp
        if not (0 <= cell_idx < TOTAL_CELLS):
            return
        # resolve: if unclaimed -> grant, else ignore
        # conflict resolution: first-come wins at server; client timestamp used as tie-breaker only if simultaneous arrival (rare)
        current_owner = self.grid[cell_idx]
        granted = False
        if current_owner == 0:
            # grant it immediately to this player
            self.grid[cell_idx] = player_id
            granted = True
        else:
            # already owned, no change
            granted = False

        # record an event result (server will broadcast)
        eid = self.next_event_id
        self.next_event_id += 1
        srv_ts = int(time.time() * 1000)
        ev = PendingEvent(event_id=eid, cell_idx=cell_idx, owner_player_id=(player_id if granted else current_owner),
                          server_ts_ms=srv_ts)
        self.pending_events[eid] = ev

        # Also create a snapshot including this change to include in next broadcast (we append snapshot now)
        # For snapshot change we send only this cell change
        snap = Snapshot(snapshot_id=self.next_snapshot_id, seq_num=self.next_seq, timestamp_ms=int(time.time()*1000),
                        changes=[(cell_idx, self.grid[cell_idx])])
        self.snapshots.appendleft(snap)
        self.next_snapshot_id += 1
        self.next_seq += 1

        # send an immediate EVENT packet to all clients so critical event propagates faster (server->clients)
        self._broadcast_event(ev)

    def _handle_ack(self, payload: bytes, addr):
        # ACK payload: type byte (1 = snapshot ack, 2 = event ack) followed by ack id (I)
        if len(payload) < 5:
            return
        ack_type = payload[0]
        (ack_id,) = struct.unpack_from("!I", payload, 1)
        client = self.clients.get(addr)
        if not client:
            return
        if ack_type == 1:
            # snapshot ack
            client.last_ack_snapshot = max(client.last_ack_snapshot, ack_id)
        elif ack_type == 2:
            # event ack
            # mark pending event acked by this client
            pe = self.pending_events.get(ack_id)
            if pe:
                pe.acked_by.add(client.player_id)
            client.last_ack_event = max(client.last_ack_event, ack_id)

    async def _broadcast_loop(self):
        period = 1.0 / BROADCAST_FREQ_HZ
        while True:
            start = self.loop.time()
            # create periodic snapshot summarizing recent changes if any
            # For continuous operation, we will send an empty snapshot (no changes) occasionally so clients know time advanced.
            snap_changes = []  # for simplicity, send aggregated changes since last snapshot creation
            # here we don't compute diffs; we will send the most recent snapshots kept in self.snapshots
            self._broadcast_snapshots()
            elapsed = self.loop.time() - start
            await asyncio.sleep(max(0, period - elapsed))

    def _pack_and_send(self, msg_type: int, payload: bytes, snapshot_id=0, seq_num=0):
        header = struct.pack(HEADER_FMT,
                             PROTOCOL_ID,
                             VERSION,
                             msg_type,
                             snapshot_id,
                             seq_num,
                             int(time.time() * 1000),
                             len(payload),
                             0)
        checksum = zlib.crc32(header[:HEADER_SIZE-4] + payload) & 0xffffffff
        header = struct.pack(HEADER_FMT,
                             PROTOCOL_ID,
                             VERSION,
                             msg_type,
                             snapshot_id,
                             seq_num,
                             int(time.time() * 1000),
                             len(payload),
                             checksum)
        packet = header + payload
        # send to all clients
        for addr in list(self.clients.keys()):
            try:
                self.transport.sendto(packet, addr)
                self.packets_sent += 1
            except Exception as e:
                print("Send error to", addr, e)

    def _broadcast_snapshots(self):
        # include up to REDUNDANT_K latest snapshots in payload (oldest-first in payload)
        latest_snaps = list(self.snapshots)[:REDUNDANT_K]
        # build payload: for each snapshot -> 4bytes meta_len + meta_json + 2bytes num_changes + changes...
        total_payload = bytearray()
        for snap in reversed(latest_snaps):  # oldest first
            meta = json.dumps({"snapshot_id": snap.snapshot_id, "seq_num": snap.seq_num, "ts": snap.timestamp_ms}).encode()
            total_payload.extend(struct.pack("!I", len(meta)))
            total_payload.extend(meta)
            # number of changes
            total_payload.extend(struct.pack("!H", len(snap.changes)))
            for cell_idx, owner in snap.changes:
                total_payload.extend(struct.pack(CELL_CHANGE_FMT, cell_idx, owner))

        if len(total_payload) > SNAPSHOT_PAYLOAD_LIMIT:
            # If too big, trim older snapshots until fit
            while len(total_payload) > SNAPSHOT_PAYLOAD_LIMIT and len(latest_snaps) > 1:
                latest_snaps.pop(0)
                total_payload = bytearray()
                for snap in reversed(latest_snaps):
                    meta = json.dumps({"snapshot_id": snap.snapshot_id, "seq_num": snap.seq_num, "ts": snap.timestamp_ms}).encode()
                    total_payload.extend(struct.pack("!I", len(meta)))
                    total_payload.extend(meta)
                    total_payload.extend(struct.pack("!H", len(snap.changes)))
                    for cell_idx, owner in snap.changes:
                        total_payload.extend(struct.pack(CELL_CHANGE_FMT, cell_idx, owner))
            if len(total_payload) > SNAPSHOT_PAYLOAD_LIMIT:
                # still too big; truncate change lists per snapshot (not fully implemented)
                total_payload = total_payload[:SNAPSHOT_PAYLOAD_LIMIT]

        payload_bytes = bytes(total_payload)
        # snapshot_id in header: most recent snapshot id if available
        head_snap_id = latest_snaps[-1].snapshot_id if latest_snaps else 0
        self._pack_and_send(MSG_SNAPSHOT, payload_bytes, snapshot_id=head_snap_id, seq_num=self.next_seq)

    def _broadcast_event(self, pending_event: PendingEvent):
        # EVENT payload: EVENT_RESULT_FMT (event_id, cell_idx, owner_id, server_ts)
        payload = struct.pack(EVENT_RESULT_FMT, pending_event.event_id, pending_event.cell_idx,
                              pending_event.owner_player_id, pending_event.server_ts_ms)
        # send to all clients
        self._pack_and_send(MSG_EVENT, payload, snapshot_id=self.next_snapshot_id - 1, seq_num=self.next_seq)
        pending_event.last_sent_ms = time.time()
        pending_event.send_count += 1

    async def _retransmit_loop(self):
        # retransmit pending events every RETRANSMIT_INTERVAL for clients that haven't acked them
        RETRANSMIT_INTERVAL = 0.5  # seconds
        while True:
            now = time.time()
            to_delete = []
            for eid, pe in list(self.pending_events.items()):
                # if all connected clients acked it, remove it
                all_acked = True
                for c in self.clients.values():
                    if c.player_id not in pe.acked_by:
                        all_acked = False; break
                if all_acked:
                    to_delete.append(eid)
                    continue
                # else, retransmit if last sent older than interval
                if now - pe.last_sent_ms >= RETRANSMIT_INTERVAL:
                    self._broadcast_event(pe)
            for eid in to_delete:
                del self.pending_events[eid]
            await asyncio.sleep(RETRANSMIT_INTERVAL)
            
    def pause_writing(self):
        pass

    def resume_writing(self):
        pass
    
    def error_received(self, exc):
        print("Error received:", exc)

if __name__ == "__main__":
    gs = GridServerProtocol(host="127.0.0.1", port=9999)
    gs.start()
