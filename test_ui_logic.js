/* 前端逻辑验证：用 jsdom 真实执行页面里的 <script>，逐项断言。
 *
 * 为什么需要这个：截图只能证明「看起来对」，无法证明逻辑对；而浏览器点击
 * 通道不稳定时又容易把「没触发」误判成「逻辑错误」。jsdom 直接执行页面脚本，
 * 断言 DOM 结果，可重复、可定位。
 *
 * 用法：node test_ui_logic.js <渲染后的页面.html> <propose响应.json>
 */
const fs = require("fs");
const { JSDOM } = require("jsdom");

const htmlPath = process.argv[2];
const proposePath = process.argv[3];
const html = fs.readFileSync(htmlPath, "utf8");
const propose = JSON.parse(fs.readFileSync(proposePath, "utf8"));

const failures = [];
function check(name, ok, detail) {
  console.log(`  [${ok ? "PASS" : "FAIL"}] ${name}` + (detail ? `   ${detail}` : ""));
  if (!ok) failures.push(name);
}
const wait = (ms) => new Promise((r) => setTimeout(r, ms));

const fetched = [];
const dom = new JSDOM(html, {
  runScripts: "dangerously",
  pretendToBeVisual: true,
  beforeParse(w) {
    /* 只拦住网络，其余全部真实执行 */
    w.fetch = (url) => {
      fetched.push(String(url));
      if (String(url) === "/api/propose")
        return Promise.resolve({ json: () => Promise.resolve(propose) });
      return Promise.resolve({ json: () => Promise.resolve({ ok: true }) });
    };
  },
});
const { window } = dom;
const doc = window.document;

(async () => {
  await wait(200);

  console.log("测试 1  初始渲染");
  const tot0 = doc.getElementById("segtotal").textContent;
  check('状态「共 1 片」', /共 1 片/.test(tot0), JSON.stringify(tot0));
  check("切割线数 = 0", doc.querySelectorAll(".ln").length === 0);
  check("片高输入框已移除", !doc.getElementById("minh") && !doc.getElementById("maxh"));
  check("颜色说明文字已移除", !/蓝线|橙线/.test(doc.querySelector(".side").textContent));
  check("输出标签显示「切图」", /切图/.test(doc.getElementById("outdir").textContent));

  const W0 = +(/var W = (\d+)/.exec(doc.documentElement.innerHTML) || [0, 0])[1];
  const zoomOn = doc.querySelector(".zoom button.on");
  check("默认缩放按钮为 50%", !!zoomOn && zoomOn.textContent === "50%",
        `实际 ${zoomOn ? zoomOn.textContent : "无"}`);
  check("初始显示宽度 = 图宽的一半",
        doc.getElementById("stage").style.width === Math.round(W0 * 0.5) + "px",
        `实际 ${doc.getElementById("stage").style.width}，图宽 ${W0}`);

  console.log("\n测试 2  自动识别 → 后端 /api/propose");
  doc.getElementById("auto").click();
  await wait(300);
  check("确实请求了 /api/propose", fetched.includes("/api/propose"), JSON.stringify(fetched));

  const lines = Array.from(doc.querySelectorAll(".ln"));
  check(`渲染 ${propose.cuts.length} 条切割线`, lines.length === propose.cuts.length,
        `实际 ${lines.length}`);

  const cls = lines.map((e) => e.className);
  check("所有切割线样式一致（已取消分级）",
        cls.every((c) => c.trim() === "ln"), JSON.stringify(cls.slice(0, 3)));

  /* 切割线的 top 百分比必须与 Y 值严格对应，否则预览与坐标会错位 */
  const H = H_from(doc);
  const tops = lines.map((e) => parseFloat(e.style.top));
  const bad = tops.filter((t, i) =>
    Math.abs(t - propose.cuts[i] / H * 100) > 1e-6);
  check("切割线 top% 与该刀 Y 坐标严格对应",
        H > 0 && bad.length === 0,
        `H=${H} 首条 ${tops[0].toFixed(4)}% 期望 ${(propose.cuts[0] / H * 100).toFixed(4)}%`);

  const st = doc.getElementById("status").textContent;
  check("状态栏报刀数/片数", /共 \d+ 片/.test(st));
  check("状态栏报最矮片高", /最矮 \d+px/.test(st), JSON.stringify(st.slice(0, 90)));
  check("状态栏不再提主/次刀", !/主刀|次刀/.test(st));

  console.log("\n测试 3  过矮碎片亮警告（阈值固定 1000px）");
  window.cuts = [500, 800];          /* 顶部两片 500 / 300px，均低于 1000px */
  window.render();
  const warn = doc.querySelectorAll("#segs .seg.warn").length;
  check("存在低于 1000px 的片时标警告", warn >= 1, `${warn} 片被标记`);
  window.cuts = [3000, 6000];        /* 全部合格 */
  window.render();
  check("片高都合格后不再有警告",
        doc.querySelectorAll("#segs .seg.warn").length === 0);

  console.log("\n测试 4  切片结果面板");
  const fake = {
    out_dir: "C:\\Users\\user\\Desktop\\切图",
    count: 3, total_mb: 1.5, compressed_n: 1, over_n: 0, under_n: 1, min_h: 1000,
    stale: ["G2783S-24.jpg"], sheet: true,
    slices: [
      { file: "G2783S-01.jpg", height: 2000, mb: 0.5, quality: 100, compressed: false, over: false, under: false },
      { file: "G2783S-02.jpg", height: 300, mb: 0.4, quality: 98, compressed: true, over: false, under: true },
      { file: "G2783S-03.jpg", height: 1500, mb: 0.6, quality: 96, compressed: true, over: false, under: false },
    ],
  };
  window.showRes(fake, 1234);
  await wait(80);
  check("结果表 3 行", doc.querySelectorAll(".res tbody tr").length === 3);
  check("矮片行标记为橙色 u", doc.querySelectorAll(".res tbody tr.u").length === 1);
  const st2 = doc.getElementById("status").textContent;
  check("状态栏提示矮片", /低于最小片高 1000px/.test(st2), JSON.stringify(st2.slice(0, 130)));
  check("状态栏提示清理旧片", /清理旧片 1 个/.test(st2));
  check("状态栏提示已压缩", /1 片已压缩/.test(st2));
  check("出现「打开输出目录」按钮", !!doc.getElementById("reveal"));
  check("出现「查看复核图」按钮", !!doc.getElementById("sheet"));

  console.log();
  console.log(failures.length ? `失败 ${failures.length} 项：\n  - ` + failures.join("\n  - ")
                              : "全部通过");
  process.exit(failures.length ? 1 : 0);
})();

/* 从页面里取回图片原始高度，用于校验百分比换算 */
function H_from(doc) {
  const m = /var W = (\d+), H = (\d+)/.exec(doc.documentElement.innerHTML);
  return m ? parseInt(m[2], 10) : 0;
}
