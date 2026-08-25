#!/usr/bin/env python3
"""独立资源采样器：记录 vchat 与整机资源随通话次数的变化趋势

用途：判断重启是否由资源累积泄漏导致（若是，则崩溃可预测，且直接指向根因）。
与 autocall.py 并行运行，互不干扰。整机内存仅 72MB，空闲约 6.8MB，压力很紧。

用法: python sampler.py [采样间隔秒，默认20]
"""
import csv
import os
import re
import subprocess
import sys
import time
from datetime import datetime

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

LAMP = '192.168.1.109:5555'
BASE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(BASE, 'logs',
                   f'resource_{datetime.now():%Y%m%d_%H%M%S}.csv')

FIELDS = ['time', 'uptime', 'pid', 'thr', 'udp', 'cpu', 'rss_kb', 'vsz_kb',
          'fd', 'mem_free_kb', 'mem_avail_kb', 'loadavg']


def sh(cmd, timeout=25):
    try:
        p = subprocess.run(['adb', '-s', LAMP, 'shell', cmd],
                           capture_output=True, timeout=timeout)
        return p.stdout.decode('utf-8', 'replace')
    except Exception:
        return ''


def sample():
    out = sh(
        "PID=$(ps | grep cmcc_vchat | grep -v grep | awk '{print $1}' | head -1); "
        "echo \"PID=$PID\"; "
        "cut -d' ' -f1 /proc/uptime; "
        "ls /proc/$PID/task 2>/dev/null | wc -l; "
        "netstat -anup 2>/dev/null | grep -c \"$PID/cmcc_vchat\"; "
        "awk '{print $14+$15}' /proc/$PID/stat 2>/dev/null; "
        "grep -E '^VmRSS|^VmSize' /proc/$PID/status 2>/dev/null; "
        "ls /proc/$PID/fd 2>/dev/null | wc -l; "
        "grep -E '^MemFree|^MemAvailable' /proc/meminfo; "
        "cut -d' ' -f1-3 /proc/loadavg"
    )
    if not out.strip():
        return None
    lines = [x.strip() for x in out.splitlines() if x.strip()]

    def num(pat, default=''):
        for line in lines:
            m = re.search(pat, line)
            if m:
                return m.group(1)
        return default

    try:
        pid = num(r'PID=(\d+)')
        # 位置型字段：uptime / thr / udp / cpu 依次紧跟 PID 行
        idx = next(i for i, x in enumerate(lines) if x.startswith('PID='))
        pos = lines[idx + 1:idx + 5]
        rec = {
            'time': datetime.now().strftime('%H:%M:%S'),
            'uptime': pos[0] if len(pos) > 0 else '',
            'pid': pid,
            'thr': pos[1] if len(pos) > 1 else '',
            'udp': pos[2] if len(pos) > 2 else '',
            'cpu': pos[3] if len(pos) > 3 else '',
            'rss_kb': num(r'VmRSS:\s+(\d+)'),
            'vsz_kb': num(r'VmSize:\s+(\d+)'),
            'fd': '',
            'mem_free_kb': num(r'MemFree:\s+(\d+)'),
            'mem_avail_kb': num(r'MemAvailable:\s+(\d+)'),
            'loadavg': num(r'^([\d.]+ [\d.]+ [\d.]+)$'),
        }
        # fd 数是 VmSize 行之后的那个纯数字
        for i, line in enumerate(lines):
            if line.startswith('VmSize') and i + 1 < len(lines):
                if lines[i + 1].isdigit():
                    rec['fd'] = lines[i + 1]
                break
        return rec
    except (StopIteration, IndexError):
        return None


def main():
    interval = int(sys.argv[1]) if len(sys.argv) > 1 else 20
    print(f'采样间隔 {interval}s -> {OUT}')
    print('Ctrl-C 停止\n')

    with open(OUT, 'w', newline='', encoding='utf-8-sig') as f:
        csv.DictWriter(f, fieldnames=FIELDS).writeheader()

    first = None
    n = 0
    try:
        while True:
            rec = sample()
            if rec:
                n += 1
                with open(OUT, 'a', newline='', encoding='utf-8-sig') as f:
                    csv.DictWriter(f, fieldnames=FIELDS).writerow(rec)
                if first is None:
                    first = rec
                try:
                    d_rss = int(rec['rss_kb']) - int(first['rss_kb'])
                    d_fd = int(rec['fd'] or 0) - int(first['fd'] or 0)
                    delta = f'  ΔRSS={d_rss:+}kB Δfd={d_fd:+}'
                except ValueError:
                    delta = ''
                print(f'[{rec["time"]}] 线程{rec["thr"]:>3} UDP{rec["udp"]:>2} '
                      f'RSS{rec["rss_kb"]:>7}kB fd{rec["fd"]:>4} '
                      f'空闲{rec["mem_free_kb"]:>7}kB '
                      f'可用{rec["mem_avail_kb"]:>7}kB{delta}', flush=True)
            time.sleep(interval)
    except KeyboardInterrupt:
        print(f'\n停止，共 {n} 个采样点 -> {OUT}')


if __name__ == '__main__':
    main()
