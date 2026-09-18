"""Transfermarkt normalizers: server-rendered tables / header blocks and the
tmapi JSON envelopes -> one clean snake_case shape per entity.

Conventions: `link` for canonical page URLs (always absolute), `*_link`
for other URLs, `image` / `crest` / `logo` / `flag` for pictures, `*_count`
for counters, `is_*` booleans, ISO dates (YYYY-MM-DD), numbers as numbers
(money as `{amount, currency}` with the amount in whole euros), null for
missing. Entity refs share one vocabulary everywhere: a player is
{id, name, link, image?, position?}, a club is {id, name, link, crest?}, a
competition is {id, name, link, logo?}, a country is {id, name, code?}.

Money: the site prints compact euros — "€220.00m", "€1.43bn", "€500k",
"€ 145.00 m", "€-189.18m" (negative balances), "€4,123,650,000" (full),
"-" (none). Transfer fees add words: "free transfer", "loan transfer",
"Loan fee: €5.00m", "End of loan", "?" (undisclosed) -> fee() returns the
amount plus a `type`.

Dates: "01/07/2022", "17.09.2026", "Sat 16/08/2025", "Fri, 16/08/24",
"2000-07-21" -> ISO. Ages ride next to birthdays: "21/07/2000 (26)".

Dropped everywhere (noise): the `compact` display triple tmapi ships next
to every value, `relativeUrl` (rebuilt as `link`), `identifier` strings,
`preferences.themeId`, `metadata`, tracking/forum/ad cells, sort links,
duplicated mobile-only short names, "hide-for-small" clones, image size
variants (one canonical size is kept per picture).
"""
import re
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from . import refs

BASE = "https://www.transfermarkt.com"
IMG = "https://img.a.transfermarkt.technology"

_WS_RE = re.compile(r"\s+")
_MONEY_RE = re.compile(r"([€£$])\s*(-?)\s*([\d.,]+)\s*(bn|m|k|mio|mill|tsd|th)?\b", re.I)
_DATE_RE = re.compile(r"(\d{1,2})[./](\d{1,2})[./](\d{2,4})")
_ISO_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
_AGE_RE = re.compile(r"\((\d{1,2})\)")
_ID_IN_PATH = {
    "player": re.compile(r"/spieler/(\d+)"),
    "club": re.compile(r"/verein/(\d+)"),
    "competition": re.compile(r"/(?:pokal)?wettbewerb/([A-Z0-9]{2,8})(?=/|\?|#|$)"),
    "manager": re.compile(r"/trainer/(\d+)"),
    "match": re.compile(r"/spielbericht/(\d+)"),
    "referee": re.compile(r"/schiedsrichter/(\d+)"),
    "agent": re.compile(r"/berater/(\d+)"),
    "stadium": re.compile(r"/stadion/verein/(\d+)"),
}
_COUNTRY_FLAG_RE = re.compile(r"/flagge/[a-z0-9]+/(\d+)\.png")
_CLUB_CREST_RE = re.compile(r"/wappen/[a-z0-9]+/(\d+)(?:_\d+)?\.png")
_SEASON_RE = re.compile(r"\b(\d{2})/(\d{2})\b")
_LONG_SEASON_RE = re.compile(r"\b((?:18|19|20)\d{2})/(\d{2}|\d{4})\b")
_FULL_DATE_RE = re.compile(r"\b\d{1,2}[./]\d{1,2}[./]\d{2,4}\b|\b\d{4}-\d{2}-\d{2}\b")
_YEAR_RE = re.compile(r"\b((?:18|19|20)\d{2})\b")


# ---- text / numbers ------------------------------------------------------------------

def soup(html):
    return BeautifulSoup(html or "", "lxml")


def clean(value):
    """Collapse whitespace; '', '-', '?' and '–' become None."""
    if value is None:
        return None
    value = _WS_RE.sub(" ", str(value).replace("\xa0", " ")).strip()
    if value in ("", "-", "–", "—", "?", "n/a", "N/A"):
        return None
    return value


def text(el):
    return clean(el.get_text(" ", strip=True)) if el is not None else None


def to_int(value):
    """'25' -> 25; '52.640' / '73,297' (thousand separators) -> 52640; None otherwise."""
    value = clean(value)
    if value is None:
        return None
    m = re.search(r"-?\d[\d.,]*", value)
    if not m:
        return None
    digits = m.group(0)
    if re.fullmatch(r"-?\d{1,3}([.,]\d{3})+", digits):
        digits = re.sub(r"[.,]", "", digits)
    elif "," in digits or "." in digits:
        return None
    try:
        return int(digits)
    except ValueError:
        return None


def to_float(value):
    """'25.4' / '25,4' / '2.37' -> float; '72.1 %' -> 72.1."""
    value = clean(value)
    if value is None:
        return None
    m = re.search(r"-?\d+(?:[.,]\d+)?", value)
    if not m:
        return None
    try:
        return float(m.group(0).replace(",", "."))
    except ValueError:
        return None


def _int(value):
    """int(value) or None (tmapi ids arrive as strings)."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def percent(value):
    """'17 68.0 %' -> 68.0 (the number right before the % sign)."""
    value = clean(value)
    if not value or "%" not in value:
        return None
    m = re.search(r"(-?\d+(?:[.,]\d+)?)\s*%", value)
    return float(m.group(1).replace(",", ".")) if m else None


def money(value):
    """'€220.00m' -> (220000000, 'EUR'); '-' -> (None, None)."""
    value = clean(value)
    if value is None:
        return None, None
    m = _MONEY_RE.search(value)
    if not m:
        return None, None
    symbol, sign, number, unit = m.groups()
    number = number.replace(",", "") if "," in number and "." in number else number
    if re.fullmatch(r"\d{1,3}(,\d{3})+", number):
        number = number.replace(",", "")
    elif "," in number and "." not in number:
        number = number.replace(",", ".")
    try:
        amount = float(number)
    except ValueError:
        return None, None
    unit = (unit or "").lower()
    mult = {"bn": 1e9, "m": 1e6, "mio": 1e6, "mill": 1e6, "k": 1e3, "tsd": 1e3, "th": 1e3}.get(unit, 1)
    amount = int(round(amount * mult))
    if sign == "-":
        amount = -amount
    return amount, {"€": "EUR", "£": "GBP", "$": "USD"}.get(symbol, "EUR")


def money_obj(value):
    """'€220.00m' -> {amount, currency} or None when the cell is empty."""
    amount, currency = money(value)
    if amount is None:
        return None
    return {"amount": amount, "currency": currency}


def fee(value):
    """Transfer-fee cell -> {amount, currency, type}; type is one of
    transfer | free | loan | end_of_loan | undisclosed | none."""
    stripped = _WS_RE.sub(" ", str(value or "")).strip()
    if stripped == "?":
        return {"amount": None, "currency": None, "type": "undisclosed"}
    raw = clean(stripped)
    if raw is None:
        return {"amount": None, "currency": None, "type": "none"}
    low = raw.lower()
    amount, currency = money(raw)
    if "end of loan" in low or "loan return" in low:
        kind = "end_of_loan"
    elif "loan" in low:
        kind = "loan"
    elif "free" in low:
        kind, amount, currency = "free", 0, "EUR"
    elif amount is None:
        kind = "undisclosed" if "?" in low or "undisclosed" in low else "none"
    else:
        kind = "transfer"
    return {"amount": amount, "currency": currency, "type": kind}


def iso_date(value):
    """'01/07/2022' | '17.09.2026' | 'Sat 16/08/2025' | 'Fri, 16/08/24' | '2000-07-21' -> ISO."""
    value = clean(value)
    if value is None:
        return None
    m = _ISO_RE.search(value)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    m = _DATE_RE.search(value)
    if not m:
        return None
    day, month, year = int(m.group(1)), int(m.group(2)), m.group(3)
    if len(year) == 2:
        year = 2000 + int(year) if int(year) < 70 else 1900 + int(year)
    year = int(year)
    if not (1 <= month <= 12 and 1 <= day <= 31):
        return None
    return f"{year:04d}-{month:02d}-{day:02d}"


def date_and_age(value):
    """'21/07/2000 (26)' -> ('2000-07-21', 26)."""
    value = clean(value)
    if value is None:
        return None, None
    m = _AGE_RE.search(value)
    return iso_date(value), (int(m.group(1)) if m else None)


def season_obj(value):
    """'25/26' | '2025' | {'id': 2025, 'display': '25/26'} -> {id, name}."""
    if isinstance(value, dict):
        sid = value.get("id")
        if sid in (None, 0, ""):
            return None
        return {"id": int(sid), "name": value.get("display") or refs.season_name(int(sid))}
    value = clean(value)
    if value is None:
        return None
    # "16/17 (01/07/2016)" -> keep the season, never read a date's dd/mm as one
    value = clean(_FULL_DATE_RE.sub(" ", value))
    if value is None:
        return None
    m = _LONG_SEASON_RE.search(value)
    if m:
        start = int(m.group(1))
        return {"id": start, "name": refs.season_name(start)}
    m = _SEASON_RE.search(value)
    if m:
        start = int(m.group(1))
        start = 2000 + start if start < 70 else 1900 + start
        return {"id": start, "name": m.group(0)}
    m = _YEAR_RE.search(value)
    if m:
        year = int(m.group(1))
        return {"id": year, "name": str(year)}
    return None


def height_m(value):
    """'1,95 m' -> 1.95."""
    value = clean(value)
    if not value:
        return None
    m = re.search(r"(\d)[.,](\d{2})", value)
    return float(f"{m.group(1)}.{m.group(2)}") if m else None


def minutes(value):
    """\"3.060'\" -> 3060."""
    value = clean(value)
    if value is None:
        return None
    return to_int(value.replace("'", ""))


# ---- links / ids / images ----------------------------------------------------------

def link(href):
    href = (href or "").strip()
    if not href or href.startswith("#") or href.startswith("javascript"):
        return None
    return urljoin(BASE + "/", href)


def id_in(href, kind):
    """Numeric id (or competition code) of `kind` inside a href, else None."""
    if not href:
        return None
    found = _ID_IN_PATH[kind].findall(href)
    if not found:
        return None
    value = found[-1]   # "/wettbewerb/startseite/wettbewerb/ES1" -> the last code wins
    return value.upper() if kind == "competition" else int(value)


_CONTROLLERS = {"spieler", "verein", "wettbewerb", "pokalwettbewerb", "trainer", "spielbericht",
                "schiedsrichter", "berater", "stadion", "statistik", "transfers", "jumplist", "wettbewerbe"}


def slug_of(href):
    """The SEO slug segment of a site path ('/manchester-city/startseite/verein/281' -> 'manchester-city')."""
    if not href:
        return None
    path = urlparse(href).path if "://" in href else href.split("?")[0]
    parts = [p for p in path.split("/") if p]
    if len(parts) >= 3 and parts[0] not in _CONTROLLERS and parts[0] != "-":
        return parts[0]
    return None


def entity_link(kind, entity_id, slug=None):
    """Canonical page URL for an entity id (slug is optional — the site ignores it)."""
    slug = slug or "-"
    if kind == "player":
        return f"{BASE}/{slug}/profil/spieler/{entity_id}"
    if kind == "club":
        return f"{BASE}/{slug}/startseite/verein/{entity_id}"
    if kind == "competition":
        return f"{BASE}/{slug}/startseite/wettbewerb/{entity_id}"
    if kind == "manager":
        return f"{BASE}/{slug}/profil/trainer/{entity_id}"
    if kind == "match":
        return f"{BASE}/spielbericht/index/spielbericht/{entity_id}"
    if kind == "referee":
        return f"{BASE}/{slug}/profil/schiedsrichter/{entity_id}"
    return None


def image(src, size=None):
    """Absolute picture URL; `size` swaps the site's size segment
    (portrait: small|medium|big|header, wappen: tiny|small|head|big)."""
    src = (src or "").strip()
    if not src or src.startswith("data:"):
        return None
    src = urljoin(BASE + "/", src)
    if size:
        src = re.sub(r"/(portrait|wappen|logo|flagge)/[a-zA-Z0-9]+/", rf"/\1/{size}/", src, count=1)
    return src


def _img_src(img):
    return img.get("data-src") or img.get("src") if img is not None else None


def portrait(img):
    src = _img_src(img)
    return image(src, "medium") if src and "/portrait/" in src else image(src)


def crest(img):
    src = _img_src(img)
    return image(src, "head") if src and "/wappen/" in src else image(src)


def logo(img):
    src = _img_src(img)
    return image(src, "medium") if src and "/logo/" in src else image(src)


def flag_country(img):
    """A flag <img> -> {id, name, code} (name from the title, id from the src)."""
    if img is None:
        return None
    src = _img_src(img) or ""
    m = _COUNTRY_FLAG_RE.search(src)
    cid = int(m.group(1)) if m else None
    row = refs.country(cid) if cid else None
    name = clean(img.get("title") or img.get("alt")) or (row or {}).get("name")
    if not name and cid is None:
        return None
    return {"id": cid, "name": name, "code": (row or {}).get("code")}


def flags(td):
    """Every flag in a cell -> list of countries (dedup, order kept)."""
    out, seen = [], set()
    for img in td.select("img.flaggenrahmen, img[src*='/flagge/'], img[data-src*='/flagge/']") if td is not None else []:
        c = flag_country(img)
        if c and (c["id"], c["name"]) not in seen:
            seen.add((c["id"], c["name"]))
            out.append(c)
    return out


def club_ref(el, name=None):
    """A cell / anchor holding a club link and/or crest -> {id, name, link, crest}."""
    if el is None:
        return None
    a = el if el.name == "a" else el.select_one("a[href*='/verein/']")
    href = a.get("href") if a is not None else None
    cid = id_in(href, "club")
    img = el.select_one("img[src*='/wappen/'], img[data-src*='/wappen/']") if el.name != "img" else el
    if cid is None and img is not None:
        m = _CLUB_CREST_RE.search(_img_src(img) or "")
        cid = int(m.group(1)) if m else None
    title = None
    if img is not None:
        title = clean(img.get("title") or img.get("alt"))
    if a is not None and not title:
        title = clean(a.get("title")) or text(a)
    if title and ":" in title and re.search(r"[€£$]|Abl[öo]se|fee", title.split(":", 1)[1], re.I):
        title = title.split(":", 1)[0].strip()   # "Paris Saint-Germain: Ablöse €30.00m"
    name = name or title
    if cid is None and not name:
        return None
    return {"id": cid, "name": name, "link": entity_link("club", cid, slug_of(href)) if cid else link(href),
            "crest": crest(img)}


def player_ref(el):
    """A cell with the site's inline-table (portrait + name + position) or a bare
    player anchor -> {id, name, link, image, position}."""
    if el is None:
        return None
    a = el.select_one("a[href*='/spieler/']")
    if a is None:
        return None
    pid = id_in(a.get("href"), "player")
    name = clean(a.get("title")) or text(a)
    img = el.select_one("img[src*='/portrait/'], img[data-src*='/portrait/']")
    position = None
    inline = el.select_one("table.inline-table")
    if inline is not None:
        rows = inline.select("tr")
        if len(rows) > 1:
            position = text(rows[1])
    return {"id": pid, "name": name,
            "link": entity_link("player", pid, slug_of(a.get("href"))) if pid else link(a.get("href")),
            "image": portrait(img), "position": position}


def competition_ref(el):
    if el is None:
        return None
    a = el.select_one("a[href*='wettbewerb/']") if el.name != "a" else el
    if a is None:
        return None
    cid = id_in(a.get("href"), "competition")
    img = el.select_one("img[src*='/logo/'], img[data-src*='/logo/']")
    name = clean(a.get("title")) or text(a) or (clean(img.get("title")) if img is not None else None)
    return {"id": cid, "name": name,
            "link": entity_link("competition", cid, slug_of(a.get("href"))) if cid else link(a.get("href")),
            "logo": logo(img)}


def manager_ref(el):
    if el is None:
        return None
    a = el.select_one("a[href*='/trainer/']") if el.name != "a" else el
    if a is None:
        return None
    mid = id_in(a.get("href"), "manager")
    img = el.select_one("img[src*='/portrait/'], img[data-src*='/portrait/']")
    return {"id": mid, "name": clean(a.get("title")) or text(a),
            "link": entity_link("manager", mid, slug_of(a.get("href"))) if mid else link(a.get("href")),
            "image": portrait(img)}


def match_id_in(el):
    a = el.select_one("a[href*='/spielbericht/']") if el is not None else None
    return id_in(a.get("href"), "match") if a is not None else None


# ---- tables ------------------------------------------------------------------------------

def header_labels(table):
    """Header labels of a table: th text, else the title of its icon/sort link."""
    out = []
    for th in table.select("thead th"):
        label = text(th)
        if not label:
            inner = th.select_one("[title]")
            label = clean(inner.get("title")) if inner is not None else None
        out.append(label or "")
    return out


def table_rows(table):
    """Data rows of a table (direct rows, not the nested inline-table rows)."""
    rows = []
    for tr in table.find_all("tr"):
        if tr.find_parent("table") is not table:
            continue
        if tr.find("th") and not tr.find("td"):
            continue
        tds = tr.find_all("td", recursive=False)
        if tds:
            rows.append(tr)
    return rows


def cells(tr):
    return tr.find_all("td", recursive=False)


def items_table(table):
    """A site 'items' table -> list of {header: <td>} dicts (duplicate headers
    get a numeric suffix), one per data row that spans all columns."""
    heads = header_labels(table)
    out = []
    for tr in table_rows(table):
        tds = cells(tr)
        if heads and len(tds) < len(heads) // 2:
            continue   # section / spacer rows
        row, seen = {}, {}
        for i, td in enumerate(tds):
            key = heads[i] if i < len(heads) and heads[i] else f"col{i}"
            if key in row:
                seen[key] = seen.get(key, 1) + 1
                key = f"{key}_{seen[key]}"
            row[key] = td
        out.append(row)
    return out


def find_table(doc, headline=None, css="table.items"):
    """First `css` table, optionally the one inside the box whose headline
    contains `headline` (case-insensitive)."""
    if headline:
        for box in doc.select(".box"):
            h = box.select_one("h2, .content-box-headline")
            if h is not None and headline.lower() in (text(h) or "").lower():
                t = box.select_one(css)
                if t is not None:
                    return t
        return None
    return doc.select_one(css)


def box_by_headline(doc, headline):
    for box in doc.select(".box"):
        h = box.select_one("h2, .content-box-headline, h1")
        if h is not None and headline.lower() in (text(h) or "").lower():
            return box
    return None


def hits(doc, headline):
    """'Search results for players - 13 Hits' -> 13 for the matching headline."""
    for h in doc.select("h2.content-box-headline"):
        t = text(h) or ""
        if headline.lower() in t.lower():
            m = re.search(r"(\d[\d.,]*)\s*Hits", t)
            return to_int(m.group(1)) if m else None
    return None


def total_pages(doc, page_param=None):
    """Highest page number in the site's pager (links carry ?page=N,
    &<Prefix>_page=N or /page/N); 1 when there is no pager."""
    best = 1
    pattern = re.compile(rf"(?:[?&]{re.escape(page_param)}=|/page/)(\d+)") if page_param \
        else re.compile(r"(?:[?&](?:\w+_)?page=|/page/)(\d+)")
    for a in doc.select(".tm-pagination a[href], .pager a[href], ul.pagination a[href]"):
        m = pattern.search(a.get("href") or "")
        if m:
            best = max(best, int(m.group(1)))
    return best


def pagination(page, per_page, total_count=None, total_pages_=None):
    if total_pages_ is None and total_count is not None and per_page:
        total_pages_ = max(1, -(-total_count // per_page))
    return {"page": page, "items_per_page": per_page, "total_pages": total_pages_ or 1,
            "total_count": total_count}


# ---- profile blocks -----------------------------------------------------------------------

def data_header(doc):
    """The profile hero's label/value pairs ('Contract expires' -> '30/06/2034')
    plus the raw element under `_el`."""
    out = {}
    hero = doc.select_one(".data-header")
    if hero is None:
        return out
    for item in hero.select(".data-header__label"):
        label = clean(item.get_text(" ", strip=True).split(":")[0]) if ":" in item.get_text() else None
        contents = item.select(".data-header__content")
        if label:
            # one value -> the value element; several (Caps/Goals: 55 / 62) -> the whole item
            out[label.rstrip(":").strip()] = contents[0] if len(contents) == 1 else item
    return out


def info_table(doc):
    """The 'Facts and data' table ('Name in home country' -> <td>)."""
    out = {}
    labels = doc.select(".info-table__content--regular")
    values = doc.select(".info-table__content--bold")
    for lab, val in zip(labels, values):
        key = (text(lab) or "").rstrip(":").strip()
        if key:
            out[key] = val
    return out


def profile_rows(box):
    """A th/td facts table (stadium / manager boxes) -> {label: [values]};
    rows with an empty label continue the previous one (address lines)."""
    out, last = {}, None
    if box is None:
        return out
    for tr in box.select("tr"):
        cells_ = tr.find_all(["th", "td"])
        if len(cells_) < 2:
            continue
        key = (text(cells_[0]) or "").rstrip(":").strip()
        if key:
            last = key
            out.setdefault(key, [])
        if last is None:
            continue
        out[last].append(cells_[1])
    return out


def info_spans(box):
    """The span-based info table (club facts box) -> {label: [value elements]}."""
    out, last = {}, None
    if box is None:
        return out
    for el in box.select(".info-table__content"):
        classes = el.get("class") or []
        if "info-table__content--regular" in classes:
            last = (text(el) or "").rstrip(":").strip()
            if last:
                out.setdefault(last, [])
            continue
        if last and "info-table__content--bold" in classes:
            out[last].append(el)
    return out


def joined_text(elements):
    parts = [text(e) for e in elements or []]
    return " ".join(p for p in parts if p) or None


def ordered(data, first, last=()):
    """Reorder a dict: `first` keys, then the rest, then `last`."""
    out = {}
    for k in first:
        if k in data:
            out[k] = data[k]
    for k, v in data.items():
        if k not in out and k not in last:
            out[k] = v
    for k in last:
        if k in data:
            out[k] = data[k]
    return out


# ---- tmapi normalizers ---------------------------------------------------------------------

def api_money(mv):
    """tmapi {value, currency, determined?} -> {amount, currency, updated_at?}; None when empty."""
    if not isinstance(mv, dict) or mv.get("value") in (None, ""):
        return None
    out = {"amount": mv.get("value"), "currency": mv.get("currency") or "EUR"}
    if mv.get("determined"):
        out["updated_at"] = mv["determined"]
    return out


def api_market_value(details):
    """tmapi marketValueDetails -> {current, previous, highest, change}."""
    if not isinstance(details, dict):
        return None
    delta = details.get("delta") or {}
    change = None
    if delta.get("type"):
        change = {"direction": (delta.get("type") or "").lower() or None,
                  "amount": _delta_amount(delta.get("value")),
                  "percent": to_float(delta.get("percentage"))}
    return {"current": api_money(details.get("current")),
            "previous": api_money(details.get("previous")),
            "highest": api_money(details.get("highest")),
            "change": change}


def _delta_amount(value):
    """'+20.000.000 €' -> 20000000."""
    value = clean(value)
    if not value:
        return None
    sign = -1 if value.strip().startswith("-") else 1
    digits = re.sub(r"[^\d]", "", value)
    return sign * int(digits) if digits else None


def api_nationalities(nat):
    """tmapi nationalities {nationalityId, secondNationalityId} -> [country]."""
    out = []
    for key in ("nationalityId", "secondNationalityId"):
        c = refs.country((nat or {}).get(key))
        if c:
            out.append(c)
    return out


def api_season(season):
    return season_obj(season) if season else None


def api_player_ref(p):
    """tmapi embedded player (id, name, shortName, relativeUrl...) -> player ref."""
    if not isinstance(p, dict) or not p.get("id"):
        return None
    return {"id": int(p["id"]), "name": p.get("name"), "short_name": p.get("shortName") or None,
            "link": link(p.get("relativeUrl")) or entity_link("player", p["id"]),
            "image": p.get("portraitUrl") or None,
            "position": refs.position((p.get("attributes") or {}).get("positionId")),
            "nationalities": api_nationalities(p.get("nationalities") or (p.get("nationalityDetails") or {}).get("nationalities"))}


def api_club_ref(c):
    """tmapi club object -> club ref (works for /clubs?ids[] rows)."""
    if not isinstance(c, dict) or not c.get("id"):
        return None
    base = c.get("baseDetails") or {}
    return {"id": int(c["id"]), "name": c.get("name"), "short_name": base.get("shortName") or None,
            "link": link(c.get("relativeUrl")) or entity_link("club", c["id"]), "crest": c.get("crestUrl") or None,
            "country": refs.country(base.get("countryId")),
            "is_national_team": bool(base.get("isNationalTeam"))}


def api_competition_ref(c):
    if not isinstance(c, dict) or not c.get("id"):
        return None
    return {"id": c["id"], "name": c.get("name"),
            "link": link(c.get("relativeUrl")) or entity_link("competition", c["id"]),
            "logo": c.get("logoUrl") or None}
