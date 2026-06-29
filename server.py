from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
import uvicorn

app = FastAPI(
    title="BMS 物联网云平台", 
    description="专门接收 4G 板子数据并显示在网页上的网关系统"
)

class BatteryData(BaseModel):
    volt: float    
    current: float 
    soc: int       
    temp: float    

latest_battery_status = {"volt": 0.0, "current": 0.0, "soc": 0, "temp": 0.0}

# 【新增接口】专门给前端 JS 提供最新的 JSON 数据
@app.get("/api/status")
def get_latest_status():
    return latest_battery_status

# 修改后的网页路由，加入了 JavaScript 轮询代码
@app.get("/", response_class=HTMLResponse)
def show_web_page():
    html_content = """
    <html>
        <head>
            <title>Ethast 实时监控</title>
            <meta charset="utf-8">
            <script>
                // 每隔 1000 毫秒（1秒）执行一次拉取
                setInterval(function() {
                    fetch('/api/status')
                        .then(response => response.json())
                        .then(data => {
                            // 动态更新网页上的文字，而不需要刷新整个页面
                            document.getElementById('volt').innerText = data.volt + " V";
                            document.getElementById('current').innerText = data.current + " A";
                            document.getElementById('temp').innerText = data.temp + " °C";
                            document.getElementById('soc').innerText = data.soc + " %";
                        })
                        .catch(err => console.error("获取数据失败:", err));
                }, 1000); 
            </script>
        </head>
        <body style="font-family: Arial; text-align: center; margin-top: 50px; background-color: #f4f6f9;">
            <h1 style="color: #2c3e50;"> BMS 远程监控大屏</h1>
            <hr style="width: 50%; margin: 20px auto;">
            <div style="display: inline-block; background: white; padding: 30px; border-radius: 10px; box-shadow: 0 4px 6px rgba(0,0,0,0.1); text-align: left;">
                <p style="font-size: 24px;">当前电压: <strong id="volt" style="color: #e74c3c;">0.0 V</strong></p>
                <p style="font-size: 24px;">当前电流: <strong id="current" style="color: #2980b9;">0.0 A</strong></p>
                <p style="font-size: 24px;">电池温度: <strong id="temp" style="color: #e67e22;">0.0 °C</strong></p>
                <p style="font-size: 24px;">剩余电量: <strong id="soc" style="color: #27ae60;">0 %</strong></p>
            </div>
        </body>
    </html>
    """
    return html_content

@app.post("/")
def receive_4g_data(data: BatteryData):
    global latest_battery_status
    latest_battery_status = {
        "volt": data.volt,
        "current": data.current,
        "soc": data.soc,
        "temp": data.temp
    }
    print(f"【FastAPI 网页端收到数据】: {latest_battery_status}")
    return {"status": "success", "msg": "Ethast Server Received!"}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8080)