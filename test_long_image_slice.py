#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""long_image_slice.py 的端到端验证。

验证思路：构造一张「每一行的颜色都唯一编码了它自己的 Y 坐标」的 30000px 高测试图，
切完之后逐片检查首行/末行的颜色能否解回正确的原始 Y。这能同时证明：
  - 切片高度与设计一致（不等高模块边界）
  - 切片之间没有重叠、没有漏行、没有偏移
  - 30000px 高度不会触发 Pillow 的像素炸弹保护

用法：python test_long_image_slice.py
"""

import os
import shutil
import subprocess
import sys

from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "long_image_slice.py")
WORK = os.path.join(HERE, "_test_output")

W, H = 16, 30000          # 高度刻意顶到 30000px
CUTS = [1200, 3400, 5600, 12000, 29000]          # 不等高的模块边界
SPANS = [(0, 1200), (1200, 3400), (3400, 5600),
         (5600, 12000), (12000, 29000), (29000, 30000)]

failures = []


def check(name, ok, detail=""):
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  {detail}" if detail else ""))
    if not ok:
        failures.append(name)


def row_color(y):
    """把 Y 坐标编码成唯一颜色：R = y % 256, G = y // 256。"""
    return (y % 256, (y // 256) % 256, 0)


def make_tall_image(path):
    im = Image.new("RGB", (W, H))
    data = []
    for y in range(H):
        data.extend([row_color(y)] * W)
    im.putdata(data)
    im.save(path, "PNG")
    return path


def run(*argv):
    return subprocess.run(
        [sys.executable, SCRIPT, *argv],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )


def jpeg_sampling(path):
    """读 JPEG 的 SOF 段，返回各分量的采样因子。4:4:4 -> [0x11,0x11,0x11]。"""
    with open(path, "rb") as f:
        d = f.read()
    i = 2
    while i < len(d) - 1:
        if d[i] != 0xFF:
            i += 1
            continue
        m = d[i + 1]
        if m == 0xFF:                       # 填充字节
            i += 1
            continue
        if m in (0xD8, 0x01) or 0xD0 <= m <= 0xD7:
            i += 2
            continue
        if m in (0xC0, 0xC1, 0xC2):
            ncomp = d[i + 9]
            out, p = [], i + 10
            for _ in range(ncomp):
                out.append(d[p + 1])
                p += 3
            return out
        i += 2 + int.from_bytes(d[i + 2:i + 4], "big")
    return None


def main():
    if os.path.isdir(WORK):
        shutil.rmtree(WORK)
    os.makedirs(WORK)

    src = os.path.join(WORK, "详情页长图.png")
    make_tall_image(src)

    # ---------- 测试 1：绝对坐标切分，边界精确性 ----------
    print("\n测试 1  --cuts 绝对坐标 / 不等高 / 30000px 高度")
    out1 = os.path.join(WORK, "out_cuts")
    r = run(src, "--cuts", *map(str, CUTS), "--out", out1, "--format", "png")
    check("命令正常退出", r.returncode == 0, r.stderr.strip()[:120])
    check("源图尺寸读取为 30000px", "30000" in r.stdout)

    files = sorted(os.listdir(out1)) if os.path.isdir(out1) else []
    check("切片数量 = 6", len(files) == 6, f"实际 {len(files)}")

    if len(files) == len(SPANS):
        all_ok = True
        for fn, (top, bottom) in zip(files, SPANS):
            with Image.open(os.path.join(out1, fn)) as t:
                exp = (W, bottom - top)
                if t.size != exp:
                    all_ok = False
                    check(f"{fn} 尺寸", False, f"期望 {exp}，实际 {t.size}")
                    continue
                first, last = t.getpixel((0, 0)), t.getpixel((0, t.size[1] - 1))
                if first != row_color(top) or last != row_color(bottom - 1):
                    all_ok = False
                    check(f"{fn} 像素边界", False,
                          f"首行 {first}≠{row_color(top)} 或末行 {last}≠{row_color(bottom - 1)}")
        check("每片高度与首末行像素均精确匹配（无偏移/无重叠/无漏行）", all_ok)

    # ---------- 测试 2：--heights 与 --cuts 等价 ----------
    print("\n测试 2  --heights 分段高度口径")
    out2 = os.path.join(WORK, "out_heights")
    # 最后一片吃掉剩余高度，故只给前 5 个模块高度
    r2 = run(src, "--heights", "1200", "2200", "2200", "6400", "17000",
             "--out", out2, "--format", "png")
    check("命令正常退出", r2.returncode == 0, r2.stderr.strip()[:120])

    same_names = sorted(os.listdir(out2)) == files if os.path.isdir(out2) else False
    same_sizes = same_names and all(
        Image.open(os.path.join(out1, f)).size == Image.open(os.path.join(out2, f)).size
        for f in files
    )
    check("切片文件集合与 --cuts 一致", same_names)
    check("每片尺寸与 --cuts 完全一致", same_sizes)

    # ---------- 测试 3：JPEG 输出正确性 ----------
    print("\n测试 3  JPEG 输出")
    out3 = os.path.join(WORK, "out_jpg")
    r3 = run(src, "--cuts", *map(str, CUTS), "--out", out3)
    check("命令正常退出", r3.returncode == 0, r3.stderr.strip()[:120])

    jfiles = sorted(os.listdir(out3)) if os.path.isdir(out3) else []
    check("切片数量 = 6", len(jfiles) == 6, f"实际 {len(jfiles)}")

    if len(jfiles) == len(SPANS):
        with Image.open(os.path.join(out3, jfiles[2])) as t:
            check("模式转为 RGB（JPEG 不支持透明通道）", t.mode == "RGB", t.mode)
            check("尺寸正确", t.size == (W, SPANS[2][1] - SPANS[2][0]), str(t.size))

        samp = jpeg_sampling(os.path.join(out3, jfiles[2]))
        check("色度抽样为 4:4:4（subsampling=0）",
              samp == [0x11, 0x11, 0x11], f"实际采样因子 {[hex(s) for s in (samp or [])]}")

    # ---------- 测试 4：异常输入应当明确报错 ----------
    print("\n测试 4  异常输入处理")
    cases = [
        ("坐标超出图片高度", run(src, "--cuts", "40000", "--out", os.path.join(WORK, "e1"))),
        ("分段高度之和超限", run(src, "--heights", "20000", "20000", "--out", os.path.join(WORK, "e2"))),
        ("混用 --cuts 与 --heights",
         run(src, "--cuts", "1000", "--heights", "1000", "--out", os.path.join(WORK, "e3"))),
        ("未指定切割位置", run(src, "--out", os.path.join(WORK, "e4"))),
    ]
    for name, res in cases:
        check(f"{name} → 拒绝执行并给出提示",
              res.returncode != 0 and ("错误" in res.stderr or "error" in res.stderr.lower()),
              res.stderr.strip().splitlines()[0][:80] if res.stderr.strip() else "无提示")

    # ---------- 测试 5：dry-run 不落盘 ----------
    print("\n测试 5  --dry-run 只预览不写文件")
    out5 = os.path.join(WORK, "out_dry")
    r5 = run(src, "--cuts", *map(str, CUTS), "--out", out5, "--dry-run")
    check("命令正常退出", r5.returncode == 0)
    check("未创建输出目录", not os.path.isdir(out5))
    check("打印了完整切片清单", r5.stdout.count("Y ") >= 6)

    # ---------- 汇总 ----------
    print("\n" + "=" * 52)
    if failures:
        print(f"失败 {len(failures)} 项：")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("全部通过。")

    shutil.rmtree(WORK, ignore_errors=True)
    print("已清理临时测试文件。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
