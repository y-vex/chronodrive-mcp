"""
Chronodrive API client — authentication, product search, cart management.
All configuration via environment variables (see .env.example).
"""
import re, os, json, math, secrets, hashlib, base64
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

EMAIL    = os.environ["CHRONODRIVE_EMAIL"]
PASSWORD = os.environ["CHRONODRIVE_PASSWORD"]
SITE_ID  = os.environ["CHRONODRIVE_STORE_ID"]

SESSION_FILE = Path(os.getenv("CHRONODRIVE_SESSION_FILE",
                              Path.home() / ".chronodrive-mcp" / "session.json"))
SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
_CONFIG_CACHE_FILE = SESSION_FILE.parent / "frontend_config.json"

# ── Chronodrive OAuth2 constants ───────────────────────────────────────────────
CONNECT_BASE  = "https://connect.chronodrive.com"
CONNECT_SCOPE = "openid profile email phone full_write offline_access"

def _fetch_frontend_config() -> dict:
    """Extract public constants from Chronodrive homepage Nuxt config."""
    r = requests.get("https://www.chronodrive.com", headers={
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36"
    }, timeout=10)
    r.raise_for_status()
    pairs = re.findall(r'([A-Z_]{3,30}APIKEY)[\":\s]+([a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12})', r.text)
    keys = {k: v for k, v in pairs}
    client_id = re.search(r'CLIENT_ID[\"\':\s]+([A-Za-z0-9_-]{10,})', r.text).group(1)
    return {
        "customer":  keys["CHR_API_CUSTOMERS_APIKEY"],
        "cart":      keys["CHR_API_CARTS_APIKEY"],
        "search":    keys["CHR_API_PRODUCTS_APIKEY"],
        "client_id": client_id,
    }

def _load_frontend_config() -> dict:
    if _CONFIG_CACHE_FILE.exists():
        try:
            return json.loads(_CONFIG_CACHE_FILE.read_text())
        except Exception:
            pass
    config = _fetch_frontend_config()
    _CONFIG_CACHE_FILE.touch(mode=0o600)
    _CONFIG_CACHE_FILE.write_text(json.dumps(config, indent=2))
    return config

_FRONTEND = _load_frontend_config()
API_KEY_CUSTOMER = _FRONTEND["customer"]
API_KEY_CART_ADD = _FRONTEND["cart"]
API_KEY_SEARCH   = _FRONTEND["search"]
CONNECT_CLIENT   = _FRONTEND["client_id"]

BASE_HEADERS = {
    "User-Agent":              "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36",
    "x-chronodrive-site-id":   SITE_ID,
    "x-chronodrive-site-mode": "DRIVE",
    "x-device-type":           "WEB",
    "referer":                 "https://www.chronodrive.com/",
    "content-type":            "application/json",
    "Accept":                  "application/json",
}

_NOISE = {"de", "du", "des", "le", "la", "les", "au", "aux", "et", "en", "un", "une"}


# ── Authentication ─────────────────────────────────────────────────────────────

def _pkce():
    verifier  = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()
    ).rstrip(b"=").decode()
    return verifier, challenge

def authenticate() -> dict:
    """Run PKCE OAuth2 flow, save and return session dict."""
    s = requests.Session()

    login_r = s.post(f"{CONNECT_BASE}/identity/v1/password/login", json={
        "client_id": CONNECT_CLIENT, "scope": CONNECT_SCOPE,
        "email": EMAIL, "password": PASSWORD,
    }, timeout=15)
    login_r.raise_for_status()
    tkn = login_r.json()["tkn"]

    verifier, challenge = _pkce()
    auth_r = s.get(f"{CONNECT_BASE}/oauth/authorize",
        headers={"Origin": "https://www.chronodrive.com", "Referer": "https://www.chronodrive.com/"},
        params={
            "client_id": CONNECT_CLIENT, "response_type": "code",
            "nonce": str(secrets.randbelow(10**10)), "persistent": "true",
            "redirect_uri": "https://www.chronodrive.com",
            "scope": CONNECT_SCOPE, "response_mode": "web_message",
            "prompt": "none", "code_challenge": challenge,
            "code_challenge_method": "S256", "tkn": tkn,
        }, timeout=15)
    auth_r.raise_for_status()
    code = re.search(r'"code"\s*:\s*"([^"]+)"', auth_r.text).group(1)

    token_r = s.post(f"{CONNECT_BASE}/oauth/token", json={
        "client_id": CONNECT_CLIENT, "grant_type": "authorization_code",
        "code_verifier": verifier, "code": code,
        "redirect_uri": "https://www.chronodrive.com",
    }, timeout=15)
    token_r.raise_for_status()
    bearer = token_r.json()["access_token"]

    hdrs = {**BASE_HEADERS, "Authorization": f"Bearer {bearer}", "x-api-key": API_KEY_CUSTOMER}
    customer_id = str(requests.get("https://api.chronodrive.com/v1/customers/me",
                                   headers=hdrs, timeout=15).json().get("id", ""))
    cart_id = requests.get("https://api.chronodrive.com/v1/customers/me/carts",
                           headers=hdrs, timeout=15).json()["content"][0]["id"]

    session = {"bearer": bearer, "cart_id": cart_id, "customer_id": customer_id}
    SESSION_FILE.touch(mode=0o600)
    SESSION_FILE.write_text(json.dumps(session, indent=2))
    return session

def _load_session() -> dict:
    if SESSION_FILE.exists():
        return json.loads(SESSION_FILE.read_text())
    return authenticate()

def ensure_session() -> dict:
    """Return valid session, re-authenticating transparently on 401."""
    session = _load_session()
    hdrs = {**BASE_HEADERS, "Authorization": f"Bearer {session['bearer']}",
            "x-api-key": API_KEY_CUSTOMER}
    r = requests.get("https://api.chronodrive.com/v1/customers/me", headers=hdrs, timeout=10)
    if r.status_code == 401:
        session = authenticate()
    return session


# ── Scoring ────────────────────────────────────────────────────────────────────

def score_product(name: str, search_terms: str) -> float:
    sw = set(w for w in re.split(r"\W+", search_terms.lower()) if w)
    nw = set(w for w in re.split(r"\W+", name.lower()) if w)
    matched = sw & nw
    extra = nw - sw - _NOISE
    return len(matched) - 0.35 * len(extra)

def _pkg_grams(p: dict) -> float | None:
    uql = (p.get("labels") or {}).get("unitQuantityLabel", "").lower()
    for pat, mult in [
        (r"(\d+[\.,]?\d*)\s*kg", 1000), (r"(\d+[\.,]?\d*)\s*l\b", 1000),
        (r"(\d+[\.,]?\d*)\s*g\b", 1),   (r"(\d+[\.,]?\d*)\s*ml\b", 1),
        (r"(\d+[\.,]?\d*)\s*cl\b", 10),
    ]:
        m = re.search(pat, uql)
        if m:
            return float(m.group(1).replace(",", ".")) * mult
    w = (p.get("packaging") or {}).get("weight", 0)
    return w * 1000 if w else None

def _pkg_pcs(p: dict) -> float:
    labels = p.get("labels") or {}
    for text in [labels.get("portionLabel", ""), labels.get("unitQuantityLabel", "")]:
        t = text.lower()
        for pat in [r"(\d+)\s*pièces?", r"x\s*(\d+)", r"lot\s*de\s*(\d+)", r"(\d+)\s*unités?"]:
            m = re.search(pat, t)
            if m:
                return float(m.group(1))
    return 1.0


# ── Product search ─────────────────────────────────────────────────────────────

def search_products(query: str, session: dict) -> list[dict]:
    hdrs = {
        **BASE_HEADERS,
        "Authorization": f"Bearer {session['bearer']}",
        "X-Api-Key": API_KEY_SEARCH,
        "X-Kamino-User-Consent": "true",
        "X-Kamino-User-Id": session.get("customer_id", ""),
    }
    r = requests.get("https://api.chronodrive.com/v1/products", params={
        "searchTerm": query, "page": 1, "size": 50,
        "withFeaturedSell": "true", "withPushLists": "true",
        "includeNavigationInFacets": "false",
        "withKamino": "true", "kaminoMode": "ADVANCED",
    }, headers=hdrs, timeout=15)
    if not r.ok:
        return []
    return r.json().get("content", [])

def pick_candidates(products: list, search_terms: str, unit: str, quantity: int,
                    top_n: int = 3) -> list[dict]:
    scored = []
    for p in products:
        labels = p.get("labels") or {}
        name   = labels.get("productLabel", "").strip()
        if not name:
            continue
        if p.get("maxCartQuantity") == 0 or p.get("stock") == "NO_STOCK":
            continue
        s = score_product(name, search_terms)

        size      = labels.get("unitQuantityLabel", "")
        prices    = p.get("prices") or {}
        price_raw = prices.get("defaultPrice", 0)
        flags     = p.get("flags") or {}
        chars     = p.get("characteristics") or {}
        pkg_info  = p.get("packaging") or {}
        anim      = p.get("animation") or {}

        if unit in ("g", "ml"):
            pkg = _pkg_grams(p)
            units_to_add = math.ceil(quantity / pkg) if pkg else 1
        else:
            units_to_add = math.ceil(quantity / _pkg_pcs(p))

        promo = None
        if anim:
            promo = anim.get("label") or anim.get("type")

        price_per_kg = prices.get("pricePerUnitMeasure")
        lowest_30d   = prices.get("lastPeriodLowestPrice")

        dims = {k: pkg_info[k] for k in ("weight", "height", "length", "width") if pkg_info.get(k)}

        scored.append((s, {
            "name":        name,
            "productId":   str(p.get("id") or ""),
            "brand":       labels.get("brandLabel", "").strip(),
            "size":        size,
            "price":       f"{float(price_raw):.2f}€" if price_raw else None,
            "price_per_kg": f"{float(price_per_kg):.2f}€/kg" if price_per_kg else None,
            "lowest_30d":  f"{float(lowest_30d):.2f}€" if lowest_30d else None,
            "promo":       promo,
            "stock":       p.get("stock"),
            "flags": {
                "fresh":   flags.get("isFresh", False),
                "organic": flags.get("isOrganic", False),
                "french":  flags.get("isFrench", False),
                "local":   flags.get("isLocal", False),
                "new":     flags.get("isNew", False),
            },
            "origin":      chars.get("origin", ""),
            "dims":        dims or None,
            "complementary":   p.get("complementaryProducts") or [],
            "substitutions":   p.get("substitutionProducts") or [],
            "units_to_add": units_to_add,
            "score":        round(s, 2),
        }))

    scored.sort(key=lambda x: x[0], reverse=True)
    seen, results = set(), []
    for _, c in scored:
        if c["name"] not in seen:
            seen.add(c["name"])
            results.append(c)
        if len(results) == top_n:
            break
    return results


# ── Cart operations ────────────────────────────────────────────────────────────

def add_item_to_cart(product_id: str, quantity: int, session: dict) -> bool:
    cart_id = session["cart_id"]
    hdrs = {**BASE_HEADERS, "Authorization": f"Bearer {session['bearer']}",
            "x-api-key": API_KEY_CART_ADD}
    payload = {"content": [{"clientOrigin": "WEB|SEARCH|TG|/promotions",
                             "productId": product_id,
                             "quantity": quantity}],
               "optimizedMode": True}
    r = requests.post(f"https://api.chronodrive.com/v1/carts/{cart_id}/items",
                      json=payload, headers=hdrs, timeout=15)
    if not r.ok:
        return False
    result = r.json().get("content", [{}])[0]
    return result.get("returnType") == "SUCCESS"

def remove_item_from_cart(product_id: str, session: dict) -> bool:
    cart_id = session["cart_id"]
    hdrs = {**BASE_HEADERS, "Authorization": f"Bearer {session['bearer']}",
            "x-api-key": API_KEY_CART_ADD}
    r = requests.delete(f"https://api.chronodrive.com/v1/carts/{cart_id}/items/{product_id}",
                        headers=hdrs, timeout=15)
    return r.ok

def get_cart(session: dict) -> list[dict]:
    cart_id = session["cart_id"]
    hdrs = {**BASE_HEADERS, "Authorization": f"Bearer {session['bearer']}",
            "x-api-key": API_KEY_CART_ADD}
    r = requests.get(f"https://api.chronodrive.com/v1/carts/{cart_id}",
                     headers=hdrs, timeout=15)
    r.raise_for_status()
    items = r.json().get("items", [])
    result = []
    for item in items:
        product = item.get("product") or {}
        labels  = product.get("labels") or {}
        prices  = product.get("prices") or {}
        result.append({
            "productId": str(product.get("id", "")),
            "name":      labels.get("productLabel", "").strip(),
            "brand":     labels.get("brandLabel", "").strip(),
            "size":      labels.get("unitQuantityLabel", ""),
            "price":     f"{float(prices.get('defaultPrice', 0)):.2f}€" if prices.get("defaultPrice") else None,
            "quantity":  item.get("quantity", 1),
        })
    return result

def reset_cart(session: dict) -> tuple[int, int]:
    cart_id = session["cart_id"]
    hdrs = {**BASE_HEADERS, "Authorization": f"Bearer {session['bearer']}",
            "x-api-key": API_KEY_CART_ADD}
    r = requests.get(f"https://api.chronodrive.com/v1/carts/{cart_id}",
                     headers=hdrs, timeout=15)
    r.raise_for_status()
    items = r.json().get("items", [])
    removed, failed = 0, 0
    for item in items:
        pid = str((item.get("product") or {}).get("id", ""))
        if not pid:
            continue
        d = requests.delete(f"https://api.chronodrive.com/v1/carts/{cart_id}/items/{pid}",
                            headers=hdrs, timeout=15)
        if d.ok:
            removed += 1
        else:
            failed += 1
    return removed, failed
