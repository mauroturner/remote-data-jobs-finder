"""SVG charts for the market snapshot, in light and dark variants (requires matplotlib).

Colors follow a validated palette: one blue hue as an ordinal ramp for the share bands (checked for monotone
lightness, adjacent contrast and surface contrast in both modes), neutral gray for skills below every threshold.
"""
from __future__ import annotations

from pathlib import Path

from .market import SENIORITY_ORDER, MarketReport, segmentation, skills_at

THEMES = {
    "light": {
        "surface": "#fcfcfb", "ink": "#0b0b0b", "ink2": "#52514e", "muted": "#898781", "baseline": "#c3c2b7",
        "below": "#d6d5ce", "ramp": ["#86b6ef", "#3987e5", "#184f95"], "single": "#2a78d6",
    },
    "dark": {
        "surface": "#1a1a19", "ink": "#f0efec", "ink2": "#c3c2b7", "muted": "#898781", "baseline": "#383835",
        "below": "#44443f", "ramp": ["#184f95", "#2a78d6", "#86b6ef"], "single": "#3987e5",
    },
}
FONT = ["Segoe UI", "Helvetica Neue", "Arial", "DejaVu Sans", "sans-serif"]


def _setup(theme: dict):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams.update({
        "font.family": "sans-serif", "font.sans-serif": FONT, "font.size": 10,
        "svg.fonttype": "none", "svg.hashsalt": "jobfinder",
        "figure.facecolor": theme["surface"], "axes.facecolor": theme["surface"],
        "text.color": theme["ink"], "axes.labelcolor": theme["ink2"],
        "xtick.color": theme["muted"], "ytick.color": theme["ink"],
    })
    return plt


def _clean_axes(ax, theme: dict) -> None:
    for side in ("top", "right", "bottom"):
        ax.spines[side].set_visible(False)
    ax.spines["left"].set_color(theme["baseline"])
    ax.tick_params(axis="y", length=0)
    ax.tick_params(axis="x", length=0)


def _band_color(share: float, thresholds: list[float], theme: dict) -> str:
    ramp = theme["ramp"][-len(thresholds):] if len(thresholds) <= 3 else theme["ramp"]
    color = theme["below"]
    for t, c in zip(sorted(thresholds), ramp):
        if share >= t:
            color = c
    return color


def _band_labels(thresholds: list[float]) -> list[str]:
    ts = sorted(thresholds)
    labels = [f"{a:.0%}–{b:.0%}" for a, b in zip(ts, ts[1:])] + [f"≥ {ts[-1]:.0%}"]
    return labels


def _save(fig, out_dir: Path, name: str, mode: str) -> Path:
    path = out_dir / f"{name}-{mode}.svg"
    fig.savefig(path, format="svg", metadata={"Date": None}, bbox_inches="tight", pad_inches=0.25)
    return path


def skills_by_role(report: MarketReport, thresholds: list[float], day: str, mode: str, out_dir: Path,
                   min_postings: int = 5, top: int = 10) -> Path:
    theme = THEMES[mode]
    plt = _setup(theme)
    from matplotlib.patches import Patch

    roles = [r for r in report.roles if r.count >= min_postings]
    cols = 2
    rows = (len(roles) + 1 + cols - 1) // cols  # one extra slot for the legend
    fig, axes = plt.subplots(rows, cols, figsize=(11, 2.9 * rows))
    flat = list(axes.flat) if hasattr(axes, "flat") else [axes]
    for ax, role in zip(flat, roles):
        skills = role.all_skills[:top][::-1]
        names = [s for s, _ in skills]
        shares = [v for _, v in skills]
        ax.barh(names, shares, height=0.62, color=[_band_color(v, thresholds, theme) for v in shares])
        for y, v in enumerate(shares):
            ax.text(v + 0.012, y, f"{v:.0%}", va="center", fontsize=8.5, color=theme["ink2"])
        for t in thresholds:
            ax.axvline(t, color=theme["muted"], linewidth=0.8, linestyle=(0, (3, 3)), zorder=0)
        ax.set_xlim(0, 1.08)
        ax.set_xticks([0, 0.25, 0.5, 0.75, 1.0], ["0%", "25%", "50%", "75%", "100%"], fontsize=8)
        ax.set_title(f"{role.name}  ·  {role.count} postings", loc="left", fontsize=11, fontweight="bold",
                     color=theme["ink"], pad=6)
        _clean_axes(ax, theme)
        ax.tick_params(axis="y", labelsize=9)
    legend_ax = flat[len(roles)]
    legend_ax.axis("off")
    handles = [Patch(color=c, label=l) for c, l in zip(theme["ramp"][::-1], _band_labels(thresholds)[::-1])]
    handles.append(Patch(color=theme["below"], label=f"< {min(thresholds):.0%}"))
    legend_ax.legend(handles=handles, title="Share of the role's postings", loc="center left", frameon=False,
                     labelcolor=theme["ink"], title_fontproperties={"weight": "bold"}, fontsize=10)
    legend_ax.text(0.0, 0.08, "Dashed lines: thresholds " + ", ".join(f"{t:.0%}" for t in thresholds) +
                   f".\nTop {top} skills per role. Remote data postings, {day}.", transform=legend_ax.transAxes,
                   fontsize=9, color=theme["ink2"], va="bottom")
    for ax in flat[len(roles) + 1:]:
        ax.axis("off")
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    fig.text(0.01, 0.995, "Most requested skills by data role", ha="left", va="top", fontsize=15,
             fontweight="bold", color=theme["ink"])
    fig.text(0.01, 0.968, f"{report.total} remote data postings, deduplicated · {day}", ha="left", va="top",
             fontsize=10, color=theme["ink2"])
    path = _save(fig, out_dir, "skills-by-role", mode)
    plt.close(fig)
    return path


def skills_per_threshold(report: MarketReport, thresholds: list[float], day: str, mode: str, out_dir: Path,
                         min_postings: int = 5) -> Path:
    theme = THEMES[mode]
    plt = _setup(theme)

    roles = [r for r in report.roles if r.count >= min_postings][::-1]
    ts = sorted(thresholds)
    seg = {row["threshold"]: row for row in segmentation(report, ts, min_postings)}
    fig, ax = plt.subplots(figsize=(10, 0.62 * len(roles) * len(ts) / 2 + 1.6))
    height = 0.8 / len(ts)
    for i, (t, color) in enumerate(zip(ts, theme["ramp"][-len(ts):])):
        counts = [len(skills_at(r, t)) for r in roles]
        # Lowest threshold on top of each group, so groups read 40% -> 50% -> 60% like the legend.
        ys = [y - (i - (len(ts) - 1) / 2) * height for y in range(len(roles))]
        label = f"{t:.0%}  (avg {seg[t]['avg_skills']:.1f} skills, overlap {seg[t]['mean_jaccard']:.2f})"
        ax.barh(ys, counts, height=height * 0.88, color=color, label=label)
        for y, c in zip(ys, counts):
            ax.text(c + 0.1, y, str(c), va="center", fontsize=8.5, color=theme["ink2"])
    ax.set_yticks(range(len(roles)), [f"{r.name} ({r.count})" for r in roles], fontsize=9.5)
    ax.set_xlabel("Skills shared by at least that share of the role's postings", fontsize=9)
    ax.xaxis.grid(True, color=theme["baseline"], linewidth=0.5)
    ax.set_axisbelow(True)
    _clean_axes(ax, theme)
    ax.set_xlim(0, max((len(skills_at(r, ts[0])) for r in roles), default=0) + 1.5)
    ax.legend(title="Threshold", frameon=False, loc="upper right",
              labelcolor=theme["ink"], title_fontproperties={"weight": "bold"}, fontsize=9)
    ax.set_title(f"How many shared skills each threshold keeps per role · {day}", loc="left", fontsize=13,
                 fontweight="bold", color=theme["ink"], pad=10)
    fig.tight_layout()
    path = _save(fig, out_dir, "skills-per-threshold", mode)
    plt.close(fig)
    return path


def seniority(report: MarketReport, day: str, mode: str, out_dir: Path) -> Path:
    theme = THEMES[mode]
    plt = _setup(theme)

    levels = [lvl for lvl in SENIORITY_ORDER if report.by_seniority.get(lvl)][::-1]
    values = [report.by_seniority[lvl] for lvl in levels]
    fig, ax = plt.subplots(figsize=(9, 0.5 * len(levels) + 1.3))
    ax.barh(levels, values, height=0.62, color=theme["single"])
    for y, v in enumerate(values):
        ax.text(v + max(values) * 0.01, y, f"{v}  ({v / report.total:.0%})", va="center", fontsize=9,
                color=theme["ink2"])
    ax.set_xlim(0, max(values) * 1.18)
    ax.set_xticks([])
    _clean_axes(ax, theme)
    ax.set_title(f"Remote data postings by seniority · {report.total} postings · {day}", loc="left", fontsize=13,
                 fontweight="bold", color=theme["ink"], pad=10)
    fig.tight_layout()
    path = _save(fig, out_dir, "seniority", mode)
    plt.close(fig)
    return path


def render_charts(report: MarketReport, out_dir: Path, thresholds: list[float], day: str,
                  min_postings: int = 5) -> list[Path]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for mode in THEMES:
        paths.append(skills_by_role(report, thresholds, day, mode, out_dir, min_postings))
        paths.append(skills_per_threshold(report, thresholds, day, mode, out_dir, min_postings))
        paths.append(seniority(report, day, mode, out_dir))
    return paths
