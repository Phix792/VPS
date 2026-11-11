# MT5 web trade panel

Two parts:

| File | Where it goes | Public? |
|---|---|---|
| `index.html` | GitHub repo, served with GitHub Pages | Yes, it contains no secrets |
| `server.py`, `.env`, `requirements.txt` | Windows PC/VPS running MT5 | No. Keep `.env` off GitHub |

How a trade flows: browser (GitHub Pages) -> HTTPS -> `server.py` on your VPS -> MT5 terminal -> broker. This is the same path as the phone app after step 1.

## 1. Set up the VPS (Windows)

1. Install MetaTrader 5 from your broker and log in to a **demo** account first.
2. In MT5: Tools > Options > Expert Advisors, tick **Allow algorithmic trading**. Also make sure the AutoTrading button in the toolbar is green.
3. Install Python 3.10+ (64-bit), then in a terminal:

```
pip install -r requirements.txt
copy .env.example .env
```

4. Edit `.env`: fill in your MT5 login, password, server name, a long random `API_KEY`, and your GitHub Pages URL as `ALLOWED_ORIGIN`.
5. Start it (leave MT5 open):

```
python server.py
```

For a more stable run, use `waitress-serve --host 127.0.0.1 --port 5000 server:app`.

## 2. Give the server an HTTPS address

GitHub Pages is HTTPS, and browsers block it from calling plain `http://` addresses. Easiest fix: a free Cloudflare Tunnel.

```
cloudflared tunnel --url http://127.0.0.1:5000
```

It prints an `https://...trycloudflare.com` URL. Use that as the bridge URL in the page. (For a permanent address, set up a named tunnel with your own domain.)

## 3. Publish the page

1. Create a GitHub repo and upload **only `index.html`**.
2. Repo Settings > Pages > deploy from the `main` branch.
3. Open `https://yourname.github.io/yourrepo/`, paste the bridge URL and your API key, press Connect.

The API key is typed into the page each session and kept only in that browser tab. It is never stored in the repo.

## Built-in safety

- Every request needs the `X-API-Key` header. Without it, nobody can trade.
- Only listed symbols, a maximum lot size, a required stop loss and a daily trade cap.
- CORS only allows your GitHub Pages origin.
- The server listens on localhost; only the tunnel exposes it.
- The page shows a DEMO/REAL badge and asks to confirm each order.

## If a trade is rejected

- "AutoTrading disabled": turn on the AutoTrading button and the Expert Advisors option in MT5.
- "Symbol not found": use the exact broker name (check suffixes) in `ALLOWED_SYMBOLS`.
- "Invalid stops": your stop loss is too close; raise the pips.
- "Market closed" / no price: wait for market hours.
- "Cannot connect to MT5": MT5 is closed or logged out on the VPS.
- Unsupported filling mode: handled automatically, but tell me the retcode if it still fails.

Test on a demo account for at least a few days before going live.
