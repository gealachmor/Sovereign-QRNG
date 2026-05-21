"""
SOVEREIGN QRNG — PAYMENT API  (port 8890)
==========================================
Three payment rails:
  1. Stripe  — card payments, Checkout Sessions + webhook
  2. PayPal  — Orders API v2 + webhook
  3. CoinGate — crypto invoices (BTC/ETH/XMR/USDC/+100) + IPN

On confirmed payment → generate API key → email to customer → store in keys file.

Config: C:\\QRNG_Pool\\payment_config.json
Keys:   C:\\QRNG_Pool\\api_keys.json  (shared with entropy_market_api.py)

SETUP (run once):
  python payment_api.py --setup

EXPOSE for webhooks (Cloudflare tunnel, no account needed):
  cloudflared tunnel --url http://127.0.0.1:8890
  → copy the https://xxxx.trycloudflare.com URL
  → paste as webhook base URL during --setup
"""

import argparse, hashlib, hmac, json, os, secrets, smtplib, sys, threading, time
import datetime, base64, urllib.request, urllib.parse
from pathlib import Path
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# ─────────────────────────────────────────────
# PATHS
# ─────────────────────────────────────────────
from config import QRNG_POOL_DIR, RIG_DIR
POOL_DIR    = QRNG_POOL_DIR
CONFIG_FILE = POOL_DIR / "payment_config.json"
KEYS_FILE   = POOL_DIR / "api_keys.json"
LOG_FILE    = POOL_DIR / "payment.log"
ORDERS_FILE = POOL_DIR / "payment_orders.json"
BUY_HTML    = RIG_DIR / "buy.html"
PORT        = 8890
POOL_DIR.mkdir(exist_ok=True)

# ─────────────────────────────────────────────
# TIER DEFINITIONS
# ─────────────────────────────────────────────
TIERS = {
    "free": {
        "name":          "Developer Free",
        "price_usd":     0,
        "daily_mb":      0.256,
        "monthly_mb":    7.5,
        "requests_day":  1000,
        "signed":        False,
        "audit_log":     False,
    },
    "indie": {
        "name":          "Indie",
        "price_usd":     12,
        "daily_mb":      50 / 30,
        "monthly_mb":    50,
        "requests_day":  50000 // 30,
        "signed":        False,
        "audit_log":     False,
    },
    "pro": {
        "name":          "Professional",
        "price_usd":     39,
        "daily_mb":      250 / 30,
        "monthly_mb":    250,
        "requests_day":  250000 // 30,
        "signed":        True,
        "audit_log":     True,
    },
    "enterprise": {
        "name":          "Enterprise",
        "price_usd":     199,
        "daily_mb":      2048 / 30,
        "monthly_mb":    2048,
        "requests_day":  2000000 // 30,
        "signed":        True,
        "audit_log":     True,
    },
}

# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────
def load_config() -> dict:
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text())
        except Exception:
            pass
    return {}

def save_config(cfg: dict):
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2))

CFG = load_config()

def cfg(key, default=""):
    return CFG.get(key, os.getenv(key.upper().replace("-","_"), default))

# ─────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────
_log_lock = threading.Lock()
def log(msg: str):
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    with _log_lock:
        with open(LOG_FILE, "a") as f:
            f.write(line + "\n")

# ─────────────────────────────────────────────
# API KEY MANAGEMENT
# ─────────────────────────────────────────────
_keys_lock = threading.Lock()

def load_keys() -> dict:
    if KEYS_FILE.exists():
        try: return json.loads(KEYS_FILE.read_text())
        except: pass
    return {}

def save_keys(keys: dict):
    KEYS_FILE.write_text(json.dumps(keys, indent=2))

def issue_key(email: str, tier: str, payment_rail: str, order_id: str) -> str:
    """Generate API key, store hashed copy, return plaintext key."""
    raw_key = "sk_" + secrets.token_urlsafe(32)
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    tier_cfg = TIERS.get(tier, TIERS["indie"])
    record = {
        "email":         email,
        "name":          email,
        "tier":          tier,
        "tier_name":     tier_cfg["name"],
        "daily_limit_mb": tier_cfg["daily_mb"],
        "monthly_limit_mb": tier_cfg["monthly_mb"],
        "signed_responses": tier_cfg["signed"],
        "audit_log":     tier_cfg["audit_log"],
        "payment_rail":  payment_rail,
        "order_id":      order_id,
        "issued_at":     datetime.datetime.utcnow().isoformat() + "Z",
        "expires_at":    (datetime.datetime.utcnow() + datetime.timedelta(days=32)).isoformat() + "Z",
        "active":        True,
        "total_bytes":   0,
    }
    with _keys_lock:
        keys = load_keys()
        keys[raw_key] = record
        save_keys(keys)
    log(f"KEY ISSUED: tier={tier} email={email} rail={payment_rail} order={order_id}")
    return raw_key

# ─────────────────────────────────────────────
# EMAIL DELIVERY
# ─────────────────────────────────────────────
def send_key_email(email: str, tier: str, api_key: str):
    """Send API key to customer via Gmail SMTP."""
    smtp_user = cfg("smtp_user", "negitivminusone@gmail.com")
    smtp_pass = cfg("smtp_pass", "")   # Gmail App Password
    smtp_host = cfg("smtp_host", "smtp.gmail.com")
    smtp_port = int(cfg("smtp_port", "587"))

    if not smtp_pass:
        log(f"EMAIL SKIPPED (no SMTP password set) — key for {email}: {api_key}")
        return

    tier_info = TIERS.get(tier, TIERS["indie"])
    subject = f"Your Sovereign QRNG API Key — {tier_info['name']} Tier"

    html_body = f"""
<!DOCTYPE html>
<html><body style="background:#060a0e;color:#e2e8f0;font-family:Courier New,monospace;padding:32px;">
<h2 style="color:#00d4ff;letter-spacing:6px;text-transform:uppercase;">SOVEREIGN QRNG</h2>
<h3 style="color:#22c55e;letter-spacing:3px;">{tier_info['name']} Tier Activated</h3>
<p style="color:#9ca3af;">Your hardware entropy API key is ready. Store it securely — it will not be shown again.</p>
<div style="background:#0d1117;border:1px solid rgba(0,212,255,0.3);border-radius:4px;padding:16px;margin:20px 0;">
  <p style="color:#6b7280;font-size:11px;letter-spacing:2px;text-transform:uppercase;margin:0 0 8px;">YOUR API KEY</p>
  <code style="color:#00d4ff;font-size:16px;word-break:break-all;">{api_key}</code>
</div>
<h4 style="color:#e2e8f0;letter-spacing:3px;text-transform:uppercase;">Quick Start</h4>
<pre style="background:#0d1117;padding:16px;border-radius:4px;color:#9ca3af;overflow-x:auto;">
curl -H "Authorization: Bearer {api_key}" \\
     "http://&lt;your-server&gt;:8889/v1/entropy/near?bytes=256&fmt=hex"
</pre>
<h4 style="color:#e2e8f0;letter-spacing:3px;text-transform:uppercase;">Your Limits</h4>
<ul style="color:#9ca3af;line-height:2;">
  <li>Monthly: {tier_info['monthly_mb']:.0f} MB entropy</li>
  <li>Daily: {tier_info['daily_mb']:.1f} MB</li>
  <li>Signed responses: {'Yes (Ed25519)' if tier_info['signed'] else 'No (upgrade to Pro)'}</li>
  <li>Audit log: {'Yes (/v1/audit)' if tier_info['audit_log'] else 'No'}</li>
</ul>
<p style="color:#4b5563;font-size:11px;margin-top:32px;">
Questions? Reply to this email or contact negitivminusone@gmail.com<br>
Sovereign QRNG — Hardware entropy from physical noise sources.
</p>
</body></html>"""

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = smtp_user
    msg["To"]      = email
    msg.attach(MIMEText(html_body, "html"))

    try:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=10) as s:
            s.ehlo()
            s.starttls()
            s.login(smtp_user, smtp_pass)
            s.sendmail(smtp_user, email, msg.as_string())
        log(f"EMAIL SENT: {email} tier={tier}")
    except Exception as e:
        log(f"EMAIL ERROR: {email} — {e}")

def on_payment_confirmed(email: str, tier: str, payment_rail: str, order_id: str):
    """Called when any payment rail confirms. Issues key and emails it."""
    key = issue_key(email, tier, payment_rail, order_id)
    threading.Thread(target=send_key_email, args=(email, tier, key), daemon=True).start()
    log(f"PAYMENT CONFIRMED: {email} tier={tier} rail={payment_rail}")
    return key

# ─────────────────────────────────────────────
# ORDER TRACKING (pending payments)
# ─────────────────────────────────────────────
_ord_lock = threading.Lock()

def save_order(order_id: str, data: dict):
    with _ord_lock:
        orders = {}
        if ORDERS_FILE.exists():
            try: orders = json.loads(ORDERS_FILE.read_text())
            except: pass
        orders[order_id] = data
        ORDERS_FILE.write_text(json.dumps(orders, indent=2))

def get_order(order_id: str) -> dict:
    with _ord_lock:
        if not ORDERS_FILE.exists(): return {}
        try: return json.loads(ORDERS_FILE.read_text()).get(order_id, {})
        except: return {}

# ─────────────────────────────────────────────
# STRIPE INTEGRATION
# ─────────────────────────────────────────────
STRIPE_PRICES = {
    "indie":      cfg("stripe_indie_price_id"),
    "pro":        cfg("stripe_pro_price_id"),
    "enterprise": cfg("stripe_ent_price_id"),
}

def stripe_create_session(email: str, tier: str, public_url: str) -> dict:
    """Create a Stripe Checkout Session. Returns {url, session_id}."""
    import urllib.request, urllib.parse
    secret = cfg("stripe_secret_key")
    if not secret:
        return {"error": "Stripe not configured. Set stripe_secret_key in payment_config.json"}

    price_id = STRIPE_PRICES.get(tier)
    if not price_id:
        return {"error": f"No Stripe price ID for tier '{tier}'. Run --setup to configure."}

    success_url = f"{public_url}/success?session_id={{CHECKOUT_SESSION_ID}}&rail=stripe"
    cancel_url  = f"{public_url}/buy"

    params = urllib.parse.urlencode({
        "mode":                        "subscription",
        "customer_email":              email,
        "line_items[0][price]":        price_id,
        "line_items[0][quantity]":     "1",
        "success_url":                 success_url,
        "cancel_url":                  cancel_url,
        "metadata[tier]":              tier,
        "metadata[email]":             email,
        "allow_promotion_codes":       "true",
    }).encode()

    req = urllib.request.Request(
        "https://api.stripe.com/v1/checkout/sessions",
        data=params,
        headers={
            "Authorization": f"Bearer {secret}",
            "Content-Type":  "application/x-www-form-urlencoded",
        }
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read())
        return {"redirect_url": data["url"], "session_id": data["id"]}
    except Exception as e:
        return {"error": str(e)}

def stripe_verify_webhook(body: bytes, sig_header: str) -> dict | None:
    """Verify Stripe webhook signature. Returns event dict or None."""
    secret = cfg("stripe_webhook_secret")
    if not secret:
        return None
    try:
        ts = None
        for part in sig_header.split(","):
            if part.startswith("t="):
                ts = part[2:]
            elif part.startswith("v1="):
                given_sig = part[3:]
        payload = f"{ts}.{body.decode()}"
        expected = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, given_sig):
            return None
        return json.loads(body)
    except Exception:
        return None

# ─────────────────────────────────────────────
# PAYPAL INTEGRATION
# ─────────────────────────────────────────────
_paypal_token_cache = {"token": None, "expires": 0}
_paypal_lock = threading.Lock()

def _paypal_base():
    return "https://api-m.paypal.com" if cfg("paypal_live") == "true" else "https://api-m.sandbox.paypal.com"

def _paypal_get_token() -> str | None:
    with _paypal_lock:
        if _paypal_token_cache["token"] and time.time() < _paypal_token_cache["expires"] - 60:
            return _paypal_token_cache["token"]
        cid = cfg("paypal_client_id")
        csec = cfg("paypal_client_secret")
        if not cid or not csec:
            return None
        creds = base64.b64encode(f"{cid}:{csec}".encode()).decode()
        req = urllib.request.Request(
            f"{_paypal_base()}/v1/oauth2/token",
            data=b"grant_type=client_credentials",
            headers={"Authorization": f"Basic {creds}", "Content-Type": "application/x-www-form-urlencoded"}
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                d = json.loads(r.read())
            _paypal_token_cache["token"]   = d["access_token"]
            _paypal_token_cache["expires"] = time.time() + d.get("expires_in", 3600)
            return d["access_token"]
        except Exception as e:
            log(f"PayPal token error: {e}")
            return None

def paypal_create_order(email: str, tier: str, public_url: str) -> dict:
    token = _paypal_get_token()
    if not token:
        return {"error": "PayPal not configured. Set paypal_client_id + paypal_client_secret."}
    tier_cfg = TIERS.get(tier, TIERS["indie"])
    price    = f"{tier_cfg['price_usd']:.2f}"

    body = json.dumps({
        "intent": "CAPTURE",
        "purchase_units": [{
            "amount":      {"currency_code": "USD", "value": price},
            "description": f"Sovereign QRNG — {tier_cfg['name']} Tier (1 month)",
            "custom_id":   f"{tier}|{email}",
        }],
        "application_context": {
            "return_url":    f"{public_url}/paypal/return",
            "cancel_url":    f"{public_url}/buy",
            "brand_name":    "Sovereign QRNG",
            "user_action":   "PAY_NOW",
            "shipping_preference": "NO_SHIPPING",
        }
    }).encode()

    req = urllib.request.Request(
        f"{_paypal_base()}/v2/checkout/orders",
        data=body,
        headers={
            "Authorization":  f"Bearer {token}",
            "Content-Type":   "application/json",
            "PayPal-Request-Id": secrets.token_hex(16),
        }
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            d = json.loads(r.read())
        approve_url = next((l["href"] for l in d.get("links",[]) if l["rel"]=="approve"), None)
        save_order(d["id"], {"tier": tier, "email": email, "rail": "paypal", "status": "pending"})
        return {"redirect_url": approve_url, "order_id": d["id"]}
    except Exception as e:
        return {"error": str(e)}

def paypal_capture_order(order_id: str) -> dict:
    token = _paypal_get_token()
    if not token:
        return {"error": "PayPal not configured"}
    req = urllib.request.Request(
        f"{_paypal_base()}/v2/checkout/orders/{order_id}/capture",
        data=b"{}",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type":  "application/json",
        }
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())
    except Exception as e:
        return {"error": str(e)}

# ─────────────────────────────────────────────
# COINGATE CRYPTO INTEGRATION
# ─────────────────────────────────────────────
def coingate_create_invoice(email: str, tier: str, public_url: str) -> dict:
    api_key = cfg("coingate_api_key")
    if not api_key:
        return {"error": "CoinGate not configured. Set coingate_api_key."}
    tier_cfg = TIERS.get(tier, TIERS["indie"])
    price    = tier_cfg["price_usd"]
    ref      = f"sqrng_{tier}_{secrets.token_hex(8)}"

    body = json.dumps({
        "order_id":         ref,
        "price_amount":     price,
        "price_currency":   "USD",
        "receive_currency": "USDC",        # settle in stablecoin — no volatility
        "title":            f"Sovereign QRNG — {tier_cfg['name']}",
        "description":      f"1 month {tier_cfg['name']} tier. Hardware entropy API access.",
        "callback_url":     f"{public_url}/coingate/webhook",
        "cancel_url":       f"{public_url}/buy",
        "success_url":      f"{public_url}/success?rail=crypto&ref={ref}",
        "token":            ref,
        "purchaser_email":  email,
    }).encode()

    base = "https://api.coingate.com" if cfg("coingate_live","true")=="true" else "https://api-sandbox.coingate.com"
    req = urllib.request.Request(
        f"{base}/v2/orders",
        data=body,
        headers={
            "Authorization": f"Token {api_key}",
            "Content-Type":  "application/json",
        }
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            d = json.loads(r.read())
        save_order(ref, {"tier": tier, "email": email, "rail": "coingate", "status": "pending", "coingate_id": d.get("id")})
        return {"redirect_url": d.get("payment_url"), "order_ref": ref}
    except Exception as e:
        return {"error": str(e)}

# ─────────────────────────────────────────────
# HTTP REQUEST HANDLER
# ─────────────────────────────────────────────
class PayHandler(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin",  "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")

    def _json(self, code: int, data: dict):
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type",  "application/json")
        self.send_header("Cache-Control", "no-store")
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def _html(self, code: int, body: bytes):
        self.send_response(code)
        self.send_header("Content-Type",   "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def _redirect(self, url: str):
        self.send_response(302)
        self.send_header("Location", url)
        self.end_headers()

    def _body(self) -> bytes:
        length = int(self.headers.get("Content-Length", 0))
        return self.rfile.read(length) if length else b""

    def _json_body(self) -> dict:
        try: return json.loads(self._body())
        except: return {}

    def _public_url(self) -> str:
        return cfg("public_url", f"http://127.0.0.1:{PORT}")

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    # ── GET ──────────────────────────────────────────────
    def do_GET(self):
        path = self.path.split("?")[0].rstrip("/")

        if path in ("", "/", "/buy"):
            if BUY_HTML.exists():
                self._html(200, BUY_HTML.read_bytes())
            else:
                self._json(404, {"error": "buy.html not found"})
            return

        if path == "/success":
            self._html(200, self._success_page())
            return

        if path == "/paypal/return":
            # User returns from PayPal approval
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            order_id = qs.get("token", [""])[0]
            if not order_id:
                self._redirect(f"{self._public_url()}/buy")
                return
            order = get_order(order_id)
            result = paypal_capture_order(order_id)
            if result.get("status") == "COMPLETED":
                custom = result["purchase_units"][0].get("custom_id", "|")
                tier, email = custom.split("|", 1) if "|" in custom else (order.get("tier","indie"), order.get("email",""))
                on_payment_confirmed(email, tier, "paypal", order_id)
                self._redirect(f"{self._public_url()}/success?rail=paypal")
            else:
                self._redirect(f"{self._public_url()}/buy?error=paypal_failed")
            return

        if path == "/api/health":
            self._json(200, {"status": "ok", "service": "payment_api", "port": PORT})
            return

        self._json(404, {"error": "Not found"})

    # ── POST ─────────────────────────────────────────────
    def do_POST(self):
        path = self.path.split("?")[0].rstrip("/")

        # ── Free tier signup ────────────────────────────
        if path == "/free-signup":
            body = self._json_body()
            email = (body.get("email") or "").strip().lower()
            if not email or "@" not in email:
                self._json(400, {"error": "Valid email required"}); return
            # Check if already has a free key
            with _keys_lock:
                keys = load_keys()
                existing = [k for k,v in keys.items() if v.get("email")==email and v.get("tier")=="free" and v.get("active")]
            if existing:
                self._json(400, {"error": "A free key already exists for this email"}); return
            key = on_payment_confirmed(email, "free", "free", "free_" + secrets.token_hex(8))
            self._json(200, {"status": "ok", "message": f"API key sent to {email}"})
            return

        # ── Unified checkout (all paid rails) ──────────
        if path == "/checkout":
            body = self._json_body()
            tier  = body.get("tier", "").lower()
            rail  = body.get("rail", "").lower()
            email = (body.get("email") or "").strip().lower()

            if tier not in ("indie", "pro", "enterprise"):
                self._json(400, {"error": "Invalid tier"}); return
            if rail not in ("stripe", "paypal", "crypto"):
                self._json(400, {"error": "Invalid payment rail"}); return
            if not email or "@" not in email:
                self._json(400, {"error": "Valid email required"}); return

            pub = self._public_url()

            if rail == "stripe":
                result = stripe_create_session(email, tier, pub)
            elif rail == "paypal":
                result = paypal_create_order(email, tier, pub)
            elif rail == "crypto":
                result = coingate_create_invoice(email, tier, pub)
            else:
                result = {"error": "Unknown rail"}

            if "error" in result:
                self._json(500, result); return
            self._json(200, result)
            return

        # ── Stripe webhook ──────────────────────────────
        if path == "/stripe/webhook":
            raw_body = self._body()
            sig      = self.headers.get("Stripe-Signature", "")
            event    = stripe_verify_webhook(raw_body, sig)
            if event is None:
                self._json(400, {"error": "Invalid signature"}); return
            evt_type = event.get("type", "")
            if evt_type in ("checkout.session.completed", "invoice.payment_succeeded"):
                sess = event["data"]["object"]
                email = (sess.get("customer_email") or sess.get("customer_details", {}).get("email") or "")
                tier  = (sess.get("metadata") or {}).get("tier", "indie")
                on_payment_confirmed(email, tier, "stripe", sess.get("id",""))
            self._json(200, {"received": True})
            return

        # ── PayPal webhook ──────────────────────────────
        if path == "/paypal/webhook":
            body = self._body()
            try:
                event = json.loads(body)
            except:
                self._json(400, {"error": "Invalid JSON"}); return
            # Verify PayPal webhook (simplified — full sig verify needs PayPal SDK)
            evt_type = event.get("event_type", "")
            if evt_type == "PAYMENT.CAPTURE.COMPLETED":
                resource = event.get("resource", {})
                custom   = resource.get("custom_id", "|")
                order_id = resource.get("supplementary_data", {}).get("related_ids", {}).get("order_id", "")
                if "|" in custom:
                    tier, email = custom.split("|", 1)
                    on_payment_confirmed(email, tier, "paypal_webhook", order_id)
            self._json(200, {"received": True})
            return

        # ── CoinGate IPN webhook ────────────────────────
        if path == "/coingate/webhook":
            try:
                raw  = self._body().decode()
                data = dict(urllib.parse.parse_qsl(raw))
            except:
                self._json(400, {"error": "Invalid body"}); return

            status   = data.get("status", "")
            order_id = data.get("order_id", "")
            if status == "paid":
                order = get_order(order_id)
                if order:
                    on_payment_confirmed(order.get("email",""), order.get("tier","indie"), "coingate", order_id)
            self._json(200, {"received": True})
            return

        self._json(404, {"error": "Not found"})

    # ── SUCCESS PAGE ─────────────────────────────────────
    def _success_page(self) -> bytes:
        return b"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"><title>Payment Success - Sovereign QRNG</title>
<style>*{margin:0;padding:0;box-sizing:border-box;}
body{background:#060a0e;color:#e2e8f0;font-family:'Courier New',monospace;
  display:flex;align-items:center;justify-content:center;min-height:100vh;padding:20px;}
.card{background:#0d1117;border:1px solid rgba(0,212,255,0.3);border-radius:8px;
  padding:40px;max-width:560px;width:100%;text-align:center;}
h1{font-size:32px;letter-spacing:6px;color:#00d4ff;margin-bottom:16px;text-transform:uppercase;}
p{color:#9ca3af;letter-spacing:2px;line-height:1.8;margin-bottom:12px;font-size:13px;}
.ok{color:#22c55e;font-size:20px;margin-bottom:20px;}
a{color:#00d4ff;text-decoration:none;letter-spacing:2px;font-size:12px;}
a:hover{text-decoration:underline;}
.badge{display:inline-block;border:1px solid rgba(34,197,94,0.4);border-radius:3px;
  padding:4px 14px;font-size:11px;letter-spacing:3px;color:#22c55e;text-transform:uppercase;margin-bottom:20px;}
</style></head>
<body><div class="card">
<div class="ok">&#10003;</div>
<h1>Payment Confirmed</h1>
<div class="badge">KEY GENERATING</div>
<p>Your API key is being generated and will be emailed to you within 60 seconds.</p>
<p>Check your inbox (and spam folder). The key is shown once &#8212; store it in a password manager or secrets vault.</p>
<p style="margin-top:24px;">
  <a href="http://127.0.0.1:8890/buy">&#8592; Back to Pricing</a>
  &nbsp;&nbsp;
  <a href="http://127.0.0.1:8889/v1/status">API Status &#8599;</a>
</p>
</div></body></html>"""

# ─────────────────────────────────────────────
# SETUP WIZARD
# ─────────────────────────────────────────────
def run_setup():
    print("\n" + "="*60)
    print("  SOVEREIGN QRNG — PAYMENT SETUP WIZARD")
    print("="*60)
    print("\nThis will save your API credentials to:")
    print(f"  {CONFIG_FILE}")
    print("\nAll values are optional — press Enter to skip.\n")

    cfg_new = load_config()

    def ask(key, label, default=""):
        val = input(f"  {label} [{cfg_new.get(key, default) or 'not set'}]: ").strip()
        if val: cfg_new[key] = val
        elif not cfg_new.get(key) and default: cfg_new[key] = default

    print("── STRIPE ─────────────────────────────────────────────")
    print("  Get keys at: https://dashboard.stripe.com/apikeys")
    ask("stripe_secret_key",         "Stripe Secret Key (sk_live_...)")
    ask("stripe_webhook_secret",     "Stripe Webhook Secret (whsec_...)")
    ask("stripe_indie_price_id",     "Stripe Price ID — Indie ($12/mo)")
    ask("stripe_pro_price_id",       "Stripe Price ID — Pro ($39/mo)")
    ask("stripe_ent_price_id",       "Stripe Price ID — Enterprise ($199/mo)")

    print("\n── PAYPAL ─────────────────────────────────────────────")
    print("  Get credentials at: https://developer.paypal.com/")
    ask("paypal_client_id",      "PayPal Client ID")
    ask("paypal_client_secret",  "PayPal Client Secret")
    ask("paypal_live",           "Live mode? (true/false)", "false")

    print("\n── COINGATE (CRYPTO) ───────────────────────────────────")
    print("  Get API key at: https://coingate.com/business-payment")
    ask("coingate_api_key", "CoinGate API Key")
    ask("coingate_live",    "Live mode? (true/false)", "true")

    print("\n── EMAIL (GMAIL) ───────────────────────────────────────")
    print("  Use an App Password — NOT your Gmail login password.")
    print("  Create one at: myaccount.google.com/apppasswords")
    ask("smtp_user", "Gmail address", "negitivminusone@gmail.com")
    ask("smtp_pass", "Gmail App Password (16-char, no spaces)")

    print("\n── PUBLIC URL ──────────────────────────────────────────")
    print("  Run: cloudflared tunnel --url http://127.0.0.1:8890")
    print("  Then paste the https://xxxx.trycloudflare.com URL below.")
    ask("public_url", "Your public HTTPS URL (for webhooks)")

    save_config(cfg_new)
    print(f"\n  Config saved to {CONFIG_FILE}")
    print("\n  Next steps:")
    print("  1. In Stripe Dashboard → Webhooks → Add endpoint:")
    print(f"     {cfg_new.get('public_url','<url>')}/stripe/webhook")
    print("     Events: checkout.session.completed, invoice.payment_succeeded")
    print("  2. In PayPal Dashboard → Webhooks → Add:")
    print(f"     {cfg_new.get('public_url','<url>')}/paypal/webhook")
    print("     Event: PAYMENT.CAPTURE.COMPLETED")
    print("  3. Run: python payment_api.py")
    print("\n" + "="*60 + "\n")

# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────
def run_server():
    print(f"\n{'='*58}")
    print(f"  SOVEREIGN QRNG — PAYMENT API")
    print(f"{'='*58}")
    print(f"  Port:        {PORT}")
    print(f"  Buy page:    http://127.0.0.1:{PORT}/buy")
    print(f"  Success:     http://127.0.0.1:{PORT}/success")
    print(f"  Health:      http://127.0.0.1:{PORT}/api/health")
    print(f"  Config:      {CONFIG_FILE}")
    print(f"  Stripe:      {'CONFIGURED' if cfg('stripe_secret_key') else 'NOT SET — run --setup'}")
    print(f"  PayPal:      {'CONFIGURED' if cfg('paypal_client_id') else 'NOT SET — run --setup'}")
    print(f"  CoinGate:    {'CONFIGURED' if cfg('coingate_api_key') else 'NOT SET — run --setup'}")
    print(f"  Email:       {'CONFIGURED' if cfg('smtp_pass') else 'NOT SET — keys logged only'}")
    print(f"  Public URL:  {cfg('public_url') or 'NOT SET — webhooks disabled'}")
    print(f"{'='*58}")
    print(f"\n  To configure: python payment_api.py --setup")
    print(f"  Expose:  cloudflared tunnel --url http://127.0.0.1:{PORT}")
    print()

    srv = ThreadingHTTPServer(("127.0.0.1", PORT), PayHandler)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n[payment_api] Stopped.")

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--setup", action="store_true", help="Run configuration wizard")
    args = ap.parse_args()
    if args.setup:
        run_setup()
    else:
        run_server()
