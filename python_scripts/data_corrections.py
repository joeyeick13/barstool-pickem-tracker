from common import PICKS_FILE, load_json, now_iso, save_json


def norm(value):
    return str(value or "").strip().lower()


def week_num(pick):
    try:
        return int(pick.get("week") or 0)
    except Exception:
        return 0


def make_manual_pick(
    *,
    picker,
    week,
    selection,
    bet_type,
    side,
    line,
    team=None,
    opponent=None,
    matchup=None,
    event_id=None,
    source_post_id=None,
    note=None,
):
    timestamp = now_iso()

    return {
        "picker": picker,
        "sport": "CFB",
        "matchup": matchup,
        "team": team,
        "opponent": opponent,
        "bet_type": bet_type,
        "selection": selection,
        "side": side,
        "line": line,
        "odds": None,
        "units": 1.0,
        "mortal_lock": False,
        "week": week,
        "added_pick": True,
        "confidence": 1.0,
        "status": "OPEN",
        "result": None,
        "profit_units": 0,
        "source_post_id": source_post_id,
        "source_url": (
            f"https://x.com/barstoolpickem/status/{source_post_id}"
            if source_post_id
            else None
        ),
        "source_text": "Verified Barstool Pick Em added pick",
        "source_is_reply": False,
        "conversation_id": None,
        "posted_at": None,
        "graded_at": None,
        "final_score": None,
        "event_id": event_id,
        "official_reconciled": False,
        "verified_correction": True,
        "verified_correction_note": note,
        "verified_correction_at": timestamp,
        "game_match_status": (
            "MATCHED"
            if event_id
            else "REVIEW"
        ),
        "game_match_source": (
            "VERIFIED_CORRECTION"
            if event_id
            else None
        ),
        "game_match_confidence": (
            "EXACT"
            if event_id
            else "NONE"
        ),
    }


def pick_exists(
    picks,
    *,
    picker,
    week,
    selection,
):
    wanted = norm(selection)

    return any(
        pick.get("picker") == picker
        and week_num(pick) == week
        and norm(pick.get("selection")) == wanted
        for pick in picks
    )


def apply_verified_corrections():
    picks = load_json(PICKS_FILE, [])

    changed = False

    print()
    print(
        "========== VERIFIED DATA CORRECTIONS =========="
    )

    # =========================================================
    # 1. RICO BOSCO
    # Remove duplicate Week 2 Army -3.5.
    # Keep earliest/original occurrence.
    # =========================================================

    rico_army = [
        pick
        for pick in picks
        if (
            pick.get("picker") == "Rico Bosco"
            and week_num(pick) == 2
            and norm(pick.get("selection"))
            == "army -3.5"
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

        duplicate_objects = {
            id(pick)
            for pick in rico_army[1:]
        }

        picks = [
            pick
            for pick in picks
            if id(pick) not in duplicate_objects
        ]

        changed = True

        print(
            "REMOVED RICO DUPLICATE:",
            "Army -3.5",
            "| kept:",
            keep.get("source_post_id")
            or keep.get("id"),
        )

    else:
        print(
            "RICO DUPLICATE CHECK:",
            "Army -3.5 already appears once"
        )

    # =========================================================
    # 2. DAVE / STOOL PRESIDENTE
    # Correct pick:
    # UConn +12.5
    # =========================================================

    dave_correct = False

    for pick in picks:

        if (
            pick.get("picker")
            == "Stool Presidente"
            and week_num(pick) == 2
            and (
                norm(pick.get("selection"))
                == "uconn +2.5"
                or norm(pick.get("selection"))
                == "uconn +12.5"
            )
        ):

            before = pick.get("selection")

            pick["selection"] = "UConn +12.5"
            pick["bet_type"] = "SPREAD"
            pick["team"] = "UConn"
            pick["side"] = "UConn"
            pick["opponent"] = "Maryland"
            pick["matchup"] = "Maryland @ UConn"
            pick["line"] = 12.5
            pick["event_id"] = "401858444"

            pick["game_match_status"] = "MATCHED"
            pick["game_match_source"] = (
                "VERIFIED_CORRECTION"
            )
            pick["game_match_confidence"] = "EXACT"

            pick.pop(
                "game_match_review_reason",
                None,
            )

            pick["verified_correction"] = True
            pick["verified_correction_note"] = (
                "Confirmed Dave/Stool Presidente "
                "pick is UConn +12.5."
            )
            pick["verified_correction_at"] = (
                now_iso()
            )

            if before != "UConn +12.5":
                changed = True

                print(
                    "CORRECTED DAVE:",
                    before,
                    "-> UConn +12.5"
                )

            dave_correct = True

    if dave_correct:
        print(
            "DAVE VERIFIED:",
            "UConn +12.5"
        )
    else:
        print(
            "DAVE WARNING:",
            "UConn wager not found"
        )

    # =========================================================
    # 3. BIG CAT UCONN
    # Correct original-card pick:
    # UConn +12.5
    # =========================================================

    bigcat_uconn_correct = False

    for pick in picks:

        if (
            pick.get("picker") == "Big Cat"
            and week_num(pick) == 2
            and (
                norm(pick.get("selection"))
                == "maryland +12.5"
                or norm(pick.get("selection"))
                == "uconn +12.5"
            )
        ):

            before = pick.get("selection")

            pick["selection"] = "UConn +12.5"
            pick["bet_type"] = "SPREAD"
            pick["team"] = "UConn"
            pick["side"] = "UConn"
            pick["opponent"] = "Maryland"
            pick["matchup"] = "Maryland @ UConn"
            pick["line"] = 12.5
            pick["event_id"] = "401858444"

            pick["game_match_status"] = "MATCHED"
            pick["game_match_source"] = (
                "VERIFIED_CORRECTION"
            )
            pick["game_match_confidence"] = "EXACT"

            pick.pop(
                "game_match_review_reason",
                None,
            )

            pick["verified_correction"] = True
            pick["verified_correction_note"] = (
                "Confirmed Big Cat pick is "
                "UConn +12.5."
            )
            pick["verified_correction_at"] = (
                now_iso()
            )

            if before != "UConn +12.5":
                changed = True

                print(
                    "CORRECTED BIG CAT:",
                    before,
                    "-> UConn +12.5"
                )

            bigcat_uconn_correct = True

    if bigcat_uconn_correct:
        print(
            "BIG CAT UCONN VERIFIED:",
            "UConn +12.5"
        )
    else:
        print(
            "BIG CAT UCONN WARNING:",
            "UConn wager not found"
        )

    # =========================================================
    # 4. BIG CAT
    # Southern @ Houston Over 60.5
    # =========================================================

    southern_correct = False

    for pick in picks:

        if (
            pick.get("picker") == "Big Cat"
            and week_num(pick) == 2
            and norm(pick.get("selection"))
            == "over 60.5"
        ):

            pick["matchup"] = (
                "Southern @ Houston"
            )

            pick["bet_type"] = "TOTAL"
            pick["side"] = "OVER"
            pick["line"] = 60.5

            pick["event_id"] = "401856787"

            pick["game_match_status"] = "MATCHED"
            pick["game_match_source"] = (
                "VERIFIED_CORRECTION"
            )
            pick["game_match_confidence"] = "EXACT"

            pick.pop(
                "game_match_review_reason",
                None,
            )

            pick["verified_correction"] = True
            pick["verified_correction_note"] = (
                "Confirmed Over 60.5 is "
                "Southern @ Houston."
            )
            pick["verified_correction_at"] = (
                now_iso()
            )

            southern_correct = True

    if southern_correct:
        print(
            "BIG CAT TOTAL VERIFIED:",
            "Southern @ Houston Over 60.5",
            "| event 401856787"
        )
    else:
        print(
            "BIG CAT TOTAL WARNING:",
            "Over 60.5 not found"
        )

    # =========================================================
    # 5. BIG CAT MISSING ADDS
    #
    # Verified from the official
    # @barstoolpickem "Adds for @BarstoolBigCat"
    # post.
    #
    # These are only inserted if they do not already exist.
    # This makes the correction safe to run repeatedly.
    # =========================================================

    missing_bigcat_adds = [
        {
            "selection":
                "Grambling/TCU Over 54.5",
            "bet_type":
                "TOTAL",
            "side":
                "OVER",
            "line":
                54.5,
            "team":
                None,
            "opponent":
                None,
            "matchup":
                "Grambling @ TCU",
        },
        {
            "selection":
                "Prairie View/Baylor Over 55.5",
            "bet_type":
                "TOTAL",
            "side":
                "OVER",
            "line":
                55.5,
            "team":
                None,
            "opponent":
                None,
            "matchup":
                "Prairie View @ Baylor",
        },
        {
            "selection":
                "Purdue +3",
            "bet_type":
                "SPREAD",
            "side":
                "Purdue",
            "line":
                3.0,
            "team":
                "Purdue",
            "opponent":
                "Wake Forest",
            "matchup":
                "Purdue @ Wake Forest",
        },
        {
            "selection":
                "Oregon State +25.5",
            "bet_type":
                "SPREAD",
            "side":
                "Oregon State",
            "line":
                25.5,
            "team":
                "Oregon State",
            "opponent":
                None,
            "matchup":
                None,
        },
    ]

    added_missing = 0

    for wager in missing_bigcat_adds:

        if pick_exists(
            picks,
            picker="Big Cat",
            week=2,
            selection=wager["selection"],
        ):
            print(
                "BIG CAT ADD ALREADY PRESENT:",
                wager["selection"],
            )
            continue

        new_pick = make_manual_pick(
            picker="Big Cat",
            week=2,
            selection=wager["selection"],
            bet_type=wager["bet_type"],
            side=wager["side"],
            line=wager["line"],
            team=wager["team"],
            opponent=wager["opponent"],
            matchup=wager["matchup"],
            event_id=None,
            source_post_id=(
                "2098612140789661909"
            ),
            note=(
                "Verified missing Big Cat added "
                "pick from official Barstool "
                "Pick Em post."
            ),
        )

        picks.append(
            new_pick
        )

        changed = True
        added_missing += 1

        print(
            "ADDED VERIFIED BIG CAT PICK:",
            wager["selection"],
        )

    # =========================================================
    # 6. WEEK 2 BIG CAT COUNT AUDIT
    # =========================================================

    bigcat_week2 = [
        pick
        for pick in picks
        if (
            pick.get("picker") == "Big Cat"
            and week_num(pick) == 2
        )
    ]

    print()
    print(
        "BIG CAT WEEK 2 VERIFIED COUNT:",
        len(bigcat_week2),
        "/ 34"
    )

    if len(bigcat_week2) == 34:
        print(
            "BIG CAT WEEK 2 COUNT IS COMPLETE"
        )
    else:
        print(
            "WARNING — BIG CAT WEEK 2 COUNT "
            "DOES NOT EQUAL 34"
        )

    # =========================================================
    # SAVE
    # =========================================================

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
        "New missing Big Cat picks inserted:",
        added_missing,
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
