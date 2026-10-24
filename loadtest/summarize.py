"""Итоги нагрузочного прогона в виде строки таблицы для docs/slo.md.

    python loadtest/summarize.py loadtest/out/50x1 [loadtest/out/100x3 ...]

Читает файлы Locust (<префикс>_stats.csv, <префикс>_stats_history.csv) и, если METRICS_URL был задан,
<префикс>_queue.csv с глубиной очереди этапов. Печатает: сколько заявок, за сколько получены все решения,
p50/p95/максимум времени «отправка → решение», ответы API (опрос статуса и запись в пик подачи)
и наибольшую очередь этапов.
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

PIPELINE = "отправка → решение (все заявки)"


def _seconds(ms: str) -> str:
    return f"{float(ms) / 1000:.1f}"


def summarize(prefix: str) -> dict:
    stats = list(csv.DictReader(open(f"{prefix}_stats.csv", encoding="utf-8")))
    pipeline = next((r for r in stats if r["Name"] == PIPELINE), None)
    if pipeline is None:
        raise SystemExit(f"{prefix}: в отчёте нет строки «{PIPELINE}» — ни одна заявка не дошла до решения")
    api = [r for r in stats if r["Type"] in ("GET", "POST", "PATCH")]
    api_count = sum(int(r["Request Count"]) for r in api)
    api_fail = sum(int(r["Failure Count"]) for r in api)
    # опрос статуса — то, что кабинет делает всё время обработки; запись — черновик, подпись и отправка,
    # которые все пользователи делают одновременно в первые секунды прогона (пик)
    polls = [r for r in api if r["Type"] == "GET"]
    writes = [r for r in api if r["Type"] != "GET"]
    poll_p50 = max((float(r["50%"]) for r in polls), default=0.0)
    poll_p95 = max((float(r["95%"]) for r in polls), default=0.0)
    write_p95 = max((float(r["95%"]) for r in writes), default=0.0)
    history = list(csv.DictReader(open(f"{prefix}_stats_history.csv", encoding="utf-8")))
    rows = [r for r in history if r["Name"] == "Aggregated" and int(r["Total Request Count"]) > 0]
    wall = int(rows[-1]["Timestamp"]) - int(rows[0]["Timestamp"]) + 1 if rows else 0
    users = max(int(r["User Count"]) for r in history) if history else 0
    queue = Path(f"{prefix}_queue.csv")
    max_pending = ""
    if queue.exists():
        samples = list(csv.DictReader(open(queue, encoding="utf-8")))
        max_pending = str(max((int(s["pending"]) for s in samples), default=0))
    outcomes = {
        r["Name"].split("[", 1)[1].rstrip("]"): int(r["Request Count"])
        for r in stats
        if r["Type"] == "конвейер" and "[" in r["Name"]
    }
    return {
        "prefix": prefix,
        "users": users,
        "applications": int(pipeline["Request Count"]),
        "wall_s": wall,
        "p50_s": _seconds(pipeline["50%"]),
        "p95_s": _seconds(pipeline["95%"]),
        "max_s": _seconds(pipeline["100%"]),
        "api_requests": api_count,
        "api_failures": api_fail,
        "poll_p50_ms": f"{poll_p50:.0f}",
        "poll_p95_ms": f"{poll_p95:.0f}",
        "write_p95_ms": f"{write_p95:.0f}",
        "max_pending": max_pending,
        "outcomes": outcomes,
    }


def main() -> None:
    print(
        "| Прогон | Заявок | Все решения за | p50 | p95 | max | Опрос статуса p50 / p95 "
        "| Запись в пик подачи p95 | Ошибок API | Очередь, max |"
    )
    print("|---|---|---|---|---|---|---|---|---|---|")
    for prefix in sys.argv[1:]:
        s = summarize(prefix)
        print(
            f"| {Path(prefix).name} | {s['applications']} | {s['wall_s']} с | {s['p50_s']} с | {s['p95_s']} с "
            f"| {s['max_s']} с | {s['poll_p50_ms']} / {s['poll_p95_ms']} мс | {s['write_p95_ms']} мс "
            f"| {s['api_failures']} из {s['api_requests']} | {s['max_pending'] or '—'} |"
        )
    for prefix in sys.argv[1:]:
        s = summarize(prefix)
        print(f"\n{Path(prefix).name}: исходы — " + ", ".join(f"{k}: {v}" for k, v in sorted(s["outcomes"].items())))


if __name__ == "__main__":
    main()
