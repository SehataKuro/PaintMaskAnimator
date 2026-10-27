"""Measure automatic painting against a saved .pman without modifying it.

Run from the repository root: python -m scripts.benchmark_auto_paint project.pman
"""
import argparse
from hashlib import sha256
import importlib.util
import io
import json
from pathlib import Path
import statistics
import sys
from time import perf_counter
import zipfile

import numpy as np
from PIL import Image

import paintmaskanimator as package
from paintmaskanimator import auto_paint, bucket_fill


def numbers(text):
    return [int(value) for value in text.split(",")]


def load_snapshot(directory):
    loaded = {}
    for name in ("bucket_fill", "auto_paint", "auto_bucket"):
        spec = importlib.util.spec_from_file_location(
            f"paintmaskanimator._benchmark_{name}", directory / f"{name}.py",
        )
        if spec is None or spec.loader is None:
            raise ValueError(f"Cannot load snapshot module: {name}")
        module = importlib.util.module_from_spec(spec)
        module.__package__ = "paintmaskanimator"
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        setattr(package, name, module)
        loaded[name] = module
    return loaded["auto_paint"], loaded["bucket_fill"]


def fingerprint(plan, pixels, ap):
    digest = sha256()
    for index, paint in sorted(plan.cells.items()):
        digest.update(ap.apply_paint(pixels[index], paint).tobytes())
        digest.update(repr((index, paint.filled, paint.unsure, paint.checked,
                            paint.line_pixels, paint.clicks)).encode())
        for note in paint.notes:
            digest.update(repr((note.kind, note.rgb, note.bounds, note.expected)).encode())
        for name in ("undecided", "unpainted"):
            for bounds, mask in getattr(paint, name):
                digest.update(repr(bounds).encode())
                digest.update(mask.tobytes())
    return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project", type=Path)
    parser.add_argument("--frames", type=numbers, default=numbers("1,4,7,10,13"))
    parser.add_argument("--references", type=numbers, default=[1])
    parser.add_argument("--layer", type=int, default=1, help="Zero-based layer index")
    parser.add_argument("--gpu", choices=("off", "auto", "on"), default="off")
    parser.add_argument("--preparation-workers", type=int, default=2)
    parser.add_argument("--no-reference-cache", action="store_true")
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--snapshot", type=Path, help="Directory with saved baseline modules")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    ap, bucket = (load_snapshot(args.snapshot) if args.snapshot else (auto_paint, bucket_fill))
    references = [args.frames.index(frame) for frame in args.references]
    pixels, initial = [], []
    with zipfile.ZipFile(args.project) as archive:
        metadata = json.loads(archive.read("project.json"))

        def read(name):
            return np.array(Image.open(io.BytesIO(archive.read(name))).convert("RGBA"))

        for index, frame in enumerate(args.frames):
            layer = metadata["frames"][frame - 1]["layers"][args.layer]
            raw = ap.line_only(read(layer["initial"])) if layer.get("initial") else None
            pixels.append(read(layer["image"]) if index in references or raw is None else raw)
            initial.append(raw)
    results = []
    for run in range(args.repeats):
        kwargs = {} if args.snapshot else dict(
            gpu=args.gpu, preparation_workers=args.preparation_workers,
            reuse_reference_regions=not args.no_reference_cache,
        )
        started = perf_counter()
        plan = ap.plan_auto_paint(
            pixels, references[0], reference_indices=references, lines=initial,
            bucket_options=bucket.BucketOptions(extend_lines=True), max_rounds=args.rounds,
            **kwargs,
        )
        seconds = perf_counter() - started
        result = dict(run=run + 1, seconds=seconds, fingerprint=fingerprint(plan, pixels, ap),
                      rounds=plan.rounds, filled=sum(p.filled for p in plan.cells.values()),
                      unsure=sum(p.unsure for p in plan.cells.values()),
                      trace_pixels=sum(p.line_pixels for p in plan.cells.values()),
                      remaining_primary=sum(int(ap.primary_line_mask(
                          ap.apply_paint(pixels[index], paint)).sum())
                          for index, paint in plan.cells.items()),
                      optimizations=getattr(plan, "optimization_stats", {}))
        results.append(result)
        print(json.dumps(result), flush=True)
    report = dict(frames=args.frames, references=args.references, gpu=args.gpu,
                  preparation_workers=args.preparation_workers,
                  reuse_reference_regions=not args.no_reference_cache,
                  median_seconds=statistics.median(item["seconds"] for item in results), runs=results,
                  tp_palette=[
                      dict(source_rgb=list(rgb), finishes=[
                          dict(finish_rgb=list(ap.reference_check._to_rgb(value)), pixels=count)
                          for value, count in sorted(colors.items(), key=lambda item: (-item[1], item[0]))
                      ]) for rgb, colors in sorted(getattr(plan, "trace_palette", {}).items())
                  ])
    print(json.dumps(report), flush=True)
    if args.output:
        args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
