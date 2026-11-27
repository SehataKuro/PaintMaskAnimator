"""Run pytest without invoking unsafe QtAds static destructors.

PySide6-QtAds can access already-destroyed Qt objects during interpreter
shutdown after pytest has completely finished.  Exiting directly after
``pytest.main`` preserves pytest's real status while avoiding that third-party
native teardown defect.  This replaces the former CI ``|| true`` workaround.

On Windows, ``os._exit`` is not direct enough: the C runtime's ``_exit`` still
runs enough teardown for a C++ exception to escape and abort the process, so
the run fails with 0xC0000409 (or an access violation) even though every test
passed -- intermittently, in roughly half of the runs.  ``TerminateProcess``
on our own process stops it there and then, with the status pytest reported.

手元で軽く回すための並列実行
----------------------------
``--jobs N`` を付けると、テストのファイルを N 組に分け、組ごとに別の
pytest のプロセスで回す（``python scripts/run_tests.py --jobs 2 -q``）。
プロセスの数は N を超えないので、ファイルごとにプロセスを立てるやり方の
ように機械を埋め尽くさない。組のプロセスは優先度を下げて回す（``--nice M``、
既定 10。Windows では「通常以下」）ので、ほかのアプリの操作を邪魔しにくい。
``--jobs`` を付けなければ従来どおり 1 つのプロセスで回す（CI はこちら）。
このときも ``--nice M`` を付ければ、そのプロセスの優先度だけを下げる。

``--jobs`` と一緒には使えないもの：``::`` によるテストの名前の指定、
前回の結果を使うオプション（``--lf`` など）、結果をファイルに書くオプション
（``--junitxml`` など。組どうしで上書きし合うため）。
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

# 空白で区切って値を書ける pytest のオプション。値をテストの場所と取り違えない。
_TAKES_VALUE = {
    "-p", "-k", "-m", "-c", "-o", "-W", "--ignore", "--ignore-glob", "--deselect",
    "--durations", "--durations-min", "--rootdir", "--maxfail", "--tb",
    "--basetemp", "--confcutdir", "--junitxml", "--junit-xml", "--cov",
    "--cov-report", "--cov-config", "--log-level", "--log-file",
    "--import-mode", "--capture", "--color", "--override-ini",
}
# 前回の結果（キャッシュ）を使うので、キャッシュを切る組の実行では使えない。
_NEEDS_CACHE = {"--lf", "--last-failed", "--ff", "--failed-first", "--nf",
                "--new-first", "--sw", "--stepwise", "--sw-skip",
                "--stepwise-skip", "--cache-show", "--cache-clear"}
# 結果をファイルに書くので、組どうしで上書きし合う。
_WRITES_FILES = ("--junitxml", "--junit-xml", "--cov", "--log-file",
                 "--basetemp", "--html", "--result-log")


class _UsageError(Exception):
    pass


def _hard_exit(status: int) -> None:
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32")
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
        kernel32.TerminateProcess(kernel32.GetCurrentProcess(), status)
    os._exit(status)


def _take_option(args: list[str], name: str) -> int | None:
    """``args`` から ``name N`` か ``name=N`` を取り除き、その値を返す。"""
    found = None
    index = 0
    while index < len(args):
        arg = args[index]
        if arg == name or arg.startswith(name + "="):
            if found is not None:
                raise _UsageError(f"{name} が 2 回あります")
            if arg == name:
                if index + 1 >= len(args):
                    raise _UsageError(f"{name} の値がありません")
                value = args[index + 1]
                del args[index : index + 2]
            else:
                value = arg.split("=", 1)[1]
                del args[index]
            try:
                found = int(value)
            except ValueError:
                raise _UsageError(f"{name} の値は整数です：{value}") from None
            continue
        if arg in _TAKES_VALUE:
            index += 2
        else:
            index += 1
    return found


def _split_paths(args: list[str]) -> tuple[list[Path], list[str]]:
    """引数を、テストの場所（ファイルかフォルダ）とそれ以外の pytest の
    オプションに分ける。値を取るオプションの値は場所と取り違えない。"""
    paths: list[Path] = []
    options: list[str] = []
    pending = False
    for arg in args:
        if pending:
            options.append(arg)
            pending = False
        elif arg.startswith("-"):
            options.append(arg)
            pending = arg in _TAKES_VALUE
        elif Path(arg.split("::", 1)[0]).exists():
            paths.append(Path(arg))
        else:
            options.append(arg)
    return paths, options


def _check_options(paths: list[Path], options: list[str]) -> None:
    if any("::" in str(path) for path in paths):
        raise _UsageError("--jobs は ::（テストの名前の指定）と一緒には使えません")
    for option in options:
        name = option.split("=", 1)[0]
        if name in _NEEDS_CACHE:
            raise _UsageError(f"--jobs は {name}（前回の結果を使う）と一緒には使えません")
        if name in _WRITES_FILES:
            raise _UsageError(f"--jobs は {name}（組どうしでファイルを上書きし合う）と一緒には使えません")


def _ignored(options: list[str]) -> list[Path]:
    ignored = []
    for index, arg in enumerate(options):
        if arg == "--ignore" and index + 1 < len(options):
            ignored.append(Path(options[index + 1]).resolve())
        elif arg.startswith("--ignore="):
            ignored.append(Path(arg.split("=", 1)[1]).resolve())
    return ignored


def _test_files(paths: list[Path], ignored: list[Path]) -> list[Path]:
    files: dict[Path, None] = {}
    for path in paths or [ROOT / "tests"]:
        found = sorted(path.rglob("test_*.py")) if path.is_dir() else [path]
        for file in found:
            resolved = file.resolve()
            if any(resolved == skip or skip in resolved.parents for skip in ignored):
                continue
            files[resolved] = None
    return list(files)


def _shards(files: list[Path], count: int) -> list[list[Path]]:
    """ファイルの大きさを手間の目安にして、重い順に一番軽い組へ配る。"""
    shards: list[list[Path]] = [[] for _ in range(count)]
    loads = [0] * count
    for file in sorted(files, key=lambda f: f.stat().st_size, reverse=True):
        lightest = loads.index(min(loads))
        shards[lightest].append(file)
        loads[lightest] += file.stat().st_size
    return [shard for shard in shards if shard]


def _lower_priority(niceness: int):
    if sys.platform == "win32" or niceness <= 0:
        return None
    return lambda: os.nice(niceness)


def _priority_flags(niceness: int) -> int:
    if sys.platform == "win32" and niceness > 0:
        return subprocess.BELOW_NORMAL_PRIORITY_CLASS
    return 0


def _combined_status(statuses: list[int]) -> int:
    """組ごとの終了コードを 1 つにまとめる。

    signal で死んだ組（POSIX では負の値）は失敗とみなす。5（集めたテストが
    ない）は、``-k`` などで一部の組だけ空になることがあるので、全部の組が
    5 のときだけ 5 とする。それ以外は重い方（1 = 失敗、2 以上 = 中断や
    内部エラー）を返す。
    """
    normalized = [128 - status if status < 0 else status for status in statuses]
    if normalized and all(status == 5 for status in normalized):
        return 5
    return max((status for status in normalized if status != 5), default=0)


def run_parallel(args: list[str], jobs: int, niceness: int) -> int:
    paths, options = _split_paths(args)
    _check_options(paths, options)
    files = _test_files(paths, _ignored(options))
    if not files:
        print("テストのファイルが見つかりません", file=sys.stderr)
        return 5
    shards = _shards(files, max(1, jobs))
    script = Path(__file__).resolve()
    environment = dict(os.environ, PYTHONIOENCODING="utf-8")
    running = []
    try:
        for number, shard in enumerate(shards, 1):
            log = tempfile.NamedTemporaryFile(
                "w+", encoding="utf-8", errors="replace",
                prefix=f"pma-tests-{number}-", suffix=".txt", delete=False,
            )
            running.append((number, len(shard), None, log))
            command = [sys.executable, str(script), "-p", "no:cacheprovider",
                       *options, *map(str, shard)]
            process = subprocess.Popen(
                command, cwd=ROOT, env=environment, stdout=log,
                stderr=subprocess.STDOUT, preexec_fn=_lower_priority(niceness),
                creationflags=_priority_flags(niceness),
            )
            running[-1] = (number, len(shard), process, log)
        print(f"{len(files)} 個のファイルを {len(shards)} 組に分けて実行中…", flush=True)
        statuses = []
        for number, size, process, log in running:
            status = process.wait()
            statuses.append(status)
            log.seek(0)
            output = log.read()
            lines = [line for line in output.splitlines() if line.strip()]
            summary = lines[-1] if lines else "（出力なし）"
            print(f"[{number}] {size} ファイル（終了コード {status}）: {summary}", flush=True)
            if status not in (0, 5):
                print(output, flush=True)
        return _combined_status(statuses)
    finally:
        for _number, _size, process, log in running:
            if process is not None and process.poll() is None:
                process.terminate()
                process.wait()
            log.close()
            os.unlink(log.name)


def main() -> None:
    args = sys.argv[1:]
    try:
        jobs = _take_option(args, "--jobs")
        niceness = _take_option(args, "--nice")
        if jobs is not None:
            status = run_parallel(args, jobs, 10 if niceness is None else niceness)
        else:
            lower = _lower_priority(niceness or 0)
            if lower is not None:
                lower()
            status = int(pytest.main(args))
    except _UsageError as error:
        print(f"run_tests.py: {error}", file=sys.stderr)
        status = 4
    sys.stdout.flush()
    sys.stderr.flush()
    _hard_exit(status)


if __name__ == "__main__":
    main()
