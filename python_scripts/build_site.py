from __future__ import annotations

from common import (
    load_json,
    save_json,
    PICKERS,
    PICKS_FILE,
    ROOT,
    now_iso,
)


def finalized(p):
    return (
        p.get("status") == "FINAL"
        and p.get("result") in {"WIN", "LOSS", "PUSH"}
    )


def wl(picks):
    wins = sum(
        p.get("result") == "WIN"
        for p in picks
    )

    losses = sum(
        p.get("result") == "LOSS"
        for p in picks
    )

    pushes = sum(
        p.get("result") == "PUSH"
        for p in picks
    )

    win_pct = (
        round(
            wins / (wins + losses) * 100,
            1,
        )
        if wins + losses
        else 0.0
    )

    return (
        wins,
        losses,
        pushes,
        win_pct,
    )


def picker_summary(
    picks,
    picker,
):
    picker_picks = [
        p
        for p in picks
        if p.get("picker") == picker
    ]

    finals = [
        p
        for p in picker_picks
        if finalized(p)
    ]

    wins, losses, pushes, win_pct = (
        wl(finals)
    )

    return {
        "picker": picker,
        "wins": wins,
        "losses": losses,
        "pushes": pushes,
        "win_pct": win_pct,
    }


def week_num(p):
    try:
        return int(
            p.get("week")
            or 0
        )

    except (
        TypeError,
        ValueError,
    ):
        return 0


def cumulative_series(
    picks,
    picker,
):
    picker_picks = [
        p
        for p in picks
        if (
            p.get("picker") == picker
            and finalized(p)
            and week_num(p) > 0
        )
    ]

    max_week = max(
        [
            week_num(p)
            for p in picks
        ]
        or [0]
    )

    output = []
    running = []

    for week in range(
        1,
        max_week + 1,
    ):
        running.extend(
            [
                p
                for p in picker_picks
                if week_num(p) == week
            ]
        )

        wins, losses, _, win_pct = (
            wl(running)
        )

        if wins + losses:
            output.append(
                {
                    "week": week,
                    "win_pct": win_pct,
                }
            )

    return output


def build():
    picks = load_json(
        PICKS_FILE,
        [],
    )

    weeks = sorted(
        {
            week_num(p)
            for p in picks
            if week_num(p) > 0
        }
    )

    payload = {
        "updated_at": now_iso(),

        "cappers": [
            picker_summary(
                picks,
                picker,
            )
            for picker in PICKERS
        ],

        "win_pct_history": {
            picker: cumulative_series(
                picks,
                picker,
            )
            for picker in PICKERS
        },

        "weeks": weeks,

        "picks": sorted(
            picks,
            key=lambda p: (
                week_num(p),
                p.get("posted_at")
                or "",
                p.get("picker")
                or "",
            ),
            reverse=True,
        ),
    }

    save_json(
        ROOT
        / "data"
        / "dashboard.json",
        payload,
    )

    print(
        "dashboard.json rebuilt"
    )


if __name__ == "__main__":
    build()
