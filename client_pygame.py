"""
Grid Clash client with pygame UI.

- Connects to server via UDP, registers with player_id (arg).
- Renders GRID_N x GRID_N cells; left-click sends ACQUIRE_REQUEST (critical).
- Receives SNAPSHOT and EVENT messages; discards outdated snapshots.
- Sends ACKs for snapshots and for event results it receives (so server can stop retransmitting).
Run: python client_pygame.py <player_id>
"""

import asyncio
import struct
import time
import zlib
import json
import sys
import threading
import pygame
from dotenv import load_dotenv
import os
load_dotenv()

# Server address
SERVER_HOST = os.getenv("SERVER_HOST", "127.0.0.1")
SERVER_PORT = int(os.getenv("SERVER_PORT", 9999))

# Protocol constants (same as server)
PROTOCOL_ID = b'GSS1'
VERSION = 1
MSG_SNAPSHOT = 1
MSG_EVENT = 2
MSG_ACK = 3
MSG_REGISTER = 4
MSG_ACQUIRE_REQ = 5

HEADER_FMT = "!4s B B I I Q H I"
HEADER_SIZE = struct.calcsize(HEADER_FMT)
PLAYER_ENTRY_FMT = ""  # not used here
MAX_PACKET = 1200

# ACQUIRE_REQ_FMT: event_id (I), player_id (I), cell_idx (I), client_ts_ms (Q)
ACQUIRE_REQ_FMT = "!I I I Q"
ACQUIRE_REQ_SIZE = struct.calcsize(ACQUIRE_REQ_FMT)

# EVENT_RESULT_FMT (server->client): event_id (I), cell_idx (I), owner_player_id (I), server_ts_ms (Q)
EVENT_RESULT_FMT = "!I I I Q"
EVENT_RESULT_SIZE = struct.calcsize(EVENT_RESULT_FMT)

CELL_CHANGE_FMT = "!I I"
CELL_CHANGE_SIZE = struct.calcsize(CELL_CHANGE_FMT)

# Game config (must match server)
GRID_N = 20
TOTAL_CELLS = GRID_N * GRID_N
CELL_SIZE = 24
MARGIN = 2
WINDOW_W = GRID_N * (CELL_SIZE + MARGIN) + 200
WINDOW_H = GRID_N * (CELL_SIZE + MARGIN) + 40

# Colors
WHITE = (255,255,255); BLACK=(0,0,0); GREY=(200,200,200)
PLAYER_COLORS = [
    (160, 160, 160), # id 0
    (255, 100, 100), # id 1
    (100, 255, 100), # id 2
    (100, 100, 255), # id 3
    (255, 255, 100), # id 4
    (255, 100, 255),
    (100,255,255),
]

class UDPClient:
    def __init__(self, server_host, server_port, player_id, loop):
        self.server_addr = (server_host, server_port)
        self.player_id = player_id
        self.loop = loop
        self.transport = None
        self.last_applied_snapshot = 0
        self.grid = [0] * TOTAL_CELLS
        self.event_acknowledged = set()
        self.pending_local_event_id = 1  # for requests; client generates an event_id for request
        self.recv_count = 0

    async def start(self):
        # create endpoint
        self.transport, _ = await self.loop.create_datagram_endpoint(lambda: self, local_addr=("0.0.0.0", 0))
        # register
        payload = json.dumps({"player_id": self.player_id}).encode()
        header = struct.pack(HEADER_FMT, PROTOCOL_ID, VERSION, MSG_REGISTER, 0, 0, int(time.time()*1000), len(payload), 0)
        checksum = zlib.crc32(header[:HEADER_SIZE-4] + payload) & 0xffffffff
        header = struct.pack(HEADER_FMT, PROTOCOL_ID, VERSION, MSG_REGISTER, 0, 0, int(time.time()*1000), len(payload), checksum)
        self.transport.sendto(header + payload, self.server_addr)
        print("Registered to server as player", self.player_id)

    def connection_made(self, transport):
        pass

    def send_acquire_request(self, cell_idx):
        # create local event id (monotonic per client)
        eid = self.pending_local_event_id
        self.pending_local_event_id += 1
        client_ts = int(time.time() * 1000)
        payload = struct.pack(ACQUIRE_REQ_FMT, eid, self.player_id, cell_idx, client_ts)
        header = struct.pack(HEADER_FMT, PROTOCOL_ID, VERSION, MSG_ACQUIRE_REQ, 0, 0, int(time.time()*1000), len(payload), 0)
        checksum = zlib.crc32(header[:HEADER_SIZE-4] + payload) & 0xffffffff
        header = struct.pack(HEADER_FMT, PROTOCOL_ID, VERSION, MSG_ACQUIRE_REQ, 0, 0, int(time.time()*1000), len(payload), checksum)
        self.transport.sendto(header + payload, self.server_addr)
        # optimistic local pending UI could be set; we rely on server authoritative update

    def datagram_received(self, data, addr):
        if len(data) < HEADER_SIZE: return
        header = struct.unpack(HEADER_FMT, data[:HEADER_SIZE])
        protocol_id, version, msg_type, snapshot_id, seq_num, ts, payload_len, checksum = header
        if protocol_id != PROTOCOL_ID: return
        payload = data[HEADER_SIZE:HEADER_SIZE+payload_len]
        computed = zlib.crc32(data[:HEADER_SIZE-4] + payload) & 0xffffffff
        if computed != checksum:
            print("Checksum mismatch, dropping"); return
        if msg_type == MSG_SNAPSHOT:
            self._handle_snapshot(payload)
        elif msg_type == MSG_EVENT:
            self._handle_event(payload)
        else:
            pass

    def _handle_snapshot(self, payload: bytes):
        # parse concatenated snapshot blocks: for each: meta_len(4) + meta_json + num_changes(2) + changes...
        idx = 0
        newest_snapshot_id = 0
        parsed = False
        while idx + 4 <= len(payload):
            (meta_len,) = struct.unpack_from("!I", payload, idx); idx += 4
            if idx + meta_len > len(payload): break
            meta = payload[idx:idx+meta_len]; idx += meta_len
            try:
                meta_obj = json.loads(meta.decode())
                snap_id = int(meta_obj['snapshot_id'])
            except:
                break
            (num_changes,) = struct.unpack_from("!H", payload, idx); idx += 2
            for _ in range(num_changes):
                if idx + CELL_CHANGE_SIZE > len(payload): break
                cell_idx, owner = struct.unpack_from(CELL_CHANGE_FMT, payload, idx); idx += CELL_CHANGE_SIZE
                # Only apply if snapshot is newer than last applied
                if snap_id > self.last_applied_snapshot:
                    self.grid[cell_idx] = owner
            parsed = True
            if snap_id > newest_snapshot_id:
                newest_snapshot_id = snap_id
        if parsed and newest_snapshot_id > 0 and newest_snapshot_id > self.last_applied_snapshot:
            self.last_applied_snapshot = newest_snapshot_id
            # send snapshot ACK (ack type 1)
            ack_payload = bytes([1]) + struct.pack("!I", newest_snapshot_id)
            header = struct.pack(HEADER_FMT, PROTOCOL_ID, VERSION, MSG_ACK, newest_snapshot_id, 0, int(time.time()*1000), len(ack_payload), 0)
            checksum = zlib.crc32(header[:HEADER_SIZE-4] + ack_payload) & 0xffffffff
            header = struct.pack(HEADER_FMT, PROTOCOL_ID, VERSION, MSG_ACK, newest_snapshot_id, 0, int(time.time()*1000), len(ack_payload), checksum)
            self.transport.sendto(header + ack_payload, self.server_addr)

    def _handle_event(self, payload: bytes):
        if len(payload) < EVENT_RESULT_SIZE: return
        event_id, cell_idx, owner_pid, server_ts = struct.unpack(EVENT_RESULT_FMT, payload[:EVENT_RESULT_SIZE])
        # apply immediately (authoritative)
        if 0 <= cell_idx < TOTAL_CELLS:
            self.grid[cell_idx] = owner_pid
        # send event ACK (type 2)
        ack_payload = bytes([2]) + struct.pack("!I", event_id)
        header = struct.pack(HEADER_FMT, PROTOCOL_ID, VERSION, MSG_ACK, 0, 0, int(time.time()*1000), len(ack_payload), 0)
        checksum = zlib.crc32(header[:HEADER_SIZE-4] + ack_payload) & 0xffffffff
        header = struct.pack(HEADER_FMT, PROTOCOL_ID, VERSION, MSG_ACK, 0, 0, int(time.time()*1000), len(ack_payload), checksum)
        self.transport.sendto(header + ack_payload, self.server_addr)
        self.event_acknowledged.add(event_id)

# ---------------------------
# Pygame UI
# ---------------------------
class GridClientApp:
    def __init__(self, server_host, server_port, player_id):
        pygame.init()
        self.screen = pygame.display.set_mode((WINDOW_W, WINDOW_H))
        pygame.display.set_caption(f"Grid Clash - Player {player_id}")
        self.clock = pygame.time.Clock()
        self.loop = asyncio.new_event_loop()
        self.udp_client = UDPClient(server_host, server_port, player_id, self.loop)
        self.player_id = player_id

        # start asyncio UDP client in background thread
        t = threading.Thread(target=self._start_async_loop, daemon=True)
        t.start()
        # wait until transport is ready (simple short sleep)
        time.sleep(0.1)

    def _start_async_loop(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_until_complete(self.udp_client.start())
        self.loop.run_forever()

    def run(self):
        font = pygame.font.SysFont(None, 20)
        running = True
        while running:
            dt = self.clock.tick(30)  # cap 30 fps
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    running = False
                elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                    mx, my = pygame.mouse.get_pos()
                    # map to cell
                    cx = mx // (CELL_SIZE + MARGIN)
                    cy = my // (CELL_SIZE + MARGIN)
                    if 0 <= cx < GRID_N and 0 <= cy < GRID_N:
                        cell_idx = cy * GRID_N + cx
                        # send acquire request
                        self.udp_client.send_acquire_request(cell_idx)

            # draw background
            self.screen.fill(GREY)
            # draw grid
            for row in range(GRID_N):
                for col in range(GRID_N):
                    idx = row * GRID_N + col
                    owner = self.udp_client.grid[idx]
                    color = PLAYER_COLORS[owner % len(PLAYER_COLORS)] if owner >= 0 else PLAYER_COLORS[0]
                    x = col * (CELL_SIZE + MARGIN) + MARGIN
                    y = row * (CELL_SIZE + MARGIN) + MARGIN
                    pygame.draw.rect(self.screen, color, (x, y, CELL_SIZE, CELL_SIZE))
                    # border
                    pygame.draw.rect(self.screen, BLACK, (x, y, CELL_SIZE, CELL_SIZE), 1)

            # draw scoreboard
            counts = {}
            for v in self.udp_client.grid:
                counts[v] = counts.get(v, 0) + 1
            score_texts = []
            for pid in sorted(counts.keys()):
                if pid == 0: continue
                score_texts.append(f"P{pid}:{counts[pid]}")
            score_surface = font.render("  ".join(score_texts), True, BLACK)
            self.screen.blit(score_surface, (GRID_N*(CELL_SIZE+MARGIN)+10, 10))

            # draw status
            status = f"Player {self.player_id} snapshots:{self.udp_client.last_applied_snapshot} events_ack:{len(self.udp_client.event_acknowledged)}"
            status_surface = font.render(status, True, BLACK)
            self.screen.blit(status_surface, (GRID_N*(CELL_SIZE+MARGIN)+10, 40))

            pygame.display.flip()

        pygame.quit()
        # stop asyncio loop
        try:
            self.loop.call_soon_threadsafe(self.loop.stop)
        except:
            pass

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python client_pygame.py <player_id>")
        sys.exit(1)
    pid = int(sys.argv[1])
    app = GridClientApp(server_host=SERVER_HOST, server_port=SERVER_PORT, player_id=pid)
    app.run()
