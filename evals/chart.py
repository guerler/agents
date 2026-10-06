"""Render evals/snapshots/overview.svg: clean scenarios per tier across every snapshot.

python3 evals/chart.py
"""

import json
import pathlib

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.ticker import PercentFormatter  # noqa: E402

SNAPSHOTS = pathlib.Path(__file__).resolve().parent / "snapshots"
TIERS = (("grounded", "black", "D", "-"), ("parity", "gray", ".", ":"))


def order(path):
    parts = path.stem.split("-")
    return "-".join(parts[1:4]), int(parts[4]) if len(parts) > 4 else 1


def main():
    paths = sorted(SNAPSHOTS.glob("run-*.json"), key=order)
    snapshots = [json.loads(p.read_text()) for p in paths]
    labels = []
    for path, snap in zip(paths, snapshots):
        day, nth = order(path)
        label = day[5:] + (f" ({nth})" if nth > 1 else "")
        labels.append(label + (f"\n{snap['build']}" if snap.get("build") else ""))

    fig, ax = plt.subplots(figsize=(14.0, 6.0))
    x = range(len(snapshots))
    rates = {
        tier: [100 * s["tiers"][tier]["clean"] / s["tiers"][tier]["scenarios"] for s in snapshots] for tier, *_ in TIERS
    }
    for tier, color, marker, style in TIERS:
        counts = [s["tiers"][tier] for s in snapshots]
        other = rates["parity" if tier == "grounded" else "grounded"]
        ax.plot(
            x,
            rates[tier],
            color=color,
            marker=marker,
            markersize=7,
            linewidth=1.5,
            linestyle=style,
            label=tier,
            zorder=3,
        )
        for i, (rate, c) in enumerate(zip(rates[tier], counts)):
            ax.annotate(
                f"{c['clean']}/{c['scenarios']}",
                (i, rate),
                textcoords="offset points",
                xytext=(0, 8 if (rate, tier == "grounded") >= (other[i], True) else -14),
                ha="center",
                fontsize=7,
                color=color,
            )

    ax.set_ylabel("Clean scenarios", fontweight="bold")
    ax.set_ylim(50, 101)
    ax.yaxis.set_major_formatter(PercentFormatter(xmax=100, decimals=0))
    ax.set_xticks(list(x), labels, fontsize=8)
    ax.grid(axis="y", color="lightgray", linewidth=0.5)
    ax.legend(loc="lower left", frameon=False)
    for tick_labels in (ax.get_xticklabels(), ax.get_yticklabels()):
        plt.setp(tick_labels, fontweight="bold")
    fig.tight_layout()
    out = SNAPSHOTS / "overview.svg"
    fig.savefig(out)
    plt.close(fig)
    print(out)


if __name__ == "__main__":
    main()
