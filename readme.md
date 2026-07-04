# BMS Wireless Telemetry Dashboard

A lightweight wireless telemetry system for uploading Battery Management System (BMS) data and GPS positioning data to a real-time web dashboard using MQTT and FastAPI.

This project is designed for a CT511N 4G communication module or an MCU-based serial gateway. The device publishes BMS and GPS telemetry to an MQTT topic, while a FastAPI server subscribes to the data topic and updates a browser-based dashboard in real time.

## Features

- Real-time BMS data display
- GPS positioning display with map visualization
- MQTT-based wireless communication
- Web-based control buttons for requesting BMS or GPS updates
- Support for JSON telemetry payloads
- Support for raw GPS output such as `+GPSSTEX`
- Simple FastAPI backend
- Browser dashboard with automatic refresh
- Designed for CT511N / MCU / serial gateway integration

## System Architecture

```text
CT511N / MCU / BMS Device
        |
        | MQTT publish
        | Topic: bms/data
        v
MQTT Broker
        |
        | FastAPI subscribes to bms/data
        v
FastAPI Backend
        |
        | HTTP API: /api/status
        v
Web Dashboard
```

The control direction works separately:

```text
Web Dashboard Button
        |
        | POST /api/control/bms or /api/control/gps
        v
FastAPI Backend
        |
        | MQTT publish
        | Topic: bms/control
        v
CT511N / MCU subscribes to bms/control
        |
        | MCU executes command
        v
BMS or GPS module
```

## MQTT Topics

| Topic | Direction | Description |
|---|---|---|
| `bms/data` | Device to server | Uploads BMS, GPS, or combined telemetry data |
| `bms/control` | Server to device | Sends control commands from the web dashboard to the device |

The device must subscribe to `bms/control` if web button commands are required.

Example CT511N subscription command:

```text
AT+MSUB="bms/control",0
```

## Default Configuration

| Item | Value |
|---|---|
| Serial settings | `115200 / 8 / N / 1`, CRLF |
| Web dashboard | `http://8.148.13.100:8080/` |
| MQTT broker | `8.148.13.100:1883` |
| Upload topic | `bms/data` |
| Control topic | `bms/control` |

These values can be changed according to the deployment environment.

## Backend Configuration

In the FastAPI server code:

```python
MQTT_BROKER = "127.0.0.1"
MQTT_PORT = 1883

TOPIC_SUB_DATA = "bms/data"
TOPIC_PUB_CTRL = "bms/control"
```

If FastAPI and Mosquitto are running on the same server, use:

```python
MQTT_BROKER = "127.0.0.1"
```

If FastAPI connects to a remote broker, replace it with the broker IP address:

```python
MQTT_BROKER = "8.148.13.100"
```

## Installation

### 1. Install Python dependencies

```bash
pip install fastapi uvicorn paho-mqtt
```

### 2. Install and start Mosquitto

On Ubuntu:

```bash
sudo apt update
sudo apt install mosquitto mosquitto-clients
sudo systemctl enable mosquitto
sudo systemctl start mosquitto
```

Check status:

```bash
sudo systemctl status mosquitto
```

### 3. Run the FastAPI server

```bash
python main.py
```

Or:

```bash
uvicorn main:app --host 0.0.0.0 --port 8080
```

Then open:

```text
http://your-server-ip:8080/
```

## CT511N Basic MQTT Setup

The following sequence can be used for basic MQTT testing.

```text
AT
AT+MDISCONNECT
AT+MIPCLOSE
AT+QICSGP=1,1,"","",""
AT+NETOPEN
AT+CEREG?
AT+CEREG=0
AT+MCONFIG="BMS_0001"
AT+MIPSTART="8.148.13.100",1883
AT+MCONNECT=1,60
AT+MSUB="bms/control",0
```

Notes:

- `AT+MDISCONNECT` disconnects the previous MQTT session.
- `AT+MIPCLOSE` closes the socket connection.
- These commands are useful for recovering from stuck or stale MQTT sessions.
- Each device should use a unique MQTT client ID to avoid connection conflicts.

## Upload BMS Data

Example JSON payload:

```json
{
  "type": "bms",
  "voltage": 52.3,
  "current": 1.8,
  "soc": 76,
  "temperature": 31.5
}
```

Example MQTT publish command:

```text
AT+MPUB="bms/data",0,0,"{\"type\":\"bms\",\"voltage\":52.3,\"current\":1.8,\"soc\":76,\"temperature\":31.5}"
```

The web dashboard will display the fields in the BMS panel.

## Upload GPS Data

Example JSON payload:

```json
{
  "type": "gps",
  "fix_status": 1,
  "longitude": 114.394170,
  "latitude": 30.515838,
  "high": 15.598,
  "speed": 0.466,
  "satellites": 19
}
```

Example MQTT publish command:

```text
AT+MPUB="bms/data",0,0,"{\"type\":\"gps\",\"fix_status\":1,\"longitude\":114.394170,\"latitude\":30.515838,\"high\":15.598,\"speed\":0.466,\"satellites\":19}"
```

The web dashboard will update the GPS panel and move the map marker to the received position.

## Upload Combined BMS and GPS Telemetry

For real deployments, a combined telemetry message is recommended.

```json
{
  "type": "telemetry",
  "bms": {
    "voltage": 52.3,
    "current": 1.8,
    "soc": 76,
    "temperature": 31.5
  },
  "gps": {
    "fix_status": 1,
    "longitude": 114.394170,
    "latitude": 30.515838,
    "high": 15.598,
    "speed": 0.466,
    "satellites": 19
  }
}
```

Example MQTT publish command:

```text
AT+MPUB="bms/data",0,0,"{\"type\":\"telemetry\",\"bms\":{\"voltage\":52.3,\"current\":1.8,\"soc\":76,\"temperature\":31.5},\"gps\":{\"fix_status\":1,\"longitude\":114.394170,\"latitude\":30.515838,\"high\":15.598,\"speed\":0.466,\"satellites\":19}}"
```

## Upload Raw GPS Output

The backend can also parse raw CT511N GPS lines such as:

```text
+GPSSTEX: 1, 1, 114.394170, 15.598000, 30.515838, 0.466000, 28, 19
```

Example MQTT publish command:

```text
AT+MPUB="bms/data",0,0,"+GPSSTEX: 1, 1, 114.394170, 15.598000, 30.515838, 0.466000, 28, 19"
```

## GPS Initialization

For boards using an active GPS antenna, antenna bias power may need to be enabled before GPS positioning.

```text
AT+CGDRT=12,1
AT+CGSETV=12,1
AT+CGGETV=12
AT+MGPSC=1
AT+AGNSSGET=pos.asrmicro.com
AT+AGNSSSET
AT+GPSMODE=1
AT+MGPSGET=ALL,0
```

Then request extended GPS output:

```text
AT+GPSSTEX
```

Notes:

- Active antenna power settings depend on the hardware design.
- AGNSS requires 4G network connectivity, DNS availability, server reachability, and firmware support.
- If AGNSS fails, test normal GPS positioning outdoors before assuming hardware failure.

## Web Button Control

The dashboard provides two control buttons:

| Button | MQTT Topic | Payload | Expected Device Action |
|---|---|---|---|
| Update BMS Data | `bms/control` | `REQ_BMS_UPDATE` | MCU reads BMS data and publishes JSON to `bms/data` |
| Update GPS Position | `bms/control` | `AT+GPSSTEX` | MCU forwards the command to CT511N UART and publishes the GPS result to `bms/data` |

Important:

The web button only publishes an MQTT control message. It does not directly write to the CT511N serial port. A device-side MCU or serial bridge must subscribe to `bms/control`, parse the payload, and execute the corresponding serial command.

## Testing MQTT Manually

Subscribe to uploaded telemetry:

```bash
mosquitto_sub -h 127.0.0.1 -t bms/data -v
```

Subscribe to control commands:

```bash
mosquitto_sub -h 127.0.0.1 -t bms/control -v
```

Publish a test BMS message:

```bash
mosquitto_pub -h 127.0.0.1 -t bms/data -m '{"type":"bms","voltage":52.3,"soc":76}'
```

## API Endpoints

### Get latest status

```http
GET /api/status
```

Example response:

```json
{
  "bms": {
    "voltage": 52.3,
    "soc": 76
  },
  "gps": {
    "fix_status": "1",
    "longitude": "114.394170",
    "latitude": "30.515838",
    "high": "15.598",
    "speed": "0.466",
    "satellites": "19"
  },
  "mqtt": {
    "connected": true,
    "last_topic": "bms/data",
    "last_payload": "{\"type\":\"bms\",\"voltage\":52.3}",
    "last_update_time": "2026-07-04 18:30:00"
  }
}
```

### Send control command

```http
POST /api/control/bms
POST /api/control/gps
```

These endpoints publish control payloads to the MQTT topic `bms/control`.

## Troubleshooting

| Problem | Possible Cause | Solution |
|---|---|---|
| Web dashboard does not update | FastAPI did not receive MQTT data | Check `bms/data` using `mosquitto_sub` |
| MQTT publish succeeds but dashboard is empty | Payload is not valid JSON or not parsed correctly | Check `/api/status` and FastAPI logs |
| Web button has no effect | Device did not subscribe to `bms/control` | Run `AT+MSUB="bms/control",0` |
| CT511N receives MQTT payload but GPS does not output | MQTT payload is not automatically executed as an AT command | MCU must write `AT+GPSSTEX` to CT511N UART |
| MQTT connection gets stuck | Old MQTT/IP session remains active | Run `AT+MDISCONNECT` and `AT+MIPCLOSE` |
| GPS cannot fix | Antenna, sky view, GPS power, or AGNSS issue | Check antenna, `AT+MGPSC=1`, AGNSS, and outdoor conditions |
| Repeated network registration messages appear | CEREG URC enabled | Use `AT+CEREG=0` to disable active registration reports |

## Project Structure

```text
.
├── main.py              # FastAPI + MQTT backend and web dashboard
├── README.md            # Project documentation
└── docs/                # Optional hardware or protocol documents
```

## Security Notes

This project is intended for prototype and engineering testing.

For production use, consider:

- MQTT authentication
- TLS encryption
- Device-specific client IDs
- Access control for web APIs
- Firewall restrictions
- Input validation and logging
- Persistent storage for historical telemetry

## License

This project can be released under the MIT License, unless your hardware or company requirements specify otherwise.

## Author

Developed for a BMS wireless telemetry prototype using CT511N, MQTT, FastAPI, and a web-based monitoring dashboard.
