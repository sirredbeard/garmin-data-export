"""Garmin Connect client: auth, pacing, and one safe call wrapper.

There is no official Garmin API for personal accounts. The
python-garminconnect library goes through the same SSO flow as the
website, and garth caches the OAuth tokens locally for about a year.
After one interactive login, every other run is non-interactive.
That is what makes a daily scheduled sync possible at all.
"""

from __future__ import annotations

import logging
import os
import sys
import threading
import time
from getpass import getpass
from pathlib import Path
from typing import Any, Optional

from garth.exc import GarthHTTPError

from garminconnect import (
    Garmin,
    GarminConnectAuthenticationError,
    GarminConnectConnectionError,
    GarminConnectTooManyRequestsError,
)

log = logging.getLogger("garmin_daily")


class RateLimiter:
    """Adaptive pacer. Stays fast until Garmin pushes back with a 429,
    then doubles the delay and waits. Thread-safe, because a day's
    endpoints are fetched in parallel through the one limiter."""

    def __init__(self, base_delay: float = 0.15):
        self.base_delay = base_delay
        self.current_delay = base_delay
        self.call_count = 0
        self.last_call = 0.0
        self.consecutive_ok = 0
        self._lock = threading.Lock()

    def wait(self):
        with self._lock:
            elapsed = time.time() - self.last_call
            if elapsed < self.current_delay:
                time.sleep(self.current_delay - elapsed)
            self.last_call = time.time()
            self.call_count += 1
            if self.call_count % 250 == 0:
                log.info("Pacing break after %d API calls", self.call_count)
                time.sleep(2)

    def on_success(self):
        with self._lock:
            self.consecutive_ok += 1
            if self.consecutive_ok > 10 and self.current_delay > self.base_delay:
                self.current_delay = max(self.base_delay, self.current_delay * 0.9)

    def on_rate_limit(self):
        with self._lock:
            self.consecutive_ok = 0
            self.current_delay = min(self.current_delay * 2, 10.0)
            delay = self.current_delay
        log.warning("Rate limited. Delay now %.1fs, waiting 60s", delay)
        time.sleep(60)

    def on_error(self):
        with self._lock:
            self.consecutive_ok = 0
            self.current_delay = min(self.current_delay * 1.2, 5.0)


limiter = RateLimiter()


def safe_call(fn, *args, label: str = "", **kwargs) -> Optional[Any]:
    """Call one Garmin endpoint. Return None when the data is not there.

    A missing endpoint is normal, not an error. Plenty of accounts have
    no golf, no blood pressure, no weigh-in for a given day. Callers get
    None and move on. A 429 gets one retry after the limiter backs off.
    """
    limiter.wait()
    try:
        result = fn(*args, **kwargs)
        limiter.on_success()
        return result
    except GarminConnectTooManyRequestsError:
        limiter.on_rate_limit()
        limiter.wait()
        try:
            result = fn(*args, **kwargs)
            limiter.on_success()
            return result
        except Exception as exc:
            log.warning("Retry failed (%s): %s", label, exc)
            limiter.on_error()
            return None
    except GarthHTTPError as exc:
        status = getattr(getattr(exc, "response", None), "status_code", None)
        if status == 429:
            limiter.on_rate_limit()
            return None
        if status in (400, 404):
            log.debug("Not available (%s) [%s]", status, label)
        else:
            log.warning("HTTP %s [%s]: %s", status, label, exc)
            limiter.on_error()
        return None
    except Exception as exc:
        log.warning("API error [%s]: %s", label, exc)
        limiter.on_error()
        return None


def _load_env_file(start: Path) -> None:
    """Read GARMIN_EMAIL and GARMIN_PASSWORD from a .env file if one sits
    next to the checkout. Values already in the environment win."""
    for env_path in (Path.cwd() / ".env", start / ".env"):
        if env_path.exists():
            for line in env_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, _, val = line.partition("=")
                    key, val = key.strip(), val.strip().strip("\"'")
                    if key and val:
                        os.environ.setdefault(key, val)
            return


def friendly_login_error(exc: Exception) -> str:
    """Short version of a login failure. The raw garth error includes the
    full SSO URL and tells the reader nothing useful."""
    msg = str(exc)
    status = getattr(getattr(exc, "response", None), "status_code", None)
    if status is None and hasattr(exc, "__cause__"):
        status = getattr(getattr(exc.__cause__, "response", None), "status_code", None)
    if status is None:
        for code in ("401", "403", "429"):
            if code in msg:
                status = int(code)
                break
    if status == 401:
        return "Wrong email or password (401)."
    if status == 403:
        return "Access denied (403). The account may be locked. Log in at connect.garmin.com first."
    if status == 429:
        return "Too many login attempts (429). Wait a few minutes and try again."
    if "authentication" in msg.lower() or "unauthorized" in msg.lower():
        return "Wrong email or password."
    if "connection" in msg.lower() or "timeout" in msg.lower():
        return "Could not reach Garmin servers. Check the internet connection."
    if "https://" in msg:
        msg = msg[: msg.index("https://")].rstrip(": ")
    return msg or "Unknown login error."


def authenticate(tokenstore: str) -> Garmin:
    """Return a logged-in Garmin client.

    Cached tokens first. They cover almost every run. Credentials, from
    the environment or a prompt, are only needed when the cache is gone
    or expired, which in practice means the one-time --login run.
    """
    tokenstore_path = Path(tokenstore).expanduser()

    if tokenstore_path.exists():
        try:
            garmin = Garmin()
            garmin.login(str(tokenstore_path))
            log.info("Authenticated with cached tokens")
            return garmin
        except Exception as exc:
            log.info("Cached tokens did not load (%s), fresh login needed", type(exc).__name__)

    _load_env_file(Path(__file__).resolve().parent.parent)
    email = os.getenv("GARMIN_EMAIL") or os.getenv("EMAIL")
    password = os.getenv("GARMIN_PASSWORD") or os.getenv("PASSWORD")

    if not email or not password:
        if not sys.stdin.isatty():
            raise GarminConnectAuthenticationError(
                "No cached tokens and no terminal to prompt on. "
                "Run the login command once in a terminal."
            )
        print()
        print("  One-time Garmin Connect login")
        print("  Credentials go straight to Garmin's SSO, same as the website.")
        print("  Tokens cache locally for about a year after this.")
        print()
        if not email:
            email = input("  Garmin email: ").strip()
        if not password:
            password = getpass("  Garmin password: ")

    if not email or not password:
        raise GarminConnectAuthenticationError("Email and password are required")

    log.info("Logging in to Garmin Connect")
    garmin = Garmin(email=email, password=password, is_cn=False, return_on_mfa=True)
    result1, result2 = garmin.login()
    if result1 == "needs_mfa":
        code = input("  Enter MFA code from your authenticator app: ").strip()
        garmin.resume_login(result2, code)

    # Save through whichever object this garminconnect version exposes.
    auth_client = getattr(garmin, "client", None) or getattr(garmin, "garth", None)
    if auth_client is not None and hasattr(auth_client, "dump"):
        tokenstore_path.mkdir(parents=True, exist_ok=True)
        auth_client.dump(str(tokenstore_path))
    return garmin
