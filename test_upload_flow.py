#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""上传换图链路端到端验证。

为什么值得单写一份：`/api/upload` 是一条**二进制**通道，它同时牵动
「HTTP 请求体分流 → 流式落盘 → 重建会话 → 换掉 cfg → 清理旧预览块」
五个环节，任何一环断了都表现为「点了按钮没反应」这种最难查的症状。
所以这里不看代码，只起服务、真发请求、真读回页面。

覆盖：
  1. 无图启动 → 起始页（且未授权请求一律 403）
  2. 上传长图 → 页面换成新图（尺寸/分隔带/输出子目录都对）
  3. 一键切图 → 文件落进 <输出根>/<图名>/，且都 ≤ 体积上限
  4. 再上传一张 → 可重入；旧预览块被回收，不会在临时目录里堆积
  5. 错误路径 → 扩展名不符、空体，都要给出明确中文提示而不是崩掉

运行：
    python test_upload_flow.py
"""
import http.client
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import glob
import random

from PIL import Image, ImageChops

HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
PORT = 8899
BASE = "127.0.0.1"

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if ok else "FAIL", name,
                           ("  " + str(detail)) if detail else ""))


# ---------------------------------------------------------------- 合成素材

def make_long_image(path, w, h, block, gap):
    """造一张带纹理色块 + 纯白分隔带的长图。

    色块必须**有纹理**：`detect_bands` 把「行内 std<0.8 且跨行差<0.5」当安静行，
    纯色块的行也满足这个条件，会导致整张图被当成一条大分隔带，测不出真本事。
    所以给色块叠一层高斯噪声（effect_noise 走 C 实现，比逐像素快几个数量级）。
    """
    im = Image.new("RGB", (w, h), (255, 255, 255))
    colors = [(200, 40, 40), (40, 120, 200), (30, 150, 80),
              (220, 160, 30), (120, 60, 180)]
    rnd = random.Random(7)
    y, idx = gap, 0
    while y + block <= h:
        c = colors[idx % len(colors)]
        blk = Image.new("RGB", (w, block), c)
        noise = Image.effect_noise((w, block), 40).convert("RGB") \
                     .point(lambda v: v // 4)
        blk = ImageChops.add(blk, noise)
        im.paste(blk, (0, y))
        y += block + gap
        idx += 1
    _ = rnd
    im.save(path)
    return im.size


# ---------------------------------------------------------------- HTTP

def req(method, path, body=None, headers=None, port=PORT, timeout=120):
    c = http.client.HTTPConnection(BASE, port, timeout=timeout)
    try:
        c.request(method, path, body=body, headers=headers or {})
        r = c.getresponse()
        return r.status, r.read()
    finally:
        c.close()


def get_page(port=PORT):
    st, raw = req("GET", "/", port=port)
    return st, raw.decode("utf-8", "replace")


def token_of(html):
    m = re.search(r"TOKEN\s*=\s*\"([0-9a-f]+)\"", html)
    return m.group(1) if m else None


def upload(path, port=PORT, token=None, name=None, ctype="image/png"):
    import urllib.parse
    name = name or os.path.basename(path)
    with open(path, "rb") as f:
        data = f.read()
    # 必须百分号编码：浏览器发的是 encodeURIComponent(file.name)，
    # 而 HTTP 头只能承载 latin-1，中文原名塞进去会直接编码失败。
    hdr = {"X-Token": token, "X-Filename": urllib.parse.quote(name),
           "Content-Type": ctype, "Content-Length": str(len(data))}
    st, raw = req("POST", "/api/upload", body=data, headers=hdr, port=port)
    try:
        return st, json.loads(raw)
    except Exception:
        return st, {"error": raw.decode("utf-8", "replace")[:200]}


def wait_ready(port=PORT, timeout=40):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            st, _ = req("GET", "/", port=port, timeout=3)
            if st == 200:
                return True
        except Exception:
            time.sleep(0.3)
    return False


def spawn(extra, cwd=HERE):
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    return subprocess.Popen([PY, "-u", os.path.join(cwd, "slice_studio.py")]
                            + extra, cwd=cwd, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)


# ---------------------------------------------------------------- 主流程

def main():
    tmp = tempfile.mkdtemp(prefix="studio_test_")
    out_root = os.path.join(tmp, "out")
    # 注意：预览块目录是 mkdtemp(prefix="slice_studio_")，落在系统临时目录**根下**，
    # 不是落在 Temp\slice_studio\（那是上传原图的暂存目录）。找错地方会得到
    # 空集合，然后误报「新建了 0 个」。
    stage_parent = tempfile.gettempdir()
    upload_parent = os.path.join(tempfile.gettempdir(), "slice_studio")
    img_a = os.path.join(tmp, "测试长图.png")
    img_b = os.path.join(tmp, "第二张.png")
    txt = os.path.join(tmp, "不是图片.txt")

    print("造测试素材…")
    make_long_image(img_a, 1600, 9000, 900, 120)
    make_long_image(img_b, 1200, 6000, 800, 110)
    with open(txt, "w", encoding="utf-8") as f:
        f.write("not an image")

    print("\n=== 1. 无图启动 → 起始页 ===")
    p = spawn(["Z:\\不存在的目录\\没有这张图.jpg", "--port", str(PORT),
               "--no-open", "--out", out_root])
    try:
        check("服务在 40s 内就绪", wait_ready())
        st, html = get_page()
        check("GET / → 200", st == 200, st)
        check("返回的是「选择长图」起始页",
              "选择长图" in html and "点击选择图片" in html)
        check("起始页不含任何图片尺寸（没有图可言）",
              "原图 0 × 0" not in html and "__W__" not in html)

        st, raw = req("POST", "/api/slice", body=b"{}",
                      headers={"Content-Type": "application/json"})
        check("无 token 调 /api/slice → 403", st == 403, st)

        st, _ = req("POST", "/api/upload", body=b"x",
                    headers={"Content-Type": "image/png",
                             "X-Filename": "a.png"})
        check("无 token 调 /api/upload → 403", st == 403, st)

        st, html = get_page()
        tok = token_of(html)
        check("能从页面取到 TOKEN", bool(tok), tok)

        st, raw = req("POST", "/api/slice", body=b"{}", headers={
            "Content-Type": "application/json", "X-Token": tok})
        j = json.loads(raw)
        check("未选图就切图 → 明确中文提示",
              "error" in j and "还没有选择图片" in j["error"], j)

        print("\n=== 2. 上传长图 A ===")
        before = set(glob.glob(os.path.join(stage_parent, "slice_studio_*")))
        st, j = upload(img_a, token=tok)
        check("上传 → 200 且 ok", st == 200 and j.get("ok"), j)
        check("读到的尺寸正确 1600 × 9000",
              j.get("w") == 1600 and j.get("h") == 9000, (j.get("w"), j.get("h")))
        check("检测到分隔带", j.get("bands", 0) > 0, j.get("bands"))
        check("输出子目录 = <根>/测试长图",
              j.get("out_dir", "").replace("\\", "/").endswith("/out/测试长图"),
              j.get("out_dir"))

        st, html = get_page()
        check("页面标题已换成新图", "<title>切片工作台 · 测试长图.png</title>" in html)
        check("页面尺寸已更新为 1600 × 9000", "原图 1600 × 9000 px" in html)
        check("__H__ 占位符零残留", "__H__" not in html and "__W__" not in html)
        check("BANDS 已注入且非空", "BANDS = [[[" not in html and "BANDS = [" in html)
        check("带上了「换一张图」按钮", "换一张图…" in html)
        check("输出标签显示 切图/图名 层级",
              re.search(r"输出\s+[^<]*out/测试长图/", html) is not None
              or "输出 out/测试长图/" in html,
              re.findall(r"输出[^<]{0,40}", html)[:1])
        after_a = set(glob.glob(os.path.join(stage_parent, "slice_studio_*"))) - before
        check("新建了 1 个预览块目录", len(after_a) == 1, len(after_a))

        print("\n=== 3. 一键切图 ===")
        st, raw = req("POST", "/api/propose",
                      body=json.dumps({"minH": 1000, "maxH": 0}).encode(),
                      headers={"Content-Type": "application/json",
                               "X-Token": tok})
        pj = json.loads(raw)
        check("/api/propose 给出切割线", bool(pj.get("cuts")), pj.get("cuts"))
        check("片高都不小于 min_h=1000",
              all(h >= 1000 for h in pj.get("heights", [])), pj.get("heights"))

        st, raw = req("POST", "/api/slice",
                      body=json.dumps({"cuts": pj["cuts"], "maxSize": 1}).encode(),
                      headers={"Content-Type": "application/json",
                               "X-Token": tok})
        sj = json.loads(raw)
        check("/api/slice 成功", st == 200 and not sj.get("error"), sj.get("error"))
        check("切出多片", sj.get("count", 0) >= 2, sj.get("count"))
        check("全部 ≤1MB", all(s["bytes"] <= 1000000 for s in sj["slices"]),
              max(s["bytes"] for s in sj["slices"]) if sj.get("slices") else None)
        d = sj["out_dir"]
        check("落盘目录 = <根>/测试长图",
              d.replace("\\", "/").endswith("/out/测试长图"), d)
        files = sorted(os.listdir(d))
        check("目录里只有切片 JPG（无 PNG/子目录）",
              all(f.lower().endswith(".jpg") for f in files) and len(files) == sj["count"],
              files[:4])
        check("切片命名带图名前缀",
              all(f.startswith("测试长图-") for f in files), files[:3])

        print("\n=== 4. 再上传一张（可重入 + 旧预览回收）===")
        st, j2 = upload(img_b, token=tok)
        check("第二次上传成功", st == 200 and j2.get("ok"), j2)
        check("尺寸更新为 1200 × 6000",
              j2.get("w") == 1200 and j2.get("h") == 6000, (j2.get("w"), j2.get("h")))
        check("新输出子目录 = <根>/第二张",
              j2.get("out_dir", "").replace("\\", "/").endswith("/out/第二张"),
              j2.get("out_dir"))
        after_b = set(glob.glob(os.path.join(stage_parent, "slice_studio_*"))) - before
        check("旧预览块已被回收（只剩 1 个）", len(after_b) == 1, len(after_b))
        check("且是新的那个（旧的已删）", after_b != after_a, (after_a, after_b))

        st, html = get_page()
        check("页面已切到第二张", "原图 1200 × 6000 px" in html
              and "第二张.png" in html)

        print("\n=== 5. 错误路径 ===")
        st, j = upload(txt, token=tok, ctype="text/plain")
        check("非图片扩展名 → 明确报错",
              st == 200 and "只支持" in str(j.get("error", "")), j)
        st, raw = req("POST", "/api/upload", body=b"",
                      headers={"X-Token": tok, "X-Filename": "x.png",
                               "Content-Type": "image/png", "Content-Length": "0"})
        j = json.loads(raw)
        check("空请求体 → 明确报错", "没有收到文件内容" in str(j.get("error", "")), j)

        st, raw = req("GET", "/", headers={"Host": "evil.example.com"})
        check("伪造 Host（DNS rebinding）→ 403", st == 403, st)

        print("\n=== 6. 服务退出 ===")
        st, raw = req("POST", "/api/shutdown", body=b"{}",
                      headers={"Content-Type": "application/json", "X-Token": tok})
        check("/api/shutdown → ok", st == 200 and json.loads(raw).get("ok"), raw[:80])
        try:
            p.wait(timeout=12)
            check("进程已退出", True, p.returncode)
        except subprocess.TimeoutExpired:
            check("进程已退出", False, "超时未退出")
    finally:
        try:
            if p.poll() is None:
                p.kill()
        except Exception:
            pass
        # 收尾：清掉本次测试在临时目录留下的东西（预览块 + 暂存的原图）
        for d0 in glob.glob(os.path.join(stage_parent, "slice_studio_*")):
            shutil.rmtree(d0, ignore_errors=True)
        for f0 in ("测试长图.png", "第二张.png"):
            try:
                os.remove(os.path.join(upload_parent, "uploads", f0))
            except OSError:
                pass
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + "=" * 52)
    print("通过 %d 项，失败 %d 项" % (len(PASS), len(FAIL)))
    if FAIL:
        print("失败项：")
        for f in FAIL:
            print("  - " + f)
        print("=" * 52)
        return 1
    print("全部通过。")
    print("=" * 52)
    return 0


if __name__ == "__main__":
    sys.exit(main())
