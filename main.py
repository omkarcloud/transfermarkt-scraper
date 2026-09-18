"""Use the scraper straight from Python — no server needed.

    python main.py

Every function returns the same JSON the API does; results are written to
output/*.json. See README.md → "Endpoints" for the full function list.
"""
import json
import os

from transfermarkt.clubs import get_squad
from transfermarkt.competitions import get_standings
from transfermarkt.players import get_profile

os.makedirs("output", exist_ok=True)


def save(name, data):
    path = os.path.join("output", name)
    with open(path, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"saved {path}")


if __name__ == "__main__":
    # a player id or any transfermarkt.com player link
    save("player_messi.json", get_profile("28003"))

    # every player of a club, with market values (season optional: 2025 = 25/26)
    save("squad_real_madrid.json", get_squad("418", season=2025))

    # the league table of any competition, any season
    save("standings_laliga.json", get_standings("ES1", season=2025))
