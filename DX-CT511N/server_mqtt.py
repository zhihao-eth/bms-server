from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from contextlib import asynccontextmanager
import uvicorn
import json
import re
import threading
import paho.mqtt.client as mqtt

# ----------------- 核心数据区与线程安全机制 -----------------
latest_bms_status = {}
latest_gps_status = {
    "fix_status": "0",        # 0: 未定位成功, 1: 定位成功
    "longitude": "0.000000",  # 经度
    "latitude": "0.000000",   # 纬度
    "high": "0.000",          # 高度/海拔
    "speed": "0.000",         # 速度
    "satellites": "0"         # 参与定位卫星数
}
data_lock = threading.Lock()

MQTT_BROKER = "127.0.0.1"
MQTT_PORT = 1883
TOPIC_SUB_DATA = "bms/data"
TOPIC_PUB_CTRL = "bms/control"

# ----------------- GPS / BMS 解析核心逻辑 -----------------
def safe_str(value, default="0"):
    if value is None:
        return default
    return str(value)

def nmea_to_dec(nmea_val, direction):
    """将 NMEA 度分格式转换为十进制度数，例如 3030.967227 -> 30.516120"""
    if not nmea_val or nmea_val.strip() == "":
        return "0.000000"
    try:
        dot = nmea_val.find(".")
        if dot == -1:
            return "0.000000"
        degrees = float(nmea_val[:dot - 2])
        minutes = float(nmea_val[dot - 2:])
        dec = degrees + (minutes / 60.0)
        if direction in ["S", "W"]:
            dec = -dec
        return f"{dec:.6f}"
    except Exception:
        return "0.000000"

def update_gps_from_dict(gps_obj: dict) -> bool:
    """
    兼容 JSON GPS 格式：
    1) {"type":"gps","fix_status":1,"longitude":113.8,"latitude":22.6,...}
    2) {"type":"telemetry","gps":{"fix_status":1,"longitude":113.8,"latitude":22.6,...}}
    3) {"gps":{"lng":113.8,"lat":22.6,"altitude":12.1,...}}
    """
    if not isinstance(gps_obj, dict):
        return False

    # 兼容 lng/lat/altitude 等别名
    fix_status = gps_obj.get("fix_status", gps_obj.get("fix", gps_obj.get("gps_fix", None)))
    longitude = gps_obj.get("longitude", gps_obj.get("lng", gps_obj.get("lon", None)))
    latitude = gps_obj.get("latitude", gps_obj.get("lat", None))
    high = gps_obj.get("high", gps_obj.get("altitude", gps_obj.get("alt", None)))
    speed = gps_obj.get("speed", None)
    satellites = gps_obj.get("satellites", gps_obj.get("satellite", gps_obj.get("sats", None)))

    # 至少要有 fix 或经纬度之一，否则不认为是 GPS 数据
    if fix_status is None and longitude is None and latitude is None:
        return False

    with data_lock:
        if fix_status is not None:
            latest_gps_status["fix_status"] = safe_str(int(fix_status) if isinstance(fix_status, bool) else fix_status, "0")
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
    兼容两种 JSON 上传：
    A. 统一 telemetry:
       {"type":"telemetry","bms":{...},"gps":{...}}
    B. 分开上传:
       {"type":"gps",...}
       {"type":"bms",...}
    """
    global latest_bms_status

    if not isinstance(obj, dict):
        return False

    handled = False
    msg_type = str(obj.get("type", "")).lower()

    # 1. 嵌套 GPS: {"gps": {...}}
    if isinstance(obj.get("gps"), dict):
        if update_gps_from_dict(obj["gps"]):
            handled = True

    # 2. 平铺 GPS: {"type":"gps", ...}
    if msg_type == "gps" or any(k in obj for k in ["longitude", "latitude", "lng", "lat", "gps_fix"]):
        if update_gps_from_dict(obj):
            handled = True

    # 3. 嵌套 BMS: {"bms": {...}}
    if isinstance(obj.get("bms"), dict):
        with data_lock:
            latest_bms_status = obj["bms"]
        handled = True

    # 4. 平铺 BMS: {"type":"bms", ...}
    if msg_type == "bms":
        bms_obj = {k: v for k, v in obj.items() if k != "type"}
        with data_lock:
            latest_bms_status = bms_obj
        handled = True

    # 5. 没有 type 但也不是 GPS，则默认当 BMS
    if not handled:
        with data_lock:
            latest_bms_status = obj
        handled = True

    return handled

def parse_gnss_data(raw_text: str):
    """
    兼容三类 GPS 数据：
    1. CT511N 扩展定位结果: +GPSSTEX / +GPS5TEX
    2. NMEA: $GNRMC / $GNGGA
    3. JSON: {"type":"gps",...} 或 {"type":"telemetry","gps":{...}}
    """
    if not raw_text:
        return False

    raw_text = raw_text.strip()
    has_parsed = False

    # 0. 优先尝试 JSON，支持嵌套 JSON
    try:
        obj = json.loads(raw_text)
        if isinstance(obj, dict):
            return handle_json_payload(obj)
    except json.JSONDecodeError:
        pass

    # 1. 解析 CT511N +GPSSTEX / +GPS5TEX
    # 示例：
    # +GPSSTEX: 1, 1, 113.831385, 12.166000, 22.606304, 0.013000, 15, 14
    # 字段：
    # 1 fix_status, 2 module_status, 3 longitude, 4 high, 5 latitude, 6 speed, 7 visible sats, 8 used sats
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

    # 2. 解析 NMEA $GNRMC，主要拿经纬度和速度
    # $GNRMC,024603.00,A,2236.3782,N,11349.8831,E,0.013,,...
    if "$GNRMC" in raw_text:
        parts = raw_text.split(",")
        if len(parts) >= 8:
            status = parts[2]  # A=有效定位, V=无效定位
            with data_lock:
                if status == "A":
                    latest_gps_status["fix_status"] = "1"
                    latest_gps_status["latitude"] = nmea_to_dec(parts[3], parts[4])
                    latest_gps_status["longitude"] = nmea_to_dec(parts[5], parts[6])
                    try:
                        knots = float(parts[7]) if parts[7] else 0.0
                        latest_gps_status["speed"] = f"{(knots * 0.514444):.3f}"
                    except Exception:
                        latest_gps_status["speed"] = "0.000"
                elif status == "V":
                    latest_gps_status["fix_status"] = "0"
            has_parsed = True

    # 3. 解析 NMEA $GNGGA，主要拿定位质量、卫星数和高度
    # $GNGGA,024603.00,2236.3782,N,11349.8831,E,1,14,0.8,12.1,M,...
    if "$GNGGA" in raw_text:
        parts = raw_text.split(",")
        if len(parts) >= 10:
            fix_quality = parts[6] if len(parts) > 6 else "0"
            satellites = parts[7] if len(parts) > 7 else ""
            altitude = parts[9] if len(parts) > 9 else ""

            with data_lock:
                if fix_quality and fix_quality != "0":
                    latest_gps_status["fix_status"] = "1"
                else:
                    latest_gps_status["fix_status"] = "0"

                if satellites:
                    latest_gps_status["satellites"] = satellites
                if altitude:
                    latest_gps_status["high"] = altitude

                # 如果 GGA 里有经纬度，也顺手更新
                if len(parts) >= 6 and parts[2] and parts[4]:
                    latest_gps_status["latitude"] = nmea_to_dec(parts[2], parts[3])
                    latest_gps_status["longitude"] = nmea_to_dec(parts[4], parts[5])

            has_parsed = True

    return has_parsed

def process_incoming_text(raw_text: str):
    """统一处理 HTTP / MQTT 收到的数据。支持多行、原始 GPS、JSON。"""
    global latest_bms_status

    if not raw_text:
        return

    raw_text = raw_text.strip()

    # 1. 整体 JSON 优先，避免 nested JSON 被正则截断
    try:
        obj = json.loads(raw_text)
        if isinstance(obj, dict):
            handle_json_payload(obj)
            return
    except json.JSONDecodeError:
        pass

    # 2. 多行逐行处理
    for line in raw_text.splitlines():
        line = line.strip()
        if not line:
            continue

        if parse_gnss_data(line):
            continue

        # 3. 兼容单行简单 JSON
        try:
            obj = json.loads(line)
            if isinstance(obj, dict):
                handle_json_payload(obj)
                continue
        except json.JSONDecodeError:
            pass

        # 4. 兼容普通 BMS key=value 或其他原始文本，可按需扩展
        # 当前忽略无法识别的行

# ----------------- MQTT 协议栈回调驱动 -----------------
def on_connect(client, userdata, flags, rc):
    if rc == 0:
        print(f"[INFO] MQTT Connected to {MQTT_BROKER}")
        client.subscribe(TOPIC_SUB_DATA, qos=0)
    else:
        print(f"[ERROR] MQTT connection failed: {rc}")

def on_message(client, userdata, msg):
    try:
        raw_text = msg.payload.decode("utf-8", errors="ignore").strip()
        process_incoming_text(raw_text)
    except Exception as e:
        print(f"[ERROR] MQTT message callback error: {str(e)}")

mqtt_client = mqtt.Client(client_id="BMS_Gateway_Server")
mqtt_client.on_connect = on_connect
mqtt_client.on_message = on_message

@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        mqtt_client.connect(MQTT_BROKER, MQTT_PORT, keepalive=60)
        mqtt_client.loop_start()
    except Exception as e:
        # 没有本机 MQTT broker 时，网页和 HTTP 上传仍然可用
        print(f"[WARN] MQTT startup failed, HTTP mode still works: {str(e)}")

    yield

    try:
        mqtt_client.loop_stop()
        mqtt_client.disconnect()
    except Exception:
        pass

app = FastAPI(title="BMS Gateway Solution", lifespan=lifespan)

@app.get("/api/status")
def get_latest_status():
    with data_lock:
        return {"bms": latest_bms_status, "gps": latest_gps_status}

@app.post("/api/control")
def send_control_cmd():
    """
    注意：这个接口只会向本机 MQTT 发控制消息。
    如果没有本地 MCU/串口桥接程序订阅 bms/control 并写入 CT511N UART，
    它不会真的让 CT511N 执行 AT+GPSSTEX。
    """
    try:
        mqtt_client.publish(TOPIC_PUB_CTRL, json.dumps({"cmd": "read_all"}), qos=0)
        mqtt_client.publish(TOPIC_PUB_CTRL, "AT+GPSSTEX", qos=0)
        return {"status": "success", "message": "control command published to MQTT"}
    except Exception as e:
        return {"status": "error", "detail": str(e)}

@app.post("/data/upload")
async def receive_data_via_http(request: Request):
    raw_body = await request.body()
    raw_text = raw_body.decode("utf-8", errors="ignore").strip()
    process_incoming_text(raw_text)
    return {"status": "success", "received": raw_text[:200]}

@app.get("/", response_class=HTMLResponse)
def show_web_page():
    html_content = """
<!DOCTYPE html>
<html>
<head>
    <title>BMS 远程监控与实时定位总线</title>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">

    <!-- 注意：
         1. 如果地图不显示，优先检查浏览器 F12 Console。
         2. 高德 Key 可能有域名白名单、Referer、服务权限限制。
         3. 境外网络/公司网络可能加载不了 webapi.amap.com。
    -->
    <script src="https://webapi.amap.com/maps?v=2.0&key=7403b2df7e7a57a5e0034df12a9eb763"></script>

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
        .grid-layout {
            display: grid;
            grid-template-columns: 280px 1fr 320px;
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
        .metric-value { font-size: 16px; font-weight: 700; font-family: monospace; margin-top: 2px; }
        #map-container {
            width: 100%;
            height: 550px;
            min-height: 550px;
            border-radius: 6px;
            border: 1px solid var(--border-color);
            background: #e5e7eb;
            display: flex;
            align-items: center;
            justify-content: center;
            color: #64748b;
            font-size: 13px;
        }
        .gps-row {
            display: flex;
            justify-content: space-between;
            padding: 8px 0;
            border-bottom: 1px solid #f1f5f9;
            font-size: 13px;
        }
        .gps-val { font-family: monospace; font-weight: 600; }
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
        #map-error {
            margin-top: 8px;
            font-size: 12px;
            color: #991b1b;
            display: none;
        }
    </style>
</head>

<body>
    <div class="dashboard">
        <header>
            <div>
                <h1>BMS 远程监控与实时定位看板</h1>
                <div style="font-size: 12px; color: var(--text-secondary); margin-top: 2px;">LTE Cat.1 双栈网关总线</div>
            </div>
            <button onclick="triggerFetch()">手动查询定位</button>
        </header>

        <div class="grid-layout">
            <div class="card">
                <div class="card-title">🔋 电芯状态指标</div>
                <div id="bms-grid" style="flex: 1; overflow-y: auto;">
                    <div style="color:var(--text-secondary); font-size:13px;">📡 等待电芯数据...</div>
                </div>
            </div>

            <div class="card" style="padding: 10px;">
                <div id="map-container">地图加载中...</div>
                <div id="map-error"></div>
            </div>

            <div class="card">
                <div class="card-title">
                    <span>📍 定位遥测元数据</span>
                    <span id="gps-badge" class="badge bg-warn">正在搜星</span>
                </div>
                <div>
                    <div class="gps-row"><span>经度 (Lng)</span><span id="val-lng" class="gps-val">0.000000</span></div>
                    <div class="gps-row"><span>纬度 (Lat)</span><span id="val-lat" class="gps-val">0.000000</span></div>
                    <div class="gps-row"><span>海拔高度</span><span id="val-high" class="gps-val">0.000 m</span></div>
                    <div class="gps-row"><span>行驶速度</span><span id="val-speed" class="gps-val">0.000 m/s</span></div>
                    <div class="gps-row"><span>有效卫星数</span><span id="val-sats" class="gps-val">0</span></div>
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
            document.getElementById('gps-badge').className = 'badge bg-error';
        }

        function initMap() {
            if (typeof AMap === 'undefined') {
                showMapError('地图加载失败：AMap 未定义。请检查高德 Key、域名白名单、HTTPS/HTTP、浏览器 Console 或网络是否能访问 webapi.amap.com。');
                document.getElementById('map-container').innerText = '地图加载失败';
                return;
            }

            try {
                document.getElementById('map-container').innerHTML = '';

                map = new AMap.Map('map-container', {
                    zoom: 15,
                    center: [114.394223, 30.516120],
                    resizeEnable: true
                });

                marker = new AMap.Marker({
                    map: map,
                    position: [114.394223, 30.516120],
                    title: 'BMS 终端位置'
                });

                mapReady = true;
            } catch (e) {
                showMapError('地图初始化失败：' + e.message);
                document.getElementById('map-container').innerText = '地图初始化失败';
            }
        }

        function renderBmsObject(obj, prefix = '') {
            let html = '';
            for (const key in obj) {
                if (typeof obj[key] === 'object' && obj[key] !== null) {
                    html += renderBmsObject(obj[key], prefix + key + '.');
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
                        bmsContainer.innerHTML = renderBmsObject(data.bms);
                    }

                    const gps = data.gps || {};
                    const isFixed = String(gps.fix_status) === "1";

                    document.getElementById('gps-badge').className = isFixed ? 'badge bg-success' : 'badge bg-warn';
                    document.getElementById('gps-badge').innerText = isFixed ? '已定位' : '未定位';

                    document.getElementById('val-lng').innerText = (gps.longitude || '0.000000') + ' °';
                    document.getElementById('val-lat').innerText = (gps.latitude || '0.000000') + ' °';
                    document.getElementById('val-high').innerText = (gps.high || '0.000') + ' m';
                    document.getElementById('val-speed').innerText = (gps.speed || '0.000') + ' m/s';
                    document.getElementById('val-sats').innerText = gps.satellites || '0';

                    if (isFixed && mapReady && marker) {
                        const rawLng = parseFloat(gps.longitude);
                        const rawLat = parseFloat(gps.latitude);

                        // 不能只允许 >0，否则西经/南纬会显示不了。这里改为合法经纬度范围。
                        if (!isNaN(rawLng) && !isNaN(rawLat) &&
                            rawLng >= -180 && rawLng <= 180 &&
                            rawLat >= -90 && rawLat <= 90 &&
                            !(rawLng === 0 && rawLat === 0)) {

                            // 高德地图在中国大陆需要 GPS/WGS84 -> GCJ02 坐标转换。
                            AMap.convertFrom([rawLng, rawLat], 'gps', function (status, result) {
                                if (status === 'complete' && result && result.locations && result.locations.length > 0) {
                                    const correctedLngLat = result.locations[0];
                                    marker.setPosition(correctedLngLat);
                                    map.panTo(correctedLngLat);
                                } else {
                                    // 如果转换失败，就直接用原始坐标，至少能显示 marker。
                                    const lnglat = [rawLng, rawLat];
                                    marker.setPosition(lnglat);
                                    map.panTo(lnglat);
                                }
                            });
                        }
                    }
                })
                .catch(err => {
                    console.error('updateDashboard error:', err);
                });
        }

        function triggerFetch() {
            fetch('/api/control', { method: 'POST' })
                .then(resp => resp.json())
                .then(data => console.log('control:', data))
                .catch(err => console.error('control error:', err));
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
