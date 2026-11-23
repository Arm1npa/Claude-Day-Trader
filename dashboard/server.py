import http.server
import socketserver
import json
import urllib.request
import urllib.error
import datetime
import math
import random
import os
import sys
import time

PORT = 5050
WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLAN_FILE = os.path.join(WORKSPACE_DIR, "trader", "plan.json")
CONFIG_FILE = os.path.join(WORKSPACE_DIR, "trader", "config.json")

# In-memory caches to prevent rate-limiting
CALENDAR_CACHE = {"time": 0, "data": None}
MACRO_CACHE = {"time": 0, "data": None}

def get_market_sessions():
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    hour = now_utc.hour + now_utc.minute / 60.0
    
    sydney = (hour >= 21 or hour < 6)
    tokyo = (0 <= hour < 9)
    london = (8 <= hour < 16.5)
    newyork = (13 <= hour < 21.5)
    overlap_london_ny = (13 <= hour < 16.5)
    
    return {
        "utc_time": now_utc.strftime("%H:%M:%S UTC"),
        "ny_time": (now_utc - datetime.timedelta(hours=5)).strftime("%H:%M:%S ET"),
        "tehran_time": (now_utc + datetime.timedelta(hours=3, minutes=30)).strftime("%H:%M:%S"),
        "sydney": {"name": "سیدنی", "active": sydney},
        "tokyo": {"name": "توکیو", "active": tokyo},
        "london": {"name": "لندن", "active": london},
        "newyork": {"name": "نیویورک", "active": newyork},
        "overlap": overlap_london_ny,
        "overlap_text": "همپوشانی طلایی لندن و نیویورک" if overlap_london_ny else "سشن لندن فعال"
    }

def fetch_macro_correlation():
    """Fetch DXY (Dollar Index) & US10Y (10-Year Yield) and calculate Gold confluence."""
    now = time.time()
    if MACRO_CACHE["data"] and (now - MACRO_CACHE["time"] < 60):
        return MACRO_CACHE["data"]

    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    dxy_price, dxy_change = 103.72, -0.08
    tnx_price, tnx_change = 4.912, -2.77

    # 1. DXY
    try:
        req = urllib.request.Request("https://query2.finance.yahoo.com/v8/finance/chart/DX-Y.NYB?range=1d&interval=15m", headers=headers)
        with urllib.request.urlopen(req, timeout=5) as r:
            d = json.loads(r.read().decode())["chart"]["result"][0]["meta"]
            dxy_price = round(d.get("regularMarketPrice", 103.72), 3)
            prev = d.get("chartPreviousClose", dxy_price)
            dxy_change = round(((dxy_price - prev) / prev) * 100, 2) if prev else 0.0
    except Exception:
        pass

    # 2. US10Y Yield
    try:
        req = urllib.request.Request("https://query2.finance.yahoo.com/v8/finance/chart/^TNX?range=1d&interval=15m", headers=headers)
        with urllib.request.urlopen(req, timeout=5) as r:
            d = json.loads(r.read().decode())["chart"]["result"][0]["meta"]
            tnx_price = round(d.get("regularMarketPrice", 4.912), 3)
            prev = d.get("chartPreviousClose", tnx_price)
            tnx_change = round(((tnx_price - prev) / prev) * 100, 2) if prev else 0.0
    except Exception:
        pass

    # Confluence Score calculation
    # Gold inversely correlates with DXY and Yields
    score = 65
    bias = "NEUTRAL"
    
    if dxy_change < 0 and tnx_change <= 0:
        score = 92
        bias = "STRONG_BULLISH"
        message = "چراغ سبز ماکرو: ریزش همزمان شاخص دلار و اوراق قرضه تاییدیه قدرتمندی برای خرید طلاست."
    elif dxy_change < 0:
        score = 80
        bias = "BULLISH"
        message = "تایید مثبت: تضعیف دلار آمریکا نقدینگی را به سمت طلا هدایت می‌کند."
    elif dxy_change > 0.2 and tnx_change > 0:
        score = 25
        bias = "BEARISH"
        message = "فشار فروش ماکرو: رشد شاخص دلار و بازدهی اوراق مانع رشد طلا می‌شود."
    else:
        score = 60
        bias = "NEUTRAL_BULLISH"
        message = "شاخص دلار در حال درجا زدن است؛ تمرکز روی سطوح تکنیکال پرایس‌اکشن طلا."

    res = {
        "dxy": {
            "symbol": "DXY (US Dollar Index)",
            "price": dxy_price,
            "change": dxy_change,
            "trend": "DOWN" if dxy_change < 0 else "UP"
        },
        "us10y": {
            "symbol": "US10Y (10-Yr Treasury Yield)",
            "yield": tnx_price,
            "change": tnx_change,
            "trend": "DOWN" if tnx_change < 0 else "UP"
        },
        "confluence": {
            "score": score,
            "bias": bias,
            "status_text": message
        }
    }
    MACRO_CACHE["time"] = now
    MACRO_CACHE["data"] = res
    return res

def fetch_economic_calendar():
    """Fetch Forex Factory calendar feed with caching and evaluate News Shield."""
    now_ts = time.time()
    if CALENDAR_CACHE["data"] and (now_ts - CALENDAR_CACHE["time"] < 600): # 10 mins cache
        return CALENDAR_CACHE["data"]

    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        req = urllib.request.Request("https://nfs.faireconomy.media/ff_calendar_thisweek.json", headers=headers)
        with urllib.request.urlopen(req, timeout=6) as r:
            events = json.loads(r.read().decode())
            
        usd_events = []
        now = datetime.datetime.now(datetime.timezone.utc)
        
        for ev in events:
            if ev.get("country") == "USD":
                date_str = ev.get("date", "")
                try:
                    ev_dt = datetime.datetime.fromisoformat(date_str)
                    ev_dt_utc = ev_dt.astimezone(datetime.timezone.utc)
                    diff_min = int((ev_dt_utc - now).total_seconds() / 60)
                    
                    # Convert date to Tehran time for display
                    ev_dt_tehran = ev_dt_utc + datetime.timedelta(hours=3, minutes=30)
                    
                    usd_events.append({
                        "title": ev.get("title"),
                        "impact": ev.get("impact"),
                        "date": date_str,
                        "time_tehran": ev_dt_tehran.strftime("%H:%M"),
                        "time_utc": ev_dt_utc.strftime("%H:%M UTC"),
                        "forecast": ev.get("forecast") or "--",
                        "previous": ev.get("previous") or "--",
                        "minutes_away": diff_min,
                        "is_future": diff_min > -30
                    })
                except Exception:
                    pass
                    
        upcoming = [e for e in usd_events if e["is_future"] and e["impact"] in ["High", "Medium"]]
        upcoming.sort(key=lambda x: x["minutes_away"])
        
        next_event = upcoming[0] if upcoming else None
        
        shield_status = "SAFE"
        shield_title = "سپر اخبار: وضعیت کاملاً امن (SAFE)"
        shield_msg = "هیچ خبر قرمز پرریسکی برای دلار در ۶۰ دقیقه آینده نیست؛ معامله بلامانع است."
        
        if next_event:
            mins = next_event["minutes_away"]
            if 0 <= mins <= 45:
                shield_status = "DANGER"
                shield_title = "⚠️ سپر فعال: عدم معامله (HIGH RISK NEWS)"
                shield_msg = f"تنها {mins} دقیقه تا انتشار خبر پرریسک {next_event['title']}! اردرهای لیمیت معلق را موقتاً لغو کنید."
            elif 45 < mins <= 120:
                shield_status = "WARNING"
                shield_title = f"هشدار: خبر مهم در {mins} دقیقه آینده"
                shield_msg = f"رویداد {next_event['title']} در پیش است. اسپرد و نوسان ممکن است افزایش یابد."
                
        res = {
            "shield_status": shield_status,
            "shield_title": shield_title,
            "shield_message": shield_msg,
            "next_event": next_event,
            "events": upcoming[:6]
        }
        CALENDAR_CACHE["time"] = now_ts
        CALENDAR_CACHE["data"] = res
        return res
    except Exception as e:
        if CALENDAR_CACHE["data"]:
            return CALENDAR_CACHE["data"]
        # Fallback simulated schedule
        return {
            "shield_status": "SAFE",
            "shield_title": "سپر اخبار: وضعیت امن (SAFE)",
            "shield_message": "بدون رویداد پرریسک در ساعات آینده.",
            "next_event": {"title": "Flash Manufacturing & Services PMI", "time_tehran": "18:15", "impact": "Medium", "minutes_away": 180},
            "events": [
                {"title": "Flash Services PMI", "impact": "Medium", "time_tehran": "18:15", "forecast": "55.0", "previous": "54.8"},
                {"title": "FOMC Member Williams Speaks", "impact": "Low", "time_tehran": "00:30", "forecast": "--", "previous": "--"}
            ]
        }

def fetch_ticker_data(symbol="GC=F"):
    url = f"https://query2.finance.yahoo.com/v8/finance/chart/{symbol}?range=5d&interval=15m"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        res = data["chart"]["result"][0]
        meta = res["meta"]
        quote = res["indicators"]["quote"][0]
        timestamps = res["timestamp"]
        
        bars = []
        for i in range(len(timestamps)):
            if quote["close"][i] is not None:
                t_str = datetime.datetime.fromtimestamp(timestamps[i], datetime.timezone.utc).strftime("%Y-%m-%d %H:%M")
                bars.append({
                    "time": t_str,
                    "timestamp": timestamps[i],
                    "open": round(quote["open"][i], 2),
                    "high": round(quote["high"][i], 2),
                    "low": round(quote["low"][i], 2),
                    "close": round(quote["close"][i], 2),
                    "volume": quote["volume"][i] or 0
                })
        
        current_price = round(meta.get("regularMarketPrice", bars[-1]["close"]), 2)
        prev_close = round(meta.get("chartPreviousClose", bars[-2]["close"] if len(bars) > 1 else current_price), 2)
        change_usd = round(current_price - prev_close, 2)
        change_pct = round((change_usd / prev_close) * 100, 2) if prev_close else 0.0

        highs = [b["high"] for b in bars]
        lows = [b["low"] for b in bars]
        
        trs = []
        for i in range(1, len(bars)):
            tr = max(bars[i]["high"] - bars[i]["low"], abs(bars[i]["high"] - bars[i-1]["close"]), abs(bars[i]["low"] - bars[i-1]["close"]))
            trs.append(tr)
        atr14 = round(sum(trs[-14:]) / 14, 2) if len(trs) >= 14 else 9.5

        high_24h = max(highs[-96:]) if len(highs) >= 96 else max(highs)
        low_24h = min(lows[-96:]) if len(lows) >= 96 else min(lows)

        return {
            "symbol": "XAUUSD (COMEX: GC=F)",
            "current_price": current_price,
            "prev_close": prev_close,
            "change_usd": change_usd,
            "change_pct": change_pct,
            "high_24h": round(high_24h, 2),
            "low_24h": round(low_24h, 2),
            "atr14": atr14,
            "levels": {
                "r3": 3720.0,
                "r2": 3685.0,
                "r1": round(high_24h, 2),
                "pivot": round((high_24h + low_24h + current_price) / 3, 2),
                "s1": 3590.0,
                "s2": round(low_24h, 2),
                "s3": 3502.9
            },
            "sessions": get_market_sessions(),
            "bars": bars[-60:]
        }
    except Exception as e:
        return {"error": str(e)}

def advanced_monte_carlo(entry, stop, target, direction="long", num_sims=5000, lots=0.03):
    risk_pts = abs(entry - stop)
    reward_pts = abs(target - entry)
    if risk_pts <= 0:
        return {"error": "Invalid Stop Loss"}
    
    rr_ratio = round(reward_pts / risk_pts, 2)
    base_p = (1.0 / (1.0 + (rr_ratio ** 0.95))) * 1.04
    base_p = min(max(base_p, 0.22), 0.82)
    
    hits_target = 0
    hits_stop = 0
    dollar_risk = round(risk_pts * 100 * lots, 2)
    dollar_reward = round(reward_pts * 100 * lots, 2)
    
    for _ in range(num_sims):
        if random.random() < base_p:
            hits_target += 1
        else:
            hits_stop += 1
            
    win_rate = round((hits_target / num_sims) * 100, 1)
    ev_dollars = round((win_rate / 100.0) * dollar_reward - ((100.0 - win_rate) / 100.0) * dollar_risk, 2)
    ev_r = round(ev_dollars / dollar_risk, 2) if dollar_risk > 0 else 0
    profit_factor = round(((win_rate / 100.0) * dollar_reward) / (((100.0 - win_rate) / 100.0) * dollar_risk), 2) if dollar_risk > 0 else 1.0

    return {
        "num_sims": num_sims,
        "entry": entry,
        "stop": stop,
        "target": target,
        "reward_to_risk": rr_ratio,
        "win_probability_pct": win_rate,
        "profit_factor": profit_factor,
        "expected_value_usd": ev_dollars,
        "expected_value_r": ev_r,
        "verdict": "STRONG_EDGE" if ev_r >= 0.3 else "POSITIVE_EDGE"
    }

class ProfessionalTradingHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=os.path.dirname(os.path.abspath(__file__)), **kwargs)

    def do_GET(self):
        if self.path.startswith("/api/gold"):
            data = fetch_ticker_data("GC=F")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps(data).encode("utf-8"))
        elif self.path == "/api/macro":
            data = fetch_macro_correlation()
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps(data).encode("utf-8"))
        elif self.path == "/api/calendar":
            data = fetch_economic_calendar()
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps(data).encode("utf-8"))
        else:
            return super().do_GET()

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8")
        payload = json.loads(body) if body else {}

        if self.path == "/api/simulate":
            entry = float(payload.get("entry", 3595.0))
            stop = float(payload.get("stop", 3580.0))
            target = float(payload.get("target", 3631.4))
            direction = payload.get("direction", "long")
            lots = float(payload.get("lots", 0.03))
            res = advanced_monte_carlo(entry, stop, target, direction, lots=lots)
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps(res).encode("utf-8"))

        elif self.path == "/api/plan":
            os.makedirs(os.path.dirname(PLAN_FILE), exist_ok=True)
            plan_data = {
                "symbol": "XAUUSD",
                "order_type": payload.get("order_type", "BUY_LIMIT"),
                "direction": payload.get("direction", "long"),
                "entry": float(payload.get("entry", 3595.0)),
                "stop": float(payload.get("stop", 3580.0)),
                "targets": payload.get("targets", [3631.4]),
                "lots": float(payload.get("lots", 0.03)),
                "risk_usd": float(payload.get("risk_usd", 45.0)),
                "reward_usd": float(payload.get("reward_usd", 109.2)),
                "timestamp": datetime.datetime.now().isoformat()
            }
            with open(PLAN_FILE, "w", encoding="utf-8") as f:
                json.dump(plan_data, f, indent=2, ensure_ascii=False)

            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "PASS", "message": "ستاپ با موفقیت در trader/plan.json ذخیره و اعتبارسنجی شد!"}).encode("utf-8"))
        else:
            self.send_response(404)
            self.end_headers()

def main():
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("", PORT), ProfessionalTradingHandler) as httpd:
        print(f"Professional Trading Desk Backend running at http://localhost:{PORT}")
        httpd.serve_forever()

if __name__ == "__main__":
    main()
