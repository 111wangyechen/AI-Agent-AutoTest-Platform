#!/usr/bin/env python3
"""AI台灯 音视频通话重启缺陷 —— 自动长跑复现工具

每轮：台灯拨出 → 手机自动接听 → 关麦/关扬声器 → 保持通话 → 台灯挂断
命中重启立即停止并保存现场证据。

以下实测结论决定了本工具的判定方式，改动前请先读：

1. /tmp/log.txt 被 libc 块缓冲（实测卡在 32768 字节边界、落后设备时钟 4 分钟），
   不能用于任何实时判定。冒烟测试中"喂狗断流 106s"是假阳性，设备其实没重启。
   所以全部状态判定改用 /proc 与 netstat 的即时信号。

2. vchat 线程数是精确的三态判据（实测）：
        空闲 53   响铃 54   已接通 91
   UDP 媒体端口数：空闲 0，响铃/通话 4。接通后 CPU 增速翻倍（~70 → ~120/采样）。

3. 挂断键坐标随界面变化：
        响铃界面 (158,414)   通话中界面 (89,419)
   用错坐标挂不断，通话会一直挂着，导致后续每通都拨不出去。
"""
import csv
import os
import re
import subprocess
import sys
import threading
import time
from datetime import datetime

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

# ==================== 配置 ====================
# 台灯走 WiFi/adb-tcpip，重启或 DHCP 续租后 IP 会变（实测 .109 -> .108），
# 所以 LAMP 不写死：以下是「上次已知地址」，开跑时自动发现真实地址并回填。
# 这对本缺陷尤其关键——要抓的就是重启，而重启可能改 IP、冲垮 adb 与自动恢复。
#
# 【血的教训】环境里有多台台灯（hostname 都叫 virgo、都跑 cmcc_vchat），只凭
# "有 cmcc_vchat 进程"认设备会漂移到别台，造成假阳性重启（实测第37通：
# 漂到 .106 后拿它的 PID/uptime 和 .108 比，误报整机重启，其实 .108 一直在通话）。
# 所以设备身份用 MAC 锁定：开跑记下 LAMP_MAC，换 IP 前必校验 MAC 一致，否则拒绝。
LAMP = '192.168.1.108:5555'         # 运行时会被 resolve_lamp() 覆盖为实际地址
LAMP_PORT = 5555
LAMP_MAC = None                     # 预检时锁定被测台灯的 MAC，之后不认别的设备
PHONE = '10AE3N0TS500351'           # 手机走 USB，序列号固定，是区分两机的锚点

TALK_SEC = 60           # 单通通话保持时长
GAP_SEC = 15            # 每通间隔
MAX_ROUNDS = 0          # 0 = 不限
RING_WAIT = 15          # 拨出后等媒体端口出现
ANSWER_WAIT = 20        # 点接听后等线程数跃升
HANGUP_WAIT = 25        # 挂断后等回落空闲（含 adb 瞬断的重连余量）

# 卡死/忙线（HANG）判据 —— 这是疑似重启缺陷的同源路径，见记忆
# lamp-teardown-hang-on-network-loss：拆流遇网络抖动会 hang 住喂狗线程
HANG_OFFLINE_SEC = 45   # adb 持续无响应超过此秒数 => 疑卡死（uptime 不变则确非重启）
CONSEC_FAIL_LIMIT = 3   # 连续这么多通失败 => 判定台灯进入忙线态，触发恢复
AUTO_RECOVER = True     # 卡死/忙线时自动 reboot 台灯解卡续跑（--no-recover 关闭）
RECOVER_BOOT_WAIT = 90  # 主动 reboot 后等台灯 + vchat 恢复的最长秒数

# 台灯触摸（合成 tap，已推送到 /data/autocall/）
LAMP_TOUCH_NODE = '/dev/input/event1'
TAP_DIAL = '/data/autocall/tap_dial.bin'                  # (218,121) 主界面呼叫
TAP_HANGUP_RING = '/data/autocall/tap_hangup.bin'         # (158,414) 响铃界面挂断
TAP_HANGUP_CALL = '/data/autocall/tap_hangup_incall.bin'  # (89,419) 通话中挂断
LAMP_LOG = '/tmp/log.txt'

# 手机触摸坐标（逻辑分辨率 1080x2400）
# 来电界面是悬浮层，dumpsys 看不到、uiautomator 也 dump 不出，但 input tap 能打进去
PHONE_ANSWER = (951, 233)
PHONE_MUTE_MIC = (266, 1507)
PHONE_MUTE_SPK = (546, 1525)

SERIAL_PORT = 'COM6'
SERIAL_BAUD = 115200

# vchat 线程数三态阈值（实测 53/54/91）
THR_IDLE_MAX = 60       # <= 视为空闲
THR_CONNECTED_MIN = 80  # >= 视为已接通

# vchat CPU 卡死判据：连续多次采样 CPU 完全不增长
CPU_STALL_SAMPLES = 6   # 每次间隔 5s，即 ~30s 无 CPU 推进

BASE = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(BASE, 'logs')
EVI_DIR = os.path.join(BASE, 'evidence')
for _d in (LOG_DIR, EVI_DIR):
    os.makedirs(_d, exist_ok=True)

RUN_ID = datetime.now().strftime('%Y%m%d_%H%M%S')
SERIAL_LOG = os.path.join(LOG_DIR, f'serial_{RUN_ID}.log')
VCHAT_LOG = os.path.join(LOG_DIR, f'vchat_{RUN_ID}.log')
ROUND_CSV = os.path.join(LOG_DIR, f'rounds_{RUN_ID}.csv')
RUN_LOG = os.path.join(LOG_DIR, f'run_{RUN_ID}.log')

STOP = threading.Event()
REBOOT_EVT = threading.Event()      # 抓到「自发重启」缺陷 —— 命中即停机
REBOOT_INFO = []
HANG_EVT = threading.Event()        # 拆流卡死/忙线 —— 触发恢复，不停机
HANG_INFO = []
TOOL_REBOOT = threading.Event()     # 工具主动 reboot 台灯期间置位，抑制误判
LAST_UPTIME = [None]                # 供各线程读取的最新 uptime（判断失联时是否真重启）
VCHAT_PID = None


# ==================== 基础工具 ====================
def ts():
    return datetime.now().strftime('%H:%M:%S.%f')[:-3]


_log_lock = threading.Lock()


def say(msg, tag='*'):
    line = f'[{ts()}] [{tag}] {msg}'
    print(line, flush=True)
    with _log_lock:
        with open(RUN_LOG, 'a', encoding='utf-8') as f:
            f.write(line + '\n')


def sh(dev, cmd, timeout=25):
    """在指定设备上跑 adb shell，返回 (rc, stdout)"""
    try:
        p = subprocess.run(['adb', '-s', dev, 'shell', cmd],
                           capture_output=True, timeout=timeout)
        return p.returncode, p.stdout.decode('utf-8', 'replace')
    except subprocess.TimeoutExpired:
        return -1, '<TIMEOUT>'
    except Exception as e:
        return -2, f'<ERR {e}>'


def adb_devices():
    """返回 [(serial, state)]，state 如 device/offline"""
    try:
        p = subprocess.run(['adb', 'devices'], capture_output=True, timeout=15)
        out = p.stdout.decode('utf-8', 'replace')
    except Exception:
        return []
    res = []
    for ln in out.splitlines()[1:]:
        parts = ln.split()
        if len(parts) >= 2:
            res.append((parts[0], parts[1]))
    return res


def is_lamp_online():
    return any(s == LAMP and st == 'device' for s, st in adb_devices())


def read_mac(dev):
    """读某地址上设备的 WiFi MAC，作为唯一身份。读不到返回 None"""
    rc, out = sh(dev, 'cat /sys/class/net/wlan0/address 2>/dev/null', timeout=10)
    mac = out.strip().lower()
    return mac if re.fullmatch(r'([0-9a-f]{2}:){5}[0-9a-f]{2}', mac) else None


def is_target_lamp(dev):
    """dev 是否就是被测台灯：必须 MAC 匹配锁定值。
    未锁定（预检阶段）时，退化为"有 cmcc_vchat 进程"。"""
    if LAMP_MAC is None:
        rc, out = sh(dev, 'pidof cmcc_vchat', timeout=10)
        return rc == 0 and out.strip().isdigit()
    return read_mac(dev) == LAMP_MAC


def resolve_lamp(candidates=None):
    """自动发现被测台灯的 tcp 地址并回填全局 LAMP。

    台灯走 WiFi，重启/DHCP 续租后 IP 会变（实测 .109->.108），写死会让
    重启后所有 adb 失效、自动恢复也失败——而重启正是本缺陷要抓的时刻。

    【关键】环境有多台台灯都跑 cmcc_vchat，只凭进程名会漂到别台造成假阳性
    重启。所以一旦 LAMP_MAC 锁定，换 IP 必须 MAC 匹配，否则拒绝——宁可判失联
    等原设备回来，也绝不认别的机器。
    策略：① 先试当前 LAMP（MAC 校验）；② 试候选/同网段邻居（MAC 校验）；
    ③ 兜底枚举 tcp 设备（MAC 校验）。全部失败返回 None。
    """
    global LAMP

    # ① 当前地址还活着且 MAC 匹配就直接用
    if is_lamp_online() and is_target_lamp(LAMP):
        return LAMP

    # ② 主动 connect 候选地址（上次已知 + 同网段扫描）
    tried = list(candidates or [])
    if LAMP not in tried:
        tried.insert(0, LAMP)
    m = re.match(r'(\d+\.\d+\.\d+)\.(\d+):(\d+)', LAMP)
    if m:
        base, _, port = m.groups()
        # 同网段常见几个地址（重启后多在原地址附近漂移）
        for last in (108, 109, 110, 107, 111, 106, 112):
            addr = f'{base}.{last}:{port}'
            if addr not in tried:
                tried.append(addr)
    for addr in tried:
        try:
            subprocess.run(['adb', 'connect', addr],
                           capture_output=True, timeout=8)
        except Exception:
            pass
        online = any(s == addr and st == 'device' for s, st in adb_devices())
        if not online:
            continue
        if is_target_lamp(addr):
            if addr != LAMP:
                say(f'台灯地址更新：{LAMP} -> {addr}'
                    f'（MAC 校验通过 {LAMP_MAC}）', 'NET')
            LAMP = addr
            return LAMP
        elif LAMP_MAC is not None:
            # 明确拒绝：这台在线但不是被测设备（另一台台灯）
            other = read_mac(addr)
            if other and other != LAMP_MAC:
                say(f'跳过 {addr}：MAC {other} 非被测台灯（锁定 {LAMP_MAC}）',
                    'NET')

    # ③ 兜底：adb devices 里排除手机后，任一 MAC 匹配的 tcp 设备
    for serial, state in adb_devices():
        if serial == PHONE or state != 'device' or ':' not in serial:
            continue
        if is_target_lamp(serial):
            if serial != LAMP:
                say(f'台灯地址更新：{LAMP} -> {serial}'
                    f'（MAC 校验通过 {LAMP_MAC}）', 'NET')
            LAMP = serial
            return LAMP
    return None


def adb_reconnect(timeout=15):
    """网络抖动/重启后 adb 会掉线。先试当前地址，失败则重新发现（IP 可能已变）"""
    try:
        subprocess.run(['adb', 'connect', LAMP],
                       capture_output=True, timeout=timeout)
    except Exception:
        pass
    if is_lamp_online():
        return True
    return resolve_lamp() is not None


def lamp_tap(binfile):
    return sh(LAMP, f'cat {binfile} > {LAMP_TOUCH_NODE}')


def phone_tap(xy):
    return sh(PHONE, f'input tap {xy[0]} {xy[1]}')


def find_vchat_pid():
    rc, out = sh(LAMP, 'ps | grep cmcc_vchat | grep -v grep', timeout=15)
    m = re.search(r'^\s*(\d+)', out.strip())
    return m.group(1) if m else None


def snap():
    """一次 adb 往返取全部实时指标（不受日志缓冲影响）

    返回 dict(thr, udp, cpu, up) 或 None。
    """
    if VCHAT_PID is None:
        return None
    cmd = (
        f"echo -n \"$(ls /proc/{VCHAT_PID}/task 2>/dev/null | wc -l)|\"; "
        f"echo -n \"$(netstat -anup 2>/dev/null "
        f"| grep -c '{VCHAT_PID}/cmcc_vchat')|\"; "
        f"echo -n \"$(awk '{{print $14+$15}}' /proc/{VCHAT_PID}/stat "
        f"2>/dev/null)|\"; "
        f"echo \"$(cut -d' ' -f1 /proc/uptime)\""
    )
    rc, out = sh(LAMP, cmd, timeout=20)
    if rc != 0:
        return None
    parts = out.strip().split('|')
    if len(parts) < 4:
        return None
    try:
        s = {'thr': int(parts[0]), 'udp': int(parts[1]),
             'cpu': int(parts[2]), 'up': float(parts[3])}
        LAST_UPTIME[0] = s['up']
        return s
    except (ValueError, IndexError):
        return None


def state_of(s):
    """由线程数/UDP 判定通话状态：idle / ringing / connected"""
    if s is None:
        return 'unknown'
    if s['thr'] >= THR_CONNECTED_MIN:
        return 'connected'
    if s['udp'] > 0:
        return 'ringing'
    if s['thr'] <= THR_IDLE_MAX:
        return 'idle'
    return 'unknown'


def wait_state(want, timeout, poll=1.5):
    """等待进入目标状态。返回命中时的 snap 或 None"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if REBOOT_EVT.is_set():
            return None
        s = snap()
        if state_of(s) == want:
            return s
        time.sleep(poll)
    return None


def force_idle(reason=''):
    """确保回到空闲：按界面依次尝试两个挂断坐标"""
    s = snap()
    st = state_of(s)
    if st == 'idle':
        return True
    if reason:
        say(f'清场（{reason}，当前 {st}）', 'CLR')
    for tapfile, label in ((TAP_HANGUP_CALL, '通话中(89,419)'),
                           (TAP_HANGUP_RING, '响铃(158,414)')):
        lamp_tap(tapfile)
        if wait_state('idle', 8):
            say(f'已挂断（{label}）', 'CLR')
            return True
    s = snap()
    say(f'清场失败，仍为 {state_of(s)}（线程 '
        f'{s["thr"] if s else "?"}）', 'CLR!')
    return False


def confirm_hangup(timeout):
    """挂断后确认是否回到空闲，容忍 adb 瞬断。

    实测教训（第13通）：挂断其实成功了（串口 HVX_H264FFDecClose、
    vchat user_hangup->IDLE），但确认窗口正好撞上 adb 失联，被误判成
    「挂断未确认」并引发级联。这里区分三种结局：
      (True,  'idle')               -> 已回空闲，确认成功
      (True,  'offline_no_reboot')  -> adb 瞬断但 uptime 未变，判定已挂断
      (False, 'busy'/'offline_?')   -> 真没挂净（疑卡死/忙线）
    """
    deadline = time.time() + timeout
    saw_offline = False
    up_before = LAST_UPTIME[0]
    while time.time() < deadline:
        if REBOOT_EVT.is_set():
            return False, 'reboot'
        s = snap()
        if s is not None:
            if state_of(s) == 'idle':
                return True, 'idle'
            saw_offline = False        # 又能采到了，重置瞬断标记
        else:
            saw_offline = True
            adb_reconnect()
        time.sleep(1.5)
    # 超时。若结束时仍失联，看是否真重启
    s = snap()
    if s is not None:
        return (True, 'idle') if state_of(s) == 'idle' else (False, 'busy')
    # 仍失联：重连再核一次
    adb_reconnect()
    s = snap()
    if s is not None:
        return (True, 'idle') if state_of(s) == 'idle' else (False, 'busy')
    # 彻底采不到：uptime 无从更新。若之前有基线且未见重启标志，
    # 判为 adb 瞬断（拆流成功但网络抖动），交由 uptime_thread 兜底判重启
    if saw_offline and up_before is not None and not REBOOT_EVT.is_set():
        return True, 'offline_no_reboot'
    return False, 'offline_unknown'


# ==================== 通道1：串口 boot 日志 ====================
# 只匹配确凿的重启/崩溃标志。
# 不能用裸的 watchdog / reboot / WDT —— 台灯 console=ttySLB2，
# cmcc_vchat 的 fd2 指向 /dev/console，应用 stderr 也流到串口，
# 裸关键字会把正常喂狗日志误判成重启，使长跑被假阳性中断。
BOOT_PAT = re.compile(
    r'U-Boot\s+\d|'
    r'Starting kernel|'
    r'Booting Linux on|'
    r'Linux version \d|'
    r'Kernel panic|'
    r'Unable to handle kernel|'
    r'Restarting system|'
    r'watchdog.{0,20}(reset|expired|timeout|bark|bite)|'
    r'SError|Internal error:',
    re.I,
)

# 仅记录、不停机的软告警
WARN_PAT = re.compile(r'Oops|BUG:|segfault|out of memory|oom-kill', re.I)


def hit_reboot(src, detail):
    REBOOT_INFO.append((src, ts(), detail))
    REBOOT_EVT.set()


def hit_hang(src, detail):
    """拆流卡死/忙线：记录并置位 HANG，由主流程决定收证+恢复，不停机"""
    HANG_INFO.append((src, ts(), detail))
    HANG_EVT.set()


def serial_thread():
    try:
        import serial
    except ImportError:
        say('pyserial 未安装，串口通道禁用', 'SER')
        return
    try:
        sp = serial.Serial(SERIAL_PORT, SERIAL_BAUD, timeout=1)
    except Exception as e:
        say(f'{SERIAL_PORT} 打不开（{e.__class__.__name__}：可能被 XCOM 等工具占用），'
            f'串口通道降级禁用，仅用 adb 通道', 'SER')
        return

    say(f'{SERIAL_PORT}@{SERIAL_BAUD} 已接管 -> {os.path.basename(SERIAL_LOG)}',
        'SER')
    buf = b''
    with open(SERIAL_LOG, 'a', encoding='utf-8', errors='replace') as f:
        while not STOP.is_set():
            try:
                data = sp.read(4096)
            except Exception as e:
                say(f'串口读失败: {e}', 'SER')
                break
            if not data:
                continue
            buf += data
            while b'\n' in buf:
                raw, buf = buf.split(b'\n', 1)
                line = raw.decode('utf-8', 'replace').rstrip('\r')
                f.write(f'[{ts()}] {line}\n')
                f.flush()
                if BOOT_PAT.search(line):
                    say(f'串口命中重启/崩溃标志: {line[:160]}', 'SER!')
                    hit_reboot('serial', line[:400])
                elif WARN_PAT.search(line):
                    say(f'串口软告警（仅记录）: {line[:140]}', 'SER')
    try:
        sp.close()
    except Exception:
        pass


# ==================== 通道2：uptime 归零 / vchat 重生 ====================
def uptime_thread():
    """uptime 变小 = 整机重启；vchat PID 变化 = 通话进程被拉起（未必整机重启）"""
    global VCHAT_PID
    s = snap()
    last_up = s['up'] if s else None
    last_pid = VCHAT_PID
    say(f'基线 uptime={last_up}s  vchat PID={last_pid}', 'UP')
    offline_since = None

    hang_flagged = False
    in_tool_reboot = False
    while not STOP.is_set():
        time.sleep(3)
        # 工具主动 reboot 台灯期间，一切失联/uptime归零都属预期，不判缺陷
        if TOOL_REBOOT.is_set():
            offline_since = None
            hang_flagged = False
            in_tool_reboot = True
            continue
        s = snap()
        # 刚结束工具重启：重置基线，否则会把「我们重启后的小 uptime」
        # 误判成自发重启
        if in_tool_reboot:
            in_tool_reboot = False
            last_up = s['up'] if s else None
            last_pid = VCHAT_PID
            offline_since = None
            hang_flagged = False
            if s:
                say(f'工具重启后重置基线 uptime={last_up:.0f}s '
                    f'PID={last_pid}', 'UP')
            continue
        if s is None:
            if offline_since is None:
                offline_since = time.time()
                say('台灯 adb 无响应（可能正在重启，或网络抖动）', 'UP')
            else:
                off = time.time() - offline_since
                if off > 8:
                    # 重启可能改 IP，adb_reconnect 会自动重新发现台灯地址
                    adb_reconnect()
                    pid = find_vchat_pid()
                    if pid and pid != last_pid:
                        say(f'*** vchat PID 变化 {last_pid} -> {pid}，'
                            f'通话进程已重启 ***', 'UP!')
                        hit_reboot('vchat_respawn', f'pid {last_pid} -> {pid}')
                        VCHAT_PID, last_pid = pid, pid
                # 长时间失联但从未见 uptime 变小、PID 也没变 =>
                # 疑拆流卡死/忙线（第13通同源路径），记 HANG，不停机
                if off > HANG_OFFLINE_SEC and not hang_flagged \
                        and not REBOOT_EVT.is_set():
                    say(f'*** 台灯 adb 失联 {off:.0f}s 且无重启迹象 —— '
                        f'疑拆流卡死/忙线 ***', 'UP!')
                    hit_hang('offline_no_reboot',
                             f'adb offline {off:.0f}s, last_uptime='
                             f'{last_up:.0f}s, pid unchanged')
                    hang_flagged = True
            continue

        if offline_since is not None:
            say(f'台灯 adb 恢复，uptime={s["up"]:.0f}s', 'UP')
            offline_since = None
            hang_flagged = False

        if last_up is not None and s['up'] < last_up - 5:
            say(f'*** uptime 归零：{last_up:.0f}s -> {s["up"]:.0f}s，'
                f'整机已重启 ***', 'UP!')
            hit_reboot('uptime', f'uptime {last_up:.0f} -> {s["up"]:.0f}')
        last_up = s['up']


# ==================== 通道3：vchat CPU 卡死 ====================
def cpu_stall_thread():
    """vchat 卡死（不再推进 CPU）是看门狗复位的前兆。
    这是唯一能在重启【之前】抓到征兆的通道。

    注意：不能用日志里的 [wdog] feed ok 判断——log.txt 被块缓冲，
    实测会假报"断流 106s"而设备其实正常。CPU 时间来自 /proc，即时可信。

    重要（第13通实测教训）：CPU 停滞是卡死【征兆】，但实测能自恢复（停滞
    235s 后喂狗恢复、设备没重启）。所以这里只记 HANG（触发收证+观察），
    不直接判重启——真重启由 uptime_thread（uptime 归零/PID 变）独家裁定。
    这样既不会因短暂卡顿假报重启，也不会用工具主动重启抢先毁掉自发重启的现场。
    """
    last_cpu = None
    stall = 0
    hang_flagged = False
    while not STOP.is_set():
        time.sleep(5)
        if TOOL_REBOOT.is_set():
            last_cpu = None
            stall = 0
            hang_flagged = False
            continue
        s = snap()
        if s is None:
            continue
        if last_cpu is not None and s['cpu'] == last_cpu:
            stall += 1
            if stall >= 3:
                say(f'vchat CPU 停滞 {stall * 5}s（线程 {s["thr"]}，'
                    f'UDP {s["udp"]}）', 'CPU')
            if stall >= CPU_STALL_SAMPLES and not hang_flagged \
                    and not REBOOT_EVT.is_set():
                say(f'*** vchat CPU 停滞 {stall * 5}s —— 进程疑似卡死'
                    f'（拆流 hang 征兆，看门狗前兆）***', 'CPU!')
                hit_hang('cpu_stall', f'no CPU progress for {stall * 5}s, '
                         f'thr={s["thr"]}, udp={s["udp"]}')
                hang_flagged = True
        else:
            stall = 0
            hang_flagged = False
        last_cpu = s['cpu']


# ==================== vchat 日志镜像（事后分析用） ====================
def mirror_thread():
    """log.txt 在 tmpfs 上，重启即丢，必须持续镜像到 PC 留证。

    注意：因块缓冲，镜像内容天然滞后，只能事后分析，不能用于实时判定。
    """
    rc, out = sh(LAMP, f'wc -c < {LAMP_LOG}', timeout=10)
    try:
        off = max(1, int(out.strip()) - 20000)
    except (ValueError, AttributeError):
        off = 1
    with open(VCHAT_LOG, 'a', encoding='utf-8', errors='replace') as f:
        while not STOP.is_set():
            time.sleep(4)
            rc, out = sh(LAMP, f'wc -c < {LAMP_LOG}', timeout=10)
            if rc != 0:
                continue
            try:
                size = int(out.strip())
            except ValueError:
                continue
            if size < off:
                # vchat 会自行截断 log.txt（实测 2MB -> 4KB）而设备并未重启，
                # 这里只记录事实，重启判定交给前三路通道
                f.write(f'\n===== [{ts()}] log.txt 被截断（{off} -> {size}），'
                        f'vchat 日志轮转（非重启依据）=====\n')
                f.flush()
                off = 1
            if size > off:
                rc, chunk = sh(LAMP, f'tail -c +{off} {LAMP_LOG} 2>/dev/null',
                               timeout=30)
                if rc == 0 and chunk:
                    f.write(chunk)
                    f.flush()
                    off = size


# ==================== 现场取证 ====================
def collect_evidence(round_no, reason):
    d = os.path.join(EVI_DIR, f'{RUN_ID}_r{round_no}_{reason}')
    os.makedirs(d, exist_ok=True)
    say(f'开始取证 -> {d}', 'EVI')

    # 台灯可能正在重启，逐项尽力抓取，失败不阻塞
    items = {
        'uptime.txt': 'cat /proc/uptime',
        'dmesg.txt': 'dmesg',
        'ps.txt': 'ps',
        'meminfo.txt': 'cat /proc/meminfo',
        'loadavg.txt': 'cat /proc/loadavg',
        'netstat.txt': 'netstat -anp',
        'vchat_tail.txt': f'tail -c 200000 {LAMP_LOG}',
        'ovdlog_tail.txt': 'tail -c 100000 /tmp/hy/OVDLOG.txt',
        'rtclog_a.txt': 'tail -c 100000 /tmp/hy/rtc_Lamp_Xunfei_EEASYTECH_A.txt',
    }
    if VCHAT_PID:
        items['vchat_status.txt'] = f'cat /proc/{VCHAT_PID}/status'
        items['vchat_stack.txt'] = (
            f'for t in /proc/{VCHAT_PID}/task/*; do echo "--- $t"; '
            f'cat $t/stat 2>/dev/null; cat $t/wchan 2>/dev/null; echo; done')

    for name, cmd in items.items():
        rc, out = sh(LAMP, cmd, timeout=40)
        with open(os.path.join(d, name), 'w', encoding='utf-8',
                  errors='replace') as f:
            f.write(f'# rc={rc}  cmd={cmd}\n{out}')

    # 台灯屏幕（帧缓冲原始数据，可用 findbtn.py 解析成字符图）
    rc, _ = sh(LAMP, 'dd if=/dev/fb0 of=/data/fb_evi.raw bs=1024 count=600 '
                     '2>/dev/null', timeout=40)
    subprocess.run(['adb', '-s', LAMP, 'pull', '/data/fb_evi.raw',
                    os.path.join(d, 'lamp_fb.raw')],
                   capture_output=True, timeout=60)
    sh(LAMP, 'rm -f /data/fb_evi.raw')

    # 手机侧截图 + 前台，判断对端是否也异常
    try:
        subprocess.run(['adb', '-s', PHONE, 'shell', 'screencap', '-p',
                        '/sdcard/evi.png'], capture_output=True, timeout=25)
        subprocess.run(['adb', '-s', PHONE, 'pull', '/sdcard/evi.png',
                        os.path.join(d, 'phone_screen.png')],
                       capture_output=True, timeout=25)
        rc, out = sh(PHONE, 'dumpsys window | grep mCurrentFocus', timeout=20)
        with open(os.path.join(d, 'phone_focus.txt'), 'w',
                  encoding='utf-8', errors='replace') as f:
            f.write(out)
    except Exception as e:
        say(f'手机取证失败: {e}', 'EVI')

    with open(os.path.join(d, 'trigger.txt'), 'w', encoding='utf-8') as f:
        f.write(f'run_id={RUN_ID}\nround={round_no}\nreason={reason}\n')
        f.write(f'vchat_pid={VCHAT_PID}\n')
        f.write(f'last_uptime={LAST_UPTIME[0]}\n')
        f.write('\n=== REBOOT 命中记录（自发重启缺陷）===\n')
        for src, t, detail in REBOOT_INFO:
            f.write(f'[{t}] {src}: {detail}\n')
        f.write('\n=== HANG 命中记录（拆流卡死/忙线，疑同源路径）===\n')
        for src, t, detail in HANG_INFO:
            f.write(f'[{t}] {src}: {detail}\n')

    say(f'取证完成: {d}', 'EVI')
    return d


# ==================== 卡死/忙线：协调式恢复 ====================
def recover_lamp(reason):
    """台灯卡死/忙线且清不掉时，主动 reboot 解卡续跑。

    关键：全程置 TOOL_REBOOT，让 uptime_thread/cpu_stall_thread 明白这次
    uptime 归零、PID 变化、失联都是【工具干的】，不要误报成缺陷复现。
    自发重启（真缺陷）在本函数之外发生，仍由 uptime_thread 独家裁定并停机。
    """
    global VCHAT_PID
    if not AUTO_RECOVER:
        say(f'检测到需恢复（{reason}），但 --no-recover 已关闭自动恢复，'
            f'停机等人工处理', 'RCV!')
        return False
    say(f'开始协调式恢复（原因：{reason}）：reboot 台灯解卡', 'RCV')
    TOOL_REBOOT.set()
    try:
        adb_reconnect()
        sh(LAMP, 'reboot', timeout=15)
        subprocess.run(['adb', '-s', LAMP, 'reboot'],
                       capture_output=True, timeout=15)
        time.sleep(20)  # 等设备真正断开
        deadline = time.time() + RECOVER_BOOT_WAIT
        pid = None
        while time.time() < deadline:
            adb_reconnect()
            pid = find_vchat_pid()
            if pid:
                s = snap()
                if s and state_of(s) == 'idle':
                    VCHAT_PID = pid
                    say(f'台灯已恢复：新 vchat PID={pid}，'
                        f'uptime={s["up"]:.0f}s，状态 idle', 'RCV')
                    break
            time.sleep(5)
        else:
            say(f'恢复超时（{RECOVER_BOOT_WAIT}s 内未回到空闲），'
                f'继续尝试但可能需人工介入', 'RCV!')
            if pid:
                VCHAT_PID = pid
        # 清掉本轮 HANG 标记与失联基线，重新开始计数
        HANG_EVT.clear()
        LAST_UPTIME[0] = None
        time.sleep(3)
        return True
    finally:
        TOOL_REBOOT.clear()


# ==================== 单轮通话 ====================
def one_round(n):
    r = {
        'round': n,
        'start': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'dialed': 0, 'ringing': 0, 'answered': 0, 'thr_peak': 0,
        'talk_sec': 0, 'hangup': 0, 'reboot': 0, 'hang': 0, 'note': '',
    }

    # 上一通若没挂净，本通必然拨不出去，先清场
    if not force_idle(f'第 {n} 通开始前'):
        r['note'] = '开始前无法回到空闲'
        return r

    # 手机保持亮屏，否则 input tap 打不到悬浮来电层
    sh(PHONE, 'input keyevent KEYCODE_WAKEUP', timeout=15)

    # 1) 台灯拨出
    rc, _ = lamp_tap(TAP_DIAL)
    if rc != 0:
        r['note'] = 'dial 注入失败'
        return r
    r['dialed'] = 1

    # 2) 等响铃：UDP 媒体端口出现（实测 0 -> 4）
    s = wait_state('ringing', RING_WAIT)
    if s is None:
        if REBOOT_EVT.is_set():
            r['reboot'] = 1
            return r
        cur = snap()
        r['note'] = f'未进入响铃（当前 {state_of(cur)}）'
        say(f'第 {n} 通：{r["note"]}', 'RUN')
        force_idle('响铃失败')
        return r
    r['ringing'] = 1
    say(f'第 {n} 通：已拨出，响铃中（线程 {s["thr"]}，UDP {s["udp"]}）', 'RUN')

    # 3) 手机接听。来电界面是悬浮层（dumpsys/uiautomator 都看不到），
    #    但 input tap 能打进去——已实测验证
    time.sleep(2.5)
    phone_tap(PHONE_ANSWER)

    s = wait_state('connected', ANSWER_WAIT)
    if s is None:
        if REBOOT_EVT.is_set():
            r['reboot'] = 1
            return r
        cur = snap()
        r['note'] = (f'接听未生效（当前 {state_of(cur)}，线程 '
                     f'{cur["thr"] if cur else "?"}；坐标漂移或悬浮层未弹出）')
        say(f'第 {n} 通：{r["note"]}', 'RUN')
        force_idle('接听失败')
        return r
    r['answered'] = 1
    r['thr_peak'] = s['thr']
    say(f'第 {n} 通：已接通（线程 {s["thr"]}）-> 保持 {TALK_SEC}s', 'RUN')

    # 4) 关麦/关扬声器，避免两机同室啸叫
    time.sleep(1.5)
    phone_tap(PHONE_MUTE_MIC)
    time.sleep(0.8)
    phone_tap(PHONE_MUTE_SPK)

    # 5) 保持通话，期间盯重启，并确认媒体一直在跑
    t0 = time.time()
    dropped = False
    while time.time() - t0 < TALK_SEC:
        if REBOOT_EVT.is_set():
            r['talk_sec'] = round(time.time() - t0, 1)
            r['reboot'] = 1
            r['note'] = '通话中检测到重启'
            return r
        time.sleep(3)
        s = snap()
        if s:
            r['thr_peak'] = max(r['thr_peak'], s['thr'])
            if state_of(s) == 'idle':
                dropped = True
                r['note'] = f'通话意外中断（{round(time.time() - t0, 1)}s 时掉话）'
                say(f'第 {n} 通：{r["note"]}', 'RUN!')
                break
    r['talk_sec'] = round(time.time() - t0, 1)

    # 6) 挂断。通话中界面红键在 (89,419)，与响铃界面 (158,414) 不同
    if dropped:
        r['hangup'] = 1
    else:
        lamp_tap(TAP_HANGUP_CALL)
        ok, verdict = confirm_hangup(HANGUP_WAIT)
        r['hangup'] = 1 if ok else 0
        if ok:
            # verdict 可能是 'idle' 或 'offline_no_reboot'（adb 瞬断但已挂断）
            if verdict == 'offline_no_reboot':
                r['note'] = (r['note'] + '; ' if r['note'] else '') + \
                    '挂断已发出，确认时 adb 瞬断（uptime 未变，非重启）'
        elif REBOOT_EVT.is_set():
            r['reboot'] = 1
        else:
            # 真没回到空闲：可能是拆流卡死/忙线，记 note 并尝试清场
            r['note'] = (r['note'] + '; ' if r['note'] else '') + \
                f'挂断后未回空闲（{verdict}）'
            force_idle('挂断未确认')

    say(f'第 {n} 通：完成（通话 {r["talk_sec"]}s，峰值线程 {r["thr_peak"]}，'
        f'挂断{"成功" if r["hangup"] else "失败"}）', 'RUN')
    return r


# ==================== 主流程 ====================
def preflight():
    global VCHAT_PID
    say('=== 预检 ===', 'PRE')
    ok = True

    # 台灯 IP 可能已随重启/DHCP 改变，先自动发现（此时 MAC 未锁定，按进程名认）
    if resolve_lamp() is None:
        say('找不到在线台灯（已试当前地址+同网段扫描+设备枚举）', 'PRE!')
        say('请确认台灯已连 WiFi 且开了 adb tcpip，或手动 adb connect 后重试',
            'PRE!')
        return False
    # 锁定被测台灯 MAC —— 之后 resolve_lamp 只认这台，绝不漂移到别的台灯
    global LAMP_MAC
    LAMP_MAC = read_mac(LAMP)
    if LAMP_MAC is None:
        say('读不到台灯 MAC（/sys/class/net/wlan0/address），'
            'IP 漂移防护无法启用，中止以免误判', 'PRE!')
        return False
    say(f'台灯 {LAMP} 在线，MAC 锁定 {LAMP_MAC}', 'PRE')

    if not any(s == PHONE and st == 'device' for s, st in adb_devices()):
        say(f'手机 {PHONE} 不在线（USB 未连或未授权）', 'PRE!')
        return False
    say(f'手机 {PHONE} 在线', 'PRE')

    for f in (TAP_DIAL, TAP_HANGUP_RING, TAP_HANGUP_CALL):
        rc, o = sh(LAMP, f'test -f {f} && echo yes')
        if 'yes' not in o:
            say(f'台灯缺少触摸文件 {f}（用 mktap.py 生成后 adb push）', 'PRE!')
            ok = False
    if ok:
        say('触摸文件就位（拨号/响铃挂断/通话中挂断）', 'PRE')

    VCHAT_PID = find_vchat_pid()
    if not VCHAT_PID:
        say('找不到 cmcc_vchat 进程', 'PRE!')
        return False
    say(f'vchat PID = {VCHAT_PID}', 'PRE')

    s = snap()
    if s is None:
        say('实时指标采样失败', 'PRE!')
        return False
    say(f'当前状态 = {state_of(s)}（线程 {s["thr"]}，UDP {s["udp"]}，'
        f'uptime {s["up"]:.0f}s）', 'PRE')

    # 台灯若还挂着上一通，第一通必然拨不出去
    if state_of(s) != 'idle':
        if not force_idle('预检'):
            say('无法回到空闲状态，中止', 'PRE!')
            return False

    return ok


def parse_args():
    import argparse
    global TALK_SEC, GAP_SEC, MAX_ROUNDS, AUTO_RECOVER
    ap = argparse.ArgumentParser(description='AI台灯通话重启复现长跑工具')
    ap.add_argument('--talk', type=int, default=TALK_SEC, help='单通通话保持秒数')
    ap.add_argument('--gap', type=int, default=GAP_SEC, help='每通间隔秒数')
    ap.add_argument('--rounds', type=int, default=MAX_ROUNDS,
                    help='最多跑几通，0=不限')
    ap.add_argument('--no-recover', action='store_true',
                    help='卡死/忙线时不自动 reboot 解卡，直接停机等人工')
    a = ap.parse_args()
    TALK_SEC, GAP_SEC, MAX_ROUNDS = a.talk, a.gap, a.rounds
    if a.no_recover:
        AUTO_RECOVER = False


def main():
    parse_args()
    say(f'===== 长跑开始 run_id={RUN_ID} =====')
    say(f'参数：通话 {TALK_SEC}s，间隔 {GAP_SEC}s，'
        f'上限 {"不限" if MAX_ROUNDS == 0 else str(MAX_ROUNDS) + " 通"}')

    if not preflight():
        say('预检未通过，中止', 'PRE!')
        return 1

    for t in (serial_thread, uptime_thread, cpu_stall_thread, mirror_thread):
        threading.Thread(target=t, daemon=True).start()
    time.sleep(2)

    fields = ['round', 'start', 'dialed', 'ringing', 'answered', 'thr_peak',
              'talk_sec', 'hangup', 'reboot', 'hang', 'note']
    with open(ROUND_CSV, 'w', newline='', encoding='utf-8-sig') as f:
        csv.DictWriter(f, fieldnames=fields).writeheader()

    n = 0
    stats = {'ok': 0, 'fail': 0, 'hang': 0, 'recover': 0}
    consec_fail = 0
    try:
        while not REBOOT_EVT.is_set():
            if MAX_ROUNDS and n >= MAX_ROUNDS:
                say(f'已达上限 {MAX_ROUNDS} 通，正常结束')
                break
            n += 1
            r = one_round(n)
            if HANG_EVT.is_set():
                r['hang'] = 1

            with open(ROUND_CSV, 'a', newline='', encoding='utf-8-sig') as f:
                csv.DictWriter(f, fieldnames=fields).writerow(r)

            # ---- 结局1：自发重启 = 真缺陷复现，收证后停机 ----
            if REBOOT_EVT.is_set() or r['reboot']:
                say('*' * 60, 'HIT')
                say(f'*** 复现！第 {n} 通触发【自发重启】 ***', 'HIT')
                say(f'*** 此前累计：正常 {stats["ok"]} / 异常 {stats["fail"]} / '
                    f'卡死恢复 {stats["recover"]} ***', 'HIT')
                for src, t, detail in REBOOT_INFO:
                    say(f'*** 命中通道 [{src}] {t}: {detail}', 'HIT')
                say('*' * 60, 'HIT')
                time.sleep(8)  # 等重启日志落盘
                collect_evidence(n, 'reboot')
                break

            # 统计
            if r['answered'] and r['hangup']:
                stats['ok'] += 1
                consec_fail = 0
            else:
                stats['fail'] += 1
                consec_fail += 1

            # ---- 结局2：拆流卡死/忙线 = 疑同源路径，收证 + 恢复续跑 ----
            need_recover = HANG_EVT.is_set() or consec_fail >= CONSEC_FAIL_LIMIT
            if need_recover:
                stats['hang'] += 1
                why = ('HANG 事件' if HANG_EVT.is_set()
                       else f'连续 {consec_fail} 通失败')
                say('-' * 60, 'HANG')
                say(f'*** 第 {n} 通后检测到卡死/忙线（{why}）—— '
                    f'收证并协调式恢复 ***', 'HANG')
                for src, t, detail in HANG_INFO[-4:]:
                    say(f'*** HANG 通道 [{src}] {t}: {detail}', 'HANG')
                collect_evidence(n, 'hang')
                if recover_lamp(why):
                    stats['recover'] += 1
                    consec_fail = 0
                    HANG_EVT.clear()
                    del HANG_INFO[:]
                else:
                    say('恢复被禁用或失败，停机', 'HANG!')
                    break

            say(f'--- 累计 {n} 通（正常 {stats["ok"]} / 异常 {stats["fail"]} / '
                f'恢复 {stats["recover"]}），休息 {GAP_SEC}s ---')
            for _ in range(GAP_SEC):
                if REBOOT_EVT.is_set():
                    break
                time.sleep(1)
    except KeyboardInterrupt:
        say('收到 Ctrl-C，收尾中…')
        force_idle('用户中断')
    finally:
        STOP.set()
        time.sleep(2)
        say('')
        say('===== 汇总 =====')
        say(f'总轮次 {n}，正常通话 {stats["ok"]}，异常 {stats["fail"]}，'
            f'卡死恢复 {stats["recover"]}，'
            f'自发重启 {"是（已复现！）" if REBOOT_EVT.is_set() else "否"}')
        say(f'逐通记录  {ROUND_CSV}')
        say(f'vchat镜像 {VCHAT_LOG}')
        if os.path.exists(SERIAL_LOG):
            say(f'串口日志  {SERIAL_LOG}')
        say(f'运行日志  {RUN_LOG}')
    return 0


if __name__ == '__main__':
    sys.exit(main())

