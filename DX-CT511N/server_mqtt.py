from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from contextlib import asynccontextmanager
import uvicorn
import json
import re
import threading
import time
import paho.mqtt.client as mqtt

# ============================================================
# MQTT-only BMS + GPS Gateway
# 数据链路：
# CT511N/MCU -> MQTT publish bms/data -> FastAPI订阅 -> 网页显示
#
# 注意：
# 1. 本代码不再使用 HTTP POST 上传数据。
# 2. 服务器上必须有 MQTT Broker，例如 Mosquitto。
# 3. CT511N 模块要连接公网 Broker 地址：8.148.13.100:1883
# 4. 本 FastAPI 程序如果和 Broker 在同一台服务器，MQTT_BROKER 用 127.0.0.1 即可。
# ============================================================

# ----------------- 数据区 -----------------
latest_bms_status = {}
latest_gps_status = {
    "fix_status": "0",
    "longitude": "0.000000",
    "latitude": "0.000000",
    "high": "0.000",
    "speed": "0.000",
    "satellites": "0"
}
latest_mqtt_status = {
    "connected": False,
    "last_topic": "",
    "last_payload": "",
    "last_update_time": ""
}

data_lock = threading.Lock()

# ----------------- MQTT 配置 -----------------
# FastAPI 和 MQTT Broker 在同一台云服务器时，用 127.0.0.1。
# CT511N 模块连接时，应该连接公网 IP：8.148.13.100:1883。
MQTT_BROKER = "127.0.0.1"
MQTT_PORT = 1883

# 模块/MCU 上传数据到这个 topic
TOPIC_SUB_DATA = "bms/data"

# 网页按钮下发控制命令到这个 topic
# 只有当 MCU/串口桥接程序订阅这个 topic 并转成 AT 指令时，它才会真正控制 CT511N。
TOPIC_PUB_CTRL = "bms/control"

# 可选：如果 Broker 设置了用户名密码，填这里；没有就保持 None
MQTT_USERNAME = None
MQTT_PASSWORD = None


# ----------------- 解析函数 -----------------
def safe_str(value, default="0"):
    if value is None:
        return default
    return str(value)


def nmea_to_dec(nmea_val, direction):
    """NMEA 度分格式转十进制度数，例如 3030.967227 -> 30.516120"""
    if not nmea_val or nmea_val.strip() == "":
        return "0.000000"
    try:
        dot = nmea_val.find(".")
        if dot == -1:
            return "0.000000"
        degrees = float(nmea_val[:dot - 2])
        minutes = float(nmea_val[dot - 2:])
        dec = degrees + minutes / 60.0
        if direction in ["S", "W"]:
            dec = -dec
        return f"{dec:.6f}"
    except Exception:
        return "0.000000"


def update_gps_from_dict(gps_obj: dict) -> bool:
    """兼容 JSON GPS: longitude/latitude 或 lng/lat"""
    if not isinstance(gps_obj, dict):
        return False

    fix_status = gps_obj.get("fix_status", gps_obj.get("fix", gps_obj.get("gps_fix", None)))
    longitude = gps_obj.get("longitude", gps_obj.get("lng", gps_obj.get("lon", None)))
    latitude = gps_obj.get("latitude", gps_obj.get("lat", None))
    high = gps_obj.get("high", gps_obj.get("altitude", gps_obj.get("alt", None)))
    speed = gps_obj.get("speed", None)
    satellites = gps_obj.get("satellites", gps_obj.get("satellite", gps_obj.get("sats", None)))

    if fix_status is None and longitude is None and latitude is None:
        return False

    with data_lock:
        if fix_status is not None:
            latest_gps_status["fix_status"] = safe_str(fix_status, "0")
        if longitude is not None:
            latest_gps_status["longitude"] = safe_str(longitude, "0.000000")
        if latitude is not None:
            latest_gps_status["latitude"] = safe_str(latitude, "0.000000")
        if high is not None:
            latest_gps_status["high"] = safe_str(high, "0.000")
        if speed is not None:
            latest_gps_status["speed"] = safe_str(speed, "0.000")
        if satellites is not None:
            latest_gps_status["satellites"] = safe_str(satellites, "0")

    return True


def handle_json_payload(obj: dict) -> bool:
    """
    支持：
    1. {"type":"bms","voltage":52.3,"current":1.8,"soc":76,"temperature":31.5}
    2. {"type":"gps","fix_status":1,"longitude":113.8,"latitude":22.6}
    3. {"type":"telemetry","bms":{...},"gps":{...}}
    """
    global latest_bms_status

    if not isinstance(obj, dict):
        return False

    handled = False
    msg_type = str(obj.get("type", "")).lower()

    if isinstance(obj.get("gps"), dict):
        if update_gps_from_dict(obj["gps"]):
            handled = True

    if msg_type == "gps" or any(k in obj for k in ["longitude", "latitude", "lng", "lat", "gps_fix"]):
        if update_gps_from_dict(obj):
            handled = True

    if isinstance(obj.get("bms"), dict):
        with data_lock:
            latest_bms_status = obj["bms"]
        handled = True

    if msg_type == "bms":
        bms_obj = {k: v for k, v in obj.items() if k != "type"}
        with data_lock:
            latest_bms_status = bms_obj
        handled = True

    # telemetry 里除了 bms/gps 外的 device_id/timestamp 不放进左侧 BMS 面板，避免太乱
    if msg_type == "telemetry":
        handled = True

    # 没有 type，也不是 GPS，则默认当 BMS
    if not handled:
        with data_lock:
            latest_bms_status = obj
        handled = True

    return handled


def parse_gnss_data(raw_text: str) -> bool:
    """
    兼容：
    1. +GPSSTEX / +GPS5TEX
    2. $GNRMC / $GNGGA
    3. JSON GPS / telemetry
    """
    if not raw_text:
        return False

    raw_text = raw_text.strip()
    has_parsed = False

    # JSON
    try:
        obj = json.loads(raw_text)
        if isinstance(obj, dict):
            return handle_json_payload(obj)
    except json.JSONDecodeError:
        pass

    # CT511N 扩展 GPS
    # +GPSSTEX: 1, 1, 113.831385, 12.166000, 22.606304, 0.013000, 15, 14
    if "+GPS" in raw_text:
        match = re.search(
            r"\+GPS(?:5|S)TEX[:：]\s*"
            r"(-?[\d.]+)\s*,\s*(-?[\d.]+)\s*,\s*(-?[\d.]+)\s*,\s*(-?[\d.]+)\s*,\s*"
            r"(-?[\d.]+)\s*,\s*(-?[\d.]+)\s*,\s*(-?[\d.]+)\s*,\s*(-?[\d.]+)",
            raw_text
        )
        if match:
            with data_lock:
                latest_gps_status["fix_status"] = match.group(1)
                latest_gps_status["longitude"] = match.group(3)
                latest_gps_status["high"] = match.group(4)
                latest_gps_status["latitude"] = match.group(5)
                latest_gps_status["speed"] = match.group(6)
                latest_gps_status["satellites"] = match.group(8)
            has_parsed = True

    # NMEA RMC
    if "$GNRMC" in raw_text:
        parts = raw_text.split(",")
        if len(parts) >= 8:
            status = parts[2]
            with data_lock:
                if status == "A":
                    latest_gps_status["fix_status"] = "1"
                    latest_gps_status["latitude"] = nmea_to_dec(parts[3], parts[4])
                    latest_gps_status["longitude"] = nmea_to_dec(parts[5], parts[6])
                    try:
                        knots = float(parts[7]) if parts[7] else 0.0
                        latest_gps_status["speed"] = f"{knots * 0.514444:.3f}"
                    except Exception:
                        latest_gps_status["speed"] = "0.000"
                elif status == "V":
                    latest_gps_status["fix_status"] = "0"
            has_parsed = True

    # NMEA GGA
    if "$GNGGA" in raw_text or "$GPGGA" in raw_text:
        parts = raw_text.split(",")
        if len(parts) >= 10:
            fix_quality = parts[6] if len(parts) > 6 else "0"
            satellites = parts[7] if len(parts) > 7 else ""
            altitude = parts[9] if len(parts) > 9 else ""

            with data_lock:
                latest_gps_status["fix_status"] = "1" if fix_quality and fix_quality != "0" else "0"
                if satellites:
                    latest_gps_status["satellites"] = satellites
                if altitude:
                    latest_gps_status["high"] = altitude
                if len(parts) >= 6 and parts[2] and parts[4]:
                    latest_gps_status["latitude"] = nmea_to_dec(parts[2], parts[3])
                    latest_gps_status["longitude"] = nmea_to_dec(parts[4], parts[5])

            has_parsed = True

    return has_parsed


def process_incoming_text(raw_text: str):
    """统一处理 MQTT 收到的数据。支持多行、原始 GPS、JSON。"""
    if not raw_text:
        return

    raw_text = raw_text.strip()

    # 先整体尝试 JSON，避免 telemetry 嵌套 JSON 被逐行/正则破坏
    try:
        obj = json.loads(raw_text)
        if isinstance(obj, dict):
            handle_json_payload(obj)
            return
    except json.JSONDecodeError:
        pass

    # 再逐行处理原始 GPS / NMEA
    for line in raw_text.splitlines():
        line = line.strip()
        if not line:
            continue
        parse_gnss_data(line)


# ----------------- MQTT 回调 -----------------
def on_connect(client, userdata, flags, rc):
    with data_lock:
        latest_mqtt_status["connected"] = (rc == 0)

    if rc == 0:
        print(f"[INFO] MQTT connected to {MQTT_BROKER}:{MQTT_PORT}")
        client.subscribe(TOPIC_SUB_DATA, qos=0)
        print(f"[INFO] Subscribed topic: {TOPIC_SUB_DATA}")
    else:
        print(f"[ERROR] MQTT connection failed: rc={rc}")


def on_disconnect(client, userdata, rc):
    with data_lock:
        latest_mqtt_status["connected"] = False
    print(f"[WARN] MQTT disconnected: rc={rc}")


def on_message(client, userdata, msg):
    try:
        raw_text = msg.payload.decode("utf-8", errors="ignore").strip()

        with data_lock:
            latest_mqtt_status["last_topic"] = msg.topic
            latest_mqtt_status["last_payload"] = raw_text[:500]
            latest_mqtt_status["last_update_time"] = time.strftime("%Y-%m-%d %H:%M:%S")

        print(f"[MQTT] {msg.topic}: {raw_text}")
        process_incoming_text(raw_text)

    except Exception as e:
        print(f"[ERROR] MQTT message callback error: {str(e)}")


mqtt_client = mqtt.Client(client_id="BMS_Gateway_Server")
if MQTT_USERNAME:
    mqtt_client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)

mqtt_client.on_connect = on_connect
mqtt_client.on_disconnect = on_disconnect
mqtt_client.on_message = on_message


# ----------------- FastAPI -----------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        mqtt_client.connect(MQTT_BROKER, MQTT_PORT, keepalive=60)
        mqtt_client.loop_start()
    except Exception as e:
        print(f"[CRITICAL] MQTT startup failed: {str(e)}")
        print("[CRITICAL] Please check whether Mosquitto/MQTT broker is running.")

    yield

    try:
        mqtt_client.loop_stop()
        mqtt_client.disconnect()
    except Exception:
        pass


app = FastAPI(title="BMS MQTT Gateway Solution", lifespan=lifespan)


@app.get("/api/status")
def get_latest_status():
    with data_lock:
        return {
            "bms": latest_bms_status,
            "gps": latest_gps_status,
            "mqtt": latest_mqtt_status
        }


@app.post("/api/control")
def send_control_cmd():
    """
    网页按钮发布控制命令到 MQTT。
    注意：只有 MCU/串口桥接程序订阅 bms/control，并把命令写入 CT511N UART，
    CT511N 才会真的执行 AT+GPSSTEX。
    """
    try:
        payload = "AT+GPSSTEX"
        mqtt_client.publish(TOPIC_PUB_CTRL, payload, qos=0)
        return {"status": "success", "topic": TOPIC_PUB_CTRL, "payload": payload}
    except Exception as e:
        return {"status": "error", "detail": str(e)}


@app.get("/", response_class=HTMLResponse)
def show_web_page():
    html_content = """
<!DOCTYPE html>
<html>
<head>
    <title>BMS 远程实时监控与定位</title>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">

    <!-- Leaflet + OpenStreetMap，无需高德 Key，避免高德 Key/白名单/安全码导致灰屏 -->
    <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
    <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>

    <style>
        :root {
            --bg-main: #f8fafc;
            --panel-bg: #ffffff;
            --border-color: #e2e8f0;
            --text-primary: #0f172a;
            --text-secondary: #64748b;
            --brand-color: #2563eb;
        }
        body {
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Arial, sans-serif;
            background-color: var(--bg-main);
            color: var(--text-primary);
            margin: 0;
            padding: 24px;
        }
        .dashboard {
            max-width: 1300px;
            margin: 0 auto;
        }
        header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 20px;
            border-bottom: 1px solid var(--border-color);
            padding-bottom: 12px;
        }
        h1 { font-size: 20px; margin: 0; font-weight: 600; }
        .subtitle { font-size: 12px; color: var(--text-secondary); margin-top: 2px; }
        .grid-layout {
            display: grid;
            grid-template-columns: 280px 1fr 340px;
            gap: 20px;
        }
        @media (max-width: 1024px) {
            .grid-layout { grid-template-columns: 1fr; }
        }
        .card {
            background: var(--panel-bg);
            border-radius: 6px;
            border: 1px solid var(--border-color);
            padding: 16px;
            display: flex;
            flex-direction: column;
        }
        .card-title {
            font-size: 13px;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            color: var(--text-secondary);
            margin-bottom: 12px;
            font-weight: 600;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        .metric-item {
            background: #f8fafc;
            border: 1px solid var(--border-color);
            border-radius: 4px;
            padding: 10px 12px;
            margin-bottom: 10px;
        }
        .metric-label { font-size: 11px; color: var(--text-secondary); }
        .metric-value {
            font-size: 16px;
            font-weight: 700;
            font-family: monospace;
            margin-top: 2px;
            word-break: break-word;
        }
        #map-container {
            width: 100%;
            height: 550px;
            min-height: 550px;
            border-radius: 6px;
            border: 1px solid var(--border-color);
            background: #e5e7eb;
        }
        .gps-row {
            display: flex;
            justify-content: space-between;
            gap: 10px;
            padding: 8px 0;
            border-bottom: 1px solid #f1f5f9;
            font-size: 13px;
        }
        .gps-val { font-family: monospace; font-weight: 600; text-align: right; }
        .badge {
            font-size: 11px;
            padding: 2px 6px;
            border-radius: 3px;
            font-weight: 500;
        }
        .bg-success { background: #d1fae5; color: #065f46; }
        .bg-warn { background: #fef3c7; color: #92400e; }
        .bg-error { background: #fee2e2; color: #991b1b; }
        button {
            background: var(--brand-color);
            color: white;
            border: none;
            padding: 6px 12px;
            font-size: 13px;
            border-radius: 4px;
            cursor: pointer;
        }

        button:disabled {
            opacity: 0.65;
            cursor: not-allowed;
        }
        #map-error {
            margin-top: 8px;
            font-size: 12px;
            color: #991b1b;
            display: none;
        }
        .small-text {
            font-size: 12px;
            color: var(--text-secondary);
            line-height: 1.5;
            word-break: break-word;
        }
    </style>
</head>

<body>
    <div class="dashboard">
        <header>
            <div>
                <h1>BMS 远程实时监控与定位看板</h1>
                <div class="subtitle">数据来源：MQTT topic bms/data</div>
            </div>
            <div style="display:flex; gap:8px;">
            <button id="btn-bms" onclick="requestBmsData()">更新 BMS 数据</button>
            <button id="btn-gps" onclick="requestGpsData()">更新 GPS 定位</button>
            </div>
        </header>

        <div class="grid-layout">
            <div class="card">
                <div class="card-title">🔋 电芯状态指标</div>
                <div id="bms-grid" style="flex: 1; overflow-y: auto;">
                    <div style="color:var(--text-secondary); font-size:13px;">📡 等待 MQTT 电芯数据...</div>
                </div>
            </div>

            <div class="card" style="padding: 10px;">
                <div id="map-container"></div>
                <div id="map-error"></div>
            </div>

            <div class="card">
                <div class="card-title">
                    <span>📍 定位遥测数据</span>
                    <span id="gps-badge" class="badge bg-warn">未定位</span>
                </div>
                <div>
                    <div class="gps-row"><span>经度 (Lng)</span><span id="val-lng" class="gps-val">0.000000</span></div>
                    <div class="gps-row"><span>纬度 (Lat)</span><span id="val-lat" class="gps-val">0.000000</span></div>
                    <div class="gps-row"><span>海拔高度</span><span id="val-high" class="gps-val">0.000 m</span></div>
                    <div class="gps-row"><span>行驶速度</span><span id="val-speed" class="gps-val">0.000 m/s</span></div>
                    <div class="gps-row"><span>有效卫星数</span><span id="val-sats" class="gps-val">0</span></div>
                </div>

                <div class="card-title" style="margin-top: 20px;">📡 MQTT 状态</div>
                <div class="small-text">
                    <div>连接状态：<span id="mqtt-connected">unknown</span></div>
                    <div>最后时间：<span id="mqtt-time">-</span></div>
                    <div>最后Topic：<span id="mqtt-topic">-</span></div>
                    <div>最后收到的数据：</div>
                    <div id="mqtt-payload" style="font-family: monospace; margin-top: 4px;">-</div>
                </div>
            </div>
        </div>
    </div>

    <script>
        let map = null;
        let marker = null;
        let mapReady = false;

        function showMapError(message) {
            const err = document.getElementById('map-error');
            err.style.display = 'block';
            err.innerText = message;
            console.error(message);
        }

        function initMap() {
            if (typeof L === 'undefined') {
                showMapError('Leaflet 未加载。请检查浏览器是否能访问 unpkg.com。');
                return;
            }

            try {
                map = L.map('map-container').setView([30.516120, 114.394223], 15);

                L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
                    maxZoom: 19,
                    attribution: '&copy; OpenStreetMap contributors'
                }).addTo(map);

                marker = L.marker([30.516120, 114.394223]).addTo(map);
                marker.bindPopup('BMS 终端位置');

                setTimeout(function () {
                    map.invalidateSize();
                }, 300);

                mapReady = true;
            } catch (e) {
                showMapError('地图初始化失败：' + e.message);
            }
        }

        function renderObject(obj, prefix = '') {
            let html = '';
            for (const key in obj) {
                if (typeof obj[key] === 'object' && obj[key] !== null) {
                    html += renderObject(obj[key], prefix + key + '.');
                } else {
                    html += `<div class="metric-item">
                                <div class="metric-label">${(prefix + key).toUpperCase()}</div>
                                <div class="metric-value">${obj[key]}</div>
                             </div>`;
                }
            }
            return html;
        }

        function updateDashboard() {
            fetch('/api/status')
                .then(response => response.json())
                .then(data => {
                    const bmsContainer = document.getElementById('bms-grid');
                    if (data.bms && Object.keys(data.bms).length > 0) {
                        bmsContainer.innerHTML = renderObject(data.bms);
                    }

                    const gps = data.gps || {};
                    const isFixed = String(gps.fix_status) === "1";

                    document.getElementById('gps-badge').className = isFixed ? 'badge bg-success' : 'badge bg-warn';
                    document.getElementById('gps-badge').innerText = isFixed ? '已定位' : '未定位';

                    if (isFixed) {
                        document.getElementById('val-lng').innerText = gps.longitude + ' °';
                        document.getElementById('val-lat').innerText = gps.latitude + ' °';
                        document.getElementById('val-high').innerText = gps.high + ' m';
                        document.getElementById('val-speed').innerText = gps.speed + ' m/s';
                        document.getElementById('val-sats').innerText = gps.satellites;
                    } else {
                        document.getElementById('val-lng').innerText = '-----';
                        document.getElementById('val-lat').innerText = '-----';
                        document.getElementById('val-high').innerText = '-----';
                        document.getElementById('val-speed').innerText = '-----';
                        document.getElementById('val-sats').innerText = '-----';
                    }

                    const mqtt = data.mqtt || {};
                    document.getElementById('mqtt-connected').innerText = mqtt.connected ? 'connected' : 'disconnected';
                    document.getElementById('mqtt-connected').style.color = mqtt.connected ? '#065f46' : '#991b1b';
                    document.getElementById('mqtt-topic').innerText = mqtt.last_topic || '-';
                    document.getElementById('mqtt-time').innerText = mqtt.last_update_time || '-';
                    document.getElementById('mqtt-payload').innerText = mqtt.last_payload || '-';

                    if (isFixed && mapReady && marker) {
                        const rawLng = parseFloat(gps.longitude);
                        const rawLat = parseFloat(gps.latitude);

                        if (!isNaN(rawLng) && !isNaN(rawLat) &&
                            rawLng >= -180 && rawLng <= 180 &&
                            rawLat >= -90 && rawLat <= 90 &&
                            !(rawLng === 0 && rawLat === 0)) {

                            // Leaflet 使用 [lat, lng]，注意顺序和高德相反
                            marker.setLatLng([rawLat, rawLng]);
                            map.setView([rawLat, rawLng], 15);
                            setTimeout(function () {
                                map.invalidateSize();
                            }, 100);
                        }
                    }
                })
                .catch(err => console.error('updateDashboard error:', err));
        }

        function setButtonLoading(buttonId, loadingText, normalText, isLoading) {
            const btn = document.getElementById(buttonId);
            if (!btn) return;

            btn.disabled = isLoading;
            btn.innerText = isLoading ? loadingText : normalText;
        }

        function requestBmsData() {
            setButtonLoading('btn-bms', '发送中...', '更新 BMS 数据', true);

            fetch('/api/control/bms', { method: 'POST' })
                .then(resp => resp.json())
                .then(data => {
                    console.log('request bms:', data);
                })
                .catch(err => {
                    console.error('request bms error:', err);
                })
                .finally(() => {
                    setTimeout(() => {
                        setButtonLoading('btn-bms', '发送中...', '更新 BMS 数据', false);
                    }, 800);
                });
        }

        function requestGpsData() {
            setButtonLoading('btn-gps', '发送中...', '更新 GPS 定位', true);

            fetch('/api/control/gps', { method: 'POST' })
                .then(resp => resp.json())
                .then(data => {
                    console.log('request gps:', data);
                })
                .catch(err => {
                    console.error('request gps error:', err);
                })
                .finally(() => {
                    setTimeout(() => {
                        setButtonLoading('btn-gps', '发送中...', '更新 GPS 定位', false);
                    }, 800);
                });
        }

        window.addEventListener('load', function () {
            initMap();
            updateDashboard();
            setInterval(updateDashboard, 1000);
        });
    </script>
</body>
</html>
    """
    return html_content


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8080)
