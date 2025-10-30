# EchoSync Protocol (ESP)

The EchoSync Protocol (ESP) is a custom UDP-based protocol designed for low-latency synchronization of player positions and game events in the "Grid Clash" multiplayer game.

---
## Run Locally

### 1. Clone the Repository

```bash
git clone https://github.com/okhadragy/EchoSync-Protocol
cd "EchoSync-Protocol"
```

### 2. Install Dependencies

```bash
pip install -r requirements.txt
```

### 3. Set Up Environment Variables

Create a .env file and add your server host ip and port:

```bash
SERVER_HOST=127.0.0.1
SERVER_PORT=9999
```

### 4. Run the server

```bash
python server.py
```

### 5. Run the client game

```bash
python client_pygame.py <player_id>
```
