# Telegram Forwarder Bot

> **Note:** Major part of this codebase was generated with **Qwen3.6 35B MoE** running locally via **llama.cpp** and **Zoo Code**. Later versions refined with **GPT 5.6 Sol - Medium**.

A multi-instance Telegram bot that monitors specified chat groups and topics for keyword matches, forwarding notifications to an admin user in real-time.

## Features

- **Real-time message monitoring** - Listens to messages from multiple Telegram chat groups and discussion forum topics
- **Keyword matching** - Supports AND-grouped keywords with Aho-Corasik automaton-based pattern matching (via `pyahocorasick`)
- **Multi-instance support** - Run multiple independent bot instances, each with its own configuration
- **Flexible subscription model** - Subscribe/unsubscribe to groups via URL links or explicit commands
- **Pause/resume notifications** - Temporarily halt notifications without losing subscriptions
- **Persistent storage** - Uses Python `pickle` with atomic file replacement for durable state persistence across restarts
- **Metrics collection** - Optional InfluxDB3 integration for performance and usage metrics

## Architecture

```mermaid
graph TD
    subgraph "User Client"
        UC[Telethon User Account]
    end

    subgraph "Bot Client"
        BC[Telethon Bot Account]
    end

    UC -->|Monitors messages| M[Monitor]
    M -->|Keyword match found| MQ[Match Queue]
    M -->|Checks subscriptions| SD[Storage Data]
    SD -->|Pattern matching| KM[Aho-Corasik Automaton]
    SD -->|Group subscriptions| GM[Group Manager]

    MQ -->|Dispatches| N[Notifier]
    N -->|Sends notification| BC
    BC -->|Delivers to admin| ADM[Admin User]

    BC <--->|Handles commands| N
    
    N -.->|Metrics| ID3[InfluxDB 3]
    ID3 -.->|Query| G[Grafana]
```

## Tech Stack

- **Python 3.12+** with `asyncio` for asynchronous operation
- **Telethon** - Telegram MTProto API client library
- **pickle** - Persistent key-value storage with atomic file replacement
- **Click** - Command-line interface framework
- **pyahocorasick** - Fast multi-pattern string matching
- **InfluxDB3** (optional) - Time-series metrics storage

## Prerequisites

- Python 3.12 or higher
- A Telegram API key pair ([get them here](https://my.telegram.org))
- A bot token from [@BotFather](https://t.me/BotFather)
- (Optional) InfluxDB 3 and Grafana for metrics visualization

## Installation

1. Clone this repository:

```bash
git clone git@github.com:rogday/telegram-forwarder-bot.git
cd telegram-forwarder-bot
```

2. Install dependencies:

```bash
pip install -r requirements.txt
```

3. Create an instance directory with static and dynamic configuration files:

```bash
mkdir -p data/my-instance
cp examples/static.yml data/my-instance/.static.yml
cp examples/dynamic.yml data/my-instance/.dynamic.yml
```

4. Edit `data/my-instance/.static.yml` and fill in your credentials:

```yaml
client:
  api_id: 123456
  api_hash: "your_api_hash_here"
  bot_token: "your_bot_token_here"
  admin_id: 123456789
  admin_phone: "+1234567890"
  session_dir: "./sessions"

storage_manager:
  database_path: "./storage.db"

metric_recorder:
  telemetry_instance_id: "default-instance"
  influxdb3:
    endpoint:
    token:
    database_name:
```

The `.dynamic.yml` file contains runtime-reloadable settings such as the timezone, deduplication, metric intervals, logging, and instrumentation. The bot reloads it when the file changes.

5. Start the telemetry stack (optional):

```bash
cp examples/compose.env .compose.env
# Set GRAFANA_ADMIN_PASSWORD in .compose.env, then:
docker compose --project-directory . --env-file .compose.env -f deploy/compose.telemetry.yml up -d
```

This starts the shared InfluxDB, Loki, Grafana, and Alloy services. Grafana, InfluxDB, and Alloy listen on all host interfaces by default; Loki remains internal to the Compose network.

### Access telemetry services

Grafana, InfluxDB, and Alloy are reachable through the host address on their published ports:

- Grafana: `http://HOST:3000`
- InfluxDB HTTP API: `http://HOST:8181`
- Alloy diagnostics: `http://HOST:12345`

### Initialize InfluxDB

Create the first administrator token inside the running InfluxDB container and extract the token value into a permission-restricted local file:

```bash
docker compose --project-directory . --env-file .compose.env -f deploy/compose.telemetry.yml exec -T influxdb \
  influxdb3 create token --admin \
  | grep -m1 -o "apiv3_[^[:space:]]*" \
  > .influxdb-token
```

Create the `metrics` database with 15-day retention:

```bash
docker compose --project-directory . --env-file .compose.env -f deploy/compose.telemetry.yml exec influxdb \
  influxdb3 create database \
  --host http://localhost:8181 \
  --token "$(cat .influxdb-token)" \
  --retention-period 15d \
  metrics
```

Configure each containerized bot instance to write to that database in its `.static.yml`:

```yaml
metric_recorder:
  telemetry_instance_id: "my-instance"
  influxdb3:
    endpoint: "http://influxdb:8181"
    token: "apiv3_replace_with_your_token"
    database_name: "metrics"
```

Use a distinct `telemetry_instance_id` for every bot. 

### Configure Grafana

Open `http://localhost:3000`, sign in with the administrator credentials from `.compose.env`, and select **Connections → Data sources → Add new data source → InfluxDB**. Configure it as follows:

- Query language: **SQL**
- URL: `http://influxdb:8181`
- Database: `metrics`
- Token: the value stored in `.influxdb-token`
- Advanced database settings → Insecure Connection: **enabled**

Select **Save & test**. SQL is the supported query mode for this dashboard and InfluxDB 3.

The included dashboard also contains a Loki panel. Add the **Loki** data source with URL `http://loki:3100`, then select **Save & test**.

To import the dashboard:

1. Select **Dashboards → New → Import dashboard**.
2. Select **Upload dashboard JSON file** and choose `deploy/grafana-dashboard.json` from this repository.
3. Select the InfluxDB and Loki data sources if Grafana prompts for them.
4. Select **Import**.

### Dashboard preview

![Grafana dashboard for the Telegram Forwarder Bot](examples/Dashboard.png)

## Running the Bot

### Directly with Python

```bash
python run_instance.py --env ./data/my-instance
```

The `--env` option points to the instance directory. Relative paths in the configuration are resolved from that directory, so each instance has its own sessions, storage, logs, and profiles.

### As an independent Compose instance

With the telemetry stack already running, start the bot:

```bash
BOT_INSTANCE_ID=my-instance docker compose --project-directory . --env-file .compose.env -f deploy/compose.bot.yml up -d --build
```

To add another instance, create `data/another-instance` with its own two YAML files, then run `BOT_INSTANCE_ID=another-instance docker compose --project-directory . --env-file .compose.env -f deploy/compose.bot.yml up -d`. Each ID gets its own Compose project, container, sessions, storage, and logs. Alloy discovers new `data/<instance>/logs/*.jsonl` files automatically, so its configuration and the telemetry stack do not need to be changed or restarted.

Use the same instance ID for lifecycle commands:

```bash
# Follow only this bot logs
BOT_INSTANCE_ID=my-instance docker compose --project-directory . --env-file .compose.env -f deploy/compose.bot.yml logs -f

# Stop and remove only this bot
BOT_INSTANCE_ID=my-instance docker compose --project-directory . --env-file .compose.env -f deploy/compose.bot.yml down
```

For metrics from a containerized bot, set `metric_recorder.influxdb3.endpoint` in that instance configuration to `http://influxdb:8181`. Leaving the endpoint and token empty keeps metrics export disabled.

## Bot Commands

All commands are sent to the bot directly and are only recognized by the admin user:

| Command | Description |
|---------|-------------|
| `/status` | Show current configuration and status |
| `/pause` | Toggle notification pause/resume |

### Smart Input

The bot interprets each space-separated token as an independent keyword group:

- Sending a space separated **Telegram URLs** (`https://t.me/groupname/topic_id https://t.me/another_groupname/topic_id`) toggles subscription for these groups/topics
- Sending `python_machine-learning` toggles one AND-group containing `python` and `machine-learning`
- Sending `python_machine-learning ubuntu_linux` toggles both groups at once
- Prefixing a keyword with `!` excludes messages containing that keyword from its group, for example `python_!course`

## How Keyword Matching Works

Keywords are organized into **AND-groups**. A message matches a group only if **all** keywords in that group appear somewhere in the message text. Multiple groups are evaluated with **OR** logic - matching any single group triggers a notification.

The bot uses the Aho-Corasik algorithm (via `pyahocorasick`) for efficient multi-pattern string matching across all keyword groups simultaneously.

Examples:

- Group 1: `python_machine-learning` - matches messages containing both "python" AND "machine-learning"
- Group 2: `ubuntu_linux` - matches messages containing both "ubuntu" AND "linux"

A message with "python ubuntu" would **not** match either group, but "python machine-learning" would match Group 1.

Identical message text is treated as a repost and only notifies once while its content hash remains in the configured deduplication cache.

## File Structure

```
telegram-forwarder-bot/
├── run_instance.py           # CLI entry point with Click
├── requirements.txt          # Python dependencies
├── deploy/
│   ├── Dockerfile            # Bot runtime image
│   ├── compose.telemetry.yml # Shared telemetry services
│   ├── compose.bot.yml       # Independently managed bot instance
│   ├── alloy.config          # Discovers logs for every bot instance
│   └── grafana-dashboard.json
├── examples/
│   ├── Dashboard.png         # Grafana dashboard preview
│   ├── compose.env           # Portable Compose path and port defaults
│   ├── static.yml            # Credentials and startup configuration
│   └── dynamic.yml           # Runtime-reloadable configuration
└── src/
    ├── main.py               # Async application bootstrap
    ├── config.py             # YAML configuration models and loaders
    ├── storage.py            # Persistent storage and keyword management
    ├── monitoring.py         # Message monitor and chat entity resolver
    ├── notification.py       # Command handler and notification dispatcher
    ├── metrics.py            # InfluxDB 3 performance metrics
    └── app_logging.py        # JSON file and stderr logging setup
```

## License

This project is provided as-is for personal or internal use.
