# -*- coding: utf-8 -*-
"""
把切片目录里的所有切片等比缩略后平铺成一张接触印相图，用于人工一眼复核切割质量。
用法：
  python make_contact_sheet.py <切片目录> [--cols 6] [--cell-w 220] [--out 路径]
"""
import argparse
import os

from PIL import Image, ImageDraw

Image.MAX_IMAGE_PIXELS = None

BG = (245, 246, 248)
LINE = (208, 212, 218)
TXT = (34, 38, 44)
ACCENT = (232, 90, 48)


def build_sheet(src_dir, out=None, cols=6, cell_w=220, pad=40, gap=26, label_h=30):
    """把切片目录内的所有切片平铺成一张接触印相图。返回 (输出路径, 宽, 高, 片数)。"""
    files = sorted(
        f for f in os.listdir(src_dir)
        if f.lower().endswith((".jpg", ".jpeg", ".png", ".webp"))
    )
    if not files:
        raise SystemExit("目录中没有图片：" + src_dir)

    thumbs = []
    for f in files:
        im = Image.open(os.path.join(src_dir, f)).convert("RGB")
        w, h = im.size
        tw = cell_w
        th = max(1, round(h * tw / w))
        thumbs.append((f, im.resize((tw, th), Image.LANCZOS), h))

    cols = cols
    rows = (len(thumbs) + cols - 1) // cols
    cell_h = cell_w + 2
    label = label_h

    # 逐行取该行最高缩略图，避免空白浪费
    row_h = []
    for r in range(rows):
        group = thumbs[r * cols:(r + 1) * cols]
        row_h.append(max(t[1].size[1] for t in group) + label)

    W = pad * 2 + cols * cell_h + (cols - 1) * gap
    H = pad * 2 + sum(row_h) + (rows - 1) * gap
    canvas = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(canvas)

    y = pad
    for r in range(rows):
        x = pad
        for c in range(cols):
            i = r * cols + c
            if i >= len(thumbs):
                break
            name, thumb, orig_h = thumbs[i]
            # 单元格边框
            d.rectangle(
                [x - 1, y - 1, x + cell_h, y + thumb.size[1] + 1],
                outline=LINE,
                width=1,
            )
            canvas.paste(thumb, (x, y))
            # 标注：序号 + 该片原始像素高
            idx = name.rsplit("-", 1)[-1].split(".")[0]
            d.text((x, y + thumb.size[1] + 6), "片 %s" % idx, fill=TXT)
            d.text((x + 62, y + thumb.size[1] + 6), "%dpx" % orig_h, fill=ACCENT)
            x += cell_h + gap
        y += row_h[r] + gap

    out = out or os.path.join(
        os.path.dirname(os.path.abspath(src_dir)),
        os.path.basename(os.path.abspath(src_dir)) + "_接触印相图.png",
    )
    canvas.save(out)
    return out, W, H, len(thumbs)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dir")
    ap.add_argument("--cols", type=int, default=6)
    ap.add_argument("--cell-w", type=int, default=220)
    ap.add_argument("--pad", type=int, default=40)
    ap.add_argument("--gap", type=int, default=26)
    ap.add_argument("--label-h", type=int, default=30)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    out, W, H, n = build_sheet(
        args.dir, out=args.out, cols=args.cols, cell_w=args.cell_w,
        pad=args.pad, gap=args.gap, label_h=args.label_h)
    print("拼 %d 片  →  %s" % (n, out))
    print("印相图尺寸 %d × %d px  （%.2f MB）" % (W, H, os.path.getsize(out) / 1048576))


if __name__ == "__main__":
    main()
