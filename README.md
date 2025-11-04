# EchoSync Protocol (ESP)

The EchoSync Protocol (ESP) is a custom UDP-based protocol designed for low-latency synchronization of player positions and game events in the "Grid Clash" multiplayer game.

---
## Run Locally

### 1. Clone the Repository

```bash
git clone https://github.com/okhadragy/EchoSync-Multiplayer-Game-Protocol
cd "EchoSync-Multiplayer-Game-Protocol"
```

### 2. Install Dependencies

Make sure python is installed

```bash
pip install -r requirements.txt
```

### 3. Run the server

```bash
python server.py
```

### 4. Run the client

```bash
python client.py 
```

### 5. Run the Baseline Test (Multi-client Simulation)

```bash
bash run_baseline.sh
```

This script will:

- **Install the required libraries**
- **Start the server**
- **Start a client** that creates a room with a random name and joins it
- **Start 3 more clients** and join the created room (total 4 players required to start)
- **All clients begin clicking cells randomly** to simulate gameplay
- **Run the test for 60 seconds**
- **Collect metrics** in `./results/`
- **Generate performance plots** in `./plots/`

---

### 📂 Output Structure
At the end, you’ll get:
```bash
results/
  ├── metrics.csv
  ├── summary.csv
plots/
  ├── latency_cdf.png
  ├── snapshots_per_sec.png
  ├── latency_timeseries.png
  ├── jitter_timeseries.png
  ├── cpu_timeseries.png
  ├── bandwidth_timeseries.png
  └── latency_histogram.png
```