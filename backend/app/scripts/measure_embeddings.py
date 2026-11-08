"""Compare local embedding models on YAAP's own data.

    python -m app.scripts.measure_embeddings --db data/yaap.db --queries data/embedding_queries.json
        --model multilingual-e5-small=<folder> ...

Reads the chunks of the search index (read-only), embeds them with each model, and runs the queries of a queries
file. The queries name files and conversations of one installation, so they are kept with its data; the repository
holds `embedding_queries.example.json` to start from. Per model it reports, by query kind (Dutch to English, Dutch
to Dutch, ...), how often the expected source is the best hit and in the top five, and how far the hits are from
where unrelated queries start: a query is "separable" when its right hit is closer than the best hit of every
unrelated query, so a cutoff can keep it while dropping noise.
"""

import argparse
import json
import sqlite3
import statistics
import time
from collections import defaultdict
from pathlib import Path

import numpy as np

from app.core.embeddings import MODELS, OnnxEmbedder

EXAMPLE_QUERIES = Path(__file__).with_name("embedding_queries.example.json")


def load_chunks(database: Path) -> tuple[list[str], list[str]]:
    connection = sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)
    rows = connection.execute(
        """
        SELECT search_chunks.text,
               CASE search_chunks.kind
                 WHEN 'message' THEN 'conversation:' || messages.conversation_id
                 WHEN 'memory' THEN 'memory'
                 ELSE 'path:' || search_chunks.path
               END
        FROM search_chunks LEFT JOIN messages ON messages.id = search_chunks.message_id
        WHERE length(trim(search_chunks.text)) > 0
        """
    ).fetchall()
    return [row[0] for row in rows], [row[1] for row in rows]


def target_keys(targets: list[object]) -> set[str]:
    keys = set()
    for target in targets:
        if isinstance(target, int):
            keys.add(f"conversation:{target}")
        elif target == "memory":
            keys.add("memory")
        else:
            keys.add(f"path:{target}")
    return keys


def measure(name: str, folder: Path, texts: list[str], sources: list[str], queries: list[dict]) -> dict:
    embedder = OnnxEmbedder(folder, MODELS[name])
    started = time.perf_counter()
    passages = np.stack(embedder.embed(texts, "passage").vectors)
    seconds = time.perf_counter() - started
    query_vectors = np.stack(embedder.embed([query["q"] for query in queries], "query").vectors)
    distances = (1.0 - query_vectors @ passages.T).astype(float).tolist()

    results = []
    for query, row in zip(queries, distances, strict=True):
        best_per_source: dict[str, float] = {}
        for source, distance in zip(sources, row, strict=True):
            best_per_source[source] = min(distance, best_per_source.get(source, 2.0))
        ranked = sorted(best_per_source, key=best_per_source.__getitem__)
        wanted = target_keys(query["targets"])
        rank = next((index + 1 for index, source in enumerate(ranked) if source in wanted), None)
        right = min((best_per_source[source] for source in wanted if source in best_per_source), default=None)
        results.append({**query, "rank": rank, "right": right, "best": best_per_source[ranked[0]], "top": ranked[0]})

    noise = [result["best"] for result in results if result["kind"] == "noise"]
    floor = min(noise)
    by_kind: dict[str, list[dict]] = defaultdict(list)
    for result in results:
        if result["kind"] != "noise":
            by_kind[result["kind"]].append(result)
    summary = {
        "model": name,
        "seconds_for_passages": round(seconds, 1),
        "noise_floor": round(floor, 3),
        "noise_median": round(statistics.median(noise), 3),
        "kinds": {},
    }
    for kind, items in sorted(by_kind.items()):
        summary["kinds"][kind] = {
            "queries": len(items),
            "top1": sum(1 for item in items if item["rank"] == 1),
            "top5": sum(1 for item in items if item["rank"] is not None and item["rank"] <= 5),
            "separable": sum(1 for item in items if item["right"] is not None and item["right"] < floor),
            "median_right": round(statistics.median(item["right"] for item in items if item["right"] is not None), 3),
        }
    summary["details"] = [
        {"q": item["q"], "rank": item["rank"], "right": None if item["right"] is None else round(float(item["right"]), 3),
         "best": round(float(item["best"]), 3), "top": item["top"]}
        for item in results
    ]
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--model", action="append", required=True, help="name=folder, name one of " + ", ".join(MODELS))
    parser.add_argument("--queries", type=Path, default=EXAMPLE_QUERIES, help="the queries file (default: the example)")
    parser.add_argument("--details", action="store_true")
    arguments = parser.parse_args()
    texts, sources = load_chunks(arguments.db)
    queries = json.loads(arguments.queries.read_text(encoding="utf-8"))["queries"]
    print(f"{len(texts)} chunks, {len(queries)} queries")
    for option in arguments.model:
        name, folder = option.split("=", 1)
        summary = measure(name, Path(folder), texts, sources, queries)
        details = summary.pop("details")
        print(json.dumps(summary, indent=2))
        if arguments.details:
            for item in details:
                print(f"  {item['rank']!s:>4} right={item['right']} best={item['best']}  {item['q'][:50]}  -> {item['top']}")


if __name__ == "__main__":
    main()
