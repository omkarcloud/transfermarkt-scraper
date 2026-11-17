"""Offline tests for the transport's exit handling: the gateway exit try in
front of the residential attempts (config.TRANSFERMARKT_GATEWAY_EXIT). Fake
sessions, no network.

    python -m pytest transfermarkt/test_fetch.py -q
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from transfermarkt import fetch  # noqa: E402
from scraper_utils.testing import hosted_only

_WAF = '<script>window.awsWafCookieDomainList = [];</script><script src="/challenge.js"></script>'
_API_OK = '{"success": true, "data": {"id": "28003"}}'


class _Resp:
    def __init__(self, status=200, text="<html>page</html>", url=None):
        self.status_code, self.text, self.url = status, text, url

    def json(self):
        import json
        return json.loads(self.text)


class _Jar:
    def __init__(self):
        self.cleared, self.seeded = 0, []

    def clear(self):
        self.cleared += 1

    def set(self, name, value, domain=None):
        self.seeded.append((name, domain))


class _FakeSession:
    """Answers its queue in order (an Exception is raised); the last answer repeats."""

    def __init__(self, answers):
        self.answers, self.calls, self.closed = list(answers), [], False
        self.cookies, self.proxies = _Jar(), {}

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(answer, Exception):
            raise answer
        if answer.url is None:
            answer.url = url
        return answer

    def get(self, url, **kwargs):
        return self.request("GET", url, **kwargs)

    def close(self):
        self.closed = True


def _wire(monkeypatch, gateway_exit, gateway_answers, residential_answers):
    """fetch on fake sessions: the gateway one is built by egress.new_session,
    the residential ones by curl_cffi's Session (one shared fake, so its
    calls add up across rotations). `built` lists the exits opened."""
    import curl_cffi.requests
    gateway, residential, built = _FakeSession(gateway_answers), _FakeSession(residential_answers), []
    for name in ("session", "gateway", "via_gateway"):
        monkeypatch.setattr(fetch._local, name, None, raising=False)
    monkeypatch.setattr(fetch, "_gateway_paused_until", 0.0)
    monkeypatch.setattr(fetch, "_gateway_failures", [])
    monkeypatch.setattr(fetch, "_waf_seen", False)
    monkeypatch.setattr(fetch.config, "TRANSFERMARKT_GATEWAY_EXIT", gateway_exit)
    monkeypatch.setattr(fetch.config, "transfermarkt_proxy", lambda: "http://residential-us")
    monkeypatch.setattr(fetch.egress, "new_session", lambda exit, impersonate=None: built.append(exit) or gateway)
    monkeypatch.setattr(curl_cffi.requests, "Session",
                        lambda impersonate=None: built.append("residential") or residential)
    monkeypatch.setattr(fetch, "_fallback_token", lambda replace=None, hint=None: None)      # no box in a unit test
    monkeypatch.setattr(fetch.time, "sleep", lambda s: None)
    return gateway, residential, built


@hosted_only
def test_every_surface_rides_the_gateway_first(monkeypatch):
    gateway, residential, built = _wire(monkeypatch, "gateway", [_Resp(), _Resp(200, '{"list": []}'), _Resp(),
                                                             _Resp(200, _API_OK)], [_Resp()])
    assert fetch.get_page("/x/profil/spieler/28003", expect="/spieler/28003") == "<html>page</html>"
    assert fetch.get_ceapi("/ceapi/transferHistory/list/28003") == {"list": []}
    assert fetch.post_page("/detailsuche/spielerdetail/suche", [("a", "1")]) == "<html>page</html>"
    assert fetch.get_api("/player/28003") == {"id": "28003"}
    assert built == ["gateway"] and not residential.calls          # one gateway session, no residential exit opened
    assert [c[0] for c in gateway.calls] == ["GET", "GET", "POST", "GET"]
    assert gateway.cookies.cleared == 4 and not gateway.cookies.seeded     # stateless: nothing carried between addresses
    assert fetch._local.via_gateway is None


@hosted_only
def test_knob_off_keeps_everything_on_the_residential_exit(monkeypatch):
    gateway, residential, built = _wire(monkeypatch, None, [_Resp()], [_Resp()])
    fetch.get_page("/x/profil/spieler/28003")
    assert built == ["residential"] and not gateway.calls and len(residential.calls) == 1
    assert residential.proxies == {"http": "http://residential-us", "https": "http://residential-us"}


@hosted_only
def test_a_challenged_gateway_falls_back_to_the_residential_attempts(monkeypatch):
    gateway, residential, built = _wire(monkeypatch, "gateway", [_Resp(202, _WAF)], [_Resp(200, "<html>resi</html>")])
    assert fetch.get_page("/x/profil/spieler/28003") == "<html>resi</html>"
    assert len(gateway.calls) == 1 and len(residential.calls) == 1 and built == ["gateway", "residential"]
    assert gateway.closed and fetch._local.gateway is None      # a gateway session that failed is dropped


@hosted_only
def test_a_gateway_transport_error_falls_back_too(monkeypatch):
    gateway, residential, _ = _wire(monkeypatch, "gateway", [RuntimeError("gateway 504")], [_Resp(200, _API_OK)])
    assert fetch.get_api("/player/28003") == {"id": "28003"}
    assert len(gateway.calls) == 1 and len(residential.calls) == 1


@hosted_only
def test_gateway_and_residential_blocks_still_raise_blocked(monkeypatch):
    gateway, residential, _ = _wire(monkeypatch, "gateway", [_Resp(403, "denied")], [_Resp(202, _WAF)])
    with pytest.raises(fetch.TransfermarktBlocked):
        fetch.get_page("/x/profil/spieler/28003")
    assert len(gateway.calls) == 1 and len(residential.calls) == fetch.config.MAX_RETRIES     # ONE gateway try, then the old loop in full


@hosted_only
def test_not_found_from_the_gateway_is_final(monkeypatch):
    landing = "https://www.transfermarkt.com/spieler-statistik/wertvollstespieler/marktwertetop"
    gateway, residential, _ = _wire(monkeypatch, "gateway", [_Resp(200, url=landing), _Resp(404, "{}")], [_Resp()])
    with pytest.raises(fetch.TransfermarktNotFound):            # an unknown id redirects to a list, HTTP 200
        fetch.get_page("/-/profil/spieler/999999999", expect="/spieler/999999999")
    with pytest.raises(fetch.TransfermarktNotFound):
        fetch.get_api("/player/999999999")
    assert len(gateway.calls) == 2 and not residential.calls   # never retried, on any exit
    assert fetch._gateway_failures == [] and fetch._local.via_gateway is None


@hosted_only
def test_repeated_gateway_failures_pause_the_gateway_try(monkeypatch):
    gateway, residential, _ = _wire(monkeypatch, "gateway", [_Resp(202, _WAF)], [_Resp()])
    monkeypatch.setattr(fetch.config, "TRANSFERMARKT_GATEWAY_FAILURES", 2)
    for _ in range(4):
        fetch.get_page("/x/profil/spieler/28003")
    assert len(gateway.calls) == 2 and len(residential.calls) == 4      # calls 3 and 4 skip the gateway
    assert fetch._gateway_exit() is None
    monkeypatch.setattr(fetch, "_gateway_paused_until", fetch.time.monotonic() - 1)    # the pause is over
    gateway.answers = [_Resp()]
    fetch.get_page("/x/profil/spieler/28003")
    assert len(gateway.calls) == 3 and len(residential.calls) == 4
