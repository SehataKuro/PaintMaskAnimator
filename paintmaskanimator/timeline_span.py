"""Common span entry point; Phase B retains the legacy exposure definition."""


def span_at(frames, layer_index, column):
    """Return (kind, start, raw key length), including legacy unclipped length."""
    if (
        column < 0
        or column >= len(frames)
        or layer_index < 0
    ):
        return None, None, 0

    for start in range(column, -1, -1):
        if layer_index >= len(frames[start].layers):
            continue
        layer = frames[start].layers[layer_index]
        if getattr(layer, "sequence_only", False):
            continue
        is_content = bool(layer.has_content)
        is_blank = bool(
            getattr(layer, "is_blank_key", False)
        )
        if not (is_content or is_blank):
            continue

        exposure = max(1, int(layer.exposure))
        if column < start + exposure:
            return (
                "content" if is_content else "blank",
                start,
                exposure,
            )
        # 直近の明示コマが届いていない場合、それ以前の露出は
        # この明示コマを越えて表示しない。
        break

    return None, None, 0
