"""転送・集計・結果回収まで含めたCPU/GPUの実測に基づく振り分け。"""


class PipelinePolicy:
    def __init__(self, budget=8):
        self.records = {}
        self.cpu_seconds = 0.0
        self.gpu_seconds = 0.0
        self.budget = budget

    def choose(self, key):
        record = self.records.get(key)
        if record is not None:
            return record[0]
        related = [(candidate, item) for candidate, item in self.records.items() if candidate[0] == key[0]]
        if len(related) < self.budget:
            return None
        # 新しい寸法のたびに二重計算を続けない。同種・近い仕事量だけ実測を引き継ぐ。
        def distance(candidate):
            if key[0] == 'alignment':
                if candidate[1] != key[1]:
                    return 100
                return abs(candidate[2] - key[2])
            if key[0] == 'sample' and candidate[3:] != key[3:]:
                return 100
            return sum(abs(a-b) for a, b in zip(candidate[1:3], key[1:3]))
        closest, result = min(related, key=lambda pair: distance(pair[0]))
        return result[0] if distance(closest) <= 1 else False

    def record(self, key, cpu_seconds, gpu_seconds, *, valid=True):
        # わずかな差や計測誤差でGPUへ移さない。CPUの並列実行時間と比較する。
        use_gpu = valid and gpu_seconds < cpu_seconds * 0.9
        self.records[key] = use_gpu, cpu_seconds, gpu_seconds
        self.cpu_seconds += cpu_seconds
        self.gpu_seconds += gpu_seconds
        return use_gpu
