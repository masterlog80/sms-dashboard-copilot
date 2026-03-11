# SMS Dashboard

A production-ready SMS dashboard that reads messages from a GSM modem connected via serial port (`/dev/ttyUSB0`) and displays them in a modern web interface.

---

## Features

- **Real-time SMS reading** from a GSM modem using AT commands via PySerial  
- **REST API** (Flask/Gunicorn) with endpoints for listing, reading, and deleting messages  
- **Web UI** – clean, responsive single-page app; auto-refreshes every 30 seconds  
- **SQLite persistence** – messages survive container restarts  
- **Docker & Docker Compose** ready – one command to run  
- **Health-check endpoint** at `/api/health`

---

## Quick Start

### Prerequisites

- Docker and Docker Compose installed  
- A GSM modem plugged in as `/dev/ttyUSB0` (or configure `SERIAL_PORT`)

### Run with Docker Compose

```bash
docker compose up -d
```

Open **http://localhost:8080** in your browser.

> The web UI port defaults to **8080**. Override with the `SMS_DASHBOARD_PORT` environment variable.

### Build the image manually

```bash
docker build -t sms-dashboard .
docker run -d \
  --device /dev/ttyUSB0 \
  -p 8080:5000 \
  -v sms-data:/data \
  --name sms-dashboard \
  sms-dashboard
```

---

## Configuration

All options are set via environment variables (or the `.env` file used by Docker Compose):

| Variable              | Default         | Description                          |
|-----------------------|-----------------|--------------------------------------|
| `SERIAL_PORT`         | `/dev/ttyUSB0`  | Path to the GSM modem serial device  |
| `SERIAL_BAUD`         | `9600`          | Serial baud rate                     |
| `DB_PATH`             | `/data/sms.db`  | SQLite database file path            |
| `LOG_LEVEL`           | `INFO`          | Logging verbosity (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |
| `SMS_DASHBOARD_PORT`  | `8080`          | Host port for the web UI             |

---

## REST API

| Method | Endpoint                        | Description                        |
|--------|---------------------------------|------------------------------------|
| GET    | `/api/messages`                 | List messages (paginated)          |
| GET    | `/api/messages?unread=1`        | List unread messages only          |
| GET    | `/api/messages/<id>`            | Get a single message               |
| POST   | `/api/messages/<id>/read`       | Mark a message as read             |
| DELETE | `/api/messages/<id>`            | Delete a message                   |
| GET    | `/api/stats`                    | Summary statistics                 |
| GET    | `/api/health`                   | Health check                       |

### Example

```bash
curl http://localhost:8080/api/messages
curl http://localhost:8080/api/stats
curl -X POST http://localhost:8080/api/messages/1/read
curl -X DELETE http://localhost:8080/api/messages/1
```

---

## Project Structure

```
.
├── app/
│   ├── app.py           # Flask backend + SMS reader
│   ├── wsgi.py          # Gunicorn entry-point
│   └── requirements.txt
├── static/
│   └── index.html       # Single-page web UI
├── Dockerfile
├── docker-compose.yml
└── README.md
```

---

## Modem Compatibility

Tested with standard GSM/GPRS modems that support AT commands (e.g. SIM800, SIM900, Quectel EC21/EC25, u-blox SARA series). The modem is initialised with:

- `AT+CMGF=1` – text mode  
- `AT+CSCS="GSM"` – GSM character set  
- `AT+CMGL="ALL"` – read all messages every 30 seconds  

Messages are deleted from the modem after being stored locally.