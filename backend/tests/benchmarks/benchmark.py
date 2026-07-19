"""
Benchmark runner for the HAQQ pipeline.

Runs every row in benchmark_dataset.csv through the real graph (classify →
extract_keywords → search → fetch_bodies → verify → score) and compares the
pipeline's final `verdict` against the labeled `expected_verdict`.

Usage:
    python benchmark.py                      # uses benchmark_dataset.csv next to this file
    python benchmark.py path/to/other.csv     # or point at a different dataset

Add rows to benchmark_dataset.csv as you find edge cases — more rows = a more
trustworthy accuracy number, especially for the classes with few examples.
"""
import asyncio
import csv
import sys
import time
from dataclasses import dataclass
from pathlib import Path

# Ensure the root directory is in sys.path so 'import backend' works
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent))

from sklearn.metrics import accuracy_score, classification_report, confusion_matrix, f1_score

# Adjust these imports to match your actual project layout.
from backend.graph.builder import build_graph, run_verify
import backend.nodes.search as search_module
from backend.core import text_processing

DEFAULT_DATASET = Path(__file__).parent / "benchmark_dataset.csv"
# benchmark.py — add this function after imports

def _initialize_pipeline():
    from transformers import pipeline as hf_pipeline
    import easyocr
    from backend.nodes import classify

    if getattr(classify, "classifier", None) is not None:
        print("[benchmark] Classifier already initialized — skipping.")
        return

    print("[benchmark] Loading classifier...")
    classify.classifier = hf_pipeline(
        "zero-shot-classification",
        model="MoritzLaurer/mDeBERTa-v3-base-mnli-xnli",
    )
    print(f"[benchmark] Classifier injected: {classify.classifier is not None} ✅")

    print("[benchmark] Loading EasyOCR...")
    classify.ocr_reader = easyocr.Reader(["ar", "en"], gpu=False)
    print("[benchmark] EasyOCR injected ✅")
# Which extractor implementations to A/B test. Keys are just display labels;
# values are the actual function objects from text_processing.py.
EXTRACTORS = {
    "yake":      text_processing._extract_keywords_yake,
    "heuristic": text_processing._extract_keywords_heuristic,
}


@dataclass
class Prediction:
    text: str
    lang: str
    expected: str
    predicted: str
    latency_s: float
    explanation: str = ""
    degraded: bool = False   # True if rate-limit/classifier-init noise was detected
                             # for this row — means the pipeline didn't actually run
                             # cleanly, so this row's correctness is less informative
    total_tokens: int = 0
    total_cost_usd: float = 0.0


class _use_extractor:
    """
    Context manager that swaps which keyword extractor extract_keywords_node
    actually calls, then restores the original on exit.

    Why patch backend.nodes.search._extract_keywords and not
    text_processing._extract_keywords: `from x import y` copies a reference
    into the importing module's namespace at import time. Rebinding
    text_processing._extract_keywords afterward does NOT change the name
    already bound inside backend.nodes.search — that module's own attribute
    has to be patched directly for extract_keywords_node to pick it up.
    """
    def __init__(self, extractor_fn):
        self.extractor_fn = extractor_fn
        self.original = None

    def __enter__(self):
        self.original = search_module._extract_keywords
        search_module._extract_keywords = self.extractor_fn
        return self

    def __exit__(self, *exc):
        search_module._extract_keywords = self.original


def _load_dataset(path: Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        raise ValueError(f"No rows found in {path}")
    return rows


import contextlib
import io

REQUEST_DELAY_S = 2.0  # pause between rows to avoid tripping per-minute/daily rate
                       # limits on Groq/newsdata/gnews mid-benchmark. Raise this if
                       # you're still seeing 429s; it only slows the benchmark down,
                       # it doesn't touch pipeline behavior.

# Substrings that show up in your pipeline's own print() logging when something
# degraded silently (LLM rate-limited, classifier not initialized, upstream API
# 429s) rather than actually failing loudly. Used only to TAG rows for reporting —
# doesn't change what verdict gets recorded, just lets you separate "the pipeline
# was working and got this wrong" from "the pipeline couldn't actually run".
_DEGRADED_MARKERS = (
    #"rate_limit_exceeded",
    #"429",
    "Classifier has not been initialized",
    #"LLM error",
    #"LLM skipped",
)


class _Tee(io.TextIOBase):
    """Writes to multiple streams at once — lets us keep live console output
    while also capturing it into a buffer we can inspect afterward."""
    def __init__(self, *streams):
        self.streams = streams

    def write(self, s):
        for st in self.streams:
            st.write(s)
        return len(s)

    def flush(self):
        for st in self.streams:
            st.flush()


async def run_benchmark(dataset_path: Path, use_agent: bool = False) -> list[Prediction]:
    if use_agent:
        from backend.agent_pipeline.builder import build_agent_graph, run_agent_verify
        graph = build_agent_graph()
        verify_func = run_agent_verify
    else:
        graph = build_graph()
        verify_func = run_verify
        
    rows = _load_dataset(dataset_path)
    predictions: list[Prediction] = []

    for i, row in enumerate(rows, start=1):
        buf = io.StringIO()
        tee = _Tee(sys.stdout, buf)

        start = time.perf_counter()
        with contextlib.redirect_stdout(tee):
            result = await verify_func(graph, row["text"], row["lang"])
        latency = time.perf_counter() - start

        captured = buf.getvalue()
        degraded = any(marker in captured for marker in _DEGRADED_MARKERS)

        pred = Prediction(
            text=row["text"],
            lang=row["lang"],
            expected=row["expected_verdict"].strip(),
            predicted=result["verdict"],
            latency_s=latency,
            explanation=result.get("explanation", ""),
            degraded=degraded,
            total_tokens=result.get("total_tokens", 0),
            total_cost_usd=result.get("total_cost_usd", 0.0),
        )
        predictions.append(pred)

        mark = "✅" if pred.expected == pred.predicted else "❌"
        flag = " 🟡degraded" if degraded else ""
        print(
            f"{mark} [{i}/{len(rows)}] expected={pred.expected:<12} "
            f"predicted={pred.predicted:<12} ({latency:.2f}s){flag}  {row['text'][:60]}"
        )

        if i < len(rows):
            await asyncio.sleep(REQUEST_DELAY_S)

    return predictions


def report(predictions: list[Prediction]) -> None:
    degraded_count = sum(1 for p in predictions if p.degraded)
    clean = [p for p in predictions if not p.degraded]

    print("\n" + "=" * 70)
    print("RUN HEALTH")
    print("=" * 70)
    print(
        f"{degraded_count}/{len(predictions)} rows were degraded "
        f"(classifier not initialized, and/or LLM or search API rate-limited).\n"
        f"Metrics below are split so a bad quota day doesn't get silently\n"
        f"averaged into your real accuracy number."
    )
    if degraded_count == len(predictions):
        print("⚠️  EVERY row was degraded — the numbers below reflect the fallback")
        print("    path only, not your actual classifier/LLM pipeline. Fix quotas")
        print("    and re-run before trusting any of this.")

    print("\n" + "=" * 70)
    print("CLASSIFICATION REPORT — ALL ROWS (precision / recall / F1 per class)")
    print("=" * 70)
    _print_classification_block(predictions)

    if clean and len(clean) < len(predictions):
        print("\n" + "=" * 70)
        print(f"CLASSIFICATION REPORT — CLEAN ROWS ONLY (n={len(clean)}, degraded rows excluded)")
        print("=" * 70)
        _print_classification_block(clean)
    elif not clean:
        print("\n(No clean rows to report separately — every row was degraded.)")

    avg_latency = sum(p.latency_s for p in predictions) / len(predictions)
    max_latency = max(p.latency_s for p in predictions)
    print(f"\nLatency — avg: {avg_latency:.2f}s, max: {max_latency:.2f}s, n={len(predictions)}")

    misses = [p for p in predictions if p.expected != p.predicted]
    print(f"\n{len(misses)}/{len(predictions)} MISCLASSIFIED:")
    for p in misses:
        flag = " [degraded]" if p.degraded else ""
        print(f"  [{p.lang}]{flag} expected={p.expected:<12} → predicted={p.predicted:<12} | {p.text[:70]}")
        if p.explanation:
            print(f"      explanation: {p.explanation[:100]}")


def _print_classification_block(predictions: list[Prediction]) -> None:
    y_true = [p.expected for p in predictions]
    y_pred = [p.predicted for p in predictions]
    labels = sorted(set(y_true) | set(y_pred))

    print(classification_report(y_true, y_pred, labels=labels, zero_division=0))
    print("CONFUSION MATRIX  (rows = expected, columns = predicted)")
    cm = confusion_matrix(y_true, y_pred, labels=labels)
    header = "              " + "".join(f"{l:>14}" for l in labels)
    print(header)
    for label, row_counts in zip(labels, cm):
        print(f"{label:<14}" + "".join(f"{c:>14}" for c in row_counts))


def _summary(predictions: list[Prediction]) -> dict:
    clean = [p for p in predictions if not p.degraded] or predictions
    y_true = [p.expected for p in clean]
    y_pred = [p.predicted for p in clean]
    return {
        "accuracy":  accuracy_score(y_true, y_pred),
        "macro_f1":  f1_score(y_true, y_pred, average="macro", zero_division=0),
        "avg_latency": sum(p.latency_s for p in predictions) / len(predictions),
        "degraded_pct": sum(1 for p in predictions if p.degraded) / len(predictions),
        "avg_tokens": sum(p.total_tokens for p in predictions) / len(predictions) if predictions else 0,
        "total_cost": sum(p.total_cost_usd for p in predictions),
    }


def print_comparison(all_results: dict[str, list[Prediction]]) -> None:
    print("\n" + "=" * 70)
    print("EXTRACTOR COMPARISON  (accuracy/F1 computed on CLEAN rows only)")
    print("=" * 70)
    print(f"{'extractor/mode':<16}{'accuracy':>12}{'macro F1':>12}{'avg latency':>15}{'degraded %':>13}{'avg tokens':>13}{'total cost':>13}")
    degraded_pcts = []
    for name, predictions in all_results.items():
        s = _summary(predictions)
        degraded_pcts.append(s["degraded_pct"])
        print(
            f"{name:<16}{s['accuracy']:>12.2%}{s['macro_f1']:>12.2%}"
            f"{s['avg_latency']:>14.2f}s{s['degraded_pct']:>13.1%}"
            f"{s.get('avg_tokens', 0):>13.0f}{s.get('total_cost', 0):>13.4f}$"
        )

    if len(set(round(p, 1) for p in degraded_pcts)) > 1:
        print(
            "\n⚠️  Degraded %% differs noticeably between runs — likely because\n"
            "    running extractors back-to-back in one process means the later\n"
            "    run has less API/LLM quota left over. This comparison may not\n"
            "    be apples-to-apples; consider running each extractor separately\n"
            "    (e.g. on different days, after quota resets) for a fair test."
        )

def main() -> None:
    _initialize_pipeline()
    
    args = [arg for arg in sys.argv[1:] if not arg.startswith("--")]
    dataset_path = Path(args[0]) if len(args) > 0 else DEFAULT_DATASET
    which = args[1] if len(args) > 1 else "both"
    
    run_all = "--all" in sys.argv
    use_agent_flags = [False, True] if run_all else ["--agent" in sys.argv]
    
    extractors_to_run = EXTRACTORS if which == "both" else {which: EXTRACTORS[which]}

    all_results: dict[str, list[Prediction]] = {}

    # Single event loop for the whole benchmark run — avoids the
    # "Event loop is closed" error from httpx on the second asyncio.run() call
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    try:
        for use_agent in use_agent_flags:
            for name, extractor_fn in extractors_to_run.items():
                run_name = f"{name} (agent)" if use_agent else f"{name} (traditional)"
                print(f"\n{'#' * 70}\n# RUNNING WITH EXTRACTOR: {run_name}\n{'#' * 70}")
                with _use_extractor(extractor_fn):
                    predictions = loop.run_until_complete(run_benchmark(dataset_path, use_agent=use_agent))
                report(predictions)
                all_results[run_name] = predictions
    finally:
        # Give httpx a chance to close connections cleanly before the loop dies
        loop.run_until_complete(asyncio.sleep(0.25))
        loop.close()

    if len(all_results) > 1:
        print_comparison(all_results)
if __name__ == "__main__":
    main()