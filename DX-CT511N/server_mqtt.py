from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from contextlib import asynccontextmanager
import uvicorn
import json
import re
import threading  # 线程锁，保障内存数据安全
import paho.mqtt.client as mqtt

# ----------------- 核心数据区与线程安全机制 -----------------
# 维护全局上下文单例
latest_bms_status = {}
# 独立维护定位数据单例
latest_gps_status = {
    "fix_status": "0",       # 0:未定位成功, 1:定位成功
    "longitude": "0.000000", # 经度
    "latitude": "0.000000",  # 纬度
    "high": "0.000",         # 高度
    "speed": "0.000",        # 速度
    "satellites": "0"        # 参与定位卫星数
}
data_lock = threading.Lock()  # 读写互斥锁

MQTT_BROKER = "127.0.0.1"
MQTT_PORT = 1883
TOPIC_SUB_DATA = "bms/data"
TOPIC_PUB_CTRL = "bms/control"

# ----------------- DX-CT511N GNSS 报文解析器 -----------------
def parse_gnss_sentence(raw_text: str):
    """
    根据大夏龙雀 AT 串口应用指导 5.7.4 节规范解析经纬度坐标
    匹配格式 1: +GPS5TEX: <fix_status>,<module_status>,<longitude>,<high>,<latitude>,<speed>,<sta_num0>,<sta_num1>
    匹配格式 2: +GPSSTEX: ...
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
    """MQTT 连接建立回调"""
    if rc == 0:
        print(f"[INFO] MQTT client connected successfully to {MQTT_BROKER}:{MQTT_PORT}")
        client.subscribe(TOPIC_SUB_DATA, qos=0)
        print(f"[INFO] Subscribed to topic: [{TOPIC_SUB_DATA}]")
    else:
        print(f"[ERROR] MQTT connection failed with result code: {rc}")

def on_message(client, userdata, msg):
    """数据流下行报文异步处理回调"""
    global latest_bms_status
    try:
        raw_text = msg.payload.decode('utf-8', errors='ignore').strip()
        
        # 1. 优先提取并解析模组输出的 GNSS 报文
        is_gnss = parse_gnss_sentence(raw_text)
        
        # 2. 如果包含标准 JSON 块，作为常规 BMS 电池状态解析
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
        print(f"[ERROR] Exception in MQTT message callback: {str(e)}")

# 初始化 MQTT 异步客户端实例
mqtt_client = mqtt.Client(client_id="BMS_Gateway_Server")
mqtt_client.on_connect = on_connect
mqtt_client.on_message = on_message

# ----------------- FastAPI 生命周期上下文管理 -----------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        mqtt_client.connect(MQTT_BROKER, MQTT_PORT, keepalive=60)
        mqtt_client.loop_start()
        print("[INFO] Asynchronous MQTT loop background thread started.")
    except Exception as e:
        print(f"[CRITICAL] Failed to initialize MQTT background thread: {str(e)}")
    
    yield  # 服务器保持运行
    
    mqtt_client.loop_stop()
    mqtt_client.disconnect()
    print("[INFO] MQTT background thread safely terminated.")

app = FastAPI(
    title="BMS Gateway Solution",
    description="Production-grade asynchronous MQTT-HTTP dual-stack gateway.",
    lifespan=lifespan
)

# ----------------- 北向 API 路由驱动 -----------------
@app.get("/api/status")
def get_latest_status():
    """合并暴露电池基础信息与模组北向定位总线"""
    with data_lock:
        return {
            "bms": latest_bms_status,
            "gps": latest_gps_status
        }

@app.post("/api/control")
def send_control_cmd():
    """向 DX-CT511 发送数据催促指令，同时下发定位查询命令"""
    ctrl_payload = {"cmd": "read_all"}
    try:
        # 下发自定义网关业务控制指令
        mqtt_client.publish(TOPIC_PUB_CTRL, json.dumps(ctrl_payload), qos=0)
        # 下发模组查询定位指令规范 (5.7.4 节：AT+GPSSTEX)
        mqtt_client.publish(TOPIC_PUB_CTRL, "AT+GPSSTEX", qos=0)
        return {"status": "success", "detail": "BMS payload query and AT+GPSSTEX command broadcasted."}
    except Exception as e:
        return {"status": "error", "detail": str(e)}

@app.get("/", response_class=HTMLResponse)
def show_web_page():
    """全响应式专业级工业看板"""
    html_content = """
    <!DOCTYPE html>
    <html>
        <head>
            <title>BMS 远程监控与定位总线系统</title>
            <meta charset="utf-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <style>
                :root {
                    --bg-main: #f5f7fa;
                    --panel-bg: #ffffff;
                    --border-color: #e2e8f0;
                    --text-primary: #1e293b;
                    --text-secondary: #64748b;
                    --brand-color: #2563eb;
                    --brand-success: #10b981;
                    --brand-warn: #f59e0b;
                }
                body {
                    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Arial, sans-serif;
                    background-color: var(--bg-main);
                    color: var(--text-primary);
                    margin: 0;
                    padding: 24px;
                }
                .dashboard {
                    max-width: 1200px;
                    margin: 0 auto;
                }
                header {
                    display: flex;
                    justify-content: space-between;
                    align-items: center;
                    margin-bottom: 24px;
                    padding-bottom: 16px;
                    border-bottom: 1px solid var(--border-color);
                }
                h1 { font-size: 22px; margin: 0; font-weight: 600; color: #0f172a; }
                .grid-layout {
                    display: grid;
                    grid-template-columns: 2fr 1fr;
                    gap: 24px;
                }
                @media (max-width: 768px) {
                    .grid-layout { grid-template-columns: 1fr; }
                }
                .card {
                    background: var(--panel-bg);
                    border-radius: 8px;
                    border: 1px solid var(--border-color);
                    padding: 20px;
                    box-shadow: 0 1px 3px rgba(0,0,0,0.02);
                }
                .card-title {
                    font-size: 14px;
                    text-transform: uppercase;
                    letter-spacing: 0.05em;
                    color: var(--text-secondary);
                    margin-top: 0;
                    margin-bottom: 16px;
                    font-weight: 600;
                    display: flex;
                    align-items: center;
                    gap: 6px;
                }
                .metrics-grid {
                    display: grid;
                    grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
                    gap: 16px;
                }
                .metric-item {
                    background: #f8fafc;
                    border: 1px solid var(--border-color);
                    border-radius: 6px;
                    padding: 14px 16px;
                }
                .metric-label { font-size: 12px; color: var(--text-secondary); margin-bottom: 4px; }
                .metric-value { font-size: 20px; font-weight: 700; font-family: ui-monospace, monospace; }
                
                .gps-row {
                    display: flex;
                    justify-content: space-between;
                    padding: 10px 0;
                    border-bottom: 1px solid #f1f5f9;
                    font-size: 14px;
                }
                .gps-row:last-child { border: none; }
                .gps-val { font-family: ui-monospace, monospace; font-weight: 600; }
                
                .status-badge {
                    display: inline-flex;
                    align-items: center;
                    gap: 6px;
                    font-size: 12px;
                    padding: 4px 8px;
                    border-radius: 4px;
                    font-weight: 500;
                }
                .status-active { background: #ecfdf5; color: #065f46; }
                .status-inactive { background: #fef3c7; color: #92400e; }
                
                button {
                    background: var(--brand-color);
                    color: white;
                    border: none;
                    padding: 8px 16px;
                    font-size: 14px;
                    font-weight: 500;
                    border-radius: 6px;
                    cursor: pointer;
                    transition: background 0.15s;
                }
                button:hover { background: #1d4ed8; }
            </style>
            <script>
                function updateDashboard() {
                    fetch('/api/status')
                        .then(response => response.json())
                        .then(data => {
                            // 1. 更新 BMS 常规核心指标
                            const bmsContainer = document.getElementById('bms-grid');
                            if (Object.keys(data.bms).length === 0) {
                                bmsContainer.innerHTML = '<div style="color:var(--text-secondary); font-size:14px; padding:10px;">📡 等待终端首次上报电芯数据...</div>';
                            } else {
                                let html = '';
                                for (const key in data.bms) {
                                    html += `
                                        <div class="metric-item">
                                            <div class="metric-label">${key.toUpperCase()}</div>
                                            <div class="metric-value">${data.bms[key]}</div>
                                        </div>
                                    `;
                                }
                                bmsContainer.innerHTML = html;
                            }

                            // 2. 更新 GPS 定位状态栏
                            const gps = data.gps;
                            const isFixed = gps.fix_status === "1";
                            
                            document.getElementById('gps-badge').className = isFixed ? 'status-badge status-active' : 'status-badge status-inactive';
                            document.getElementById('gps-badge').innerText = isFixed ? '● 已定位 (WGS-84)' : '○ 正在搜索卫星...';
                            
                            document.getElementById('val-lng').innerText = gps.longitude + ' °';
                            document.getElementById('val-lat').innerText = gps.latitude + ' °';
                            document.getElementById('val-high').innerText = gps.high + ' m';
                            document.getElementById('val-speed').innerText = gps.speed + ' m/s';
                            document.getElementById('val-sats').innerText = gps.satellites;
                        })
                        .catch(err => console.error("Northbound API Error:", err));
                }

                function triggerFetch() {
                    const btn = document.getElementById('query-btn');
                    btn.innerText = "正在下发 AT+GPSSTEX...";
                    fetch('/api/control', { method: 'POST' })
                        .then(() => setTimeout(() => btn.innerText = "刷新模组总线", 1000));
                }

                setInterval(updateDashboard, 1000);
            </script>
        </head>
        <body>
            <div class="dashboard">
                <header>
                    <div>
                        <h1>BMS 数据远程检测与定位看板</h1>
                        <div style="font-size: 12px; color: var(--text-secondary); margin-top: 4px;">基于大夏龙雀 DX-CT511 / LTE Cat.1</div>
                    </div>
                    <button id="query-btn" onclick="triggerFetch()">刷新模组总线</button>
                </header>

                <div class="grid-layout">
                    <div class="card">
                        <div class="card-title">🔋 电池组实时电芯状态指标</div>
                        <div id="bms-grid" class="metrics-grid">
                            <div style="color:var(--text-secondary); font-size:14px; padding:10px;">📡 等待终端首次上报电芯数据...</div>
                        </div>
                    </div>

                    <div class="card">
                        <div class="card-title" style="justify-content: space-between;">
                            <span>📍 模组定位总线</span>
                            <span id="gps-badge" class="status-badge status-inactive">○ 离线</span>
                        </div>
                        <div style="margin-top: 8px;">
                            <div class="gps-row">
                                <span style="color: var(--text-secondary);">经度 (Longitude)</span>
                                <span id="val-lng" class="gps-val">0.000000 °</span>
                            </div>
                            <div class="gps-row">
                                <span style="color: var(--text-secondary);">纬度 (Latitude)</span>
                                <span id="val-lat" class="gps-val">0.000000 °</span>
                            </div>
                            <div class="gps-row">
                                <span style="color: var(--text-secondary);">海拨高度 (Altitude)</span>
                                <span id="val-high" class="gps-val">0.000 m</span>
                            </div>
                            <div class="gps-row">
                                <span style="color: var(--text-secondary);">对地速度 (Speed)</span>
                                <span id="val-speed" class="gps-val">0.000 m/s</span>
                            </div>
                            <div class="gps-row">
                                <span style="color: var(--text-secondary);">有效卫星 (Satellites)</span>
                                <span id="val-sats" class="gps-val">0</span>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </body>
    </html>
    """
    return html_content

# 💡 保留过渡方案中的南向 HTTP 报文上报清洗流
@app.post("/data/upload")
async def receive_bms_data_via_http(request: Request):
    global latest_bms_status
    raw_body = await request.body()
    raw_text = raw_body.decode('utf-8', errors='ignore').strip()
    
    # 同时在 HTTP 上报中支持 GNSS 及常规数据解析
    parse_gnss_sentence(raw_text)
    
    json_blocks = re.findall(r'\{[^{}]*\}', raw_text)
    if json_blocks:
        for block in json_blocks:
            try:
                parsed_json = json.loads(block)
                with data_lock:
                    latest_bms_status = parsed_json
            except: 
                pass
    return {"status": "success", "processed": true}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8080)