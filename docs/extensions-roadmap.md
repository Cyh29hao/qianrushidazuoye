# 扩展版实施路线

## 当前定位

- 基础功能版本以提交 `7b119f7` 为封板基线。
- 后续扩展开发在分支 `feature/extensions-v1` 上推进。

## 4.2 扩展功能顺序

1. `E1` 网络对时
2. `E3` 自动昼夜模式
3. `E2` 天气获取
4. `E4` 数据可视化看板

## 4.3 自主增加功能组合

### 大点 1

- 多日程/课程提醒系统
- 可配置铃声类型：
  - `DEFAULT`
  - `WORK_START`
  - `WORK_END`
  - `WAKE`
  - `SONG`

### 大点 2

- PC 白天/黑夜主题
- 与板端 `DAY/NIGHT` 模式双向同步

### 小点

- PC 语音播报
- 自动化测试脚本

## 接口约定

- 复用现有：
  - `*SET:DATE`
  - `*SET:TIME`
  - `*SET:MODE`
  - `*SET:MSG`
  - `*SET:BEEP`
  - `*EVT:KEY USER1`
  - `*EVT:KEY USER2`
  - `*EVT:MODE`
- 新增：
  - `*SET:WEATHER DISP <token8> LED <hex>`
  - `*SET:RING <DEFAULT|WORK_START|WORK_END|WAKE|SONG>`

## 设计约束

- 不大改现有 UI 骨架，只增量加扩展页和配置项。
- 板端 `MODE` 视为主题同步的唯一真源。
- `USER1` 在扩展阶段优先承担“网络对时快捷键”职责。
- `USER2` 专用于触发天气短显；即使处于编辑态，也会先退出编辑并显示天气或 `NO WX`，不再承担 `SUB`/减一语义。
- 扩展实现不能破坏当前基础闭环。
