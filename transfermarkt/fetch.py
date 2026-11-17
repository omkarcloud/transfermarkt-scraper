"""Transfermarkt transport: plain HTTP via curl_cffi, each call tried once
through the shared gateway exit and then through rotating residential exits,
with a box-minted AWS WAF token as the last fallback.

Since 2026-11-11 config.TRANSFERMARKT_GATEWAY_EXIT = "gateway" puts the gateway
exit (exit_gateway.py, a fresh gateway address per request) in front of
everything below: 207 of 207 requests on all four surfaces (HTML pages,
/ceapi, the advanced-search POST, tmapi) answered through it with the
residential exit's data and no WAF challenge, bursts at concurrency 6
included (table in config.py). A gateway failure falls through to the
residential path, which is unchanged; see "the gateway exit" below.

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
that also fails the process asks the browser box for an `aws-waf-token`
(browser_client.mint -> recipes/transfermarkt.py, a headless homepage load
through the challenged exit) — validated 2026-09-18: the minted token
unlocks the challenged exit AND is accepted from other exits, so it is
replayed through whatever residential exit the curl session already uses.
One process-wide token lives in an identity_pool.Pool (one mint per block
wave, not one per blocked thread). GKE runs no browser for this site
(config.POOLLESS_FLAGS, no chrome pool).

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
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait as wait_futures
from urllib.parse import urlparse

# Allow direct execution (python transfermarkt/fetch.py): flat imports resolve
# like under the server. Idempotent when imported normally.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import browser_client
import config
import egress
import identity_pool
from browser_client import BoxRefused, BoxUnavailable, RemintBudgetExceeded
from scraper_errors import BadRequest, Blocked, NotFound, UpstreamError
from scraper_utils import debug, fanout, sessions

SITE = "https://www.transfermarkt.com"
API = "https://tmapi.transfermarkt.technology"
IMPERSONATE = "chrome"
PAGE_TIMEOUT = 45      # a competition's transfers page is ~1.4 MB (2-3 s normally)
JSON_TIMEOUT = 45      # performance-game payloads are ~1.4 MB (2-4 s normally)
FANOUT_WORKERS = 4     # parallel upstream calls for endpoints that stitch several

WAF_MARKER = "awswafcookiedomainlist"
WAF_COOKIE = "aws-waf-token"
BOX_SITE = "transfermarkt"          # recipes/transfermarkt.py; its ttl_seconds is the token TTL

PAGE_HEADERS = {
    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "accept-language": egress.site_accept_language("transfermarkt"),
    "upgrade-insecure-requests": "1",
}
JSON_HEADERS = {
    "accept": "application/json, text/plain, */*",
    "accept-language": egress.site_accept_language("transfermarkt"),
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
    if getattr(_local, "via_gateway", None):
        return _gateway_session(_local.via_gateway)
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
        # the pooled token (an identity_pool.Identity, kept on the session so
        # a block can ask the pool to replace THIS one), or None before the
        # WAF has been met in this process
        ident = _tokens.peek() if _waf_seen else None
        if ident is not None:
            sess.cookies.set(WAF_COOKIE, ident.value, domain="www.transfermarkt.com")
        sess.waf_ident = ident
        _local.session = sess
        _local.used = 0
    _local.used = getattr(_local, "used", 0) + 1
    return sess


def _drop_session():
    """Close the thread's session so a poisoned exit / keep-alive dies."""
    sessions.drop(_local)


# ---- the gateway exit, in front of the residential exits ---------------------------
# config.TRANSFERMARKT_GATEWAY_EXIT ("gateway"): every call is tried ONCE through
# the shared gateway exit first (_retrying), a fresh gateway address per request,
# no proxy traffic. The gateway session is the thread's own, apart from its
# residential one, and carries no cookies from call to call: the next
# request leaves from another address, and nothing the site serves needs a
# cookie. Any retryable failure there (a WAF challenge, a 403 / 429, a
# transport or gateway error) falls through to the residential attempts,
# unchanged; TRANSFERMARKT_GATEWAY_FAILURES of them within
# TRANSFERMARKT_GATEWAY_WINDOW seconds switch the gateway try off for
# TRANSFERMARKT_GATEWAY_PAUSE seconds process-wide, so a WAF that starts
# challenging gateway addresses costs two wasted requests per pause, not one per
# call.
_gateway_lock = threading.Lock()
_gateway_failures = []          # time.monotonic() of the latest gateway failures
_gateway_paused_until = 0.0     # time.monotonic() until which calls skip the gateway


def _gateway_exit():
    """The gateway exit for the next call ("gateway"), or None: the knob is off
    or the gateway try is paused after failures."""
    exit = config.transfermarkt_gateway_exit()
    return exit if exit and time.monotonic() >= _gateway_paused_until else None


def _gateway_session(exit):
    sess = getattr(_local, "gateway", None)
    if sess is None:
        sess = _local.gateway = egress.new_session(exit, impersonate=IMPERSONATE)
    sess.cookies.clear()
    return sess


def _gateway_failed(exit, error):
    """Drop the thread's gateway session and count the failure towards the
    process-wide pause."""
    global _gateway_paused_until
    sess, _local.gateway = getattr(_local, "gateway", None), None
    sessions.close_quietly(sess)
    print(f"transfermarkt: gateway exit ({exit}) failed, residential exit next: {error}")
    now = time.monotonic()
    with _gateway_lock:
        _gateway_failures[:] = [t for t in _gateway_failures if now - t < config.TRANSFERMARKT_GATEWAY_WINDOW] + [now]
        if len(_gateway_failures) >= config.TRANSFERMARKT_GATEWAY_FAILURES:
            del _gateway_failures[:]
            _gateway_paused_until = now + config.TRANSFERMARKT_GATEWAY_PAUSE
            print(f"transfermarkt: {config.TRANSFERMARKT_GATEWAY_FAILURES} gateway exit failures within "
                  f"{config.TRANSFERMARKT_GATEWAY_WINDOW} s; residential exits only for {config.TRANSFERMARKT_GATEWAY_PAUSE} s")


def dump_debug(name, text):
    """Write raw response text to $TRANSFERMARKT_DEBUG_DIR/<name>.txt."""
    debug.dump("TRANSFERMARKT_DEBUG_DIR", name, text)


# ---- AWS WAF token (fallback identity) -------------------------------------------
# A token only exists on an exit the WAF actually challenges (2026-10-22: the
# box loaded the page on 4 residential exits and on its own IP with no
# challenge and no token). So the mint runs on the browser box ON THE SAME
# STICKY PORT whose curl session just got the 202 (a proxy port gives
# the box and this pod the same exit IP), exactly as the in-pod mint did.
# The token itself is accepted from any exit, so one process-wide token
# (identity_pool.Pool, shared mode) is used by every worker thread until the
# recipe's expires_at; the pool single-flights the mint, so a block wave
# costs one mint however many threads it hits (before 2026-11-07 every
# blocked thread minted and most were refused by the 5/min cap). Nothing is
# minted until a WAF challenge has survived exit rotation once in this
# process (_waf_seen); from then on every new session carries the live token.
_waf_seen = False
_BOX_ERRORS = (BoxUnavailable, BoxRefused, RemintBudgetExceeded, identity_pool.MintBudgetExceeded)


def _token_of(ident):
    token = (ident.get("tokens") or {}).get(WAF_COOKIE)
    if not token:
        token = next((c["value"] for c in ident.get("cookies", []) if c.get("name") == WAF_COOKIE), None)
    return token or None


def _mint(key, hint):
    """The pool's mint function: the box loads the homepage through `hint`
    (the challenged session's sticky exit) and hands back the token. Box
    errors propagate (the pool never catches); a box answer without a token
    is a BoxRefused. Returns an Identity whose expiry is the recipe's."""
    ident = browser_client.mint(BOX_SITE, proxy=hint)
    token = _token_of(ident)
    if not token:
        raise BoxRefused("the browser box answered without an aws-waf-token")
    print(f"transfermarkt: aws-waf-token minted ({len(token)} chars)")
    return identity_pool.Identity(token, exit=hint, expires_at=float(ident.get("expires_at") or (time.time() + 3600)))


_tokens = identity_pool.Pool(BOX_SITE, _mint, mints_per_minute=5)


def _fallback_token(replace=None, hint=None):
    """The shared token for the next session: the live one, or a mint on the
    box through `hint`; `replace` is the Identity the blocked session carried
    (the pool re-mints only if it is still the current one, so one refusal
    wave costs one mint). None when the box is down / refused /
    unchallenged / over the mint budget: no browser fallback."""
    global _waf_seen
    _waf_seen = True
    try:
        return _tokens.get(replace=replace, hint=hint).value
    except _BOX_ERRORS as e:
        print(f"transfermarkt: WAF token mint failed: {type(e).__name__}: {e}")
        return None


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
    rotates the exit first and, before the last try, fetches the fallback
    token from the box (a forced re-mint when the blocked session already
    carried one). With config.TRANSFERMARKT_GATEWAY_EXIT the call is tried
    once through the gateway exit before all that; NotFound / BadRequest
    from it surface as they do from a residential exit."""
    exit = _gateway_exit()
    if exit:
        _local.via_gateway = exit # _session() hands out the gateway session
        try:
            return fn()
        except TransfermarktUpstreamError as e:
            _gateway_failed(exit, e)
        finally:
            _local.via_gateway = None
    last = None
    for attempt in range(1, config.MAX_RETRIES + 1):
        try:
            return fn()
        except TransfermarktBlocked as e:
            last = e
            blocked = getattr(_local, "session", None)
            used = getattr(blocked, "waf_ident", None)      # the pooled token the blocked session carried
            blocked_proxy = (getattr(blocked, "proxies", None) or {}).get("https")
            _drop_session()
            if attempt == config.MAX_RETRIES - 1:
                _fallback_token(replace=used, hint=blocked_proxy)
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


# Long-lived pools (2026-10-23): their threads keep their thread-local curl
# sessions — and the sticky exits behind them — between requests. The
# per-call executor they replace opened fresh sessions/exits for every
# fan-out and threw them away when the call returned. Two levels: a fan-out
# running inside a fan-out goes to the second pool, so it neither waits on
# the workers it occupies (deadlock) nor loses its parallelism; a third
# level runs inline.
_pools = (ThreadPoolExecutor(max_workers=FANOUT_WORKERS * 4, thread_name_prefix="transfermarkt-fanout-0"),
          ThreadPoolExecutor(max_workers=FANOUT_WORKERS * 4, thread_name_prefix="transfermarkt-fanout-1"))


def _windowed(fns, workers):
    """Run `fns` on the pool for this nesting level, at most `workers` at a
    time; results align with `fns`. The first failure (in `fns` order)
    propagates once the callables already started have finished."""
    return fanout.windowed(_pools, "transfermarkt-fanout", fns, workers)


def run_parallel(fns):
    """Run zero-arg callables in parallel; returns results aligned with `fns`.
    Exceptions propagate from the first failing call."""
    if not fns:
        return []
    if len(fns) == 1:
        return [fns[0]()]
    return _windowed(fns, FANOUT_WORKERS)


if __name__ == "__main__":
    # Smoke test: python transfermarkt/fetch.py [tmapi path]
    import json
    target = sys.argv[1] if len(sys.argv) > 1 else "/player/418560"
    print(json.dumps(get_api(target), ensure_ascii=False)[:1500])
    html = get_page("/erling-haaland/profil/spieler/418560", expect="/spieler/418560")
    print(len(html), re.search(r"<title>(.*?)</title>", html, re.S).group(1).strip())
