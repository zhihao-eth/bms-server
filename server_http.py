from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
import uvicorn
import json
import re  # 👈 引入正则表达式库

app = FastAPI(
    title="BMS 远程数据检测平台", 
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

@app.post("/data/upload")
async def receive_bms_data(request: Request):
    global latest_bms_status
    
    # 1. 抓取原始网络流文本
    raw_body = await request.body()
    raw_text = raw_body.decode('utf-8', errors='ignore').strip()
    print(f"\n🚨【BMS硬件原始报文绝对曝光】: ---> {raw_text} <---")

    # 2. ⚡【核心升级】使用正则提取出所有符合 {...} 结构的潜伏 JSON 块
    # 即使发过来的是 "{"volt":3.85...} {"vol"，也能准确揪出前面的完整包
    json_blocks = re.findall(r'\{[^{}]*\}', raw_text)
    
    if not json_blocks:
        print("❌【未提取到任何合法的花括号结构】")
        return {"status": "error", "msg": "No valid JSON structure found"}

    success_count = 0
    # 3. 逐个尝试解析提取出来的 JSON 块
    for block in json_blocks:
        try:
            parsed_json = json.loads(block)
            # 只要解析成功一个，就立刻覆盖更新看板内存
            latest_bms_status = parsed_json
            success_count += 1
            print(f"✅【流式剥离成功】有效数据块已同步: {parsed_json}")
        except Exception as block_err:
            # 略过后面黏着的残缺包（比如 {"vol ），不让它导致整个程序报错
            print(f"⚠️【忽略残缺数据块】{block} 解析跳过，原因: {block_err}")

    if success_count > 0:
        return {"status": "success", "msg": f"Successfully processed {success_count} BMS block(s)!"}
    else:
        return {"status": "error", "msg": "All extracted blocks were invalid JSON"}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8080)