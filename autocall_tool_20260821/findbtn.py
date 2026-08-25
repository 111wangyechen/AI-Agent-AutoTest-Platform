#!/usr/bin/env python3
"""读台灯帧缓冲，定位当前界面上的按钮（尤其是红色挂断键）

图片在当前环境无法直接渲染，所以输出字符图 + 色块聚类中心坐标。
fb0: 320x480, 32bpp, stride=1280, virtual_size=320x960（双缓冲）
"""
import struct
import subprocess
import sys

sys.stdout.reconfigure(encoding='utf-8')

LAMP = '192.168.1.109:5555'
W, H, BPP = 320, 480, 4
FRAME = W * H * BPP          # 614400
NFRAME = 2                   # virtual 320x960 = 2 帧


def sh(cmd, timeout=60):
    p = subprocess.run(['adb', '-s', LAMP, 'shell', cmd],
                       capture_output=True, timeout=timeout)
    return p.stdout.decode('utf-8', 'replace')


def dump_fb(local):
    total_kb = FRAME * NFRAME // 1024
    print(sh(f'dd if=/dev/fb0 of=/data/fb.raw bs=1024 count={total_kb} '
             f'2>&1 | tail -1'))
    subprocess.run(['adb', '-s', LAMP, 'pull', '/data/fb.raw', local],
                   capture_output=True, timeout=90)
    sh('rm -f /data/fb.raw')


def px(buf, base, x, y):
    """返回 (r,g,b)。ARGB8888 小端 -> 字节序 B,G,R,A"""
    o = base + (y * W + x) * BPP
    b, g, r = buf[o], buf[o + 1], buf[o + 2]
    return r, g, b


def classify(r, g, b):
    mx = max(r, g, b)
    mn = min(r, g, b)
    if mx < 40:
        return '.'                      # 黑
    if mx - mn < 28:
        return '#' if mx > 170 else ('+' if mx > 90 else ',')   # 灰阶
    if r == mx and r > g + 35 and r > b + 35:
        return 'R'
    if g == mx and g > r + 30 and g > b + 30:
        return 'G'
    if b == mx and b > r + 30 and b > g + 30:
        return 'B'
    if r > b + 40 and g > b + 40:
        return 'Y'
    if r > g + 30 and b > g + 30:
        return 'M'
    if g > r + 20 and b > r + 20:
        return 'C'
    return '?'


def ascii_map(buf, base, cw=8, ch=16):
    print(f'    字符图（每格 {cw}x{ch}px）  '
          f'R=红 G=绿 B=蓝 Y=黄 C=青 M=品红 #=亮灰 +=中灰 ,=暗灰 .=黑')
    hdr = '     ' + ''.join(str((i * cw // 10) % 10) for i in range(W // cw))
    print(hdr)
    for gy in range(H // ch):
        row = []
        for gx in range(W // cw):
            rs = gs = bs = 0
            n = 0
            for y in range(gy * ch, (gy + 1) * ch, 4):
                for x in range(gx * cw, (gx + 1) * cw, 2):
                    r, g, b = px(buf, base, x, y)
                    rs += r
                    gs += g
                    bs += b
                    n += 1
            row.append(classify(rs // n, gs // n, bs // n))
        print(f'{gy * ch:4d} ' + ''.join(row))


def clusters(buf, base, want, min_px=60):
    """找指定颜色的连通块，返回 [(面积,cx,cy,x0,y0,x1,y1)]"""
    mask = bytearray(W * H)
    for y in range(0, H, 2):
        for x in range(0, W, 2):
            r, g, b = px(buf, base, x, y)
            if classify(r, g, b) == want:
                mask[y * W + x] = 1
    out = []
    seen = bytearray(W * H)
    for y in range(0, H, 2):
        for x in range(0, W, 2):
            i = y * W + x
            if not mask[i] or seen[i]:
                continue
            stack = [(x, y)]
            seen[i] = 1
            pts = []
            while stack:
                cx, cy = stack.pop()
                pts.append((cx, cy))
                for dx, dy in ((2, 0), (-2, 0), (0, 2), (0, -2)):
                    nx, ny = cx + dx, cy + dy
                    if 0 <= nx < W and 0 <= ny < H:
                        j = ny * W + nx
                        if mask[j] and not seen[j]:
                            seen[j] = 1
                            stack.append((nx, ny))
            if len(pts) * 4 >= min_px:
                xs = [p[0] for p in pts]
                ys = [p[1] for p in pts]
                out.append((len(pts) * 4, sum(xs) // len(xs),
                            sum(ys) // len(ys), min(xs), min(ys),
                            max(xs), max(ys)))
    out.sort(reverse=True)
    return out


def main():
    local = 'D:/L20-toB/缺陷验证/autocall/caps/fb.raw'
    dump_fb(local)
    buf = open(local, 'rb').read()
    print(f'fb.raw = {len(buf)} bytes（单帧 {FRAME}）\n')

    for fi in range(min(NFRAME, len(buf) // FRAME)):
        base = fi * FRAME
        nonzero = sum(1 for k in range(base, base + FRAME, 4096) if buf[k])
        print(f'===== 帧 {fi}（offset {base}，非零采样 {nonzero}） =====')
        ascii_map(buf, base)
        for col, name in (('R', '红色'), ('G', '绿色')):
            cs = clusters(buf, base, col)
            if cs:
                print(f'  {name}聚类：')
                for a, cx, cy, x0, y0, x1, y1 in cs[:4]:
                    print(f'    面积{a:>6}  中心=({cx},{cy})  '
                          f'范围 x{x0}-{x1} y{y0}-{y1}')
        print()


if __name__ == '__main__':
    main()
