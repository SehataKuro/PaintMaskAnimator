"""局所探索の画素ずれと、最良候補と次点のスコア差を保持する。"""


class LocalShift(tuple):
    gap: float

    def __new__(cls, dy, dx, gap):
        result = super().__new__(cls, (int(dy), int(dx)))
        result.gap = float(gap)
        return result
