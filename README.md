# ⚽ Transfermarkt Scraper

Transfermarkt Scraper is a **free and open-source** scraper that gets you **unlimited** detailed Transfermarkt data for free.

## ✨ What Can I Get?

- ⚽ **Every player card on Transfermarkt** — club, contract, agent, position, caps & market value
- 💰 **Market values back to 2004** — every re-rating with the date, the player's age and his club
- 🏆 **900+ competitions across 254 countries** — squads, tables, top scorers, fixtures & results
- 🔁 **The live transfer market** — latest deals, records, rumours, expiring contracts & free agents

## 🥅 Example: A Full Player Card

```json
{
  "id": 28003,
  "name": "Lionel Messi",
  "full_name": "Lionel Andrés Messi Cuccitini",
  "link": "https://www.transfermarkt.com/lionel-messi/profil/spieler/28003",
  "image": "https://img.a.transfermarkt.technology/portrait/big/28003-1771694720.jpg",
  "date_of_birth": "1987-06-24",
  "age": 39,
  "place_of_birth": "Rosario",
  "nationalities": [
    { "id": 9, "name": "Argentina", "code": "ARG" },
    { "id": 157, "name": "Spain", "code": "ESP" }
  ],
  "height_m": 1.7,
  "preferred_foot": "left",
  "position": { "id": 12, "name": "Right Winger", "short_name": "RW", "group": "Striker" },
  "shirt_number": 10,
  "current_club": {
    "id": 69261,
    "name": "Inter Miami CF",
    "link": "https://www.transfermarkt.com/inter-miami-cf/startseite/verein/69261",
    "crest": "https://img.a.transfermarkt.technology/wappen/big/69261.png",
    "country": { "id": 184, "name": "United States", "code": "USA" },
    "is_captain": true,
    "joined": "2023-07-15"
  },
  "national_team": { "id": 3437, "name": "Argentina", "is_current": true },
  "caps": 207,
  "international_goals": 125,
  "contract_until": "2028-12-31",
  "last_contract_extension": "2025-10-23",
  "market_value": {
    "current": { "amount": 15000000, "currency": "EUR", "updated_at": "2026-06-02" },
    "highest": { "amount": 180000000, "currency": "EUR", "updated_at": "2018-05-30" }
  },
  "agent": { "id": 1207, "name": "Relatives", "is_verified": false },
  "outfitter": "adidas",
  "youth_clubs": "Grandoli FC (1992-1995), Newell's Old Boys (1995-2000)"
}
```

*Trimmed for readability.*

## 🚀 Unlimited Free Transfermarkt Data — Get It in 60 Seconds

1️⃣ Clone and install:
```bash
git clone https://github.com/omkarcloud/transfermarkt-scraper
cd transfermarkt-scraper
python -m pip install -r requirements.txt
```

2️⃣ Start the API:
```bash
python run.py
```

3️⃣ Get your first data:
```bash
curl "http://localhost:8000/players/profile?player=28003"
```

```json
{
  "id": 28003,
  "name": "Lionel Messi",
  "link": "https://www.transfermarkt.com/lionel-messi/profil/spieler/28003",
  "date_of_birth": "1987-06-24",
  "age": 39,
  "nationalities": [{ "id": 9, "name": "Argentina", "code": "ARG" }],
  "position": { "id": 12, "name": "Right Winger", "short_name": "RW", "group": "Striker" },
  "shirt_number": 10,
  "current_club": { "id": 69261, "name": "Inter Miami CF", "is_captain": true, "joined": "2023-07-15" },
  "caps": 207,
  "international_goals": 125,
  "contract_until": "2028-12-31",
  "market_value": {
    "current": { "amount": 15000000, "currency": "EUR", "updated_at": "2026-06-02" },
    "highest": { "amount": 180000000, "currency": "EUR", "updated_at": "2018-05-30" }
  },
  "outfitter": "adidas"
}
```

All 51 endpoints are now live at `http://localhost:8000`.

> Transfermarkt answers a direct connection from most networks. If calls time out or come back blocked, set a residential proxy: `TRANSFERMARKT_PROXY=http://user:pass@host:port python run.py`.

## 📚 Endpoints

51 endpoints cover everything you need.

| Endpoint | Path | Returns |
|---|---|---|
| Player Details | `/players/profile` | Everything about one player in a single call |
| Search | `/search` | Players, clubs, competitions, managers & agents at once |
| Advanced Player Search | `/players/search-advanced` | Scouting search with 35 filters |
| Player Market Value | `/players/market-value` | Every value the player was ever rated at |
| Player Transfers | `/players/transfers` | Career moves with clubs, dates and fees |
| Player Stats | `/players/stats` | Goals, assists and minutes per season & competition |
| Player Match Log | `/players/match-log` | Game-by-game numbers for every match played |
| Player National Team | `/players/national-team` | Caps, goals and debut for every national team |
| Player Injuries | `/players/injuries` | Injury history with days out and games missed |
| Player Achievements | `/players/achievements` | Every title and award, with season and club |
| Player Squad Numbers | `/players/squad-numbers` | Shirt number history season by season |
| Player Rumours | `/players/rumours` | Interested clubs and the rumour archive |
| Player Upcoming Matches | `/players/upcoming-matches` | Next club and country fixtures with kick-off times |
| Club Details | `/clubs/profile` | Squad value, stadium, transfer record and top signings |
| Club Squad | `/clubs/squad` | Every player with contract, height, foot and value |
| Club Transfers | `/clubs/transfers` | Arrivals and departures of a season with fees |
| Club Fixtures & Results | `/clubs/fixtures` | Every match of a season across all competitions |
| Club Squad Statistics | `/clubs/squad-stats` | Per-player appearances, goals, cards and minutes |
| Club Staff | `/clubs/staff` | Coaches, board, scouting and medical departments |
| Club Stadium | `/clubs/stadium` | Capacity, pitch, address, owner and naming rights |
| Club Achievements & History | `/clubs/achievements`, `/clubs/history` | Titles won and season-by-season placements |
| Club Transfer Records | `/clubs/transfer-records` | Record signings and sales ranked by fee |
| Club National Players | `/clubs/national-players` | Internationals in the squad with caps and goals |
| List Competitions | `/competitions/list` | Leagues and cups of a continent by tier |
| Competition Details | `/competitions/overview` | Champion, record holder, values and every club |
| Competition Standings | `/competitions/standings` | The league table with qualification zones |
| Competition Top Scorers | `/competitions/top-scorers` | Goalscorer charts with assists and minutes |
| Competition Matchday | `/competitions/matchday` | Every match of a round with scores |
| Competition Fixtures | `/competitions/fixtures` | The full season schedule round by round |
| Competition Transfers | `/competitions/transfers` | Every club's deals plus spend and balance |
| Competition Market Values | `/competitions/market-values`, `/competitions/club-values` | Most valuable players and clubs of a league |
| Competition Rumours | `/competitions/rumours` | Live rumours around a league's clubs |
| Match Details | `/matches/details` | Full report: goals, cards, subs, referee, attendance |
| Match Line-ups & Statistics | `/matches/lineups`, `/matches/statistics` | Starting XI, bench, possession, shots and passes |
| Manager Details | `/managers/profile` | Current club, licence, formation and club history |
| Manager Career & Titles | `/managers/career`, `/managers/achievements` | Every station with W/D/L, and every title won |
| Latest Transfers | `/transfers/latest` | The newest deals worldwide with fees |
| Transfer Records | `/transfers/records` | The most expensive transfers ever, or per season |
| Latest Rumours | `/transfers/rumours` | The newest rumours worldwide |
| Most Valuable Players / Clubs | `/players/most-valuable`, `/clubs/most-valuable` | The world's most valuable players and squads |
| Market Value Changes | `/players/market-value-changes` | Players just re-rated, with the new value |
| Contracts Expiring | `/players/contracts-expiring` | Players running out of contract in any year |
| Free Agents | `/players/free-agents` | Players without a club and free to sign |
| FIFA World Ranking | `/rankings/fifa` | The ranking with squad value and points |
| Countries & Positions | `/helpers/countries`, `/helpers/positions` | The IDs and codes every filter accepts |

## 🔍 Exploring Parameters

The same API is published on RapidAPI, and its playground is the easiest place to try parameters and see raw responses. Once a request looks right, run it locally for **unlimited free** data.

1. [Subscribe to the free plan](https://rapidapi.com/OmkarCloud/api/transfermarkt-scraper-free-1000-calls/pricing) — 1,000 calls/month, no credit card.
2. [Try the endpoints in the playground](https://rapidapi.com/OmkarCloud/api/transfermarkt-scraper-free-1000-calls/playground) — every param is pre-filled, so you see real data in one click.
3. Copy the generated code and replace `https://transfermarkt-scraper-free-1000-calls.p.rapidapi.com` with `http://localhost:8000`. It will now run against your local API.

```python
import requests

# generated by the playground, host swapped for the local API
response = requests.get(
    "http://localhost:8000/players/profile",
    params={"player": "28003"},
)
print(response.json())
```

## 💬 Have Questions? We Have Answers.

You're a developer — we know how hard completing a project can be. So we offer full support: just message us and we'll reply ✅ with a solution within 1 working day.

[![Message Us on WhatsApp about Transfermarkt Scraper](https://raw.githubusercontent.com/omkarcloud/assets/master/images/whatsapp-us.png)](https://api.whatsapp.com/send?phone=918178804274&text=I%20need%20help%20using%20the%20Transfermarkt%20Scraper%20API.)

[![Ask Us by Email about Transfermarkt Scraper](https://raw.githubusercontent.com/omkarcloud/assets/master/images/ask-on-email.png)](mailto:happy.to.help@omkar.cloud?subject=Help%20with%20Transfermarkt%20Scraper%20API&body=I%20need%20help%20using%20the%20Transfermarkt%20Scraper%20API.)

## ⚡ Popular Scrapers by Omkar Cloud

- [**Google Maps Scraper (3,100+ GitHub Stars)**](https://github.com/omkarcloud/google-maps-scraper) — type "dentists in New York", get every business as a ready-to-call lead list: phones, emails, websites & reviews. Up to 100K free leads/month.
- [**IMDb Scraper**](https://github.com/omkarcloud/imdb-scraper) — free and open-source: titles, ratings, cast, box office & charts
- [**G2 Scraper**](https://www.omkar.cloud/tools/g2-scraper) — G2 product details, ratings & AI-found contacts
- [**Website Email Contact Scraper**](https://www.omkar.cloud/tools/website-email-contact-scraper) — emails, phones & socials from any website
- [**AliExpress Scraper**](https://www.omkar.cloud/tools/aliexpress-scraper) — live product details, SKU variants, stock & shipping
- [**Booking Scraper**](https://www.omkar.cloud/tools/booking-scraper) — Booking.com hotels: prices, ratings, rooms & amenities

## ⭐ Love It? [Star It ⭐!](https://github.com/omkarcloud/transfermarkt-scraper)

Star the repo ⭐ and become my star hero!

It's just 1 click, but it means the world to me.

[![Star us on GitHub](https://raw.githubusercontent.com/omkarcloud/google-maps-scraper/master/screenshots/star-us.png)](https://github.com/omkarcloud/transfermarkt-scraper)
