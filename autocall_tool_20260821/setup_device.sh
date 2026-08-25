#!/usr/bin/env bash
# 一键把触摸事件文件部署到台灯 /data/autocall/
# 用途：烧录新包 / 换设备后，/data 被清空，跑这个把触摸 bin 推回去。
# 用法： bash setup_device.sh [台灯IP:端口]   默认自动发现
set -e
export MSYS_NO_PATHCONV=1

DEV="$1"
if [ -z "$DEV" ]; then
  # 未指定则从 adb devices 里挑一个 tcp 设备（ip:port），排除 USB 手机
  DEV=$(adb devices | awk '/:5555[[:space:]]+device/{print $1; exit}')
fi
if [ -z "$DEV" ]; then
  echo "找不到台灯 tcp 设备，请先 adb connect <台灯IP>:5555，或显式传参"
  exit 1
fi
echo "目标台灯: $DEV"

adb -s "$DEV" shell 'mkdir -p /data/autocall'
for f in tap_dial.bin tap_hangup.bin tap_hangup_incall.bin; do
  adb -s "$DEV" push "assets/$f" "/data/autocall/$f"
done

echo "--- 部署结果 ---"
adb -s "$DEV" shell 'ls -l /data/autocall/'
echo "完成。触摸坐标若因新包 UI 变化失效，用 findbtn.py 重新定位、mktap.py 重新生成。"
