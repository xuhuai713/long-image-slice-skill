#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""超长图按指定位置切分 —— 绕过 Photoshop 30000px 导出限制。

背景：PS 的「存储为 Web 所用格式（旧版）」及切片导出有高度 30000px 硬上限，
详情页长图超限后无法切片。本脚本在 PS 之外完成切割。

切割位置有两种口径，二选一，不要混用：

  --cuts     绝对坐标。每个数字是「从图片顶部算起的像素行号」，在该行处断开。
             例：--cuts 1200 3400 5600
             结果：4 片，高度依次为 1200 / 2200 / 2200 / 剩余

  --heights  分段高度。每个数字是「一片的像素高度」，脚本自动累加。
             例：--heights 1200 2200 2200
             结果：与上面完全等价（最后一片吃掉剩余高度）

输出质量：默认 jpg。编码策略经实测确定（见 README 说明）：

  1. 始终 subsampling=0（4:4:4 无色度抽样）。实测长图切片的色度信息密度高、
     边缘锐利，降到 4:2:0 会在霓虹/高饱和边缘产生可见色边；而降低 quality
     换来的体积收益更大且画质损失更小。实测对比（2646px 最坏切片）：
         q100 4:4:4 baseline  1.83MB  PSNR 56.3  SSIM 0.9997
         q96  4:4:4 progressive 0.95MB  PSNR 43.3  SSIM 0.9968   ← 体积达标且更优
         q95  4:2:0 progressive 0.64MB  PSNR 34.9  SSIM 0.9878   ← 更小但画质更差
     故策略为「降质量、死守 4:4:4」，而非「保质量、牺牲色度」。

  2. 始终 progressive=True。实测同质量下 progressive 比 baseline 小 4–5%
     （1.83 → 1.75MB），而 PSNR/SSIM 完全一致 —— 纯免费收益。

  3. --max-size 体积上限：对每片二分搜索「不超过上限的最高质量」。
     已在上限内的切片保持 quality 上限不变，不做任何多余压缩。

注意 JPEG 是有损格式，若要求数学意义上的严格无损，请改用 --format png。
"""

import argparse
import io
import os

from PIL import Image

Image.MAX_IMAGE_PIXELS = None  # 长图动辄数千万像素，关闭默认的像素炸弹保护

MB = 1000 * 1000          # 电商平台所说的「1M」按十进制 1,000,000 字节计
SAFETY = 0.98             # 目标体积留 2% 余量，防止平台按其自身口径判定超限


def build_cuts(total_h, heights):
    """把分段高度列表换算成切线坐标列表（不含图片底部本身）。"""
    acc = 0
    cuts = []
    for h in heights:
        if h <= 0:
            raise SystemExit(f"错误：分段高度必须是正整数，收到 {h}。")
        acc += h
        if acc < total_h:
            cuts.append(acc)
        elif acc == total_h:
            break  # 正好切到底，不需要多余的切线
        else:
            raise SystemExit(f"错误：分段高度之和 {acc} 已超出图片高度 {total_h}。")
    return cuts


def desktop_dir():
    """返回当前用户桌面目录（优先 ~/Desktop，其次 OneDrive 重定向位置）。"""
    home = os.path.expanduser("~")
    for cand in (os.path.join(home, "Desktop"),
                 os.path.join(home, "OneDrive", "Desktop"),
                 os.path.join(home, "OneDrive", "桌面")):
        if os.path.isdir(cand):
            return cand
    return os.path.join(home, "Desktop")   # 不存在也返回，调用处会新建


def default_out_dir():
    """切片的固定输出目录：<桌面>/切图。

    为什么固定：切片是要整批上传到电商平台的「投递物」。输出位置若跟着源图走，
    同一批货就会散落在多个目录里，上传时容易漏片或错页。固定成一个位置后，
    「打开输出目录 → 全选 → 上传」是一条无需思考的路径。
    """
    return os.path.join(desktop_dir(), "切图")


def detect_bands(src, min_len=6):
    """找出「安静行」的连续区段，即模块之间的空白分隔带。返回 [[起,止], ...]。

    判定依据：该行自身像素几乎无变化（行内标准差 ≈ 0），且与上下行之间也
    几乎无变化。真正的模块分隔带满足两条；带内是图片或文字的区段不满足。

    实测（G2783S，750×33744）：全图行跳变强度中位数 4.56、90% 分位 18.61，
    而人工下刀的 22 个位置多数为 0.00 —— 说明分界即空白带。
    """
    try:
        import numpy as np
    except ImportError:
        return []
    with Image.open(src) as im:
        a = np.asarray(im.convert("L"), dtype=np.float32)
    h = a.shape[0]
    row_std = a.std(axis=1)
    diff = np.abs(np.diff(a, axis=0)).mean(axis=1)
    d = np.concatenate([[diff[0]], diff])
    quiet = (row_std < 0.8) & (d < 0.5)

    bands = []
    y = 0
    while y < h:
        if quiet[y]:
            s = y
            while y < h and quiet[y]:
                y += 1
            if y - s >= min_len:
                bands.append([int(s), int(y - 1)])
        else:
            y += 1
    return bands


def propose_cuts(bands, total_h, min_h=1000, max_h=None, min_band=10):
    """按留白宽度择优、并受片高上下限约束，提出一组切割线。

    为什么以留白宽度当分数（实测得出，非推测）：
        本图 750×33744 上，人工真正下刀的位置都在大标题之前的宽留白
        （Y=21916 大标题上方 143px、Y=25756 大标题上方 128px），
        而切得过细的碎片全落在小标题之前的窄留白（7–27px）。
        所以「优先选最宽的留白」等价于「优先切在大标题之前」，
        天然贴合内容层级 —— 大标题开新片，小标题留在片内。

    为什么必须设 min_h：
        原先只在候选之间设了 250px 间距，结果在密集区切出 53–762px 的碎片
        （实测 25 片里有 10 片 <800px，最矮 53px）。加下限后与人工切法吻合：
        min_h=1000 → 23 片、最矮 1041px；人工是 23 片、最矮 961px。

    max_h 防止单片过高导致体积超出平台上限；0/None 表示不限。
    """
    cands = sorted((((s + e) // 2, e - s + 1)
                    for s, e in bands if e - s + 1 >= min_band),
                   key=lambda x: (-x[1], x[0]))

    pts = []
    for c, _n in cands:
        if all(abs(c - p) >= min_h for p in pts):
            pts.append(c)
    pts.sort()
    while pts and pts[0] < min_h:              # 首片过矮 → 去掉第一刀
        pts.pop(0)
    while pts and total_h - pts[-1] < min_h:   # 尾片过矮 → 去掉最后一刀
        pts.pop()

    if max_h:                                   # 过高则用次级留白细分
        changed = True
        while changed:
            changed = False
            edges = [0] + pts + [total_h]
            for i in range(len(edges) - 1):
                a, b = edges[i], edges[i + 1]
                if b - a > max_h:
                    inner = [c for c, _n in cands
                             if a + min_h <= c <= b - min_h and c not in pts]
                    if inner:
                        pts.append(min(inner))
                        pts.sort()
                        changed = True
                        break
    return pts


def resolve_cuts(total_h, cuts, heights):
    """统一成升序去重后的切线坐标列表。"""
    if heights is not None:
        cuts = build_cuts(total_h, heights)

    cuts = sorted(set(cuts))
    for c in cuts:
        if not 0 < c < total_h:
            raise SystemExit(
                f"错误：切割位置 {c} 不在有效区间 (0, {total_h}) 内。\n"
                "  提示：若你想给的是「每片的高度」而不是「绝对坐标」，"
                "请改用 --heights。"
            )
    if not cuts:
        raise SystemExit("错误：没有产生任何切割位置，请检查输入。")
    return cuts


def _encode_jpeg(tile, quality, progressive, subsampling, icc):
    """把一片编码为 JPEG 字节串。"""
    buf = io.BytesIO()
    tile.save(buf, "JPEG", quality=quality, subsampling=subsampling,
              optimize=True, progressive=progressive, icc_profile=icc)
    return buf.getvalue()


def encode_within_limit(tile, limit_bytes, quality_cap=100, quality_floor=70,
                        progressive=True, subsampling=0, icc=None):
    """把一片编码成不超过 limit_bytes 的 JPEG，返回 (data, quality, compressed, warn)。

    先按 quality_cap 试一次：已在限内就直接用（不做多余压缩）。
    超限则对 quality 做二分搜索，取「不超过上限的最高质量」——JPEG 体积随
    quality 单调递增，故二分有效，通常 5~6 次编码即可收敛。
    """
    data = _encode_jpeg(tile, quality_cap, progressive, subsampling, icc)
    if limit_bytes is None or len(data) <= limit_bytes:
        return data, quality_cap, False, None

    lo, hi = quality_floor, quality_cap - 1
    best = None
    while lo <= hi:
        mid = (lo + hi) // 2
        d = _encode_jpeg(tile, mid, progressive, subsampling, icc)
        if len(d) <= limit_bytes:
            best = (d, mid)
            lo = mid + 1
        else:
            hi = mid - 1

    if best is not None:
        return best[0], best[1], True, None

    # 连质量下限都超限：保持 4:4:4 与质量下限，如实报出，不擅自降低色度
    warn = (f"质量下限 {quality_floor} 仍超出体积上限，已按下限输出。"
            "请减少该片高度或改用 --format png")
    return (_encode_jpeg(tile, quality_floor, progressive, subsampling, icc),
            quality_floor, True, warn)


def clean_stale(out_dir, stem, keep_count, fmt):
    """删除输出目录中上一轮遗留的同前缀切片。

    若本轮切出 20 片而目录里还留着上一轮的 -21/-22/-23，按文件名排序上传就会
    多传旧内容、页面错版。这是静默错误，故默认清理。只匹配本工具生成的
    「前缀-纯数字.后缀」文件名，不触碰其他任何文件。
    """
    import re
    pat = re.compile(r"^%s-(\d+)\.(jpg|jpeg|png)$" % re.escape(stem), re.I)
    removed = []
    if not os.path.isdir(out_dir):
        return removed
    for fn in sorted(os.listdir(out_dir)):
        m = pat.match(fn)
        if m and int(m.group(1)) > keep_count:
            os.remove(os.path.join(out_dir, fn))
            removed.append(fn)
    return removed


def slice_image(src, cuts=None, heights=None, out_dir=None, name=None, fmt="jpg",
                quality=100, max_size_mb=1.0, quality_floor=70,
                progressive=True, clean=True, progress=None):
    """切割长图。返回每片的结果 dict 列表。

    max_size_mb  单片体积上限（MB，十进制）。None 或 <=0 表示不限制。
    clean        是否清理上一轮遗留的多余切片，默认 True。
    progress     可选回调 progress(text)，用于把进度推给 UI。
    """
    src = os.path.abspath(src)
    if not os.path.isfile(src):
        raise SystemExit(f"错误：找不到文件 {src}")

    out_dir = out_dir or os.path.join(os.path.dirname(src), "切片")
    stem = name or os.path.splitext(os.path.basename(src))[0]

    limit = None
    if max_size_mb:
        limit = int(max_size_mb * MB * SAFETY)

    results = []
    with Image.open(src) as im:
        w, h = im.size
        edges = [0] + resolve_cuts(h, cuts, heights) + [h]
        spans = [(edges[i], edges[i + 1]) for i in range(len(edges) - 1)]
        pad = max(2, len(str(len(spans))))
        icc = im.info.get("icc_profile")
        os.makedirs(out_dir, exist_ok=True)
        stale = clean_stale(out_dir, stem, len(spans), fmt) if clean else []

        for i, (top, bottom) in enumerate(spans, 1):
            fn = f"{stem}-{i:0{pad}d}.{fmt}"
            path = os.path.join(out_dir, fn)
            if progress:
                progress(f"[{i}/{len(spans)}] {fn}")
            tile = im.crop((0, top, w, bottom))
            rec = {"index": i, "file": fn, "path": path, "top": top,
                   "bottom": bottom, "height": bottom - top, "width": w}
            if fmt == "jpg":
                data, q, comp, warn = encode_within_limit(
                    tile.convert("RGB"), limit, quality_cap=quality,
                    quality_floor=quality_floor, progressive=progressive,
                    subsampling=0, icc=icc)
                with open(path, "wb") as f:
                    f.write(data)
                rec.update(bytes=len(data), quality=q, compressed=comp, warn=warn)
            else:
                tile.save(path, "PNG", optimize=True, icc_profile=icc)
                rec.update(bytes=os.path.getsize(path), quality=None,
                           compressed=False, warn=None)
            rec["mb"] = rec["bytes"] / MB
            results.append(rec)

    return {"src": src, "out_dir": out_dir, "width": w, "height": h,
            "count": len(results), "limit": limit, "stale": stale,
            "slices": results}


def parse_args():
    ap = argparse.ArgumentParser(
        description="超长图按指定位置切分（绕过 PS 30000px 导出限制）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="示例：\n"
               "  先确认尺寸与切片清单：python long_image_slice.py 长图.png --cuts 1200 3400 5600 --dry-run\n"
               "  确认后正式切割：      python long_image_slice.py 长图.png --cuts 1200 3400 5600\n",
    )
    ap.add_argument("image", help="源图片路径（从 PS 导出的整张长图）")

    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--cuts", type=int, nargs="+", metavar="Y",
                   help="绝对坐标：图片顶部算起的像素行号，在该行断开")
    g.add_argument("--heights", type=int, nargs="+", metavar="H",
                   help="分段高度：每片的像素高度，自动累加为切线")

    ap.add_argument("--out", default=None,
                    help="输出目录，默认为 <桌面>/切图")
    ap.add_argument("--name", default=None,
                    help="输出文件名前缀，默认取源文件名")
    ap.add_argument("--format", choices=("jpg", "png"), default="jpg",
                    help="输出格式，默认 jpg（4:4:4 + progressive）")
    ap.add_argument("--quality", type=int, default=100,
                   help="JPEG 质量上限 1-100，默认 100（实际质量按体积上限自动下调）")
    ap.add_argument("--max-size", type=float, default=1.0, metavar="MB",
                    help="单片体积上限，单位 MB（1M=1,000,000 字节），默认 1；填 0 表示不限制")
    ap.add_argument("--quality-floor", type=int, default=70,
                    help="压缩时允许降到的最低质量，默认 70（仍超限则如实报出）")
    ap.add_argument("--no-progressive", action="store_true",
                    help="输出 baseline JPEG（默认 progressive，同质量下小 4–5%%）")
    ap.add_argument("--keep-stale", action="store_true",
                    help="不清理输出目录中上一轮遗留的多余切片（默认会清理，避免错页）")
    ap.add_argument("--dry-run", action="store_true",
                    help="只打印切片清单，不写出文件")
    return ap.parse_args()


def main():
    args = parse_args()

    src = os.path.abspath(args.image)
    if not os.path.isfile(src):
        raise SystemExit(f"错误：找不到文件 {src}")

    out_dir = args.out or default_out_dir()
    stem = args.name or os.path.splitext(os.path.basename(src))[0]

    with Image.open(src) as im:
        w, h = im.size
        cuts = resolve_cuts(h, args.cuts, args.heights)

    edges = [0] + cuts + [h]
    spans = [(edges[i], edges[i + 1]) for i in range(len(edges) - 1)]
    pad = max(2, len(str(len(spans))))

    print(f"源图：{src}")
    print(f"尺寸：{w} × {h} px   共 {len(spans)} 片   格式：{args.format}")
    if args.format == "jpg":
        if args.max_size:
            print(f"质量上限：{args.quality}   单片体积上限：{args.max_size} MB"
                  f"（{int(args.max_size * MB * SAFETY):,} 字节，含 2% 余量）")
        else:
            print(f"质量：{args.quality}   体积：不限制")
        print(f"编码：4:4:4 无色度抽样 · {'progressive' if not args.no_progressive else 'baseline'}")
    print("-" * 52)
    for i, (top, bottom) in enumerate(spans, 1):
        print(f"  {stem}-{i:0{pad}d}.{args.format}   Y {top:>6} – {bottom:<6} 高 {bottom - top}")
    print("-" * 52)

    if args.dry_run:
        print("dry-run：未写出任何文件。确认清单无误后去掉 --dry-run 重跑。")
        return

    res = slice_image(
        src, cuts=cuts, out_dir=out_dir, name=stem, fmt=args.format,
        quality=args.quality, max_size_mb=args.max_size,
        quality_floor=args.quality_floor, progressive=not args.no_progressive,
        clean=not args.keep_stale,
    )

    if res["stale"]:
        print(f"已清理上一轮遗留的 {len(res['stale'])} 个切片："
              f"{', '.join(res['stale'])}")

    for r in res["slices"]:
        if args.format == "jpg":
            tag = "已压缩" if r["compressed"] else "原质量"
            line = (f"  {r['file']:<20} {r['width']}×{r['height']:<6} "
                    f"{r['mb']:>6.2f} MB   质量 {r['quality']:<4}{tag}")
        else:
            line = f"  {r['file']:<20} {r['width']}×{r['height']:<6} {r['mb']:>6.2f} MB"
        print(line)
        if r["warn"]:
            print(f"      ⚠ {r['warn']}")

    over = [r for r in res["slices"] if res["limit"] and r["bytes"] > res["limit"]]
    comp = [r for r in res["slices"] if r["compressed"]]
    print("-" * 52)
    print(f"共 {res['count']} 片   合计 {sum(r['bytes'] for r in res['slices']) / MB:.2f} MB")
    print(f"其中 {len(comp)} 片触发压缩   超出上限 {len(over)} 片")
    print(f"\n完成。输出目录：{out_dir}")


if __name__ == "__main__":
    main()
