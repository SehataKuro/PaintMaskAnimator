"""Qt-independent exposure resolution, shared by editing and playback."""
import numpy as np

from . import native


def resolve_exposures(frames):
    count = len(frames)
    layers = max((len(frame.layers) for frame in frames), default=0)
    # Missing cells do not cancel a preceding exposure (resolve_key_frame's
    # historical behaviour). Explicit blank keys do cancel it.
    states = np.zeros((count, layers), dtype=np.uint8)
    exposures = np.ones((count, layers), dtype=np.int64)
    max_exposure = np.iinfo(np.int64).max
    for column, frame in enumerate(frames):
        for index, layer in enumerate(frame.layers):
            states[column, index] = 1 if layer.has_content else 2 if getattr(layer, "is_blank_key", False) else 0
            exposures[column, index] = min(max_exposure, max(1, int(layer.exposure)))
    result = native.resolve_exposures(states, exposures)
    if result is not None:
        return result
    result = np.full((count, layers), -1, dtype=np.int64)
    for index in range(layers):
        key, end = -1, -1
        for column in range(count):
            state = states[column, index]
            if state == 1:
                key, end = column, column + int(exposures[column, index])
            elif state == 2:
                key, end = -1, -1
            if column < end:
                result[column, index] = key
    return result
