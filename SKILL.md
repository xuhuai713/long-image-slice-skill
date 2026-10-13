---
name: 切图工作台
title: 切图工作台
description: 长图切片工作台（可复用于任意长图）。用户说出「切图」「切片」「切长图」「详情页切图」「打开切图工作台」「切片工作台」「换一张图」「上传图片切图」等词时，立即加载本技能并在本地启动切片工作台服务：在浏览器里上传或拖入任意长图、标切口、一键出片（自动把单片压到 1MB 以内、生成复核图、打开输出目录）。凡是需要把超长详情页 / 长截图 / PS 导出的长图（例如 750×33744px）按模块边界不等高切成多张 JPG 的场景，一律用本技能；不要改用在线切图网站或浏览器 canvas 方案——GPU 纹理上限 16384px（2^14）决定了那条路物理上不通。
agent_created: true
---

# 切图工作台 · Slice Studio

把超长图按设计稿模块边界切成多张 ≤1MB 的 JPG。**运算在本地 Python，浏览器只负责显示与标点。**

## 触发

用户说「**切图**」「切片」「切长图」「详情页切图」「打开切片工作台」时——**立即启动服务**，不要先追问一串参数。

## 一条命令唤醒（默认动作）

- 技能目录：`C:\Users\user\.workbuddy\skills\切图工作台\`
- 解释器：`C:\Users\user\.workbuddy\binaries\python\envs\default\Scripts\python.exe`（Python 3.13 + Pillow 12.2.0，**无 tkinter**，不要用文件选择框）

```bash
cd "C:\Users\user\.workbuddy\skills\切图工作台"
"C:\Users\user\.workbuddy\binaries\python\envs\default\Scripts\python.exe" slice_studio.py
```

| 情形 | 命令 |
|---|---|
| 直接开工（用配置里的图） | 不带参数，读同目录 `slice_studio.default` 的 `IMAGE=` |
| 用户给了图路径 | `python slice_studio.py "D:\新图.jpg"` |
| 用户给了图 + 指定输出根目录 | `python slice_studio.py "D:\新图.jpg" --out "D:\出片"` |
| **什么都不指定** | 直接跑，进「选择长图」起始页，让用户在浏览器里选 |
| 不想自动弹系统浏览器 | 追加 `--no-open` |

**图片是可选的。** 没有配置图、或配置里的图已经不在，服务照样启动，只是先进起始页等用户选图。所以「用户还没说要切哪张图」时不要卡住，先把工作台打开。

**关键：它是常驻服务器，必须后台运行，不要等它返回。** 启动后从 stdout 读实际地址：

```
  切片工作台已启动：http://127.0.0.1:8765/
```

端口默认 8765，被占用时自动向后顺延（最多 20 个），**所以地址要以输出为准**。拿到 URL 后用结果展示能力把它打开给用户，并说一句：「选图（或换图）→ 标切割线 → 点『一键切图』 → 点『打开输出目录』」。

### 用户自助启动（等价路径）

技能根目录下的 `切片工作台.bat` 双击效果完全相同，还可以把图片直接拖到它上面。

## 换图：这是「通用工具」而不是「切某一张图」

**在浏览器里换图，不需要重启服务。** 三个入口，效果一样：

1. 侧栏顶部「**换一张图…**」按钮 → 文件选择框
2. 把图片**拖进页面任意位置**（全屏虚线提示层会亮起）
3. 起始页的「点击选择图片 / 拖到这里」

换图后服务端会**重建整个会话**：重读尺寸 → 重扫分隔带 → 重新生成预览块 → 换掉 cfg，页面自动刷新。已标好的切割线会清空，所以换图前会弹一次确认。

**为什么这条路必须走「上传」**：浏览器出于安全不给页面本地真实路径，而 `input[type=file]` 拿到的 File 对象是可读的——把字节发给本地服务器是最短路径。
**关键：整条链路不经过 canvas**，浏览器只搬字节，解码全在本地 Pillow，所以 16384px 的 GPU 纹理上限在这条路上根本不构成约束。上传体按 1MB 分块流式落盘，不在内存里堆整份文件。

上传的原图暂存到 `%TEMP%\slice_studio\uploads\`（原图在用户自己磁盘上本来就有，这份副本只为本次会话服务，放桌面只会多一堆垃圾）。

## 界面怎么用

1. 画面**单击落一刀**，再点同一条线删除。所有切割线同色——线只表示「切在这里」，不对结果做任何区分。
2. 侧栏可改坐标，或点「自动识别切口」在**最宽留白处**起手铺刀，再手动微调。
3. 点「**一键切图**」→ 切割 + 压到 ≤1MB + 生成复核图 + 打开输出目录，全自动。
4. 点「查看复核图」用接触印相图人眼复核切口（像素指标证明不了「没切穿文字」，只能靠看）。
5. 预览**默认 50% 缩放**（整图看得见，落刀更准）；顶部 50% / 75% / 100% / 适合宽度 随时切换。

## 命令行直接切（批量 / 无人值守）

```bash
python long_image_slice.py 长图.jpg --cuts 1285 3562 6059      # 按绝对 Y 坐标
python long_image_slice.py 长图.jpg --heights 1000 1200 1500   # 按分段高度
python long_image_slice.py 长图.jpg --dry-run                  # 只打印清单不落盘
python long_image_slice.py 长图.jpg --max-size 1 --out "D:\出片"  # 体积上限(MB)与输出目录
python long_image_slice.py 长图.jpg --format png               # 严格无损（体积大得多）
```

`--cuts` 与 `--heights` 互斥。JPEG 无严格无损；「无损 jpg」只能理解为 quality 拉满 + 4:4:4。

## 硬约束（别凭直觉改，依据见 references/）

- **输出根目录固定为 `C:\Users\<你>\Desktop\切图`**（`--out` > 配置 `OUT=` > 默认），**每张图在其下各建一个同名子目录**：`切图\G2783S\`、`切图\M27831\`。切片是投递物，「打开目录 → 全选 → 上传」的动线不变。
- **必须一图一目录**：若全部平铺在同一目录，切第二张图时第一张的切片会被「全选」一起选上——而 `clean_stale` 只清同名前缀，救不了这种串台。这是静默错误，比直接报错危险得多。
- **切片目录里只能有切片**：预览块走系统临时目录，复核图落**源图旁边**（`<源图名>_切片复核图.png`）。混进任何 PNG/子目录都会被一起上传。
- **换图不重启服务**：图相关状态**全部**只存在于 `cfg` 里，任何漏到外面的全局量都会在换图时残留。`build_session()` 就是为此存在的唯一入口——加新状态时务必塞进 cfg。
- **单片 ≤1MB**（十进制 1,000,000 再留 2% 余量，兼容 1MiB 口径）。超了自动二分搜索最高达标质量，**本来就在限内的片不动**。
- **编码死守 4:4:4 + progressive**：实测降 quality 比降色度更划算（q96+4:4:4 → 0.95MB/SSIM 0.9968，优于 q95+4:2:0 → 0.64MB/SSIM 0.9878）。
- **最小片高 1000px 只是内部默认值**：仅喂给「自动识别切口」当参数、并给低于 1000px 的碎片亮警告，**不参与实际切割**。该值下实测出 22 片、最矮 1041px，与用户手工切法（23 片、最矮 961px）几乎一致。
- **自动铺刀只是助手**：它只能找到「有留白的地方」，对模块直接相接的刀位无能为力，不能替代人的判断。
- **片高与线型都不影响出片**：`/api/slice` 只读你点的 `cuts`、`单片上限`、以及用于打警告标记的 `min_h`。UI 上的片高输入框与线条颜色分级已按此原则**删除，不要再加回来**——它们是纯噪音，且会暗示「某种线更安全」这种并不存在的差别。

## 环境坑

- **curl 测本地服务必须加 `--noproxy '*'`**——本机有 `HTTP_PROXY`/`HTTPS_PROXY`，会把 127.0.0.1 拦成 502。
- 写桌面的部分路径会被安全策略拦截（`PermissionError`）→ 复核图改落技能目录 / 项目目录再展示。
- 托管 venv **无 tkinter** → 中文路径一律走 UTF-8 配置文件，不进 `.bat`。
- 前端逻辑验证**优先 jsdom**，不要依赖浏览器截图/坐标点击（截图常报 `display surface not available`、CDP timeout）。
- 服务自带 X-Token 校验 + Host 头防 DNS rebinding；无 token 或伪造 Host 一律 403，属**预期行为**。

## 自检

改过代码后跑一遍，确认没破坏既有行为：

```bash
cd "C:\Users\user\.workbuddy\skills\切图工作台"
PY="C:\Users\user\.workbuddy\binaries\python\envs\default\Scripts\python.exe"
"$PY" test_long_image_slice.py    # 切割核心（偏移/重叠/色度抽样/异常输入）
"$PY" test_upload_flow.py         # 换图上传链路（自带服务，38 项）
```

前端页面逻辑要喂一份**真实渲染的页面**进去（截图只能证明「看起来对」，jsdom 能证明逻辑对）：

```bash
curl -s --noproxy '*' http://127.0.0.1:8765/ -o /tmp/page.html
TOK=$(grep -oE 'TOKEN = "[0-9a-f]+"' /tmp/page.html | sed 's/.*"\([0-9a-f]*\)"/\1/')
curl -s --noproxy '*' -X POST http://127.0.0.1:8765/api/propose \
  -H "Content-Type: application/json" -H "X-Token: $TOK" \
  -d '{"minH":1000,"maxH":0}' -o /tmp/propose.json
NODE_PATH="C:\Users\user\.workbuddy\binaries\node\workspace\node_modules" \
  "C:\Users\user\.workbuddy\binaries\node\versions\22.22.2-6\node.exe" \
  test_ui_logic.js /tmp/page.html /tmp/propose.json
```

## 文件

| 文件 | 职责 |
|---|---|
| `slice_studio.py` | 工作台主程序：本地 HTTP 服务器 + 页面 + 一键出片 + 上传换图 |
| `long_image_slice.py` | 切割核心 + 空白带检测 + 切点提出算法（**单一来源**，两处共用） |
| `make_contact_sheet.py` | 接触印相图（切片网格拼图），人眼复核用 |
| `slice_studio.default` | 配置（UTF-8）：`IMAGE=` 默认源图（可空）/ `OUT=` 输出根目录 |
| `切片工作台.bat` | 双击启动器（纯 ASCII，可拖图上去） |
| `test_long_image_slice.py` | 切割核心端到端验证 |
| `test_upload_flow.py` | 换图上传链路端到端验证（起真服务、发真请求、读回页面） |
| `test_ui_logic.js` | 页面 JS 逻辑验证（jsdom 真实执行 + 断言 DOM） |
