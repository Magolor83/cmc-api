"""
Railway Market Data Server — Updated Version
Hybrid: yfinance (EU/Global) + Alpha Vantage (US Fallback)
 
Endpoints:
  /stocks?tickers=RMS.PA,SAP.DE,AAPL,^GDAXI
  /prices?coins=BTC,ETH,SEI   (CMC via bestehende Logik)

Ticker-Format:
  US:       AAPL, MSFT, TSLA
  Paris:    RMS.PA, AI.PA, BNP.PA
  Xetra:    SAP.DE, BMW.DE
  London:   SHEL.L, AZN.L
  Milan:    ENI.MI
  Madrid:   ITX.MC
  Indizes:  ^GDAXI, ^GSPC, ^DJI
  Gold:     GC=F
  Silber:   SI=F
  EUR/USD:  EURUSD=X
"""

import os
import json
import time
from datetime import datetime, timezone
from flask import Flask, jsonify, request
import yfinance as yf
import requests

app = Flask(__name__)

# ── Konfiguration ──────────────────────────────────────────────────────────────
ALPHA_VANTAGE_KEY = os.environ.get("ALPHA_VANTAGE_KEY", "")
CMC_API_KEY       = os.environ.get("CMC_API_KEY", "")

# Suffixe die yfinance nativ kennt (Euronext, Xetra, LSE, etc.)
YFINANCE_SUFFIXES = {
    ".PA", ".DE", ".L", ".MI", ".MC", ".AS", ".BR", ".VX",
    ".VI", ".LI", ".IS", ".F", ".SG", ".HK", ".T", ".AX",
    ".TO", ".SA", ".MX", ".KS", ".SS", ".SZ",
}

# Symbole die immer yfinance verwenden (Indizes, Futures, FX)
YFINANCE_PREFIXES = {"^", "=X"}
YFINANCE_FUTURES  = {"=F"}

# ── Helper ─────────────────────────────────────────────────────────────────────

def uses_yfinance(ticker: str) -> bool:
    """Entscheidet ob yfinance oder Alpha Vantage verwendet wird."""
    upper = ticker.upper()
    # Indizes: ^GDAXI, ^GSPC
    if ticker.startswith("^"):
        return True
    # FX: EURUSD=X, Futures: GC=F, SI=F
    if "=" in ticker:
        return True
    # Europäische / globale Börsen via Suffix
    for suffix in YFINANCE_SUFFIXES:
        if upper.endswith(suffix.upper()):
            return True
    return False


def fetch_yfinance(tickers: list) -> dict:
    """Holt Live-Kurse via yfinance für alle übergebenen Ticker."""
    result = {}
    if not tickers:
        return result

    try:
        # Batch-Download für alle Ticker auf einmal
        data = yf.download(
            tickers=tickers,
            period="2d",
            interval="1d",
            group_by="ticker",
            auto_adjust=True,
            progress=False,
            threads=True,
        )

        for ticker in tickers:
            try:
                info = yf.Ticker(ticker).fast_info
                price     = float(info.last_price)    if info.last_price    else None
                prev      = float(info.previous_close) if info.previous_close else None
                currency  = info.currency or "USD"
                change_pct = round((price - prev) / prev * 100, 2) if price and prev else None

                result[ticker] = {
                    "price":          round(price, 4) if price else None,
                    "currency":       currency,
                    "change_24h_pct": change_pct,
                    "prev_close":     round(prev, 4) if prev else None,
                    "source":         "yfinance (Yahoo Finance)",
                    "latest_day":     datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                }
            except Exception as e:
                result[ticker] = {"error": str(e), "source": "yfinance"}

    except Exception as e:
        for ticker in tickers:
            result[ticker] = {"error": str(e), "source": "yfinance"}

    return result


def fetch_alpha_vantage(tickers: list) -> dict:
    """Holt Kurse via Alpha Vantage für US-Aktien (Fallback)."""
    result = {}
    if not tickers or not ALPHA_VANTAGE_KEY:
        for t in tickers:
            result[t] = {"error": "Alpha Vantage API key missing", "source": "alpha_vantage"}
        return result

    for ticker in tickers:
        try:
            url = (
                f"https://www.alphavantage.co/query"
                f"?function=GLOBAL_QUOTE&symbol={ticker}&apikey={ALPHA_VANTAGE_KEY}"
            )
            r = requests.get(url, timeout=10)
            data = r.json().get("Global Quote", {})

            if not data:
                result[ticker] = {"error": "No data from Alpha Vantage", "source": "alpha_vantage"}
                continue

            price      = float(data.get("05. price", 0))
            prev_close = float(data.get("08. previous close", 0))
            change_pct = float(data.get("10. change percent", "0%").replace("%", ""))

            result[ticker] = {
                "price":          round(price, 4),
                "currency":       "USD",
                "change_24h_pct": round(change_pct, 2),
                "prev_close":     round(prev_close, 4),
                "day_high":       float(data.get("03. high", 0)),
                "day_low":        float(data.get("04. low", 0)),
                "volume":         int(data.get("06. volume", 0)),
                "latest_day":     data.get("07. latest trading day", ""),
                "source":         "Alpha Vantage",
            }
        except Exception as e:
            result[ticker] = {"error": str(e), "source": "alpha_vantage"}

        time.sleep(0.2)  # Rate-Limit schonen

    return result


# ── Endpoints ──────────────────────────────────────────────────────────────────

@app.route("/stocks")
def get_stocks():
    """
    GET /stocks?tickers=RMS.PA,SAP.DE,AAPL,^GDAXI,GC=F,EURUSD=X
    Automatische Quellen-Wahl: yfinance für EU/Global, Alpha Vantage für US.
    """
    raw = request.args.get("tickers", "")
    if not raw:
        return jsonify({"error": "Bitte ?tickers=TICKER1,TICKER2 angeben"}), 400

    tickers = [t.strip() for t in raw.split(",") if t.strip()]

    # Aufteilen nach Datenquelle
    yf_tickers  = [t for t in tickers if uses_yfinance(t)]
    av_tickers  = [t for t in tickers if not uses_yfinance(t)]

    results = {}
    if yf_tickers:
        results.update(fetch_yfinance(yf_tickers))
    if av_tickers:
        results.update(fetch_alpha_vantage(av_tickers))

    return jsonify({
        "timestamp": datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M:%S UTC"),
        "sources":   {
            "yfinance":      yf_tickers,
            "alpha_vantage": av_tickers,
        },
        "note": "yfinance: EU/Global/Indizes/FX/Futures | Alpha Vantage: US-Aktien",
        "tickers": results,
    })


@app.route("/prices")
def get_crypto_prices():
    """
    GET /prices?coins=BTC,ETH,SEI
    Bestehende CMC-Logik bleibt unverändert.
    """
    raw = request.args.get("coins", "")
    if not raw:
        return jsonify({"error": "Bitte ?coins=BTC,ETH angeben"}), 400

    coins = [c.strip().upper() for c in raw.split(",") if c.strip()]

    if not CMC_API_KEY:
        return jsonify({"error": "CMC_API_KEY nicht gesetzt"}), 500

    try:
        url = "https://pro-api.coinmarketcap.com/v1/cryptocurrency/quotes/latest"
        headers = {"X-CMC_PRO_API_KEY": CMC_API_KEY, "Accept": "application/json"}
        params  = {"symbol": ",".join(coins), "convert": "USD"}
        r = requests.get(url, headers=headers, params=params, timeout=10)
        data = r.json().get("data", {})

        result = {}
        for coin in coins:
            if coin in data:
                q = data[coin]["quote"]["USD"]
                result[coin] = {
                    "price":        round(q["price"], 6),
                    "change_1h":    round(q.get("percent_change_1h",  0), 2),
                    "change_24h":   round(q.get("percent_change_24h", 0), 2),
                    "change_7d":    round(q.get("percent_change_7d",  0), 2),
                    "market_cap_usd": q.get("market_cap"),
                    "volume_24h_usd": q.get("volume_24h"),
                    "rank":         data[coin].get("cmc_rank"),
                }
            else:
                result[coin] = {"error": "Nicht gefunden"}

        return jsonify({
            "timestamp": datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M:%S UTC"),
            "source":    "CoinMarketCap Pro API (Live)",
            "coins":     result,
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/health")
def health():
    return jsonify({"status": "ok", "version": "2.0-yfinance"})


@app.route("/")
def index():
    return jsonify({
        "name":    "Railway Market Data Server v2.0",
        "endpoints": {
            "/stocks": "?tickers=RMS.PA,SAP.DE,AAPL,^GDAXI,GC=F,EURUSD=X",
            "/prices": "?coins=BTC,ETH,SEI",
            "/health": "Statuscheck",
        },
        "sources": {
            "EU/Global/Indizes/FX": "yfinance (Yahoo Finance, kostenlos, unlimitiert)",
            "US-Aktien":            "Alpha Vantage (kostenlos, 25 req/Tag)",
            "Krypto":               "CoinMarketCap Pro API",
        }
    })


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
