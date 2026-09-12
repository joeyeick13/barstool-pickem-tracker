from common import PICKS_FILE, load_json, now_iso, save_json


def norm(value):
    return str(value or "").strip().lower()


def apply_verified_corrections():
    picks = load_json(PICKS_FILE, [])

    changed = False

    print()
    print("========== VERIFIED DATA CORRECTIONS ==========")

    # ============================================================
    # 1. RICO BOSCO
    # Remove duplicate Week 2 Army -3.5.
    # Keep the earliest/original occurrence.
    # ============================================================

    rico_army = [
        pick
        for pick in picks
        if (
            pick.get("picker") == "Rico Bosco"
            and int(pick.get("week") or 0) == 2
            and norm(pick.get("selection")) == "army -3.5"
        )
    ]

    if len(rico_army) > 1:
        rico_army.sort(
            key=lambda pick: (
                pick.get("posted_at") or "",
                pick.get("source_post_id") or "",
                pick.get("id") or "",
            )
        )

        keep = rico_army[0]
        duplicate_ids = {
            id(pick)
            for pick in rico_army[1:]
        }

        picks = [
            pick
            for pick in picks
            if id(pick) not in duplicate_ids
        ]

        changed = True

        print(
            "REMOVED RICO DUPLICATE:",
            "Army -3.5",
            "| kept:",
            keep.get("source_post_id") or keep.get("id"),
        )

    else:
        print(
            "RICO DUPLICATE CHECK:",
            "Army -3.5 already appears once"
        )

    # ============================================================
    # 2. STOOL PRESIDENTE / DAVE
    # Verified pick is UConn +12.5, not UConn +2.5.
    # ============================================================

    dave_fixed = False

    for pick in picks:
        if (
            pick.get("picker") == "Stool Presidente"
            and int(pick.get("week") or 0) == 2
            and (
                norm(pick.get("selection")) == "uconn +2.5"
                or (
                    str(pick.get("event_id") or "")
                    == "401858444"
                    and norm(pick.get("team")) in {
                        "uconn",
                        "connecticut",
                    }
                )
            )
        ):
            before = pick.get("selection")

            pick["selection"] = "UConn +12.5"
            pick["bet_type"] = "SPREAD"
            pick["team"] = "UConn"
            pick["side"] = "UConn"
            pick["opponent"] = "Maryland"
            pick["matchup"] = "UConn @ Maryland"
            pick["line"] = 12.5

            # This is already the correct ESPN game.
            pick["event_id"] = "401858444"

            pick["verified_correction"] = True
            pick["verified_correction_note"] = (
                "Confirmed Dave/Stool Presidente pick is "
                "UConn +12.5."
            )
            pick["verified_correction_at"] = now_iso()

            changed = True
            dave_fixed = True

            print(
                "CORRECTED DAVE:",
                before,
                "-> UConn +12.5"
            )

    if not dave_fixed:
        print(
            "DAVE CHECK:",
            "No incorrect UConn +2.5 row found"
        )

    # ============================================================
    # 3. BIG CAT
    # Verified pick is UConn +12.5, not Maryland +12.5.
    # ============================================================

    bigcat_uconn_fixed = False

    for pick in picks:
        if (
            pick.get("picker") == "Big Cat"
            and int(pick.get("week") or 0) == 2
            and (
                norm(pick.get("selection"))
                == "maryland +12.5"
            )
        ):
            before = pick.get("selection")

            pick["selection"] = "UConn +12.5"
            pick["bet_type"] = "SPREAD"
            pick["team"] = "UConn"
            pick["side"] = "UConn"
            pick["opponent"] = "Maryland"
            pick["matchup"] = "UConn @ Maryland"
            pick["line"] = 12.5

            # Same ESPN game that was already attached.
            pick["event_id"] = "401858444"

            pick["verified_correction"] = True
            pick["verified_correction_note"] = (
                "Confirmed Big Cat pick is "
                "UConn +12.5."
            )
            pick["verified_correction_at"] = now_iso()

            changed = True
            bigcat_uconn_fixed = True

            print(
                "CORRECTED BIG CAT:",
                before,
                "-> UConn +12.5"
            )

    if not bigcat_uconn_fixed:
        print(
            "BIG CAT UCONN CHECK:",
            "No incorrect Maryland +12.5 row found"
        )

    # ============================================================
    # 4. BIG CAT TOTAL
    # Verified matchup is Southern @ Houston Over 60.5.
    #
    # ESPN event:
    #   Southern @ Houston
    #   event_id 401856787
    # ============================================================

    southern_fixed = False

    for pick in picks:
        if (
            pick.get("picker") == "Big Cat"
            and int(pick.get("week") or 0) == 2
            and norm(pick.get("selection")) == "over 60.5"
        ):
            before = pick.get("matchup")

            pick["matchup"] = "Southern @ Houston"
            pick["bet_type"] = "TOTAL"
            pick["side"] = "OVER"
            pick["line"] = 60.5

            # Verified exact ESPN event.
            pick["event_id"] = "401856787"

            pick["game_match_status"] = "MATCHED"
            pick["game_match_source"] = (
                "VERIFIED_CORRECTION"
            )
            pick["game_match_confidence"] = "EXACT"
            pick.pop("game_match_review_reason", None)

            pick["verified_correction"] = True
            pick["verified_correction_note"] = (
                "Confirmed Over 60.5 is "
                "Southern @ Houston."
            )
            pick["verified_correction_at"] = now_iso()

            changed = True
            southern_fixed = True

            print(
                "CORRECTED BIG CAT TOTAL:",
                before,
                "-> Southern @ Houston | "
                "Over 60.5 | event 401856787"
            )

    if not southern_fixed:
        print(
            "SOUTHERN/HOUSTON CHECK:",
            "Over 60.5 row not found"
        )

    # ============================================================
    # SAVE
    # ============================================================

    if changed:
        save_json(
            PICKS_FILE,
            picks,
        )

        print(
            "Verified corrections saved."
        )

    else:
        print(
            "No verified corrections needed."
        )

    print(
        "Total stored wagers after corrections:",
        len(picks),
    )

    print(
        "==============================================="
    )

    return picks


if __name__ == "__main__":
    apply_verified_corrections()
