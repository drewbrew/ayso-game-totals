"""Load all the ref data and output a CSV of official info

To use this script:

1.  Log in to stack officials as a referee
2.  In your browser, open the dev tools (usually the F12 key)
3.  Go to the network tab of the dev tools
4.  Type games?page in the filter box to only get the queries for listing games
5.  Go to the games tab in the nav bar
6.  Select the event (e.g. AYSO 5C)
7.  Wait for the page to load. You should see two new queries in the network requests,
    one with a status code of 204 (No Content) and one of 200 (OK). Right-click on the
    one that has the 200 response and choose Copy -> Copy Response
8.  Paste that data into a text file and save it in this directory as games1.json
9.  Repeat for each additional page.
10. Run this script: python load_data.py --help
11. Select your mode or just dump it to a CSV using the various switches
    presented to you (replacing --help with --basic, for example)


All data is parsed as it is entered in AYSO Area 5C (North Alabama); your
particular administrator may enter things differently, at which point things
are pretty much guaranteed to implode. Good luck!
"""

import argparse
from decimal import Decimal
import json
import datetime
from collections import defaultdict
from csv import DictWriter
from pathlib import Path
from typing import Any


BASE_DIR = Path(__file__).parent

RoleType = dict[str, int]
LevelType = dict[str, RoleType]
RegularSeasonOrTourneyType = dict[str, LevelType]
UserTotalsType = dict[str, RegularSeasonOrTourneyType]


GAME_MINUTES = {
    "07U": Decimal(40),
    "08U": Decimal(40),
    "09U": Decimal(50),
    "10U": Decimal(50),
    "12U": Decimal(60),
    "14U": Decimal(70),
    "15U": Decimal(80),
    "16U": Decimal(80),
    "18U": Decimal(90),
    "19U": Decimal(90),
}


def load_games(game_path: Path = BASE_DIR) -> dict[str, dict[str, Any]]:
    games = {}
    game_assignments = {}
    event_roles = {}
    users = {}
    game_levels = {}
    for game_file in game_path.glob("games*.json"):
        try:
            game_data = json.loads(game_file.read_text())
        except json.JSONDecodeError:
            continue
        if not game_data:
            continue
        for game in game_data["data"]:
            game_id = game["id"]
            if game_id in games:
                raise ValueError(f"Found duplicate game ID {game_id} in {game_file}")
            games[game_id] = game
            game["assignments"] = {}
        for included_info in game_data["included"]:
            if included_info["type"] == "game_assignment":
                game_assignments[included_info["id"]] = included_info
            elif included_info["type"] == "event_role":
                event_roles[included_info["id"]] = included_info
            elif included_info["type"] == "user":
                users[included_info["id"]] = included_info
            elif included_info["type"] == "game_level":
                game_levels[included_info["id"]] = included_info
            else:
                raise ValueError(f"unknown relationship type {included_info['type']}")
    # ok, now we have our games. We need to map the users into the games
    # and also extract division, role, and tournament status
    for game in games.values():
        if game["attributes"]["status"] in {"cancelled", "postponed"}:
            continue
        game_level = game_levels[game["relationships"]["game_level"]["data"]["id"]]
        assignment_ids = [
            assignment["id"]
            for assignment in game["relationships"]["assignments_game"]["data"]
        ]
        division = game_level["attributes"]["game_level"]
        if division == "U8C":
            division = "08UC"
        game["division"] = division
        is_tournament = (
            "tourney" in game_level["attributes"]["schedule_name"].casefold()
        )
        game["is_tournament"] = is_tournament
        for assignment_id in assignment_ids:
            assignment = game_assignments[assignment_id]
            assert assignment["attributes"]["external_game_id"] == game["id"]
            role = game_level["attributes"]["labels"][
                assignment["attributes"]["official_label_col"]
            ]
            if assignment["attributes"]["status"] != "accepted":
                continue
            event_role = event_roles[
                assignment["relationships"]["event_role"]["data"]["id"]
            ]
            user_id = str(event_role["attributes"]["user_id"])
            user = users[user_id]
            try:
                game["refs"].append(
                    {
                        "user_id": user["id"],
                        "first_name": user["attributes"]["first_name"],
                        "last_name": user["attributes"]["last_name"],
                        "role": role,
                    },
                )
            except KeyError:
                game["refs"] = [
                    {
                        "user_id": user["id"],
                        "first_name": user["attributes"]["first_name"],
                        "last_name": user["attributes"]["last_name"],
                        "role": role,
                    },
                ]

    return games


def assemble_totals(
    games: dict[str, dict[str, Any]],
) -> UserTotalsType:
    totals: UserTotalsType = defaultdict(
        lambda: defaultdict(  # regular season or tournament
            lambda: defaultdict(  # division
                lambda: defaultdict(  # role
                    int,
                )
            )
        )
    )
    for game in games.values():
        if not game.get("refs"):
            continue
        division = convert_raw_division_to_age_group(game["division"])[:3]
        season_type = "Tournament" if game["is_tournament"] else "Regular Season"
        for ref in game["refs"]:
            name = " ".join(i.strip() for i in (ref["first_name"], ref["last_name"]))
            role = ref["role"]
            totals[name][season_type][division][role] += 1
    return totals


def get_minutes(totals: UserTotalsType) -> Decimal:
    """Extract how many minutes a referee spent on the pitch

    R = 100%
    AR = 80%
    other roles = 75%
    """
    result = 0

    for event_dict in totals.values():
        for division_label, division_dict in event_dict.items():
            minutes_per_game = GAME_MINUTES[division_label]
            for role, game_count in division_dict.items():
                if is_referee(role):
                    result += minutes_per_game * game_count
                elif "AR" in role:
                    result += minutes_per_game * game_count * 4 / 5
                else:
                    result += minutes_per_game * game_count * 3 / 4
    return round(result)


def basic_score(user_totals: RegularSeasonOrTourneyType) -> int:
    total = 0
    for event_totals in user_totals.values():
        for role_totals in event_totals.values():
            for role_count in role_totals.values():
                total += role_count
    return total


def division_boost_score(user_totals: RegularSeasonOrTourneyType) -> int:
    total = 0
    base_score = {
        "07U": 1,
        "08U": 1,
        "09U": 2,
        "10U": 2,
        "12U": 3,
        "14U": 4,
        "15U": 5,
        "16U": 5,
        "18U": 6,
        "19U": 6,
    }
    for event_totals in user_totals.values():
        for division, role_totals in event_totals.items():
            division = convert_raw_division_to_age_group(division)
            base = base_score[division[:3]]
            for role_count in role_totals.values():
                total += role_count * base
    return total


def convert_raw_division_to_age_group(division: str) -> str:
    if "U" not in division[:3]:
        try:
            year = int(division[:4])
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Unable to parse division {division}") from exc
        now = datetime.datetime.now(tz=datetime.UTC)
        # hACK: guess based on the year
        # TLDR: if it's spring, we can safely subtract the year
        # but if it's fall, we need to add one
        # IOW, if it's spring 2024, 2014 = 10U and 2012 = 12U
        delta = now.year - year
        if now.month >= 8:
            delta += 1
        return f"{delta:0>2}U"
    return division


def division_and_role_boost_score(user_totals: RegularSeasonOrTourneyType) -> int:
    total = 0
    base_score = {
        "07U": 1,
        "08U": 1,
        "09U": 2,
        "10U": 2,
        "12U": 3,
        "14U": 4,
        "15U": 5,
        "16U": 5,
        "18U": 6,
        "19U": 6,
    }
    for event_totals in user_totals.values():
        for division, role_totals in event_totals.items():
            division = convert_raw_division_to_age_group(division)
            base = base_score[division[:3]]
            for role, role_count in role_totals.items():
                role_modifier = 2 if is_referee(role) else 1
                total += role_count * base * role_modifier
    return total


def is_referee(role: str) -> bool:
    """Is the role a referee (rather than AR or 4th official)"""
    return role in {"CR", "Referee"}


def division_tourney_and_role_boost_score(
    user_totals: RegularSeasonOrTourneyType,
) -> int:
    total = 0
    base_score = {
        "07U": 1,
        "08U": 1,
        "09U": 2,
        "10U": 2,
        "12U": 3,
        "14U": 4,
        "15U": 5,
        "16U": 5,
        "18U": 6,
        "19U": 6,
    }
    for season_type, event_totals in user_totals.items():
        tournament_mod = 2 if season_type == "Tournament" else 1
        for division, role_totals in event_totals.items():
            division = convert_raw_division_to_age_group(division)
            base = base_score[division[:3]]
            for role, role_count in role_totals.items():
                role_modifier = 2 if is_referee(role) else 1
                total += role_count * base * role_modifier * tournament_mod
    return total


def format_user_totals_for_spreadsheet(
    user: str,
    totals: RegularSeasonOrTourneyType,
    minutes: Decimal,
    headers: list[str],
) -> list[dict[str, int | str]]:
    base_result = {
        "Name": user,
        "Total minutes": round(minutes, 1),
        "Basic score (1 per game)": basic_score(totals),
        "+1 point per age group": division_boost_score(totals),
        "+1 point per age group * 2 points for centering": division_and_role_boost_score(
            totals
        ),
        "+1 point per age group * 2 points for centering * 2 for tournament": division_tourney_and_role_boost_score(
            totals
        ),
    }
    base_result |= {header: 0 for header in headers if header not in base_result}
    for season_type, season_totals in totals.items():
        for division, division_totals in sorted(season_totals.items()):
            division = convert_raw_division_to_age_group(division)
            for role, role_total in division_totals.items():
                base_result[f"{season_type} {division} {role}"] = role_total
    return base_result


def get_headers_for_spreadsheet(overall_totals: UserTotalsType) -> list[str]:
    base_result = [
        "Name",
        "Basic score (1 per game)",
        "+1 point per age group",
        "+1 point per age group * 2 points for centering",
        "+1 point per age group * 2 points for centering * 2 for tournament",
    ]
    extra_roles = set()
    for totals in overall_totals.values():
        for season_type, season_totals in totals.items():
            for division, division_totals in sorted(season_totals.items()):
                division = convert_raw_division_to_age_group(division)
                for role in division_totals:
                    extra_roles.add(f"{season_type} {division} {role}")
    base_result += sorted(extra_roles)
    return base_result


def dump_to_csv(user_totals: UserTotalsType, csv_path: str):
    headers = get_headers_for_spreadsheet(user_totals)
    user_dict = [
        format_user_totals_for_spreadsheet(
            headers=headers,
            totals=totals,
            user=user,
            minutes=get_minutes(totals=totals),
        )
        for user, totals in user_totals.items()
    ]
    with open(csv_path, "w") as csvfile:
        writer = DictWriter(
            csvfile,
            fieldnames=user_dict[0].keys(),
        )
        writer.writeheader()
        writer.writerows(sorted(user_dict, key=lambda k: k["Name"].casefold()))


def parse_args():
    parser = argparse.ArgumentParser(
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--basic",
        action="store_true",
        help="One point per game scored, regardless of role or division",
    )
    group.add_argument(
        "--division-boost-only",
        action="store_true",
        help="+1 point per increase in age group (8U - 1, 10U - 2, etc.)",
    )
    group.add_argument(
        "--division-and-role-boost",
        action="store_true",
        help="+1 point per increase in age group (8U - 1, 10U - 2, etc.), counts double for Referee",
    )
    group.add_argument(
        "--division-tournament-and-role-boost",
        action="store_true",
        help="+1 point per increase in age group (8U - 1, 10U - 2, etc.), counts double for Referee and double for tournament",
    )
    group.add_argument(
        "--dump-all-modes-to-csv",
        type=str,
        help="Dump all score modes to a CSV for comparison",
        metavar="CSV_FILE",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    games = load_games()
    totals = assemble_totals(games=games)
    if args.dump_all_modes_to_csv:
        dump_to_csv(totals, args.dump_all_modes_to_csv)
        print(f"Saved to {args.dump_all_modes_to_csv}")
        return
    if args.basic:
        score_func = basic_score
    elif args.division_boost_only:
        score_func = division_boost_score
    elif args.division_and_role_boost:
        score_func = division_and_role_boost_score
    elif args.division_tournament_and_role_boost:
        score_func = division_tourney_and_role_boost_score
    scores = {user: score_func(user_totals) for user, user_totals in totals.items()}
    for user, score in sorted(scores.items(), key=lambda k: k[1], reverse=True):
        print(f"{user}: {score}")


if __name__ == "__main__":
    main()
