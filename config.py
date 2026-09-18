"""Configuration for the Transfermarkt Scraper. Everything can be set with an
environment variable; the defaults work out of the box.

    PORT                 port the API listens on (default 8000)
    TRANSFERMARKT_PROXY  proxy URL for every request, e.g.
                         http://user:pass@host:port (default: none — direct).
                         Transfermarkt answers a direct connection from most
                         networks, but some ISPs and regions are blocked
                         outright and AWS WAF challenges some IPs. If requests
                         time out or come back "blocked the request", put a
                         residential proxy here — a rotating one is ideal,
                         because a fresh exit IP is what clears the challenge.

Everything else below is a plain constant with a working default — edit it
here if you need to.
"""
import os

PORT = int(os.environ.get("PORT", "8000"))

# Retry policy for transport errors and blocks (every request).
MAX_RETRIES = 3
RETRY_BACKOFF = 2          # seconds, multiplied by the attempt number

TRANSFERMARKT_PROXY = os.environ.get("TRANSFERMARKT_PROXY") or None

# With a rotating proxy, rebuild the connection after this many requests so the
# provider hands out a fresh exit IP (harmless on a sticky one, ignored without
# a proxy — a direct connection has nothing to rotate).
TRANSFERMARKT_REQUESTS_PER_EXIT = 60

# The hosted API mints an aws-waf-token with a headless browser when a
# challenged exit cannot be rotated away. That needs Playwright/patchright, so
# it is off here: this scraper is plain HTTP, and a fresh proxy exit clears the
# challenge just as well.
TRANSFERMARKT_WAF_FALLBACK = False


def transfermarkt_proxy():
    """Proxy URL for one connection (None = direct)."""
    return TRANSFERMARKT_PROXY
