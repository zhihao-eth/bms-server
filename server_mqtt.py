from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
import uvicorn
import json
import re
import paho.mqtt.client as mqtt  # 👈 引入 MQTT 库

app = FastAPI(
    title="BMS 远程数据检测与实时控制平台", 
    description="支持 MQTT 双向实时通信的全动态自适应网关系统"
)

# 内存中保存的最新的 BMS 数据字典
latest_bms_status = {}

# 🚀 MQTT 配置参数
MQTT_BROKER = "127.0.0.1"       # 如果你的 MQTT Broker 装在当前阿里云上，写 127.0.0.1 即可
MQTT_PORT = 1883
TOPIC_SUB_DATA = "bms/data"     # 监听硬件上报数据的主题
TOPIC_PUB_CTRL = "bms/control"   # 向硬件下发控制命令的主题

# ----------------- ⚡ MQTT 后台监听核心逻辑平移 -----------------

def on_connect(client, userdata, flags, rc):
    print(f"📡 【MQTT 连接状态】: 成功连接到 Broker, 返回码: {rc}")
    # 连接成功后，立刻订阅硬件数据上报的主题
    client.subscribe(TOPIC_SUB_DATA)
    print(f"✅ 成功订阅数据通道主题: [{TOPIC_SUB_DATA}]")

def on_message(client, userdata, msg):
    global latest_bms_status
    try:
        # 1. 抓取 MQTT 管道传过来的硬件原始报文文本
        raw_text = msg.payload.decode('utf-8', errors='ignore').strip()
        print(f"\n🚨【BMS硬件流式报文绝对曝光】(MQTT): ---> {raw_text} <---")

        # 2. ⚡【完美平移你写的正则清洗器】使用正则抓取符合 {...} 结构的所有数据块，抗粘包
        json_blocks = re.findall(r'\{[^{}]*\}', raw_text)
        
        if not json_blocks:
            print("❌【MQTT 未提取到任何合法的花括号结构】")
            return

        # 3. 逐个尝试解析提取出来的 JSON 块
        for block in json_blocks:
            try:
                parsed_json = json.loads(block)
                # 解析成功，立刻同步至全局内存字典，网页端 1 秒内自动跳活
                latest_bms_status = parsed_json
                print(f"✅【MQTT流式剥离成功】有效数据已同步: {parsed_json}")
            except Exception as block_err:
                print(f"⚠️【MQTT忽略残缺块】{block} 解析跳过，原因: {block_err}")
                
    except Exception as e:
        print(f"❌【MQTT 接收线程发生未知错误】: {e}")

# 启动并配置后台 MQTT 客户端实例
mqtt_client = mqtt.Client(client_id="BMS_Cloud_Server")
mqtt_client.on_connect = on_connect
mqtt_client.on_message = on_message

@app.on_event("startup")
def startup_mqtt():
    try:
        mqtt_client.connect(MQTT_BROKER, MQTT_PORT, 60)
        mqtt_client.loop_start()  # 👈 核心：启动独立的后台非阻塞线程，全天候守候硬件数据
        print("🚀 MQTT 后台流式监听线程已成功拉起！")
    except Exception as e:
        print(f"❌ 启动 MQTT 监听失败，请检查服务器是否安装并启动了 Mosquitto/EMQX Broker。原因: {e}")

# -------------------------------------------------------------

@app.get("/api/status")
def get_latest_status():
    return latest_bms_status

# 🚀 【新增控制接口】供前端网页按钮调用，将指令打包从 MQTT 高架桥主动推给板子
@app.post("/api/control")
def send_control_cmd():
    ctrl_payload = {"cmd": "read_all"}
    # 将字典转成 JSON 字符串，并砸向 bms/control 主题
    mqtt_client.publish(TOPIC_PUB_CTRL, json.dumps(ctrl_payload))
    print(f"📢 【网页按钮触发】已向硬件下发遥控指令: {ctrl_payload}")
    return {"status": "success", "msg": "Control command pushed via MQTT!"}

@app.get("/", response_class=HTMLResponse)
def show_web_page():
    html_content = """
    <html>
        <head>
            <title>BMS 数据远程检测与实时控制平台</title>
            <meta charset="utf-8">
            <script>
                // 1. 定时刷新大屏卡片逻辑（完美保留）
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
                        .catch(err => console.error("读取BMS数据失败:", err));
                }, 1000); 

                // 2. ⚡【新增函数】点击按钮时，调用后端的控制 API
                function triggerHardwareCollect() {
                    const btn = document.getElementById('ctrl-btn');
                    btn.innerText = "⏳ 正在催促板子上传...";
                    btn.style.background = "#7f8c8d";
                    
                    fetch('/api/control', { method: 'POST' })
                        .then(response => response.json())
                        .then(res => {
                            console.log("命令下发成功:", res);
                            setTimeout(() => {
                                btn.innerText = "⚡ 实时召唤最新数据";
                                btn.style.background = "#e67e22";
                            }, 1000);
                        })
                        .catch(err => {
                            console.error("下发失败:", err);
                            btn.innerText = "❌ 下发失败，重试";
                            btn.style.background = "#e74c3c";
                        });
                }
            </script>
        </head>
        <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background-color: #f4f6f9; margin: 0; padding: 40px 20px;">
            <div style="max-width: 650px; margin: 0 auto; background: white; padding: 35px; border-radius: 12px; box-shadow: 0 4px 20px rgba(0,0,0,0.06);">
                <h1 style="color: #2c3e50; text-align: center; margin-bottom: 5px; font-size: 28px;">🔋 BMS 数据远程检测平台</h1>
                <p style="color: #7f8c8d; text-align: center; margin-bottom: 25px; font-size: 14px;">流式物联网网关 · 支持 MQTT 双向实时自适应交互</p>
                
                <div style="text-align: center; margin-bottom: 25px; background: #fff3e0; padding: 15px; border-radius: 8px; border: 1px dashed #f39c12;">
                    <p style="margin: 0 0 10px 0; font-size: 14px; color: #d35400; font-weight: bold;">🎮 远程双向控制器</p>
                    <button id="ctrl-btn" onclick="triggerHardwareCollect()" style="background: #e67e22; color: white; border: none; padding: 10px 25px; font-size: 16px; font-weight: bold; border-radius: 6px; cursor: pointer; transition: background 0.2s;">
                        ⚡ 实时召唤最新数据
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

# 💡 保留原有的 HTTP 接收路径，实现“双模共存”，方便你平滑过渡调试
@app.post("/data/upload")
async def receive_bms_data_via_http(request: Request):
    global latest_bms_status
    raw_body = await request.body()
    raw_text = raw_body.decode('utf-8', errors='ignore').strip()
    print(f"\n🚨【BMS硬件原始报文绝对曝光】(HTTP): ---> {raw_text} <---")
    json_blocks = re.findall(r'\{[^{}]*\}', raw_text)
    if not json_blocks: return {"status": "error", "msg": "No valid JSON found"}
    for block in json_blocks:
        try:
            latest_bms_status = json.loads(block)
        except: pass
    return {"status": "success", "msg": "HTTP block processed"}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8080)