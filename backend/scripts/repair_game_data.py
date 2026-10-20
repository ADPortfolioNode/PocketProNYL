"""Remove other games' rows / invalid draws from game collections.

Run from backend/:
    python -m scripts.repair_game_data --game take5 --dry-run
    python -m scripts.repair_game_data --all
"""
import argparse
import json

from services.data_repair import repair_all_games, repair_game_collection


def main():
    parser = argparse.ArgumentParser(description="Repair per-game Chroma collections (idempotent).")
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--game", help="Game key, e.g. take5")
    target.add_argument("--all", action="store_true", help="Repair every configured game")
    parser.add_argument("--dry-run", action="store_true", help="Report what would be removed; delete nothing")
    parser.add_argument("--max-rows", type=int, default=None, help="Skip collections larger than this")
    args = parser.parse_args()

    if args.all:
        result = repair_all_games(dry_run=args.dry_run, max_rows=args.max_rows)
    else:
        result = repair_game_collection(args.game, dry_run=args.dry_run, max_rows=args.max_rows)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
