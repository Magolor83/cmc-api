#!/usr/bin/env python3
import os
from datetime import datetime, timezone
import requests
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="CMC Live Price API", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET"], allow_headers=["*"])

CMC_API_KEY = os.environ.get("CMC_API_KEY", "")
CMC_URL = "https://pro-api.coinmarketcap.com/v1/cryptocurrency/quotes/latest"

@app.get("/health")
def health():
    return {"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()}

@app.get("/prices")
def get_prices(coins: str = Query(..., example="SEI,BTC,ETH")):
    if not CMC_API_KEY:
        raise HTTPException(status_code=500, detail="CMC_API_KEY not configured")
    symbols = [s.strip().upper() for s in coins.split(",") if s.strip()]
    if not symbols:
        raise HTTPException(status_code=400, detail="No valid symbols")
    if len(symbols) > 50:
        raise HTTPException(status_code=400, detail="Max 50 coins")
    headers = {"Accepts": "application/json", "X-CMC_PRO_API_KEY": CMC_API_KEY}
    params = {"symbol": ",".join(symbols), "convert": "USD"}
    try:
        resp = requests.get(CMC_URL, headers=headers, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=502, detail=f"CMC API error: {e}")
    if data.get("status", {}).get("error_code") != 0:
        raise HTTPException(status_code=502, detail=f"CMC error: {data['status'].get('error_message')}")
    result = {}
    for symbol in symbols:
        coin = data["data"].get(symbol)
        if not coin:
            result[symbol] = {"error": "not found"}
            continue
        q = coin["quote"]["USD"]
        result[symbol] = {
            "price": round(q["price"], 8),
            "change_1h": round(q.get("percent_change_1h") or 0, 2),
            "change_24h": round(q.get("percent_change_24h") or 0, 2),
            "change_7d": round(q.get("percent_change_7d") or 0, 2),
            "market_cap_usd": round(q.get("market_cap") or 0, 0),
            "volume_24h_usd": round(q.get("volume_24h") or 0, 0),
            "rank": coin.get("cmc_rank"),
        }
    return {"timestamp": datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M:%S UTC"), "source": "CoinMarketCap Pro API (Live)", "coins": result}
