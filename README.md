# 切图工作台 · Slice Studio

把**任意**超长图（详情页设计稿、长截图）按模块边界**不等高**切成多张 **≤1MB 的 JPG**。

> 技能名：`切图工作台`　目录：`C:\Users\user\.workbuddy\skills\切图工作台\`
> 说「切图」即唤醒；进了页面还能随时**换图**，不用重启。

## 为什么不用现成工具

| 方案 | 为什么不行 |
|---|---|
| PS「存储为 Web 所用格式」/ 切片导出 | **高度 30000px 硬上限**（CS6 起沿用）。实测素材 750×33744px，直接撞线 |
| 在线切图网站 / 网页版工具 | 浏览器 canvas 上限 **16384px = 2¹⁴**（GPU 纹理尺寸限制），33744px 必然失败 |
| 现成 GitHub 长图切割库 | 无大厂级成熟工具；且不暴露 JPEG 色度抽样参数，做不到 4:4:4 |

**正解**：运算放本地 CPU（33744px 位图展开仅 72MB，毫无压力），浏览器只负责显示与输入。
人只保留唯一不可替代的判断——**切在哪里**。

## 快速开始

**方式一：双击**（推荐给日常使用）
直接双击 `切片工作台.bat`。把图片拖到它上面就是切那张图；不拖也行，会开在「选择长图」页。

**方式二：命令行**
```bash
cd "C:\Users\user\.workbuddy\skills\切图工作台"
PY="C:\Users\user\.workbuddy\binaries\python\envs\default\Scripts\python.exe"
"$PY" slice_studio.py                    # 用配置里的默认图
"$PY" slice_studio.py "D:\新图.jpg"       # 直接切这张
"$PY" slice_studio.py --out "D:\出片"     # 换输出根目录
```

启动后输出：
```
  切片工作台已启动：http://127.0.0.1:8765/
  切片输出到：C:\Users\<你>\Desktop\切图\<图名>\
```
端口被占用会自动向后顺延，**以实际输出为准**。

**方式三：让 AI 唤醒**
在 WorkBuddy 里说「**切图**」，技能自动加载并启动工作台。

## 换图（不必重启）

**图片是可选的。** 配置里没写、或写的图已不在，服务照常启动，先进「选择长图」起始页。

三个换图入口，效果一样：

1. 侧栏顶部「**换一张图…**」
2. 把图片**拖进页面任意位置**（全屏虚线提示会亮起）
3. 起始页的「点击选择图片 / 拖到这里」

换图后服务端重建整个会话（重读尺寸 → 重扫分隔带 → 重新生成预览块），页面自动刷新。已标好的切割线会清空，所以换图前会先确认一次。

**这条链路不经过 canvas** —— 浏览器只搬字节，解码全在本地 Pillow，所以 16384px 的 GPU 纹理上限在这里根本不成立。上传按 1MB 分块流式落盘，不占内存。

## 流程

```
启动 → 选图/换图 → 浏览器里点标切割线 → 点「一键切图」→ 点「打开输出目录」
```
切割、压缩到 ≤1MB、生成复核图、打开目录，全部自动完成。

## 配置

改 `slice_studio.default`（UTF-8，**不要**把中文路径写进 `.bat`，cmd 会按 ANSI 代码页读成乱码）：

```
IMAGE=C:\Users\<你>\Desktop\长图.jpg   # 默认源图，可留空
OUT=C:\Users\<你>\Desktop\切图                          # 输出根目录
```

优先级：`--out` 参数 > 配置 `OUT=` > 默认 `<桌面>/切图`。

## 输出纪律

- **一图一子目录**：`切图\G2783S\`、`切图\M27831\`。若全部平铺在同一目录，切第二张图时第一张的切片会被「全选」一起选上——而自动清理只认同名前缀，救不了这种串台。
- **切片目录里只有切片**。预览块在系统临时目录，复核图落**源图旁边**（`<源图名>_切片复核图.png`）。混进任何 PNG / 子目录都会被一起传上去——静默错误。
- 重切为更少片数时，旧的 `-22/-23` 会被自动清理（只匹配 `^前缀-\d+\.(jpg|jpeg|png)$`，不碰 `说明.jpg` / `notes.txt`）。
- 上传的原图暂存在 `%TEMP%\slice_studio\uploads\`（你自己磁盘上本来就有，不必再占桌面）。

## 命令行切割（批量 / 无人值守）

```bash
python long_image_slice.py 长图.jpg --cuts 1285 3562 6059       # 按绝对 Y 坐标（模块边界用这个）
python long_image_slice.py 长图.jpg --heights 1000 1200 1500    # 按分段高度自动累加
python long_image_slice.py 长图.jpg --dry-run                   # 只打印清单，不落盘
python long_image_slice.py 长图.jpg --max-size 1 --out "D:\出片"  # 单片上限(MB) / 输出目录
python long_image_slice.py 长图.jpg --format png                # 严格无损
```

注意：这里是 `long_image_slice.py` 的 `--out`，指**确切目录**，不再套子目录。

## 自检

```bash
PY="C:\Users\user\.workbuddy\binaries\python\envs\default\Scripts\python.exe"
"$PY" test_long_image_slice.py    # 切割核心：零偏移 / 零重叠 / 色度抽样 / 异常输入
"$PY" test_upload_flow.py         # 换图上传链路：起真服务、发真请求、读回页面（38 项）
```

页面 JS 逻辑走 jsdom（见 `SKILL.md` 的自检段落）。

## 深挖

- 默认参数（min_h=1000、4:4:4、progressive、≤1MB）的**实测依据** → `references/硬约束与实测数据.md`
- 给 AI 用的操作手册 → `SKILL.md`

## 环境

Python 3.13 + Pillow 12.2.0：`C:\Users\user\.workbuddy\binaries\python\envs\default\Scripts\python.exe`
托管 venv **无 tkinter**，所以没有原生文件选择框——正好，选图交给浏览器的 `input[type=file]`，中文路径一律走 UTF-8 配置文件。
