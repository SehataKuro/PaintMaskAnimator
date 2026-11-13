"""Dense timeline spans; blank starts retain independent adjacent circles."""


def span_at(frames, layer_index, column):
    if not 0 <= column < len(frames) or layer_index < 0:
        return None, None, 0

    def state(index):
        if layer_index >= len(frames[index].layers):
            return None, None
        slot = frames[index].layers[layer_index]
        from .timeline_metadata import reference_for
        reference = reference_for(slot)
        return {"CELL": "content", "BLANK": "blank", "EMPTY": None}[reference.kind], reference.number

    kind, number = state(column)
    if kind is None:
        return None, None, 0
    start, end = column, column + 1
    while start > 0 and state(start - 1) == (kind, number):
        if kind == "blank" and frames[start].layers[layer_index].blank_start:
            break
        start -= 1
    while end < len(frames) and state(end) == (kind, number):
        if kind == "blank" and frames[end].layers[layer_index].blank_start:
            break
        end += 1
    return kind, start, end - start
