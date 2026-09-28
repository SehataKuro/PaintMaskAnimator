"""セル番号（絵番号）の正規化の計算。

番号は 1 から始まる整数の連番だけを使う（docs/design-principles.md）。
ここではデータを変えずに、正規化したときの対応表だけを求める。
"""


def normalization_mapping(frames, archive_keys, layer_index):
    """正規化したときの {旧番号: 新番号} を返す。

    シートに登場する順に 1, 2, 3… を振り、シートで使っていない番号
    （連番だけの番号や削除済みの番号）はその後ろへ番号順に並べる。
    ``archive_keys`` は ``(layer_index, number)`` の並び（削除済みの絵）。
    """
    if not frames:
        return {}
    target = int(layer_index)
    sheet_numbers = []
    all_numbers = set()
    for frame in frames:
        if not (0 <= target < len(frame.layers)):
            continue
        layer = frame.layers[target]
        number = layer.sequence_number
        if number is None:
            continue
        all_numbers.add(int(number))
        if (
            layer.has_content
            and not layer.sequence_only
            and int(number) not in sheet_numbers
        ):
            sheet_numbers.append(int(number))
    all_numbers.update(
        int(number)
        for archived_layer, number in archive_keys
        if int(archived_layer) == target
    )
    ordered = sheet_numbers + sorted(all_numbers - set(sheet_numbers))
    return {
        old_number: new_number
        for new_number, old_number in enumerate(ordered, 1)
    }


def changed_count(mapping):
    """正規化で番号が変わる絵の数。"""
    return sum(1 for old, new in mapping.items() if int(old) != int(new))
