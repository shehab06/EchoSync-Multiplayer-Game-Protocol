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

### 5. Run the ALL Test Cases (Multi-client Simulation)

```bash
bash run_all_tests.sh
```

This script will:

- **Install the required libraries**
- **Run 4 test scenarios: baseline, loss2, loss5, delay100**
  - **Create test folders in full_run folder**
  - **Run test command**
  - **Start the server**
  - **Start a client** that creates a room with a random name and joins it
  - **Start 3 more clients** and join the created room (total 4 players required to start)
  - **All clients begin clicking cells randomly** to simulate gameplay
  - **Run the test for 60 seconds**
  - **Collect raw metrics** in `.full_run/{scenario}/results_raw/`
  - **Generate merged metrics and summarised results** in `.full_run/{scenario}/results/`
  - **Generate performance plots** in `.full_run/{scenario}/plots/`
  - **Collect clients and server logs** in `.full_run/{scenario}/logs/`
  - **Collect PCAP file and logs** in `.full_run/{scenario}/pcaps/`
  - **Collect NETEM list file** in `.full_run/{scenario}/netem_list.txt`


---

### 📂 Output Structure
At the end, you’ll get:
```bash
full_run/
└── <scenario>/                # e.g., baseline, loss2, loss5, delay100
    ├── pcaps/
    │   └── tcpdump_<scenario>.log
    │   └── <scenario>.pcap
    ├── logs/
    │   ├── client1_stdout.log
    │   ├── client2_stdout.log
    │   ├── client3_stdout.log
    │   ├── client4_stdout.log
    │   └── server_stdout.log
    ├── results_raw/
    │   ├── client_1_metrics.csv
    │   ├── client_2_metrics.csv
    │   ├── client_3_metrics.csv
    │   ├── client_4_metrics.csv
    │   └── server_metrics.csv
    ├── results/
    │   ├── metrics.csv
    │   └── summary.csv
    ├── plots/
    │   ├── latency_cdf.png
    │   ├── snapshots_per_sec.png
    │   ├── latency_timeseries.png
    │   ├── jitter_timeseries.png
    │   ├── cpu_timeseries.png
    │   ├── bandwidth_timeseries.png
    │   ├── per_client_snapshots.png
    │   └── latency_histogram.png
    └── netem_list.txt
```