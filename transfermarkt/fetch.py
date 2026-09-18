"""Transfermarkt transport: plain HTTP via curl_cffi through rotating
residential exits, with a patchright-minted AWS WAF token as the fallback.

Validated 2026-09-18 (through US residential exits; www.transfermarkt.com does
not answer this network's direct egress at all — TCP connects time out, so a
proxy is mandatory, config.transfermarkt_proxy()):

  * www.transfermarkt.com HTML — every page is server-rendered (player /
    club / competition / match / coach tabs, the global statistic lists, the
    quick search, the advanced player search which is a POST of the full
    form) and answers a browser-impersonated curl session with no cookies.
  * www.transfermarkt.com/ceapi/* — small JSON helpers the pages call:
    marketValueDevelopment/graph/{player}, transferHistory/list/{player},
    nextMatches/player/{player}. Same egress, `accept: application/json`.
  * tmapi.transfermarkt.technology — the JSON API behind the site's web
    components (`tm-player-performance-table-new` etc.). Open, no key, JSON
    envelope {success, message, data}: /player/{id}, /players?ids[]=,
    /player/{id}/performance-game (every game of the career, ~1.4 MB),
    /club/{id}, /clubs?ids[]=, /club/{id}/squad, /competition/{id},
    /competitions?ids[]=, /competition/{id}/table?season=, /game/{id},
    /games?ids[]=, /coach/{id}, /referee/{id}, /stadium/{id}, /attributes,
    /quick-search?term=. Bulk id lists of 250+ work in one call.

The one wall is AWS WAF: SOME exit IPs get an HTTP 202 interstitial
(`window.awsWafCookieDomainList` + challenge.js) on every HTML/ceapi URL,
others never do, and the decision is per IP, not per request. So a 202 is
handled by ROTATING to a fresh sticky exit (one new port = one new IP); if
that also fails the process mints an `aws-waf-token` cookie with a throwaway
headless patchright page-load on the challenged exit (booking/fetch.py
pattern) — validated 2026-09-18: the minted token unlocks the challenged
exit AND is accepted from other exits. The browser is used for nothing
else (config.POOLLESS_FLAGS, no chrome pool).

Nonexistent ids are NOT always 404s: an unknown player id redirects to the
"most valuable players" list and an unknown competition id to the
competitions index, both HTTP 200 — get_page(expect=...) checks the final
URL still carries the requested entity path and raises NotFound otherwise.

Failure taxonomy (scraper_errors, mapped by route_glue):
  TransfermarktUpstreamError  transport failure / 5xx — retryable
  TransfermarktBlocked        WAF 202 / 403 / 429 — retryable on a new exit
  TransfermarktBadRequest     upstream 400 — never retried
  TransfermarktNotFound       404 / redirected-away entity — never retried
"""
import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

# Allow direct execution (python transfermarkt/fetch.py): flat imports resolve
# like under the server. Idempotent when imported normally.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from scraper_errors import BadRequest, Blocked, NotFound, UpstreamError

SITE = "https://www.transfermarkt.com"
API = "https://tmapi.transfermarkt.technology"
IMPERSONATE = "chrome"
PAGE_TIMEOUT = 45      # a competition's transfers page is ~1.4 MB (2-3 s normally)
JSON_TIMEOUT = 45      # performance-game payloads are ~1.4 MB (2-4 s normally)
FANOUT_WORKERS = 4     # parallel upstream calls for endpoints that stitch several

WAF_MARKER = "awswafcookiedomainlist"
WAF_COOKIE = "aws-waf-token"
HARVEST_SETTLE_TIMEOUT = 60
HARVEST_POLL_INTERVAL = 2
IDENTITY_MAX_AGE = 3 * 24 * 3600   # the cookie lives ~4 days; re-mint at 3

PAGE_HEADERS = {
    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "accept-language": "en-US,en;q=0.9",
    "upgrade-insecure-requests": "1",
}
JSON_HEADERS = {
    "accept": "application/json, text/plain, */*",
    "accept-language": "en-US,en;q=0.9",
    "referer": SITE + "/",
}


class TransfermarktUpstreamError(UpstreamError):
    """Transport failure or 5xx — retryable."""


class TransfermarktBlocked(TransfermarktUpstreamError, Blocked):
    """AWS WAF 202 / 403 / 429 — retryable on a fresh exit, then with a minted token."""


class TransfermarktBadRequest(BadRequest):
    """Upstream 400 — never retried."""


class TransfermarktNotFound(NotFound):
    """Entity / page does not exist — never retried."""


# ---- sessions ------------------------------------------------------------------
# One curl session per worker thread (a curl handle must not be shared across
# threads). Each session holds one sticky exit and is rotated after
# config.TRANSFERMARKT_REQUESTS_PER_EXIT requests or on any failure.
_local = threading.local()


def _session():
    sess = getattr(_local, "session", None)
    used = getattr(_local, "used", 0)
    if sess is not None and used >= config.TRANSFERMARKT_REQUESTS_PER_EXIT:
        _drop_session()
        sess = None
    if sess is None:
        from curl_cffi import requests as curl_requests
        sess = curl_requests.Session(impersonate=IMPERSONATE)
        proxy = config.transfermarkt_proxy()
        if proxy:
            sess.proxies = {"http": proxy, "https": proxy}
        sess.tm_proxy = proxy
        token = _current_token()
        if token:
            sess.cookies.set(WAF_COOKIE, token, domain="www.transfermarkt.com")
        _local.session = sess
        _local.used = 0
    _local.used = getattr(_local, "used", 0) + 1
    return sess


def _drop_session():
    """Close the thread's session so a poisoned exit / keep-alive dies."""
    sess = getattr(_local, "session", None)
    _local.session = None
    if sess is not None:
        try:
            sess.close()
        except Exception:
            pass


def dump_debug(name, text):
    """Write raw response text to $TRANSFERMARKT_DEBUG_DIR/<name>.txt."""
    dbg = os.environ.get("TRANSFERMARKT_DEBUG_DIR", "")
    if dbg and text:
        try:
            os.makedirs(dbg, exist_ok=True)
            with open(os.path.join(dbg, name + ".txt"), "w") as f:
                f.write(text)
        except OSError:
            pass


# ---- AWS WAF token (fallback identity) -------------------------------------------
# Process-wide: the token is accepted from any exit, so every worker thread
# shares one. Minted only after exit rotation failed to dodge the challenge.
_identity = None
_identity_lock = threading.Lock()


def _current_token():
    ident = _identity
    if ident and time.monotonic() - ident["harvested_at"] < IDENTITY_MAX_AGE:
        return ident["token"]
    return None


def _harvest_identity(proxy):
    """One throwaway headless patchright page-load of the homepage on `proxy`;
    the WAF challenge clears itself and sets aws-waf-token. Returns the
    token string. Raises RuntimeError when none appears."""
    from chrome_manager import create_scope
    from patchright_driver import PatchrightDriver

    print("transfermarkt: minting aws-waf-token via patchright...")
    with create_scope():
        driver = PatchrightDriver(proxy_url=proxy, headless=True)
    try:
        driver.nav(SITE + "/", referer="https://www.google.com/",
                   mode="blocked", challenge_markers=(WAF_MARKER,))
        deadline = time.monotonic() + HARVEST_SETTLE_TIMEOUT
        while True:
            cookies = {c["name"]: c["value"]
                       for c in driver.call(lambda page: page.context.cookies(SITE))}
            token = cookies.get(WAF_COOKIE)
            if token:
                print(f"transfermarkt: aws-waf-token minted ({len(token)} chars)")
                return token
            if time.monotonic() >= deadline:
                raise RuntimeError("AWS WAF challenge did not set aws-waf-token")
            time.sleep(HARVEST_POLL_INTERVAL)
    finally:
        driver.close()


def _mint_token(proxy):
    """Mint (or reuse a fresh) token; one thread mints while others wait."""
    global _identity
    if not config.TRANSFERMARKT_WAF_FALLBACK:
        return None
    with _identity_lock:
        if _current_token() and time.monotonic() - _identity["harvested_at"] < 60:
            return _identity["token"]   # a peer just minted one
        try:
            token = _harvest_identity(proxy)
        except Exception as e:
            print(f"transfermarkt: WAF token mint failed: {type(e).__name__}: {e}")
            return None
        _identity = {"token": token, "harvested_at": time.monotonic()}
        return token


# ---- requests --------------------------------------------------------------------

def _is_waf(resp):
    return resp.status_code == 202 and WAF_MARKER in resp.text[:5000].lower()


def _classify(resp, label):
    if _is_waf(resp):
        dump_debug("waf", resp.text)
        raise TransfermarktBlocked(f"AWS WAF challenge on {label}")
    if resp.status_code == 404:
        raise TransfermarktNotFound(f"{label} not found")
    if resp.status_code in (403, 429):
        dump_debug("blocked", resp.text)
        raise TransfermarktBlocked(f"HTTP {resp.status_code} on {label}")
    if resp.status_code == 400:
        raise TransfermarktBadRequest(resp.text[:200])
    if resp.status_code != 200:
        raise TransfermarktUpstreamError(f"HTTP {resp.status_code} on {label}")


def _do(method, url, *, params=None, data=None, headers=None, timeout=PAGE_TIMEOUT):
    sess = _session()
    try:
        resp = sess.request(method, url, params=params, data=data, headers=headers,
                            timeout=timeout, allow_redirects=True)
    except Exception as e:
        raise TransfermarktUpstreamError(f"request failed: {type(e).__name__}: {e}")
    _classify(resp, url.split("//", 1)[-1][:120])
    return resp


def _retrying(fn):
    """Shared retry policy: transport errors rotate the exit; a WAF block
    rotates the exit first and mints the fallback token before the last try."""
    last = None
    for attempt in range(1, config.MAX_RETRIES + 1):
        try:
            return fn()
        except TransfermarktBlocked as e:
            last = e
            proxy = getattr(getattr(_local, "session", None), "tm_proxy", None)
            _drop_session()
            if attempt >= config.MAX_RETRIES - 1 and not _current_token():
                _mint_token(proxy or config.transfermarkt_proxy())
        except TransfermarktUpstreamError as e:
            last = e
            _drop_session()
        if attempt < config.MAX_RETRIES:
            time.sleep(config.RETRY_BACKOFF * attempt)
    raise last


def _check_landing(resp, expect, label):
    """The site answers unknown ids with a 200 redirect to a generic list
    (players -> most valuable players, competitions -> competitions index)."""
    if expect and expect not in urlparse(resp.url or "").path:
        raise TransfermarktNotFound(f"{label} not found")


def get_page(path, params=None, *, expect=None):
    """GET one www.transfermarkt.com page -> HTML text. `path` starts with
    "/". `expect` = a path fragment the FINAL url must contain (e.g.
    "/spieler/418560"), else NotFound."""
    url = SITE + path

    def once():
        resp = _do("GET", url, params=params, headers=PAGE_HEADERS, timeout=PAGE_TIMEOUT)
        _check_landing(resp, expect, path)
        return resp.text
    return _retrying(once)


def post_page(path, data, params=None):
    """POST a form (list of (name, value) pairs or dict) -> HTML text."""
    url = SITE + path

    def once():
        resp = _do("POST", url, params=params, data=data, headers=PAGE_HEADERS,
                   timeout=PAGE_TIMEOUT)
        return resp.text
    return _retrying(once)


def _json_of(resp, label):
    try:
        return resp.json()
    except Exception:
        dump_debug("nonjson", resp.text)
        raise TransfermarktBlocked(f"non-JSON response from {label}")


def get_ceapi(path, params=None):
    """GET one www.transfermarkt.com/ceapi/* helper -> parsed JSON."""
    url = SITE + path

    def once():
        resp = _do("GET", url, params=params, headers=JSON_HEADERS, timeout=JSON_TIMEOUT)
        return _json_of(resp, path)
    return _retrying(once)


def get_api(path, params=None):
    """GET one tmapi.transfermarkt.technology route -> the `data` of its
    envelope. A `success: false` envelope maps to NotFound ("... not found")
    or BadRequest."""
    url = API + path

    def once():
        sess = _session()
        try:
            resp = sess.get(url, params=params, headers=JSON_HEADERS, timeout=JSON_TIMEOUT)
        except Exception as e:
            raise TransfermarktUpstreamError(f"request failed: {type(e).__name__}: {e}")
        if resp.status_code == 404:
            payload = _json_of(resp, path) if resp.text.startswith("{") else {}
            raise TransfermarktNotFound(payload.get("message") or f"{path} not found")
        _classify(resp, path)
        payload = _json_of(resp, path)
        if not isinstance(payload, dict) or not payload.get("success"):
            msg = (payload or {}).get("message") if isinstance(payload, dict) else None
            if msg and "not found" in msg.lower():
                raise TransfermarktNotFound(msg)
            raise TransfermarktBadRequest(msg or "tmapi rejected the request")
        return payload.get("data")
    return _retrying(once)


def get_api_optional(path, params=None):
    """Like get_api but a NotFound yields None."""
    try:
        return get_api(path, params)
    except TransfermarktNotFound:
        return None


def ids_query(ids):
    """tmapi bulk lookups take repeated ids[] params."""
    return [("ids[]", str(i)) for i in ids]


def run_parallel(fns):
    """Run zero-arg callables in parallel; returns results aligned with `fns`.
    Exceptions propagate from the first failing call."""
    if not fns:
        return []
    if len(fns) == 1:
        return [fns[0]()]
    with ThreadPoolExecutor(max_workers=min(FANOUT_WORKERS, len(fns))) as ex:
        futures = [ex.submit(fn) for fn in fns]
        return [f.result() for f in futures]


if __name__ == "__main__":
    # Smoke test: python transfermarkt/fetch.py [tmapi path]
    import json
    target = sys.argv[1] if len(sys.argv) > 1 else "/player/418560"
    print(json.dumps(get_api(target), ensure_ascii=False)[:1500])
    html = get_page("/erling-haaland/profil/spieler/418560", expect="/spieler/418560")
    print(len(html), re.search(r"<title>(.*?)</title>", html, re.S).group(1).strip())
