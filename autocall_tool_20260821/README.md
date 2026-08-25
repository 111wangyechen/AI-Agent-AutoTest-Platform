# autocall —— AI台灯 通话重启缺陷 自动长跑复现工具

复现 AI台灯（Lombo SoC / Linux / cmcc_vchat 引擎）"音视频通话中偶现设备重启"的缺陷。

## 原理
台灯拨出 → 手机(iQOO12)自动接听 → 关麦/关扬声器 → 保持通话 60s → 台灯挂断 → 歇 15s → 循环。
台灯和手机两端都无自动接听，所以两端触摸都要注入（台灯写 `/dev/input/event1` 裸事件，手机 `input tap`）。
命中自发重启即停机并保留现场证据。

## 快速开始
```bash
cd "D:/L20-toB/缺陷验证/autocall"
python autocall.py                # 无限长跑，默认 通话60s / 间隔15s
python autocall.py --rounds 2     # 先跑2通冒烟
python autocall.py --talk 90 --gap 20
python autocall.py --no-recover   # 卡死不自动reboot，停机等人工
python status.py                  # 另开终端速查进度 + 设备实时状态
```

**开跑前提**：台灯连 WiFi 且已 adb tcpip；手机 USB 连接、**已解锁、微信在可接听状态**。

## 三种结局
| 结局 | 判据 | 处理 |
|---|---|---|
| **自发重启**（真缺陷） | uptime 归零 或 vchat PID 变，双通道交叉确认 | 立即停机，抓现场到 `evidence/<run_id>_r<N>_reboot/` |
| **HANG**（卡死/忙线，疑同源路径） | adb 长时间失联但 uptime 没变、或 CPU 停滞 | 收证后自动 `adb reboot` 解卡续跑（`--no-recover` 可关） |
| **正常** | 接通且挂断成功 | 计入统计，继续 |

## 状态判定（只用 /proc 实时信号，不用日志）
`/tmp/log.txt` 被 libc 块缓冲、还会自截断，会造成假阳性，**不能**用于实时判定。可靠判据：

| 信号 | 空闲 | 响铃 | 已接通 |
|---|---|---|---|
| 线程数 `ls /proc/PID/task \| wc -l` | 53 | 54 | **91** |
| UDP 媒体端口 `netstat -anup \| grep -c PID` | 0 | 4 | 4 |

## 设备寻址：MAC 锁定（勿改坏）
台灯走 WiFi，重启/DHCP 会换 IP，故 IP 不写死、`resolve_lamp()` 自动发现。
**环境有多台台灯都跑 cmcc_vchat**，只凭进程名会漂到别台造成假阳性重启（已踩坑）。
所以预检时 `cat /sys/class/net/wlan0/address` 锁定被测台灯 MAC，之后换 IP 必须 MAC 匹配才认，
否则判失联等原设备回来。真重启时 MAC 不变，仍能靠它找回同一台。

## 触摸坐标（挂断键随界面变，用错挂不断）
| 动作 | 坐标 | 文件 |
|---|---|---|
| 台灯 主界面呼叫 | (218,121) | `/data/autocall/tap_dial.bin` |
| 台灯 响铃界面挂断 | (158,414) | `/data/autocall/tap_hangup.bin` |
| 台灯 通话中挂断 | (89,419) | `/data/autocall/tap_hangup_incall.bin` |
| 手机 接听 | (951,233) | `input tap` |
| 手机 关麦 / 关扬声器 | (266,1507) / (546,1525) | `input tap` |

## 产物（logs/ 与 evidence/，run_id = 开跑时间戳）
- `rounds_<run_id>.csv` 逐通结果：`round,start,dialed,ringing,answered,thr_peak,talk_sec,hangup,reboot,hang,note`
- `run_<run_id>.log` 运行时间线（PC 时钟）
- `vchat_<run_id>.log` /tmp/log.txt 镜像（块缓冲有滞后，仅事后分析）
- `serial_<run_id>.log` 串口 console（跨重启不断，抓 U-Boot/复位横幅）
- `evidence/<run_id>_r<N>_reboot/` 命中现场：dmesg、ps、meminfo、netstat、vchat 线程栈、手机截图等

## 辅助脚本
| 脚本 | 用途 |
|---|---|
| `status.py` | 速查最新长跑进度 + 设备实时状态 |
| `mktap.py`  | 合成触摸事件 bin：`python mktap.py 89 419 out.bin` |
| `findbtn.py`| 拉 fb0 帧缓冲输出字符图 + 红/绿聚类中心，定位按钮坐标 |
| `probe.py`  | 状态机定标（采集各态特征，产出 53/54/91 映射） |
| `sampler.py`| 资源趋势采样（判泄漏）。注：fd 列解析有已知 bug |

## 排障
- **手机不接听**：来电是微信悬浮层，须手机已解锁、屏幕亮、微信前台；`input tap` 能打进悬浮层。
- **挂不断 / 下一通拨不出**：多半用错挂断坐标。手动清场（通话中界面）：
  ```bash
  adb -s <台灯IP:5555> shell 'cat /data/autocall/tap_hangup_incall.bin > /dev/input/event1'
  ```
  响铃界面则用 `tap_hangup.bin`。
- **触摸文件缺失**：用 mktap.py 生成后 `adb push` 到 `/data/autocall/`（/data 持久，重启不丢）。
- **串口打不开**：COM6 被 XCOM 等占用会降级禁用，仅用 adb，不影响主流程。
- **报重启但设备明明在通话**：查是否漂移到别台——`adb devices` 看几台、比对各自 MAC。

## 时钟提醒
vchat/串口日志用**设备时钟**，run 日志用 **PC 时钟**，两者可能差几十秒（实测设备慢约 73s），
对齐日志时要加偏移。日志时间戳因块缓冲有滞后，事件顺序可靠、精确时刻不可靠。

