"""
Railway Market Data Server v4.2
Ultra-minimal startup — kein Import beim Start ausser FastAPI
"""

import os
import time
from datetime import datetime, timezone

import requests
from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse

app = FastAPI()

ALPHA_VANTAGE_KEY = os.environ.get("ALPHA_VANTAGE_KEY", "")
CMC_API_KEY       = os.environ.get("CMC_API_KEY", "")

YFINANCE_SUFFIXES = (
    ".PA", ".DE", ".L", ".MI", ".MC", ".AS", ".BR", ".VX",
    ".VI", ".LI", ".IS", ".F", ".SG", ".HK", ".T", ".AX",
    ".TO", ".SA", ".MX", ".KS", ".SS", ".SZ",
)

def uses_yfinance(ticker: str) -> bool:
    if ticker.startswith("^") or "=" in ticker:
        return True
    upper = ticker.upper()
    return any(upper.endswith(s.upper()) for s in YFINANCE_SUFFIXES)


@app.get("/health")
def health():
    return {"status": "ok", "version": "4.2"}


@app.get("/")
def index():
    return {"name": "Railway Market Data Server v4.2", "status": "online"}


@app.get("/stocks")
def get_stocks(tickers: str = Query(...)):
    import yfinance as yf  # lazy import

    ticker_list = [t.strip() for t in tickers.split(",") if t.strip()]
    yf_list = [t for t in ticker_list if uses_yfinance(t)]
    av_list = [t for t in ticker_list if not uses_yfinance(t)]

    results = {}

    # yfinance
    for ticker in yf_list:
        try:
            info = yf.Ticker(ticker).fast_info
            price = float(info.last_price) if info.last_price else None
            prev  = float(info.previous_close) if info.previous_close else None
            currency = getattr(info, "currency", "EUR") or "EUR"
            change_pct = round((price - prev) / prev * 100, 2) if price and prev and prev != 0 else None
            results[ticker] = {
                "price": round(price, 4) if price else None,
                "currency": currency,
                "change_24h_pct": change_pct,
                "prev_close": round(prev, 4) if prev else None,
                "latest_day": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                "source": "yfinance",
            }
        except Exception as e:
            results[ticker] = {"error": str(e), "source": "yfinance"}

    # Alpha Vantage
    for ticker in av_list:
        if not ALPHA_VANTAGE_KEY:
            results[ticker] = {"error": "ALPHA_VANTAGE_KEY fehlt"}
            continue
        try:
            url  = f"https://www.alphavantage.co/query?function=GLOBAL_QUOTE&symbol={ticker}&apikey={ALPHA_VANTAGE_KEY}"
            r    = requests.get(url, timeout=10)
            data = r.json().get("Global Quote", {})
            if not data:
                results[ticker] = {"error": "Keine Daten", "source": "alpha_vantage"}
                continue
            results[ticker] = {
                "price":          round(float(data.get("05. price", 0)), 4),
                "currency":       "USD",
                "change_24h_pct": round(float(data.get("10. change percent", "0%").replace("%", "")), 2),
                "prev_close":     round(float(data.get("08. previous close", 0)), 4),
                "day_high":       float(data.get("03. high", 0)),
                "day_low":        float(data.get("04. low", 0)),
                "volume":         int(data.get("06. volume", 0)),
                "latest_day":     data.get("07. latest trading day", ""),
                "source":         "alpha_vantage",
            }
        except Exception as e:
            results[ticker] = {"error": str(e), "source": "alpha_vantage"}
        time.sleep(0.2)

    return {
        "timestamp": datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M:%S UTC"),
        "tickers": results,
    }


@app.get("/prices")
def get_crypto_prices(coins: str = Query(...)):
    if not CMC_API_KEY:
        return JSONResponse({"error": "CMC_API_KEY fehlt"}, status_code=500)
    coin_list = [c.strip().upper() for c in coins.split(",") if c.strip()]
    try:
        r    = requests.get(
            "https://pro-api.coinmarketcap.com/v1/cryptocurrency/quotes/latest",
            headers={"X-CMC_PRO_API_KEY": CMC_API_KEY, "Accept": "application/json"},
            params={"symbol": ",".join(coin_list), "convert": "USD"},
            timeout=10,
        )
        data = r.json().get("data", {})
        result = {}
        for coin in coin_list:
            if coin in data:
                q = data[coin]["quote"]["USD"]
                result[coin] = {
                    "price":          round(q["price"], 6),
                    "change_1h":      round(q.get("percent_change_1h",  0), 2),
                    "change_24h":     round(q.get("percent_change_24h", 0), 2),
                    "change_7d":      round(q.get("percent_change_7d",  0), 2),
                    "market_cap_usd": q.get("market_cap"),
                    "volume_24h_usd": q.get("volume_24h"),
                    "rank":           data[coin].get("cmc_rank"),
                }
            else:
                result[coin] = {"error": "Nicht gefunden"}
        return {
            "timestamp": datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M:%S UTC"),
            "source": "CoinMarketCap Pro API",
            "coins": result,
        }
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
