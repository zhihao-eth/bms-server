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
data_lock = threading.Lock()  # 读写互斥锁

MQTT_BROKER = "127.0.0.1"
MQTT_PORT = 1883
TOPIC_SUB_DATA = "bms/data"
TOPIC_PUB_CTRL = "bms/control"

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
        
        # 流式粘包自动断句清洗流
        json_blocks = re.findall(r'\{[^{}]*\}', raw_text)
        if not json_blocks:
            return

        for block in json_blocks:
            try:
                parsed_json = json.loads(block)
                # 线程安全临界区写入
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
    
    # 断开长连接
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
    """向前端暴露的北向数据总线接口"""
    with data_lock:  # 线程安全读取
        return latest_bms_status

@app.post("/api/control")
def send_control_cmd():
    """网页下行控制命令网关接口"""
    ctrl_payload = {"cmd": "read_all"}
    try:
        # 下发控制指令
        result = mqtt_client.publish(TOPIC_PUB_CTRL, json.dumps(ctrl_payload), qos=0)
        if result.rc == mqtt.MQTT_ERR_SUCCESS:
            return {"status": "success", "detail": "Control command broadcasted via MQTT."}
        else:
            return {"status": "error", "detail": f"Failed to publish, code: {result.rc}"}
    except Exception as e:
        return {"status": "error", "detail": str(e)}

@app.get("/", response_class=HTMLResponse)
def show_web_page():
    """实时可视化看板视图逻辑"""
    html_content = """
    <html>
        <head>
            <title>BMS 数据远程检测与实时控制平台</title>
            <meta charset="utf-8">
            <script>
                setInterval(function() {
                    fetch('/api/status')
                        .then(response => response.json())
                        .then(data => {
                            const container = document.getElementById('data-container');
                            if (Object.keys(data).length === 0) {
                                container.innerHTML = '<p style="color:#7f8c8d; text-align:center; font-size:16px;">📡 等待 BMS 采集终端首次上报数据...</p>';
                                return;
                            }
                            let htmlElements = '';
                            for (const key in data) {
                                const displayName = key.toUpperCase();
                                const value = data[key];
                                htmlElements += `
                                    <div style="background: #ffffff; padding: 18px 25px; margin: 12px 0; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.02); border-left: 5px solid #2980b9; display: flex; justify-content: space-between; align-items: center;">
                                        <span style="font-size: 16px; font-weight: bold; color: #34495e;">${displayName}</span>
                                        <strong style="font-size: 22px; color: #2c3e50; font-family: 'Courier New', Courier, monospace;">${value}</strong>
                                    </div>
                                `;
                            }
                            container.innerHTML = htmlElements;
                        })
                        .catch(err => console.error("Northbound API access error:", err));
                }, 1000); 

                function triggerHardwareCollect() {
                    const btn = document.getElementById('ctrl-btn');
                    btn.innerText = "⏳ 正在催促板子上传...";
                    btn.style.background = "#7f8c8d";
                    
                    fetch('/api/control', { method: 'POST' })
                        .then(response => response.json())
                        .then(res => {
                            setTimeout(() => {
                                btn.innerText = "实时获取最新数据";
                                btn.style.background = "#e67e22";
                            }, 1000);
                        })
                        .catch(err => {
                            btn.innerText = "❌ 下发失败，请重试";
                            btn.style.background = "#e74c3c";
                        });
                }
            </script>
        </head>
        <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background-color: #f4f6f9; margin: 0; padding: 40px 20px;">
            <div style="max-width: 650px; margin: 0 auto; background: white; padding: 35px; border-radius: 12px; box-shadow: 0 4px 20px rgba(0,0,0,0.06);">
                <h1 style="color: #2c3e50; text-align: center; margin-bottom: 5px; font-size: 28px;">🔋 BMS 数据远程检测平台</h1>
                <p style="color: #7f8c8d; text-align: center; margin-bottom: 25px; font-size: 14px;">流式IoT网关 · 支持 MQTT 实时交互</p>
                
                <div style="text-align: center; margin-bottom: 25px; background: #fff3e0; padding: 15px; border-radius: 8px; border: 1px dashed #f39c12;">
                    <p style="margin: 0 0 10px 0; font-size: 14px; color: #d35400; font-weight: bold;">🎮 远程控制</p>
                    <button id="ctrl-btn" onclick="triggerHardwareCollect()" style="background: #e67e22; color: white; border: none; padding: 10px 25px; font-size: 16px; font-weight: bold; border-radius: 6px; cursor: pointer; transition: background 0.2s;">
                        实时获取最新数据
                    </button>
                </div>

                <hr style="border: 0; border-top: 1px solid #eaeded; margin-bottom: 25px;">
                <div id="data-container">
                    <p style="color:#7f8c8d; text-align:center; font-size:16px;">📡 等待 BMS 采集终端首次上报数据...</p>
                </div>
            </div>
        </body>
    </html>
    """
    return html_content

# 💡 保留原有的南向 HTTP 接收路径，双栈过渡方案
@app.post("/data/upload")
async def receive_bms_data_via_http(request: Request):
    global latest_bms_status
    raw_body = await request.body()
    raw_text = raw_body.decode('utf-8', errors='ignore').strip()
    json_blocks = re.findall(r'\{[^{}]*\}', raw_text)
    if not json_blocks: 
        return {"status": "error", "msg": "No valid JSON found"}
    for block in json_blocks:
        try:
            parsed_json = json.loads(block)
            with data_lock:
                latest_bms_status = parsed_json
        except: 
            pass
    return {"status": "success", "msg": "HTTP block processed"}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8080)