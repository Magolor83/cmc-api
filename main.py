#!/usr/bin/env python3
"""
Market Data API v3.0
- GET /prices?coins=SEI,BTC,ETH                    → Live Krypto via CMC
- GET /stocks?tickers=AAPL,SAP.DE,GC=F,^GDAXI     → Aktien, ETFs, Rohstoffe, Forex via Alpha Vantage
- GET /health                                       → Server Status
"""

import os
import time
from datetime import datetime, timezone

import requests
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="Market Data API", version="3.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)

CMC_API_KEY = os.environ.get("CMC_API_KEY", "")
AV_API_KEY  = os.environ.get("AV_API_KEY", "")

CMC_URL = "https://pro-api.coinmarketcap.com/v1/cryptocurrency/quotes/latest"
AV_URL  = "https://www.alphavantage.co/query"

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
#  AKTIEN / ETFs / ROHSTOFFE / FOREX
#  (Alpha Vantage)
# ─────────────────────────────────────────────

# Alpha Vantage ticker mapping für Sonderfälle
AV_TICKER_MAP = {
    # Rohstoffe → Forex/Commodity Symbols
    "GC=F":   ("GLOBAL_QUOTE", "GLD"),      # Gold ETF als Proxy
    "SI=F":   ("GLOBAL_QUOTE", "SLV"),      # Silber ETF als Proxy
    "BZ=F":   ("GLOBAL_QUOTE", "BNO"),      # Brent ETF als Proxy
    "CL=F":   ("GLOBAL_QUOTE", "USO"),      # WTI ETF als Proxy
    # Indizes → ETF Proxies
    "^GSPC":  ("GLOBAL_QUOTE", "SPY"),      # S&P 500 → SPY
    "^GDAXI": ("GLOBAL_QUOTE", "EWG"),      # DAX → iShares Germany ETF
    "^NDX":   ("GLOBAL_QUOTE", "QQQ"),      # Nasdaq 100 → QQQ
    "^DJI":   ("GLOBAL_QUOTE", "DIA"),      # Dow Jones → DIA
    # Forex
    "EURUSD=X": ("FX_INTRADAY", "EUR/USD"),
    "GBPUSD=X": ("FX_INTRADAY", "GBP/USD"),
    "USDJPY=X": ("FX_INTRADAY", "USD/JPY"),
}

def _fetch_av_quote(ticker: str) -> dict:
    """Fetch single ticker from Alpha Vantage with cache."""
    if not AV_API_KEY:
        return {"error": "AV_API_KEY not configured"}

    now = time.time()
    if ticker in _stock_cache:
        ts, cached = _stock_cache[ticker]
        if now - ts < CACHE_TTL:
            return cached

    # Determine function + symbol
    mapped = AV_TICKER_MAP.get(ticker)

    if mapped and mapped[0] == "FX_INTRADAY":
        # Forex
        from_currency, to_currency = mapped[1].split("/")
        params = {
            "function":      "CURRENCY_EXCHANGE_RATE",
            "from_currency": from_currency,
            "to_currency":   to_currency,
            "apikey":        AV_API_KEY,
        }
        try:
            resp = requests.get(AV_URL, params=params, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            rate_data = data.get("Realtime Currency Exchange Rate", {})
            price = float(rate_data.get("5. Exchange Rate", 0))
            result = {
                "price":          round(price, 6),
                "currency":       to_currency,
                "type":           "forex",
                "from_currency":  from_currency,
                "to_currency":    to_currency,
                "last_refreshed": rate_data.get("6. Last Refreshed"),
            }
        except Exception as e:
            return {"error": str(e)}

    else:
        # Stock / ETF / Index-Proxy
        av_symbol = mapped[1] if mapped else ticker.replace(".DE", "")
        # For German stocks, use the raw symbol (AV supports some)
        if ticker.endswith(".DE"):
            av_symbol = ticker  # Try with .DE first
        params = {
            "function": "GLOBAL_QUOTE",
            "symbol":   av_symbol,
            "apikey":   AV_API_KEY,
        }
        try:
            resp = requests.get(AV_URL, params=params, timeout=10)
            resp.raise_for_status()
            data = resp.json()

            # Check for API limit message
            if "Information" in data or "Note" in data:
                msg = data.get("Information") or data.get("Note", "")
                return {"error": f"API limit: {msg[:80]}"}

            quote = data.get("Global Quote", {})
            if not quote or not quote.get("05. price"):
                return {"error": f"no data for {av_symbol}"}

            price      = float(quote.get("05. price", 0))
            prev_close = float(quote.get("08. previous close", 0))
            change_pct = float(quote.get("10. change percent", "0%").replace("%", ""))
            high       = float(quote.get("03. high", 0))
            low        = float(quote.get("04. low", 0))
            volume     = int(quote.get("06. volume", 0))
            latest_day = quote.get("07. latest trading day")

            proxy_note = None
            if mapped:
                proxy_note = f"Proxy ETF für {ticker}: {av_symbol}"

            result = {
                "price":          round(price, 4),
                "currency":       "USD",
                "change_24h_pct": round(change_pct, 2),
                "prev_close":     round(prev_close, 4),
                "day_high":       round(high, 4),
                "day_low":        round(low, 4),
                "volume":         volume,
                "latest_day":     latest_day,
            }
            if proxy_note:
                result["note"] = proxy_note

        except Exception as e:
            return {"error": str(e)}

    _stock_cache[ticker] = (time.time(), result)
    return result


@app.get("/stocks")
def get_stock_prices(
    tickers: str = Query(
        ...,
        example="AAPL,SAP.DE,GC=F,^GDAXI,EURUSD=X",
        description=(
            "Kommagetrennte Ticker. Beispiele:\n"
            "US-Aktien:  AAPL NVDA MSFT TSLA\n"
            "DE-Aktien:  SAP.DE BMW.DE SIE.DE (limitierte AV-Unterstützung)\n"
            "Rohstoffe:  GC=F (Gold/GLD) SI=F (Silber/SLV) BZ=F (Brent/BNO)\n"
            "Indizes:    ^GSPC (SPY) ^GDAXI (EWG) ^NDX (QQQ)\n"
            "Forex:      EURUSD=X GBPUSD=X USDJPY=X\n"
            "⚠️ Free Plan: 25 Requests/Tag, 1 Req/min"
        ),
    )
):
    if not AV_API_KEY:
        raise HTTPException(status_code=500, detail="AV_API_KEY not configured")

    ticker_list = [t.strip() for t in tickers.split(",") if t.strip()]
    if not ticker_list:
        raise HTTPException(status_code=400, detail="No valid tickers")
    if len(ticker_list) > 10:
        raise HTTPException(status_code=400, detail="Max 10 tickers per request (Alpha Vantage free: 25 req/day)")

    result = {}
    for i, ticker in enumerate(ticker_list):
        if i > 0:
            time.sleep(1.2)  # Respect 1 req/min rate limit on free plan
        result[ticker] = _fetch_av_quote(ticker)

    return {
        "timestamp": datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M:%S UTC"),
        "source":    "Alpha Vantage API",
        "note":      "Rohstoffe & Indizes via ETF-Proxy | Free Plan: 25 req/Tag",
        "tickers":   result,
    }
