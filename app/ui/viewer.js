// キャンバスの表示（WebGL2）。
//
// 画像は GPU のテクスチャとして1枚持ち、Rust から届いた「変わった範囲」の画素だけを
// texSubImage2D で書き戻す（画像全体を毎回送らない）。ズーム・移動・回転・左右／上下反転は
// 頂点の変換だけで行う（Qt と同じく、反転は入力の座標にも掛かる）。
//
// Qt と同じ見え方にするため：
// - 画素はいつもニアレストで描く（Qt の縮小表示「速さ優先」は FastTransformation で間引く）。
// - 縮小表示「線を残す」のときは、Rust が線を残して縮めた画像（setShrunk）を別のテクスチャに
//   持ち、その大きさが今の表示の大きさと同じあいだだけ使う。
// - 市松模様（環境設定の背景の表示）は、画像の下に 8px の升目を敷く（paint_view_background）。
//
// 座標の呼び方：
// - 画面の座標：キャンバス要素の左上を原点にした CSS の px
// - 画像の座標：画像の左上を原点にした画素
"use strict";

const VERTEX = `#version 300 es
in vec2 corner;            // 0〜1 の四隅
uniform vec2 imageSize;    // 画像の画素数
uniform mat3 toClip;       // 画像の座標 → クリップ座標
uniform vec2 checkerOrigin; // 市松模様：画像の中心の、回転前の画面の座標（CSS の px）
uniform vec2 checkerScale;  // 市松模様：画像の 1 画素の、回転前の画面の大きさ（反転で負）
out vec2 uv;
out vec2 checkerPos;
void main() {
  uv = corner;
  checkerPos = checkerOrigin + (corner - 0.5) * imageSize * checkerScale;
  vec3 p = toClip * vec3(corner * imageSize, 1.0);
  gl_Position = vec4(p.xy, 0.0, 1.0);
}`;

const FRAGMENT = `#version 300 es
precision mediump float;
in vec2 uv;
in vec2 checkerPos;
uniform sampler2D image;
uniform bool checker;
out vec4 color;
void main() {
  // 画像はストレートアルファ。背景（窓の色）の上に普通に重ねる。
  vec4 c = texture(image, uv);
  if (checker) {
    // Qt の 16px の升目（240 の地に、左上と右下の 8px が 192）の上に重ねる。
    vec2 tile = floor(checkerPos / 8.0);
    float dark = mod(tile.x + tile.y, 2.0) < 0.5 ? 192.0 : 240.0;
    color = vec4(mix(vec3(dark / 255.0), c.rgb, c.a), 1.0);
  } else {
    color = c;
  }
}`;

function compile(gl, type, source) {
  const shader = gl.createShader(type);
  gl.shaderSource(shader, source);
  gl.compileShader(shader);
  if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
    throw new Error(gl.getShaderInfoLog(shader) ?? "シェーダーを組めなかった");
  }
  return shader;
}

export class Viewer {
  constructor(canvas) {
    this.canvas = canvas;
    const gl = canvas.getContext("webgl2", { alpha: false, antialias: false, premultipliedAlpha: false });
    if (!gl) throw new Error("WebGL2 が使えない");
    this.gl = gl;
    const program = gl.createProgram();
    gl.attachShader(program, compile(gl, gl.VERTEX_SHADER, VERTEX));
    gl.attachShader(program, compile(gl, gl.FRAGMENT_SHADER, FRAGMENT));
    gl.linkProgram(program);
    if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
      throw new Error(gl.getProgramInfoLog(program) ?? "シェーダーをつなげなかった");
    }
    this.program = program;
    this.uniforms = {
      imageSize: gl.getUniformLocation(program, "imageSize"),
      toClip: gl.getUniformLocation(program, "toClip"),
      checkerOrigin: gl.getUniformLocation(program, "checkerOrigin"),
      checkerScale: gl.getUniformLocation(program, "checkerScale"),
      checker: gl.getUniformLocation(program, "checker"),
    };
    const buffer = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([0, 0, 1, 0, 0, 1, 1, 1]), gl.STATIC_DRAW);
    const vao = gl.createVertexArray();
    gl.bindVertexArray(vao);
    const loc = gl.getAttribLocation(program, "corner");
    gl.enableVertexAttribArray(loc);
    gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0);
    this.vao = vao;
    this.texture = gl.createTexture();
    // 線を残して縮めた画像（縮小表示「線を残す」）。{width, height} は中身の大きさ。
    this.shrunkTexture = gl.createTexture();
    this.shrunk = null;
    gl.enable(gl.BLEND);
    gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);

    this.width = 0;
    this.height = 0;
    // 表示の状態。pan は画面の座標で、画像の中心が来る位置。
    this.zoom = 1;
    this.rotation = 0; // 度
    this.flipped = false;
    this.flippedV = false; // 上下反転
    this.checker = false; // 市松模様を敷く
    this.useShrunk = false; // 縮小表示「線を残す」
    this.pan = { x: 0, y: 0 };
    this.background = [0.17, 0.17, 0.17];
    this.onchange = null;
    this._frame = 0;
  }

  /// 画像の大きさを決め、テクスチャを作り直す（中身は白）。
  setImageSize(width, height) {
    const gl = this.gl;
    this.width = width;
    this.height = height;
    gl.bindTexture(gl.TEXTURE_2D, this.texture);
    gl.pixelStorei(gl.UNPACK_ALIGNMENT, 1);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA8, width, height, 0, gl.RGBA, gl.UNSIGNED_BYTE, null);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    this._applyFilter();
    this.shrunk = null;
    this.requestDraw();
  }

  /// 画像の (x, y) から幅 w・高さ h の範囲を、ストレート RGBA の画素で書き換える。
  writeRect(x, y, w, h, pixels) {
    if (w <= 0 || h <= 0) return;
    const gl = this.gl;
    gl.bindTexture(gl.TEXTURE_2D, this.texture);
    gl.pixelStorei(gl.UNPACK_ALIGNMENT, 1);
    gl.texSubImage2D(gl.TEXTURE_2D, 0, x, y, w, h, gl.RGBA, gl.UNSIGNED_BYTE, pixels);
    // 縮めた画像は古くなった（描いている途中は Qt と同じく元の画像を描く）。
    this.shrunk = null;
    this.requestDraw();
  }

  /// 「線を残す」で縮めた画像を置く（大きさは shrinkSize() の値）。
  setShrunk(width, height, pixels) {
    const gl = this.gl;
    gl.bindTexture(gl.TEXTURE_2D, this.shrunkTexture);
    gl.pixelStorei(gl.UNPACK_ALIGNMENT, 1);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA8, width, height, 0, gl.RGBA, gl.UNSIGNED_BYTE, pixels);
    for (const [k, v] of [[gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE], [gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE],
      [gl.TEXTURE_MIN_FILTER, gl.NEAREST], [gl.TEXTURE_MAG_FILTER, gl.NEAREST]]) gl.texParameteri(gl.TEXTURE_2D, k, v);
    this.shrunk = { width, height };
    this.requestDraw();
  }

  /// 今の表示で画像を描く画素の大きさ（Qt の _display_image_for_rect：装置の画素で、
  /// 元の大きさを超えない。round は偶数丸め）。縮めないなら null。
  shrinkSize() {
    if (!this.width || !this.height) return null;
    const ratio = Math.max(1, window.devicePixelRatio || 1);
    const even = (v) => {
      const r = Math.round(v);
      return Math.abs(v - Math.trunc(v)) === 0.5 && r % 2 !== 0 ? r - 1 : r;
    };
    const size = (n) => Math.min(n, Math.max(1, even(n * this.zoom * ratio)));
    const w = size(this.width);
    const h = size(this.height);
    return w === this.width && h === this.height ? null : { width: w, height: h };
  }

  setChecker(on) {
    this.checker = Boolean(on);
    this.requestDraw();
  }

  /// 画像の全体が見えるように、ズームと位置を合わせる（回転はそのまま）。
  fit(margin = 20) {
    const rect = this.canvas.getBoundingClientRect();
    if (!this.width || !this.height) return;
    // Qt の fit_canvas と同じく、余白を引いた大きさは 100px より小さくしない。
    const z = Math.min(Math.max(100, rect.width - margin * 2) / this.width, Math.max(100, rect.height - margin * 2) / this.height);
    this.zoom = Math.max(0.01, z);
    this.pan = { x: rect.width / 2, y: rect.height / 2 };
    this._changed();
  }

  /// 画面の点 (sx, sy) を動かさずに、ズームを factor 倍にする。
  zoomAt(factor, sx, sy) {
    const before = this.screenToImage(sx, sy);
    // Qt の set_zoom_around_canvas_center と同じ 5%〜1600%。
    this.zoom = Math.min(16, Math.max(0.05, this.zoom * factor));
    const after = this.imageToScreen(before.x, before.y);
    this.pan.x += sx - after.x;
    this.pan.y += sy - after.y;
    this._changed();
  }

  panBy(dx, dy) {
    this.pan.x += dx;
    this.pan.y += dy;
    this._changed();
  }

  /// 画像の中心を動かさずにズームを zoom にする（Qt の set_zoom_around_canvas_center）。
  setZoom(zoom) {
    this.zoom = Math.min(16, Math.max(0.05, zoom));
    this._changed();
  }

  /// 100% にして、画像の中心を表示の真ん中へ置く（show_canvas_at_100_percent）。
  showActual() {
    const rect = this.canvas.getBoundingClientRect();
    this.zoom = 1;
    this.pan = { x: rect.width / 2, y: rect.height / 2 };
    this._changed();
  }

  setFlippedVertical(flipped) {
    this.flippedV = flipped;
    this._changed();
  }

  setRotation(degrees) {
    this.rotation = ((degrees + 180) % 360 + 360) % 360 - 180;
    this._changed();
  }

  setFlipped(flipped) {
    this.flipped = flipped;
    this._changed();
  }

  /// 画像の座標 → 画面の座標の変換（2×3 の行列 [a, b, c, d, e, f]：x' = a x + c y + e）。
  _imageToScreenMatrix() {
    const r = (this.rotation * Math.PI) / 180;
    const cos = Math.cos(r) * this.zoom;
    const sin = Math.sin(r) * this.zoom;
    const fx = this.flipped ? -1 : 1;
    const fy = this.flippedV ? -1 : 1;
    // 画像の中心を原点にし、反転・拡大・回転のあと pan へ動かす。
    const cx = this.width / 2;
    const cy = this.height / 2;
    const a = cos * fx;
    const b = sin * fx;
    const c = -sin * fy;
    const d = cos * fy;
    const e = this.pan.x - (a * cx + c * cy);
    const f = this.pan.y - (b * cx + d * cy);
    return [a, b, c, d, e, f];
  }

  imageToScreen(x, y) {
    const [a, b, c, d, e, f] = this._imageToScreenMatrix();
    return { x: a * x + c * y + e, y: b * x + d * y + f };
  }

  screenToImage(sx, sy) {
    const [a, b, c, d, e, f] = this._imageToScreenMatrix();
    const det = a * d - b * c;
    const x = sx - e;
    const y = sy - f;
    return { x: (d * x - c * y) / det, y: (-b * x + a * y) / det };
  }

  _applyFilter() {
    const gl = this.gl;
    // Qt と同じく、拡大も縮小も画素を間引くだけ（FastTransformation）。「線を残す」縮小は
    // Rust が縮めた画像（setShrunk）を使う。
    const filter = gl.NEAREST;
    gl.bindTexture(gl.TEXTURE_2D, this.texture);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, filter);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, filter);
  }

  _changed() {
    this._applyFilter();
    this.requestDraw();
    this.onchange?.();
  }

  requestDraw() {
    if (this._frame) return;
    this._frame = requestAnimationFrame(() => {
      this._frame = 0;
      this.draw();
    });
  }

  draw() {
    const gl = this.gl;
    const rect = this.canvas.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    const w = Math.max(1, Math.round(rect.width * dpr));
    const h = Math.max(1, Math.round(rect.height * dpr));
    if (this.canvas.width !== w || this.canvas.height !== h) {
      this.canvas.width = w;
      this.canvas.height = h;
    }
    gl.viewport(0, 0, w, h);
    gl.clearColor(...this.background, 1);
    gl.clear(gl.COLOR_BUFFER_BIT);
    if (!this.width) return;
    // 画面の座標（CSS の px、y は下向き）→ クリップ座標（-1〜1、y は上向き）。
    const [a, b, c, d, e, f] = this._imageToScreenMatrix();
    const sx = 2 / rect.width;
    const sy = -2 / rect.height;
    // 列優先の 3×3。
    const m = new Float32Array([
      a * sx, b * sy, 0,
      c * sx, d * sy, 0,
      e * sx - 1, f * sy + 1, 1,
    ]);
    gl.useProgram(this.program);
    gl.bindVertexArray(this.vao);
    gl.uniform2f(this.uniforms.imageSize, this.width, this.height);
    gl.uniformMatrix3fv(this.uniforms.toClip, false, m);
    gl.uniform2f(this.uniforms.checkerOrigin, this.pan.x, this.pan.y);
    gl.uniform2f(this.uniforms.checkerScale, this.zoom * (this.flipped ? -1 : 1), this.zoom * (this.flippedV ? -1 : 1));
    gl.uniform1i(this.uniforms.checker, this.checker ? 1 : 0);
    gl.activeTexture(gl.TEXTURE0);
    const want = this.useShrunk ? this.shrinkSize() : null;
    const shrunk = want && this.shrunk && this.shrunk.width === want.width && this.shrunk.height === want.height;
    gl.bindTexture(gl.TEXTURE_2D, shrunk ? this.shrunkTexture : this.texture);
    gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
  }
}
