#!/usr/bin/env python3
"""
Market Data API v2.1
- GET /prices?coins=SEI,BTC,ETH          → Live Krypto via CMC
- GET /stocks?tickers=AAPL,SAP.DE,GC=F   → Aktien, ETFs, Rohstoffe, Indizes via Yahoo Finance
- GET /health                             → Server Status
"""

import os
import time
from datetime import datetime, timezone
from functools import lru_cache

import requests
import yfinance as yf
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="Market Data API", version="2.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)

CMC_API_KEY = os.environ.get("CMC_API_KEY", "")
CMC_URL     = "https://pro-api.coinmarketcap.com/v1/cryptocurrency/quotes/latest"

# Simple in-memory cache: {ticker: (timestamp, data)}
_stock_cache: dict = {}
CACHE_TTL = 60  # seconds


# ─────────────────────────────────────────────
#  HEALTH
# ─────────────────────────────────────────────
@app.get("/health")
def health():
    return {"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()}


# ─────────────────────────────────────────────
#  KRYPTO  (CoinMarketCap)
# ─────────────────────────────────────────────
@app.get("/prices")
def get_crypto_prices(coins: str = Query(..., example="SEI,BTC,ETH")):
    if not CMC_API_KEY:
        raise HTTPException(status_code=500, detail="CMC_API_KEY not configured")

    symbols = [s.strip().upper() for s in coins.split(",") if s.strip()]
    if not symbols:
        raise HTTPException(status_code=400, detail="No valid symbols")
    if len(symbols) > 50:
        raise HTTPException(status_code=400, detail="Max 50 coins per request")

    headers = {"Accepts": "application/json", "X-CMC_PRO_API_KEY": CMC_API_KEY}
    params  = {"symbol": ",".join(symbols), "convert": "USD"}

    try:
        resp = requests.get(CMC_URL, headers=headers, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=502, detail=f"CMC API error: {e}")

    if data.get("status", {}).get("error_code") != 0:
        raise HTTPException(status_code=502, detail=data["status"].get("error_message"))

    result = {}
    for symbol in symbols:
        coin = data["data"].get(symbol)
        if not coin:
            result[symbol] = {"error": "not found"}
            continue
        q = coin["quote"]["USD"]
        result[symbol] = {
            "price":          round(q["price"], 8),
            "change_1h":      round(q.get("percent_change_1h")  or 0, 2),
            "change_24h":     round(q.get("percent_change_24h") or 0, 2),
            "change_7d":      round(q.get("percent_change_7d")  or 0, 2),
            "market_cap_usd": round(q.get("market_cap")         or 0, 0),
            "volume_24h_usd": round(q.get("volume_24h")         or 0, 0),
            "rank":           coin.get("cmc_rank"),
        }

    return {
        "timestamp": datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M:%S UTC"),
        "source":    "CoinMarketCap Pro API (Live)",
        "coins":     result,
    }


# ─────────────────────────────────────────────
#  AKTIEN / ETFs / ROHSTOFFE / INDIZES (Yahoo)
# ─────────────────────────────────────────────
def _fetch_ticker(ticker: str) -> dict:
    """Fetch single ticker with cache + retry."""
    now = time.time()

    # Return cached value if fresh
    if ticker in _stock_cache:
        ts, cached_data = _stock_cache[ticker]
        if now - ts < CACHE_TTL:
            return cached_data

    # Set User-Agent to avoid Yahoo rate limiting
    session = requests.Session()
    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        )
    })

    for attempt in range(3):
        try:
            t    = yf.Ticker(ticker, session=session)
            info = t.fast_info

            price = getattr(info, "last_price", None)
            if price is None:
                return {"error": "no price data"}

            prev_close = getattr(info, "previous_close", None)
            currency   = getattr(info, "currency", "USD")
            market_cap = getattr(info, "market_cap", None)
            day_high   = getattr(info, "day_high", None)
            day_low    = getattr(info, "day_low", None)
            year_high  = getattr(info, "year_high", None)
            year_low   = getattr(info, "year_low", None)

            change_24h = None
            if prev_close and prev_close != 0:
                change_24h = round((price - prev_close) / prev_close * 100, 2)

            data = {
                "price":          round(price, 4),
                "currency":       currency,
                "change_24h_pct": change_24h,
                "prev_close":     round(prev_close, 4) if prev_close else None,
                "day_high":       round(day_high, 4)   if day_high   else None,
                "day_low":        round(day_low, 4)    if day_low    else None,
                "year_high":      round(year_high, 4)  if year_high  else None,
                "year_low":       round(year_low, 4)   if year_low   else None,
                "market_cap":     round(market_cap, 0) if market_cap else None,
            }

            # Store in cache
            _stock_cache[ticker] = (time.time(), data)
            return data

        except Exception as e:
            if attempt < 2:
                time.sleep(1 + attempt)  # 1s, 2s backoff
            else:
                return {"error": str(e)}

    return {"error": "failed after retries"}


@app.get("/stocks")
def get_stock_prices(
    tickers: str = Query(
        ...,
        example="AAPL,SAP.DE,GC=F,^GDAXI,EURUSD=X",
    )
):
    ticker_list = [t.strip() for t in tickers.split(",") if t.strip()]
    if not ticker_list:
        raise HTTPException(status_code=400, detail="No valid tickers")
    if len(ticker_list) > 30:
        raise HTTPException(status_code=400, detail="Max 30 tickers per request")

    result = {ticker: _fetch_ticker(ticker) for ticker in ticker_list}

    return {
        "timestamp": datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M:%S UTC"),
        "source":    "Yahoo Finance (~15min Verzögerung während Handelszeiten)",
        "note":      "Indizes: ^GDAXI=DAX ^GSPC=S&P500 ^NDX=Nasdaq100 | Rohstoffe: GC=F=Gold SI=F=Silber BZ=F=Brent | DE-Aktien: SAP.DE BMW.DE etc.",
        "tickers":   result,
    }
