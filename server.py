"""
MT5 trade bridge.

Runs on the same Windows machine/VPS as your MetaTrader 5 terminal.
Your website sends buy/sell requests here; this server validates them
and places the order on your MT5 account.

Secrets live in a .env file on this machine only. Never commit it.
"""
import os
import sys
import hmac
import threading
from datetime import date
from functools import wraps

from dotenv import load_dotenv
from flask import Flask, request, jsonify
from flask_cors import CORS
import MetaTrader5 as mt5

load_dotenv()

MT5_LOGIN = int(os.getenv("MT5_LOGIN", "0"))
MT5_PASSWORD = os.getenv("MT5_PASSWORD", "")
MT5_SERVER = os.getenv("MT5_SERVER", "")
API_KEY = os.getenv("API_KEY", "")
ALLOWED_ORIGIN = os.getenv("ALLOWED_ORIGIN", "")
ALLOWED_SYMBOLS = [s.strip().upper() for s in os.getenv("ALLOWED_SYMBOLS", "EURUSD").split(",") if s.strip()]
MAX_VOLUME = float(os.getenv("MAX_VOLUME", "0.10"))
MAX_TRADES_PER_DAY = int(os.getenv("MAX_TRADES_PER_DAY", "10"))
DEVIATION = int(os.getenv("DEVIATION", "20"))
MAGIC = int(os.getenv("MAGIC", "909090"))

if len(API_KEY) < 24:
    sys.exit("Set API_KEY in .env to a long random string (24+ characters).")
if not ALLOWED_ORIGIN:
    sys.exit("Set ALLOWED_ORIGIN in .env, e.g. https://yourname.github.io")
if not (MT5_LOGIN and MT5_PASSWORD and MT5_SERVER):
    sys.exit("Set MT5_LOGIN, MT5_PASSWORD and MT5_SERVER in .env")

app = Flask(__name__)
CORS(app, origins=[ALLOWED_ORIGIN], allow_headers=["Content-Type", "X-API-Key"])

mt5_lock = threading.Lock()
daily = {"day": date.today(), "count": 0}


def ensure_mt5():
    if mt5.terminal_info() is not None and mt5.account_info() is not None:
        return True
    return mt5.initialize(login=MT5_LOGIN, password=MT5_PASSWORD, server=MT5_SERVER)


def require_key(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        sent = request.headers.get("X-API-Key", "")
        if not hmac.compare_digest(sent.encode(), API_KEY.encode()):
            return jsonify(ok=False, error="Unauthorized"), 401
        return fn(*args, **kwargs)
    return wrapper


def fail(msg, code=400):
    return jsonify(ok=False, error=msg), code


@app.get("/health")
def health():
    return jsonify(ok=True)


@app.get("/config")
@require_key
def config():
    return jsonify(ok=True, symbols=ALLOWED_SYMBOLS, max_volume=MAX_VOLUME,
                   max_trades_per_day=MAX_TRADES_PER_DAY)


@app.get("/account")
@require_key
def account():
    with mt5_lock:
        if not ensure_mt5():
            return fail("Cannot connect to MT5 terminal", 503)
        info = mt5.account_info()
        positions = mt5.positions_get() or []
    return jsonify(
        ok=True,
        login=info.login,
        server=info.server,
        currency=info.currency,
        balance=info.balance,
        equity=info.equity,
        margin_free=info.margin_free,
        trade_mode="demo" if info.trade_mode == 0 else "real" if info.trade_mode == 2 else "contest",
        positions=[
            {"ticket": p.ticket, "symbol": p.symbol,
             "side": "buy" if p.type == 0 else "sell",
             "volume": p.volume, "price_open": p.price_open,
             "profit": p.profit}
            for p in positions
        ],
    )


def pick_filling(symbol_info):
    mode = symbol_info.filling_mode
    if mode & 2:
        return mt5.ORDER_FILLING_IOC
    if mode & 1:
        return mt5.ORDER_FILLING_FOK
    return mt5.ORDER_FILLING_RETURN


@app.post("/trade")
@require_key
def trade():
    data = request.get_json(silent=True) or {}
    symbol = str(data.get("symbol", "")).upper()
    side = str(data.get("side", "")).lower()

    try:
        volume = float(data.get("volume"))
        sl_pips = float(data.get("sl_pips"))
        tp_pips = float(data.get("tp_pips") or 0)
    except (TypeError, ValueError):
        return fail("volume and sl_pips must be numbers")

    if symbol not in ALLOWED_SYMBOLS:
        return fail(f"Symbol not allowed. Allowed: {', '.join(ALLOWED_SYMBOLS)}")
    if side not in ("buy", "sell"):
        return fail("side must be buy or sell")
    if not (0 < volume <= MAX_VOLUME):
        return fail(f"volume must be above 0 and at most {MAX_VOLUME}")
    if sl_pips <= 0:
        return fail("A stop loss (sl_pips) is required")
    if tp_pips < 0:
        return fail("tp_pips cannot be negative")

    today = date.today()
    if daily["day"] != today:
        daily["day"], daily["count"] = today, 0
    if daily["count"] >= MAX_TRADES_PER_DAY:
        return fail("Daily trade limit reached", 429)

    with mt5_lock:
        if not ensure_mt5():
            return fail("Cannot connect to MT5 terminal", 503)
        if not mt5.symbol_select(symbol, True):
            return fail(f"Symbol {symbol} not found at your broker")
        info = mt5.symbol_info(symbol)
        tick = mt5.symbol_info_tick(symbol)
        if info is None or tick is None:
            return fail("No price available (market closed?)", 503)

        pip = info.point * 10 if info.digits in (3, 5) else info.point
        if side == "buy":
            price = tick.ask
            order_type = mt5.ORDER_TYPE_BUY
            sl = price - sl_pips * pip
            tp = price + tp_pips * pip if tp_pips else 0.0
        else:
            price = tick.bid
            order_type = mt5.ORDER_TYPE_SELL
            sl = price + sl_pips * pip
            tp = price - tp_pips * pip if tp_pips else 0.0

        req = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": volume,
            "type": order_type,
            "price": price,
            "sl": round(sl, info.digits),
            "tp": round(tp, info.digits) if tp else 0.0,
            "deviation": DEVIATION,
            "magic": MAGIC,
            "comment": "web-bridge",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": pick_filling(info),
        }
        result = mt5.order_send(req)

    if result is None:
        return fail(f"order_send failed: {mt5.last_error()}", 502)

    if result.retcode != mt5.TRADE_RETCODE_DONE:
        return jsonify(ok=False, error=result.comment, retcode=result.retcode), 400

    daily["count"] += 1
    return jsonify(ok=True, order=result.order, price=result.price,
                   volume=result.volume, retcode=result.retcode)


if __name__ == "__main__":
    # Bind to localhost only. Put an HTTPS tunnel/proxy in front (see README).
    app.run(host="127.0.0.1", port=5000)
