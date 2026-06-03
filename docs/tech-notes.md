# 技术答辩笔记

## 1 ms 时基
- MCU 使用 `SysTick` 以 `120 MHz / 1000 = 1 ms` 周期中断。
- `SysTick_Handler()` 不直接做 I2C / UART 重活，只累加毫秒与多个软节拍计数。
- 主循环按 `1 ms / 10 ms / 100 ms / 500 ms / 1 s` 软标志分发任务，避免在中断里阻塞总线。

## I2C 数码管扫描
- 蓝板 `TCA6424` 地址是 `0x22`。
- `P0` 连接 8 个按键输入，低有效。
- `P1` 连接 8 位段码 `A~G + DP`。
- `P2` 连接 8 位片选 `COM1~COM8`。
- 扫描策略是：
  1. 先关闭片选；
  2. 写段码到 `P1`；
  3. 打开当前位片选到 `P2`；
  4. 每 `1 ms` 切下一位。

## 8 键 + USER1/USER2
- `SW1~SW8` 来自 `TCA6424 P0`，低有效。
- `USER1/USER2` 对应红板 `PJ0/PJ1`，低有效。
- 防抖采用 `10 ms` 扫描、`3` 个稳定采样确认。
- `FUNC` 长按保存退出；`ADD` 在编辑模式下支持按住连发。

## FORMAT RIGHT 的实现思路
- 先构造左向可见字符串，例如 `12.30.45`。
- `RIGHT` 模式不是简单倒置数值字段，而是把整条“带小数点的可见字符串”按字符级反转。
- 反转后再重新解析回 `8 个字符 + dp_mask`，这样小数点会自动跟随到正确位置。
- 这也是答辩时可以直接解释“为什么 RIGHT 不是只 reverse 数字数组”的关键点。

## 串口容错
- 波特率固定 `115200 8N1`。
- 接收按行结尾解析，兼容 `\r / \n / \r\n`。
- 缓冲超长时返回 `ERROR LEN`。
- 参数缺失或字段名错误返回 `ERROR SYNTAX / PARAM`。
- 取值越界返回 `ERROR RANGE`。
- `*SET:KEY` 明确不回放 `*EVT:KEY`，避免 PC 镜像按钮形成环路。

## PC 数字孪生同步
- PC 不是自己推演显示，而是以板端主动上报为准：
  - `*EVT:DISP <8chars> <dpHex>`
  - `*EVT:LED <hex2>`
  - `*EVT:MODE <STATE>`
  - `*EVT:ALARM / *EVT:ALARM_OFF`
  - `*EVT:EDIT ...`
- 这样 PC 镜像永远跟随真实硬件，而不是“PC 自己猜现在应该显示什么”。

## 蜂鸣器映射
- 扩展板原理图里 `BEEP` 模块的控制网名是 `PWM7`。
- 对应 LaunchPad `X11` 引脚表可追到 `PK5 / M0PWM7`。
- 当前基础实现先把 `PK5` 当普通 GPIO 输出，高电平驱动有源蜂鸣器响铃。

## 当前 PC 环境结论
- `Python 3.12 64-bit`、`PyQt5`、`pyserial` 可用。
- `pyqt5-tools` 在当前 `Python 3.12` 上安装不稳定，所以当前使用：
  - `.ui` 文件保存界面结构
  - `python -m PyQt5.uic.pyuic` 生成 `ui_main.py`
- 这不影响提交与运行，只是少了 `designer.exe` 这条额外工具链。
