// Hero logotype: the PMA mark (from logo.png) and the wordmark, binarised into
// dot art and filled with a paint bucket. The mark's grey ring and the letter
// outlines are the line art; the three petals and the letters are the fills.
// Hovering (or tapping) a fill floods it again with the next colour.
(() => {
  "use strict";

  const canvas = document.getElementById("logo-canvas");
  if (!canvas || !canvas.getContext) return;
  const stage = canvas.parentElement;
  const ctx = canvas.getContext("2d");

  // Fill colours: the three petals of the mark. Paint / Mask / Animator take
  // the same three, so each capital matches its petal.
  const PALETTE = [[232, 56, 61], [47, 128, 237], [60, 176, 90]];
  const LETTER = -2; // base marker for wordmark dots before colouring
  // Line art and bucket colours follow the page's light / dark scheme: on a
  // light page the line art is black, as a binarised drawing would be.
  const THEMES = {
    light: {
      line: [29, 31, 36],
      ring: [128, 132, 140],
      eye: [255, 255, 255],
      bucket: { k: "#1d1f24", o: "#dfe2e8", h: "#ffffff", s: "#9aa0ab", m: "#6f7682" },
    },
    dark: {
      line: [242, 243, 245],
      ring: [139, 142, 150],
      eye: [250, 250, 250],
      bucket: { k: "#0c0d10", o: "#d0d4dc", h: "#fafafc", s: "#969ca8", m: "#b8bec8" },
    },
  };
  const darkScheme = window.matchMedia ? window.matchMedia("(prefers-color-scheme: dark)") : null;
  let theme = darkScheme && darkScheme.matches ? THEMES.dark : THEMES.light;
  // A paint pail with its bail, paint showing in the mouth and running down
  // the left side. The tip of the drip (bottom-left) is where it pours.
  // 'c' is the current paint colour.
  const BUCKET = [
    ".....mmmmmm.....",
    "....m......m....",
    "...m........m...",
    "..kmmmmmmmmmmk..",
    ".kmcccccccccmmk.",
    "ckmcccccccccccmk",
    "ckmmcccccccccmmk",
    "ckhmmmmmmmmmmmsk",
    "ckhhoooooooooosk",
    "c.khhooooooooosk",
    "c.khhoooooooosk.",
    "c.khhoooooooosk.",
    "c..khhooooooosk.",
    "c..kkkkkkkkkkkk.",
    "c...............",
    "c...............",
    "................",
    "c...............",
  ];
  const EMPTY = 0;
  const OUTLINE = 1; // letter line art
  const RING_KIND = 2; // the mark's grey ring
  const EYE_KIND = 3; // white spots on the petals
  const FILL = 4;

  const PAD = 3;
  const SPRITE = 2; // the bucket is drawn at twice the dot size
  // Room for the bucket above and to the right of the dot it pours on.
  const PAD_TOP = BUCKET.length * SPRITE + 2;
  const PAD_RIGHT = BUCKET[0].length * SPRITE;
  const LINE_REVEAL_MS = 650;
  const MOVE_MS = 95;
  const POUR_MS = 55;
  const LAYER_MS = 8; // one ring of the flood fill
  // The mark occupies these rows of logo.png (the "PMA" lettering is below).
  const MARK_SRC = { x: 0, y: 10, w: 390, h: 338 };
  const FONT_FAMILY = '"M PLUS Rounded 1c", "Arial Rounded MT Bold", "Helvetica Neue", Arial, sans-serif';
  const WORDS = [["Paint", "Mask"], ["Animator"]];
  const reduceMotion =
    window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  const font = (size) => `800 ${size}px ${FONT_FAMILY}`;

  let logoImage = null;
  let scene = null;
  let rafId = 0;
  let lastComp = -1;
  let builtWidth = 0;

  function neighbours(i, x, y, cols, rows) {
    const out = [];
    if (x > 0) out.push(i - 1);
    if (x < cols - 1) out.push(i + 1);
    if (y > 0) out.push(i - cols);
    if (y < rows - 1) out.push(i + cols);
    return out;
  }

  // Lay the lockup out for a font size.
  function measure(size, narrow, probe) {
    probe.font = font(size);
    const ascent = Math.ceil(probe.measureText("PMAkt").actualBoundingBoxAscent || size * 0.76);
    const gap = Math.max(3, Math.round(ascent * 0.3));
    const textH = ascent * 2 + gap;
    // Letters are set one by one with a little tracking, so that at this
    // weight neighbours never touch and each letter is its own fill.
    const track = Math.max(2, Math.round(size * 0.08));
    const lineWidths = WORDS.map((l) => {
      const chars = [...l.join("")];
      return Math.ceil(chars.reduce((sum, ch) => sum + probe.measureText(ch).width, 0) + track * (chars.length - 1));
    });
    const textW = Math.max(...lineWidths);
    const markH = narrow ? Math.round(textH * 0.9) : textH + 2;
    const markW = Math.round((markH * MARK_SRC.w) / MARK_SRC.h);
    const markGap = Math.max(4, Math.round(ascent * 0.4));
    const content = PAD * 2 + (narrow ? Math.max(markW, textW) : markW + markGap + textW);
    const cols = content + PAD_RIGHT;
    const rows = PAD_TOP + PAD + (narrow ? markH + markGap + textH : markH);
    return { content, size, ascent, gap, track, textH, textW, lineWidths, markH, markW, markGap, cols, rows };
  }

  function rgbToHsl(r, g, b) {
    r /= 255;
    g /= 255;
    b /= 255;
    const max = Math.max(r, g, b);
    const min = Math.min(r, g, b);
    const l = (max + min) / 2;
    if (max === min) return [0, 0, l];
    const d = max - min;
    const s = l > 0.5 ? d / (2 - max - min) : d / (max + min);
    const h = max === r ? (g - b) / d + (g < b ? 6 : 0) : max === g ? (b - r) / d + 2 : (r - g) / d + 4;
    return [h * 60, s, l];
  }

  function build(animate) {
    const avail = stage.clientWidth;
    builtWidth = avail;
    const narrow = avail < 560;
    const probe = document.createElement("canvas").getContext("2d");
    const targetCell = narrow ? 2 : 2.5;
    let L = null;
    for (let size = 68; size >= 16; size--) {
      L = measure(size, narrow, probe);
      if (avail / L.content >= targetCell) break;
    }
    const { cols, rows } = L;
    const n = cols * rows;
    const kind = new Uint8Array(n);
    const base = new Int8Array(n).fill(-1);

    const markX = narrow ? Math.floor((L.content - L.markW) / 2) : PAD;
    const markY = PAD_TOP;
    const textX = narrow ? PAD : PAD + L.markW + L.markGap;
    const textY = narrow ? PAD_TOP + L.markH + L.markGap : PAD_TOP + Math.floor((L.markH - L.textH) / 2);

    // --- The mark: downscale logo.png and sort each dot by colour. ---
    const m = document.createElement("canvas");
    m.width = L.markW;
    m.height = L.markH;
    const mc = m.getContext("2d");
    mc.imageSmoothingQuality = "high";
    mc.drawImage(logoImage, MARK_SRC.x, MARK_SRC.y, MARK_SRC.w, MARK_SRC.h, 0, 0, L.markW, L.markH);
    const mp = mc.getImageData(0, 0, L.markW, L.markH).data;
    const white = new Uint8Array(L.markW * L.markH);
    for (let y = 0; y < L.markH; y++) {
      for (let x = 0; x < L.markW; x++) {
        const p = (y * L.markW + x) * 4;
        const i = (markY + y) * cols + markX + x;
        const [h, s, l] = rgbToHsl(mp[p], mp[p + 1], mp[p + 2]);
        // Tinted highlights count as petal; only near-neutral white is a spot.
        if (s > 0.25 && l < 0.97) {
          kind[i] = FILL;
          base[i] = h < 40 || h > 320 ? 0 : h > 170 && h < 270 ? 1 : 2;
        } else if (s < 0.25 && l < 0.72) {
          kind[i] = RING_KIND;
        } else {
          white[y * L.markW + x] = 1;
        }
      }
    }
    // White enclosed by a petal (not reachable from outside, not touching the
    // ring) is one of the petal's spots.
    const seenWhite = new Uint8Array(white.length);
    for (let start = 0; start < white.length; start++) {
      if (!white[start] || seenWhite[start]) continue;
      const stack = [start];
      const region = [];
      seenWhite[start] = 1;
      let open = false;
      while (stack.length) {
        const j = stack.pop();
        region.push(j);
        const x = j % L.markW;
        const y = (j - x) / L.markW;
        if (x === 0 || y === 0 || x === L.markW - 1 || y === L.markH - 1) open = true;
        for (const k of neighbours(j, x, y, L.markW, L.markH)) {
          const kx = k % L.markW;
          const gi = (markY + (k - kx) / L.markW) * cols + markX + kx;
          if (white[k]) {
            if (!seenWhite[k]) {
              seenWhite[k] = 1;
              stack.push(k);
            }
          } else if (kind[gi] === RING_KIND) {
            open = true;
          }
        }
      }
      if (!open) {
        for (const j of region) {
          const x = j % L.markW;
          kind[(markY + (j - x) / L.markW) * cols + markX + x] = EYE_KIND;
        }
      }
    }

    // --- The wordmark: rasterise and threshold (the binarisation). ---
    const t = document.createElement("canvas");
    t.width = cols;
    t.height = rows;
    const tc = t.getContext("2d");
    const wordCanvas = document.createElement("canvas");
    wordCanvas.width = cols;
    wordCanvas.height = rows;
    const cc = wordCanvas.getContext("2d");
    for (const c of [tc, cc]) {
      c.font = font(L.size);
      c.fillStyle = "#fff";
      c.textBaseline = "alphabetic";
    }
    probe.font = font(L.size);
    let word = 0;
    WORDS.forEach((words, li) => {
      let x = textX + (narrow ? Math.floor((L.textW - L.lineWidths[li]) / 2) : 0);
      const baseline = textY + li * (L.ascent + L.gap) + L.ascent;
      for (const w of words) {
        [...w].forEach((ch) => {
          tc.fillText(ch, x, baseline);
          // The word index rides in the red channel.
          cc.fillStyle = `rgb(${word + 1},0,0)`;
          cc.fillText(ch, x, baseline);
          x += probe.measureText(ch).width + L.track;
        });
        word++;
      }
    });
    const tp = tc.getImageData(0, 0, cols, rows).data;
    const cp = cc.getImageData(0, 0, cols, rows).data;
    const wordOf = new Int8Array(n).fill(-1);
    for (let i = 0; i < n; i++) {
      if (tp[i * 4 + 3] >= 110 && kind[i] === EMPTY) {
        kind[i] = FILL;
        base[i] = LETTER;
        if (cp[i * 4 + 3] >= 110) wordOf[i] = cp[i * 4] - 1;
      }
    }
    // Letter line art: the ring of dots just outside each letter.
    for (let y = 0; y < rows; y++) {
      for (let x = 0; x < cols; x++) {
        const i = y * cols + x;
        if (kind[i] !== EMPTY) continue;
        search: for (let dy = -1; dy <= 1; dy++) {
          for (let dx = -1; dx <= 1; dx++) {
            const nx = x + dx;
            const ny = y + dy;
            if (nx < 0 || ny < 0 || nx >= cols || ny >= rows) continue;
            const j = ny * cols + nx;
            if (kind[j] === FILL && base[j] === LETTER) {
              kind[i] = OUTLINE;
              break search;
            }
          }
        }
      }
    }

    // --- Fill regions: 4-connected dots of one base colour. ---
    const compId = new Int32Array(n).fill(-1);
    const comps = [];
    for (let start = 0; start < n; start++) {
      if (kind[start] !== FILL || compId[start] >= 0) continue;
      const id = comps.length;
      const pixels = [];
      const stack = [start];
      compId[start] = id;
      let sx = 0;
      let sy = 0;
      const wordVotes = [0, 0, 0];
      while (stack.length) {
        const i = stack.pop();
        pixels.push(i);
        const x = i % cols;
        const y = (i - x) / cols;
        sx += x;
        sy += y;
        if (wordOf[i] >= 0 && wordOf[i] < 3) wordVotes[wordOf[i]]++;
        for (const j of neighbours(i, x, y, cols, rows)) {
          if (kind[j] === FILL && compId[j] < 0 && base[j] === base[start]) {
            compId[j] = id;
            stack.push(j);
          }
        }
      }
      const cx = sx / pixels.length;
      const cy = sy / pixels.length;
      let seed = pixels[0];
      let best = Infinity;
      for (const i of pixels) {
        const x = i % cols;
        const d = (x - cx) ** 2 + ((i - x) / cols - cy) ** 2;
        if (d < best) {
          best = d;
          seed = i;
        }
      }
      const isMark = base[start] !== LETTER;
      let colour = base[start];
      // Each letter takes the colour of its word.
      if (!isMark) colour = wordVotes.indexOf(Math.max(...wordVotes));
      comps.push({ seed, seedX: seed % cols, seedY: Math.floor(seed / cols), base: colour, isMark, size: pixels.length });
    }
    // Stray dots from the anti-aliased petal edges are not worth a bucket trip.
    const tiny = comps.map((c) => c.isMark && c.size < 16);

    const lineDelay = new Float32Array(n);
    for (let i = 0; i < n; i++) {
      lineDelay[i] = ((i % cols) / cols) * LINE_REVEAL_MS * 0.8 + Math.random() * LINE_REVEAL_MS * 0.2;
    }

    const dpr = window.devicePixelRatio || 1;
    const cellCss = Math.max(1.5, Math.min(4, avail / L.content));
    const cell = Math.max(1, Math.round(cellCss * dpr));
    canvas.width = cols * cell;
    canvas.height = rows * cell;
    canvas.style.width = `${(cols * cell) / dpr}px`;
    canvas.style.height = `${(rows * cell) / dpr}px`;
    // The bucket's margin on the right should not push the logo off centre.
    canvas.style.marginRight = `-${(PAD_RIGHT * cell) / dpr}px`;

    const small = document.createElement("canvas");
    small.width = cols;
    small.height = rows;
    const smallCtx = small.getContext("2d");

    scene = {
      cols, rows, n, cell, kind, compId, comps, lineDelay, small, smallCtx,
      image: smallCtx.createImageData(cols, rows),
      fill: new Int8Array(n).fill(-1),
      compColour: new Int8Array(comps.length).fill(-1),
      floods: new Map(),
      start: performance.now(),
      linesDone: !animate,
      tour: null,
      bucket: null,
      bucketColour: 0,
    };

    comps.forEach((c, id) => {
      if (!animate || tiny[id]) {
        scene.compColour[id] = c.base;
        for (let i = 0; i < n; i++) if (compId[i] === id) scene.fill[i] = c.base;
      }
    });
    if (animate) {
      const secondLine = textY + L.ascent + L.gap / 2;
      // Petals first (red, blue, green), then the letters in reading order.
      const order = comps
        .map((c, id) => id)
        .filter((id) => !tiny[id])
        .sort((a, b) => {
          const A = comps[a];
          const B = comps[b];
          if (A.isMark !== B.isMark) return A.isMark ? -1 : 1;
          if (A.isMark) return A.base - B.base || B.size - A.size;
          const lineA = A.seedY > secondLine ? 1 : 0;
          const lineB = B.seedY > secondLine ? 1 : 0;
          return lineA - lineB || A.seedX - B.seedX;
        });
      scene.tour = { order, started: 0, t0: scene.start + LINE_REVEAL_MS };
    }
    loop();
  }

  function startFlood(comp, colour, from, t0 = performance.now()) {
    const s = scene;
    const seen = new Uint8Array(s.n);
    seen[from] = 1;
    s.floods.set(comp, { comp, colour, frontier: [from], seen, t0, layers: 0 });
    s.compColour[comp] = colour;
    s.bucketColour = colour;
  }

  // Floods advance by elapsed time, not by frame, so a throttled or 120 Hz
  // display paints at the same speed.
  function stepFloods(now) {
    const s = scene;
    for (const [comp, flood] of s.floods) {
      const due = Math.floor((now - flood.t0) / LAYER_MS) + 1;
      while (flood.layers < due && flood.frontier.length) {
        const next = [];
        for (const i of flood.frontier) {
          s.fill[i] = flood.colour;
          const x = i % s.cols;
          for (const j of neighbours(i, x, (i - x) / s.cols, s.cols, s.rows)) {
            if (!flood.seen[j] && s.compId[j] === comp) {
              flood.seen[j] = 1;
              next.push(j);
            }
          }
        }
        flood.frontier = next;
        flood.layers++;
      }
      if (!flood.frontier.length) s.floods.delete(comp);
    }
  }

  const ease = (t) => 1 - (1 - t) ** 3;

  // The bucket visits one region per slot. Everything is derived from the
  // clock, so a frame that arrives late catches up instead of stalling.
  function stepTour(now) {
    const s = scene;
    const tour = s.tour;
    if (!tour || now < tour.t0) return;
    const slot = MOVE_MS + POUR_MS;
    const { order } = tour;
    while (tour.started < order.length) {
      const pourAt = tour.t0 + tour.started * slot + MOVE_MS;
      if (now < pourAt) break;
      const id = order[tour.started];
      // Regions the visitor already painted keep their colour.
      if (s.compColour[id] < 0) startFlood(id, s.comps[id].base, s.comps[id].seed, pourAt);
      tour.started++;
    }
    const k = Math.floor((now - tour.t0) / slot);
    if (k >= order.length) {
      s.tour = null;
      s.bucket = null;
      return;
    }
    const cur = s.comps[order[k]];
    const prev = k > 0 ? s.comps[order[k - 1]] : { seedX: cur.seedX - 16, seedY: cur.seedY };
    const u = now - tour.t0 - k * slot;
    s.bucketColour = cur.base;
    if (u < MOVE_MS) {
      const e = ease(u / MOVE_MS);
      s.bucket = { x: prev.seedX + (cur.seedX - prev.seedX) * e, y: prev.seedY + (cur.seedY - prev.seedY) * e, dip: 0 };
    } else {
      s.bucket = { x: cur.seedX, y: cur.seedY, dip: 1 };
    }
  }

  function render(now) {
    const s = scene;
    const data = s.image.data;
    const elapsed = now - s.start;
    if (!s.linesDone && elapsed > LINE_REVEAL_MS) s.linesDone = true;
    for (let i = 0, p = 0; i < s.n; i++, p += 4) {
      const f = s.fill[i];
      const k = s.kind[i];
      let rgb = null;
      if (f >= 0) rgb = PALETTE[f];
      else if (k !== EMPTY && k !== FILL && (s.linesDone || elapsed >= s.lineDelay[i])) {
        rgb = k === RING_KIND ? theme.ring : k === EYE_KIND ? theme.eye : theme.line;
      }
      if (rgb) {
        data[p] = rgb[0];
        data[p + 1] = rgb[1];
        data[p + 2] = rgb[2];
        data[p + 3] = 255;
      } else {
        data[p + 3] = 0;
      }
    }
    s.smallCtx.putImageData(s.image, 0, 0);
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    ctx.imageSmoothingEnabled = false;
    ctx.drawImage(s.small, 0, 0, s.cols * s.cell, s.rows * s.cell);

    if (s.bucket) drawBucket(s.bucket, s.bucket.dip);
  }

  function paintSprite(target, x0, y0, size, colour) {
    for (let y = 0; y < BUCKET.length; y++) {
      for (let x = 0; x < BUCKET[y].length; x++) {
        const ch = BUCKET[y][x];
        if (ch === ".") continue;
        target.fillStyle = ch === "c" ? `rgb(${PALETTE[colour].join(",")})` : theme.bucket[ch];
        target.fillRect(x0 + x * size, y0 + y * size, size, size);
      }
    }
  }

  function drawBucket(at, dip) {
    const s = scene;
    const size = s.cell * SPRITE;
    // The tip of the drip lands on the target dot.
    const px = Math.round(at.x) * s.cell;
    const py = (Math.round(at.y) + 1) * s.cell - BUCKET.length * size + dip * size;
    paintSprite(ctx, px, py, size, s.bucketColour);
  }

  // The mouse pointer itself becomes the bucket, filled with the last colour.
  let cursors = new Map();
  let cursorColour = 0;
  function updateCursor(colour) {
    cursorColour = colour;
    if (!cursors.has(colour)) {
      const c = document.createElement("canvas");
      c.width = BUCKET[0].length * 2;
      c.height = BUCKET.length * 2;
      paintSprite(c.getContext("2d"), 0, 0, 2, colour);
      cursors.set(colour, `url(${c.toDataURL()}) 1 ${c.height - 1}, pointer`);
    }
    canvas.style.cursor = cursors.get(colour);
  }

  function loop() {
    if (rafId) return;
    const frame = (now) => {
      rafId = 0;
      if (!scene) return;
      stepTour(now);
      stepFloods(now);
      render(now);
      const busy = scene.tour || scene.floods.size || !scene.linesDone;
      if (busy) rafId = requestAnimationFrame(frame);
    };
    rafId = requestAnimationFrame(frame);
  }

  function onPointer(event, press) {
    if (!scene) return;
    const rect = canvas.getBoundingClientRect();
    const x = ((event.clientX - rect.left) / rect.width) * scene.cols;
    const y = ((event.clientY - rect.top) / rect.height) * scene.rows;
    const gx = Math.floor(x);
    const gy = Math.floor(y);
    if (gx >= 0 && gy >= 0 && gx < scene.cols && gy < scene.rows) {
      const i = gy * scene.cols + gx;
      const comp = scene.compId[i];
      if (comp >= 0 && (comp !== lastComp || press)) {
        const current = scene.compColour[comp];
        startFlood(comp, current < 0 ? scene.comps[comp].base : (current + 1) % PALETTE.length, i);
        updateCursor(scene.bucketColour);
      }
      if (scene.kind[i] === EMPTY || comp >= 0) lastComp = comp;
    }
    loop();
    render(performance.now());
  }

  stage.addEventListener("pointermove", (e) => onPointer(e, false));
  stage.addEventListener("pointerdown", (e) => onPointer(e, true));
  stage.addEventListener("pointerleave", () => {
    lastComp = -1;
  });

  // Switching between light and dark only changes colours, so a repaint does.
  if (darkScheme) {
    darkScheme.addEventListener("change", () => {
      theme = darkScheme.matches ? THEMES.dark : THEMES.light;
      cursors = new Map();
      updateCursor(cursorColour);
      if (scene) render(performance.now());
    });
  }

  let resizeTimer = 0;
  window.addEventListener("resize", () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => {
      if (scene && stage.clientWidth !== builtWidth) build(false);
    }, 150);
  });

  const withTimeout = (promise, ms) => Promise.race([promise, new Promise((r) => setTimeout(r, ms))]);
  const image = new Image();
  image.src = "logo.png";
  Promise.all([image.decode(), document.fonts ? withTimeout(document.fonts.load(font(24)), 1500) : null])
    .then(() => {
      logoImage = image;
      updateCursor(0);
      build(!reduceMotion);
    })
    .catch(() => {
      stage.innerHTML = '<p class="logo-fallback">PaintMaskAnimator</p>';
    });
})();
