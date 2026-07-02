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
    "fix_status": "0",       # 0:未定位成功, 1:定位成功 [cite: 800]
    "longitude": "0.000000", # 经度 [cite: 800]
    "latitude": "0.000000",  # 纬度 [cite: 800]
    "high": "0.000",         # 高度 [cite: 800]
    "speed": "0.000",        # 速度 [cite: 800]
    "satellites": "0"        # 参与定位卫星数 [cite: 800]
}
data_lock = threading.Lock()  

MQTT_BROKER = "127.0.0.1"
MQTT_PORT = 1883
TOPIC_SUB_DATA = "bms/data"
TOPIC_PUB_CTRL = "bms/control"

# ----------------- DX-CT511N GNSS 报文解析器 -----------------
def parse_gnss_sentence(raw_text: str):
    """
    根据大夏龙雀 AT 串口应用指导 5.7.4 节规范解析经纬度坐标 [cite: 800]
    支持形式如：+GPS5TEX: 1, 1, 113.831385, 12.166000, 22.606304... [cite: 812]
    """
    global latest_gps_status
    match = re.search(r'\+GPS(?:5|S)TEX:\s*([\d\.]+),\s*([\d\.]+),\s*([\d\.]+),\s*([\d\.]+),\s*([\d\.]+),\s*([\d\.]+),\s*([\d\.]+),\s*([\d\.]+)', raw_text)
    if match:
        with data_lock:
            latest_gps_status["fix_status"] = match.group(1)
            latest_gps_status["longitude"] = match.group(3)
            latest_gps_status["high"] = match.group(4)
            latest_gps_status["latitude"] = match.group(5)
            latest_gps_status["speed"] = match.group(6)
            latest_gps_status["satellites"] = match.group(8)
        return True
    return False

# ----------------- MQTT 协议栈回调驱动 -----------------
def on_connect(client, userdata, flags, rc):
    if rc == 0:
        print(f"[INFO] MQTT Connected to {MQTT_BROKER}")
        client.subscribe(TOPIC_SUB_DATA, qos=0)
    else:
        print(f"[ERROR] Connection failed: {rc}")

def on_message(client, userdata, msg):
    global latest_bms_status
    try:
        raw_text = msg.payload.decode('utf-8', errors='ignore').strip()
        
        # 提取 GPS 数据
        parse_gnss_sentence(raw_text)
        
        # 提取 JSON 常规 BMS 块
        json_blocks = re.findall(r'\{[^{}]*\}', raw_text)
        if json_blocks:
            for block in json_blocks:
                try:
                    parsed_json = json.loads(block)
                    with data_lock:
                        latest_bms_status = parsed_json
                except json.JSONDecodeError:
                    continue
    except Exception as e:
        print(f"[ERROR] msg callback error: {str(e)}")

mqtt_client = mqtt.Client(client_id="BMS_Gateway_Server")
mqtt_client.on_connect = on_connect
mqtt_client.on_message = on_message

@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        mqtt_client.connect(MQTT_BROKER, MQTT_PORT, keepalive=60)
        mqtt_client.loop_start()
    except Exception as e:
        print(f"[CRITICAL] MQTT startup error: {str(e)}")
    yield  
    mqtt_client.loop_stop()
    mqtt_client.disconnect()

app = FastAPI(title="BMS Gateway Solution", lifespan=lifespan)

@app.get("/api/status")
def get_latest_status():
    with data_lock:
        return {"bms": latest_bms_status, "gps": latest_gps_status}

@app.post("/api/control")
def send_control_cmd():
    try:
        mqtt_client.publish(TOPIC_PUB_CTRL, json.dumps({"cmd": "read_all"}), qos=0)
        mqtt_client.publish(TOPIC_PUB_CTRL, "AT+GPSSTEX", qos=0) # 5.7.4 节规范指令 [cite: 800]
        return {"status": "success"}
    except Exception as e:
        return {"status": "error", "detail": str(e)}

@app.get("/", response_class=HTMLResponse)
def show_web_page():
    html_content = """
    <!DOCTYPE html>
    <html>
        <head>
            <title>BMS 远程监控与实时定位总线</title>
            <meta charset="utf-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <script type="text/javascript" src="https://webapi.amap.com/maps?v=2.0&key=7403b2df7e7a57a5e0034df12a9eb763"></script>
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
                .metric-value { font-size: 16px; font-weight: 700; font-family: monospace; margin-top: 2px;}
                #map-container {
                    width: 100%;
                    height: 550px;
                    border-radius: 6px;
                    border: 1px solid var(--border-color);
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
                    font-size: 11px; padding: 2px 6px; border-radius: 3px; font-weight: 500;
                }
                .bg-success { background: #d1fae5; color: #065f46; }
                .bg-warn { background: #fef3c7; color: #92400e; }
                button {
                    background: var(--brand-color); color: white; border: none;
                    padding: 6px 12px; font-size: 13px; border-radius: 4px; cursor: pointer;
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
                        <div id="map-container"></div>
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
                // 初始化高德地图
                var map = new AMap.Map('map-container', {
                    zoom: 15,
                    center: [113.831385, 22.606304] // 默认定位至说明书示例的深圳宝安航城航空路工业园 [cite: 17, 812]
                });

                // 创建专用的实时轨迹标记图标
                var marker = new AMap.Marker({
                    map: map,
                    position: [113.831385, 22.606304],
                    title: 'BMS 终端位置'
                });

                function updateDashboard() {
                    fetch('/api/status')
                        .then(response => response.json())
                        .then(data => {
                            // 更新 BMS 信息
                            const bmsContainer = document.getElementById('bms-grid');
                            if (Object.keys(data.bms).length > 0) {
                                let html = '';
                                for (const key in data.bms) {
                                    html += `<div class="metric-item">
                                                <div class="metric-label">${key.toUpperCase()}</div>
                                                <div class="metric-value">${data.bms[key]}</div>
                                             </div>`;
                                }
                                bmsContainer.innerHTML = html;
                            }

                            // 更新定位遥测元数据
                            const gps = data.gps;
                            const isFixed = gps.fix_status === "1";
                            
                            document.getElementById('gps-badge').className = isFixed ? 'badge bg-success' : 'badge bg-warn';
                            document.getElementById('gps-badge').innerText = isFixed ? '已定位' : '未定位';
                            
                            document.getElementById('val-lng').innerText = gps.longitude;
                            document.getElementById('val-lat').innerText = gps.latitude;
                            document.getElementById('val-high').innerText = gps.high + ' m';
                            document.getElementById('val-speed').innerText = gps.speed + ' m/s';
                            document.getElementById('val-sats').innerText = gps.satellites;

                            // 当定位成功时，无缝通过高德内置算法完成标准的 WGS-84 坐标纠偏并平滑平移
                            if (isFixed) {
                                var rawLng = parseFloat(gps.longitude);
                                var rawLat = parseFloat(gps.latitude);
                                
                                if (!isNaN(rawLng) && !isNaN(rawLat) && rawLng > 0 && rawLat > 0) {
                                    // 使用高德官方插件完成 WGS84 -> GCJ02(火星坐标系) 的硬件级精准纠偏
                                    AMap.convertFrom([rawLng, rawLat], 'gps', function (status, result) {
                                        if (result && result.locations) {
                                            var correctedLngLat = result.locations[0];
                                            marker.setPosition(correctedLngLat); // 刷新图层标记点位置
                                            map.panTo(correctedLngLat);         // 地图中心平滑移至设备处
                                        }
                                    });
                                }
                            }
                        });
                }

                function triggerFetch() {
                    fetch('/api/control', { method: 'POST' });
                }

                setInterval(updateDashboard, 1000); // 1秒高频同步刷新
            </script>
        </body>
    </html>
    """
    return html_content

@app.post("/data/upload")
async def receive_bms_data_via_http(request: Request):
    global latest_bms_status
    raw_body = await request.body()
    raw_text = raw_body.decode('utf-8', errors='ignore').strip()
    parse_gnss_sentence(raw_text)
    json_blocks = re.findall(r'\{[^{}]*\}', raw_text)
    if json_blocks:
        for block in json_blocks:
            try:
                with data_lock:
                    latest_bms_status = json.loads(block)
            except: pass
    return {"status": "success"}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8080)