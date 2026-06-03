#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "hw_memmap.h"
#include "gpio.h"
#include "hw_i2c.h"
#include "hw_types.h"
#include "i2c.h"
#include "interrupt.h"
#include "pin_map.h"
#include "sysctl.h"
#include "systick.h"
#include "uart.h"

#define SYSTICK_FREQUENCY          1000U
#define DISPLAY_WIDTH              8U
#define VISIBLE_TEXT_MAX           64U
#define UART_LINE_MAX              80U
#define DISPLAY_HEARTBEAT_MS       1000U
#define KEY_SCAN_PERIOD_MS         10U
#define DEBOUNCE_TICKS             3U
#define FUNC_LONG_PRESS_TICKS      60U
#define ADD_REPEAT_START_TICKS     40U
#define ADD_REPEAT_INTERVAL_TICKS  15U
#define EDIT_TIMEOUT_MS            5000UL
#define RXTX_FLASH_MS              300UL
#define MESSAGE_SHOW_MS            10000UL
#define SCROLL_SLOW_MS             1000UL
#define SCROLL_FAST_MS             500UL
#define BOOT_STEP_SHORT_MS         250UL
#define BOOT_STEP_FLASH_MS         350UL
#define BOOT_VERSION_MS            1200UL
#define ALARM_MAX_RING_MS          10000UL
#define REMOTE_BEEP_MIN_MS         10UL
#define REMOTE_BEEP_MAX_MS         5000UL
#define MANUAL_LED_SHOW_MS         1000UL

#define TCA6424_I2CADDR            0x22
#define PCA9557_I2CADDR            0x18

#define PCA9557_INPUT              0x00
#define PCA9557_OUTPUT             0x01
#define PCA9557_POLINVERT          0x02
#define PCA9557_CONFIG             0x03

#define TCA6424_INPUT_PORT0        0x00
#define TCA6424_INPUT_PORT1        0x01
#define TCA6424_INPUT_PORT2        0x02
#define TCA6424_OUTPUT_PORT0       0x04
#define TCA6424_OUTPUT_PORT1       0x05
#define TCA6424_OUTPUT_PORT2       0x06
#define TCA6424_CONFIG_PORT0       0x0C
#define TCA6424_CONFIG_PORT1       0x0D
#define TCA6424_CONFIG_PORT2       0x0E

#define BOARD_LED_COUNT            8U
#define KEY_COUNT                  10U

#define KEY_I2C_MASK               0xFFU
#define USER_GPIO_MASK             (GPIO_PIN_0 | GPIO_PIN_1)

#define LED_HEARTBEAT              0x01U
#define LED_ALARM                  0x02U
#define LED_EDIT                   0x04U
#define LED_UART_RX                0x08U
#define LED_UART_TX                0x10U
#define LED_MODE_NIGHT             0x20U
#define LED_FORMAT_RIGHT           0x40U
#define LED_MANUAL_BIT             0x80U

typedef enum {
    VIEW_TIME = 0,
    VIEW_DATE,
    VIEW_YEAR
} ViewMode;

typedef enum {
    MODE_DAY = 0,
    MODE_NIGHT
} DayNightMode;

typedef enum {
    FORMAT_LEFT = 0,
    FORMAT_RIGHT
} DisplayFormat;

typedef enum {
    EDIT_NONE = 0,
    EDIT_DATE,
    EDIT_TIME,
    EDIT_ALARM
} EditMode;

typedef enum {
    BOOT_ALL_ON = 0,
    BOOT_ALL_OFF,
    BOOT_ID,
    BOOT_ID_FLASH,
    BOOT_NAME,
    BOOT_NAME_FLASH,
    BOOT_VERSION,
    BOOT_DONE
} BootPhase;

typedef enum {
    BUZZER_IDLE = 0,
    BUZZER_REMOTE,
    BUZZER_ALARM
} BuzzerMode;

typedef enum {
    KEY_FUNC = 0,
    KEY_SHIFT,
    KEY_ADD,
    KEY_SAVE,
    KEY_DISP,
    KEY_SPEED,
    KEY_FORMAT,
    KEY_EXT,
    KEY_USER1,
    KEY_USER2
} KeyCode;

typedef struct {
    uint16_t year;
    uint8_t month;
    uint8_t day;
    uint8_t hour;
    uint8_t minute;
    uint8_t second;
} DateTime;

typedef struct {
    uint8_t hour;
    uint8_t minute;
    uint8_t second;
    uint8_t enabled;
} AlarmState;

typedef struct {
    char ch;
    uint8_t dp;
} SegmentCell;

typedef struct {
    char chars[DISPLAY_WIDTH];
    uint8_t dp_mask;
} DisplayFrame;

static void Clock_Init(void);
static void GPIO_Init(void);
static void I2C0_Init(void);
static void UART0_Init(void);
static void I2C0_Settle(void);
static uint8_t I2C0_WriteByte(uint8_t devAddr, uint8_t regAddr,
                              uint8_t writeData);
static uint8_t I2C0_ReadByte(uint8_t devAddr, uint8_t regAddr);
static void UART_WriteString(const char *text);
static void UART_WriteLine(const char *text);
static void UART_ReplyOk(const char *payload);
static void UART_ReplyError(const char *reason);
static void UART_Poll(void);
static void UART_ProcessLine(char *line);

static void Tick10ms(void);
static void Tick100ms(void);
static void Tick500ms(void);
static void Tick1000ms(void);
static void ServiceBuzzer(void);
static void StopBuzzer(void);
static void StartAlarmRing(void);
static void StartRemoteBeep(uint32_t durationMs);
static void GPIO_SetBeeper(bool on);

static void RefreshDisplayAndLeds(bool forceEvent);
static void BuildVisibleText(char *out, uint8_t outSize);
static void ReverseVisibleText(const char *in, char *out, uint8_t outSize);
static uint8_t VisibleTextToCells(const char *text, SegmentCell *cells,
                                  uint8_t maxCells);
static void ComposeFrameFromCells(const SegmentCell *cells, uint8_t count,
                                  uint8_t startIndex, DisplayFrame *frame);
static void ApplyEditBlink(DisplayFrame *frame);
static void RenderFrameToSegments(const DisplayFrame *frame);
static void Display_ScanNextDigit(void);
static uint8_t Encode7Seg(char value);
static void EmitDisplayEvent(void);
static void EmitLedEvent(void);
static void EmitModeEvent(void);
static void EmitEditEvent(EditMode mode);
static void EmitKeyEvent(KeyCode key);
static void UpdateLedHardware(bool forceEvent);
static uint8_t BuildSystemLedByte(void);

static void Keys_Scan(void);
static void HandleKeyPress(KeyCode key, bool emitEvent);
static void HandleKeyRelease(KeyCode key);
static void HandleFuncLongPress(void);
static void AdvanceEditField(void);
static void IncrementEditField(void);
static void DecrementEditField(void);
static void SaveEditState(void);
static void EnterEditMode(EditMode mode);
static void ExitEditMode(bool saveChanges);
static bool SimulateKeyPress(const char *nameToken);

static bool MatchToken(const char *token, const char *canonical,
                       uint8_t minLen);
static const char *SkipSpaces(const char *text);
static const char *ReadToken(const char *text, char *out, uint8_t outSize);
static bool ParseUint(const char *token, uint32_t *value);
static bool ParseHexByte(const char *token, uint8_t *value);
static char ToUpperAscii(char value);
static void TrimAscii(char *text);
static uint8_t StringLengthBounded(const char *text, uint8_t maxLen);
static bool IsLeapYear(uint16_t year);
static uint8_t DaysInMonth(uint16_t year, uint8_t month);
static void AdvanceClockOneSecond(DateTime *value);

static void ResetRuntimeState(void);
static void SetDefaultDateTime(DateTime *value);
static void SetDefaultAlarm(AlarmState *value);
static void BuildDottedTime(const DateTime *value, char *out, uint8_t outSize);
static void BuildDottedDateShort(const DateTime *value, char *out,
                                 uint8_t outSize);
static void BuildDottedDateYear(const DateTime *value, char *out,
                                uint8_t outSize);
static void BuildDottedAlarm(const AlarmState *value, char *out,
                             uint8_t outSize);

static void HandleSetDate(const char *params);
static void HandleSetTime(const char *params);
static void HandleSetAlarm(const char *params);
static void HandleSetDisplay(const char *params);
static void HandleSetFormat(const char *params);
static void HandleSetMessage(const char *params);
static void HandleSetBeep(const char *params);
static void HandleSetLed(const char *params);
static void HandleSetMode(const char *params);
static void HandleGet(const char *params);

static const char *kKeyNames[KEY_COUNT] = {
    "FUNC", "SHIFT", "ADD", "SAVE", "DISP",
    "SPEED", "FORMAT", "EXT", "USER1", "USER2"
};

static volatile uint32_t g_millis;
static volatile uint8_t g_scanTicks;
static volatile uint8_t g_ticks10ms;
static volatile uint8_t g_ticks100ms;
static volatile uint8_t g_ticks500ms;
static volatile uint8_t g_ticks1000ms;

static uint32_t g_sysClock;
static DateTime g_now;
static AlarmState g_alarm;
static ViewMode g_viewMode;
static DayNightMode g_dayNight;
static DisplayFormat g_displayFormat;
static EditMode g_editMode;
static uint8_t g_editField;
static DateTime g_editDateTime;
static AlarmState g_editAlarm;
static uint32_t g_editDeadlineMs;
static uint8_t g_displayEnabled;
static uint8_t g_scrollFast;
static uint32_t g_nextScrollMs;
static uint8_t g_scrollOffset;
static BootPhase g_bootPhase;
static uint32_t g_bootDeadlineMs;
static uint8_t g_blinkVisible;
static uint8_t g_heartbeatBit;
static uint8_t g_alarmBlinkBit;
static char g_messageText[33];
static uint32_t g_messageDeadlineMs;
static uint8_t g_messageActive;
static uint8_t g_manualLedMask;
static uint32_t g_manualLedUntilMs;
static uint32_t g_rxFlashUntilMs;
static uint32_t g_txFlashUntilMs;
static uint8_t g_lastRawKeys[KEY_COUNT];
static uint8_t g_stableKeys[KEY_COUNT];
static uint8_t g_debounceCounts[KEY_COUNT];
static uint16_t g_holdTicks[KEY_COUNT];
static uint8_t g_longPressDone[KEY_COUNT];
static DisplayFrame g_currentFrame;
static DisplayFrame g_previousFrame;
static uint8_t g_currentSegments[DISPLAY_WIDTH];
static uint8_t g_currentDigit;
static uint8_t g_ledByte;
static uint8_t g_prevLedByte;
static BuzzerMode g_buzzerMode;
static uint8_t g_buzzerOn;
static uint32_t g_buzzerDeadlineMs;
static uint32_t g_buzzerToggleMs;
static char g_uartLine[UART_LINE_MAX];
static uint8_t g_uartLen;
static uint8_t g_uartOverflow;

int main(void)
{
    Clock_Init();
    GPIO_Init();
    I2C0_Init();
    UART0_Init();
    ResetRuntimeState();
    RefreshDisplayAndLeds(true);
    UART_WriteLine("S800 CLOCK READY");

    while (1) {
        UART_Poll();

        while (g_scanTicks != 0U) {
            g_scanTicks--;
            Display_ScanNextDigit();
        }

        while (g_ticks10ms != 0U) {
            g_ticks10ms--;
            Tick10ms();
        }

        while (g_ticks100ms != 0U) {
            g_ticks100ms--;
            Tick100ms();
        }

        while (g_ticks500ms != 0U) {
            g_ticks500ms--;
            Tick500ms();
        }

        while (g_ticks1000ms != 0U) {
            g_ticks1000ms--;
            Tick1000ms();
        }
    }
}

static void Clock_Init(void)
{
    g_sysClock = SysCtlClockFreqSet((SYSCTL_XTAL_16MHZ |
                                     SYSCTL_OSC_INT |
                                     SYSCTL_USE_PLL |
                                     SYSCTL_CFG_VCO_480), 20000000U);
    SysTickPeriodSet(g_sysClock / SYSTICK_FREQUENCY);
    SysTickEnable();
    SysTickIntEnable();
    IntMasterEnable();
}

static void GPIO_Init(void)
{
    SysCtlPeripheralEnable(SYSCTL_PERIPH_GPIOF);
    SysCtlPeripheralEnable(SYSCTL_PERIPH_GPIOJ);
    SysCtlPeripheralEnable(SYSCTL_PERIPH_GPIOK);
    SysCtlPeripheralEnable(SYSCTL_PERIPH_GPION);

    while (!SysCtlPeripheralReady(SYSCTL_PERIPH_GPIOF) ||
           !SysCtlPeripheralReady(SYSCTL_PERIPH_GPIOJ) ||
           !SysCtlPeripheralReady(SYSCTL_PERIPH_GPIOK) ||
           !SysCtlPeripheralReady(SYSCTL_PERIPH_GPION)) {
    }

    GPIOPinTypeGPIOInput(GPIO_PORTJ_BASE, USER_GPIO_MASK);
    GPIOPadConfigSet(GPIO_PORTJ_BASE, USER_GPIO_MASK,
                     GPIO_STRENGTH_2MA, GPIO_PIN_TYPE_STD_WPU);

    GPIOPinTypeGPIOOutput(GPIO_PORTF_BASE, GPIO_PIN_0 | GPIO_PIN_4);
    GPIOPinTypeGPIOOutput(GPIO_PORTN_BASE, GPIO_PIN_0 | GPIO_PIN_1);
    GPIOPinTypeGPIOOutput(GPIO_PORTK_BASE, GPIO_PIN_5);

    GPIOPinWrite(GPIO_PORTF_BASE, GPIO_PIN_0 | GPIO_PIN_4, 0x00);
    GPIOPinWrite(GPIO_PORTN_BASE, GPIO_PIN_0 | GPIO_PIN_1, 0x00);
    GPIOPinWrite(GPIO_PORTK_BASE, GPIO_PIN_5, 0x00);
}

static void I2C0_Init(void)
{
    SysCtlPeripheralEnable(SYSCTL_PERIPH_I2C0);
    SysCtlPeripheralEnable(SYSCTL_PERIPH_GPIOB);
    while (!SysCtlPeripheralReady(SYSCTL_PERIPH_I2C0) ||
           !SysCtlPeripheralReady(SYSCTL_PERIPH_GPIOB)) {
    }

    GPIOPinConfigure(GPIO_PB2_I2C0SCL);
    GPIOPinConfigure(GPIO_PB3_I2C0SDA);
    GPIOPinTypeI2CSCL(GPIO_PORTB_BASE, GPIO_PIN_2);
    GPIOPinTypeI2C(GPIO_PORTB_BASE, GPIO_PIN_3);

    I2CMasterInitExpClk(I2C0_BASE, g_sysClock, true);
    I2CMasterEnable(I2C0_BASE);

    I2C0_WriteByte(TCA6424_I2CADDR, TCA6424_CONFIG_PORT0, 0xFFU);
    I2C0_WriteByte(TCA6424_I2CADDR, TCA6424_CONFIG_PORT1, 0x00U);
    I2C0_WriteByte(TCA6424_I2CADDR, TCA6424_CONFIG_PORT2, 0x00U);
    I2C0_WriteByte(TCA6424_I2CADDR, TCA6424_OUTPUT_PORT1, 0x00U);
    I2C0_WriteByte(TCA6424_I2CADDR, TCA6424_OUTPUT_PORT2, 0x00U);

    I2C0_WriteByte(PCA9557_I2CADDR, PCA9557_CONFIG, 0x00U);
    I2C0_WriteByte(PCA9557_I2CADDR, PCA9557_OUTPUT, 0xFFU);
}

static void UART0_Init(void)
{
    SysCtlPeripheralEnable(SYSCTL_PERIPH_UART0);
    SysCtlPeripheralEnable(SYSCTL_PERIPH_GPIOA);
    while (!SysCtlPeripheralReady(SYSCTL_PERIPH_UART0) ||
           !SysCtlPeripheralReady(SYSCTL_PERIPH_GPIOA)) {
    }

    GPIOPinConfigure(GPIO_PA0_U0RX);
    GPIOPinConfigure(GPIO_PA1_U0TX);
    GPIOPinTypeUART(GPIO_PORTA_BASE, GPIO_PIN_0 | GPIO_PIN_1);
    UARTConfigSetExpClk(UART0_BASE, g_sysClock, 115200U,
                        UART_CONFIG_WLEN_8 |
                        UART_CONFIG_STOP_ONE |
                        UART_CONFIG_PAR_NONE);
    UARTEnable(UART0_BASE);
}

static void I2C0_Settle(void)
{
    /*
     * The reference experiments leave a tiny gap between register phase and
     * data phase on this board. Keeping a short delay here makes the external
     * GPIO expanders behave much more like the known-good lab projects.
     */
    SysCtlDelay((g_sysClock / 3000000U) + 2U);
}

static uint8_t I2C0_WriteByte(uint8_t devAddr, uint8_t regAddr,
                              uint8_t writeData)
{
    uint8_t result;

    while (I2CMasterBusy(I2C0_BASE)) {
    }
    I2CMasterSlaveAddrSet(I2C0_BASE, devAddr, false);
    I2CMasterDataPut(I2C0_BASE, regAddr);
    I2CMasterControl(I2C0_BASE, I2C_MASTER_CMD_BURST_SEND_START);
    while (I2CMasterBusy(I2C0_BASE)) {
    }
    result = (uint8_t)I2CMasterErr(I2C0_BASE);
    I2C0_Settle();

    I2CMasterDataPut(I2C0_BASE, writeData);
    I2CMasterControl(I2C0_BASE, I2C_MASTER_CMD_BURST_SEND_FINISH);
    while (I2CMasterBusy(I2C0_BASE)) {
    }
    result |= (uint8_t)I2CMasterErr(I2C0_BASE);
    I2C0_Settle();
    return result;
}

static uint8_t I2C0_ReadByte(uint8_t devAddr, uint8_t regAddr)
{
    while (I2CMasterBusy(I2C0_BASE)) {
    }
    I2CMasterSlaveAddrSet(I2C0_BASE, devAddr, false);
    I2CMasterDataPut(I2C0_BASE, regAddr);
    I2CMasterControl(I2C0_BASE, I2C_MASTER_CMD_SINGLE_SEND);
    while (I2CMasterBusBusy(I2C0_BASE)) {
    }
    I2C0_Settle();
    I2CMasterSlaveAddrSet(I2C0_BASE, devAddr, true);
    I2CMasterControl(I2C0_BASE, I2C_MASTER_CMD_SINGLE_RECEIVE);
    while (I2CMasterBusBusy(I2C0_BASE)) {
    }
    I2C0_Settle();
    return (uint8_t)I2CMasterDataGet(I2C0_BASE);
}

static void UART_WriteString(const char *text)
{
    if (text == NULL) {
        return;
    }
    while (*text != '\0') {
        UARTCharPut(UART0_BASE, (uint8_t)*text);
        text++;
    }
    g_txFlashUntilMs = g_millis + RXTX_FLASH_MS;
}

static void UART_WriteLine(const char *text)
{
    UART_WriteString(text);
    UART_WriteString("\r\n");
}

static void UART_ReplyOk(const char *payload)
{
    char buffer[96];
    if ((payload == NULL) || (payload[0] == '\0')) {
        UART_WriteLine("OK");
        return;
    }
    snprintf(buffer, sizeof(buffer), "OK %s", payload);
    UART_WriteLine(buffer);
}

static void UART_ReplyError(const char *reason)
{
    char buffer[48];
    snprintf(buffer, sizeof(buffer), "ERROR %s", reason);
    UART_WriteLine(buffer);
}

static void UART_Poll(void)
{
    int32_t value;

    while (UARTCharsAvail(UART0_BASE)) {
        value = UARTCharGetNonBlocking(UART0_BASE);
        if (value < 0) {
            break;
        }
        g_rxFlashUntilMs = g_millis + RXTX_FLASH_MS;
        if ((value == '\r') || (value == '\n')) {
            if (g_uartOverflow != 0U) {
                g_uartLen = 0U;
                g_uartOverflow = 0U;
                UART_ReplyError("LEN");
                RefreshDisplayAndLeds(true);
                continue;
            }
            if (g_uartLen != 0U) {
                g_uartLine[g_uartLen] = '\0';
                UART_ProcessLine(g_uartLine);
                g_uartLen = 0U;
            }
            continue;
        }
        if (g_uartLen >= (UART_LINE_MAX - 1U)) {
            g_uartOverflow = 1U;
            continue;
        }
        g_uartLine[g_uartLen] = (char)value;
        g_uartLen++;
    }
}

static void Tick10ms(void)
{
    Keys_Scan();
    ServiceBuzzer();

    if ((g_bootPhase != BOOT_DONE) && (g_millis >= g_bootDeadlineMs)) {
        g_bootPhase = (BootPhase)((uint8_t)g_bootPhase + 1U);
        if (g_bootPhase == BOOT_ALL_OFF) {
            g_bootDeadlineMs = g_millis + BOOT_STEP_SHORT_MS;
        } else if ((g_bootPhase == BOOT_ID) ||
                   (g_bootPhase == BOOT_NAME)) {
            g_bootDeadlineMs = g_millis + BOOT_STEP_FLASH_MS;
        } else if ((g_bootPhase == BOOT_ID_FLASH) ||
                   (g_bootPhase == BOOT_NAME_FLASH)) {
            g_bootDeadlineMs = g_millis + BOOT_STEP_SHORT_MS;
        } else if (g_bootPhase == BOOT_VERSION) {
            g_bootDeadlineMs = g_millis + BOOT_VERSION_MS;
        } else if (g_bootPhase > BOOT_VERSION) {
            g_bootPhase = BOOT_DONE;
            g_nextScrollMs = g_millis + SCROLL_SLOW_MS;
        }
        RefreshDisplayAndLeds(true);
    }

    if ((g_editMode != EDIT_NONE) && (g_millis >= g_editDeadlineMs)) {
        ExitEditMode(false);
    }

    if ((g_messageActive != 0U) && (g_millis >= g_messageDeadlineMs)) {
        g_messageActive = 0U;
        RefreshDisplayAndLeds(true);
    }

    if ((g_millis >= g_nextScrollMs) && (g_bootPhase == BOOT_DONE)) {
        g_scrollOffset++;
        g_nextScrollMs = g_millis +
            (g_scrollFast != 0U ? SCROLL_FAST_MS : SCROLL_SLOW_MS);
        RefreshDisplayAndLeds(false);
    }
}

static void Tick100ms(void)
{
    if (g_bootPhase != BOOT_DONE) {
        return;
    }
    RefreshDisplayAndLeds(false);
}

static void Tick500ms(void)
{
    g_blinkVisible = (uint8_t)!g_blinkVisible;
    g_alarmBlinkBit = (uint8_t)!g_alarmBlinkBit;
    RefreshDisplayAndLeds(false);
}

static void Tick1000ms(void)
{
    if (g_bootPhase == BOOT_DONE) {
        AdvanceClockOneSecond(&g_now);
        g_heartbeatBit = (uint8_t)!g_heartbeatBit;
        if ((g_alarm.enabled != 0U) &&
            (g_now.hour == g_alarm.hour) &&
            (g_now.minute == g_alarm.minute) &&
            (g_now.second == g_alarm.second) &&
            (g_buzzerMode == BUZZER_IDLE)) {
            StartAlarmRing();
        }
    }
    RefreshDisplayAndLeds(false);
}

static void ServiceBuzzer(void)
{
    if (g_buzzerMode == BUZZER_IDLE) {
        GPIO_SetBeeper(false);
        return;
    }

    if (g_millis >= g_buzzerDeadlineMs) {
        StopBuzzer();
        if (g_buzzerMode == BUZZER_ALARM) {
            UART_WriteLine("*EVT:ALARM_OFF");
        }
        RefreshDisplayAndLeds(true);
        return;
    }

    if (g_buzzerMode == BUZZER_REMOTE) {
        GPIO_SetBeeper(true);
        return;
    }

    if (g_millis >= g_buzzerToggleMs) {
        g_buzzerOn = (uint8_t)!g_buzzerOn;
        GPIO_SetBeeper(g_buzzerOn != 0U);
        g_buzzerToggleMs = g_millis + (g_buzzerOn != 0U ? 180U : 120U);
    }
}

static void StopBuzzer(void)
{
    g_buzzerMode = BUZZER_IDLE;
    g_buzzerOn = 0U;
    GPIO_SetBeeper(false);
}

static void StartAlarmRing(void)
{
    g_buzzerMode = BUZZER_ALARM;
    g_buzzerOn = 1U;
    g_buzzerDeadlineMs = g_millis + ALARM_MAX_RING_MS;
    g_buzzerToggleMs = g_millis + 180U;
    GPIO_SetBeeper(true);
    UART_WriteLine("*EVT:ALARM");
}

static void StartRemoteBeep(uint32_t durationMs)
{
    g_buzzerMode = BUZZER_REMOTE;
    g_buzzerOn = 1U;
    g_buzzerDeadlineMs = g_millis + durationMs;
    GPIO_SetBeeper(true);
}

static void GPIO_SetBeeper(bool on)
{
    GPIOPinWrite(GPIO_PORTK_BASE, GPIO_PIN_5, on ? GPIO_PIN_5 : 0x00U);
}

static void ResetRuntimeState(void)
{
    uint8_t index;

    SetDefaultDateTime(&g_now);
    SetDefaultAlarm(&g_alarm);
    g_viewMode = VIEW_TIME;
    g_dayNight = MODE_DAY;
    g_displayFormat = FORMAT_LEFT;
    g_editMode = EDIT_NONE;
    g_editField = 0U;
    g_editDeadlineMs = 0U;
    g_displayEnabled = 1U;
    g_scrollFast = 0U;
    g_nextScrollMs = g_millis + SCROLL_SLOW_MS;
    g_scrollOffset = 0U;
    g_bootPhase = BOOT_ALL_ON;
    g_bootDeadlineMs = g_millis + BOOT_STEP_SHORT_MS;
    g_blinkVisible = 1U;
    g_heartbeatBit = 1U;
    g_alarmBlinkBit = 1U;
    g_messageText[0] = '\0';
    g_messageActive = 0U;
    g_messageDeadlineMs = 0U;
    g_manualLedMask = 0U;
    g_manualLedUntilMs = 0U;
    g_rxFlashUntilMs = 0U;
    g_txFlashUntilMs = 0U;
    g_currentDigit = 0U;
    g_buzzerMode = BUZZER_IDLE;
    g_buzzerOn = 0U;
    g_buzzerDeadlineMs = 0U;
    g_buzzerToggleMs = 0U;
    g_uartLen = 0U;
    g_uartOverflow = 0U;
    memset(&g_currentFrame, 0, sizeof(g_currentFrame));
    memset(&g_previousFrame, 0, sizeof(g_previousFrame));
    memset(g_currentSegments, 0, sizeof(g_currentSegments));
    g_ledByte = 0U;
    g_prevLedByte = 0xFFU;

    for (index = 0U; index < KEY_COUNT; index++) {
        g_lastRawKeys[index] = 0U;
        g_stableKeys[index] = 0U;
        g_debounceCounts[index] = 0U;
        g_holdTicks[index] = 0U;
        g_longPressDone[index] = 0U;
    }

    StopBuzzer();
}

static void SetDefaultDateTime(DateTime *value)
{
    value->year = 2026U;
    value->month = 6U;
    value->day = 2U;
    value->hour = 12U;
    value->minute = 0U;
    value->second = 0U;
}

static void SetDefaultAlarm(AlarmState *value)
{
    value->hour = 7U;
    value->minute = 30U;
    value->second = 0U;
    value->enabled = 0U;
}

static void BuildDottedTime(const DateTime *value, char *out, uint8_t outSize)
{
    snprintf(out, outSize, "%02u.%02u.%02u",
             (unsigned)value->hour,
             (unsigned)value->minute,
             (unsigned)value->second);
}

static void BuildDottedDateShort(const DateTime *value, char *out,
                                 uint8_t outSize)
{
    snprintf(out, outSize, "%02u.%02u.%02u",
             (unsigned)(value->year % 100U),
             (unsigned)value->month,
             (unsigned)value->day);
}

static void BuildDottedDateYear(const DateTime *value, char *out,
                                uint8_t outSize)
{
    snprintf(out, outSize, "%04u.%02u%02u",
             (unsigned)value->year,
             (unsigned)value->month,
             (unsigned)value->day);
}

static void BuildDottedAlarm(const AlarmState *value, char *out,
                             uint8_t outSize)
{
    if (value->enabled == 0U) {
        snprintf(out, outSize, "OFF");
        return;
    }
    snprintf(out, outSize, "%02u.%02u.%02u",
             (unsigned)value->hour,
             (unsigned)value->minute,
             (unsigned)value->second);
}

static void BuildVisibleText(char *out, uint8_t outSize)
{
    if (g_bootPhase == BOOT_ALL_ON) {
        snprintf(out, outSize, "88.88.88");
        return;
    }
    if (g_bootPhase == BOOT_ALL_OFF) {
        snprintf(out, outSize, "");
        return;
    }
    if (g_bootPhase == BOOT_ID) {
        snprintf(out, outSize, "31910102");
        return;
    }
    if (g_bootPhase == BOOT_ID_FLASH) {
        snprintf(out, outSize, "");
        return;
    }
    if (g_bootPhase == BOOT_NAME) {
        snprintf(out, outSize, "CHENYH");
        return;
    }
    if (g_bootPhase == BOOT_NAME_FLASH) {
        snprintf(out, outSize, "");
        return;
    }
    if (g_bootPhase == BOOT_VERSION) {
        snprintf(out, outSize, "V1.0");
        return;
    }

    if (g_editMode == EDIT_DATE) {
        BuildDottedDateYear(&g_editDateTime, out, outSize);
        return;
    }
    if (g_editMode == EDIT_TIME) {
        BuildDottedTime(&g_editDateTime, out, outSize);
        return;
    }
    if (g_editMode == EDIT_ALARM) {
        BuildDottedAlarm(&g_editAlarm, out, outSize);
        return;
    }

    if ((g_messageActive != 0U) && (g_messageText[0] != '\0')) {
        snprintf(out, outSize, "%s", g_messageText);
        return;
    }

    if ((g_dayNight == MODE_NIGHT) && (g_viewMode == VIEW_TIME)) {
        snprintf(out, outSize, "%02u.%02u",
                 (unsigned)g_now.hour, (unsigned)g_now.minute);
        return;
    }

    if (g_viewMode == VIEW_DATE) {
        BuildDottedDateShort(&g_now, out, outSize);
        return;
    }
    if (g_viewMode == VIEW_YEAR) {
        BuildDottedDateYear(&g_now, out, outSize);
        return;
    }
    BuildDottedTime(&g_now, out, outSize);
}

static void ReverseVisibleText(const char *in, char *out, uint8_t outSize)
{
    uint8_t length = StringLengthBounded(in, outSize - 1U);
    uint8_t index;
    if (outSize == 0U) {
        return;
    }
    for (index = 0U; index < length; index++) {
        out[index] = in[length - 1U - index];
    }
    out[length] = '\0';
}

static uint8_t VisibleTextToCells(const char *text, SegmentCell *cells,
                                  uint8_t maxCells)
{
    uint8_t count = 0U;
    while (*text != '\0') {
        if (*text == '.') {
            if (count > 0U) {
                cells[count - 1U].dp = 1U;
            }
        } else {
            if (count >= maxCells) {
                break;
            }
            cells[count].ch = *text;
            cells[count].dp = 0U;
            count++;
        }
        text++;
    }
    return count;
}

static void ComposeFrameFromCells(const SegmentCell *cells, uint8_t count,
                                  uint8_t startIndex, DisplayFrame *frame)
{
    uint8_t index;
    memset(frame->chars, ' ', sizeof(frame->chars));
    frame->dp_mask = 0U;

    for (index = 0U; index < DISPLAY_WIDTH; index++) {
        uint8_t cellIndex = startIndex + index;
        if (cellIndex >= count) {
            break;
        }
        frame->chars[index] = cells[cellIndex].ch;
        if (cells[cellIndex].dp != 0U) {
            frame->dp_mask |= (uint8_t)(1U << index);
        }
    }
}

static void ApplyEditBlink(DisplayFrame *frame)
{
    uint8_t start = 0U;
    uint8_t end = 0U;
    uint8_t count;

    if ((g_editMode == EDIT_NONE) || (g_blinkVisible != 0U)) {
        return;
    }

    if (g_editMode == EDIT_DATE) {
        count = 8U;
        if (g_editField == 0U) {
            start = 0U;
            end = 3U;
        } else if (g_editField == 1U) {
            start = 4U;
            end = 5U;
        } else {
            start = 6U;
            end = 7U;
        }
    } else {
        count = 6U;
        if (g_editField == 0U) {
            start = 0U;
            end = 1U;
        } else if (g_editField == 1U) {
            start = 2U;
            end = 3U;
        } else {
            start = 4U;
            end = 5U;
        }
    }

    if (g_displayFormat == FORMAT_RIGHT) {
        uint8_t mappedStart = (uint8_t)((count - 1U) - end);
        uint8_t mappedEnd = (uint8_t)((count - 1U) - start);
        start = mappedStart;
        end = mappedEnd;
    }

    if (end >= DISPLAY_WIDTH) {
        end = DISPLAY_WIDTH - 1U;
    }
    while (start <= end) {
        frame->chars[start] = ' ';
        frame->dp_mask &= (uint8_t)~(1U << start);
        start++;
    }
}

static void RenderFrameToSegments(const DisplayFrame *frame)
{
    uint8_t index;
    for (index = 0U; index < DISPLAY_WIDTH; index++) {
        g_currentSegments[index] = Encode7Seg(frame->chars[index]);
        if ((frame->dp_mask & (uint8_t)(1U << index)) != 0U) {
            g_currentSegments[index] |= 0x80U;
        }
    }
}

static void RefreshDisplayAndLeds(bool forceEvent)
{
    char visible[VISIBLE_TEXT_MAX];
    char oriented[VISIBLE_TEXT_MAX];
    char cache[VISIBLE_TEXT_MAX];
    SegmentCell cells[VISIBLE_TEXT_MAX];
    uint8_t count;
    uint8_t maxStart;
    uint8_t startIndex = 0U;

    BuildVisibleText(visible, sizeof(visible));
    if (g_displayFormat == FORMAT_RIGHT) {
        ReverseVisibleText(visible, oriented, sizeof(oriented));
    } else {
        snprintf(oriented, sizeof(oriented), "%s", visible);
    }

    count = VisibleTextToCells(oriented, cells, VISIBLE_TEXT_MAX);
    snprintf(cache, sizeof(cache), "%s", oriented);
    if ((g_displayEnabled == 0U) && (g_bootPhase == BOOT_DONE) &&
        (g_editMode == EDIT_NONE)) {
        cache[0] = '\0';
        count = 0U;
    }

    if (count > DISPLAY_WIDTH) {
        maxStart = (uint8_t)(count - DISPLAY_WIDTH);
        if (g_scrollOffset > maxStart) {
            g_scrollOffset = 0U;
        }
        if (g_displayFormat == FORMAT_RIGHT) {
            startIndex = (uint8_t)(maxStart - g_scrollOffset);
        } else {
            startIndex = g_scrollOffset;
        }
    } else {
        g_scrollOffset = 0U;
    }

    ComposeFrameFromCells(cells, count, startIndex, &g_currentFrame);
    ApplyEditBlink(&g_currentFrame);
    RenderFrameToSegments(&g_currentFrame);
    UpdateLedHardware(forceEvent);

    if ((forceEvent != 0U) ||
        (memcmp(&g_currentFrame, &g_previousFrame, sizeof(g_currentFrame)) != 0)) {
        memcpy(&g_previousFrame, &g_currentFrame, sizeof(g_currentFrame));
        EmitDisplayEvent();
    }
}

static void Display_ScanNextDigit(void)
{
    uint8_t selectMask = 0U;

    I2C0_WriteByte(TCA6424_I2CADDR, TCA6424_OUTPUT_PORT2, 0x00U);
    I2C0_WriteByte(TCA6424_I2CADDR, TCA6424_OUTPUT_PORT1,
                   g_currentSegments[g_currentDigit]);
    selectMask = (uint8_t)(1U << g_currentDigit);
    I2C0_WriteByte(TCA6424_I2CADDR, TCA6424_OUTPUT_PORT2, selectMask);

    g_currentDigit++;
    if (g_currentDigit >= DISPLAY_WIDTH) {
        g_currentDigit = 0U;
    }
}

static uint8_t Encode7Seg(char value)
{
    char upper = ToUpperAscii(value);
    switch (upper) {
    case '0': return 0x3FU;
    case '1': return 0x06U;
    case '2': return 0x5BU;
    case '3': return 0x4FU;
    case '4': return 0x66U;
    case '5': return 0x6DU;
    case '6': return 0x7DU;
    case '7': return 0x07U;
    case '8': return 0x7FU;
    case '9': return 0x6FU;
    case 'A': return 0x77U;
    case 'B': return 0x7CU;
    case 'C': return 0x39U;
    case 'D': return 0x5EU;
    case 'E': return 0x79U;
    case 'F': return 0x71U;
    case 'G': return 0x3DU;
    case 'H': return 0x76U;
    case 'I': return 0x06U;
    case 'J': return 0x1EU;
    case 'K': return 0x76U;
    case 'L': return 0x38U;
    case 'M': return 0x37U;
    case 'N': return 0x54U;
    case 'O': return 0x3FU;
    case 'P': return 0x73U;
    case 'Q': return 0x67U;
    case 'R': return 0x50U;
    case 'S': return 0x6DU;
    case 'T': return 0x78U;
    case 'U': return 0x3EU;
    case 'V': return 0x3EU;
    case 'W': return 0x3EU;
    case 'X': return 0x76U;
    case 'Y': return 0x6EU;
    case 'Z': return 0x5BU;
    case '-': return 0x40U;
    case '_': return 0x08U;
    case ' ': return 0x00U;
    default:  return 0x00U;
    }
}

static uint8_t BuildSystemLedByte(void)
{
    uint8_t result = 0U;

    if (g_heartbeatBit != 0U) {
        result |= LED_HEARTBEAT;
    }
    if (g_alarm.enabled != 0U) {
        result |= LED_ALARM;
    }
    if (g_buzzerMode == BUZZER_ALARM) {
        if (g_alarmBlinkBit != 0U) {
            result ^= LED_ALARM;
        }
    }
    if (g_editMode != EDIT_NONE) {
        result |= LED_EDIT;
    }
    if (g_millis < g_rxFlashUntilMs) {
        result |= LED_UART_RX;
    }
    if (g_millis < g_txFlashUntilMs) {
        result |= LED_UART_TX;
    }
    if (g_dayNight == MODE_NIGHT) {
        result = 0U;
        if (g_heartbeatBit != 0U) {
            result |= LED_HEARTBEAT;
        }
        return result;
    }
    if (g_dayNight == MODE_NIGHT) {
        result |= LED_MODE_NIGHT;
    }
    if (g_displayFormat == FORMAT_RIGHT) {
        result |= LED_FORMAT_RIGHT;
    }
    if ((g_manualLedUntilMs > g_millis) &&
        ((g_manualLedMask & LED_MANUAL_BIT) != 0U)) {
        result |= LED_MANUAL_BIT;
    }
    return result;
}

static void UpdateLedHardware(bool forceEvent)
{
    uint8_t actualByte = BuildSystemLedByte();

    if (g_manualLedUntilMs > g_millis) {
        actualByte = g_manualLedMask;
    }

    g_ledByte = actualByte;
    if ((forceEvent != 0U) || (g_ledByte != g_prevLedByte)) {
        g_prevLedByte = g_ledByte;
        I2C0_WriteByte(PCA9557_I2CADDR, PCA9557_OUTPUT,
                       (uint8_t)(~g_ledByte));
        EmitLedEvent();
        return;
    }
    I2C0_WriteByte(PCA9557_I2CADDR, PCA9557_OUTPUT,
                   (uint8_t)(~g_ledByte));
}

static void EmitDisplayEvent(void)
{
    char payload[DISPLAY_WIDTH + 1U];
    char buffer[48];
    uint8_t index;

    for (index = 0U; index < DISPLAY_WIDTH; index++) {
        payload[index] = (g_currentFrame.chars[index] == ' ') ? '_' :
                         g_currentFrame.chars[index];
    }
    payload[DISPLAY_WIDTH] = '\0';
    snprintf(buffer, sizeof(buffer), "*EVT:DISP %s %02X",
             payload, (unsigned)g_currentFrame.dp_mask);
    UART_WriteLine(buffer);
}

static void EmitLedEvent(void)
{
    char buffer[24];
    snprintf(buffer, sizeof(buffer), "*EVT:LED %02X", (unsigned)g_ledByte);
    UART_WriteLine(buffer);
}

static void EmitModeEvent(void)
{
    UART_WriteLine((g_dayNight == MODE_NIGHT) ?
                   "*EVT:MODE NIGHT" : "*EVT:MODE DAY");
}

static void EmitEditEvent(EditMode mode)
{
    char text[32];
    char buffer[64];

    if (mode == EDIT_DATE) {
        BuildDottedDateYear(&g_now, text, sizeof(text));
        snprintf(buffer, sizeof(buffer), "*EVT:EDIT DATE %s", text);
        UART_WriteLine(buffer);
        return;
    }
    if (mode == EDIT_TIME) {
        BuildDottedTime(&g_now, text, sizeof(text));
        snprintf(buffer, sizeof(buffer), "*EVT:EDIT TIME %s", text);
        UART_WriteLine(buffer);
        return;
    }
    BuildDottedAlarm(&g_alarm, text, sizeof(text));
    snprintf(buffer, sizeof(buffer), "*EVT:EDIT ALARM %s", text);
    UART_WriteLine(buffer);
}

static void EmitKeyEvent(KeyCode key)
{
    char buffer[32];
    snprintf(buffer, sizeof(buffer), "*EVT:KEY %s", kKeyNames[key]);
    UART_WriteLine(buffer);
}

static void Keys_Scan(void)
{
    uint8_t rawKeypad = I2C0_ReadByte(TCA6424_I2CADDR, TCA6424_INPUT_PORT0);
    uint8_t rawUser = (uint8_t)GPIOPinRead(GPIO_PORTJ_BASE, USER_GPIO_MASK);
    uint8_t keypadPressedMask = (uint8_t)(~rawKeypad);
    uint8_t index;

    /*
     * The student's EXP1/EXP1_SW1_SW2 projects only use PJ0/PJ1 as stable
     * local inputs. The blue keypad sub-board exposes KEYPAD1..8 through
     * TCA6424 port 0, but in practice we only accept clean one-key presses
     * from that port. Any multi-bit or noisy pattern is treated as "no key"
     * so the board cannot self-trigger a key storm.
     */
    for (index = 0U; index < KEY_COUNT; index++) {
        g_lastRawKeys[index] = 0U;
    }

    keypadPressedMask &= KEY_I2C_MASK;
    if ((keypadPressedMask != 0U) &&
        ((keypadPressedMask & (uint8_t)(keypadPressedMask - 1U)) == 0U)) {
        for (index = 0U; index < 8U; index++) {
            if ((keypadPressedMask & (uint8_t)(1U << index)) != 0U) {
                g_lastRawKeys[index] = 1U;
                break;
            }
        }
    }

    g_lastRawKeys[KEY_USER1] = ((rawUser & GPIO_PIN_0) == 0U) ? 1U : 0U;
    g_lastRawKeys[KEY_USER2] = ((rawUser & GPIO_PIN_1) == 0U) ? 1U : 0U;

    for (index = 0U; index < KEY_COUNT; index++) {
        if (g_lastRawKeys[index] == g_stableKeys[index]) {
            g_debounceCounts[index] = 0U;
            if (g_stableKeys[index] != 0U) {
                g_holdTicks[index]++;
                if ((index == KEY_FUNC) &&
                    (g_holdTicks[index] >= FUNC_LONG_PRESS_TICKS) &&
                    (g_longPressDone[index] == 0U)) {
                    g_longPressDone[index] = 1U;
                    HandleFuncLongPress();
                }
                if ((index == KEY_ADD) &&
                    (g_editMode != EDIT_NONE) &&
                    (g_holdTicks[index] >= ADD_REPEAT_START_TICKS) &&
                    (((g_holdTicks[index] - ADD_REPEAT_START_TICKS) %
                      ADD_REPEAT_INTERVAL_TICKS) == 0U)) {
                    IncrementEditField();
                }
            }
            continue;
        }

        g_debounceCounts[index]++;
        if (g_debounceCounts[index] < DEBOUNCE_TICKS) {
            continue;
        }

        g_debounceCounts[index] = 0U;
        g_stableKeys[index] = g_lastRawKeys[index];
        g_holdTicks[index] = 0U;
        g_longPressDone[index] = 0U;
        if (g_stableKeys[index] != 0U) {
            HandleKeyPress((KeyCode)index, true);
        } else {
            HandleKeyRelease((KeyCode)index);
        }
    }
}

static void HandleKeyPress(KeyCode key, bool emitEvent)
{
    if (emitEvent) {
        EmitKeyEvent(key);
    }

    if (g_editMode != EDIT_NONE) {
        g_editDeadlineMs = g_millis + EDIT_TIMEOUT_MS;
    }

    if ((g_buzzerMode == BUZZER_ALARM) && (key == KEY_FUNC)) {
        StopBuzzer();
        UART_WriteLine("*EVT:ALARM_OFF");
        RefreshDisplayAndLeds(true);
        return;
    }

    switch (key) {
    case KEY_FUNC:
        if (g_editMode == EDIT_NONE) {
            EnterEditMode(EDIT_DATE);
        } else if (g_editMode == EDIT_DATE) {
            EnterEditMode(EDIT_TIME);
        } else if (g_editMode == EDIT_TIME) {
            EnterEditMode(EDIT_ALARM);
        } else {
            ExitEditMode(false);
        }
        break;
    case KEY_SHIFT:
        AdvanceEditField();
        break;
    case KEY_ADD:
        IncrementEditField();
        break;
    case KEY_SAVE:
        SaveEditState();
        break;
    case KEY_DISP:
        if (g_editMode == EDIT_NONE) {
            g_viewMode = (ViewMode)(((uint8_t)g_viewMode + 1U) % 3U);
        }
        break;
    case KEY_SPEED:
        g_scrollFast = (uint8_t)!g_scrollFast;
        g_nextScrollMs = g_millis +
            (g_scrollFast != 0U ? SCROLL_FAST_MS : SCROLL_SLOW_MS);
        break;
    case KEY_FORMAT:
        g_displayFormat = (g_displayFormat == FORMAT_LEFT) ?
                          FORMAT_RIGHT : FORMAT_LEFT;
        break;
    case KEY_EXT:
        break;
    case KEY_USER1:
        if (g_editMode == EDIT_NONE) {
            g_dayNight = (g_dayNight == MODE_DAY) ? MODE_NIGHT : MODE_DAY;
            EmitModeEvent();
        }
        break;
    case KEY_USER2:
        if (g_editMode != EDIT_NONE) {
            DecrementEditField();
            return;
        }
        break;
    default:
        break;
    }

    RefreshDisplayAndLeds(true);
}

static void HandleKeyRelease(KeyCode key)
{
    (void)key;
}

static void HandleFuncLongPress(void)
{
    if (g_editMode != EDIT_NONE) {
        SaveEditState();
    }
}

static void AdvanceEditField(void)
{
    if (g_editMode == EDIT_NONE) {
        return;
    }
    g_editField = (uint8_t)((g_editField + 1U) % 3U);
    g_editDeadlineMs = g_millis + EDIT_TIMEOUT_MS;
}

static void IncrementEditField(void)
{
    uint8_t days;

    if (g_editMode == EDIT_DATE) {
        if (g_editField == 0U) {
            g_editDateTime.year++;
            if (g_editDateTime.year > 2099U) {
                g_editDateTime.year = 2020U;
            }
        } else if (g_editField == 1U) {
            g_editDateTime.month++;
            if (g_editDateTime.month > 12U) {
                g_editDateTime.month = 1U;
            }
        } else {
            g_editDateTime.day++;
            days = DaysInMonth(g_editDateTime.year, g_editDateTime.month);
            if (g_editDateTime.day > days) {
                g_editDateTime.day = 1U;
            }
        }
        days = DaysInMonth(g_editDateTime.year, g_editDateTime.month);
        if (g_editDateTime.day > days) {
            g_editDateTime.day = days;
        }
    } else if (g_editMode == EDIT_TIME) {
        if (g_editField == 0U) {
            g_editDateTime.hour = (uint8_t)((g_editDateTime.hour + 1U) % 24U);
        } else if (g_editField == 1U) {
            g_editDateTime.minute = (uint8_t)((g_editDateTime.minute + 1U) % 60U);
        } else {
            g_editDateTime.second = (uint8_t)((g_editDateTime.second + 1U) % 60U);
        }
    } else if (g_editMode == EDIT_ALARM) {
        if (g_editField == 0U) {
            g_editAlarm.hour = (uint8_t)((g_editAlarm.hour + 1U) % 24U);
        } else if (g_editField == 1U) {
            g_editAlarm.minute = (uint8_t)((g_editAlarm.minute + 1U) % 60U);
        } else {
            g_editAlarm.second = (uint8_t)((g_editAlarm.second + 1U) % 60U);
        }
        g_editAlarm.enabled = 1U;
    }

    g_editDeadlineMs = g_millis + EDIT_TIMEOUT_MS;
    RefreshDisplayAndLeds(true);
}

static void DecrementEditField(void)
{
    uint8_t days;

    if (g_editMode == EDIT_DATE) {
        if (g_editField == 0U) {
            if (g_editDateTime.year <= 2020U) {
                g_editDateTime.year = 2099U;
            } else {
                g_editDateTime.year--;
            }
        } else if (g_editField == 1U) {
            if (g_editDateTime.month <= 1U) {
                g_editDateTime.month = 12U;
            } else {
                g_editDateTime.month--;
            }
        } else {
            days = DaysInMonth(g_editDateTime.year, g_editDateTime.month);
            if (g_editDateTime.day <= 1U) {
                g_editDateTime.day = days;
            } else {
                g_editDateTime.day--;
            }
        }
        days = DaysInMonth(g_editDateTime.year, g_editDateTime.month);
        if (g_editDateTime.day > days) {
            g_editDateTime.day = days;
        }
    } else if (g_editMode == EDIT_TIME) {
        if (g_editField == 0U) {
            g_editDateTime.hour = (g_editDateTime.hour == 0U) ?
                                  23U : (uint8_t)(g_editDateTime.hour - 1U);
        } else if (g_editField == 1U) {
            g_editDateTime.minute = (g_editDateTime.minute == 0U) ?
                                    59U : (uint8_t)(g_editDateTime.minute - 1U);
        } else {
            g_editDateTime.second = (g_editDateTime.second == 0U) ?
                                    59U : (uint8_t)(g_editDateTime.second - 1U);
        }
    } else if (g_editMode == EDIT_ALARM) {
        if (g_editField == 0U) {
            g_editAlarm.hour = (g_editAlarm.hour == 0U) ?
                               23U : (uint8_t)(g_editAlarm.hour - 1U);
        } else if (g_editField == 1U) {
            g_editAlarm.minute = (g_editAlarm.minute == 0U) ?
                                 59U : (uint8_t)(g_editAlarm.minute - 1U);
        } else {
            g_editAlarm.second = (g_editAlarm.second == 0U) ?
                                 59U : (uint8_t)(g_editAlarm.second - 1U);
        }
        g_editAlarm.enabled = 1U;
    } else {
        return;
    }

    g_editDeadlineMs = g_millis + EDIT_TIMEOUT_MS;
    RefreshDisplayAndLeds(true);
}

static void SaveEditState(void)
{
    if (g_editMode == EDIT_NONE) {
        return;
    }
    ExitEditMode(true);
}

static void EnterEditMode(EditMode mode)
{
    g_editMode = mode;
    g_editField = 0U;
    g_editDeadlineMs = g_millis + EDIT_TIMEOUT_MS;
    g_editDateTime = g_now;
    g_editAlarm = g_alarm;
}

static void ExitEditMode(bool saveChanges)
{
    EditMode oldMode = g_editMode;
    if ((saveChanges != false) && (g_editMode != EDIT_NONE)) {
        if (g_editMode == EDIT_DATE) {
            g_now.year = g_editDateTime.year;
            g_now.month = g_editDateTime.month;
            g_now.day = g_editDateTime.day;
            EmitEditEvent(oldMode);
        } else if (g_editMode == EDIT_TIME) {
            g_now.hour = g_editDateTime.hour;
            g_now.minute = g_editDateTime.minute;
            g_now.second = g_editDateTime.second;
            EmitEditEvent(oldMode);
        } else if (g_editMode == EDIT_ALARM) {
            g_alarm = g_editAlarm;
            EmitEditEvent(oldMode);
        }
    }
    g_editMode = EDIT_NONE;
    g_editField = 0U;
    g_editDeadlineMs = 0U;
    RefreshDisplayAndLeds(true);
}

static bool SimulateKeyPress(const char *nameToken)
{
    uint8_t index;
    for (index = 0U; index < KEY_COUNT; index++) {
        if (MatchToken(nameToken, kKeyNames[index],
                       (uint8_t)StringLengthBounded(kKeyNames[index], 8U)) != false) {
            HandleKeyPress((KeyCode)index, false);
            return true;
        }
    }
    return false;
}

static bool MatchToken(const char *token, const char *canonical,
                       uint8_t minLen)
{
    uint8_t tokenLen = StringLengthBounded(token, 32U);
    uint8_t canonicalLen = StringLengthBounded(canonical, 32U);
    uint8_t index;

    if ((tokenLen < minLen) || (tokenLen > canonicalLen)) {
        return false;
    }

    for (index = 0U; index < tokenLen; index++) {
        if (ToUpperAscii(token[index]) != ToUpperAscii(canonical[index])) {
            return false;
        }
    }
    return true;
}

static const char *SkipSpaces(const char *text)
{
    while ((*text == ' ') || (*text == '\t')) {
        text++;
    }
    return text;
}

static const char *ReadToken(const char *text, char *out, uint8_t outSize)
{
    uint8_t index = 0U;
    text = SkipSpaces(text);
    while ((*text != '\0') && (*text != ' ') && (*text != '\t')) {
        if (index + 1U >= outSize) {
            break;
        }
        out[index] = *text;
        index++;
        text++;
    }
    out[index] = '\0';
    return text;
}

static bool ParseUint(const char *token, uint32_t *value)
{
    uint32_t result = 0U;
    if (*token == '\0') {
        return false;
    }
    while (*token != '\0') {
        if ((*token < '0') || (*token > '9')) {
            return false;
        }
        result = (result * 10U) + (uint32_t)(*token - '0');
        token++;
    }
    *value = result;
    return true;
}

static bool ParseHexByte(const char *token, uint8_t *value)
{
    uint32_t result = 0U;
    uint8_t digits = 0U;
    char ch;
    if (*token == '\0') {
        return false;
    }
    while (*token != '\0') {
        ch = ToUpperAscii(*token);
        result <<= 4U;
        if ((ch >= '0') && (ch <= '9')) {
            result |= (uint32_t)(ch - '0');
        } else if ((ch >= 'A') && (ch <= 'F')) {
            result |= (uint32_t)(10U + (uint8_t)(ch - 'A'));
        } else {
            return false;
        }
        digits++;
        token++;
    }
    if ((digits == 0U) || (digits > 2U)) {
        return false;
    }
    *value = (uint8_t)result;
    return true;
}

static char ToUpperAscii(char value)
{
    if ((value >= 'a') && (value <= 'z')) {
        return (char)(value - ('a' - 'A'));
    }
    return value;
}

static void TrimAscii(char *text)
{
    uint8_t length = StringLengthBounded(text, 127U);
    while ((length > 0U) &&
           ((text[length - 1U] == ' ') || (text[length - 1U] == '\t'))) {
        text[length - 1U] = '\0';
        length--;
    }
}

static uint8_t StringLengthBounded(const char *text, uint8_t maxLen)
{
    uint8_t length = 0U;
    while ((text[length] != '\0') && (length < maxLen)) {
        length++;
    }
    return length;
}

static bool IsLeapYear(uint16_t year)
{
    if ((year % 400U) == 0U) {
        return true;
    }
    if ((year % 100U) == 0U) {
        return false;
    }
    return ((year % 4U) == 0U);
}

static uint8_t DaysInMonth(uint16_t year, uint8_t month)
{
    static const uint8_t kDays[12] = {
        31U, 28U, 31U, 30U, 31U, 30U,
        31U, 31U, 30U, 31U, 30U, 31U
    };
    if ((month == 0U) || (month > 12U)) {
        return 31U;
    }
    if ((month == 2U) && (IsLeapYear(year) != false)) {
        return 29U;
    }
    return kDays[month - 1U];
}

static void AdvanceClockOneSecond(DateTime *value)
{
    uint8_t days;
    value->second++;
    if (value->second < 60U) {
        return;
    }
    value->second = 0U;
    value->minute++;
    if (value->minute < 60U) {
        return;
    }
    value->minute = 0U;
    value->hour++;
    if (value->hour < 24U) {
        return;
    }
    value->hour = 0U;
    value->day++;
    days = DaysInMonth(value->year, value->month);
    if (value->day <= days) {
        return;
    }
    value->day = 1U;
    value->month++;
    if (value->month <= 12U) {
        return;
    }
    value->month = 1U;
    value->year++;
}

static void UART_ProcessLine(char *line)
{
    char token[20];
    const char *params;
    TrimAscii(line);
    params = SkipSpaces(line);

    if (*params == '\0') {
        return;
    }

    if (MatchToken(params, "*PING", 5U) != false) {
        char pong[32];
        snprintf(pong, sizeof(pong), "*PONG %lu",
                 (unsigned long)(g_millis / 1000UL));
        UART_WriteLine(pong);
        return;
    }

    if (MatchToken(params, "*RST", 4U) != false) {
        ResetRuntimeState();
        RefreshDisplayAndLeds(true);
        UART_ReplyOk(NULL);
        EmitModeEvent();
        return;
    }

    if ((ToUpperAscii(params[0]) != '*') ||
        (ToUpperAscii(params[1]) != 'S' && ToUpperAscii(params[1]) != 'G')) {
        UART_ReplyError("SYNTAX");
        return;
    }

    if ((ToUpperAscii(params[1]) == 'S') &&
        (ToUpperAscii(params[2]) == 'E') &&
        (ToUpperAscii(params[3]) == 'T') &&
        (params[4] == ':')) {
        params += 5;
        params = ReadToken(params, token, sizeof(token));
        params = SkipSpaces(params);
        if (MatchToken(token, "DATE", 4U) != false) {
            HandleSetDate(params);
            return;
        }
        if (MatchToken(token, "TIME", 4U) != false) {
            HandleSetTime(params);
            return;
        }
        if (MatchToken(token, "ALARM", 5U) != false) {
            HandleSetAlarm(params);
            return;
        }
        if (MatchToken(token, "DISPLAY", 4U) != false) {
            HandleSetDisplay(params);
            return;
        }
        if (MatchToken(token, "FORMAT", 3U) != false) {
            HandleSetFormat(params);
            return;
        }
        if (MatchToken(token, "MSG", 3U) != false) {
            HandleSetMessage(params);
            return;
        }
        if (MatchToken(token, "BEEP", 4U) != false) {
            HandleSetBeep(params);
            return;
        }
        if (MatchToken(token, "LED", 3U) != false) {
            HandleSetLed(params);
            return;
        }
        if (MatchToken(token, "KEY", 3U) != false) {
            if (SimulateKeyPress(SkipSpaces(params)) != false) {
                UART_ReplyOk(NULL);
            } else {
                UART_ReplyError("PARAM");
            }
            return;
        }
        if (MatchToken(token, "MODE", 4U) != false) {
            HandleSetMode(params);
            return;
        }
        UART_ReplyError("PARAM");
        return;
    }

    if ((ToUpperAscii(params[1]) == 'G') &&
        (ToUpperAscii(params[2]) == 'E') &&
        (ToUpperAscii(params[3]) == 'T') &&
        (params[4] == ':')) {
        HandleGet(params + 5);
        return;
    }

    UART_ReplyError("SYNTAX");
}

static void HandleSetDate(const char *params)
{
    DateTime nextValue = g_now;
    char field[16];
    char valueToken[16];
    uint32_t parsed;
    uint8_t sawField = 0U;

    params = SkipSpaces(params);
    while (*params != '\0') {
        params = ReadToken(params, field, sizeof(field));
        params = SkipSpaces(params);
        if (*params == '\0') {
            UART_ReplyError("SYNTAX");
            return;
        }
        params = ReadToken(params, valueToken, sizeof(valueToken));
        if (ParseUint(valueToken, &parsed) == false) {
            UART_ReplyError("PARAM");
            return;
        }
        if (MatchToken(field, "YEAR", 4U) != false) {
            if ((parsed < 2000U) || (parsed > 2099U)) {
                UART_ReplyError("RANGE");
                return;
            }
            nextValue.year = (uint16_t)parsed;
        } else if (MatchToken(field, "MONTH", 3U) != false) {
            if ((parsed < 1U) || (parsed > 12U)) {
                UART_ReplyError("RANGE");
                return;
            }
            nextValue.month = (uint8_t)parsed;
        } else if (MatchToken(field, "DATE", 4U) != false) {
            if ((parsed < 1U) || (parsed > 31U)) {
                UART_ReplyError("RANGE");
                return;
            }
            nextValue.day = (uint8_t)parsed;
        } else {
            UART_ReplyError("PARAM");
            return;
        }
        sawField = 1U;
        params = SkipSpaces(params);
    }

    if (sawField == 0U) {
        UART_ReplyError("PARAM");
        return;
    }

    if (nextValue.day > DaysInMonth(nextValue.year, nextValue.month)) {
        UART_ReplyError("RANGE");
        return;
    }

    g_now.year = nextValue.year;
    g_now.month = nextValue.month;
    g_now.day = nextValue.day;
    RefreshDisplayAndLeds(true);
    UART_ReplyOk(NULL);
}

static void HandleSetTime(const char *params)
{
    DateTime nextValue = g_now;
    char field[16];
    char valueToken[16];
    uint32_t parsed;
    uint8_t sawField = 0U;

    params = SkipSpaces(params);
    while (*params != '\0') {
        params = ReadToken(params, field, sizeof(field));
        params = SkipSpaces(params);
        if (*params == '\0') {
            UART_ReplyError("SYNTAX");
            return;
        }
        params = ReadToken(params, valueToken, sizeof(valueToken));
        if (ParseUint(valueToken, &parsed) == false) {
            UART_ReplyError("PARAM");
            return;
        }
        if (MatchToken(field, "HOUR", 4U) != false) {
            if (parsed > 23U) {
                UART_ReplyError("RANGE");
                return;
            }
            nextValue.hour = (uint8_t)parsed;
        } else if (MatchToken(field, "MINUTE", 3U) != false) {
            if (parsed > 59U) {
                UART_ReplyError("RANGE");
                return;
            }
            nextValue.minute = (uint8_t)parsed;
        } else if (MatchToken(field, "SECOND", 3U) != false) {
            if (parsed > 59U) {
                UART_ReplyError("RANGE");
                return;
            }
            nextValue.second = (uint8_t)parsed;
        } else {
            UART_ReplyError("PARAM");
            return;
        }
        sawField = 1U;
        params = SkipSpaces(params);
    }

    if (sawField == 0U) {
        UART_ReplyError("PARAM");
        return;
    }

    g_now.hour = nextValue.hour;
    g_now.minute = nextValue.minute;
    g_now.second = nextValue.second;
    RefreshDisplayAndLeds(true);
    UART_ReplyOk(NULL);
}

static void HandleSetAlarm(const char *params)
{
    AlarmState nextValue = g_alarm;
    char field[16];
    char valueToken[16];
    uint32_t parsed;
    uint8_t sawField = 0U;

    params = SkipSpaces(params);
    if (MatchToken(params, "OFF", 3U) != false) {
        g_alarm.enabled = 0U;
        StopBuzzer();
        RefreshDisplayAndLeds(true);
        UART_ReplyOk(NULL);
        return;
    }

    while (*params != '\0') {
        params = ReadToken(params, field, sizeof(field));
        params = SkipSpaces(params);
        if (*params == '\0') {
            UART_ReplyError("SYNTAX");
            return;
        }
        params = ReadToken(params, valueToken, sizeof(valueToken));
        if (ParseUint(valueToken, &parsed) == false) {
            UART_ReplyError("PARAM");
            return;
        }
        if (MatchToken(field, "HOUR", 4U) != false) {
            if (parsed > 23U) {
                UART_ReplyError("RANGE");
                return;
            }
            nextValue.hour = (uint8_t)parsed;
        } else if (MatchToken(field, "MINUTE", 3U) != false) {
            if (parsed > 59U) {
                UART_ReplyError("RANGE");
                return;
            }
            nextValue.minute = (uint8_t)parsed;
        } else if (MatchToken(field, "SECOND", 3U) != false) {
            if (parsed > 59U) {
                UART_ReplyError("RANGE");
                return;
            }
            nextValue.second = (uint8_t)parsed;
        } else {
            UART_ReplyError("PARAM");
            return;
        }
        sawField = 1U;
        params = SkipSpaces(params);
    }

    if (sawField == 0U) {
        UART_ReplyError("PARAM");
        return;
    }

    nextValue.enabled = 1U;
    g_alarm = nextValue;
    RefreshDisplayAndLeds(true);
    UART_ReplyOk(NULL);
}

static void HandleSetDisplay(const char *params)
{
    char token[16];
    params = ReadToken(params, token, sizeof(token));
    if (MatchToken(token, "ON", 2U) != false) {
        g_displayEnabled = 1U;
    } else if (MatchToken(token, "OFF", 2U) != false) {
        g_displayEnabled = 0U;
    } else {
        UART_ReplyError("PARAM");
        return;
    }
    RefreshDisplayAndLeds(true);
    UART_ReplyOk(NULL);
}

static void HandleSetFormat(const char *params)
{
    char token[16];
    params = ReadToken(params, token, sizeof(token));
    if (MatchToken(token, "LEFT", 4U) != false) {
        g_displayFormat = FORMAT_LEFT;
    } else if (MatchToken(token, "RIGHT", 5U) != false) {
        g_displayFormat = FORMAT_RIGHT;
    } else {
        UART_ReplyError("PARAM");
        return;
    }
    g_scrollOffset = 0U;
    RefreshDisplayAndLeds(true);
    UART_ReplyOk(NULL);
}

static void HandleSetMessage(const char *params)
{
    uint8_t length;
    params = SkipSpaces(params);
    if (*params == '\0') {
        UART_ReplyError("PARAM");
        return;
    }
    length = StringLengthBounded(params, 40U);
    if (length > 32U) {
        UART_ReplyError("LEN");
        return;
    }
    snprintf(g_messageText, sizeof(g_messageText), "%s", params);
    g_messageActive = 1U;
    g_messageDeadlineMs = g_millis + MESSAGE_SHOW_MS;
    g_scrollOffset = 0U;
    g_nextScrollMs = g_millis + (g_scrollFast != 0U ?
                                 SCROLL_FAST_MS : SCROLL_SLOW_MS);
    RefreshDisplayAndLeds(true);
    UART_ReplyOk(NULL);
}

static void HandleSetBeep(const char *params)
{
    char token[16];
    uint32_t durationMs;
    params = ReadToken(params, token, sizeof(token));
    if (ParseUint(token, &durationMs) == false) {
        UART_ReplyError("PARAM");
        return;
    }
    if ((durationMs < REMOTE_BEEP_MIN_MS) || (durationMs > REMOTE_BEEP_MAX_MS)) {
        UART_ReplyError("RANGE");
        return;
    }
    StartRemoteBeep(durationMs);
    RefreshDisplayAndLeds(true);
    UART_ReplyOk(NULL);
}

static void HandleSetLed(const char *params)
{
    char token[16];
    uint8_t value;
    params = ReadToken(params, token, sizeof(token));
    if (ParseHexByte(token, &value) == false) {
        UART_ReplyError("PARAM");
        return;
    }
    g_manualLedMask = value;
    g_manualLedUntilMs = g_millis + MANUAL_LED_SHOW_MS;
    RefreshDisplayAndLeds(true);
    UART_ReplyOk(NULL);
}

static void HandleSetMode(const char *params)
{
    char token[16];
    params = ReadToken(params, token, sizeof(token));
    if (MatchToken(token, "DAY", 3U) != false) {
        g_dayNight = MODE_DAY;
    } else if (MatchToken(token, "NIGHT", 5U) != false) {
        g_dayNight = MODE_NIGHT;
    } else {
        UART_ReplyError("PARAM");
        return;
    }
    RefreshDisplayAndLeds(true);
    EmitModeEvent();
    UART_ReplyOk(NULL);
}

static void HandleGet(const char *params)
{
    char token[16];
    char payload[32];

    params = ReadToken(params, token, sizeof(token));
    if (MatchToken(token, "DATE", 4U) != false) {
        BuildDottedDateShort(&g_now, payload, sizeof(payload));
        if (g_displayFormat == FORMAT_RIGHT) {
            char reversed[32];
            ReverseVisibleText(payload, reversed, sizeof(reversed));
            UART_ReplyOk(reversed);
        } else {
            UART_ReplyOk(payload);
        }
        return;
    }
    if (MatchToken(token, "TIME", 4U) != false) {
        BuildDottedTime(&g_now, payload, sizeof(payload));
        if (g_displayFormat == FORMAT_RIGHT) {
            char reversed[32];
            ReverseVisibleText(payload, reversed, sizeof(reversed));
            UART_ReplyOk(reversed);
        } else {
            UART_ReplyOk(payload);
        }
        return;
    }
    if (MatchToken(token, "ALARM", 5U) != false) {
        BuildDottedAlarm(&g_alarm, payload, sizeof(payload));
        if ((g_alarm.enabled != 0U) && (g_displayFormat == FORMAT_RIGHT)) {
            char reversed[32];
            ReverseVisibleText(payload, reversed, sizeof(reversed));
            UART_ReplyOk(reversed);
        } else {
            UART_ReplyOk(payload);
        }
        return;
    }
    if (MatchToken(token, "DISPLAY", 4U) != false) {
        UART_ReplyOk((g_displayEnabled != 0U) ? "ON" : "OFF");
        return;
    }
    if (MatchToken(token, "FORMAT", 3U) != false) {
        UART_ReplyOk((g_displayFormat == FORMAT_RIGHT) ? "RIGHT" : "LEFT");
        return;
    }
    if (MatchToken(token, "MODE", 4U) != false) {
        UART_ReplyOk((g_dayNight == MODE_NIGHT) ? "NIGHT" : "DAY");
        return;
    }

    UART_ReplyError("PARAM");
}

void SysTick_Handler(void)
{
    static uint8_t div10 = 0U;
    static uint8_t div100 = 0U;
    static uint16_t div500 = 0U;
    static uint16_t div1000 = 0U;

    g_millis++;
    if (g_scanTicks < 200U) {
        g_scanTicks++;
    }

    div10++;
    div100++;
    div500++;
    div1000++;

    if (div10 >= KEY_SCAN_PERIOD_MS) {
        div10 = 0U;
        if (g_ticks10ms < 100U) {
            g_ticks10ms++;
        }
    }
    if (div100 >= 100U) {
        div100 = 0U;
        if (g_ticks100ms < 50U) {
            g_ticks100ms++;
        }
    }
    if (div500 >= 500U) {
        div500 = 0U;
        if (g_ticks500ms < 20U) {
            g_ticks500ms++;
        }
    }
    if (div1000 >= DISPLAY_HEARTBEAT_MS) {
        div1000 = 0U;
        if (g_ticks1000ms < 10U) {
            g_ticks1000ms++;
        }
    }
}
