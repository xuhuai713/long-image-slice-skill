#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""切片工作台 —— 一条命令启动，浏览器里标点，一次点击出片。

为什么是这个形态（第一性原理）：
    浏览器出于安全设计无法调用本地进程，也无法把大图交给 canvas 处理
    （GPU 纹理上限 16384px = 2^14，33744px 必然失败）。反过来，本地 Python
    处理 33744px 位图毫无压力（展开仅 72MB）。
    所以正确的分工是：**Python 同时充当服务器与计算引擎，浏览器只负责渲染
    与输入**。这样「标点」与「出片」之间不再需要人做搬运 —— 人只保留唯一
    不可替代的判断：切在哪里。

    由此消除的环节：复制命令 → 切终端 → 跑 dry-run → 跑正式切 → 跑验证
    → 跑印相图 → 手动打开目录。全部合并为一次点击。

启动：
    python slice_studio.py "C:\\Users\\user\\Desktop\\图片\\G2783S.jpg"
    python slice_studio.py 长图.jpg --max-size 1 --port 8765
"""

import argparse
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from PIL import Image

import long_image_slice as core
from make_contact_sheet import build_sheet

Image.MAX_IMAGE_PIXELS = None

# 支持的图片扩展名与上传上限。长图可能很大（几万像素高的 PNG 常有几十 MB），
# 所以上限给得宽松；内容按 1MB 分块**流式**写盘，绝不在内存里堆整份文件。
IMAGE_EXT_RE = re.compile(r"\.(jpe?g|png|webp|bmp|tiff?|gif)$", re.I)
MAX_UPLOAD = 512 * 1024 * 1024


def upload_stage_dir():
    """上传原图的暂存目录。

    放系统临时目录、不放桌面：原图在用户自己磁盘上本来就有，这份副本只为
    本次会话服务，切完即无用，落到桌面只会多一堆垃圾。复核图按既有约定落
    「源图旁边」，因此也会落在这里——输出目录（桌面\\切图\\<图名>\\）里必须
    只有切片，混进任何 PNG 都会被「全选上传」一起传上去。
    """
    d = os.path.join(tempfile.gettempdir(), "slice_studio", "uploads")
    os.makedirs(d, exist_ok=True)
    return d

# 空白分隔带检测与切点分级统一由 long_image_slice 提供（单一来源），
# 避免同一算法在工作台与命令行两处各写一份、随后各自走偏。


# ---------------------------------------------------------------- 预览素材

def build_preview(src, out_dir, tile_h=5000, quality=82, nav_w=60, nav_h=380):
    """生成预览块与导航缩略图。返回 (块 HTML 列表, 块数)。"""
    tiles_dir = os.path.join(out_dir, "tiles")
    if os.path.isdir(tiles_dir):
        shutil.rmtree(tiles_dir)
    os.makedirs(tiles_dir)

    with Image.open(src) as im:
        w, h = im.size
        view = im.convert("RGB")
        pad = max(2, len(str((h + tile_h - 1) // tile_h)))
        tags, n, y = [], 0, 0
        while y < h:
            y2 = min(h, y + tile_h)
            name = f"tile_{n + 1:0{pad}d}.jpg"
            view.crop((0, y, w, y2)).save(
                os.path.join(tiles_dir, name), "JPEG",
                quality=quality, optimize=True)
            tags.append(
                f'<img src="tiles/{name}" alt="第 {n + 1} 块" '
                f'style="top:{y / h * 100:.6f}%;height:{(y2 - y) / h * 100:.6f}%">')
            n += 1
            y = y2
        view.resize((nav_w, nav_h), Image.LANCZOS).save(
            os.path.join(out_dir, "nav.jpg"), "JPEG", quality=88)
    return tags, n


# ---------------------------------------------------------------- 页面

PAGE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>切片工作台 · __TITLE__</title>
<style>
:root{--bg:#fff;--pn:#f7f8fa;--bd:#e3e6ea;--fg:#1f2328;--mu:#6b7280;
      --ac:#e5484d;--pri:#2563eb;--ok:#16a34a;--wn:#d97706}
*{box-sizing:border-box}
html,body{margin:0;height:100%}
body{font:13px/1.6 system-ui,"Microsoft YaHei",sans-serif;color:var(--fg);background:var(--bg)}
.app{display:flex;height:100%}
.pane{flex:1;overflow:auto;padding:16px;background:#eceef1;min-width:0}
.stage{position:relative;margin:0 auto;cursor:crosshair;background:#fff;
       box-shadow:0 1px 3px rgba(0,0,0,.12)}
.stage img{position:absolute;left:0;width:100%;display:block}
.ov{position:absolute;inset:0;pointer-events:none}
.ln{position:absolute;left:0;right:0;border-top:2px solid var(--ac)}
.ln.sel{border-top-color:var(--pri);border-top-width:3px}
.ln span{position:absolute;right:2px;top:-10px;background:var(--ac);color:#fff;
         font:11px/1.5 ui-monospace,monospace;padding:1px 5px;border-radius:3px}
.ln.sel span{background:var(--pri)}
.side{width:392px;flex:0 0 392px;border-left:1px solid var(--bd);background:#fff;
      display:flex;flex-direction:column;min-height:0}
.side header,.side section,.side .foot{padding:12px 16px;border-bottom:1px solid var(--bd)}
.side h1{margin:0 0 2px;font-size:14px;font-weight:600}
.meta{color:var(--mu);font-size:12px}
.top{display:flex;gap:14px;align-items:flex-start}
.navbox{position:relative;width:60px;max-height:44vh;height:300px;flex:0 0 60px;
        border:1px solid var(--bd);border-radius:5px;cursor:pointer;overflow:hidden;background:#111}
.navbox img{width:100%;height:100%;display:block;object-fit:fill;opacity:.85}
.tick{position:absolute;left:0;right:0;height:0;border-top:1px solid rgba(229,72,77,.95)}
.vp{position:absolute;left:0;right:0;background:rgba(37,99,235,.22);
    border-top:1px solid var(--pri);border-bottom:1px solid var(--pri)}
.right{flex:1;min-width:0;display:flex;flex-direction:column;gap:8px}
.zoom{display:flex;gap:5px;flex-wrap:wrap}
button{border:1px solid var(--bd);background:#fff;border-radius:6px;cursor:pointer;
       padding:4px 9px;color:var(--mu);font:12px system-ui,sans-serif}
button:hover:not(:disabled){border-color:var(--mu);color:var(--fg)}
button.on{border-color:var(--pri);color:var(--pri);background:#eff4ff}
button:disabled{opacity:.5;cursor:default}
.tabs{display:flex;gap:5px;margin-bottom:6px}
.list{flex:1;overflow:auto;padding:8px 16px;min-height:110px}
.row{display:flex;align-items:center;gap:4px;padding:4px 0;border-bottom:1px dashed #eef0f2}
.row b{color:var(--mu);font-weight:400;min-width:26px;font-size:11px}
.row input{flex:1;min-width:0;padding:3px 5px;border:1px solid var(--bd);border-radius:5px;
           font:12px ui-monospace,monospace;color:var(--fg)}
.row button{padding:2px 5px;font:11px ui-monospace,monospace}
.row .del{color:var(--ac)}
/* 分段表必须限高并内部滚动：不限高时二三十条会把「一键切图」顶出屏幕 */
#segs{max-height:132px;overflow:auto;padding-right:2px}
.seg{display:flex;justify-content:space-between;font:12px ui-monospace,monospace;
     color:var(--mu);padding:1px 0}
.seg.big{color:var(--mu)}
.seg.warn{color:#e08a1e}
.res{font:12px ui-monospace,monospace}
.res table{width:100%;border-collapse:collapse}
.res th,.res td{padding:3px 4px;text-align:right;border-bottom:1px solid #f0f2f4}
.res th{color:var(--mu);font-weight:400;text-align:right}
.res td:first-child,.res th:first-child{text-align:left}
.res tr.c td:nth-child(3){color:var(--ok)}
.res tr.o td{color:var(--ac)}
.res tr.u td{color:#e08a1e}
.empty{color:var(--mu);font-size:12px;padding:8px 0}
#go{background:var(--pri);border-color:var(--pri);color:#fff;font-weight:600;
    padding:9px 0;width:100%;font-size:14px;border-radius:7px}
#go:hover:not(:disabled){background:#1d4ed8;color:#fff}
#go:disabled{background:#93b4f5;border-color:#93b4f5;color:#fff}
.bar{height:4px;border-radius:2px;background:#eef0f2;overflow:hidden;margin-top:8px;display:none}
.bar.on{display:block}
.bar i{display:block;height:100%;width:30%;background:var(--pri);
       animation:sl 1.1s ease-in-out infinite}
@keyframes sl{0%{margin-left:-30%}100%{margin-left:100%}}
.status{font-size:12px;color:var(--mu);margin-top:6px;min-height:18px}
.status.ok{color:var(--ok)}
.status.err{color:var(--ac)}
.fld{display:flex;align-items:center;gap:6px;font-size:12px;color:var(--mu)}
.fld input{width:64px;padding:3px 6px;border:1px solid var(--bd);border-radius:5px;
           font:12px ui-monospace,monospace}
.hint{color:var(--mu);font-size:11px;line-height:1.7}
.rowline{display:flex;gap:6px;align-items:center}
.rowline>button{flex:1}
/* 拖放换图时的全屏提示层。用 fixed 而不是 absolute：pane 是可滚动的长容器，
   absolute 会跟着滚走，用户拖到窗口下沿就看不到提示了。 */
.drop{position:fixed;inset:0;display:none;align-items:center;justify-content:center;
      background:rgba(37,99,235,.10);border:4px dashed var(--pri);z-index:20;
      font:600 17px system-ui,sans-serif;color:var(--pri);pointer-events:none}
.drop.on{display:flex}
</style>
</head>
<body>
<div class="app">
  <div class="pane" id="pane"><div class="stage" id="stage">
__IMGS__
<div class="ov" id="ov"></div>
  </div></div>

  <div class="side">
    <header>
      <h1>切片工作台</h1>
      <div class="meta">__TITLE__ · 原图 __W__ × __H__ px</div>
      <div class="rowline" style="margin-top:9px">
        <button id="pick">换一张图…</button>
      </div>
      <div class="hint" style="margin-top:5px">也可以把图片直接拖进左侧画面</div>
    </header>

    <section>
      <div class="top">
        <div class="navbox" id="navbox" title="全图导航：点击或拖动可跳转">
          <img src="nav.jpg" alt="全图导航">
          <div id="ticks"></div>
          <div class="vp" id="vp"></div>
        </div>
        <div class="right">
          <div class="zoom" id="zoom"></div>
          <div class="rowline">
            <button id="auto">自动识别切口</button>
            <button id="clear">清空</button>
          </div>
          <div class="rowline">
            <button id="snap" class="on">磁吸：开</button>
          </div>
        </div>
      </div>
    </section>

    <div class="list" id="list"></div>

    <div class="foot">
      <div id="segs"></div>
      <div class="seg big" id="segtotal"></div>
      <div style="display:flex;justify-content:space-between;align-items:center;margin:10px 0 8px">
        <div class="fld">单片上限
          <input id="maxs" type="number" value="__MAXSIZE__" min="0" step="0.1"> MB
        </div>
        <div class="fld" id="outdir" title="__OUTDIR__">输出 __OUTNAME__</div>
      </div>
      <button id="go">一键切图</button>
      <div class="bar" id="bar"><i></i></div>
      <div class="status" id="status">标出切割线后点击「一键切图」</div>
    </div>
  </div>
</div>

<script>
var SRC = __SRC__, PREFIX = __PREFIX__, TOKEN = __TOKEN__;
var W = __W__, H = __H__, DW = __DW__, BANDS = __BANDS__;
var cuts = [], sel = -1, snap = true, mode = 'cuts';

var $ = function(id){ return document.getElementById(id); };
var $$ = function(s){ return Array.prototype.slice.call(document.querySelectorAll(s)); };
var pane = $('pane'), stage = $('stage'), ov = $('ov'), list = $('list'),
    segs = $('segs'), statusEl = $('status');
/* 注意：不要把 status 用作全局变量名。window.status 是浏览器内置的字符串属性，
   赋值给它会被内置 setter 强制转成 "[object HTMLDivElement]"，
   随后的 status.textContent = ... 便静默失效。 */

/* cuts 始终维持升序：这样列表行号、导航刻度、切割线的索引三者一致。
   若允许乱序，行内微调按钮会改到错误的那一刀。 */
function sortCuts(){ cuts.sort(function(a, b){ return a - b; }); }
function edges(){ return [0].concat(cuts, [H]); }
function setStatus(t, cls){ statusEl.textContent = t; statusEl.className = 'status' + (cls ? ' ' + cls : ''); }

/* 磁吸：把点击位置吸附到最近的平坦分隔带中心 */
function snapY(y){
  if (!snap || !BANDS.length) return y;
  for (var i = 0; i < BANDS.length; i++){
    if (y >= BANDS[i][0] && y <= BANDS[i][1]) return (BANDS[i][0] + BANDS[i][1]) >> 1;
  }
  var best = -1, bd = 1e9;
  for (var i = 0; i < BANDS.length; i++){
    var c = (BANDS[i][0] + BANDS[i][1]) >> 1, d = Math.abs(c - y);
    if (d < bd){ bd = d; best = c; }
  }
  return bd <= __SNAP_PX__ ? best : y;
}

function renderSegs(){
  var e = edges(), rows = [], sum = 0;
  for (var i = 0; i < e.length - 1; i++){
    var h = e[i + 1] - e[i]; sum += h;
    var short = MIN_H && h < MIN_H;
    rows.push('<div class="seg' + (short ? ' warn' : '') + '"><span>片 ' + (i + 1) +
              '  Y ' + e[i] + '–' + e[i + 1] + '</span><span>' + h +
              (short ? ' ⚠' : '') + ' px</span></div>');
  }
  /* 明细放进限高可滚动的 #segs；合计单独一行常驻可见 */
  segs.innerHTML = cuts.length ? rows.join('') : '<div class="seg">—</div>';
  $('segtotal').innerHTML = '<span>共 ' + (cuts.length + 1) + ' 片</span><span>' + sum + ' px</span>';
}

/* 片高下限：只喂给「自动识别切口」当参数，并给过矮的碎片亮警告，
   不参与实际切割——切在哪里完全由人点的线决定。输入框已去掉，
   改用服务端注入的默认值（默认 1000，可用 --min-h 覆盖）。 */
var MIN_H = __MINH__;

function render(){
  ov.innerHTML = cuts.map(function(y, i){
    return '<div class="ln' + (i === sel ? ' sel' : '') +
           '" style="top:' + (y / H * 100) + '%"><span>' + y + '</span></div>';
  }).join('');

  if (mode === 'cuts'){
    list.innerHTML = cuts.length ? cuts.map(function(y, i){
      return '<div class="row"><b>#' + (i + 1) + '</b>' +
        '<input type="number" value="' + y + '" data-i="' + i + '" min="1" max="' + (H - 1) + '">' +
        '<button data-nudge="-10" data-i="' + i + '">-10</button>' +
        '<button data-nudge="-1" data-i="' + i + '">-1</button>' +
        '<button data-nudge="1" data-i="' + i + '">+1</button>' +
        '<button data-nudge="10" data-i="' + i + '">+10</button>' +
        '<button class="del" data-del="' + i + '">×</button></div>';
    }).join('') : '<div class="empty">尚未添加切割线。点击左侧画面，或点「自动识别切口」起步。</div>';
  }
  renderSegs();
  drawNav();
}

/* ---------- 全图导航：33744px 的长图必须有个「地图」，否则只能盲滚 ---------- */
var navbox = $('navbox'), vp = $('vp'), ticks = $('ticks');

function drawNav(){
  ticks.innerHTML = cuts.map(function(y){
    return '<div class="tick" style="top:' + (y / H * 100) + '%"></div>';
  }).join('');
  updateVP();
}
function updateVP(){
  var sr = stage.getBoundingClientRect(), pr = pane.getBoundingClientRect();
  if (sr.height <= 0) return;
  var top = Math.max(0, pr.top - sr.top), bot = Math.min(sr.height, pr.bottom - sr.top);
  vp.style.top = (top / sr.height * 100) + '%';
  vp.style.height = (Math.max(0, bot - top) / sr.height * 100) + '%';
}
function jumpTo(clientY){
  var r = navbox.getBoundingClientRect();
  var ratio = Math.min(1, Math.max(0, (clientY - r.top) / r.height));
  var sr = stage.getBoundingClientRect();
  pane.scrollTop = sr.top + pane.scrollTop + ratio * sr.height - pane.clientHeight / 2;
}
var dragging = false;
navbox.addEventListener('mousedown', function(e){ dragging = true; jumpTo(e.clientY); e.preventDefault(); });
window.addEventListener('mousemove', function(e){ if (dragging) jumpTo(e.clientY); });
window.addEventListener('mouseup', function(){ dragging = false; });
pane.addEventListener('scroll', updateVP);
window.addEventListener('resize', updateVP);

/* ---------- 落刀 ---------- */
stage.addEventListener('click', function(e){
  var r = stage.getBoundingClientRect();
  var y = Math.round((e.clientY - r.top) / r.height * H);
  if (y < 1 || y > H - 1) return;
  y = snapY(y);
  var hit = -1;
  for (var i = 0; i < cuts.length; i++) if (Math.abs(cuts[i] - y) <= 4){ hit = i; break; }
  if (hit >= 0) cuts.splice(hit, 1); else cuts.push(y);
  sel = -1; sortCuts();
  if (mode !== 'cuts') mode = 'cuts';
  render();
});

list.addEventListener('input', function(e){
  var i = e.target.getAttribute('data-i');
  if (i === null || !e.target.value) return;
  var v = parseInt(e.target.value, 10);
  if (!isNaN(v) && v > 0 && v < H){ cuts[+i] = v; sortCuts(); renderSegs(); drawNav(); }
});
list.addEventListener('click', function(e){
  var t = e.target;
  var d = t.getAttribute('data-del'), n = t.getAttribute('data-nudge');
  if (d !== null){ cuts.splice(+d, 1); sel = -1; render(); return; }
  if (n !== null){
    var i = +t.getAttribute('data-i');
    cuts[i] = Math.min(H - 1, Math.max(1, cuts[i] + parseInt(n, 10)));
    sortCuts(); render(); return;
  }
  if (t.tagName === 'INPUT'){ sel = +t.getAttribute('data-i'); render(); }
});

/* ---------- 自动识别切口 ---------- */
$('auto').onclick = function(){
  if (!BANDS.length) return setStatus('未检测到平坦分隔带，请手动标点', 'err');
  setStatus('正在最宽留白处择优铺刀…');
  post('/api/propose', {}).then(function(j){
    if (j.error) return setStatus('失败：' + j.error, 'err');
    cuts = j.cuts.slice().sort(function(a, b){ return a - b; });
    sel = -1; mode = 'cuts'; render();
    var hs = j.heights;
    setStatus('已铺 ' + cuts.length + ' 刀 · 共 ' + (cuts.length + 1) + ' 片 · 最矮 '
      + Math.min.apply(null, hs) + 'px · 最高 ' + Math.max.apply(null, hs)
      + 'px。自动识别只能覆盖留白边界，模块直接相接处需手动补刀。', '');
  });
};
$('snap').onclick = function(){
  snap = !snap;
  this.className = snap ? 'on' : '';
  this.textContent = '磁吸：' + (snap ? '开' : '关');
};
$('clear').onclick = function(){ cuts = []; sel = -1; mode = 'cuts'; render(); setStatus('已清空'); };

/* ---------- 缩放 ---------- */
function setWidth(px){
  stage.style.width = px + 'px';
  stage.style.height = (px * H / W) + 'px';
  render();
}
var ZOOMS = [[0.5, '50%'], [0.75, '75%'], [1, '100%']];
ZOOMS.forEach(function(z){
  var b = document.createElement('button');
  b.textContent = z[1];
  if (z[0] === __DZ__) b.className = 'on';
  b.onclick = function(){
    setWidth(Math.round(DW * z[0]));
    $$('.zoom button').forEach(function(x){ x.className = ''; });
    b.className = 'on';
  };
  $('zoom').appendChild(b);
});
var fit = document.createElement('button');
fit.textContent = '适合宽度';
fit.onclick = function(){
  setWidth(Math.max(120, pane.clientWidth - 40));
  $$('.zoom button').forEach(function(x){ x.className = ''; });
  fit.className = 'on';
};
$('zoom').appendChild(fit);

/* ---------- 一键切图 ---------- */
$('go').onclick = function(){
  if (!cuts.length) return setStatus('请先标出至少一条切割线', 'err');
  var btn = this;
  btn.disabled = true;
  $('bar').className = 'bar on';
  setStatus('正在切割并压缩…');
  var t0 = Date.now();
  fetch('/api/slice', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-Token': TOKEN },
    body: JSON.stringify({ cuts: cuts, maxSize: parseFloat($('maxs').value) })
  }).then(function(r){ return r.json(); }).then(function(j){
    $('bar').className = 'bar';
    btn.disabled = false;
    if (j.error) return setStatus('失败：' + j.error, 'err');
    showRes(j, Date.now() - t0);
  }).catch(function(err){
    $('bar').className = 'bar';
    btn.disabled = false;
    setStatus('失败：' + err, 'err');
  });
};

function showRes(j, ms){
  mode = 'res';
  var rows = j.slices.map(function(s){
    var cls = s.over ? 'o' : (s.under ? 'u' : (s.compressed ? '' : 'c'));
    return '<tr class="' + cls + '"><td>' + s.file + '</td><td>' + s.height + 'px</td>' +
      '<td>' + s.mb.toFixed(2) + 'MB</td><td>' + (s.quality || '-') + '</td></tr>';
  }).join('');
  list.innerHTML =
    '<div class="res"><table><thead><tr><th>文件</th><th>高</th><th>体积</th>' +
    '<th>质量</th></tr></thead><tbody>' + rows + '</tbody></table></div>' +
    '<div class="hint" style="margin-top:10px">输出目录：' + j.out_dir + '</div>';

  var parts = ['共 ' + j.count + ' 片', '合计 ' + j.total_mb.toFixed(2) + ' MB',
               '耗时 ' + (ms / 1000).toFixed(1) + 's'];
  if (j.compressed_n) parts.push(j.compressed_n + ' 片已压缩');
  if (j.stale.length) parts.push('清理旧片 ' + j.stale.length + ' 个');
  if (j.over_n) parts.push('⚠ ' + j.over_n + ' 片超体积上限');
  if (j.under_n) parts.push('⚠ ' + j.under_n + ' 片低于最小片高 ' + j.min_h + 'px');
  setStatus(parts.join(' · '), (j.over_n || j.under_n) ? 'err' : 'ok');

  statusEl.insertAdjacentHTML('beforeend',
    '<div class="rowline" style="margin-top:8px">' +
    '<button id="reveal">打开输出目录</button>' +
    (j.sheet ? '<button id="sheet">查看复核图</button>' : '') +
    '<button id="back">返回编辑</button></div>');
  $('reveal').onclick = function(){ post('/api/reveal', { path: j.out_dir }); };
  if (j.sheet) $('sheet').onclick = function(){ window.open('/sheet.png?t=' + TOKEN, '_blank'); };
  $('back').onclick = function(){ mode = 'cuts'; render(); };
}

function post(url, body){
  return fetch(url, { method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-Token': TOKEN },
    body: JSON.stringify(body || {}) }).then(function(r){ return r.json(); });
}

/* ---------- 换图：上传按钮 + 全屏拖放 ----------
   为什么必须走上传而不是「让服务器读本地路径」：浏览器出于安全不给页面本地真实
   路径，而 `input[type=file]` 拿到的是可读的 File 对象——所以把字节发给本地服务
   器是最短路径。注意整条链路**不经过 canvas**：浏览器只搬字节，解码全靠本地
   Pillow，因此 16384px 的 GPU 纹理上限在这条路上完全不构成约束。 */
var pick = $('pick'), fileInput = document.createElement('input');
fileInput.type = 'file'; fileInput.accept = 'image/*'; fileInput.style.display = 'none';
document.body.appendChild(fileInput);
pick.onclick = function(){ fileInput.value = ''; fileInput.click(); };
fileInput.onchange = function(){ askThenUpload(this.files && this.files[0]); };

var dropMask = document.createElement('div');
dropMask.className = 'drop'; dropMask.textContent = '松手即载入这张图';
document.body.appendChild(dropMask);

/* 拖放的 enter/leave 会在子元素间反复触发，用深度计数避免提示层闪烁 */
var dragDepth = 0;
function hasFiles(e){
  var t = e.dataTransfer && e.dataTransfer.types;
  return !!t && Array.prototype.indexOf.call(t, 'Files') >= 0;
}
window.addEventListener('dragenter', function(e){
  if (!hasFiles(e)) return;
  e.preventDefault(); dragDepth++; dropMask.className = 'drop on';
});
window.addEventListener('dragover', function(e){ if (hasFiles(e)) e.preventDefault(); });
window.addEventListener('dragleave', function(){
  if (--dragDepth <= 0){ dragDepth = 0; dropMask.className = 'drop'; }
});
window.addEventListener('drop', function(e){
  e.preventDefault(); dragDepth = 0; dropMask.className = 'drop';
  askThenUpload(e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0]);
});

/* 换图会丢掉已标好的切割线，先问一句——宁可多问，也别让人白标二三十刀 */
function askThenUpload(file){
  if (!file) return;
  if (cuts.length && !window.confirm(
      '换图会清空当前已标注的 ' + cuts.length + ' 条切割线，确定继续？')) return;
  uploadFile(file);
}

function uploadFile(file){
  if (!/^image\//.test(file.type || '') &&
      !/\.(jpe?g|png|webp|bmp|tiff?)$/i.test(file.name || '')){
    return setStatus('这不是可识别的图片文件：' + (file.name || '(无名)'), 'err');
  }
  $('bar').className = 'bar on';
  setStatus('正在上传 ' + file.name + ' …');
  var xhr = new XMLHttpRequest();          /* 用 XHR 而不是 fetch：需要上传进度 */
  xhr.open('POST', '/api/upload');
  xhr.setRequestHeader('X-Token', TOKEN);
  xhr.setRequestHeader('X-Filename', encodeURIComponent(file.name));
  xhr.upload.onprogress = function(ev){
    if (ev.lengthComputable)
      setStatus('上传中 ' + Math.round(ev.loaded / ev.total * 100) + '% …');
  };
  xhr.onload = function(){
    var j; try { j = JSON.parse(xhr.responseText); } catch (err) { j = { error: '返回格式异常' }; }
    if (j.error){ $('bar').className = 'bar'; return setStatus('换图失败：' + j.error, 'err'); }
    setStatus('已载入 ' + j.name + ' · ' + j.w + ' × ' + j.h + ' px · '
      + j.bands + ' 条分隔带，正在刷新…', 'ok');
    setTimeout(function(){ location.reload(); }, 400);
  };
  xhr.onerror = function(){ $('bar').className = 'bar'; setStatus('上传失败（网络错误）', 'err'); };
  xhr.send(file);
}

/* ---------- 启动 ---------- */
setWidth(Math.round(DW * __DZ__));   /* 默认 50%：整图看得见，落刀更准 */
render();
setTimeout(updateVP, 60);
</script>
</body>
</html>
"""


# 起始页：还没选图时显示。单独一份而不是复用 PAGE 的空状态——PAGE 里到处是
# 按 H 算的百分比（落刀坐标、预览块堆叠），H=0 会得到一堆 NaN。与其在模板里
# 到处塞 if，不如给「还没图」这个状态一张自己的、极简的页面。
EMPTY_PAGE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>切片工作台 · 选择长图</title>
<style>
:root{--bd:#e3e6ea;--fg:#1f2328;--mu:#6b7280;--pri:#2563eb;--ac:#e5484d}
*{box-sizing:border-box}
html,body{margin:0;height:100%}
body{font:14px/1.7 system-ui,"Microsoft YaHei",sans-serif;color:var(--fg);
     background:#eceef1;display:flex;align-items:center;justify-content:center;padding:24px}
.card{background:#fff;border:1px solid var(--bd);border-radius:12px;padding:32px 36px;
      max-width:600px;width:100%;box-shadow:0 2px 12px rgba(0,0,0,.07)}
h1{margin:0 0 4px;font-size:19px}
p.sub{margin:0 0 20px;color:var(--mu);font-size:13px}
#drop{border:2px dashed var(--bd);border-radius:10px;padding:34px 20px;cursor:pointer;
      text-align:center;background:#fafbfc;transition:border-color .15s,background .15s}
#drop:hover{border-color:var(--mu)}
#drop.on{border-color:var(--pri);background:#eff4ff}
#drop b{display:block;font-size:15px;color:var(--pri);margin-bottom:5px}
#drop span{color:var(--mu);font-size:12px}
#msg{margin-top:14px;min-height:20px;font-size:13px;color:var(--mu)}
#msg.err{color:var(--ac)}
#msg.ok{color:#16a34a}
.note{margin-top:20px;padding-top:14px;border-top:1px solid var(--bd);
      color:var(--mu);font-size:12px}
.note b{color:var(--fg);font-weight:600}
</style>
</head>
<body>
<div class="card">
  <h1>切片工作台</h1>
  <p class="sub">选一张长图，在浏览器里标切割线，一键出片（自动压到单片 ≤1MB）。</p>
  <div id="drop"><b>点击选择图片</b><span>或把图片拖到这里</span></div>
  <div id="msg"></div>
  <div class="note">
    支持 <b>JPG / PNG / WEBP / BMP / TIFF</b>。几万像素高的超长图也没问题——
    图片只在本地处理，不经过任何外部服务。<br>
    切片输出到 <b>桌面\切图\&lt;图名&gt;\</b>，每张图一个子目录，互不污染。
  </div>
</div>
<input type="file" id="f" accept="image/*" style="display:none">
<script>
var TOKEN = __TOKEN__;
var drop = document.getElementById('drop'), f = document.getElementById('f'),
    msg = document.getElementById('msg');
function say(t, cls){ msg.textContent = t; msg.className = cls || ''; }
drop.onclick = function(){ f.value = ''; f.click(); };
f.onchange = function(){ upload(this.files && this.files[0]); };
['dragenter', 'dragover'].forEach(function(ev){
  drop.addEventListener(ev, function(e){ e.preventDefault(); drop.className = 'on'; });
});
drop.addEventListener('dragleave', function(){ drop.className = ''; });
drop.addEventListener('drop', function(e){
  e.preventDefault(); drop.className = '';
  upload(e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0]);
});
function upload(file){
  if (!file) return;
  drop.className = '';
  say('正在上传 ' + file.name + ' …');
  var xhr = new XMLHttpRequest();
  xhr.open('POST', '/api/upload');
  xhr.setRequestHeader('X-Token', TOKEN);
  xhr.setRequestHeader('X-Filename', encodeURIComponent(file.name));
  xhr.upload.onprogress = function(ev){
    if (ev.lengthComputable) say('上传中 ' + Math.round(ev.loaded / ev.total * 100) + '% …');
  };
  xhr.onload = function(){
    var j; try { j = JSON.parse(xhr.responseText); } catch (err) { j = { error: '返回格式异常' }; }
    if (j.error) return say('失败：' + j.error, 'err');
    say('已载入 ' + j.name + ' · ' + j.w + ' × ' + j.h + ' px · '
        + j.bands + ' 条分隔带，正在打开…', 'ok');
    setTimeout(function(){ location.reload(); }, 400);
  };
  xhr.onerror = function(){ say('上传失败（网络错误）', 'err'); };
  xhr.send(file);
}
</script>
</body>
</html>
"""


# ---------------------------------------------------------------- 服务

class Handler(BaseHTTPRequestHandler):
    server_version = "SliceStudio/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass                                    # 静音访问日志，避免刷屏

    # -- 基础工具 --
    def _send(self, code, body, ctype="text/plain; charset=utf-8"):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code, obj):
        self._send(code, json.dumps(obj, ensure_ascii=False), "application/json; charset=utf-8")

    def _same_origin(self):
        """只接受本机 Host，阻断 DNS rebinding。"""
        host = (self.headers.get("Host") or "").split(":")[0]
        return host in ("127.0.0.1", "localhost", "[::1]")

    def _authed(self):
        return self.headers.get("X-Token") == self.server.token

    # -- GET --
    def do_GET(self):
        if not self._same_origin():
            return self._send(403, "forbidden")
        path = self.path.split("?")[0]
        cfg = self.server.cfg
        if path == "/":
            return self._send(200, cfg["page"], "text/html; charset=utf-8")
        if path == "/nav.jpg":
            return self._file(os.path.join(cfg["preview_dir"], "nav.jpg"), "image/jpeg")
        if path == "/sheet.png":
            t = self.path.split("t=")[-1]
            if t != self.server.token or not cfg.get("sheet"):
                return self._send(403, "forbidden")
            return self._file(cfg["sheet"], "image/png")
        if path.startswith("/tiles/"):
            name = os.path.basename(path)
            return self._file(os.path.join(cfg["preview_dir"], "tiles", name), "image/jpeg")
        return self._send(404, "not found")

    def _file(self, p, ctype):
        if not os.path.isfile(p):
            return self._send(404, "not found")
        with open(p, "rb") as f:
            data = f.read()
        self._send(200, data, ctype)

    # -- POST --
    def do_POST(self):
        if not self._same_origin() or not self._authed():
            return self._json(403, {"error": "forbidden"})
        path = self.path.split("?")[0]

        # 上传体是二进制，必须在 JSON 解析**之前**分流 —— 否则一张 16MB 的
        # 图片会先被塞进 json.loads，既白耗时间又必然抛异常。
        if path == "/api/upload":
            return self._api_upload()

        n = int(self.headers.get("Content-Length") or 0)
        try:
            payload = json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            payload = {}

        cfg = self.server.cfg

        if path == "/api/slice":
            return self._api_slice(payload)
        if path == "/api/propose":
            return self._api_propose(payload)
        if path == "/api/reveal":
            target = payload.get("path") or cfg["out_dir"]
            self._reveal(target)
            return self._json(200, {"ok": True})
        if path == "/api/shutdown":
            self._json(200, {"ok": True})
            threading.Thread(target=self.server.shutdown, daemon=True).start()
            return
        return self._json(404, {"error": "unknown endpoint"})

    # -- 上传换图 --
    def _drain_body_to(self, path, n):
        """把请求体按 1MB 分块**流式**写入磁盘，返回实际写入字节数。

        不 `read(n)` 一次性读进内存：长图动辄几十 MB，全堆在内存里既无必要，
        也会逼着把上限定得很保守。
        """
        written = 0
        with open(path, "wb") as f:
            while written < n:
                chunk = self.rfile.read(min(1 << 20, n - written))
                if not chunk:
                    break
                f.write(chunk)
                written += len(chunk)
        return written

    def _api_upload(self):
        """接收浏览器上传的长图，落盘后**重建整个会话**。

        重建 = 重新读尺寸 → 重扫分隔带 → 重新生成预览块 → 换掉 cfg。
        页面刷新后面前就是一张全新的图，全程不需要重启服务。
        之所以能这么便宜，是因为所有与图相关的状态都只存在于 cfg 里，
        没有散落在别处的全局变量。
        """
        raw = self.headers.get("X-Filename") or ""
        try:
            name = urllib.parse.unquote(raw)
        except Exception:
            name = raw
        name = os.path.basename(name.replace("\\", "/")).strip()
        if not name:
            return self._json(200, {"error": "缺少文件名"})
        if not IMAGE_EXT_RE.search(name):
            return self._json(200, {"error": "只支持 JPG / PNG / WEBP / BMP / TIFF"})

        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0:
            return self._json(200, {"error": "没有收到文件内容"})
        if n > MAX_UPLOAD:
            return self._json(200, {"error": "文件过大（%.0f MB），上限 %d MB"
                                    % (n / core.MB, MAX_UPLOAD // core.MB)})

        dst = os.path.join(upload_stage_dir(), name)
        try:
            got = self._drain_body_to(dst, n)
        except Exception as e:
            return self._json(200, {"error": "写入失败：%s" % e})
        if got != n:
            try:
                os.remove(dst)
            except OSError:
                pass
            return self._json(200, {"error": "上传中断（收到 %d / %d 字节）" % (got, n)})

        try:
            with self.server.lock:
                old = self.server.cfg
                cfg = build_session(dst, self.server.out_root,
                                    token=self.server.token,
                                    log=lambda *a: None, **self.server.opts)
                self.server.cfg = cfg
        except Exception as e:
            return self._json(200, {"error": "这张图打不开：%s" % e})

        # 上一张图的预览块已无人引用，立刻回收，免得临时目录越堆越大
        try:
            if old.get("preview_dir"):
                shutil.rmtree(old["preview_dir"], ignore_errors=True)
        except Exception:
            pass

        return self._json(200, {"ok": True, "name": name, "w": cfg["w"],
                                "h": cfg["h"], "bands": len(cfg["bands"]),
                                "out_dir": cfg["out_dir"]})

    def _api_propose(self, payload):
        """按「留白宽度」择优，并受片高上下限约束，提出一组切割线。

        以留白宽度当分数而不是按数量取前 N 条：实测本图大标题上方留白
        128–143px、小标题上方仅 7–27px，所以「优先最宽留白」等于「优先切在
        大标题之前」，天然让模块按层级断开。数量则交给片高范围间接控制。
        """
        cfg = self.server.cfg
        if not cfg.get("src"):
            return self._json(200, {"error": "还没有选择图片，请先点击「换一张图…」"})
        try:
            min_h = int(payload.get("minH") or cfg.get("min_h") or 1000)
            max_h = int(payload.get("maxH") or cfg.get("max_h") or 0)
            cuts = core.propose_cuts(cfg["bands"], cfg["h"],
                                     min_h=max(0, min_h), max_h=max_h or None)
            edges = [0] + cuts + [cfg["h"]]
            heights = [edges[i + 1] - edges[i] for i in range(len(edges) - 1)]
            self._json(200, {
                "cuts": cuts, "heights": heights,
                "min_h": min_h, "max_h": max_h,
            })
        except Exception as e:
            self._json(200, {"error": "%s: %s" % (type(e).__name__, e)})

    def _api_slice(self, payload):
        if not self.server.cfg.get("src"):
            return self._json(200, {"error": "还没有选择图片，请先点击「换一张图…」"})
        cuts = payload.get("cuts") or []
        max_size = payload.get("maxSize")
        if max_size is None:
            max_size = cfg_default_max(self.server.cfg)
        try:
            t0 = time.time()
            res = core.slice_image(
                self.server.cfg["src"], cuts=cuts,
                out_dir=self.server.cfg["out_dir"],
                name=self.server.cfg["stem"], fmt="jpg",
                quality=100, max_size_mb=max_size, quality_floor=70,
                progressive=True, clean=True,
            )
            limit = res["limit"]
            mn = int(self.server.cfg.get("min_h") or 0)
            for s in res["slices"]:
                s["over"] = bool(limit and s["bytes"] > limit)
                s["under"] = bool(mn and s["height"] < mn)
            # 复核图放在源图旁边，不进输出目录 —— 输出目录必须只有切片，
            # 否则混进去的 PNG/子目录会被一起上传。
            sheet_path = None
            try:
                sheet_out = os.path.join(
                    os.path.dirname(self.server.cfg["src"]),
                    self.server.cfg["stem"] + "_切片复核图.png")
                sheet_path, _, _, _ = build_sheet(
                    res["out_dir"], out=sheet_out, cols=6, cell_w=220)
                self.server.cfg["sheet"] = sheet_path
            except Exception:
                pass
            self._json(200, {
                "out_dir": res["out_dir"],
                "count": res["count"],
                "total_mb": sum(s["bytes"] for s in res["slices"]) / core.MB,
                "compressed_n": sum(1 for s in res["slices"] if s["compressed"]),
                "over_n": sum(1 for s in res["slices"] if s["over"]),
                "under_n": sum(1 for s in res["slices"] if s["under"]),
                "min_h": mn,
                "stale": res["stale"],
                "slices": res["slices"],
                "sheet": bool(sheet_path),
                "sheet_path": sheet_path,
                "elapsed": round(time.time() - t0, 2),
            })
        except SystemExit as e:
            self._json(200, {"error": str(e)})
        except Exception as e:
            self._json(200, {"error": "%s: %s" % (type(e).__name__, e)})

    @staticmethod
    def _reveal(path):
        try:
            if sys.platform.startswith("win"):
                os.startfile(path)                      # noqa: S606
            elif sys.platform == "darwin":
                subprocess.Popen(["open", path])
            else:
                subprocess.Popen(["xdg-open", path])
        except Exception:
            pass


def cfg_default_max(cfg):
    return cfg["max_size"]


def build_session(src, out_root, token=None, *, tile_h=5000, preview_q=82,
                  snap_px=40, max_size=1.0, min_h=1000, max_h=0,
                  display_width=None, log=print):
    """读图 → 重扫分隔带 → 重新生成预览块 → 产出一份完整的 cfg。

    抽成独立函数是为了「换图不必重启服务」：上传新图后再调一次就行。
    前提是**所有与图相关的状态都只存在于 cfg 里**——一旦有哪个变量漏到外面
    成了全局量，换图时它就会残留，变成一类极难排查的错。
    """
    src = os.path.abspath(src)
    if not os.path.isfile(src):
        raise FileNotFoundError("找不到文件：%s" % src)

    with Image.open(src) as im:
        w, h = im.size

    stem = os.path.splitext(os.path.basename(src))[0]

    # 每张图一个子目录：多张图的切片不会互相污染，而「打开目录 → 全选 → 上传」
    # 的动作完全不变。早先全部平铺在同一目录时，切第二张图会把第一张的切片
    # 一起选上——clean_stale 只清同名前缀，救不了这种串台。
    out_dir = os.path.join(out_root, stem)
    os.makedirs(out_dir, exist_ok=True)

    if log:
        log("源图：%s" % src)
        log("尺寸：%d × %d px" % (w, h))
        log("输出：%s" % out_dir)

    t0 = time.time()
    bands = core.detect_bands(src) if snap_px else []
    if log:
        log("  找到 %d 条平坦分隔带（用于磁吸与自动识别）（%.1fs）"
            % (len(bands), time.time() - t0))

    preview_dir = tempfile.mkdtemp(prefix="slice_studio_")
    t0 = time.time()
    tags, n = build_preview(src, preview_dir, tile_h=tile_h, quality=preview_q)
    if log:
        log("  %d 块预览（每块 ≤ %dpx，绕开浏览器 canvas 16384px 上限）（%.1fs）"
            % (n, tile_h, time.time() - t0))

    cfg = {
        "src": src, "stem": stem, "out_root": out_root, "out_dir": out_dir,
        "w": w, "h": h, "bands": bands, "tags": tags, "preview_dir": preview_dir,
        "display_width": display_width or w,
        "max_size": max_size, "snap_px": snap_px, "min_h": min_h, "max_h": max_h,
        "token": token or secrets.token_hex(12), "sheet": None,
        "opts": dict(tile_h=tile_h, preview_q=preview_q, snap_px=snap_px,
                     max_size=max_size, min_h=min_h, max_h=max_h,
                     display_width=display_width),
    }
    cfg["page"] = build_page(cfg)
    return cfg


def empty_cfg(out_root, token, opts):
    """尚未选图时的会话：只有「选择长图」起始页，不含任何图相关状态。"""
    cfg = {
        "src": None, "stem": None, "out_root": out_root, "out_dir": out_root,
        "w": 0, "h": 0, "bands": [], "tags": [], "preview_dir": None,
        "display_width": 0, "sheet": None, "token": token, "opts": dict(opts),
    }
    for k in ("max_size", "snap_px", "min_h", "max_h"):
        cfg[k] = opts.get(k, cfg.get(k))
    cfg["page"] = EMPTY_PAGE.replace("__TOKEN__", json.dumps(token))
    return cfg


def build_page(cfg):
    tags = cfg["tags"]
    out_label = "%s/%s/" % (os.path.basename(cfg["out_root"]), cfg["stem"])
    return (PAGE
            .replace("__TITLE__", os.path.basename(cfg["src"]))
            .replace("__IMGS__", "\n".join(tags))
            .replace("__SRC__", json.dumps(cfg["src"], ensure_ascii=False))
            .replace("__PREFIX__", json.dumps(cfg["stem"], ensure_ascii=False))
            .replace("__TOKEN__", json.dumps(cfg["token"]))
            .replace("__W__", str(cfg["w"]))
            .replace("__H__", str(cfg["h"]))
            .replace("__DW__", str(cfg["display_width"]))
            .replace("__DZ__", "0.5")
            .replace("__SNAP_PX__", str(cfg["snap_px"]))
            .replace("__BANDS__", json.dumps(cfg["bands"]))
            .replace("__MAXSIZE__", str(cfg["max_size"] or 0))
            .replace("__OUTDIR__", cfg["out_dir"])
            .replace("__OUTNAME__", out_label)
            .replace("__MINH__", str(cfg["min_h"]))
            .replace("__MAXH__", str(cfg["max_h"])))


DEFAULT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "slice_studio.default")


def read_default_config():
    """无参数启动时，从同目录的 slice_studio.default（UTF-8）读取配置。

    为什么绕这一下：.bat 启动器必须是纯 ASCII，否则 cmd 按 ANSI 代码页解析
    批处理文件会把中文路径读成乱码。把中文路径放进独立的 UTF-8 文件由 Python
    读取，就彻底规避了这个问题。

    支持两种行：
        IMAGE=<源图完整路径>
        OUT=<切片输出目录>
    为了兼容早期只写了一行图片路径的文件，不含 "=" 的行一律当作 IMAGE。
    """
    cfg = {}
    if not os.path.isfile(DEFAULT_FILE):
        return cfg
    with open(DEFAULT_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                k, v = line.split("=", 1)
                cfg[k.strip().upper()] = v.strip()
            elif "IMAGE" not in cfg:
                cfg["IMAGE"] = line
    return cfg


def main():
    ap = argparse.ArgumentParser(
        description="切片工作台：启动本地服务，在浏览器里选图、标点、一键出片")
    ap.add_argument("image", nargs="?", default=None,
                    help="源长图路径（可选）。省略时读 slice_studio.default；"
                         "两者都没有就进「选择长图」起始页，在浏览器里上传")
    ap.add_argument("--out", default=None,
                    help="切片输出**根目录**，默认 <桌面>/切图（可用配置 OUT= 覆盖）。"
                         "每张图在其下各建一个同名子目录，互不污染")
    ap.add_argument("--max-size", type=float, default=1.0, metavar="MB",
                    help="单片体积上限 MB，默认 1；填 0 不限制")
    ap.add_argument("--display-width", type=int, default=None,
                    help="100%% 缩放时的显示宽度，默认取源图宽度；初始缩放为 50%%")
    ap.add_argument("--tile-height", type=int, default=5000, help="预览块高度上限")
    ap.add_argument("--preview-quality", type=int, default=82, help="预览图质量")
    ap.add_argument("--snap", type=int, default=40, metavar="PX",
                    help="磁吸吸附半径（像素），默认 40；填 0 关闭磁吸")
    ap.add_argument("--min-h", type=int, default=1000, metavar="PX",
                    help="单片最小高度，默认 1000；自动识别据此避免切出碎片")
    ap.add_argument("--max-h", type=int, default=0, metavar="PX",
                    help="单片最大高度，默认 0（不限）；超过则自动细分")
    ap.add_argument("--port", type=int, default=8765, help="端口，默认 8765；占用时自动换")
    ap.add_argument("--no-open", action="store_true", help="不自动打开浏览器")
    args = ap.parse_args()

    conf = read_default_config()

    # 输出位置优先级：命令行 --out > 配置 OUT= > 固定默认 <桌面>/切图
    out_root = os.path.abspath(args.out or conf.get("OUT") or core.default_out_dir())
    os.makedirs(out_root, exist_ok=True)          # 「切图」文件夹不存在则新建

    opts = dict(tile_h=args.tile_height, preview_q=args.preview_quality,
                snap_px=args.snap, max_size=args.max_size,
                min_h=args.min_h, max_h=args.max_h,
                display_width=args.display_width)
    token = secrets.token_hex(12)

    # 图片是**可选**的：没有就先进起始页，让用户在浏览器里选。
    # 这一条是从「只能切这一张」变成「通用长图切图工具」的关键——
    # 启动不再被某一张图绑架。
    cfg = None
    image = args.image or conf.get("IMAGE")
    if image:
        src = os.path.abspath(image)
        if os.path.isfile(src):
            cfg = build_session(src, out_root, token=token, **opts)
        else:
            print("[提示] 配置里的图片不存在，改为在浏览器里手动选择：")
            print("  %s" % src)
            print("  （图片被移动或改名后，请更新 %s 里的 IMAGE=）" % DEFAULT_FILE)

    if cfg is None:
        cfg = empty_cfg(out_root, token, opts)
        print("尚未选图：工作台打开后点「换一张图…」，或把图片拖进页面。")

    print("输出根目录：%s（每张图在其下建同名子目录）" % out_root)

    httpd = None
    for port in range(args.port, args.port + 20):
        try:
            httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
            break
        except OSError:
            continue
    if httpd is None:
        raise SystemExit(f"错误：{args.port}–{args.port + 19} 端口均被占用。")

    httpd.cfg = cfg
    httpd.token = token             # 换图后沿用同一 token，页面无需重新授权
    httpd.out_root = out_root       # 上传换图时据此重算子目录
    httpd.opts = opts               # 换图时沿用同一套参数
    httpd.lock = threading.Lock()   # 两个上传同时到达时不至于互相踩
    url = f"http://127.0.0.1:{httpd.server_address[1]}/"

    print()
    print("=" * 58)
    print(f"  切片工作台已启动：{url}")
    print(f"  切片输出到：{out_root}\\<图名>\\")
    print("  标好切割线后点「一键切图」，再点「打开输出目录」。")
    print("  关闭本窗口即结束服务。")
    print("=" * 58)
    print()

    if not args.no_open:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
