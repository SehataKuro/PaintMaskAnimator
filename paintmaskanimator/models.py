from .common import *  # noqa: F401,F403
from .utils import blank_image


@dataclass
class Layer:
    name: str
    image: QImage
    visible: bool = True
    opacity: float = 1.0
    is_paper: bool = False
    has_content: bool = False
    alpha_locked: bool = False
    exposure: int = 1
    color_filter_enabled: bool = False
    color_filter_rgb: Optional[tuple] = None
    is_blank_key: bool = False
    sequence_number: Optional[int] = None
    sequence_only: bool = False

    def clone(self):
        return Layer(
            self.name, self.image.copy(), self.visible, self.opacity,
            self.is_paper, self.has_content, self.alpha_locked, self.exposure,
            self.color_filter_enabled,
            tuple(self.color_filter_rgb) if self.color_filter_rgb is not None else None,
            bool(self.is_blank_key),
            self.sequence_number,
            bool(self.sequence_only),
        )


@dataclass
class Frame:
    layers: List[Layer]
    duration: int = 1

    def clone(self):
        return Frame([x.clone() for x in self.layers], self.duration)


def make_frame(layer_names=None):
    names = layer_names or ["Layer 1"]
    return Frame([Layer(name, blank_image()) for name in names])
