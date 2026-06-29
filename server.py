from fastapi import FastAPI
from pydantic import BaseModel
import uvicorn

# 1. 创建一个网页服务器实例
app = FastAPI(
    title="BMS 物联网云平台", 
    description="专门接收 4G 板子数据并显示在网页上的网关系统"
)

# 2. 规定你的 4G 板子必须发送什么格式的电池数据（必须是 JSON）
class BatteryData(BaseModel):
    volt: float    # 电池电压
    current: float # 电池电流
    soc: int       # 电池剩余电量百分比

# 内存临时数据库：用来存最新收到的电池数据，以便网页显示
latest_battery_status = {"volt": 0.0, "current": 0.0, "soc": 0}

# 3. 网页路由 1：浏览器直接访问 IP 时，显示最新的电池状态
@app.get("/")
def show_web_page():
    # 这里我们返回一个简单的 HTML 网页，让它在浏览器里长得漂亮一点
    html_content = f"""
    <html>
        <head><title>Ethast 实时监控</title></head>
        <body style="font-family: Arial; text-align: center; margin-top: 50px; background-color: #f4f6f9;">
            <h1 style="color: #2c3e50;">🔋 Ethast BMS 远程监控大屏</h1>
            <hr style="width: 50%; margin: 20px auto;">
            <div style="display: inline-block; background: white; padding: 30px; border-radius: 10px; box-shadow: 0 4px 6px rgba(0,0,0,0.1);">
                <p style="font-size: 24px;">⚡ 当前电压: <strong style="color: #e74c3c;">{latest_battery_status['volt']} V</strong></p>
                <p style="font-size: 24px;">🔌 当前电流: <strong style="color: #2980b9;">{latest_battery_status['current']} A</strong></p>
                <p style="font-size: 24px;">📊 剩余电量: <strong style="color: #27ae60;">{latest_battery_status['soc']} %</strong></p>
            </div>
            <p style="color: #7f8c8d; margin-top: 30px;">数据每 10 秒由 4G 硬件板子自动无线刷新</p>
        </body>
    </html>
    """
    from fastapi.responses import HTMLResponse
    return HTMLResponse(content=html_content)

# 4. 网页路由 2：供 4G 板子（或模拟器）无线打入数据的接口
@app.post("/upload")
def receive_4g_data(data: BatteryData):
    global latest_battery_status
    # 把收到的数据更新到全局变量里
    latest_battery_status = {
        "volt": data.volt,
        "current": data.current,
        "soc": data.soc
    }
    print(f"【FastAPI 网页端收到数据】: {latest_battery_status}")
    return {"status": "success", "msg": "Ethast Server Received!"}

if __name__ == "__main__":
    # 让服务器在 8080 端口跑起来
    uvicorn.run(app, host="0.0.0.0", port=8080)