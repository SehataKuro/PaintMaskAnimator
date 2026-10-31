"""ムービー書き出しの頭に出すカット名（Issue #26）。

動画を書き出して流して見るとき、どのカットのものか分からなくなる。頭の数コマ
だけカット名を太字で重ねておくと、ファイル名を見に行かずに済む。

コマを足すのではなく最初の数コマへ重ねるので、タイミングは変わらない。
"""
from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter

#: カット名を重ねるコマ数。
LEADING_FRAMES = 8
#: 画像の高さに対する文字の大きさ。
_FONT_SCALE = 0.07
#: 画面端からの余白（画像の高さに対する割合）。
_MARGIN_SCALE = 0.04


def cut_name_for(project_path, fallback=""):
    """書き出すカット名。プロジェクトのファイル名を使う。"""
    if project_path:
        from pathlib import Path

        stem = Path(str(project_path)).stem.strip()
        if stem:
            return stem
    return str(fallback or "").strip()


def draw_cut_name(image: QImage, name: str) -> QImage:
    """``image`` の左下へ ``name`` を太字で重ねた新しい画像を返す。

    暗い絵でも明るい絵でも読めるよう、黒い縁取りの上に白で描く。
    """
    name = str(name or "").strip()
    if not name or image is None or image.isNull():
        return image

    result = QImage(image)
    height = result.height()
    font = QFont()
    font.setBold(True)
    font.setPixelSize(max(12, int(height * _FONT_SCALE)))

    painter = QPainter(result)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
    painter.setFont(font)
    margin = max(6, int(height * _MARGIN_SCALE))
    rect = QRect(
        margin,
        margin,
        max(1, result.width() - margin * 2),
        max(1, result.height() - margin * 2),
    )
    alignment = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom
    # 縁取り：8方向へずらして黒で描いてから、白を重ねる。
    painter.setPen(QColor("black"))
    for dx in (-2, 0, 2):
        for dy in (-2, 0, 2):
            if dx or dy:
                painter.drawText(rect.translated(dx, dy), alignment, name)
    painter.setPen(QColor("white"))
    painter.drawText(rect, alignment, name)
    painter.end()
    return result
