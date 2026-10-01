            )
            continue

        current_event_id = event_id(event)
        resolved_event_ids.add(current_event_id)

        if current_event_id not in payload_cache:
            try:
                payload_cache[current_event_id] = odds_client.event_odds(current_event_id)
            except requests.HTTPError as exc:
                response_status = getattr(exc.response, "status_code", "unknown")
                payload_cache[current_event_id] = {
                    "_api_error": f"HTTP_{response_status}"
                }
            except requests.RequestException as exc:
                payload_cache[current_event_id] = {
                    "_api_error": f"{type(exc).__name__}: {exc}"
                }

        payload = payload_cache[current_event_id]

        if payload.get("_api_error"):
            status = "API_ERROR"
            counts[status] += 1
            print(
                f"{status}: {label} | ESPN {current_event_id} | "
                f"{payload['_api_error']}"
            )
            continue

        item = _draftkings_item(payload)

        if item is None:
            status = "NO_DRAFTKINGS_PROVIDER"
            counts[status] += 1
            print(
                f"{status}: {label} | ESPN {current_event_id} | "
                f"{event_matchup_text(event)}"
            )
            print("  available_providers:", _available_providers(payload))
            continue

        draftkings_event_ids.add(current_event_id)

        try:
            status, reason, detail = _audit_pick(item, pick, event)
        except Exception as exc:
            status = "VALIDATOR_ERROR"
            reason = f"{type(exc).__name__}: {exc}"
            detail = None

        corrected = False

        if (
            status == "SIGN_REVIEW"
            and detail
        ):
            corrected = _apply_sign_correction(
                pick,
                event,
                detail,
            )

            if corrected:
                corrected_count += 1
                status = (
                    "SIGN_CORRECTED_WITHIN_3_POINTS"
                )

                reason = (
                    "PROVISIONAL_SPREAD_CORRECTED_"
                    "TO_DRAFTKINGS_SUMMARY"
                )

                detail = dict(
                    detail
                )

                detail[
                    "corrected_selection"
                ] = pick.get(
                    "selection"
                )

                detail[
                    "corrected_line"
                ] = pick.get(
                    "line"
                )

        counts[status] += 1

        print(
            f"{status}: {label} | ESPN {current_event_id} | {reason}"
        )

        if detail:
            print(
                "  ",
                detail,
            )

    print()
    print("-" * 72)
    print("ESPN DRAFTKINGS AUDIT SUMMARY")
    print("-" * 72)
    print(f"Eligible CFB wagers: {len(eligible)}")
    print(f"Unique ESPN events resolved: {len(resolved_event_ids)}")
    print(
        "Unique ESPN events with DraftKings: "
        f"{len(draftkings_event_ids)}"
    )
    for key in sorted(counts):
        print(f"{key}: {counts[key]}")
    print(
        f"Provisional spread sign corrections saved: "
        f"{corrected_count}"
    )

    print("-" * 72)

    if corrected_count:
        save_json(
            PICKS_FILE,
            picks,
        )

        print(
