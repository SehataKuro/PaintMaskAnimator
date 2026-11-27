// 説明の吹き出し（Qt 版の tooltip.py と同じく、1行目を見出しとして太字にする）。
//
// 要素に setTip(el, 文字列 または 文字列を返す関数) で説明を付ける。マウスを乗せて
// 少し待つと出し、離すか押すと消す。

const DELAY_MS = 600;
let box = null;
let timer = 0;
let current = null;

function ensureBox() {
  if (box) return box;
  box = document.createElement("div");
  box.className = "pma-tooltip";
  document.body.appendChild(box);
  return box;
}

function textOf(el) {
  const tip = el._pmaTip;
  return typeof tip === "function" ? tip() : tip;
}

function show(el, x, y) {
  const text = textOf(el);
  if (!text) return;
  const b = ensureBox();
  b.replaceChildren();
  const [head, ...rest] = String(text).split("\n");
  const title = document.createElement("div");
  title.className = "pma-tooltip-title";
  title.textContent = head;
  b.appendChild(title);
  if (rest.length) {
    const body = document.createElement("div");
    body.textContent = rest.join("\n");
    b.appendChild(body);
  }
  b.style.display = "block";
  const w = b.offsetWidth;
  const h = b.offsetHeight;
  const left = Math.min(x + 12, window.innerWidth - w - 4);
  const top = y + 18 + h > window.innerHeight ? y - h - 8 : y + 18;
  b.style.left = `${Math.max(4, left)}px`;
  b.style.top = `${Math.max(4, top)}px`;
}

export function hideTip() {
  clearTimeout(timer);
  current = null;
  if (box) box.style.display = "none";
}

/// 要素に説明を付ける。
export function setTip(el, tip) {
  el._pmaTip = tip;
  if (el._pmaTipBound) return el;
  el._pmaTipBound = true;
  el.addEventListener("mouseenter", (e) => {
    clearTimeout(timer);
    current = el;
    const { clientX: x, clientY: y } = e;
    timer = setTimeout(() => current === el && show(el, x, y), DELAY_MS);
  });
  el.addEventListener("mousemove", (e) => {
    if (box?.style.display === "block" && current === el) return;
    clearTimeout(timer);
    current = el;
    const { clientX: x, clientY: y } = e;
    timer = setTimeout(() => current === el && show(el, x, y), DELAY_MS);
  });
  el.addEventListener("mouseleave", hideTip);
  el.addEventListener("pointerdown", hideTip);
  return el;
}
