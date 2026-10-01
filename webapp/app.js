/* BrewBook Mini App. Plain JS, no build step.
   Inside Telegram every API call carries the signed initData, which the
   server validates. In a plain browser the app falls back to a guest
   "demo" session, which the server accepts only when BROWSER_DEMO is on. */
(() => {
  "use strict";

  const tg = window.Telegram && window.Telegram.WebApp;
  const IN_TG = !!(tg && tg.initData);
  const $ = (id) => document.getElementById(id);

  // ---------- state ----------
  const S = {
    boot: null,             // /api/bootstrap payload
    items: new Map(),
    cart: new Map(),        // key -> {item_id, size, milk, qty}
    view: "loading",
    pay: IN_TG ? "stars" : "demo",
    order: null,            // order being paid or tracked
    orderKey: null,         // cart signature the pending order was made from
    sheet: null,            // {item, size, milk, qty}
    poll: null,
  };

  // ---------- API ----------
  function visitorId() {
    const fromUrl = new URLSearchParams(location.search).get("demo_visitor");
    if (fromUrl) return fromUrl;
    try {
      let v = localStorage.getItem("brewbook_visitor");
      if (!v) {
        v = "v" + Array.from(crypto.getRandomValues(new Uint8Array(12)), (b) => b.toString(16).padStart(2, "0")).join("");
        localStorage.setItem("brewbook_visitor", v);
      }
      return v;
    } catch (e) {
      return (S._v ||= "v" + Math.random().toString(16).slice(2).padEnd(12, "0"));
    }
  }
  const AUTH = IN_TG ? `tma ${tg.initData}` : `demo ${visitorId()}`;

  async function api(path, opts = {}) {
    const res = await fetch(path, {
      method: opts.method || "GET",
      headers: { Authorization: AUTH, ...(opts.body ? { "Content-Type": "application/json" } : {}) },
      body: opts.body ? JSON.stringify(opts.body) : undefined,
    });
    let data = null;
    try { data = await res.json(); } catch (e) { /* empty body */ }
    if (!res.ok) {
      const err = new Error((data && data.detail) || `Request failed (${res.status})`);
      err.status = res.status;
      throw err;
    }
    return data;
  }

  // ---------- helpers ----------
  const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  function money(cents) {
    const cur = (S.boot && S.boot.currency) || "USD";
    const sym = { USD: "$", EUR: "€" }[cur];
    const v = (cents / 100).toFixed(2);
    return sym ? sym + v : `${v} ${cur}`;
  }
  const toStars = (cents) => Math.max(1, Math.ceil(cents * S.boot.stars_per_usd / 100 - 1e-9));
  const starIcon = '<svg class="star" viewBox="0 0 24 24" aria-hidden="true"><use href="#i-star"/></svg>';
  const tileHtml = (item) => `<div class="tile ${item.category}"><svg aria-hidden="true"><use href="#i-${item.icon}"/></svg></div>`;
  const sizeOf = (id) => S.boot.sizes.find((s) => s.id === id);
  const milkOf = (id) => S.boot.milks.find((m) => m.id === id);

  function unitCents(item, size, milk) {
    let c = item.price_cents;
    if (item.has_sizes) c += sizeOf(size).extra_cents;
    if (item.has_milk) c += milkOf(milk).extra_cents;
    return c;
  }
  function optsLabel(line) {
    const parts = [];
    if (line.size) parts.push(sizeOf(line.size).name);
    if (line.milk && line.milk !== "none") parts.push(milkOf(line.milk).name.toLowerCase());
    return parts.join(" · ");
  }
  const cartKey = (l) => `${l.item_id}|${l.size || ""}|${l.milk || ""}`;
  const cartCount = () => [...S.cart.values()].reduce((n, l) => n + l.qty, 0);
  const cartTotal = () => [...S.cart.values()].reduce((n, l) => n + l.qty * unitCents(S.items.get(l.item_id), l.size, l.milk), 0);
  const cartSignature = () => JSON.stringify([...S.cart.values()].map((l) => [cartKey(l), l.qty]).sort()) + "|" + $("comment").value.trim();

  function haptic(kind = "light") {
    try { if (IN_TG) kind === "success" || kind === "error" ? tg.HapticFeedback.notificationOccurred(kind) : tg.HapticFeedback.impactOccurred(kind); } catch (e) { /* old client */ }
  }
  let toastTimer;
  function toast(msg) {
    const t = $("toast");
    t.textContent = msg;
    t.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => (t.hidden = true), 3200);
  }

  // ---------- theme ----------
  function applyTheme() {
    const dark = IN_TG ? tg.colorScheme === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
    document.documentElement.dataset.theme = dark ? "dark" : "light";
    if (IN_TG) {
      const bg = getComputedStyle(document.documentElement).getPropertyValue("--bg").trim();
      try { tg.setHeaderColor(bg); tg.setBackgroundColor(bg); } catch (e) { /* old client */ }
      try { tg.setBottomBarColor(bg); } catch (e) { /* < 7.10 */ }
      mainButton.restyle();
    }
  }

  // ---------- Main / Back buttons (Telegram, or in-page fallback in a browser) ----------
  const mainButton = {
    handler: null,
    params: null,
    set(text, handler, opts = {}) {
      this.handler = handler;
      this.params = { text, active: opts.active !== false, progress: !!opts.progress };
      if (IN_TG) {
        const mb = tg.MainButton;
        this.restyle();
        mb.setParams({ text, is_active: this.params.active, is_visible: true });
        this.params.progress ? mb.showProgress(false) : mb.hideProgress();
      } else {
        const b = $("main-fallback-btn");
        b.textContent = opts.progress ? "…" : text;
        b.disabled = !this.params.active || this.params.progress;
        $("main-fallback").hidden = false;
      }
    },
    restyle() {
      if (!IN_TG) return;
      const cs = getComputedStyle(document.documentElement);
      tg.MainButton.setParams({ color: cs.getPropertyValue("--accent").trim(), text_color: cs.getPropertyValue("--on-accent").trim() });
    },
    progress(on) { if (this.params) this.set(this.params.text, this.handler, { ...this.params, progress: on }); },
    hide() {
      this.handler = null;
      this.params = null;
      if (IN_TG) tg.MainButton.hide();
      else $("main-fallback").hidden = true;
    },
    fire() { if (this.handler && this.params && this.params.active && !this.params.progress) this.handler(); },
  };
  if (IN_TG) tg.MainButton.onClick(() => mainButton.fire());
  $("main-fallback-btn").addEventListener("click", () => mainButton.fire());

  let backHandler = null;
  function setBack(fn) {
    backHandler = fn;
    if (IN_TG) fn ? tg.BackButton.show() : tg.BackButton.hide();
    else $("back").hidden = !fn;
  }
  if (IN_TG) tg.BackButton.onClick(() => backHandler && backHandler());
  $("back").addEventListener("click", () => backHandler && backHandler());

  // ---------- navigation ----------
  function show(view) {
    S.view = view;
    $("toast").hidden = true;
    document.querySelectorAll(".view").forEach((v) => (v.hidden = v.id !== `view-${view}`));
    $("orders-btn").hidden = !S.boot || view === "orders" || view === "blocked";
    if (view !== "order" && view !== "pay") stopPoll();
    window.scrollTo(0, 0);
    refreshButtons();
  }

  function refreshButtons() {
    if (S.sheet) {
      const { item, size, milk, qty } = S.sheet;
      mainButton.set(`Add to order · ${money(qty * unitCents(item, size, milk))}`, addFromSheet);
      setBack(closeSheet);
      return;
    }
    switch (S.view) {
      case "menu": {
        const n = cartCount();
        n ? mainButton.set(`View order · ${n} item${n > 1 ? "s" : ""} · ${money(cartTotal())}`, openCart) : mainButton.hide();
        setBack(null);
        break;
      }
      case "cart": {
        if (!cartCount()) mainButton.set("Your cart is empty", null, { active: false });
        else if (S.pay === "stars") mainButton.set(`Pay ${toStars(cartTotal())} ⭐`, checkout);
        else if (S.pay === "crypto") mainButton.set(`Pay ${money(cartTotal())} in crypto`, checkout);
        else mainButton.set(`Place order · ${money(cartTotal())}`, checkout);
        setBack(() => show("menu"));
        break;
      }
      case "pay":
        mainButton.hide();
        setBack(() => openCart());
        break;
      case "order":
        mainButton.set("Back to menu", () => show("menu"));
        setBack(() => show("menu"));
        break;
      case "orders":
        mainButton.hide();
        setBack(() => show("menu"));
        break;
      default:
        mainButton.hide();
        setBack(null);
    }
  }

  // ---------- menu ----------
  function renderMenu() {
    const b = S.boot;
    $("greeting").textContent = `Good ${partOfDay()}, ${b.user.first_name || "friend"}`;
    $("browser-note").hidden = !b.browser_demo;
    $("cats").innerHTML = b.categories.map((c, i) => `<button class="cat${i === 0 ? " is-active" : ""}" data-cat="${c.id}" type="button">${esc(c.name)}</button>`).join("");
    $("menu-list").innerHTML = b.categories.map((c) => {
      const items = b.items.filter((i) => i.category === c.id);
      return `<h2 class="section-title" id="cat-${c.id}">${esc(c.name)}</h2><ul class="items">${items.map(itemHtml).join("")}</ul>`;
    }).join("");
  }
  function itemHtml(item) {
    const n = [...S.cart.values()].filter((l) => l.item_id === item.id).reduce((a, l) => a + l.qty, 0);
    const hasOpts = item.has_sizes || item.has_milk;
    return `<li class="item" data-item="${item.id}">
      ${tileHtml(item)}
      <div>
        <div class="item-name">${esc(item.name)}</div>
        <div class="item-desc">${esc(item.description)}</div>
        <div class="item-price">${hasOpts && item.has_sizes ? '<span class="from">from </span>' : ""}${money(item.price_cents)}</div>
      </div>
      <div class="in-cart"><button class="add" type="button" data-add="${item.id}" aria-label="Add ${esc(item.name)}">+</button>${n ? `<span class="badge">${n}</span>` : ""}</div>
    </li>`;
  }
  function partOfDay() {
    const h = new Date().getHours();
    return h < 12 ? "morning" : h < 18 ? "afternoon" : "evening";
  }
  function rerenderItem(id) {
    const li = document.querySelector(`[data-item="${id}"]`);
    if (li) li.outerHTML = itemHtml(S.items.get(id));
  }

  $("menu-list").addEventListener("click", (e) => {
    const card = e.target.closest("[data-item]");
    if (!card) return;
    const item = S.items.get(card.dataset.item);
    if (item.has_sizes || item.has_milk) openSheet(item);
    else {
      addToCart({ item_id: item.id, size: null, milk: null, qty: 1 });
      toast(`${item.name} added`);
    }
  });
  $("cats").addEventListener("click", (e) => {
    const b = e.target.closest("[data-cat]");
    if (!b) return;
    document.querySelectorAll(".cat").forEach((c) => c.classList.toggle("is-active", c === b));
    $(`cat-${b.dataset.cat}`).scrollIntoView({ behavior: "smooth", block: "start" });
  });

  function addToCart(line) {
    const key = cartKey(line);
    const cur = S.cart.get(key);
    if (cur) cur.qty = Math.min(20, cur.qty + line.qty);
    else S.cart.set(key, { ...line });
    haptic("light");
    rerenderItem(line.item_id);
    refreshButtons();
  }

  // ---------- options sheet ----------
  function openSheet(item) {
    S.sheet = { item, size: item.has_sizes ? "M" : null, milk: item.has_milk ? item.default_milk : null, qty: 1 };
    $("sheet-tile").outerHTML = tileHtml(item).replace('class="tile', 'id="sheet-tile" class="tile');
    $("sheet-title").textContent = item.name;
    $("sheet-desc").textContent = item.description;
    renderSheet();
    $("sheet").hidden = false;
    refreshButtons();
  }
  function renderSheet() {
    const { item, size, milk, qty } = S.sheet;
    $("sheet-sizes").hidden = !item.has_sizes;
    $("sheet-sizes").innerHTML = `<span class="label">Size</span><div class="seg">${S.boot.sizes.map((s) =>
      `<button type="button" data-size="${s.id}" class="${s.id === size ? "is-active" : ""}">${esc(s.name)}<small>${money(item.price_cents + s.extra_cents)}</small></button>`).join("")}</div>`;
    $("sheet-milks").hidden = !item.has_milk;
    $("sheet-milks").innerHTML = `<span class="label">Milk</span><div class="chips">${S.boot.milks.map((m) =>
      `<button type="button" data-milk="${m.id}" class="${m.id === milk ? "is-active" : ""}">${esc(m.name)}${m.extra_cents ? ` <small>+${money(m.extra_cents)}</small>` : ""}</button>`).join("")}</div>`;
    $("sheet-qty").innerHTML = `<button type="button" data-q="-1" aria-label="Less">−</button><span>${qty}</span><button type="button" data-q="1" aria-label="More">+</button>`;
    refreshButtons();
  }
  $("sheet").addEventListener("click", (e) => {
    if (e.target.closest("[data-close]")) return closeSheet();
    const s = e.target.closest("[data-size]"), m = e.target.closest("[data-milk]"), q = e.target.closest("[data-q]");
    if (s) S.sheet.size = s.dataset.size;
    else if (m) S.sheet.milk = m.dataset.milk;
    else if (q) S.sheet.qty = Math.max(1, Math.min(20, S.sheet.qty + Number(q.dataset.q)));
    else return;
    haptic("light");
    renderSheet();
  });
  function closeSheet() {
    S.sheet = null;
    $("sheet").hidden = true;
    refreshButtons();
  }
  function addFromSheet() {
    const { item, size, milk, qty } = S.sheet;
    closeSheet();
    addToCart({ item_id: item.id, size, milk, qty });
    toast(`${item.name} added`);
  }

  // ---------- cart ----------
  function openCart() {
    renderCart();
    show("cart");
  }
  function renderCart() {
    const lines = [...S.cart.entries()];
    $("cart-list").innerHTML = lines.length ? lines.map(([key, l]) => {
      const item = S.items.get(l.item_id);
      return `<li class="cart-line">${tileHtml(item)}
        <div><div class="item-name">${esc(item.name)}</div>
          ${optsLabel(l) ? `<div class="opts">${esc(optsLabel(l))}</div>` : ""}
          <div class="price">${money(l.qty * unitCents(item, l.size, l.milk))}</div></div>
        <div class="stepper"><button type="button" data-key="${esc(key)}" data-d="-1" aria-label="Less">−</button><span>${l.qty}</span><button type="button" data-key="${esc(key)}" data-d="1" aria-label="More">+</button></div>
      </li>`;
    }).join("") : `<li class="empty">Nothing here yet. Pick something tasty from the menu.</li>`;
    const total = cartTotal();
    $("totals").innerHTML = `
      <div class="row grand"><span>Total</span><span>${money(total)}</span></div>
      ${S.boot.payments.stars ? `<div class="row muted small"><span>In Telegram Stars</span><span>${toStars(total)} ${starIcon}</span></div>` : ""}`;
    renderPayOptions(total);
    refreshButtons();
  }
  function renderPayOptions(total) {
    const p = S.boot.payments;
    const opts = [];
    if (p.stars) opts.push({ id: "stars", name: "Telegram Stars", sub: "Pay inside Telegram", amt: `${toStars(total)} ${starIcon}`, ic: '<svg viewBox="0 0 24 24" aria-hidden="true" style="fill:var(--star)"><use href="#i-star"/></svg>' });
    if (p.crypto || IN_TG) opts.push({
      id: "crypto", name: "Crypto", sub: p.crypto ? `CryptoBot ${p.crypto_network} · USDT, TON` : "CryptoBot unavailable · demo payment",
      amt: money(total), ic: '<svg viewBox="0 0 48 48" aria-hidden="true"><use class="coin" href="#i-coin"/></svg>',
    });
    if (!IN_TG) opts.push({ id: "demo", name: "Demo payment", sub: "Browser demo · no real money", amt: money(total), ic: '<svg viewBox="0 0 48 48" aria-hidden="true"><use class="coin" href="#i-coin"/></svg>' });
    if (!opts.some((o) => o.id === S.pay)) S.pay = opts[0].id;
    $("pay-options").innerHTML = opts.map((o) => `
      <button type="button" class="pay-opt${o.id === S.pay ? " is-active" : ""}" data-pay="${o.id}" role="radio" aria-checked="${o.id === S.pay}">
        <span class="pay-ic">${o.ic}</span>
        <span><span class="pay-name">${o.name}</span><br><span class="pay-sub">${o.sub}</span></span>
        <span class="pay-amt">${o.amt}</span>
      </button>`).join("");
  }
  $("pay-options").addEventListener("click", (e) => {
    const b = e.target.closest("[data-pay]");
    if (!b) return;
    S.pay = b.dataset.pay;
    haptic("light");
    renderPayOptions(cartTotal());
    refreshButtons();
  });
  $("cart-list").addEventListener("click", (e) => {
    const b = e.target.closest("[data-key]");
    if (!b) return;
    const l = S.cart.get(b.dataset.key);
    l.qty = Math.min(20, l.qty + Number(b.dataset.d));
    if (l.qty <= 0) S.cart.delete(b.dataset.key);
    haptic("light");
    rerenderItem(l.item_id);
    renderCart();
  });
  $("comment").addEventListener("input", () => { S.orderKey = null; });

  // ---------- checkout ----------
  async function ensureOrder() {
    const sig = cartSignature();
    if (S.order && S.orderKey === sig && !S.order.paid) return S.order;
    const lines = [...S.cart.values()].map(({ item_id, qty, size, milk }) => ({ item_id, qty, size, milk }));
    S.order = await api("/api/orders", { method: "POST", body: { lines, comment: $("comment").value } });
    S.orderKey = sig;
    return S.order;
  }

  async function checkout() {
    mainButton.progress(true);
    try {
      const order = await ensureOrder();
      if (S.pay === "stars") await payStars(order);
      else if (S.pay === "crypto") await payCrypto(order);
      else showDemoPay(order, "Browser demo: no Telegram wallet here, so this order uses a demo payment.");
    } catch (e) {
      haptic("error");
      toast(e.message);
    } finally {
      if (S.view === "cart") mainButton.progress(false);
    }
  }

  async function payStars(order) {
    const { invoice_link } = await api(`/api/orders/${order.id}/pay/stars`, { method: "POST" });
    tg.openInvoice(invoice_link, async (status) => {
      if (status === "paid") {
        haptic("success");
        // successful_payment reaches the bot a moment later; the ticket polls until then.
        S.order.paid = true;
        trackOrder(order.id, true);
      } else if (status === "failed") {
        haptic("error");
        toast("Payment failed. You can try again.");
      }
    });
  }

  async function payCrypto(order) {
    const r = await api(`/api/orders/${order.id}/pay/crypto`, { method: "POST" });
    if (r.mode === "crypto") {
      S.cryptoUrl = r.url;
      $("pay-title").textContent = `Order #${order.id}: waiting for crypto`;
      $("pay-text").textContent = `Pay ${money(order.total_cents)} in the CryptoBot invoice (${S.boot.payments.crypto_network}). We'll confirm here and in chat as soon as it's paid.`;
      $("demo-pay").hidden = true;
      $("crypto-btns").hidden = false;
      show("pay");
      openCryptoInvoice();
      startPoll(order.id, true);
    } else if (r.mode === "demo") {
      showDemoPay(order, `${r.reason}. Use the demo payment to see the rest of the flow.`);
    } else {
      toast(r.reason || "Crypto payments are unavailable");
    }
  }
  function openCryptoInvoice() {
    if (!S.cryptoUrl) return;
    if (IN_TG && /^https:\/\/t\.me\//.test(S.cryptoUrl)) tg.openTelegramLink(S.cryptoUrl);
    else if (IN_TG) tg.openLink(S.cryptoUrl);
    else window.open(S.cryptoUrl, "_blank", "noopener");
  }
  $("crypto-open").addEventListener("click", openCryptoInvoice);
  $("crypto-check").addEventListener("click", () => S.order && refreshOrder(S.order.id, true, true));

  function showDemoPay(order, text) {
    $("pay-title").textContent = `Order #${order.id}`;
    $("pay-text").textContent = text;
    $("demo-pay").hidden = false;
    $("crypto-btns").hidden = true;
    show("pay");
  }
  $("demo-pay-btn").addEventListener("click", async (e) => {
    e.target.disabled = true;
    try {
      const o = await api(`/api/orders/${S.order.id}/pay/demo`, { method: "POST" });
      haptic("success");
      orderPaid(o);
    } catch (err) {
      toast(err.message);
    } finally {
      e.target.disabled = false;
    }
  });

  function orderPaid(o) {
    S.order = o;
    S.cart.clear();
    $("comment").value = "";
    S.orderKey = null;
    renderMenu();
    renderTicket(o);
    show("order");
    if (o.status !== "ready") startPoll(o.id, false);
  }

  // ---------- order tracking ----------
  function trackOrder(id, justPaid) {
    S.cart.clear();
    $("comment").value = "";
    renderMenu();
    $("ticket").innerHTML = `<div class="ticket-head"><div class="ticket-no">#${id}</div><p>${justPaid ? "Payment received, confirming…" : "Loading…"}</p></div>`;
    show("order");
    refreshOrder(id, false);
    startPoll(id, false);
  }
  async function refreshOrder(id, check, manual) {
    try {
      const o = await api(`/api/orders/${id}${check ? "?check=1" : ""}`);
      S.order = o;
      if (S.view === "pay" && o.paid) return orderPaid(o);
      if (S.view === "pay" && manual) toast("Not paid yet. It can take a few seconds after you pay.");
      if (S.view === "order") {
        renderTicket(o);
        if (o.status === "ready") stopPoll();
      }
    } catch (e) {
      if (manual) toast(e.message);
    }
  }
  function startPoll(id, check) {
    stopPoll();
    S.poll = setInterval(() => refreshOrder(id, check), check ? 6000 : 4000);
  }
  function stopPoll() {
    clearInterval(S.poll);
    S.poll = null;
  }
  // Coming back from the CryptoBot invoice: check right away.
  const onReturn = () => { if (S.view === "pay" && S.order && S.cryptoUrl) refreshOrder(S.order.id, true); };
  document.addEventListener("visibilitychange", () => document.visibilityState === "visible" && onReturn());
  if (IN_TG) try { tg.onEvent("activated", onReturn); } catch (e) { /* old client */ }

  const STEPS = [["new", "Received"], ["preparing", "Preparing"], ["ready", "Ready"]];
  function renderTicket(o) {
    const idx = STEPS.findIndex(([s]) => s === o.status);
    const msg = {
      awaiting_payment: "Waiting for the payment to arrive…",
      new: "We've got it. A barista will start on it shortly.",
      preparing: "Your drinks are being made right now.",
      ready: "Ready! Pick it up at the counter and say your name.",
    }[o.status];
    const method = { stars: "Telegram Stars", crypto: "Crypto (testnet)", demo: "Demo payment" }[o.payment_method] || "";
    const paid = o.payment_method === "stars" ? `${o.total_stars} ${starIcon}` : money(o.total_cents);
    $("ticket").innerHTML = `
      <div class="ticket-head"><div class="ticket-no">Order #${o.id}</div><p>${o.paid ? `Paid · ${method}` : "Payment pending"}</p></div>
      <div class="track">${STEPS.map(([s, label], i) => `
        <div class="step ${s}${i < idx ? " done" : ""}${i === idx ? " now" : ""}"><div class="dot">${i < idx || (i === idx && s === "ready") ? "✓" : i + 1}</div>${label}</div>`).join("")}
      </div>
      <p class="status-msg ${o.status}">${msg}</p>
      <div class="ticket-body">
        ${o.lines.map((l) => `<div class="ticket-line"><span>${l.qty}× ${esc(l.label)}</span><span>${money(l.qty * l.unit_cents)}</span></div>`).join("")}
        <div class="ticket-total"><span>Total</span><span>${paid}</span></div>
        ${o.comment ? `<p class="ticket-note">💬 ${esc(o.comment)}</p>` : ""}
        ${S.boot.browser_demo ? "" : `<p class="ticket-note">We'll also message you in the bot chat when the status changes.</p>`}
      </div>`;
  }

  // ---------- my orders ----------
  $("orders-btn").addEventListener("click", async () => {
    show("orders");
    $("orders-list").innerHTML = `<li class="empty">Loading…</li>`;
    try {
      const rows = await api("/api/orders");
      $("orders-list").innerHTML = rows.length ? rows.map((o) => `
        <li><button class="order-row" type="button" data-order="${o.id}">
          <span><strong>#${o.id}</strong> · ${o.lines.reduce((n, l) => n + l.qty, 0)} items · ${money(o.total_cents)}<br>
          <span class="muted small">${new Date(o.created_at).toLocaleString([], { dateStyle: "medium", timeStyle: "short" })}</span></span>
          <span class="chip ${o.status}">${esc(o.status_label)}</span></button></li>`).join("")
        : `<li class="empty">No orders yet.</li>`;
    } catch (e) {
      $("orders-list").innerHTML = `<li class="empty">${esc(e.message)}</li>`;
    }
  });
  $("orders-list").addEventListener("click", (e) => {
    const b = e.target.closest("[data-order]");
    if (b) trackOrder(Number(b.dataset.order), false);
  });

  // ---------- boot ----------
  async function boot() {
    if (IN_TG) {
      tg.ready();
      tg.expand();
      try { tg.disableVerticalSwipes(); } catch (e) { /* < 7.7 */ }
      tg.onEvent("themeChanged", applyTheme);
    } else {
      matchMedia("(prefers-color-scheme: dark)").addEventListener("change", applyTheme);
    }
    applyTheme();
    try {
      S.boot = await api("/api/bootstrap");
    } catch (e) {
      $("blocked-text").textContent = e.status === 401
        ? "This menu works inside the Telegram bot. Send /start to the bot and tap “Open menu”."
        : `Something went wrong: ${e.message}`;
      show("blocked");
      return;
    }
    S.boot.items.forEach((i) => S.items.set(i.id, i));
    renderMenu();
    show("menu");
  }
  boot();
})();
