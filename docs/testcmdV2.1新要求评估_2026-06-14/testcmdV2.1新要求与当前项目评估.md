# testcmdV2.1 新要求与当前项目真机评估

> **状态更新（2026-06-15）：本文件保留修复前基线。**
> 修复后老师原版测试已达到 `10.0/10.0`、`TIMEOUT 0`，详见
> [修复后复测/串口协议修复_进度与对接_2026-06-15.md](修复后复测/串口协议修复_进度与对接_2026-06-15.md)。

- 最后更新：2026-06-14
- 评估对象：`真正的最新版`
- 真机串口：Stellaris Virtual Serial Port (`COM5`)
- 板端版本事件：`*EVT:DISP V2_2____ 04`
- 工作边界：除本 `docs` 评估目录外，工程代码、配置和构建产物均只读

## 1. 结论先行

1. **老师原版 100 条命令已在 COM5 真机执行完成，自动评分为 `4.5/10.0`。**
   - 统计：`OK 70`、`ERROR 29`、`TIMEOUT 1`
   - 原始日志和评分报告已保存到 `真机实测/`

2. **最大问题是多参数语法与老师要求相反。**
   - 老师要求：`HOUR MIN SEC 12 30 45`
   - 当前实现：`HOUR 12 MIN 30 SEC 45`
   - 所有老师格式的日期、时间、闹钟多参数命令均返回 `ERROR PARAM`

3. **项目自带 COM5 快速联合测试仍然显示 `PASS`。**
   - PC 命令构造器和项目自测都使用当前的交替语法。
   - MCU、PC、自测三者相互兼容，却共同偏离老师协议，形成明显的自测盲区。

4. **缩写规则仍然过宽。**
   - 真机单独验证：`MON`、`MONT` 和 `*SET:FOR RIGHT` 都错误返回 `OK`
   - 老师 L044 虽显示通过，但只是被参数顺序错误提前拦截，不代表 `MONT` 处理正确

5. **SET DATE/TIME 后存在非法二进制 DISPLAY 事件。**
   - 原始事件含字节 `C0 01`
   - 违反 ASCII、8 字符定长和 `dpHex` 规范
   - 后续虽会恢复为正常 DISPLAY 事件，但这仍是独立的协议质量缺陷

6. **现有仓库构建产物不可追溯到当前源码。**
   - `main.c`：2026-06-11 20:57
   - 仓库中的 `.hex/.axf`：2026-06-10 22:26
   - 板端显示 V2.2，说明烧录内容比仓库现有 HEX 新，但无法由当前构建产物复现

## 2. 真机实测证据

老师原版测试命令：

```powershell
python test_runner.py `
  --student-id 524031910102 `
  --port COM5 `
  --file testcmd.txt `
  --delay 0.1
```

为遵守只写 `docs` 的边界，老师的 `testcmd.txt` 被原样复制到 `真机实测/`。两份文件 SHA-256 一致：

```text
352FA44E8D0D3099132DA91A9A64AF53FB5B55F700037B2C2202DC52C421979C
```

生成文件：

- `真机实测/testcmd_log_524031910102_20260614_224909.txt`
- `真机实测/testcmd_log_524031910102_20260614_224909_score.txt`

### 2.1 实际评分

| 类别 | 通过 | 得分 |
|---|---:|---:|
| 大小写不敏感 | 3/6 | 1.0/2.0 |
| 空格容错 | 2/4 | 1.0/2.0 |
| 缩写合法 | 4/8 | 1.0/2.0 |
| 多参数组合不少于 3 种 | 1/6 | 0.3/2.0 |
| FORMAT RIGHT | 4/7 | 1.1/2.0 |
| **合计** |  | **4.5/10.0** |

### 2.2 全部关键失败

以下老师命令均返回 `ERROR PARAM`：

```text
*SET:DATE YEAR MONTH DATE 2026 06 15
*SET:TIME HOUR MIN SEC 14 30 00
*SET:ALARM HOUR MIN SEC 14 30 10
*SET:DATE YEAR DATE 2027 01
*SET:DATE MONTH DATE 06 01
*SET:TIME HOUR SEC 15 00
*SET:TIME HOUR MIN 16 30
```

大小写和多空格版本也失败，因此评分表看起来像“大小写、空格、缩写都不完整”，但共同根因是多参数语法顺序。

### 2.3 FORMAT 分数的正确解释

L056 设置固定时间失败：

```text
>> *SET:TIME HOUR MIN SEC 12 30 45
<< ERROR PARAM
```

因此后续查询使用 EEPROM 中原有时间约 `13:14:06`：

```text
LEFT : OK 13.14.06
RIGHT: OK 60.41.31
```

这说明 RIGHT 字符串逆序本身有效；FORMAT 类失分主要是测试前置的 SET TIME 失败，并非反转算法完全失效。

### 2.4 首条 RST 超时

L001 `*RST` 在 3 秒内只收到 EVT，没有收到 `OK`，因此记为 TIMEOUT。L003 及后续 RST 均正常返回 `OK`。

此项不进入 10 分自动评分，但表明首次连接后直接开始测试仍可能出现瞬态丢应答。正式复测前应先等待串口稳定并手工确认一次 `*PING`。

## 3. 根因：参数语法不兼容

### 3.1 老师要求的语法

字段名集中在前，数值集中在后：

```text
*SET:DATE YEAR MONTH DATE 2026 06 15
*SET:TIME HOUR MIN SEC 12 30 45
*SET:DATE YEAR DATE 2027 01
*SET:TIME HOUR SEC 15 00
```

### 3.2 当前 MCU 解析语法

`mcu/src/main.c` 的 DATE、TIME、ALARM 处理函数循环读取：

```text
字段 -> 紧随的数值 -> 字段 -> 紧随的数值
```

因此当前实际接受：

```text
*SET:DATE YEAR 2026 MONTH 06 DATE 15
*SET:TIME HOUR 12 MIN 30 SEC 45
```

短探针结果：

```text
>> *SET:DATE YEAR MONTH DATE 2026 06 15
<< ERROR PARAM

>> *SET:DATE YEAR 2026 MONTH 06 DATE 15
<< OK

>> *SET:TIME HOUR MIN SEC 12 30 45
<< ERROR PARAM

>> *SET:TIME HOUR 12 MIN 30 SEC 45
<< OK
```

### 3.3 PC 端也采用错误语法

`pc_host/protocol.py` 当前构造：

```python
*SET:DATE YEAR 2026 MONTH 6 DATE 15
*SET:TIME HOUR 12 MINUTE 30 SECOND 45
```

`pc_host/app.py` 的按钮、预设命令和闹钟设置也采用相同交替语法。

项目自带快速真机测试结果：

```text
PASS
- PING 心跳: OK
- GET FORMAT: OK
- GET MODE: OK
- GET DISPLAY: OK
- SET DATE: OK
- SET TIME: OK
- SET MODE NIGHT: OK
- SET MODE DAY: OK
- SET WEATHER: OK
- SET RING DEFAULT: OK
- USER2 安全天气短显: OK
```

所以项目自测 PASS 不能证明符合老师协议。

## 4. 缩写规则问题

正式规则是“大写字母必须输入，小写字母可以省略”：

- `MINute`：`MIN/MINU/MINUT/MINUTE` 合法，`MI` 非法
- `DISPlay`：`DISP/DISPL/DISPLA/DISPLAY` 合法
- `MONTH`：全部大写，只能完整输入 `MONTH`
- `FORMAT`：全部大写，只能完整输入 `FORMAT`

真机单独验证：

| 命令 | 真机结果 | 正确结果 |
|---|---|---|
| `*SET:DATE MONTH 07` | `OK` | `OK` |
| `*SET:DATE MONT 08` | `OK` | `ERROR` |
| `*SET:DATE MON 09` | `OK` | `ERROR` |
| `*SET:TIME MIN 31` | `OK` | `OK` |
| `*SET:TIME MI 32` | `ERROR PARAM` | `ERROR` |
| `*SET:FOR RIGHT` | `OK` | `ERROR` |

对应源码问题：

```c
MatchToken(field, "MONTH", 3U)
MatchToken(token, "FORMAT", 3U)
```

应分别使用 5 和 6 作为最短长度。

老师 L044：

```text
*SET:DATE YEAR MONT DATE 2026 01 01
```

虽然自动报告把它判为通过，但解析器首先把 `MONT` 当作 YEAR 的数值，数字解析失败后返回 ERROR，根本没有执行到 MONTH 缩写判断。这是测试被另一个错误“误打误撞通过”。

## 5. 非法 DISPLAY 事件

交替语法 SET DATE/TIME 成功后，原始串口稳定出现：

```text
*EVT:DISP <C0><01> 1C
```

十六进制：

```text
2A 45 56 54 3A 44 49 53 50 20 C0 01 20 31 43 0D 0A
```

随后又会出现正常帧：

```text
*EVT:DISP 12_34_56 24
```

影响：

- 第一次强制刷新事件不再是 ASCII
- 8 字符 payload 被截断
- PC 日志会显示替换字符
- 数字孪生可能忽略该帧或短暂显示错误

该问题不影响老师 10 分评分器，因为评分器忽略 EVT；但它直接影响正式题目中的数字孪生和日志质量。

## 6. 其他已确认问题

### 6.1 RST 与开机动画耦合

每次串口 `*RST` 都重新开始完整开机动画。测试 L075 后的十个 `*SET:KEY` 在动画期间返回 `OK`，但源码会直接跳过实际按键动作。

这会产生“日志成功、功能未执行”的假阳性。

### 6.2 RST 会重新加载 EEPROM 时间

`ResetRuntimeState()` 设置默认时间后又调用 `TimeBackup_Load()`。因此 `*RST` 后仍保留 EEPROM 日期时间，不完全符合“复位时钟/日期”的文字要求。

### 6.3 构建产物落后

仓库现有 `.hex/.axf` 早于当前 `main.c`。虽然板端报告 V2.2，但当前仓库没有与之对应的最新可追溯构建产物。

### 6.4 老师脚本的测试盲区

- L036 尾随空格会被 Python `strip()` 删除，自动模式没有真正发送尾随空格
- FORMAT 项只检查 GET 文本，不检查数码管和 `*EVT:DISP dpHex`
- SET:KEY、消息滚动、跨天闹钟、LED 和蜂鸣执行但不计入 10 分

## 7. 修改建议

### P0：支持老师规定的多参数语法

建议 DATE、TIME、ALARM 先把参数全部分词。

对于总 token 数为 2、4、6 的命令：

1. `field_count = token_count / 2`
2. 前半段必须全部是合法且不重复的字段
3. 后半段必须全部是对应数值
4. 先写入临时状态并完成全部范围校验
5. 全部成功后再一次性提交，失败时不修改原状态

为了不立即破坏现有 PC，可在过渡期：

- 优先解析老师分组语法
- 失败后再兼容现有交替语法
- PC 端统一改为发送老师分组语法
- 最终把老师语法作为唯一文档和测试基准

### P0：同步修改 PC 命令构造器

需要统一检查：

- `pc_host/protocol.py`
- `pc_host/app.py` 中日期、时间、闹钟命令
- 调试页预设命令
- NTP 对时流程
- `pc_host/run_extension_checks.py`

目标格式：

```text
*SET:DATE YEAR MONTH DATE 2026 06 15
*SET:TIME HOUR MIN SEC 12 30 45
*SET:ALARM HOUR MIN SEC 07 30 00
```

### P0：定位 DISPLAY 二进制污染

建议先增加回归测试：

1. 等待开机动画完成
2. 发送合法 SET DATE
3. 捕获直到 `OK` 后 1 秒的所有原始字节
4. 断言每一行均为 ASCII
5. 断言 DISPLAY 匹配 `^\*EVT:DISP [ -~]{8} [0-9A-F]{2}$`

重点检查 `TimeBackup_Save()`、`RefreshDisplayAndLeds()`、`UpdateLedHardware()` 和 `EmitDisplayEvent()` 之间的数据是否被覆盖。

### P1：收紧缩写长度

```c
MatchToken(field, "MONTH", 5U)
MatchToken(token, "FORMAT", 6U)
```

并补测：

```text
MON -> ERROR
MONT -> ERROR
MONTH -> OK
FOR -> ERROR
FORMAT -> OK
```

### P1：拆分冷启动与协议 RST

- 冷启动：加载 EEPROM，播放开机动画
- 串口 RST：按协议复位状态，不重新播放完整动画
- SET:KEY 返回 OK 时必须真的执行按键动作

### P1：让项目自测直接覆盖老师命令

把老师 100 条命令作为回归测试输入，至少对 30 条计分行逐条断言。

项目自测不应只复用 PC 自己的命令构造器，否则 MCU 与 PC 同时写错时仍会 PASS。

## 8. 推荐修复与复测顺序

1. 为老师参数顺序增加失败回归测试
2. 修改 MCU DATE/TIME/ALARM 参数解析
3. 修改 PC 日期、时间、闹钟命令构造器
4. 收紧 MONTH 和 FORMAT 缩写
5. 修复非法 DISPLAY 事件
6. 拆分冷启动和串口 RST
7. Keil5 重新编译，确保 `0 Error, 0 Warning`
8. 确认 HEX/AXF 时间晚于源码
9. 烧录后先运行老师 100 条
10. 再运行项目 quick/full 联合测试

复测成功标准：

- 老师评分 `10.0/10.0`
- 100 条中只有设计上应返回 ERROR 的错误命令返回 ERROR
- 无 TIMEOUT
- 日志中不存在非 ASCII 或畸形 DISPLAY 事件
- 项目 quick/full 测试通过

## 9. 文件清单

| 文件 | 说明 |
|---|---|
| `真机实测/testcmd.txt` | 与老师原文件 SHA-256 一致的命令副本 |
| `真机实测/testcmd_log_524031910102_20260614_224909.txt` | COM5 真实 100 条会话 |
| `真机实测/testcmd_log_524031910102_20260614_224909_score.txt` | 老师原版评分器报告 |
| `真机实测/补充诊断记录.md` | 参数语法、缩写、DISPLAY 原始字节和自测对照 |
| `源码静态推演日志.txt` | 已作废的早期静态推演，仅保留过程记录 |
| `源码静态推演日志_score.txt` | 已作废静态推演的评分输出 |
| `资料核验截图/` | 正式题目和新测试指南关键页 |

## 10. 评估元数据

```json
{
  "evaluation_date": "2026-06-14",
  "serial_port": "COM5",
  "board_version_event": "V2_2",
  "teacher_test_commands": 100,
  "teacher_test_ok": 70,
  "teacher_test_error": 29,
  "teacher_test_timeout": 1,
  "teacher_score": 4.5,
  "project_quick_serial_test": "PASS",
  "primary_root_cause": "teacher grouped field/value grammar is incompatible with current alternating parser grammar",
  "confirmed_secondary_issues": [
    "MONTH accepts MON and MONT",
    "FORMAT accepts FOR",
    "SET DATE/TIME emits a transient non-ASCII DISPLAY event",
    "RST replays boot animation and simulated keys may return OK without executing"
  ],
  "production_code_modified": false
}
```
