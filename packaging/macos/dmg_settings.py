# dmgbuild settings for the macOS installer window.
#
#   dmgbuild -s packaging/macos/dmg_settings.py \
#       -D app=dist/PaintMaskAnimator.app "PaintMaskAnimator" out.dmg
#
# Run from the repository root: dmgbuild exec()s this file without __file__,
# so paths are relative to the working directory.
#
# Window size and icon positions must match scripts/make_dmg_background.py,
# which renders the background (arrow and captions) around them.
import os.path

_here = "packaging/macos"
_app = defines.get("app", "dist/PaintMaskAnimator.app")  # noqa: F821
_app_name = os.path.basename(_app)
_note = os.path.join(_here, "開けないときは.txt")

format = "UDZO"
filesystem = "HFS+"

files = [_app, _note]
symlinks = {"Applications": "/Applications"}

# dmgbuild picks up dmg-background@2x.png next to it for Retina screens.
background = os.path.join(_here, "dmg-background.png")

# Taller than the background: the title bar (and, on recent macOS, a bottom
# bar) take part of this height.
window_rect = ((200, 120), (660, 540))
default_view = "icon-view"
show_status_bar = False
show_tab_view = False
show_toolbar = False
show_pathbar = False
show_sidebar = False

icon_size = 96
text_size = 13
arrange_by = None
icon_locations = {
    _app_name: (170, 225),
    "Applications": (490, 225),
    "開けないときは.txt": (330, 395),
}
