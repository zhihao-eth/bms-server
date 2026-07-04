#include <iostream>
#define _CRT_SECURE_NO_WARNINGS // 禁用 Visual Studio 的安全函数警告 (C4996)
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#ifdef _WIN32
#include <windows.h> // Windows 串口及延时库
typedef HANDLE port_t;
#define INVALID_PORT INVALID_HANDLE_VALUE
#else
#include <unistd.h>  // Linux 标准系统调用
#include <fcntl.h>   // 文件控制定义
#include <termios.h> // POSIX 终端控制定义
#include <errno.h>
typedef int port_t;
#define INVALID_PORT -1
#endif

// 模拟的电池数据结构（对应网页左侧需要渲染的 voltage, current, soc, temperature 字段）
typedef struct {
    float voltage;
    float current;
    int soc;
    float temperature;
} BMS_Data_t;

port_t hSerial = INVALID_PORT;

// 跨平台延时函数 (单位: 毫秒)
void cross_sleep(int ms) {
#ifdef _WIN32
    Sleep(ms);
#else
    usleep(ms * 1000);
#endif
}

// 1. 底层串口初始化（兼容 Windows / Linux）
int UART_Init(const char* portName) {
#ifdef _WIN32
    hSerial = CreateFileA(portName, GENERIC_READ | GENERIC_WRITE, 0, NULL, OPEN_EXISTING, 0, NULL);
    if (hSerial == INVALID_PORT) return 0;

    DCB dcbSerialParams = { 0 };
    dcbSerialParams.DCBlength = sizeof(dcbSerialParams);
    if (!GetCommState(hSerial, &dcbSerialParams)) return 0;

    // DX-CT511N 默认串口参数：115200, 8, N, 1
    dcbSerialParams.BaudRate = CBR_115200;
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
#else
    hSerial = open(portName, O_RDWR | O_NOCTTY | O_NDELAY);
    if (hSerial == INVALID_PORT) return 0;

    fcntl(hSerial, F_SETFL, 0);

    struct termios options;
    if (tcgetattr(hSerial, &options) != 0) return 0;

    // 设置波特率为 115200
    cfsetispeed(&options, B115200);
    cfsetospeed(&options, B115200);

    options.c_cflag &= ~PARENB;
    options.c_cflag &= ~CSTOPB;
    options.c_cflag &= ~CSIZE;
    options.c_cflag |= CS8;
    options.c_cflag |= (CLOCAL | CREAD);
    options.c_lflag &= ~(ICANON | ECHO | ECHOE | ISIG);
    options.c_oflag &= ~OPOST;

    options.c_cc[VMIN] = 0;
    options.c_cc[VTIME] = 5;

    if (tcsetattr(hSerial, TCSANOW, &options) != 0) return 0;
    return 1;
#endif
}

// 2. 串口发送字符串（标准 AT 指令发射器）
void UART_SendString(const char* str) {
#ifdef _WIN32
    DWORD bytesWritten;
    WriteFile(hSerial, str, (DWORD)strlen(str), &bytesWritten, NULL);
#else
    write(hSerial, str, strlen(str));
#endif
    printf("[MCU send AT]: %s", str); // 在控制台打印回显
}

// 4. DX-CT511N 网络与 MQTT 初始化序列
void BMS_Net_Init(void) {
    printf("Start automated configuration of DX-CT511N module...\n");

    // 基础防卡死清理
    UART_SendString("AT\r\n"); cross_sleep(300);
    UART_SendString("AT+MDISCONNECT\r\n"); cross_sleep(500); // 挂断旧 MQTT 连接
    UART_SendString("AT+MIPCLOSE\r\n"); cross_sleep(500);    // 关闭旧 Socket

    // 基础网络连接
    UART_SendString("AT+QICSGP=1,1,\"\",\"\",\"\"\r\n"); cross_sleep(500);
    UART_SendString("AT+NETOPEN\r\n"); cross_sleep(3000);                  // 打开网络，等待成功
    UART_SendString("AT+CEREG?\r\n"); cross_sleep(300);
    UART_SendString("AT+CEREG=0\r\n"); cross_sleep(300);                  // 关闭主动上报

    // MQTT 连接与订阅流程
    UART_SendString("AT+MCONFIG=\"BMS_0001\"\r\n"); cross_sleep(500); // 设置 Client ID

    // ==================== 【公网Broker地址：8.148.13.100:1883】 ====================
    UART_SendString("AT+MIPSTART=\"8.148.13.100\",1883\r\n"); cross_sleep(2500);
    UART_SendString("AT+MCONNECT=1,60\r\n"); cross_sleep(2000);

    // 必须订阅网页控制 Topic：bms/control
    UART_SendString("AT+MSUB=\"bms/control\",0\r\n"); cross_sleep(1000);

    // 可选：初始化有源 GPS 天线及核心引擎（依据说明书第七章）
    printf("Initializing GPS engine...\n");
    UART_SendString("AT+CGDRT=12,1\r\n"); cross_sleep(200);
    UART_SendString("AT+CGSETV=12,1\r\n"); cross_sleep(200);
    UART_SendString("AT+CGGETV=12\r\n"); cross_sleep(200);
    UART_SendString("AT+MGPSC=1\r\n"); cross_sleep(500);    // 启动GPS硬件核心
    UART_SendString("AT+AGNSSGET=pos.asrmicro.com\r\n"); cross_sleep(500);
    UART_SendString("AT+AGNSSSET\r\n"); cross_sleep(200);
    UART_SendString("AT+GPSMODE=1\r\n"); cross_sleep(200);
    UART_SendString("AT+MGPSGET=ALL,0\r\n"); cross_sleep(200); // 关闭GPS串口刷屏

    printf("The DX-CT511N module is now ready and listening for commands...\n");
}

// 5. 按照规范上报 BMS 数据 (直接内嵌在 AT+MPUB 内发送)
void BMS_Net_Report_Data(BMS_Data_t* data) {
    char cmd_buffer[512];

    // DX-CT511N 不需要先发定长宣告。直接把转义后的 JSON 嵌入 AT+MPUB 指令中一行发完。
    // 注意：转义后的双引号在 C 语言中是 \\\"，网页接收的键名必须与网页要求的 lowercase 匹配
    sprintf_s(cmd_buffer, "AT+MPUB=\"bms/data\",0,0,\"{\\\"type\\\":\\\"bms\\\",\\\"voltage\\\":%.1f,\\\"current\\\":%.1f,\\\"soc\\\":%d,\\\"temperature\\\":%.1f}\"\r\n",
        data->voltage, data->current, data->soc, data->temperature);

    UART_SendString(cmd_buffer);
}

// 6. 按照规范模拟上报 GPS 原始扩展行数据
void BMS_Net_Report_GPS(void) {
    char cmd_buffer[512];

    // 直接通过 MQTT 盲发DX-CT511N识别的标准 +GPSSTEX 行数据
    // 格式: +GPSSTEX: fix_status, 1, longitude, high, latitude, speed, sats_seen, sats_used
    // 网页渲染需要有效定位，这里模拟有效定位数据
    sprintf_s(cmd_buffer, "AT+MPUB=\"bms/data\",0,0,\"+GPSSTEX: 1, 1, 114.394170, 15.598, 30.515838, 0.466, 28, 19\"\r\n");

    UART_SendString(cmd_buffer);
}

// 7. 主循环
int main() {
    // 根据实际连接模组的端口进行修改
#ifdef _WIN32
    const char* target_com = "\\\\.\\COM3";
#else
    const char* target_com = "/dev/ttyUSB0";
#endif

    if (!UART_Init(target_com)) {
        printf("Unable to open serial port %s，please check the port configuration or whether the port is in use.\n", target_com);
#ifdef _WIN32
        system("pause");
#endif
        return -1;
    }

    BMS_Net_Init();

    char rx_buffer[2048];
    int bytesRead = 0;

    while (1) {
        // 监听串口接收缓冲区
#ifdef _WIN32
        DWORD dwBytesRead;
        if (ReadFile(hSerial, rx_buffer, sizeof(rx_buffer) - 1, &dwBytesRead, NULL) && dwBytesRead > 0) {
            bytesRead = (int)dwBytesRead;
#else
        bytesRead = read(hSerial, rx_buffer, sizeof(rx_buffer) - 1);
        if (bytesRead > 0) {
#endif
            rx_buffer[bytesRead] = '\0';
            printf("\n[Received from Module]: \n%s\n", rx_buffer);

            // 【判定条件 1】：捕捉网页点击“更新 BMS 数据”下发的控制指令
            // 网站 FastAPI 发出的 Payload 为 "REQ_BMS_UPDATE"
            if (strstr(rx_buffer, "REQ_BMS_UPDATE") != NULL) {
                printf(">>> Web BMS query request detected! Reporting telemetry...\n");

                BMS_Data_t mock_data;
                mock_data.voltage = 52.3;
                mock_data.current = 1.8;
                mock_data.soc = 76;
                mock_data.temperature = 31.5;

                cross_sleep(200);
                BMS_Net_Report_Data(&mock_data);
            }

            // 【判定条件 2】：捕捉网页点击“更新 GPS 定位”下发的控制指令
            // 网站 FastAPI 发出的 Payload 为 "REQ_GPS_UPDATE"
            if (strstr(rx_buffer, "REQ_GPS_UPDATE") != NULL) {
                printf(">>> Web GPS query request detected! Reporting coordinate lines...\n");

                cross_sleep(200);
                UART_SendString("AT+GPSSTEX\r\n"); // 驱动物理模组执行卫星解算
                UART_SendString("AT+MGPSGET=ALL,0\r\n"); cross_sleep(200); // 关闭GPS串口刷屏
            }

            //  【判定条件 3】：捕捉上传的“ GPS 定位”
            //  模组返回真实 GPS 报文 "+GPSSTEX: ..." -> MCU 捕获并推给 MQTT
            if (strstr(rx_buffer, "+GPSSTEX:") != NULL && strstr(rx_buffer, "AT+MPUB") == NULL) {
                printf(">>> Found raw GPS data from module! Routing to MQTT...\n");

                char* gps_line = strstr(rx_buffer, "+GPSSTEX:");
                if (gps_line != NULL) {
                    // 寻找当前行结束的换行符
                    char* line_end = strpbrk(gps_line, "\r\n");
                    if (line_end != NULL) {
                        *line_end = '\0'; // 直接在换行符位置写入结束符。
                    }

                    char cmd_buffer[512];
                    // gps_line 单行，安全组装
                    sprintf_s(cmd_buffer, "AT+MPUB=\"bms/data\",0,0,\"%s\"\r\n", gps_line);

                    cross_sleep(200);
                    UART_SendString(cmd_buffer);
                }
            }


#ifdef _WIN32
        }
#else
        }
#endif
        cross_sleep(100);
    }

#ifdef _WIN32
    CloseHandle(hSerial);
#else
    close(hSerial);
#endif
    return 0;
}
