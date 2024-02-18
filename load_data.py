"""Load all the ref data and output a CSV of official info"""

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any


BASE_DIR = Path(__file__).parent

RoleType = dict[str, int]
LevelType = dict[str, RoleType]
RegularSeasonOrTourneyType = dict[str, LevelType]
UserTotalsType = dict[str, RegularSeasonOrTourneyType]


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
        division = game["division"]
        season_type = "Tournament" if game["is_tournament"] else "Regular Season"
        for ref in game["refs"]:
            name = " ".join(i.strip() for i in (ref["first_name"], ref["last_name"]))
            role = ref["role"]
            totals[name][season_type][division][role] += 1
    return totals


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
            base = base_score[division[:3]]
            for role_count in role_totals.values():
                total += role_count * base
    return total


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
            base = base_score[division[:3]]
            for role, role_count in role_totals.items():
                role_modifier = 2 if role == "CR" else 1
                total += role_count * base * role_modifier
    return total


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
        help="+1 point per increase in age group (8U - 1, 10U - 2, etc.), counds double for CR",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    games = load_games()
    totals = assemble_totals(games=games)
    if args.basic:
        score_func = basic_score
    elif args.division_boost_only:
        score_func = division_boost_score
    elif args.division_and_role_boost:
        score_func = division_and_role_boost_score
    scores = {user: score_func(user_totals) for user, user_totals in totals.items()}
    for user, score in sorted(scores.items(), key=lambda k: k[1], reverse=True):
        print(f"{user}: {score}")


if __name__ == "__main__":
    main()
