"""Render evals.svg at the repository root: passing scenarios across every baseline snapshot.

python3 evals/chart.py
"""

import json
import pathlib

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.ticker import PercentFormatter  # noqa: E402

SNAPSHOTS = pathlib.Path(__file__).resolve().parent / "snapshots"
OUT = SNAPSHOTS.parents[1] / "evals.svg"


def order(path):
    parts = path.stem.split("-")
    return "-".join(parts[1:4]), int(parts[4]) if len(parts) > 4 else 1


def main():
    paths = sorted(SNAPSHOTS.glob("run-*.json"), key=order)
    if not paths:
        raise SystemExit(f"no snapshots in {SNAPSHOTS}; ingest a baseline first")
    totals = [json.loads(p.read_text())["totals"] for p in paths]
    labels = []
    for path in paths:
        day, nth = order(path)
        labels.append(day[5:] + (f" ({nth})" if nth > 1 else ""))

    fig, ax = plt.subplots(figsize=(14.0, 6.0))
    x = range(len(totals))
    rates = [100 * t["clean"] / t["scenarios"] for t in totals]
    ax.plot(x, rates, color="black", marker="D", markersize=7, linewidth=1.5, zorder=3)
    for i, (rate, t) in enumerate(zip(rates, totals)):
        ax.annotate(
            f"{t['clean']}/{t['scenarios']}",
            (i, rate),
            textcoords="offset points",
            xytext=(0, 8),
            ha="center",
            fontsize=7,
        )

    ax.set_ylabel("Passing Scenarios", fontweight="bold")
    ax.set_ylim(0, 101)
    ax.yaxis.set_major_formatter(PercentFormatter(xmax=100, decimals=0))
    ax.set_xticks(list(x), labels, fontsize=8)
    ax.grid(axis="y", color="lightgray", linewidth=0.5)
    for tick_labels in (ax.get_xticklabels(), ax.get_yticklabels()):
        plt.setp(tick_labels, fontweight="bold")
    fig.tight_layout()
    fig.savefig(OUT)
    plt.close(fig)
    print(OUT)


if __name__ == "__main__":
    main()
