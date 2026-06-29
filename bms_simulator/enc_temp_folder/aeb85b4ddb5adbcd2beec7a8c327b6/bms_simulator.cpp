// bms_simulator.cpp : 此文件包含 "main" 函数。程序执行将在此处开始并结束。
//

#include <iostream>
#define _CRT_SECURE_NO_WARNINGS // 必须放在最顶部，用于禁用 Visual Studio 的安全函数警告 (C4996)
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

// 模拟的电池数据结构
typedef struct {
    float volt;
    float current;
    int soc;
    float temp;
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
    // 显式调用 CreateFileA，使用 ANSI 编码，解决 LPCWSTR 兼容性报错
    hSerial = CreateFileA(portName, GENERIC_READ | GENERIC_WRITE, 0, NULL, OPEN_EXISTING, 0, NULL);
    if (hSerial == INVALID_PORT) return 0;

    DCB dcbSerialParams = { 0 };
    dcbSerialParams.DCBlength = sizeof(dcbSerialParams);
    if (!GetCommState(hSerial, &dcbSerialParams)) return 0;

    dcbSerialParams.BaudRate = CBR_19200; // 对齐塔石4G模组波特率
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
    // Linux POSIX 串口初始化
    hSerial = open(portName, O_RDWR | O_NOCTTY | O_NDELAY);
    if (hSerial == INVALID_PORT) return 0;

    // 清除非阻塞标志，使 read 遵循 VMIN/VTIME 的超时机制
    fcntl(hSerial, F_SETFL, 0);

    struct termios options;
    if (tcgetattr(hSerial, &options) != 0) return 0;

    // 设置波特率为 19200
    cfsetispeed(&options, B19200);
    cfsetospeed(&options, B19200);

    // 8N1 (8 数据位, 无校验位, 1 停止位)
    options.c_cflag &= ~PARENB;
    options.c_cflag &= ~CSTOPB;
    options.c_cflag &= ~CSIZE;
    options.c_cflag |= CS8;

    // 启用接收器，设置本地连接模式
    options.c_cflag |= (CLOCAL | CREAD);

    // 设置为原始输入/输出模式 (Raw Mode)，防止系统解析退格、回车等特殊字符
    options.c_lflag &= ~(ICANON | ECHO | ECHOE | ISIG);
    options.c_oflag &= ~OPOST;

    // 设置读取超时（非阻塞模式下的最小读取字节和超时时间）
    options.c_cc[VMIN] = 0;  // 允许读取 0 字节
    options.c_cc[VTIME] = 5; // 0.5 秒无数据则超时返回

    if (tcsetattr(hSerial, TCSANOW, &options) != 0) return 0;
    return 1;
#endif
}

// 2. 串口发送字符串（标准 AT 指令发射器）
void UART_SendString(const char* str) {
#ifdef _WIN32
    DWORD bytesWritten;
    WriteFile(hSerial, str, strlen(str), &bytesWritten, NULL);
#else
    write(hSerial, str, strlen(str));
#endif
    printf("[MCU data send]: %s", str); // 在控制台打印回显
}

// 3. 串口发送原始数据（盲发纯 JSON，不带 \r\n）
void UART_SendRawData(const char* data, int len) {
#ifdef _WIN32
    DWORD bytesWritten;
    WriteFile(hSerial, data, len, &bytesWritten, NULL);
#else
    write(hSerial, data, len);
#endif
    printf("[MCU data passthrough]: %.*s\n", len, data);
}

// 4. 模组网络初始化序列
void BMS_Net_Init(void) {
    printf("Start automated configuration of the 4G module...\n");
    UART_SendString("AT+QMTCLOSE=0\r\n"); cross_sleep(1000);

    // ==================== 【在此处修改为自己服务器的公网IP】 ====================
    UART_SendString("AT+QMTOPEN=0,\"8.148.13.100\",1883\r\n"); cross_sleep(3000); // 等待 +QMTOPEN: 0,0

    UART_SendString("AT+QMTCONN=0,\"BMS_Hardware_01\"\r\n"); cross_sleep(2000);       // 等待 +QMTCONN: 0,0,0
    UART_SendString("AT+QMTCFG=\"recv/mode\",0,0,1\r\n"); cross_sleep(500);         // 开启接收模式
    UART_SendString("AT+QMTSUB=0,1,\"bms/control\",0\r\n"); cross_sleep(1500);       // 订阅控制柜
    printf("The 4G module has been initialized and is now in automatic monitoring mode....\n");
}

// 5. 流式定长上报模拟
void BMS_Net_Report(BMS_Data_t* data) {
    char json_buffer[128];
    char cmd_buffer[64];

    // 动态拼接出 49 字节左右的标准电池 JSON
    sprintf_s(json_buffer, "{\"volt\":%.2f,\"current\":%.1f,\"soc\":%d,\"temp\":%.1f}",
        data->volt, data->current, data->soc, data->temp);

    int payload_len = strlen(json_buffer)+1;

    // 步骤 A：发送定长宣告指令（根据塔石手册改用 QMTPUBEX，且 msgid 必须 write 1）
    sprintf_s(cmd_buffer, "AT+QMTPUBEX=0,1,0,0,\"bms/data\",%d\r\n", payload_len);
    UART_SendString(cmd_buffer);

    cross_sleep(500); // 延时模拟单片机检测到 '>' 提示符

    // 步骤 B：盲发纯 JSON
    UART_SendRawData(json_buffer, payload_len);
}

// 6. 主循环
int main() {
    // ==================== 【在此处修改为4G模组插在电脑上的实际端口】 ====================
    // Windows 示例: "\\\\.\\COM3"
    // Ubuntu/Linux 示例: "/dev/ttyUSB0" 或 "/dev/ttyACM0"
#ifdef _WIN32
    const char* target_com = "\\\\.\\COM3";
#else
    const char* target_com = "/dev/ttyUSB0";
#endif

    if (!UART_Init(target_com)) {
        printf("无法打开串口 %s，请检查：\n", target_com);
#ifdef _WIN32
        printf("1. COM口数字是否正确\n2. 其他串口助手软件是否占用了该端口\n");
        system("pause");
#else
        printf("1. 端口路径是否正确（例如 /dev/ttyUSB0）\n2. 当前用户是否有读写权限（可执行 sudo chmod 666 %s 临时授权）\n", target_com);
#endif
        return -1;
    }

    BMS_Net_Init();

    char rx_buffer[2048];
    int bytesRead = 0;

    while (1) {
        // 全天候监听串口接收缓冲区
#ifdef _WIN32
        DWORD dwBytesRead;
        if (ReadFile(hSerial, rx_buffer, sizeof(rx_buffer) - 1, &dwBytesRead, NULL) && dwBytesRead > 0) {
            bytesRead = (int)dwBytesRead;
#else
        bytesRead = read(hSerial, rx_buffer, sizeof(rx_buffer) - 1);
        if (bytesRead > 0) {
#endif
            rx_buffer[bytesRead] = '\0';
            printf("[The module reports a URC]: \n%s\n", rx_buffer);

            // 条件判定：捕捉北向召测命令
            if (strstr(rx_buffer, "\"bms/control\"") != NULL &&
                strstr(rx_buffer, "read_all") != NULL) {

                printf("Control command detected! Triggering battery data simulation...\n");

                // 构造业务模拟数据（桩代码）
                BMS_Data_t mock_data;
                mock_data.volt = 3.85;
                mock_data.current = 12.5;
                mock_data.soc = 95;
                mock_data.temp = 26.8;

                // 自动回弹上报
                BMS_Net_Report(&mock_data);
            }
#ifdef _WIN32
        }
#else
        }
#endif
        cross_sleep(100); // 防止空循环死锁电脑 CPU 占用率
        }

#ifdef _WIN32
    CloseHandle(hSerial);
#else
    close(hSerial);
#endif
    return 0;
    }

// 运行程序: Ctrl + F5 或调试 >“开始执行(不调试)”菜单
// 调试程序: F5 或调试 >“开始调试”菜单

// 入门使用技巧: 
//   1. 使用解决方案资源管理器窗口添加/管理文件
//   2. 使用团队资源管理器窗口连接到源代码管理
//   3. 使用输出窗口查看生成输出和其他消息
//   4. 使用错误列表窗口查看错误
//   5. 转到“项目”>“添加新项”以创建新的代码文件，或转到“项目”>“添加现有项”以将现有代码文件添加到项目
//   6. 将来，若要再次打开此项目，请转到“文件”>“打开”>“项目”并选择 .sln 文件
