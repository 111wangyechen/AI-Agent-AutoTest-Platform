#!/usr/bin/env python3
"""合成台灯 evdev 触摸 tap 事件 (ARM 32-bit input_event = 16 字节)

字段顺序严格复刻真机录制的 dial.bin：BTN_TOUCH 在坐标之前，且不含 MT_SLOT。
偏离这个顺序驱动会丢弃事件（已实测验证）。

用法: python mktap.py <x> <y> <输出文件>
"""
import struct
import sys
import time

# struct input_event: timeval{long tv_sec, long tv_usec} + u16 type + u16 code + s32 value
FMT = '<llHHi'

EV_SYN, EV_KEY, EV_ABS = 0, 1, 3
SYN_REPORT = 0x00
BTN_TOUCH = 0x14a
ABS_MT_TOUCH_MAJOR = 0x30
ABS_MT_WIDTH_MAJOR = 0x32
ABS_MT_POSITION_X = 0x35
ABS_MT_POSITION_Y = 0x36
ABS_MT_TRACKING_ID = 0x39
ABS_MT_PRESSURE = 0x3a

# 同一帧内所有事件共享时间戳，抬起帧比按下帧晚约 55ms（照真机录制的节奏）
PRESS_MS = 0.0555


def tap(x, y, tracking_id=41, pressure=8, t0=None):
    if t0 is None:
        t0 = time.time()
    t1 = t0 + PRESS_MS

    def ev(t, etype, code, value):
        return struct.pack(FMT, int(t), int((t % 1) * 1_000_000), etype, code, value)

    down = (
        ev(t0, EV_KEY, BTN_TOUCH, 1)
        + ev(t0, EV_ABS, ABS_MT_TRACKING_ID, tracking_id)
        + ev(t0, EV_ABS, ABS_MT_POSITION_X, x)
        + ev(t0, EV_ABS, ABS_MT_POSITION_Y, y)
        + ev(t0, EV_ABS, ABS_MT_TOUCH_MAJOR, 1)
        + ev(t0, EV_ABS, ABS_MT_WIDTH_MAJOR, 1)
        + ev(t0, EV_ABS, ABS_MT_PRESSURE, pressure)
        + ev(t0, EV_SYN, SYN_REPORT, 0)
    )
    up = (
        ev(t1, EV_ABS, ABS_MT_TRACKING_ID, -1)
        + ev(t1, EV_KEY, BTN_TOUCH, 0)
        + ev(t1, EV_SYN, SYN_REPORT, 0)
    )
    return down + up


if __name__ == '__main__':
    if len(sys.argv) != 4:
        sys.exit('用法: python mktap.py <x> <y> <输出文件>')
    x, y, out = int(sys.argv[1]), int(sys.argv[2]), sys.argv[3]
    data = tap(x, y)
    with open(out, 'wb') as f:
        f.write(data)
    print(f'wrote {out}: {len(data)} bytes ({len(data) // 16} events) tap=({x},{y})')
