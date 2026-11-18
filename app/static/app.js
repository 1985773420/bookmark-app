/* 书签库前端：原生 JS，无构建链。所有数据走 /api/*。 */
(() => {
  "use strict";

  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

  // 和 CSS 里的 --dur-* 对表：JS 也要知道动画什么时候演完
  const REDUCED = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const DUR = { 1: 120, 2: 180, 3: 280, 4: 460 };
  const EASE_OUT = "cubic-bezier(.22, 1, .36, 1)";
  const FINE_POINTER = matchMedia("(hover: hover) and (pointer: fine)").matches;

  const isEl = (n) => n instanceof Element;

  // ---------- 基础 ----------

  async function api(path, { method = "GET", body, raw } = {}) {
    const opts = { method, headers: {} };
    if (body !== undefined) {
      if (raw) {
        opts.body = body;
      } else {
        opts.headers["Content-Type"] = "application/json";
        opts.body = JSON.stringify(body);
      }
    }
    const resp = await fetch(path, opts);
    const text = await resp.text();
    let data = null;
    try { data = text ? JSON.parse(text) : null; } catch { data = text; }
    if (resp.status === 401) {
      // 会话过期：直接回登录页，别让每个调用方各自处理
      toast("登录已过期，正在跳转登录页…", "error");
      setTimeout(() => { location.href = "/login"; }, 900);
      throw new Error("未登录或登录已过期");
    }
    if (!resp.ok) {
      const msg = (data && (data.detail || data.message)) || text || `HTTP ${resp.status}`;
      throw new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
    }
    return data;
  }

  // ---------- 提示条 ----------

  /* 一条 toast 的完整生命周期：滑入 → 悬停暂停读秒 → 滑出。
     底部那根细线跟着倒计时走，用户知道还剩多久；点一下就立刻关掉。 */
  function toast(msg, kind = "", opts = {}) {
    const wrap = $("#toasts");
    if (!wrap) return;
    // 同时冒出来太多条会糊成一片，留最近 4 条
    while (wrap.children.length >= 4) wrap.firstElementChild.remove();

    const el = document.createElement("div");
    el.className = `toast ${kind}`;
    const text = document.createElement("span");
    text.textContent = msg;
    el.append(text);

    let dead = false, left = 0, t0 = 0, timer = null;
    const bar = document.createElement("span");
    const kill = () => {
      if (dead) return;
      dead = true;
      clearTimeout(timer);
      el.classList.add("is-out");
      setTimeout(() => el.remove(), DUR[2] + 40);
    };

    if (opts.action) {
      // 有「撤销」的提示条不自动消失，等用户决定
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "undo";
      btn.textContent = opts.action.label;
      btn.addEventListener("click", (e) => { e.stopPropagation(); kill(); opts.action.onClick(); });
      el.append(btn);
    }

    const ttl = opts.action ? 0 : (opts.ttl ?? (kind === "error" ? 6000 : 2800));
    if (ttl) {
      bar.className = "ttl";
      bar.style.animationDuration = `${ttl}ms`;
      el.append(bar);
    }

    const resume = () => {
      if (!ttl || dead) return;
      t0 = Date.now();
      timer = setTimeout(kill, left);
      bar.style.animationPlayState = "running";
    };
    const pause = () => {
      if (!ttl || dead) return;
      clearTimeout(timer);
      left -= Date.now() - t0;
      bar.style.animationPlayState = "paused";
    };
    left = ttl;
    el.addEventListener("click", kill);
    el.addEventListener("mouseenter", pause);
    el.addEventListener("mouseleave", resume);
    wrap.append(el);
    resume();
    return el;
  }

  // ---------- 弹层 ----------

  let modalClosing = false;
  let modalTimer = 0;
  let lastFocus = null;
  let scrollLocks = 0;

  function lockScroll(on) {
    scrollLocks = Math.max(0, scrollLocks + (on ? 1 : -1));
    document.body.style.overflow = scrollLocks ? "hidden" : "";
  }

  const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';
  const focusables = (box) => $$(FOCUSABLE, box)
    .filter((el) => el.offsetWidth || el.offsetHeight || el.getClientRects().length);

  /* Tab 只能在弹层里转圈，转出去就等于焦点丢了 —— 键盘用户会瞬间迷路 */
  function trapTab(box, e) {
    const items = focusables(box);
    if (!items.length) return;
    const first = items[0], last = items[items.length - 1];
    const inside = box.contains(document.activeElement);
    if (e.shiftKey && (!inside || document.activeElement === first)) {
      e.preventDefault(); last.focus();
    } else if (!e.shiftKey && document.activeElement === last) {
      e.preventDefault(); first.focus();
    }
  }

  // 当前开着的那层（命令面板优先，它是最后打开的）
  function openOverlay() {
    const pal = $("#palette");
    if (pal && !pal.hidden) return $(".palette-box", pal);
    const m = $("#modal");
    if (m && !m.hidden) return $("#modal-box");
    return null;
  }

  function closeModal() {
    const m = $("#modal");
    if (!m || m.hidden || modalClosing) return;
    modalClosing = true;
    m.classList.add("is-closing");
    // 等出场动画演完再 hidden，不然弹层会"啪"地消失
    modalTimer = setTimeout(() => {
      modalTimer = 0;
      m.classList.remove("is-closing");
      m.hidden = true;
      $("#modal-box").innerHTML = "";
      modalClosing = false;
      lockScroll(false);
      // 焦点还给打开它的那个按钮，用户接着按 Tab 还在原地
      if (lastFocus && document.contains(lastFocus)) lastFocus.focus({ preventScroll: true });
      lastFocus = null;
    }, REDUCED ? 0 : DUR[2] + 30);
  }

  function openModal(html) {
    const m = $("#modal"), box = $("#modal-box");
    if (!m || !box) return;
    // 上一个弹层还在往外走的时候又开了一个（比如「编辑分类」跳「删除分类」）：
    // 把那个收尾的定时器掐掉，否则它 180ms 后会把新内容一起清空
    if (modalTimer) { clearTimeout(modalTimer); modalTimer = 0; }
    modalClosing = false;
    m.classList.remove("is-closing");
    if (m.hidden) { lastFocus = document.activeElement; lockScroll(true); }
    box.innerHTML = html;
    m.hidden = false;
    const target = box.querySelector("[data-autofocus]") || box.querySelector("input, textarea, select");
    if (target) target.focus({ preventScroll: true });
    box.scrollTop = 0;
  }

  /* 破坏性操作走这个，不再用 window.confirm：能写清后果，也能配色 */
  function confirmModal({ title, body, ok = "确定", danger = false, onOk }) {
    openModal(`
      <h2>${esc(title)}</h2>
      <p class="muted small" style="margin:0 0 2px;line-height:1.6">${body}</p>
      <div class="form-actions">
        <button class="btn" data-close>取消</button>
        <button class="btn ${danger ? "danger" : "primary"}" id="cf-ok">${esc(ok)}</button>
      </div>`);
    const btn = $("#cf-ok");
    btn.focus({ preventScroll: true });
    btn.addEventListener("click", async () => {
      btn.disabled = true;
      try { await onOk(); } finally { btn.disabled = false; }
    });
  }

  document.addEventListener("click", (e) => {
    if (!isEl(e.target)) return;
    if (e.target.id === "modal" || e.target.closest("[data-close]")) closeModal();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Tab") {
      const box = openOverlay();
      if (box) trapTab(box, e);
      return;
    }
    if (e.key === "Escape") { closeModal(); closeMenu(); closePalette(); }
  });

  function esc(s) {
    return String(s ?? "").replace(/[&<>"']/g, (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }

  // ---------- 主题 / 配色 ----------

  /* theme-color 跟着 --bg 走：值从浏览器算出来的背景色里读，不在这里再抄一份色值表——
     颜色只存在于 CSS，改色只改一处。翻主题、换配色都要重算。 */
  function syncThemeColor() {
    const m = /rgba?\((\d+),\s*(\d+),\s*(\d+)/.exec(getComputedStyle(document.body).backgroundColor);
    if (!m) return;
    const hex = "#" + [1, 2, 3].map((i) => (+m[i]).toString(16).padStart(2, "0")).join("");
    $$('meta[name="theme-color"]').forEach((el) => {
      el.removeAttribute("media");     // 主题定了就不再跟系统走
      el.content = hex;
    });
  }

  /* 整页换色（翻主题、换配色）都从被点的那个元素起，圆形铺满屏幕：
     起点落在按钮/色点上，能看出改的是哪一块。 */
  function wipe(fn, fromEl) {
    if (document.querySelector('link[href*="studio.css"]') || REDUCED || !document.startViewTransition || !fromEl) return fn();
    const r = fromEl.getBoundingClientRect();
    const x = r.left + r.width / 2, y = r.top + r.height / 2;
    const far = Math.hypot(Math.max(x, innerWidth - x), Math.max(y, innerHeight - y));
    const t = document.startViewTransition(fn);
    t.ready.then(() => {
      document.documentElement.animate(
        { clipPath: [`circle(0px at ${x}px ${y}px)`, `circle(${far}px at ${x}px ${y}px)`] },
        { duration: DUR[4], easing: EASE_OUT, pseudoElement: "::view-transition-new(root)" });
      // 旧画面按住别动，否则圆波底下会先暗一层
      document.documentElement.animate({ opacity: [1, 1] },
        { duration: DUR[4], pseudoElement: "::view-transition-old(root)" });
    }).catch(() => {});
  }

  /* 深浅色只有一个开关：color-scheme。 */
  function applyTheme(next) {
    document.documentElement.dataset.theme = next;
    try { localStorage.setItem("bm-theme", next); } catch (e) {}
    syncThemeColor();
  }

  function toggleTheme(fromEl) {
    const now = document.documentElement.dataset.theme
      || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
    const next = now === "dark" ? "light" : "dark";
    wipe(() => applyTheme(next), fromEl);
  }

  function initTheme() {
    const btn = $("#theme-toggle");
    if (!btn) return;
    btn.addEventListener("click", () => {
      // 磁石开关：先让液滴被"磁化"（爆刺 + 甩尾），再翻主题。
      // 900ms 是整个尖刺动画的长度，到点就摘掉类，避免动画常驻耗电。
      btn.classList.add("is-charging");
      setTimeout(() => btn.classList.remove("is-charging"), 900);
      toggleTheme(btn);
    });
  }

  // ---------- 配色 ----------

  /* 四套色板的数值都在 CSS 里（同一批 token 的四个取值），JS 只管记住选了哪套、
     把它写到 <html data-accent> 上。选中态也由 CSS 的 [data-accent] 选出来，
     所以首帧就是对的，不会先亮错一个再跳。 */

  const ACCENTS = ["indigo", "teal", "violet", "amber"];
  const ACC_NAME = { indigo: "石墨靛蓝", teal: "青瓷", violet: "午夜紫", amber: "暖砂琥珀" };

  function accentNow() {
    const a = document.documentElement.dataset.accent;
    return ACCENTS.indexOf(a) >= 0 ? a : "indigo";
  }

  function syncAccents() {
    const now = accentNow();
    $$(".acc-dot").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.acc === now)));
  }

  function applyAccent(next) {
    document.documentElement.dataset.accent = next;
    try { localStorage.setItem("bm-accent", next); } catch (e) {}
    syncAccents();
    syncThemeColor();     // 暖砂那套的 --bg 跟另外三套差一丝，别留在旧值上
  }

  function setAccent(next, fromEl) {
    if (ACCENTS.indexOf(next) < 0 || next === accentNow()) return;
    wipe(() => applyAccent(next), fromEl || $("#palette"));
  }

  function initAccent() {
    const row = $(".acc-row");
    if (!row) return;
    syncAccents();
    row.addEventListener("click", (e) => {
      const dot = e.target.closest(".acc-dot");
      if (dot) setAccent(dot.dataset.acc, dot);
    });
  }

  // ---------- 动效内核：调度器 + 弹簧 ----------
  //
  // 指数插值只会"平滑地追上去"，永远不会冲过头——所以看起来像滑，不像有质量。
  // 真实的手感来自欠阻尼弹簧：目标在前方，物体先冲过去一点、再被拉回来。
  // 整页只有这一个调度循环：所有任务返回 true 表示还没停，全部停了这个 rAF 就结束。
  //
  // 性能纪律：静止时 0 帧；一帧最多为"当前那一个元素"写变量。

  const motionTasks = new Set();
  let motionRaf = 0;
  let motionLast = 0;

  function scheduleMotion() {
    if (motionRaf) return;
    motionLast = performance.now();
    motionRaf = requestAnimationFrame(runMotion);
  }

  function runMotion(now) {
    motionRaf = 0;
    // 归一化到 60fps 的"一帧"：切到 120Hz 屏不会变成两倍速
    const dt = Math.min(2, Math.max(0.4, (now - motionLast) / 16.667));
    motionLast = now;
    let alive = false;
    for (const t of motionTasks) {
      if (t(dt)) alive = true;
    }
    if (alive) scheduleMotion();
  }

  // 一个自由度：当前位置 v、目标 t、速度 vel。k 是刚度，d 是阻尼。
  // d < 2√k 时欠阻尼 → 会过冲，这正是"有质量"的来源。
  function springTo(v, k, d) {
    return { v, t: v, vel: 0, k, d };
  }

  function stepSpring(s, dt) {
    const force = (s.t - s.v) * s.k - s.vel * s.d;
    s.vel += force * dt;
    s.v += s.vel * dt;
    // 到位判定要连速度一起看，否则会在目标附近永远抖
    return Math.abs(s.t - s.v) < 0.0004 && Math.abs(s.vel) < 0.0004;
  }

  /* 弹簧参数用「刚度 k / 阻尼 d」这一对描述，手感由阻尼比 ζ = d/(2√k) 决定：
       ζ > 1   过阻尼：慢慢爬过去，不回弹
       ζ ≈ 1   临界：最快收敛但不冲过头
       ζ < 1   欠阻尼：冲过去再拉回来 —— 这就是"有质量"的来源
     下面四个都是按 ζ 反推出来的，注释里写了实测过冲量（90px 位移下）。 */
  const SPRING = {
    light: [0.42, 0.62],   // ζ .48 → 过冲 11.6%，3 帧到 90%：高光要脆
    main: [0.20, 0.52],    // ζ .58 → 过冲  5.3%，5 帧到 90%：主光有一点回弹
    echo: [0.07, 0.88],    // ζ 1.66 → 过阻尼，28 帧到 90%：拖尾只该慢慢跟
    magnet: [0.16, 0.42],  // ζ .52 → 过冲 10.2%，5 帧到 90%：吸附/回弹要明显
  };

  // ---------- 卡片：液光 + 倾斜 ----------

  const TILT = 4;   // 最大倾角（度），再大就像玩具

  function initCardMotion() {
    const grid = $("#grid");
    if (!grid) return;

    // 减少动效：写一次静态光斑，不装循环
    if (REDUCED) {
      $$(".card").forEach((card) => {
        ["--mx", "--lx", "--gx"].forEach((k) => card.style.setProperty(k, "50%"));
        ["--my", "--ly", "--gy"].forEach((k) => card.style.setProperty(k, "0%"));
      });
      return;
    }
    if (!FINE_POINTER) return;

    let active = null;          // 当前指针所在的卡
    let rafTaskOn = false;
    const s = {
      mx: springTo(0.5, ...SPRING.light), my: springTo(0, ...SPRING.light),
      lx: springTo(0.5, ...SPRING.main), ly: springTo(0, ...SPRING.main),
      gx: springTo(0.5, ...SPRING.echo), gy: springTo(0, ...SPRING.echo),
    };

    function task(dt) {
      const card = active;
      if (!card) return false;
      let all = true;
      for (const k in s) {
        if (!stepSpring(s[k], dt)) all = false;
      }
      card.style.setProperty("--mx", (s.mx.v * 100).toFixed(2) + "%");
      card.style.setProperty("--my", (s.my.v * 100).toFixed(2) + "%");
      card.style.setProperty("--lx", (s.lx.v * 100).toFixed(2) + "%");
      card.style.setProperty("--ly", (s.ly.v * 100).toFixed(2) + "%");
      card.style.setProperty("--gx", (s.gx.v * 100).toFixed(2) + "%");
      card.style.setProperty("--gy", (s.gy.v * 100).toFixed(2) + "%");
      // 倾角跟着主液光走：弹簧的过冲会让卡片"甩"一下再稳住
      card.style.setProperty("--ry", ((s.lx.v - 0.5) * TILT * 2).toFixed(2) + "deg");
      card.style.setProperty("--rx", ((0.5 - s.ly.v) * TILT * 2).toFixed(2) + "deg");
      // 边缘弧光的圆心指向指针
      const ang = Math.atan2(s.my.v - 0.5, s.mx.v - 0.5) * 180 / Math.PI + 90;
      card.style.setProperty("--a", ang.toFixed(0) + "deg");
      return !all;
    }

    function kick() {
      if (!rafTaskOn) {
        rafTaskOn = true;
        motionTasks.add(task);
      }
      scheduleMotion();
    }

    function enter(card) {
      if (active === card) return;
      if (active) leave(active);
      active = card;
      // 指针刚进来：把弹簧"瞬移"到指针位置，避免第一帧从卡片中心窜过去
      for (const k of ["mx", "lx", "gx"]) s[k].v = s[k].t;
      for (const k of ["my", "ly", "gy"]) s[k].v = s[k].t;
      kick();
    }

    function leave(card) {
      if (active !== card) return;
      active = null;
      rafTaskOn = false;
      motionTasks.delete(task);
      ["--rx", "--ry", "--mx", "--my", "--lx", "--ly", "--gx", "--gy", "--a"]
        .forEach((k) => card.style.removeProperty(k));
    }

    grid.addEventListener("pointermove", (e) => {
      const card = isEl(e.target) ? e.target.closest(".card") : null;
      if (!card) return;
      const r = card.getBoundingClientRect();
      if (!r.width || !r.height) return;
      enter(card);
      s.mx.t = Math.min(1, Math.max(0, (e.clientX - r.left) / r.width));
      s.my.t = Math.min(1, Math.max(0, (e.clientY - r.top) / r.height));
      // 三层目标一致，靠刚度差拉开距离：即时 / 主光 / 拖尾
      s.lx.t = s.gx.t = s.mx.t;
      s.ly.t = s.gy.t = s.my.t;
      kick();
    }, { passive: true });

    grid.addEventListener("pointerout", (e) => {
      const card = isEl(e.target) ? e.target.closest(".card") : null;
      if (!card || card.contains(e.relatedTarget)) return;
      leave(card);
    }, { passive: true });

    // 滚动后卡片的屏幕位置变了，旧的归一化坐标会失效——松手，等下次指针移动
    addEventListener("scroll", () => {
      if (active) leave(active);
    }, { passive: true });
  }

  // ---------- 按钮：磁性吸附 + 入点灌注 ----------
  //
  // 参照 snapblocks 的 Magnetic Button：指针在按钮外就把按钮"吸"过来一点，
  // 标签比按钮本体多走一截（读起来像有厚度）；进入时从指针那一点开始灌注，
  // 离开时朝离开的那一点退回去。

  const MAGNET_REACH = 90;     // 超出按钮边缘多远开始吸
  const MAGNET_STRENGTH = 0.32;
  const LABEL_EXTRA = 0.7;     // 标签额外位移比例（本体 1 + 标签 0.7 = 1.7 倍）

  function initMagnetic() {
    if (REDUCED || !FINE_POINTER) return;
    // .icon-toggle 也一起吸：顶栏那两个圆按钮和卡片角落的小按钮手感要一致
    const buttons = $$(".btn, .icon-btn, .icon-toggle").filter((b) => !b.querySelector("input"));
    if (!buttons.length) return;

    // 把按钮里的纯文本节点包进 .btn-lb：模板不用为动效改造，
    // 标签需要独立位移（比本体多走一截）才读得出厚度。
    for (const b of buttons) {
      if (b.querySelector(".btn-lb")) continue;
      const texts = [...b.childNodes].filter((n) => n.nodeType === 3 && n.textContent.trim());
      if (!texts.length) continue;
      const span = document.createElement("span");
      span.className = "btn-lb";
      texts[0].replaceWith(span);
      texts.forEach((t) => span.appendChild(t));
    }

    const live = new Map();   // btn -> { x, y, task, on }

    function makeState(btn) {
      return {
        btn,
        x: springTo(0, ...SPRING.magnet),
        y: springTo(0, ...SPRING.magnet),
        task: null,
      };
    }

    function taskFor(st) {
      return (dt) => {
        const sx = stepSpring(st.x, dt);
        const sy = stepSpring(st.y, dt);
        const b = st.btn;
        b.style.setProperty("--pull-x", st.x.v.toFixed(2) + "px");
        b.style.setProperty("--pull-y", st.y.v.toFixed(2) + "px");
        // 标签额外位移：靠一个 CSS 变量传下去，标签自己乘系数
        b.style.setProperty("--pull-lx", (st.x.v * LABEL_EXTRA).toFixed(2) + "px");
        b.style.setProperty("--pull-ly", (st.y.v * LABEL_EXTRA).toFixed(2) + "px");
        const done = sx && sy;
        if (done) {
          // 完全归位后把变量清掉，避免长期占着合成层
          if (!b.matches(":hover")) {
            b.style.removeProperty("--pull-x");
            b.style.removeProperty("--pull-y");
            b.style.removeProperty("--pull-lx");
            b.style.removeProperty("--pull-ly");
            b.style.removeProperty("--spec-a");
            motionTasks.delete(st.task);
            live.delete(b);
          }
        }
        return !done;
      };
    }

    function ensure(btn) {
      let st = live.get(btn);
      if (!st) {
        st = makeState(btn);
        st.task = taskFor(st);
        live.set(btn, st);
      }
      if (!motionTasks.has(st.task)) motionTasks.add(st.task);
      scheduleMotion();
      return st;
    }

    document.addEventListener("pointermove", (e) => {
      // 只在真正的按钮上或按钮周围一圈内计算
      for (const btn of buttons) {
        const r = btn.getBoundingClientRect();
        if (!r.width) continue;
        const cx = r.left + r.width / 2;
        const cy = r.top + r.height / 2;
        const dx = e.clientX - cx;
        const dy = e.clientY - cy;
        const near = Math.abs(dx) < r.width / 2 + MAGNET_REACH
          && Math.abs(dy) < r.height / 2 + MAGNET_REACH;
        if (!near) {
          const st = live.get(btn);
          if (st && (st.x.t || st.y.t)) {
            st.x.t = 0; st.y.t = 0;
            scheduleMotion();
          }
          continue;
        }
        // 指针在按钮本体之内时，顺手把主按钮的镜面角度挪过去（--spec-a 给 app.css 的
        // 指针反光用）。复用这一圈已有的 rect，不另开监听、不另量一次。
        if (btn.classList.contains("primary")
            && Math.abs(dx) < r.width / 2 && Math.abs(dy) < r.height / 2) {
          // 反光条垂直于"指针→中心"的方向：指针在左边时光从斜上方来
          const a = (90 + Math.atan2(-dy, -dx) * 180 / Math.PI).toFixed(0);
          btn.style.setProperty("--spec-a", a + "deg");
          // 镶边上那段弧光也跟着指针走（::before 的径向中心读 --fx/--fy）
          btn.style.setProperty("--fx", ((e.clientX - r.left) / r.width * 100).toFixed(1) + "%");
          btn.style.setProperty("--fy", ((e.clientY - r.top) / r.height * 100).toFixed(1) + "%");
        }
        const st = ensure(btn);
        st.x.t = dx * MAGNET_STRENGTH;
        st.y.t = dy * MAGNET_STRENGTH;
        scheduleMotion();
      }
    }, { passive: true });

    // 灌注：进入点 / 离开点决定圆形扩散的原点
    const setOrigin = (btn, e) => {
      const r = btn.getBoundingClientRect();
      if (!r.width) return;
      btn.style.setProperty("--fx", (((e.clientX - r.left) / r.width) * 100).toFixed(1) + "%");
      btn.style.setProperty("--fy", (((e.clientY - r.top) / r.height) * 100).toFixed(1) + "%");
    };

    document.addEventListener("pointerenter", (e) => {
      const btn = isEl(e.target) ? e.target.closest(".btn") : null;
      // pointerenter 会为按钮的每个子元素各触发一次；从按钮内部进到标签时忽略，
      // 否则灌注原点会被重置成"刚进入标签的那一点"
      if (!btn || (e.relatedTarget && btn.contains(e.relatedTarget))) return;
      setOrigin(btn, e);
      btn.classList.add("is-flood");
    }, true);

    document.addEventListener("pointerleave", (e) => {
      const btn = isEl(e.target) ? e.target.closest(".btn") : null;
      if (!btn) return;
      // 在按钮内部从标签挪回空白处不算离开；只有真正离开按钮（relatedTarget 在外部）
      // 才把原点挪到出口并撤掉灌注
      if (e.relatedTarget && btn.contains(e.relatedTarget)) return;
      setOrigin(btn, e);
      btn.classList.remove("is-flood");
    }, true);
  }

  // ---------- 按钮：粉笔描边 ----------
  //
  // 次级按钮悬停时，边框不是"变亮"，而是从指针最近的那条边擦出来：
  // 每条边各有一段笔肚（最亮）和几个忽浓忽淡的缺口，像粉笔压在纸面上。
  // 画完停在完整的框上，离开时瞬时收回，不在页面上留动画尾巴。
  //
  // 做法：给按钮挂 ::after，边框用反向镂空出来（conic 渐变 + mask 只留 1px），
  // 再用 clip-path: inset(--fc-t --fc-r --fc-b --fc-l) 从一条边推进到对边。
  // JS 只决定"从哪条边起笔"和缺口相位，真正的 380ms 过渡由 CSS 完成。
  //
  // 种子（--fc-seed）由进入点的归一化坐标算出来：同一套缺口模板按不同相位扫，
  // 所以每次画出来的虚线都不一样，而不必给每个按钮预生成一套。

  const CHALK_SEL = ".btn:not(.primary):not(.ghost):not(.danger):not(.sm):not(.choice)";

  function initChalk() {
    if (REDUCED || !FINE_POINTER) return;
    document.addEventListener("pointerenter", (e) => {
      const btn = isEl(e.target) ? e.target.closest(CHALK_SEL) : null;
      // 从按钮内部进到标签/图标时不算"进入"：否则每次越过子元素都会重新起笔，
      // 边框会在指针下面反复跳回起点
      if (!btn || (e.relatedTarget && btn.contains(e.relatedTarget))) return;
      const r = btn.getBoundingClientRect();
      if (!r.width) return;
      const x = (e.clientX - r.left) / r.width;
      const y = (e.clientY - r.top) / r.height;
      // 从离指针最近的那条边起笔：把那条边的起始裁剪量设为 100%（整条边藏在外面），
      // 其余三条归零；随后 .is-chalk 把这四个值一起收到 0，边框就从指针那一侧擦出来。
      const edge = [y, 1 - y, x, 1 - x];
      const nearest = edge.indexOf(Math.min(...edge));
      const vars = ["--fc-t", "--fc-b", "--fc-l", "--fc-r"];
      for (const v of vars) btn.style.setProperty(v, "0%");
      btn.style.setProperty(vars[nearest], "100%");
      // 进入点决定粉笔缺口的相位——同一个按钮两次画出的虚线不会一模一样
      btn.style.setProperty("--fc-seed", (Math.round(x * 36 + y * 19) % 36) + "deg");
      btn.classList.add("is-chalk");
    }, true);

    document.addEventListener("pointerleave", (e) => {
      const btn = isEl(e.target) ? e.target.closest(CHALK_SEL) : null;
      if (!btn) return;
      // 只在真正离开按钮时收笔；按钮内部子元素之间的 pointerleave 一律忽略
      if (e.relatedTarget && btn.contains(e.relatedTarget)) return;
      btn.classList.remove("is-chalk");
    }, true);
  }

  // ---------- 点击火花 ----------
  //
  // 点下去的一瞬间，从指针位置放出一小束短光条，向外散开并衰减。
  // 用一块全屏 canvas（pointer-events:none），粒子清空后循环自动停。

  function initSparks() {
    if (REDUCED) return;
    const canvas = $("#spark-canvas");
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    let dpr = 1, W = 0, H = 0;
    const sparks = [];

    function resize() {
      dpr = Math.min(2, window.devicePixelRatio || 1);
      W = canvas.clientWidth;
      H = canvas.clientHeight;
      canvas.width = Math.round(W * dpr);
      canvas.height = Math.round(H * dpr);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    }
    resize();
    addEventListener("resize", resize, { passive: true });

    function burst(x, y, color) {
      const n = 10;
      for (let i = 0; i < n; i++) {
        const a = (i / n) * Math.PI * 2 + Math.random() * 0.5;
        const sp = 1.6 + Math.random() * 2.2;
        sparks.push({
          x, y,
          vx: Math.cos(a) * sp,
          vy: Math.sin(a) * sp,
          life: 1,
          len: 5 + Math.random() * 7,
          color,
        });
      }
      scheduleMotion();
    }

    function task(dt) {
      ctx.clearRect(0, 0, W, H);
      if (!sparks.length) return false;
      ctx.lineCap = "round";
      for (let i = sparks.length - 1; i >= 0; i--) {
        const p = sparks[i];
        p.x += p.vx * dt;
        p.y += p.vy * dt;
        p.vx *= 0.94;
        p.vy *= 0.94;
        p.life -= 0.045 * dt;
        if (p.life <= 0) {
          sparks.splice(i, 1);
          continue;
        }
        ctx.globalAlpha = p.life;
        ctx.strokeStyle = p.color;
        ctx.lineWidth = 2;
        ctx.beginPath();
        ctx.moveTo(p.x, p.y);
        ctx.lineTo(p.x - p.vx * p.len * 0.5, p.y - p.vy * p.len * 0.5);
        ctx.stroke();
      }
      ctx.globalAlpha = 1;
      return true;
    }

    motionTasks.add(task);

    document.addEventListener("pointerdown", (e) => {
      if (!isEl(e.target)) return;
      const hit = e.target.closest(".btn, .card, .icon-btn, .tag, .tree-row, .nav a");
      if (!hit) return;
      const brand = getComputedStyle(document.documentElement)
        .getPropertyValue("--brand").trim() || "#4f46e5";
      burst(e.clientX, e.clientY, brand);
    }, { passive: true });
  }

  // ---------- 滚动揭示 ----------
  //
  // 卡片进入视口时上浮 + 去模糊。JS 只加一个 class，动画全在 CSS。
  // 先给卡片加 .rv-armed（隐藏），观察器命中后换成 .is-rv；
  // 这样即使 JS 挂掉，卡片也只会保持可见，不会永久隐身。

  function initReveal() {
    const cards = $$(".card");
    if (!cards.length) return;

    if (REDUCED || !("IntersectionObserver" in window)) {
      cards.forEach((c) => c.classList.add("is-rv"));
      return;
    }

    cards.forEach((c) => c.classList.add("rv-armed"));
    const io = new IntersectionObserver((entries) => {
      for (const ent of entries) {
        if (!ent.isIntersecting) continue;
        const el = ent.target;
        io.unobserve(el);
        // 同排卡片依次进场：用元素在网格里的列位置算延迟
        const i = Number(el.dataset.rvi || 0);
        el.style.setProperty("--rv-d", Math.min(i, 7) * 55 + "ms");
        el.classList.remove("rv-armed");
        el.classList.add("is-rv");
      }
    }, { rootMargin: "0px 0px -8% 0px", threshold: 0.08 });

    cards.forEach((c, i) => {
      c.dataset.rvi = String(i % 4);
      io.observe(c);
    });
  }

  // ---------- 数字滚动 ----------

  function initCountUp() {
    const el = $(".page-sub");
    if (!el || REDUCED) return;
    const m = el.textContent.match(/(\d+)/);
    if (!m) return;
    const target = Number(m[1]);
    if (!target || target > 100000) return;
    const start = performance.now();
    const dur = 620;
    const node = [...el.childNodes].find((n) => n.nodeType === 3 && /\d/.test(n.textContent));
    if (!node) return;
    const raw = node.textContent;
    function tick(now) {
      const p = Math.min(1, (now - start) / dur);
      const eased = 1 - Math.pow(1 - p, 3);
      node.textContent = raw.replace(/\d+/, String(Math.round(target * eased)));
      if (p < 1) requestAnimationFrame(tick);
      else node.textContent = raw;
    }
    requestAnimationFrame(tick);
  }


  // ---------- Canvas 极光背景 ----------
  //
  // CSS 渐变做的光团只能在固定关键帧之间插值，看久了"像一张会呼吸的图"。
  // 这里换成 canvas：6 团光在各自的正弦轨道上漂移，叠乘叠加（lighter），
  // 边缘天然柔和，运动不会再回到同一个位置——这才像"流"。
  //
  // 成本控制：
  //   * 画布按 0.55 缩放（再被 CSS 拉伸模糊），像素量只有 1/3；
  //   * 页面不可见时整段暂停（document.hidden）；
  //   * prefers-reduced-motion 只画一帧静态图。

  const AURORA_PALETTE = {
    // 每种配色：亮色 / 暗色两套 RGB，跟 --brand 的取色保持一致
    indigo: [[79, 70, 229], [129, 140, 248]],
    teal: [[13, 148, 136], [45, 212, 191]],
    violet: [[124, 58, 237], [167, 139, 250]],
    amber: [[234, 88, 12], [251, 146, 60]],
  };
  const AURORA_ACCENT_RGB = {
    indigo: [34, 211, 238],
    teal: [56, 189, 248],
    violet: [244, 114, 182],
    amber: [250, 204, 21],
  };

  function initAurora() {
    const canvas = $("#aurora");
    if (!canvas) return;
    const ctx = canvas.getContext("2d", { alpha: true });
    if (!ctx) return;

    let W = 0, H = 0, dpr = 1;
    // 内部渲染比例：0.30。光本身就是糊的，放大 3 倍看不出来，
    // 但像素量只剩 1/11——这是这块画布能不能常驻的关键。
    const SCALE = 0.30;
    // 环境光不需要 60fps。20fps 足够"在流"，CPU 直接砍掉三分之二。
    const FPS = 20;
    const FRAME_MS = 1000 / FPS;
    const blobs = [];
    const sprites = new Map();   // 颜色 -> 预渲染的光斑
    let raf = 0;
    let t0 = performance.now();
    let lastPaint = 0;
    let running = false;

    function accent() {
      const a = document.documentElement.dataset.accent || "indigo";
      return AURORA_PALETTE[a] ? a : "indigo";
    }
    function isDark() {
      return getComputedStyle(document.documentElement).colorScheme.includes("dark");
    }

    // 一个光斑只做一次：256×256 的径向渐变贴图。
    // 每帧改用 drawImage 贴图，而不是 createRadialGradient + fill（那个每次都要重算渐变）。
    function sprite(rgb) {
      const key = rgb.join(",");
      if (sprites.has(key)) return sprites.get(key);
      const S = 256;
      const c = document.createElement("canvas");
      c.width = c.height = S;
      const g2 = c.getContext("2d");
      const g = g2.createRadialGradient(S / 2, S / 2, 0, S / 2, S / 2, S / 2);
      const [r, gg, b] = rgb;
      g.addColorStop(0, `rgba(${r},${gg},${b},1)`);
      g.addColorStop(0.45, `rgba(${r},${gg},${b},.45)`);
      g.addColorStop(1, `rgba(${r},${gg},${b},0)`);
      g2.fillStyle = g;
      g2.fillRect(0, 0, S, S);
      sprites.set(key, c);
      return c;
    }

    function build() {
      const a = accent();
      const dark = isDark();
      const [c1, c2] = AURORA_PALETTE[a];
      const c3 = AURORA_ACCENT_RGB[a];
      blobs.length = 0;
      const defs = [
        [c1, .62, .22, .055, 0.0, .30],
        [c2, .48, .19, .041, 2.1, .26],
        [c3, .40, .17, .033, 4.2, .18],
        [c1, .44, .15, .027, 1.2, .22],
        [c2, .34, .12, .048, 5.1, .16],
        [c3, .30, .14, .022, 3.4, .14],
      ];
      for (const [rgb, rr, ar, sp, ph, al] of defs) {
        blobs.push({ sprite: sprite(rgb), rr, ar, sp, ph, al: al * (dark ? 1.25 : 1), hue: Math.random() * 6.28 });
      }
    }

    function resize() {
      dpr = Math.min(2, window.devicePixelRatio || 1) * SCALE;
      const r = canvas.getBoundingClientRect();
      W = Math.max(1, r.width);
      H = Math.max(1, r.height);
      canvas.width = Math.round(W * dpr);
      canvas.height = Math.round(H * dpr);
    }

    function paint(t) {
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, W, H);
      ctx.globalCompositeOperation = "lighter";
      const R = Math.max(W, H);
      for (const b of blobs) {
        const ang = t * b.sp + b.ph;
        // 轨道 + 一个很慢的半径脉动，避免看起来像绕圈
        const wob = 1 + 0.16 * Math.sin(t * 0.11 + b.hue);
        const cx = W * (0.5 + Math.cos(ang) * b.ar * wob);
        const cy = H * (0.5 + Math.sin(ang * 0.83 + b.ph) * b.ar * 0.8 * wob);
        const rad = R * b.rr * (0.9 + 0.1 * Math.sin(t * 0.07 + b.hue));
        ctx.globalAlpha = b.al;
        ctx.drawImage(b.sprite, cx - rad, cy - rad, rad * 2, rad * 2);
      }
      ctx.globalAlpha = 1;
      ctx.globalCompositeOperation = "source-over";
    }

    function loop(now) {
      raf = 0;
      if (!running) return;
      if (now - lastPaint >= FRAME_MS) {
        lastPaint = now;
        paint((now - t0) / 1000);
      }
      raf = requestAnimationFrame(loop);
    }

    function start() {
      if (running || REDUCED) return;
      running = true;
      if (!raf) raf = requestAnimationFrame(loop);
    }
    function stop() {
      running = false;
      if (raf) cancelAnimationFrame(raf);
      raf = 0;
    }

    build();
    resize();
    paint(0);

    if (REDUCED) {
      // 静态一帧，之后主题/配色变了再重画一次
      const redraw = () => { build(); resize(); paint(0); };
      new MutationObserver(redraw).observe(document.documentElement,
        { attributes: true, attributeFilter: ["data-theme", "data-accent"] });
      return;
    }

    start();
    addEventListener("resize", () => { resize(); paint((performance.now() - t0) / 1000); }, { passive: true });
    document.addEventListener("visibilitychange", () => {
      if (document.hidden) stop();
      else start();
    });
    // 窗口在但没焦点（并排摆放/切到别的应用）时也停：环境光没人看就不烧 CPU
    addEventListener("blur", stop);
    addEventListener("focus", () => { if (!document.hidden) start(); });
    // 滚动期间也停：画布是固定层，滚动会逼它每帧重绘（实测滚动开销是空闲的 4 倍）。
    // 环境光在滚动时"冻"一下没人看得出来，停手 160ms 后再接着流。
    let scrollTimer = 0;
    addEventListener("scroll", () => {
      stop();
      clearTimeout(scrollTimer);
      scrollTimer = setTimeout(() => { if (!document.hidden) start(); }, 160);
    }, { passive: true });
    new MutationObserver(() => {
      build();
      paint((performance.now() - t0) / 1000);
    }).observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme", "data-accent"] });
  }


  function initFavicons() {
    // 缓存里的图不会再触发 load，进场时补一刀，别让图标一直隐形
    $$(".favicon img.is-loading").forEach((img) => {
      if (img.complete) img.classList.remove("is-loading");
    });
  }

  function initRipple() {
    if (REDUCED) return;
    document.addEventListener("pointerdown", (e) => {
      const btn = isEl(e.target) ? e.target.closest(".btn") : null;
      if (!btn || btn.disabled) return;
      const r = btn.getBoundingClientRect();
      const size = Math.max(r.width, r.height);
      const s = document.createElement("span");
      s.className = "ripple";
      s.style.width = s.style.height = `${size}px`;
      s.style.left = `${e.clientX - r.left - size / 2}px`;
      s.style.top = `${e.clientY - r.top - size / 2}px`;
      btn.append(s);
      setTimeout(() => s.remove(), DUR[4] + 60);
    });
  }

  /* FLIP：先量一遍位置，改完 DOM 再量一遍，把位移用动画倒着播回去。
     筛选、删除、拖拽补位都靠它，不然卡片只会"啪"地瞬移。
     exclude 用来放过正在被拖的那张：它自己有 dragging 的 transform，别抢。 */
  function flip(container, mutate, exclude = null) {
    if (!container) return;
    const items = $$(".card", container).filter((el) => el !== exclude);
    const first = new Map();
    items.forEach((el) => first.set(el, el.getBoundingClientRect()));
    mutate();
    if (REDUCED) return;
    items.forEach((el) => {
      if (!el.isConnected || el.classList.contains("is-filtered-out")) return;
      const a = first.get(el), b = el.getBoundingClientRect();
      const dx = a.left - b.left, dy = a.top - b.top;
      if (!dx && !dy) return;
      el.animate([{ transform: `translate(${dx}px, ${dy}px)` }, { transform: "none" }],
        { duration: DUR[3], easing: EASE_OUT });
    });
  }

  /* 删卡不是直接 remove：先塌陷成 0 高，再让后面的卡片滑上来 */
  function collapseCard(el) {
    const grid = el.parentElement;
    if (REDUCED) { flip(grid, () => el.remove()); return; }
    el.style.pointerEvents = "none";
    const r = el.getBoundingClientRect();
    const anim = el.animate(
      [{ opacity: 1, height: `${r.height}px` }, { opacity: 0, height: "0px" }],
      { duration: DUR[3], easing: EASE_OUT, fill: "forwards" });
    anim.onfinish = () => flip(grid, () => el.remove());
  }

  // ---------- 卡片菜单 ----------

  let menuEl = null;

  function closeMenu() {
    if (!menuEl) return;
    const el = menuEl;
    menuEl = null;
    el.classList.add("is-closing");
    setTimeout(() => el.remove(), DUR[1] + 40);
    const more = el.dataset.opener && $(`.more[data-id="${el.dataset.opener}"]`);
    if (more) more.setAttribute("aria-expanded", "false");
  }

  function openMenu(items, x, y, opener = null) {
    closeMenu();
    const el = document.createElement("div");
    el.className = "menu";
    el.setAttribute("role", "menu");
    if (opener) el.dataset.opener = opener;

    for (const it of items) {
      if (it === null) { const s = document.createElement("div"); s.className = "menu-sep"; el.append(s); continue; }
      const b = document.createElement("button");
      b.type = "button";
      b.className = `menu-item${it.danger ? " danger" : ""}`;
      b.setAttribute("role", "menuitem");
      b.textContent = it.label;
      if (it.hint) {
        const k = document.createElement("span");
        k.className = "k";
        k.textContent = it.hint;
        b.append(k);
      }
      b.addEventListener("click", () => {
        // 菜单项一按就要消失，但接下来开的弹窗会记住「谁叫醒了我」，
        // 所以要趁现在把焦点交还给触发菜单的那个按钮，不然弹窗关掉时焦点没处可去
        const back = menuEl?.dataset.opener && $(`.more[data-id="${menuEl.dataset.opener}"]`);
        closeMenu();
        if (back && back.isConnected) back.focus({ preventScroll: true });
        it.run();
      });
      el.append(b);
    }

    el.addEventListener("keydown", (e) => {
      const list = $$(".menu-item", el);
      const i = list.indexOf(document.activeElement);
      if (e.key === "ArrowDown") { e.preventDefault(); list[(i + 1) % list.length].focus(); }
      else if (e.key === "ArrowUp") { e.preventDefault(); list[(i - 1 + list.length) % list.length].focus(); }
      else if (e.key === "Escape") { e.preventDefault(); closeMenu(); }
    });

    document.body.append(el);
    // 贴着屏幕边时往回收，别让菜单跑出去
    const r = el.getBoundingClientRect();
    const left = Math.max(8, Math.min(x, innerWidth - r.width - 8));
    const top = Math.max(8, Math.min(y, innerHeight - r.height - 8));
    el.style.left = `${left}px`;
    el.style.top = `${top}px`;
    el.style.setProperty("--ox", `${x - left}px`);
    el.style.setProperty("--oy", `${y - top}px`);
    menuEl = el;
    if (opener) $(`.more[data-id="${opener}"]`)?.setAttribute("aria-expanded", "true");
    el.querySelector(".menu-item")?.focus({ preventScroll: true });
  }

  document.addEventListener("pointerdown", (e) => {
    if (menuEl && !(isEl(e.target) && e.target.closest(".menu"))) closeMenu();
  }, true);
  document.addEventListener("scroll", () => closeMenu(), { passive: true, capture: true });
  addEventListener("resize", () => closeMenu());

  async function copyText(text) {
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      // 走 http 时没有 clipboard API，退回老办法
      const ta = document.createElement("textarea");
      ta.value = text;
      ta.style.cssText = "position:fixed;opacity:0";
      document.body.append(ta);
      ta.select();
      try { document.execCommand("copy"); } catch {}
      ta.remove();
    }
    toast("链接已复制", "ok");
  }

  /* ---------- 内网 / 外网 ----------
     卡片带 data-url-lan 的，浏览器侧开一个 no-cors 请求去探内网地址：能拿到
     opaque 响应说明本机直达得了，就把 href 换成内网地址；超时或报错就继续走外网。
     探测结果按地址缓存在 sessionStorage 里，翻页不重复探。 */
  const LAN_PROBE_MS = 2500;
  const LAN_TTL = 10 * 60 * 1000;
  const lanCache = new Map();

  function lanCached(url) {
    let raw = lanCache.get(url);
    if (raw === undefined) {
      try { raw = JSON.parse(sessionStorage.getItem("lan:" + url) || "null"); }
      catch { raw = null; }
      lanCache.set(url, raw);
    }
    return raw && Date.now() - raw.at < LAN_TTL ? raw.ok : null;
  }

  function lanStore(url, ok) {
    const rec = { ok, at: Date.now() };
    lanCache.set(url, rec);
    try { sessionStorage.setItem("lan:" + url, JSON.stringify(rec)); } catch { /* 隐私模式下写不进去，无所谓 */ }
  }

  function probeLan(url) {
    const known = lanCached(url);
    if (known !== null) return Promise.resolve(known);
    // no-cors 下只要能建立连接就会拿到 opaque 响应；连不上则是 rejected。
    // 用 GET 而不是 HEAD：不少内网服务对 HEAD 直接 405/断开。
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), LAN_PROBE_MS);
    return fetch(url, { mode: "no-cors", cache: "no-store", credentials: "omit", signal: ctrl.signal })
      .then(() => true, () => false)
      .then((ok) => {
        clearTimeout(timer);
        lanStore(url, ok);
        if (ok) applyLan();
        return ok;
      });
  }

  function effectiveHref(link) {
    const lan = link?.dataset.hrefLan;
    if (lan && lanCached(lan) === true) return lan;
    return link?.href || "";
  }

  let lanPending = [];
  function applyLan() {
    lanPending.filter((l) => l.dataset.hrefLan).forEach((link) => {
      const lan = link.dataset.hrefLan;
      if (lanCached(lan) !== true) return;
      link.href = lan;
      link.closest(".card")?.classList.add("via-lan");
    });
    // 已经确认能走内网的就不用再探了
    lanPending = lanPending.filter((l) => lanCached(l.dataset.hrefLan) !== true);
  }

  function initLan() {
    lanPending = $$(".card-link[data-href-lan]");
    if (!lanPending.length) return;
    const seen = new Set();
    lanPending.forEach((link) => {
      const lan = link.dataset.hrefLan;
      if (seen.has(lan)) return;
      seen.add(lan);
      // 同一地址只探一次；结果回来时 applyLan 统一改所有卡片
      if (lanCached(lan) === null) probeLan(lan);
    });
    applyLan();
  }

  function cardMenuItems(card) {
    const id = Number(card.dataset.id);
    const link = card.querySelector(".card-link");
    const url = effectiveHref(link);
    // 内网书签给一条临时换回外网的路径：万一在能连内网的地方想试外网地址
    const wan = link?.dataset.hrefLan && lanCached(link.dataset.hrefLan) === true ? link.dataset.hrefWan : "";
    const isPrivate = card.classList.contains("is-private");
    const items = [
      { label: "打开", hint: "↵", run: () => window.open(url, "_blank", "noopener") },
      { label: "复制链接", hint: "C", run: () => copyText(url) },
    ];
    if (wan) items.push({ label: "改用外网地址打开", run: () => window.open(wan, "_blank", "noopener") });
    // 游客只有看的份：别把编辑/删除摆到他面前
    if (!$(".shell.is-anon")) {
      items.push(
        { label: "编辑…", hint: "E", run: () => openBookmarkModal(id) },
        null,
        {
          label: isPrivate ? "设为公开" : "设为私有",
          run: async () => {
            try {
              await api(`/api/bookmarks/${id}`, { method: "PATCH", body: { is_private: !isPrivate } });
              toast(isPrivate ? "已设为公开" : "已设为私有", "ok");
              setTimeout(() => location.reload(), 500);
            } catch (err) { toast(err.message, "error"); }
          },
        },
        { label: "移动到分类…", run: () => moveModal([id]) },
        null,
        { label: "删除", danger: true, run: () => deleteBookmark(id, card.dataset.title, card) },
      );
    }
    return items;
  }

  /* 删除一律带着「撤销」：卡片先塌陷，提示条上留一个按钮把同一行原样插回去。
     从编辑弹窗里删的时候没有卡片可塌，card 传 null 就行。 */
  function deleteBookmark(id, title, card = null) {
    const label = title || "这个书签";
    confirmModal({
      title: "删除这个书签？",
      body: `「${esc(label)}」<br>删掉之后 10 分钟内还能在右下角的提示条上点「撤销」找回来。`,
      ok: "删除",
      danger: true,
      onOk: async () => {
        try {
          await api(`/api/bookmarks/${id}`, { method: "DELETE" });
          closeModal();
          if (card && card.isConnected) collapseCard(card);
          // 标题能到 200 字，提示条里只留个认得出来的开头
          const short = label.length > 34 ? label.slice(0, 34) + "…" : label;
          toast(`已删除「${short}」`, "ok", {
            action: { label: "撤销", onClick: () => restoreBookmark(id) },
          });
        } catch (err) { toast(err.message, "error"); }
      },
    });
  }

  async function restoreBookmark(id) {
    try {
      await api(`/api/bookmarks/${id}/restore`, { method: "POST" });
      toast("已恢复", "ok");
      // 恢复的卡片要回到原来的位置，直接刷新最省事，也保证计数跟服务端一致
      setTimeout(() => location.reload(), 400);
    } catch (err) { toast(err.message, "error"); }
  }

  async function moveModal(ids) {
    let opts;
    try { opts = await categoryOptions(null); }
    catch (err) { return toast(err.message, "error"); }
    openModal(`
      <h2>移动到分类</h2>
      <div class="form-row"><label>目标分类</label>
        <select id="mv-cat" data-autofocus><option value="">（未分类）</option>${opts}</select></div>
      <div class="form-actions">
        <button class="btn" data-close>取消</button>
        <button class="btn primary" id="mv-go">${ids.length > 1 ? `移动 ${ids.length} 条` : "移动"}</button>
      </div>`);
    $("#mv-go").addEventListener("click", async () => {
      const sel = $("#mv-cat");
      const category_id = sel.value ? Number(sel.value) : null;
      const where = sel.value ? `「${sel.selectedOptions[0].textContent.trim()}」` : "未分类";
      const btn = $("#mv-go");
      btn.disabled = true;
      try {
        if (ids.length === 1) {
          await api(`/api/bookmarks/${ids[0]}`, { method: "PATCH", body: { category_id } });
        } else {
          await api("/api/bookmarks/bulk", { method: "POST", body: { ids, action: "move", category_id } });
        }
        closeModal();
        toast(`已移动到${where}（${ids.length} 条）`, "ok");
        setTimeout(() => location.reload(), 600);
      } catch (err) {
        toast(err.message, "error");
        btn.disabled = false;
      }
    });
  }

  function tagModal(ids, action) {
    const add = action === "tag_add";
    openModal(`
      <h2>${add ? "添加标签" : "移除标签"}</h2>
      <p class="muted small" style="margin:-4px 0 2px;line-height:1.6">对选中的 ${ids.length} 个书签生效，多个标签用逗号分开。</p>
      <div class="form-row"><label>标签</label>
        <input id="tg-input" data-autofocus placeholder="待读, 常用"></div>
      <div class="form-actions">
        <button class="btn" data-close>取消</button>
        <button class="btn primary" id="tg-go">${add ? "添加" : "移除"}</button>
      </div>`);
    const go = async () => {
      const tags = $("#tg-input").value.split(",").map((s) => s.trim()).filter(Boolean);
      if (!tags.length) return toast("至少填一个标签", "error");
      const btn = $("#tg-go");
      btn.disabled = true;
      try {
        const r = await api("/api/bookmarks/bulk", { method: "POST", body: { ids, action, tags } });
        closeModal();
        toast(`已${add ? "添加" : "移除"}标签（${r.affected} 条）`, "ok");
        setTimeout(() => location.reload(), 700);
      } catch (err) { toast(err.message, "error"); btn.disabled = false; }
    };
    $("#tg-go").addEventListener("click", go);
    // 输入框里直接回车就提交，不用去够按钮
    $("#tg-input").addEventListener("keydown", (e) => {
      if (e.key === "Enter") { e.preventDefault(); go(); }
    });
  }

  // ---------- 分类树 ----------

  async function categoryOptions(selected) {
    const tree = await api("/api/categories");
    const out = [];
    const walk = (nodes, depth) => nodes.forEach((n) => {
      out.push(`<option value="${n.id}"${String(selected) === String(n.id) ? " selected" : ""}>${"　".repeat(depth)}${esc(n.name)}</option>`);
      walk(n.children || [], depth + 1);
    });
    walk(tree, 0);
    return out.join("");
  }

  function initTree() {
    $$(".tree-node").forEach((node) => {
      const twist = node.querySelector("[data-twist]");
      if (!twist) return;
      const kids = node.querySelector(".tree-children");
      if (!kids) return;
      // 当前选中的分类要自动展开，否则用户看不到自己在哪
      if (node.querySelector(".tree-row.is-active")) {
        node.classList.add("open");
        kids.hidden = false;
      }
      twist.addEventListener("click", (e) => {
        e.preventDefault();
        e.stopPropagation();
        node.classList.toggle("open");
        kids.hidden = !node.classList.contains("open");
      });
    });
  }

  function initCategoryActions() {
    const btn = $("#add-root-cat");
    if (btn) {
      btn.addEventListener("click", () => openCategoryModal(null, null));
    }
    const tree = $("#tree");
    if (!tree) return;
    tree.addEventListener("contextmenu", (e) => {
      const node = e.target.closest(".tree-node");
      // 「全部书签」「未分类」是写死的链接，没有分类可编辑
      if (!node || !node.dataset.catId) return;
      e.preventDefault();
      openMenu(categoryMenuItems(node), e.clientX, e.clientY);
    });
  }

  function categoryMenuItems(node) {
    const id = Number(node.dataset.catId);
    const row = node.querySelector(".tree-row");
    const name = row.dataset.name || "";
    return [
      { label: "打开", hint: "↵", run: () => { location.href = row.href; } },
      { label: "重命名 / 移动…", hint: "E", run: () => openCategoryModal(id, name) },
      { label: "新建子分类…", run: () => openCategoryModal(null, null, id) },
      null,
      { label: "删除…", danger: true, run: () => deleteCategoryModal(id, name) },
    ];
  }

  function deleteCategoryModal(catId, name) {
    openModal(`
      <h2>删除分类「${esc(name)}」</h2>
      <p class="muted" style="margin-top:-6px">分类里的东西怎么办？</p>
      <div class="choices">
        <button class="btn choice" id="cat-up">
          <b>上提到父级</b>
          <small>书签和子分类都留着，只是不再挂在这个分类下</small>
        </button>
        <button class="btn choice danger" id="cat-purge">
          <b>连子树一起删除</b>
          <small>子分类整棵删掉，里面的书签移到父级，不会丢</small>
        </button>
      </div>
      <div class="form-actions"><button class="btn" data-close>取消</button></div>
    `);
    const go = (mode) => async (e) => {
      const btn = e.currentTarget;
      btn.disabled = true;
      try {
        await api(`/api/categories/${catId}?mode=${mode}`, { method: "DELETE" });
        location.href = "/";
      } catch (err) {
        toast(err.message, "error");
        btn.disabled = false;
      }
    };
    $("#cat-up").addEventListener("click", go("move"));
    $("#cat-purge").addEventListener("click", go("purge"));
  }

  async function openCategoryModal(catId, name, presetParent = null) {
    const opts = await categoryOptions(null);
    const isNew = !catId;
    openModal(`
      <h2>${isNew ? "新建分类" : "编辑分类"}</h2>
      <div class="form-row"><label>名称</label><input id="cat-name" value="${esc(name || "")}"></div>
      <div class="form-row"><label>父分类</label>
        <select id="cat-parent"><option value="">（顶层）</option>${opts}</select></div>
      <div class="form-actions">
        ${isNew ? "" : '<button class="btn danger" id="cat-del">删除</button>'}
        <button class="btn" data-close>取消</button>
        <button class="btn primary" id="cat-save">保存</button>
      </div>
      <p class="muted small" style="margin-top:12px">提示：在侧栏右键分类可以重命名 / 移动 / 删除。</p>
    `);
    const parentSel = $("#cat-parent");
    if (!isNew) {
      const all = await api("/api/categories");
      const find = (nodes) => {
        for (const n of nodes) {
          if (String(n.id) === String(catId)) return n;
          const hit = find(n.children || []);
          if (hit) return hit;
        }
        return null;
      };
      const self = find(all);
      if (self && self.parent_id) parentSel.value = String(self.parent_id);
    } else if (presetParent) {
      parentSel.value = String(presetParent);
    }

    $("#cat-save").addEventListener("click", async () => {
      const payload = {
        name: $("#cat-name").value.trim(),
        parent_id: parentSel.value ? Number(parentSel.value) : null,
      };
      if (!payload.name) return toast("分类名不能为空", "error");
      try {
        if (isNew) await api("/api/categories", { method: "POST", body: payload });
        else await api(`/api/categories/${catId}`, { method: "PATCH", body: payload });
        location.reload();
      } catch (err) { toast(err.message, "error"); }
    });

    const del = $("#cat-del");
    if (del) {
      del.addEventListener("click", () => {
        closeModal();
        deleteCategoryModal(catId, $("#cat-name").value.trim() || name);
      });
    }
  }

  // ---------- 书签编辑 ----------

  async function openBookmarkModal(id) {
    let bm = { url: "", url_lan: "", title: "", description: "", tags: [], category_id: null, is_private: false };
    if (id) {
      try { bm = await api(`/api/bookmarks/${id}`); }
      catch (err) { return toast(err.message, "error"); }
    }
    const opts = await categoryOptions(bm.category_id);
    openModal(`
      <h2>${id ? "编辑书签" : "新增书签"}</h2>
      <div class="form-row"><label>网址</label>
        <input id="bm-url" value="${esc(bm.url)}" placeholder="https://example.com 或 example.com">
        <small class="form-hint">外网地址；同一个服务在内网直连时填到下面的「内网地址」。改了这里会自动重新抓标题和图标。</small></div>
      <div class="form-row"><label>内网地址（可选）</label>
        <input id="bm-url-lan" value="${esc(bm.url_lan || "")}" placeholder="http://nas.example.test:8080">
        <small class="form-hint">填了之后，浏览器能直连内网时会自动改走这个地址；留空就只走上面那个。</small></div>
      <div class="form-row"><label>标题（留空自动抓取）</label>
        <input id="bm-title" value="${esc(bm.title || "")}"></div>
      <div class="form-row"><label>描述</label>
        <textarea id="bm-desc" rows="2">${esc(bm.description || "")}</textarea></div>
      <div class="form-row"><label>分类</label>
        <select id="bm-cat"><option value="">（未分类）</option>${opts}</select></div>
      <div class="form-row"><label>标签（逗号分隔）</label>
        <input id="bm-tags" value="${esc((bm.tags || []).map((t) => t.name).join(", "))}"></div>
      <label class="check-row" for="bm-private">
        <input type="checkbox" id="bm-private" ${bm.is_private ? "checked" : ""}>
        <span><b>私有</b><small>勾选后未登录的访客看不到这条书签</small></span>
      </label>
      <div class="form-actions">
        ${id ? '<button class="btn danger" id="bm-del">删除</button>' : ""}
        <button class="btn" data-close>取消</button>
        <button class="btn primary" id="bm-save">保存</button>
      </div>
    `);
    if ($("#bm-cat")) $("#bm-cat").value = bm.category_id ? String(bm.category_id) : "";

    const save = $("#bm-save");
    save.addEventListener("click", async () => {
      const payload = {
        url: $("#bm-url").value.trim(),
        title: $("#bm-title").value.trim() || null,
        description: $("#bm-desc").value.trim() || null,
        category_id: $("#bm-cat").value ? Number($("#bm-cat").value) : null,
        tags: $("#bm-tags").value.split(",").map((s) => s.trim()).filter(Boolean),
        is_private: $("#bm-private").checked,
        url_lan: $("#bm-url-lan").value.trim() || null,
      };
      try {
        if (id) {
          await api(`/api/bookmarks/${id}`, { method: "PATCH", body: payload });
        } else {
          payload.url = $("#bm-url").value.trim();
          if (!payload.url) return toast("网址不能为空", "error");
          payload.fetch = true;
          save.disabled = true;
          save.textContent = "抓取中…";
          await api("/api/bookmarks", { method: "POST", body: payload });
        }
        location.reload();
      } catch (err) {
        toast(err.message, "error");
        save.disabled = false;
        save.textContent = "保存";
      }
    });

    const del = $("#bm-del");
    if (del) {
      del.addEventListener("click", () => {
        const card = document.querySelector(`.card[data-id="${id}"]`);
        deleteBookmark(id, $("#bm-title").value.trim() || bm.title, card);
      });
    }
  }

  // 卡片菜单：右键任意位置，或点卡片右下角的 ⋯。菜单本身就是键盘可达的。
  function initCardMenu() {
    const grid = $("#grid");
    if (!grid) return;

    let lastX = 0, lastY = 0;
    grid.addEventListener("pointerdown", (e) => { lastX = e.clientX; lastY = e.clientY; });

    grid.addEventListener("contextmenu", (e) => {
      const card = e.target.closest(".card");
      if (!card) return;
      e.preventDefault();
      openMenu(cardMenuItems(card), e.clientX, e.clientY);
    });

    grid.addEventListener("click", (e) => {
      const more = e.target.closest(".more");
      if (!more) return;
      e.preventDefault();
      e.stopPropagation();
      const card = more.closest(".card");
      if (!card) return;
      // 键盘触发时坐标是 0：退回按钮自己的位置，别把菜单甩到屏幕左上角
      const r = more.getBoundingClientRect();
      const x = lastX && lastX > r.left - r.width * 4 ? lastX : r.left;
      const y = lastY && lastY > r.top - r.height * 8 ? lastY : r.bottom;
      openMenu(cardMenuItems(card), x, y, card.dataset.id);
    });

    // 右键菜单开着的时候，键盘快捷键跟着走
    document.addEventListener("keydown", (e) => {
      if (!menuEl) return;
      const card = document.querySelector(`.card[data-id="${menuEl.dataset.opener}"]`);
      if (!card) return;
      const url = effectiveHref(card.querySelector(".card-link"));
      if (e.key === "Enter") { e.preventDefault(); closeMenu(); window.open(url, "_blank", "noopener"); }
      else if (e.key.toLowerCase() === "c" && !e.metaKey && !e.ctrlKey) {
        e.preventDefault(); closeMenu(); copyText(url);
      } else if (e.key.toLowerCase() === "e") {
        e.preventDefault(); closeMenu(); openBookmarkModal(Number(card.dataset.id));
      }
    });
  }

  // ---------- 拖拽排序 ----------

  function initDrag() {
    const grid = $("#grid");
    if (!grid) return;
    let dragged = null;

    grid.addEventListener("dragstart", (e) => {
      const card = e.target.closest(".card");
      if (!card) return;
      dragged = card;
      card.classList.add("dragging");
      e.dataTransfer.effectAllowed = "move";
    });

    grid.addEventListener("dragend", () => {
      if (dragged) dragged.classList.remove("dragging");
      $$(".drop-target", grid).forEach((c) => c.classList.remove("drop-target"));
      dragged = null;
    });

    grid.addEventListener("dragover", (e) => {
      e.preventDefault();
      const over = e.target.closest(".card");
      if (!over || over === dragged || !dragged) return;
      $$(".drop-target", grid).forEach((c) => c.classList.remove("drop-target"));
      over.classList.add("drop-target");
      const rect = over.getBoundingClientRect();
      const after = e.clientX > rect.left + rect.width / 2;
      const at = after ? over.nextSibling : over;
      // 拖到原地就什么都别做，不然每帧都重排一次 DOM
      if (at === dragged || (dragged.nextSibling === over && !after)) return;
      flip(grid, () => grid.insertBefore(dragged, at), dragged);
    });

    grid.addEventListener("drop", async (e) => {
      e.preventDefault();
      const ids = $$(".card", grid).map((c) => Number(c.dataset.id));
      try {
        await api("/api/bookmarks/reorder", { method: "PUT", body: { ordered_ids: ids } });
        toast("顺序已保存", "ok");
      } catch (err) { toast(err.message, "error"); }
    });
  }

  // ---------- 管理页批量操作 ----------

  function initBulk() {
    const table = $("#bm-table");
    if (!table) return;
    const bar = $("#bulkbar");
    const count = $("#bulk-count");
    const selected = () => $$(".row-check:checked", table);

    function refresh() {
      const n = selected().length;
      bar.hidden = n === 0;
      count.textContent = n;
      $$("tbody tr", table).forEach((tr) => {
        tr.classList.toggle("selected", tr.querySelector(".row-check").checked);
      });
    }

    $("#check-all")?.addEventListener("change", (e) => {
      $$(".row-check", table).forEach((c) => { c.checked = e.target.checked; });
      refresh();
    });
    table.addEventListener("change", (e) => { if (e.target.classList.contains("row-check")) refresh(); });

    async function runBulk(ids, payload, okMsg) {
      try {
        const r = await api("/api/bookmarks/bulk", { method: "POST", body: { ids, ...payload } });
        toast(okMsg(r), "ok");
        setTimeout(() => location.reload(), 700);
      } catch (err) { toast(err.message, "error"); }
    }

    $$("[data-bulk]", bar).forEach((btn) => {
      btn.addEventListener("click", () => {
        const ids = selected().map((c) => Number(c.value));
        if (!ids.length) return;
        const action = btn.dataset.bulk;

        if (action === "delete") {
          confirmModal({
            title: `删除选中的 ${ids.length} 个书签？`,
            body: "删掉之后 10 分钟内，可以在提示条上点「撤销」找回其中任意一条。",
            ok: "删除",
            danger: true,
            onOk: () => runBulk(ids, { action }, (r) => `已删除 ${r.affected} 个`),
          });
        } else if (action === "move") {
          moveModal(ids);
        } else if (action === "tag_add" || action === "tag_remove") {
          tagModal(ids, action);
        } else {
          // private / public / lan_clear 是即时切换，不用问
          const label = { private: "已设为私有", public: "已设为公开", lan_clear: "已清掉内网地址" }[action]
            || "已处理";
          runBulk(ids, { action }, (r) => `${label}（${r.affected} 条）`);
        }
      });
    });

    $("#btn-refresh")?.addEventListener("click", async () => {
      const ids = selected().map((c) => Number(c.value));
      const btn = $("#btn-refresh");
      btn.disabled = true;
      btn.textContent = "抓取中…";
      try {
        const r = await api("/api/bookmarks/refresh", {
          method: "POST",
          body: { ids, check_status: true },
        });
        toast(`检查 ${r.checked} 条，更新标题 ${r.updated} 条，补到图标 ${r.icons_found} 个`, "ok");
        location.reload();
      } catch (err) {
        toast(err.message, "error");
        btn.disabled = false;
        btn.textContent = "刷新元数据";
      }
    });
  }

  // ---------- 导入页 ----------

  function initImport() {
    const ta = $("#import-text");
    if (!ta) return;
    let previewData = null;

    (async () => {
      const tree = await api("/api/categories");
      const opts = [];
      const walk = (nodes, depth) => nodes.forEach((n) => {
        opts.push(`<option value="${n.id}">${"　".repeat(depth)}${esc(n.name)}</option>`);
        walk(n.children || [], depth + 1);
      });
      walk(tree, 0);
      $("#opt-category").insertAdjacentHTML("beforeend", opts.join(""));
    })().catch(() => {});

    function payload(extra = {}) {
      return {
        text: ta.value,
        filename: $("#file-name").dataset.name || "",
        category_id: $("#opt-category").value ? Number($("#opt-category").value) : null,
        use_tree: $("#opt-tree").checked,
        tags: $("#opt-tags").value.split(",").map((s) => s.trim()).filter(Boolean),
        fetch: $("#opt-fetch").checked,
        skip_existing: $("#opt-skip").checked,
        ...extra,
      };
    }

    async function loadFile(file) {
      ta.value = await file.text();
      $("#file-name").textContent = file.name;
      $("#file-name").dataset.name = file.name;
      $("#btn-preview").click();
    }

    $("#import-file").addEventListener("change", (e) => {
      if (e.target.files[0]) loadFile(e.target.files[0]);
    });

    const drop = $("#drop");
    ["dragenter", "dragover"].forEach((ev) =>
      drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("dragover"); }));
    ["dragleave", "drop"].forEach((ev) =>
      drop.addEventListener(ev, () => drop.classList.remove("dragover")));
    drop.addEventListener("drop", (e) => {
      e.preventDefault();
      const f = e.dataTransfer.files[0];
      if (f) loadFile(f);
    });

    $("#btn-clear").addEventListener("click", () => {
      ta.value = "";
      $("#file-name").textContent = "";
      delete $("#file-name").dataset.name;
      $("#preview-panel").hidden = true;
      $("#btn-run").disabled = true;
    });

    $("#btn-preview").addEventListener("click", async () => {
      if (!ta.value.trim()) return toast("先粘点内容进来", "error");
      try {
        const r = await api("/api/import/preview", {
          method: "POST",
          body: { text: ta.value, filename: $("#file-name").dataset.name || "" },
        });
        previewData = r;
        $("#preview-panel").hidden = false;
        $("#preview-stat").innerHTML =
          `格式 <b>${esc(r.format)}</b> · 共 <b>${r.total}</b> · 可导入 <b>${r.valid}</b> · ` +
          `已存在 <b>${r.duplicates}</b> · 无效 <b>${r.invalid}</b>` +
          (r.categories.length ? ` · 将新建分类 <b>${r.categories.length}</b> 个` : "");
        $("#preview-body").innerHTML = r.items.map((it) => `
          <tr>
            <td class="cell-url">${esc(it.url)}</td>
            <td class="cell-title">${esc(it.title || "—")}</td>
            <td class="muted">${esc((it.path || []).join(" / ") || "—")}</td>
            <td>${it.valid === false
              ? `<span class="badge failed">无效</span> <span class="muted small">${esc(it.reason || "")}</span>`
              : (it.duplicate ? '<span class="badge cancelled">已存在</span>' : '<span class="badge done">待导入</span>')}</td>
          </tr>`).join("");
        $("#btn-run").disabled = r.valid === 0;
      } catch (err) { toast(err.message, "error"); }
    });

    $("#btn-run").addEventListener("click", async () => {
      const btn = $("#btn-run");
      btn.disabled = true;
      btn.textContent = "提交中…";
      try {
        const r = await api("/api/import/run", { method: "POST", body: payload() });
        toast(`任务 #${r.job_id} 已开始，共 ${r.total} 条`, "ok");
        $("#progress-panel").hidden = false;
        poll(r.job_id);
      } catch (err) {
        toast(err.message, "error");
        btn.disabled = false;
        btn.textContent = "开始导入";
      }
    });

    function poll(jobId) {
      const tick = async () => {
        try {
          const j = await api(`/api/import/jobs/${jobId}`);
          const pct = j.total ? Math.round((j.processed / j.total) * 100) : 0;
          $("#prog-fill").style.width = `${pct}%`;
          $("#prog-status").textContent = `${j.status} · ${pct}%`;
          $("#prog-stat").innerHTML =
            `总数 <b>${j.total}</b> · 已处理 <b>${j.processed}</b> · 成功 <b>${j.success}</b> · ` +
            `重复 <b>${j.duplicate}</b> · 失败 <b>${j.failed}</b>`;
          if (j.error_log) {
            $("#prog-errors").hidden = false;
            $("#prog-error-log").textContent = j.error_log;
          }
          if (["done", "failed", "cancelled"].includes(j.status)) {
            toast(`导入${j.status === "done" ? "完成" : j.status === "cancelled" ? "已取消" : "失败"}：成功 ${j.success} / 重复 ${j.duplicate} / 失败 ${j.failed}`,
              j.status === "done" ? "ok" : "error");
            const btn = $("#btn-run");
            btn.disabled = false;
            btn.textContent = "开始导入";
            setTimeout(() => location.reload(), 1600);
            return;
          }
          setTimeout(tick, 900);
        } catch (err) {
          toast(`进度获取失败：${err.message}`, "error");
        }
      };
      tick();
    }
  }

  // ---------- 备份页 ----------

  function initBackup() {
    const body = $("#backup-body");
    if (!body) return;

    $("#btn-backup")?.addEventListener("click", async (e) => {
      const btn = e.target;
      btn.disabled = true;
      btn.textContent = "打包中…";
      try {
        const r = await api("/api/backup", { method: "POST", body: { note: "手动备份" } });
        toast(`备份完成 ${r.name}（${(r.size / 1024).toFixed(1)} KB）`, "ok");
        location.reload();
      } catch (err) {
        toast(err.message, "error");
        btn.disabled = false;
        btn.textContent = "立即备份";
      }
    });

    body.addEventListener("click", (e) => {
      const restore = e.target.dataset.restore;
      const del = e.target.dataset.delete;

      if (restore) {
        confirmModal({
          title: `用 ${restore} 覆盖当前数据？`,
          body: "当前数据库会先另存一份，出问题还能换回来。",
          ok: "恢复",
          danger: true,
          onOk: async () => {
            try {
              const r = await api(`/api/backup/${restore}/restore`, { method: "POST" });
              closeModal();
              toast(`恢复完成：${r.icons_restored} 个图标，安全副本 ${r.safety_copy}`, "ok");
              setTimeout(() => location.reload(), 1200);
            } catch (err) { toast(err.message, "error"); }
          },
        });
        return;
      }
      if (del) {
        confirmModal({
          title: `删除备份 ${del}？`,
          body: "压缩包会从磁盘上删掉，这一步没法撤销。",
          ok: "删除",
          danger: true,
          onOk: async () => {
            try {
              await api(`/api/backup/${del}`, { method: "DELETE" });
              closeModal();
              const tr = e.target.closest("tr");
              if (tr) { tr.style.transition = `opacity ${DUR[2]}ms`; tr.style.opacity = "0"; setTimeout(() => tr.remove(), DUR[2]); }
              toast("已删除", "ok");
            } catch (err) { toast(err.message, "error"); }
          },
        });
      }
    });

    $("#json-file")?.addEventListener("change", async (e) => {
      const f = e.target.files[0];
      if (!f) return;
      const fd = new FormData();
      fd.append("file", f);
      try {
        const r = await api("/api/import/json", { method: "POST", body: fd, raw: true });
        toast(`导入完成：分类 ${r.categories}，书签 ${r.bookmarks}，跳过 ${r.skipped}`, "ok");
        setTimeout(() => location.reload(), 1500);
      } catch (err) { toast(err.message, "error"); }
    });

    $("#zip-file")?.addEventListener("change", async (e) => {
      const f = e.target.files[0];
      if (!f) return;
      const fd = new FormData();
      fd.append("file", f);
      try {
        const r = await api("/api/backup/upload", { method: "POST", body: fd, raw: true });
        toast(`已上传 ${r.saved}，可在列表中恢复`, "ok");
        setTimeout(() => location.reload(), 1200);
      } catch (err) { toast(err.message, "error"); }
    });
  }

  // ---------- 账号 ----------

  function initAccount() {
    const btn = $("#btn-passwd");
    if (!btn) return;
    btn.addEventListener("click", () => {
      openModal(`
        <h2>修改密码</h2>
        <div class="form-row">
          <label for="pw-old">原密码</label>
          <input id="pw-old" type="password" autocomplete="current-password">
        </div>
        <div class="form-row">
          <label for="pw-new">新密码（至少 8 位）</label>
          <input id="pw-new" type="password" autocomplete="new-password">
        </div>
        <div class="form-row">
          <label for="pw-new2">再输一次新密码</label>
          <input id="pw-new2" type="password" autocomplete="new-password">
        </div>
        <div class="form-actions">
          <button class="btn" data-close>取消</button>
          <button class="btn primary" id="pw-save">保存</button>
        </div>`);
      $("#pw-save").addEventListener("click", async () => {
        const oldPw = $("#pw-old").value;
        const newPw = $("#pw-new").value;
        if (newPw !== $("#pw-new2").value) return toast("两次输入的新密码不一致", "error");
        if (newPw.length < 8) return toast("新密码至少 8 位", "error");
        try {
          await api("/api/auth/password", { method: "POST", body: { old_password: oldPw, new_password: newPw } });
          closeModal();
          // 后端把当前 cookie 作废了，只能用新密码重登
          toast("密码已修改，请用新密码重新登录", "ok");
          setTimeout(() => { location.href = "/login"; }, 1400);
        } catch (err) { toast(err.message, "error"); }
      });
      $("#pw-new2").addEventListener("keydown", (e) => {
        if (e.key === "Enter") $("#pw-save").click();
      });
    });
  }

  // ---------- 侧栏（手机折叠分类） ----------

  function initSideToggle() {
    const btn = $("#side-toggle");
    if (!btn) return;
    const side = btn.closest(".sidebar");
    btn.addEventListener("click", () => {
      const open = side.classList.toggle("is-open");
      btn.setAttribute("aria-expanded", String(open));
    });
  }

  // ---------- PWA ----------

  function initPwa() {
    // 注册个不缓存的 SW，只为满足"可安装"条件；失败就算了，不影响使用
    if ("serviceWorker" in navigator) {
      navigator.serviceWorker.register("/sw.js").catch(() => {});
    }
  }

  // ---------- 命令面板（⌘K） ----------

  const PAL_KIND = { cmd: "命令", bm: "书签", cat: "分类", tag: "标签" };
  let palData = null;   // 书签列表懒加载，一个页面只取一回
  let paletteItems = [];
  let palRows = [];
  let palIndex = 0;

  /* 侧栏和卡片上已经是全部数据了，直接读 DOM，省一次请求 */
  function paletteStatic() {
    const items = [];

    if ($("#btn-new")) {
      items.push({ kind: "cmd", label: "新增书签", hint: "N", run: () => openBookmarkModal(null) });
    }
    if ($(".nav")) {
      const here = location.pathname;
      for (const [label, href] of [["导航", "/"], ["管理", "/manage"],
                                   ["导入", "/import"], ["备份", "/backup"]]) {
        if (href === here) continue;
        items.push({ kind: "cmd", label: `去${label}`, run: () => { location.href = href; } });
      }
    }
    items.push({ kind: "cmd", label: "全部书签", run: () => { location.href = "/?category_id=all"; } });
    items.push({ kind: "cmd", label: "未分类书签", run: () => { location.href = "/?category_id=none"; } });
    items.push({
      kind: "cmd", label: "切换深浅色", hint: "⌘⇧L",
      run: () => toggleTheme($("#theme-toggle") || $("#palette")),
    });
    // 配色也能从命令面板换：手不用离开键盘。已选的那套不重复列出来。
    const accNow = accentNow();
    ACCENTS.filter((a) => a !== accNow).forEach((a) => {
      items.push({
        kind: "cmd", label: `配色换成${ACC_NAME[a]}`, sub: `当前：${ACC_NAME[accNow]}`,
        run: () => setAccent(a, $(".acc-dot[data-acc=" + a + "]") || $("#palette")),
      });
    });

    $$("#tree .tree-node").forEach((n) => {
      const row = n.querySelector(".tree-row");
      const name = row?.dataset.name;
      if (!name) return;
      // 组标题已经写着「分类」了，副标题再重复一遍没用，换成个数
      const c = row.querySelector(".count")?.textContent?.trim();
      items.push({
        kind: "cat", label: name, sub: c ? `${c} 个书签` : "",
        run: () => { location.href = row.href; },
      });
    });
    $$(".tags .tag").forEach((a) => {
      const name = a.dataset.name;
      if (!name) return;
      const c = a.querySelector("span")?.textContent?.trim();
      items.push({
        kind: "tag", label: name, sub: c ? `${c} 个书签` : "",
        run: () => { location.href = a.href; },
      });
    });

    $$("#grid .card").forEach((c) => {
      items.push({
        kind: "bm", label: c.dataset.title || c.dataset.host, sub: c.dataset.host,
        run: () => window.open(effectiveHref(c.querySelector(".card-link")), "_blank", "noopener"),
      });
    });
    return items;
  }

  async function paletteData() {
    const base = paletteStatic();
    if (palData || !$(".nav")) return base.concat(palData || []);
    try {
      // 带上全部书签，不然在备份页按 ⌘K 只能搜到导航项
      const r = await api("/api/bookmarks?limit=2000&category_id=all");
      palData = r.items.map((b) => ({
        kind: "bm", label: b.title || b.domain, sub: b.domain,
        run: () => window.open(b.url_lan && lanCached(b.url_lan) === true ? b.url_lan : b.url, "_blank", "noopener"),
      }));
    } catch { palData = []; }
    return base.concat(palData);
  }

  function palScore(it, q) {
    const label = it.label.toLowerCase();
    const sub = (it.sub || "").toLowerCase();
    if (label.startsWith(q)) return 0;
    const i = label.indexOf(q);
    if (i >= 0) return 1 + i / 100;
    if (sub.includes(q)) return 3;
    return -1;
  }

  function palRender() {
    const list = $("#palette-list");
    const q = $("#palette-input").value.trim().toLowerCase();
    const all = paletteItems;

    let hits = all;
    if (q) {
      hits = all
        .map((it) => ({ it, s: palScore(it, q) }))
        .filter((x) => x.s >= 0)
        .sort((a, b) => a.s - b.s || a.it.label.length - b.it.label.length)
        .map((x) => x.it);
    }
    // 不搜索的时候也按类型排一下，命令永远在最上面
    const order = { cmd: 0, cat: 1, tag: 2, bm: 3 };
    // 没输字时别把上百条书签全铺出来：每组留个头，剩下的让用户打字去筛
    const CAP = { cmd: Infinity, cat: 6, tag: 6, bm: 6 };
    let truncated = 0;
    if (!q) {
      const seen = {};
      hits = hits
        .slice()
        .sort((a, b) => order[a.kind] - order[b.kind])
        .filter((it) => {
          seen[it.kind] = (seen[it.kind] || 0) + 1;
          if (seen[it.kind] > CAP[it.kind]) { truncated++; return false; }
          return true;
        });
    }

    palRows = hits.slice(0, 40);
    if (palIndex >= palRows.length) palIndex = 0;

    if (!palRows.length) {
      list.innerHTML = `<div class="pal-empty">没有匹配「${esc(q)}」的条目</div>`;
      return;
    }
    let html = "", lastKind = "";
    palRows.forEach((it, i) => {
      if (it.kind !== lastKind) {
        html += `<div class="palette-group">${PAL_KIND[it.kind]}</div>`;
        lastKind = it.kind;
      }
      html += `<button class="pal-item${i === palIndex ? " active" : ""}" role="option"
                 aria-selected="${i === palIndex}" data-i="${i}" id="pal-${i}">
                 <span class="pal-label">${esc(it.label)}</span>
                 ${it.sub ? `<span class="pal-sub">${esc(it.sub)}</span>` : ""}
                 ${it.hint ? `<kbd>${esc(it.hint)}</kbd>` : ""}
               </button>`;
    });
    if (truncated) html += `<div class="pal-more">还有 ${truncated} 项，输入关键字继续筛…</div>`;
    list.innerHTML = html;
    list.scrollTop = 0;
    $("#palette-input").setAttribute("aria-activedescendant", `pal-${palIndex}`);
  }

  function palMove(d) {
    if (!palRows.length) return;
    palIndex = (palIndex + d + palRows.length) % palRows.length;
    $$(".pal-item", $("#palette-list")).forEach((el, i) => {
      const on = i === palIndex;
      el.classList.toggle("active", on);
      el.setAttribute("aria-selected", String(on));
      if (on) el.scrollIntoView({ block: "nearest" });
    });
    $("#palette-input").setAttribute("aria-activedescendant", `pal-${palIndex}`);
  }

  let palOpen = false;
  let palOpener = null;   // 打开面板的那个元素，关掉时把焦点还给他

  function closePalette() {
    const pal = $("#palette");
    if (!pal || !palOpen) return;
    palOpen = false;
    pal.hidden = true;
    lockScroll(false);
    // 键盘用户是从某个按钮过来的，关掉之后得把他放回原处，不然焦点掉到 body 上
    if (palOpener && palOpener.isConnected && !openOverlay()) {
      palOpener.focus({ preventScroll: true });
    }
    palOpener = null;
  }

  async function openPalette() {
    const pal = $("#palette");
    if (!pal || palOpen) return;
    palOpen = true;
    palOpener = document.activeElement;
    pal.hidden = false;
    lockScroll(true);
    const input = $("#palette-input");
    input.value = "";
    input.focus({ preventScroll: true });
    palIndex = 0;
    paletteItems = await paletteData();
    if (palOpen) palRender();
  }

  function initPalette() {
    const pal = $("#palette");
    if (!pal) return;
    $("#btn-palette")?.addEventListener("click", openPalette);
    pal.addEventListener("click", (e) => {
      if (isEl(e.target) && e.target.closest("[data-pal-close]")) closePalette();
    });

    const input = $("#palette-input");
    input.addEventListener("input", () => { palIndex = 0; palRender(); });
    input.addEventListener("keydown", (e) => {
      if (e.key === "ArrowDown") { e.preventDefault(); palMove(1); }
      else if (e.key === "ArrowUp") { e.preventDefault(); palMove(-1); }
      else if (e.key === "Enter") {
        e.preventDefault();
        const it = palRows[palIndex];
        if (!it) return;
        closePalette();
        it.run();
      }
    });

    // 鼠标滑过也跟着高亮，手感跟键盘一致
    $("#palette-list").addEventListener("pointermove", (e) => {
      const item = isEl(e.target) ? e.target.closest(".pal-item") : null;
      if (!item) return;
      const i = Number(item.dataset.i);
      if (i === palIndex) return;
      palIndex = i;
      $$(".pal-item", $("#palette-list")).forEach((el, j) => {
        el.classList.toggle("active", j === i);
        el.setAttribute("aria-selected", String(j === i));
      });
    });
    $("#palette-list").addEventListener("click", (e) => {
      const item = isEl(e.target) ? e.target.closest(".pal-item") : null;
      if (!item) return;
      const it = palRows[Number(item.dataset.i)];
      if (!it) return;
      closePalette();
      it.run();
    });

    document.addEventListener("keydown", (e) => {
      const k = e.key.toLowerCase();
      const mod = e.metaKey || e.ctrlKey;
      if (mod && k === "k") { e.preventDefault(); palOpen ? closePalette() : openPalette(); return; }
      if (mod && e.shiftKey && k === "l") { e.preventDefault(); toggleTheme($("#theme-toggle") || pal); return; }
      // 输入框里打字时别抢按键
      const t = e.target;
      if (isEl(t) && (t.closest("input, textarea, select") || t.isContentEditable)) return;
      if (openOverlay() || menuEl) return;
      if (k === "/") {
        e.preventDefault();
        // 页面上摆着搜索框就先给它：看得到的东西比藏在 ⌘K 里的更该先响应
        const box = $('.search input[name="q"]');
        if (box) { box.focus({ preventScroll: true }); box.select(); } else openPalette();
      }
      else if (k === "n" && $("#btn-new")) { e.preventDefault(); openBookmarkModal(null); }
      else if (k === "t") { e.preventDefault(); toggleTheme($("#theme-toggle") || pal); }
    });
  }

  // ---------- 即时筛选 ----------

  /* 回车提交的搜索太慢：卡片都在页面上，直接在本地过滤，顺便让 FLIP 把补位演出来 */
  function initInstantFilter() {
    const form = $(".search");
    const grid = $("#grid");
    if (!form || !grid) return;
    const input = $("input[name=q]", form);
    const cards = $$(".card", grid);
    let timer = 0;

    input.addEventListener("input", () => {
      clearTimeout(timer);
      timer = setTimeout(() => {
        const q = input.value.trim().toLowerCase();
        let shown = 0;
        flip(grid, () => {
          cards.forEach((c) => {
            const hay = `${c.dataset.title || ""} ${c.dataset.host || ""}`.toLowerCase();
            const hit = !q || hay.includes(q);
            c.classList.toggle("is-filtered-out", !hit);
            if (hit) shown++;
          });
        });
        form.classList.toggle("is-filtering", Boolean(q));
        $$(".empty.is-instant").forEach((n) => n.remove());
        if (q && !shown) {
          const box = document.createElement("div");
          box.className = "empty is-instant";
          box.innerHTML = `<p class="empty-title">本页没有匹配「${esc(input.value.trim())}」的书签</p>
            <p class="muted">换个词试试，或者按回车到服务端搜全部书签。</p>`;
          grid.after(box);
        }
      }, 90);
    });

    // 按 Esc 清空，回到全部
    input.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && input.value) {
        e.stopPropagation();
        input.value = "";
        input.dispatchEvent(new Event("input"));
      }
    });
  }

  // ---------- 启动 ----------

  document.addEventListener("DOMContentLoaded", () => {
    initPwa();
    initTheme();
    initAccent();
    initFavicons();
    // 工作台使用静态表面，避免装饰性动画干扰阅读与点击。
    initSideToggle();
    initTree();
    initCategoryActions();
    initCardMenu();
    initLan();
    initDrag();
    // 搜索与快速筛选由 studio.js 统一处理。
    initPalette();
    initBulk();
    initImport();
    initBackup();
    initAccount();
    $$("#btn-new").forEach((b) => b.addEventListener("click", () => openBookmarkModal(null)));
    document.addEventListener("click", (e) => {
      const edit = e.target.closest(".edit");
      if (edit) openBookmarkModal(Number(edit.dataset.id));
    });
  });

  window.__bm = { api, toast, openBookmarkModal, openPalette, flip, REDUCED };
})();
