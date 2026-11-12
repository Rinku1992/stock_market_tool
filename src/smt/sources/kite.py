"""Zerodha Kite Connect v3: login, daily prices, holdings. Read-only. No order calls.

Built from the public API docs (https://kite.trade/docs/connect/v3/).

OPEN QUESTION, to be settled on real data by the data office: our contract stores prices
"as traded". It is not yet confirmed whether Kite's day candles are adjusted for splits
and bonus issues. Until that is checked against a known split, treat these prices as
unverified for any period that contains a corporate action.
"""

import csv
import hashlib
import io
import json
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import httpx

BASE = "https://api.kite.trade"
LOGIN = "https://kite.zerodha.com/connect/login"
IST = timezone(timedelta(hours=5, minutes=30))
UTC = timezone.utc


class KiteError(RuntimeError):
    pass


def login_url(api_key: str) -> str:
    return f"{LOGIN}?v=3&api_key={api_key}"


def checksum(api_key: str, request_token: str, api_secret: str) -> str:
    return hashlib.sha256((api_key + request_token + api_secret).encode()).hexdigest()


# ---- token cache (the access token is valid for one day) ------------------
def save_token(path: str | Path, access_token: str, today: date | None = None) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    today = today or datetime.now(IST).date()
    path.write_text(json.dumps({"access_token": access_token, "date": today.isoformat()}))
    os.chmod(path, 0o600)


def load_token(path: str | Path, today: date | None = None) -> str | None:
    path = Path(path)
    if not path.exists():
        return None
    body = json.loads(path.read_text())
    today = today or datetime.now(IST).date()
    return body["access_token"] if body.get("date") == today.isoformat() else None


# ---- http ------------------------------------------------------------------
class KiteClient:
    def __init__(self, api_key: str, access_token: str | None = None, http: httpx.Client | None = None) -> None:
        self.api_key = api_key
        self.access_token = access_token
        self._http = http or httpx.Client(timeout=30)

    def _headers(self) -> dict:
        h = {"X-Kite-Version": "3"}
        if self.access_token:
            h["Authorization"] = f"token {self.api_key}:{self.access_token}"
        return h

    @staticmethod
    def _check(r: httpx.Response) -> httpx.Response:
        if r.status_code != 200:
            raise KiteError(f"HTTP {r.status_code}: {r.text[:300]}")
        return r

    def get(self, path: str, params: dict | None = None) -> bytes:
        """Returns the response body untouched, so it can be stored as raw."""
        return self._check(self._http.get(BASE + path, params=params, headers=self._headers())).content

    def exchange_token(self, request_token: str, api_secret: str) -> str:
        data = {"api_key": self.api_key, "request_token": request_token,
                "checksum": checksum(self.api_key, request_token, api_secret)}
        r = self._check(self._http.post(BASE + "/session/token", data=data, headers={"X-Kite-Version": "3"}))
        self.access_token = r.json()["data"]["access_token"]
        return self.access_token


def find_instrument_token(instruments_csv: bytes, symbol: str, exchange: str = "NSE") -> int:
    """Look up Kite's numeric token for a symbol in the /instruments CSV dump."""
    for row in csv.DictReader(io.StringIO(instruments_csv.decode())):
        if row["tradingsymbol"] == symbol and row["exchange"] == exchange and row["instrument_type"] == "EQ":
            return int(row["instrument_token"])
    raise KiteError(f"{symbol} not found on {exchange}")


def _data(payload: bytes):
    body = json.loads(payload)
    if body.get("status") != "success":
        raise KiteError(f"Kite returned an error: {str(body)[:300]}")
    return body["data"]


# ---- adapters --------------------------------------------------------------
class KiteDailyPrices:
    name = "kite"
    dataset = "prices_daily"
    raw_ext = "json"

    def __init__(self, client: KiteClient | None, instrument_id: str, exchange: str, instrument_token: int) -> None:
        self._client = client
        self.instrument_id = instrument_id
        self.exchange = exchange
        self.instrument_token = instrument_token

    @property
    def context(self) -> dict:  # saved in the raw manifest so a raw file can be re-parsed later
        return {"instrument_id": self.instrument_id, "exchange": self.exchange, "instrument_token": self.instrument_token}

    def fetch(self, start: date, end: date) -> bytes:
        params = {"from": f"{start.isoformat()} 00:00:00", "to": f"{end.isoformat()} 23:59:59"}
        return self._client.get(f"/instruments/historical/{self.instrument_token}/day", params)

    def parse(self, payload: bytes) -> list[dict]:
        rows = []
        for ts, o, h, l, c, v, *_ in _data(payload)["candles"]:
            d = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S%z").astimezone(IST).date()
            rows.append({
                "instrument_id": self.instrument_id, "exchange": self.exchange, "trade_date": d,
                "open": float(o), "high": float(h), "low": float(l), "close": float(c), "volume": int(v),
                # a day candle is final at market close, 15:30 IST = 10:00 UTC
                "published_at": datetime(d.year, d.month, d.day, 10, 0, tzinfo=UTC),
                "source_ref": str(self.instrument_token),
            })
        return rows


class KiteHoldings:
    name = "kite"
    dataset = "holdings"
    raw_ext = "json"

    def __init__(self, client: KiteClient | None, account: str, as_of: date) -> None:
        self._client = client
        self.account = account  # an alias you choose. Never the Zerodha client id.
        self.as_of = as_of

    @property
    def context(self) -> dict:
        return {"account": self.account, "as_of": self.as_of.isoformat()}

    def fetch(self) -> bytes:
        return self._client.get("/portfolio/holdings")

    def parse(self, payload: bytes) -> list[dict]:
        rows = []
        for h in _data(payload):
            qty = float(h.get("quantity", 0)) + float(h.get("t1_quantity", 0))
            if qty == 0 or not h.get("isin"):
                continue
            rows.append({
                "account": self.account, "instrument_id": h["isin"], "as_of": self.as_of,
                "quantity": qty, "avg_price": float(h["average_price"]),
                "published_at": datetime(self.as_of.year, self.as_of.month, self.as_of.day, tzinfo=UTC),
                "source_ref": h.get("tradingsymbol"),
            })
        return rows
