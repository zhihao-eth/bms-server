from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
import uvicorn
import json

app = FastAPI(
    title="BMS 远程数据检测网关", 
    description="通用型 BMS 电池管理系统数据接收与全动态自适应看板"
)

# 内存中保存的最新的 BMS 数据字典
latest_bms_status = {}

@app.get("/api/status")
def get_latest_status():
    return latest_bms_status

@app.get("/", response_class=HTMLResponse)
def show_web_page():
    html_content = """
    <html>
        <head>
            <title>BMS 数据远程检测平台</title>
            <meta charset="utf-8">
            <script>
                // 每隔 1000 毫秒（1秒）全自动拉取一次最新数据
                setInterval(function() {
                    fetch('/api/status')
                        .then(response => response.json())
                        .then(data => {
                            const container = document.getElementById('data-container');
                            
                            // 如果服务器还没有收到任何硬件上报的数据
                            if (Object.keys(data).length === 0) {
                                container.innerHTML = '<p style="color:#7f8c8d; text-align:center; font-size:16px;">📡 等待 BMS 采集终端首次上报数据...</p>';
                                return;
                            }
                            
                            // 动态扫描 JSON 数据包中的所有键值对，全自动生成精美检测卡片
                            let htmlElements = '';
                            for (const key in data) {
                                // 将键名美化大写（例如 volt 变成 VOLT）
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
            </script>
        </head>
        <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background-color: #f4f6f9; margin: 0; padding: 40px 20px;">
            <div style="max-width: 650px; margin: 0 auto; background: white; padding: 35px; border-radius: 12px; box-shadow: 0 4px 20px rgba(0,0,0,0.06);">
                <h1 style="color: #2c3e50; text-align: center; margin-bottom: 5px; font-size: 28px;">🔋 BMS 数据远程检测平台</h1>
                <p style="color: #7f8c8d; text-align: center; margin-bottom: 25px; font-size: 14px;">流式物联网网关 · 自适应任意动态数据包解析</p>
                <hr style="border: 0; border-top: 1px solid #eaeded; margin-bottom: 25px;">
                
                <div id="data-container">
                    <p style="color:#7f8c8d; text-align:center; font-size:16px;">📡 等待 BMS 采集终端首次上报数据...</p>
                </div>
            </div>
        </body>
    </html>
    """
    return html_content

# 🚀 核心路由：使用通用的二进制流截获，100% 免疫 422 校验报错
@app.post("/")
async def receive_bms_data(request: Request):
    global latest_bms_status
    
    # 强行抓取 4G 模组透传过来的原生态报文文本
    raw_body = await request.body()
    raw_text = raw_body.decode('utf-8', errors='ignore').strip()
    
    print(f"\n🚨【BMS硬件原始报文绝对曝光】: ---> {raw_text} <---")

    try:
        # 只要是合法的 JSON 报文（无论里面包含多少个 BMS 参数，参数命名是什么），全部一键全解出来
        parsed_json = json.loads(raw_text)
        
        # 完整更新内存数据字典
        latest_bms_status = parsed_json
        
        print(f"✅【BMS解析成功】已同步至数据看板: {latest_bms_status}")
        return {"status": "success", "msg": "BMS Data Received!"}
        
    except Exception as e:
        print(f"❌【非标准JSON报文】错误原因: {e}")
        return {"status": "error", "msg": "Invalid JSON format"}

if __name__ == "__main__":
    # 监听全网络端口，占用 8080 端口
    uvicorn.run(app, host="0.0.0.0", port=8080)