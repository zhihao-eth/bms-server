#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <windows.h> // 必须在 Windows 系统下编译运行

// 模拟的电池数据结构
typedef struct {
    float volt;
    float current;
    int soc;
    float temp;
} BMS_Data_t;

HANDLE hSerial;

// 1. 底层串口初始化（代替单片机的 HAL_UART_Init）
int UART_Init(const char* portName) {
    hSerial = CreateFile(portName, GENERIC_READ | GENERIC_WRITE, 0, NULL, OPEN_EXISTING, 0, NULL);
    if (hSerial == INVALID_HANDLE_VALUE) return 0;

    DCB dcbSerialParams = { 0 };
    dcbSerialParams.DCBlength = sizeof(dcbSerialParams);
    if (!GetCommState(hSerial, &dcbSerialParams)) return 0;

    dcbSerialParams.BaudRate = CBR_19200; // 对齐塔石模组波特率
    dcbSerialParams.ByteSize = 8;
    dcbSerialParams.StopBits = ONESTOPBIT;
    dcbSerialParams.Parity = NOPARITY;
    if (!SetCommState(hSerial, &dcbSerialParams)) return 0;

    COMMTIMEOUTS timeouts = { 0 };
    timeouts.ReadIntervalTimeout = 50;
    timeouts.ReadTotalTimeoutConstant = 50;
    timeouts.ReadTotalTimeoutMultiplier = 10;
    SetCommTimeouts(hSerial, &timeouts);
    return 1;
}

// 2. 串口发送字符串（标准 AT 指令发射器）
void UART_SendString(const char* str) {
    DWORD bytesWritten;
    WriteFile(hSerial, str, strlen(str), &bytesWritten, NULL);
    printf("🤖 [MCU发送]: %s", str); // 在电脑控制台打印回显
}

// 3. 串口发送原始数据（盲发纯 JSON，绝对不带 \r\n）
void UART_SendRawData(const char* data, int len) {
    DWORD bytesWritten;
    WriteFile(hSerial, data, len, &bytesWritten, NULL);
    printf("🤖 [MCU透传]: %.*s\n", len, data);
}

// 4. 模组网络初始化序列
void BMS_Net_Init(void) {
    printf("⚙️ 开始全自动配置 4G 模组...\n");
    UART_SendString("AT+QMTCLOSE=0\r\n"); Sleep(1000);
    
    // ==================== 【🚨 注意：请在此处修改为您自己的阿里云公网 IP】 ====================
    UART_SendString("AT+QMTOPEN=0,\"8.148.13.100\",1883\r\n"); Sleep(3000); // 等待 +QMTOPEN: 0,0
    
    UART_SendString("AT+QMTCONN=0,\"BMS_Hardware_999\"\r\n"); Sleep(2000);       // 等待 +QMTCONN: 0,0,0
    UART_SendString("AT+QMTCFG=\"recv/mode\",0,0,1\r\n"); Sleep(500);         // 开启接收模式
    UART_SendString("AT+QMTSUB=0,1,\"bms/control\",0\r\n"); Sleep(1500);       // 订阅控制柜
    printf("🚨 模组初始化完毕，进入全自动监听状态...\n");
}

// 5. 流式定长上报模拟
void BMS_Net_Report(BMS_Data_t* data) {
    char json_buffer[128];
    char cmd_buffer[64];

    // 动态拼接出 48 字节左右的标准电池 JSON
    sprintf(json_buffer, "{\"volt\":%.2f,\"current\":%.1f,\"soc\":%d,\"temp\":%.1f}",
        data->volt, data->current, data->soc, data->temp);

    int payload_len = strlen(json_buffer);

    // 步骤 A：发送定长宣告指令（根据塔石手册改用 QMTPUBEX，且 msgid 必须写 1）
    sprintf(cmd_buffer, "AT+QMTPUBEX=0,1,0,0,\"bms/data\",%d\r\n", payload_len);
    UART_SendString(cmd_buffer);

    Sleep(500); // 延时模拟单片机检测到 '>' 提示符

    // 步骤 B：盲发纯 JSON
    UART_SendRawData(json_buffer, payload_len);
}

// 6. 主循环（代替单片机的 while(1) 轮询）
int main() {
    // ==================== 【🚨 注意：请在此处修改为您模组插在电脑上的实际 COM 口】 ====================
    // 例如，如果您的模组在设备管理器中显示为 COM5，请修改为 "\\\\.\\COM5"
    const char* target_com = "\\\\.\\COM3"; 

    if (!UART_Init(target_com)) {
        printf("❌ 无法打开串口 %s，请检查：\n1. COM口数字是否正确\n2. XCOM 串口助手等其他软件是否占用了该端口并处于打开状态\n", target_com);
        system("pause");
        return -1;
    }

    BMS_Net_Init();

    char rx_buffer[2048];
    DWORD bytesRead;

    while (1) {
        // 全天候监听串口接收缓冲区
        if (ReadFile(hSerial, rx_buffer, sizeof(rx_buffer) - 1, &bytesRead, NULL) && bytesRead > 0) {
            rx_buffer[bytesRead] = '\0';
            printf("📥 [模组吐出 URC]: \n%s\n", rx_buffer);

            // 核心脱壳条件判定：捕捉北向召测命令
            if (strstr(rx_buffer, "+QMTRECV: 0,0,\"bms/control\"") != NULL &&
                strstr(rx_buffer, "\"cmd\":\"read_all\"") != NULL) {

                printf("⚡ 检测到控制命令！触发电池桩数据模拟...\n");

                // 构造业务模拟数据（桩代码）
                BMS_Data_t mock_data;
                mock_data.volt = 3.85;
                mock_data.current = 12.5;
                mock_data.soc = 95;
                mock_data.temp = 26.8;

                // 自动回弹上报
                BMS_Net_Report(&mock_data);
            }
        }
        Sleep(100); // 防止空循环死锁电脑 CPU 占用率
    }

    CloseHandle(hSerial);
    return 0;
}