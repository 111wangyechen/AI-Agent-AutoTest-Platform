#!/usr/bin/env python3
"""状态机定标探测：一次通话全程采样不受缓冲影响的实时信号

背景：/tmp/log.txt 被 libc 块缓冲（实测卡在 32768 字节边界、落后设备时钟 4 分钟），
不能用于任何实时判定。改用 /proc 与 netstat 的即时信号。
"""
import re
import subprocess
import sys
import time

sys.stdout.reconfigure(encoding='utf-8')

LAMP = '192.168.1.109:5555'
PHONE = '10AE3N0TS500351'
PHONE_ANSWER = (951, 233)


def sh(dev, cmd, timeout=20):
    try:
        p = subprocess.run(['adb', '-s', dev, 'shell', cmd],
                           capture_output=True, timeout=timeout)
        return p.stdout.decode('utf-8', 'replace')
    except Exception as e:
        return f'<ERR {e}>'


def vchat_pid():
    out = sh(LAMP, "ps | grep cmcc_vchat | grep -v grep")
    m = re.search(r'^\s*(\d+)', out.strip())
    return m.group(1) if m else None


def sample(pid):
    """一次 adb 往返取全部指标，减少采样抖动"""
    cmd = (
        f"echo -n \"$(date '+%H:%M:%S')|\"; "
        f"echo -n \"$(netstat -anup 2>/dev/null | grep -c '{pid}/cmcc_vchat')|\"; "
        f"echo -n \"$(awk '{{print $14+$15}}' /proc/{pid}/stat)|\"; "
        f"echo -n \"$(ls /proc/{pid}/task | wc -l)|\"; "
        f"echo \"$(cut -d' ' -f1 /proc/uptime)\""
    )
    out = sh(LAMP, cmd).strip()
    parts = out.split('|')
    if len(parts) < 5:
        return None
    try:
        return {'t': parts[0], 'udp': int(parts[1]), 'cpu': int(parts[2]),
                'thr': int(parts[3]), 'up': float(parts[4])}
    except Exception:
        return None


def show(phase, s, prev):
    if s is None:
        print(f'  {phase:<10} <采样失败>')
        return None
    d = ''
    if prev:
        dcpu = s['cpu'] - prev['cpu']
        d = f'  ΔCPU={dcpu:>4}'
    print(f'  {phase:<10} {s["t"]}  UDP={s["udp"]:<2} CPU={s["cpu"]:<9}'
          f' 线程={s["thr"]:<3} uptime={s["up"]:.0f}{d}')
    return s


def main():
    pid = vchat_pid()
    if not pid:
        sys.exit('找不到 cmcc_vchat 进程')
    print(f'vchat PID = {pid}\n')

    print('=== 阶段1：空闲基线（4 次） ===')
    prev = None
    for _ in range(4):
        prev = show('idle', sample(pid), prev) or prev
        time.sleep(2)

    print('\n=== 阶段2：拨出，响铃中（观察 UDP 是否已出现） ===')
    sh(LAMP, 'cat /data/autocall/tap_dial.bin > /dev/input/event1')
    for _ in range(5):
        time.sleep(2)
        prev = show('ringing', sample(pid), prev) or prev

    print('\n=== 阶段3：手机自动接听 ===')
    sh(PHONE, 'input keyevent KEYCODE_WAKEUP')
    sh(PHONE, f'input tap {PHONE_ANSWER[0]} {PHONE_ANSWER[1]}')
    for _ in range(7):
        time.sleep(2)
        prev = show('connected', sample(pid), prev) or prev

    print('\n=== 阶段4：挂断 ===')
    sh(LAMP, 'cat /data/autocall/tap_hangup.bin > /dev/input/event1')
    for _ in range(5):
        time.sleep(2)
        prev = show('hungup', sample(pid), prev) or prev

    print('\n=== 结论提示 ===')
    print('若 ringing 阶段 UDP=0、connected 阶段 UDP>0，'
          '则 UDP 端口数可精确区分"响铃"与"已接通"')


if __name__ == '__main__':
    main()
