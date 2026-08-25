#!/usr/bin/env python3
"""长跑进度速查：读最新一次 run 的 CSV + 设备实时状态

用法: python status.py
"""
import csv
import glob
import os
import re
import subprocess
import sys

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

LAMP = '192.168.1.109:5555'
BASE = os.path.dirname(os.path.abspath(__file__))


def sh(cmd, timeout=20):
    try:
        p = subprocess.run(['adb', '-s', LAMP, 'shell', cmd],
                           capture_output=True, timeout=timeout)
        return p.stdout.decode('utf-8', 'replace')
    except Exception as e:
        return f'<ERR {e}>'


def latest(pattern):
    fs = glob.glob(os.path.join(BASE, 'logs', pattern))
    return max(fs, key=os.path.getmtime) if fs else None


def main():
    csv_path = latest('rounds_*.csv')
    if not csv_path:
        sys.exit('还没有 rounds_*.csv，长跑可能尚未开始')

    run_id = re.search(r'rounds_(.+)\.csv', os.path.basename(csv_path)).group(1)
    print(f'=== run_id = {run_id} ===')
    print(f'CSV: {csv_path}\n')

    rows = list(csv.DictReader(open(csv_path, encoding='utf-8-sig')))
    if not rows:
        print('尚无完成的轮次')
    else:
        ok = sum(1 for r in rows if r['answered'] == '1' and r['reboot'] != '1')
        bad = sum(1 for r in rows if r['answered'] != '1' and r['reboot'] != '1')
        reboot = sum(1 for r in rows if r['reboot'] == '1')
        talks = [float(r['talk_sec']) for r in rows if r['talk_sec']]

        print(f'总轮次   {len(rows)}')
        print(f'正常通话 {ok}')
        print(f'异常     {bad}')
        print(f'重启命中 {reboot}')
        if talks:
            print(f'累计通话 {sum(talks) / 60:.1f} 分钟'
                  f'（单通均值 {sum(talks) / len(talks):.1f}s）')

        print('\n--- 最近 8 轮 ---')
        print(f'{"轮":>4} {"接通":>4} {"线程":>4} {"通话s":>6} {"挂断":>4} 备注')
        for r in rows[-8:]:
            print(f'{r["round"]:>4} {r["answered"]:>4} {r["thr_peak"]:>4} '
                  f'{r["talk_sec"]:>6} {r["hangup"]:>4} {r["note"]}')

        bads = [r for r in rows if r['note']]
        if bads:
            print(f'\n--- 有备注的轮次（{len(bads)} 个）---')
            for r in bads[-6:]:
                print(f'  第 {r["round"]} 通: {r["note"]}')

    # 设备实时状态
    out = sh("printf '%s|%s|' $(ls /proc/$(ps | grep cmcc_vchat | grep -v grep "
             "| awk '{print $1}' | head -1)/task | wc -l) "
             "$(netstat -anup 2>/dev/null | grep -c cmcc_vchat); "
             "cut -d' ' -f1 /proc/uptime")
    parts = out.strip().split('|')
    print('\n=== 设备实时状态 ===')
    if len(parts) >= 3:
        thr = parts[0].strip()
        udp = parts[1].strip()
        up = parts[2].strip()
        try:
            st = ('已接通' if int(thr) >= 80
                  else '响铃/通话建立中' if int(udp) > 0 else '空闲')
        except ValueError:
            st = '未知'
        try:
            uph = f'{float(up) / 3600:.1f} 小时'
        except ValueError:
            uph = up
        print(f'状态 {st}  线程 {thr}  UDP {udp}  uptime {uph}')
        print('（uptime 若明显变小 = 设备重启过）')
    else:
        print(f'采样失败: {out.strip()[:120]}')

    run_log = latest('run_*.log')
    if run_log:
        print(f'\n=== 运行日志末尾 ===')
        lines = open(run_log, encoding='utf-8', errors='replace').readlines()
        for line in lines[-6:]:
            print('  ' + line.rstrip())


if __name__ == '__main__':
    main()
