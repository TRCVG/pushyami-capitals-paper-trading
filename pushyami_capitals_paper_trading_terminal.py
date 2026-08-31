import streamlit as st
import pandas as pd
import yfinance as yf

try:
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots
    PLOTLY_AVAILABLE = True
except Exception:
    go = None
    make_subplots = None
    PLOTLY_AVAILABLE = False
from datetime import date, timedelta, datetime
import calendar
import os, json, glob, hashlib, secrets, hmac
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

try:
    from jugaad_data.nse import derivatives_df
except Exception:
    derivatives_df = None

# ============================================================
# PAGE CONFIG
# ============================================================
st.set_page_config(
    page_title="PUSHYAMI CAPITALS PAPER TRADING TERMINAL",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ============================================================
# GOOGLE SHEETS CONFIG
# ============================================================
SPREADSHEET_URL = "https://docs.google.com/spreadsheets/d/1qDl4eW5vKWr99kbXUHRaXh64FZGCAw8Q7I-hP2IOJLM/edit"
DEFAULT_CAPITAL = 100000.0
ADMIN_SETUP_KEY = os.getenv("ADMIN_SETUP_KEY", "NSEPRO-ADMIN-SETUP-CHANGE-ME")

SHEET_HEADERS = {
    "Users": ["UserID", "Name", "PasswordHash", "Role", "Status", "CreatedAt", "LastLogin"],
    "Accounts": ["UserID", "InitialCapital", "CashBalance", "PortfolioValue", "TotalPnL", "UpdatedAt"],
    "Positions": ["UserID", "Contract", "Type", "Side", "Quantity", "AvgPrice", "LastPrice", "EntryDate", "UpdatedAt"],
    "Trades": ["TradeID", "UserID", "Time", "Action", "Contract", "Quantity", "Price", "Value", "Type"],
}

@st.cache_resource(show_spinner=False)
def get_google_client():
    """Connect to Google Sheets using Streamlit Secrets online,
    with local service_account.json fallback."""
    try:
        import gspread
        from google.oauth2.service_account import Credentials

        scopes = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ]

        # ONLINE: Streamlit Cloud
        if "gcp_service_account" in st.secrets:
            credentials = Credentials.from_service_account_info(
                dict(st.secrets["gcp_service_account"]),
                scopes=scopes,
            )

            client = gspread.authorize(credentials)
            return client, None

        # LOCAL: service_account.json
        app_dir = Path(__file__).resolve().parent

        candidates = [
            app_dir / "service_account.json",
            app_dir / "google_service_account.json",
            app_dir / "credentials.json",
        ]

        candidates += [
            Path(p)
            for p in glob.glob(str(app_dir / "*.json"))
        ]

        json_file = next(
            (p for p in candidates if p.exists()),
            None
        )

        if json_file is None:
            return None, "Google credentials not found."

        credentials = Credentials.from_service_account_file(
            str(json_file),
            scopes=scopes,
        )

        client = gspread.authorize(credentials)

        return client, None

    except Exception as e:
        return None, str(e)

@st.cache_resource(show_spinner=False)
def get_spreadsheet():
    client, error = get_google_client()
    if not client:
        return None, error
    try:
        return client.open_by_url(SPREADSHEET_URL), None
    except Exception as e:
        return None, str(e)


def ensure_sheets(spreadsheet):
    """Create missing tabs/headers automatically."""
    existing = {ws.title: ws for ws in spreadsheet.worksheets()}
    for name, headers in SHEET_HEADERS.items():
        if name not in existing:
            ws = spreadsheet.add_worksheet(title=name, rows=1000, cols=max(10, len(headers)))
            ws.append_row(headers)
            ws.freeze(rows=1)
        else:
            ws = existing[name]
            values = ws.get_all_values()
            if not values:
                ws.append_row(headers)
                ws.freeze(rows=1)
    return {ws.title: ws for ws in spreadsheet.worksheets()}


def hash_password(password, salt=None):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 310000)
    return f"{salt}${digest.hex()}"


def verify_password(password, stored):
    try:
        salt, expected = stored.split("$", 1)
        digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 310000).hex()
        return hmac.compare_digest(digest, expected)
    except Exception:
        return False


def records(ws):
    try:
        return ws.get_all_records()
    except Exception:
        return []


def find_user(ws, user_id):
    user_id = str(user_id).strip().lower()
    for row in records(ws):
        if str(row.get("UserID", "")).strip().lower() == user_id:
            return row
    return None


def append_user(ws, user_id, name, password, role="USER"):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ws.append_row([
        user_id,
        name,
        hash_password(password),
        role,
        "ACTIVE",
        now,
        "",
    ])


def update_user_last_login(ws, user_id):
    values = ws.get_all_values()
    if not values:
        return
    headers = values[0]
    try:
        uid_col = headers.index("UserID") + 1
        login_col = headers.index("LastLogin") + 1
    except ValueError:
        return
    for r, row in enumerate(values[1:], start=2):
        if row and str(row[uid_col - 1]).strip().lower() == user_id.lower():
            ws.update_cell(r, login_col, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            return


def account_row(ws, user_id):
    for row in records(ws):
        if str(row.get("UserID", "")).strip().lower() == user_id.lower():
            return row
    return None


def save_account(ws, user_id, initial, cash, portfolio, pnl):
    vals = ws.get_all_values()
    headers = vals[0] if vals else SHEET_HEADERS["Accounts"]
    uid_idx = headers.index("UserID") if "UserID" in headers else 0
    target = None
    for r, row in enumerate(vals[1:], start=2):
        if len(row) > uid_idx and row[uid_idx].strip().lower() == user_id.lower():
            target = r
            break
    data = [user_id, float(initial), float(cash), float(portfolio), float(pnl), datetime.now().strftime("%Y-%m-%d %H:%M:%S")]
    if target:
        ws.update(f"A{target}:F{target}", [data])
    else:
        ws.append_row(data)


def load_account(ws, user_id):
    row = account_row(ws, user_id)
    if not row:
        save_account(ws, user_id, DEFAULT_CAPITAL, DEFAULT_CAPITAL, DEFAULT_CAPITAL, 0)
        return DEFAULT_CAPITAL, DEFAULT_CAPITAL
    try:
        return float(row.get("InitialCapital", DEFAULT_CAPITAL)), float(row.get("CashBalance", DEFAULT_CAPITAL))
    except Exception:
        return DEFAULT_CAPITAL, DEFAULT_CAPITAL


def load_positions(ws, user_id):
    result = {}
    for row in records(ws):
        if str(row.get("UserID", "")).strip().lower() != user_id.lower():
            continue
        contract = str(row.get("Contract", "")).strip()
        if not contract:
            continue
        try:
            result[contract] = {
                "qty": float(row.get("Quantity", 0)),
                "avg_price": float(row.get("AvgPrice", 0)),
                "last_price": float(row.get("LastPrice", row.get("AvgPrice", 0)) or 0),
                "type": str(row.get("Type", "EQ")),
                "side": str(row.get("Side", "LONG")),
                "entry_date": str(row.get("EntryDate", "N/A")),
            }
        except Exception:
            continue
    return result


def save_positions(ws, user_id, positions, price_map=None):
    price_map = price_map or {}
    values = ws.get_all_values()
    if values:
        headers = values[0]
        user_col = headers.index("UserID") if "UserID" in headers else 0
        # Delete current user's rows from bottom to top.
        for r in range(len(values), 1, -1):
            row = values[r - 1]
            if len(row) > user_col and row[user_col].strip().lower() == user_id.lower():
                ws.delete_rows(r)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    rows = []
    for contract, p in positions.items():
        last = get_position_ltp(contract, p, price_map)
        p["last_price"] = last
        rows.append([
            user_id, contract, p.get("type", "EQ"), p.get("side", "LONG"),
            p.get("qty", 0), p.get("avg_price", 0), last, p.get("entry_date", "N/A"), now
        ])
    if rows:
        ws.append_rows(rows)


def append_trade(ws, user_id, action, contract, qty, price, asset_type):
    trade_id = f"T{datetime.now().strftime('%Y%m%d%H%M%S')}{secrets.token_hex(2).upper()}"
    ws.append_row([
        trade_id, user_id, datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        action, contract, qty, price, qty * price, asset_type
    ])


def create_account_if_missing(ws, user_id):
    if not account_row(ws, user_id):
        save_account(ws, user_id, DEFAULT_CAPITAL, DEFAULT_CAPITAL, DEFAULT_CAPITAL, 0)

# ============================================================
# AUTHENTICATION GATE
# ============================================================
spreadsheet, sheet_error = get_spreadsheet()
if spreadsheet:
    worksheets = ensure_sheets(spreadsheet)
else:
    worksheets = {}

if "authenticated" not in st.session_state:
    st.session_state.authenticated = False
if "user_id" not in st.session_state:
    st.session_state.user_id = ""
if "user_name" not in st.session_state:
    st.session_state.user_name = ""
if "user_role" not in st.session_state:
    st.session_state.user_role = ""


def login_screen():
    st.markdown("""
    <div class="login-shell">
      <div class="login-brand"><span class="login-bar"></span>PUSHYAMI CAPITALS PAPER TRADING TERMINAL</div>
      <div class="login-subtitle">Secure Paper Trading</div>
      <div class="login-description">Professional simulated trading · User-specific portfolio · Google Sheets persistence</div>
    </div>
    """, unsafe_allow_html=True)

    if not spreadsheet:
        st.error("Google Sheets connection is unavailable.")
        st.code(sheet_error or "Unknown connection error")
        st.info("Keep the service-account JSON in the same folder as this Python file and make sure it can access your spreadsheet.")
        return

    tab_login, tab_admin = st.tabs(["🔐 Sign In", "👑 First Admin"])

    with tab_login:
        with st.container(border=True):
            uid = st.text_input("Login ID", key="login_uid")
            pwd = st.text_input("Password", type="password", key="login_pwd")
            if st.button("SIGN IN", type="primary", use_container_width=True):
                if not uid or not pwd:
                    st.warning("Enter both Login ID and Password.")
                else:
                    user = find_user(worksheets["Users"], uid)
                    if not user:
                        st.error("Invalid Login ID or Password.")
                    elif str(user.get("Status", "ACTIVE")).upper() != "ACTIVE":
                        st.error("This account is inactive.")
                    elif not verify_password(pwd, str(user.get("PasswordHash", ""))):
                        st.error("Invalid Login ID or Password.")
                    else:
                        st.session_state.authenticated = True
                        st.session_state.user_id = str(user["UserID"])
                        st.session_state.user_name = str(user.get("Name", user["UserID"]))
                        st.session_state.user_role = str(user.get("Role", "USER")).upper()
                        update_user_last_login(worksheets["Users"], st.session_state.user_id)
                        create_account_if_missing(worksheets["Accounts"], st.session_state.user_id)
                        st.rerun()

    with tab_admin:
        with st.container(border=True):
            st.caption("Create the first administrator account. This setup key is not your Google password.")
            setup = st.text_input("Admin Setup Key", type="password")
            uid = st.text_input("Admin Login ID", key="admin_uid")
            name = st.text_input("Admin Name", key="admin_name")
            pwd = st.text_input("Admin Password", type="password", key="admin_pwd")
            confirm = st.text_input("Confirm Password", type="password", key="admin_confirm")
            if st.button("CREATE ADMIN", type="primary", use_container_width=True):
                existing = records(worksheets["Users"])
                if existing:
                    st.warning("A user already exists. Use Sign In, or use your existing administrator account.")
                elif setup != ADMIN_SETUP_KEY:
                    st.error("Incorrect Admin Setup Key.")
                elif not uid or not name or not pwd:
                    st.warning("Complete all fields.")
                elif len(pwd) < 6:
                    st.warning("Password must contain at least 6 characters.")
                elif pwd != confirm:
                    st.error("Passwords do not match.")
                else:
                    append_user(worksheets["Users"], uid.strip(), name.strip(), pwd, "ADMIN")
                    create_account_if_missing(worksheets["Accounts"], uid.strip())
                    st.success("Admin created successfully. Go to Sign In.")


if not st.session_state.authenticated:
    login_screen()
    st.stop()

# ============================================================
# USER-SPECIFIC SESSION STATE
# ============================================================
user_id = st.session_state.user_id
initial_capital, saved_cash = load_account(worksheets["Accounts"], user_id)

if st.session_state.get("loaded_user_id") != user_id:
    st.session_state.initial_capital = initial_capital
    st.session_state.cash_balance = saved_cash
    st.session_state.positions = load_positions(worksheets["Positions"], user_id)
    st.session_state.trade_history = []
    st.session_state.loaded_user_id = user_id

# ============================================================
# THEME / CSS — this is intentionally contained in <style>
# so CSS never appears as text in the app.
# ============================================================
if "theme" not in st.session_state:
    st.session_state.theme = "Dark"

if st.session_state.theme == "Light":
    C = dict(
        bg="#F3F5F8",
        surface="#FFFFFF",
        alt="#F8FAFC",
        border="#D7DDE7",
        text="#17202A",
        muted="#667085",
        gold="#A97900",
        green="#16803C",
        red="#D92D20",
    )
else:
    C = dict(
        bg="#0B0E14",
        surface="#12161F",
        alt="#171C28",
        border="#262C3D",
        text="#E7E9EE",
        muted="#8891A6",
        gold="#C9A227",
        green="#3FB950",
        red="#F85149",
    )

st.markdown(f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600;700&display=swap');

:root {{
    --bg: {C['bg']};
    --surface: {C['surface']};
    --surface-alt: {C['alt']};
    --border: {C['border']};
    --text: {C['text']};
    --muted: {C['muted']};
    --gold: {C['gold']};
    --green: {C['green']};
    --red: {C['red']};
    --radius: 10px;
    --radius-sm: 7px;
}}

html, body, [class*="css"] {{
    font-family: 'Inter', sans-serif;
}}

.stApp {{
    background: var(--bg);
    color: var(--text);
}}

.main .block-container {{
    max-width: 1500px;
    padding-top: 1.5rem;
    padding-bottom: 2rem;
}}

.mono, .mono * {{
    font-family: 'JetBrains Mono', monospace !important;
    font-variant-numeric: tabular-nums;
}}

/* Header */
.app-header {{
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 18px 22px;
    background: linear-gradient(180deg, var(--surface) 0%, var(--surface-alt) 100%);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    margin-bottom: 18px;
    box-shadow: 0 8px 24px rgba(0,0,0,.05);
}}

.brand-title {{
    font-size: 21px;
    font-weight: 800;
    display: flex;
    align-items: center;
    gap: 10px;
    color: var(--text);
    letter-spacing: -.02em;
}}

.brand-bar {{
    width: 4px;
    height: 22px;
    background: var(--gold);
    border-radius: 3px;
    display: inline-block;
}}

.brand-sub {{
    font-size: 12px;
    color: var(--muted);
    margin-left: 14px;
    margin-top: 3px;
}}

.header-tag {{
    font-size: 11px;
    font-weight: 700;
    letter-spacing: .07em;
    text-transform: uppercase;
    color: var(--gold);
    background: rgba(169,121,0,.10);
    border: 1px solid rgba(169,121,0,.30);
    padding: 6px 10px;
    border-radius: 20px;
}}

/* Sidebar */
section[data-testid="stSidebar"] {{
    background: var(--surface) !important;
    border-right: 1px solid var(--border);
}}

section[data-testid="stSidebar"] label {{
    color: var(--muted) !important;
    font-size: 12.5px !important;
}}

section[data-testid="stSidebar"] .stMarkdown h2,
section[data-testid="stSidebar"] .stMarkdown h3 {{
    color: var(--text) !important;
    font-weight: 700;
    letter-spacing: .03em;
}}

/* Inputs */
.stTextInput input,
.stNumberInput input,
.stDateInput input,
.stSelectbox div[data-baseweb="select"] > div {{
    background: var(--surface-alt) !important;
    border: 1px solid var(--border) !important;
    color: var(--text) !important;
    border-radius: var(--radius-sm) !important;
}}

.stTextInput input:focus,
.stNumberInput input:focus,
.stDateInput input:focus {{
    border-color: var(--gold) !important;
    box-shadow: 0 0 0 1px var(--gold) !important;
}}

/* Buttons */
.stButton > button,
.stDownloadButton > button {{
    background: var(--surface-alt);
    color: var(--text);
    border: 1px solid var(--border);
    border-radius: var(--radius-sm);
    font-weight: 600;
    min-height: 38px;
    transition: all .15s ease;
}}

.stButton > button:hover,
.stDownloadButton > button:hover {{
    border-color: var(--gold);
    color: var(--gold);
}}

.stButton > button:active {{
    transform: translateY(1px);
}}

button[kind="primary"] {{
    background: var(--gold) !important;
    color: #14171F !important;
    border-color: var(--gold) !important;
    font-weight: 800 !important;
}}

button[kind="primary"]:hover {{
    filter: brightness(1.07);
}}

/* Cards */
div[data-testid="stVerticalBlockBorderWrapper"] {{
    background: var(--surface) !important;
    border: 1px solid var(--border) !important;
    border-radius: var(--radius) !important;
}}

/* Metrics */
div[data-testid="stMetric"] {{
    background: var(--surface) !important;
    border: 1px solid var(--border);
    border-left: 3px solid var(--gold);
    border-radius: var(--radius);
    padding: 14px 16px;
}}

div[data-testid="stMetricLabel"] {{
    color: var(--muted) !important;
    font-size: 12px !important;
    font-weight: 600 !important;
}}

div[data-testid="stMetricValue"] {{
    color: var(--text) !important;
    font-family: 'JetBrains Mono', monospace !important;
    font-weight: 700 !important;
}}

div[data-testid="stMetricDelta"] {{
    font-family: 'JetBrains Mono', monospace !important;
}}

/* Tabs */
button[data-baseweb="tab"] {{
    color: var(--muted) !important;
    font-weight: 600 !important;
}}

button[data-baseweb="tab"][aria-selected="true"] {{
    color: var(--gold) !important;
}}

div[data-baseweb="tab-highlight"] {{
    background-color: var(--gold) !important;
}}

/* Tables */
[data-testid="stDataFrame"] {{
    border: 1px solid var(--border);
    border-radius: var(--radius);
    overflow: hidden;
}}

/* Alerts */
div[data-testid="stAlert"] {{
    border-radius: var(--radius-sm);
    border: 1px solid var(--border);
}}

/* Expander */
details {{
    background: var(--surface-alt) !important;
    border: 1px solid var(--border) !important;
    border-radius: var(--radius-sm) !important;
}}

/* Login */
.login-shell {{
    max-width: 720px;
    margin: 8vh auto 24px;
    text-align: center;
    padding: 34px;
    background: linear-gradient(180deg, var(--surface), var(--surface-alt));
    border: 1px solid var(--border);
    border-radius: 16px;
    box-shadow: 0 20px 60px rgba(0,0,0,.12);
}}

.login-brand {{
    font-size: 30px;
    font-weight: 800;
    letter-spacing: -.03em;
    color: var(--text);
}}

.login-bar {{
    display: inline-block;
    width: 5px;
    height: 30px;
    background: var(--gold);
    border-radius: 3px;
    vertical-align: -3px;
    margin-right: 10px;
}}

.login-subtitle {{
    color: var(--gold);
    font-weight: 700;
    margin-top: 8px;
    font-size: 16px;
}}

.login-description {{
    color: var(--muted);
    margin-top: 8px;
    font-size: 13px;
}}

/* Status */
.status-bar {{
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 12px;
    margin-top: 26px;
    padding: 10px 18px;
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    color: var(--muted);
    font-size: 12px;
}}

.status-dot {{
    width: 7px;
    height: 7px;
    background: var(--green);
    border-radius: 50%;
    display: inline-block;
    box-shadow: 0 0 7px var(--green);
}}

/* Ticker */
.ticker-wrap {{
    overflow: hidden;
    white-space: nowrap;
    padding: 12px 0;
    background: var(--surface) !important;
    border: 1px solid var(--border) !important;
    border-radius: 10px;
    margin-bottom: 20px;
}}

.ticker-move {{
    display: inline-block;
    animation: marquee 90s linear infinite;
}}

.ticker-wrap:hover .ticker-move {{
    animation-play-state: paused;
}}

.ticker-item {{
    display: inline-block;
    padding: 0 24px;
    font-family: 'JetBrains Mono', monospace;
    font-size: 13px;
    font-weight: 600;
}}

.ticker-item a {{
    color: var(--muted);
    text-decoration: none;
}}

.ticker-item a:hover {{
    color: var(--gold);
}}

@keyframes marquee {{
    0% {{ transform: translateX(0); }}
    100% {{ transform: translateX(-50%); }}
}}

/* Light theme refinements */

[data-theme="light"] .stApp {{
    background: #F3F5F8 !important;
    color: #17202A !important;
}}

[data-theme="light"] section[data-testid="stSidebar"] {{
    background: #FFFFFF !important;
    border-right: 1px solid #D7DDE7 !important;
}}

[data-theme="light"] .app-header,
[data-theme="light"] .login-shell,
[data-theme="light"] div[data-testid="stMetric"],
[data-theme="light"] div[data-testid="stVerticalBlockBorderWrapper"],
[data-theme="light"] .status-bar,
[data-theme="light"] .ticker-wrap {{
    box-shadow: 0 6px 22px rgba(16,24,40,.06);
}}

[data-theme="light"] .stTextInput input,
[data-theme="light"] .stNumberInput input,
[data-theme="light"] .stDateInput input,
[data-theme="light"] .stSelectbox div[data-baseweb="select"] > div {{
    background: #FFFFFF !important;
    color: #17202A !important;
    border-color: #CFD6E1 !important;
}}

[data-theme="light"] .stButton > button,
[data-theme="light"] .stDownloadButton > button {{
    background: #FFFFFF !important;
    color: #17202A !important;
    border-color: #CFD6E1 !important;
}}

[data-theme="light"] .stButton > button:hover,
[data-theme="light"] .stDownloadButton > button:hover {{
    border-color: #A97900 !important;
    color: #8A6700 !important;
}}

[data-theme="light"] button[kind="primary"] {{
    background: #B8860B !important;
    color: #FFFFFF !important;
    border-color: #B8860B !important;
}}

[data-theme="light"] details {{
    background: #FFFFFF !important;
    border-color: #D7DDE7 !important;
}}

[data-theme="light"] div[data-testid="stAlert"] {{
    background: #FFFFFF !important;
}}

[data-theme="light"] .header-tag {{
    background: rgba(169,121,0,.08);
    border-color: rgba(169,121,0,.25);
}}
</style>
""", unsafe_allow_html=True)

# ============================================================
# SIDEBAR USER CONTROLS
# ============================================================
st.sidebar.markdown("## 👤 Account")
st.sidebar.caption(f"Signed in as **{st.session_state.user_name}** · {st.session_state.user_role}")
sc1, sc2 = st.sidebar.columns(2)
with sc1:
    if st.button("🌗 Theme", use_container_width=True):
        st.session_state.theme = "Light" if st.session_state.theme == "Dark" else "Dark"
        st.rerun()
with sc2:
    if st.button("🚪 Logout", use_container_width=True):
        for k in ["authenticated", "user_id", "user_name", "user_role", "loaded_user_id"]:
            st.session_state.pop(k, None)
        st.rerun()

# ============================================================
# ORIGINAL TRADING HELPERS
# ============================================================

@st.cache_data(ttl=30, show_spinner=False)
def get_live_stock_data(symbol):
    """Cached single-symbol fallback."""
    data = get_live_prices_batch((symbol,))
    return data.get(symbol.upper().strip(), (0.0, 0.0))


@st.cache_data(ttl=30, show_spinner=False)
def get_live_prices_batch(symbols):
    """Fetch all ticker prices in one Yahoo request."""
    symbols = tuple(dict.fromkeys(str(s).upper().strip() for s in symbols if str(s).strip()))
    if not symbols:
        return {}

    idx_map = {"NIFTY": "^NSEI", "BANKNIFTY": "^NSEBANK", "FINNIFTY": "^CNXFIN"}
    yahoo_symbols = [
        idx_map.get(sym, f"{sym}.NS" if not sym.endswith(".NS") else sym)
        for sym in symbols
    ]

    result = {sym: (0.0, 0.0) for sym in symbols}

    try:
        data = yf.download(
            yahoo_symbols,
            period="2d",
            interval="1d",
            group_by="ticker",
            auto_adjust=False,
            progress=False,
            threads=True,
        )

        if data is None or data.empty:
            return result

        for sym, ysym in zip(symbols, yahoo_symbols):
            try:
                if len(yahoo_symbols) == 1:
                    close = pd.to_numeric(data["Close"], errors="coerce").dropna()
                else:
                    close = pd.to_numeric(data[ysym]["Close"], errors="coerce").dropna()

                if not close.empty:
                    current = float(close.iloc[-1])
                    previous = float(close.iloc[-2]) if len(close) >= 2 else current
                    change = ((current - previous) / previous * 100) if previous else 0.0
                    result[sym] = (round(current, 2), round(change, 2))
            except Exception:
                continue

    except Exception:
        pass

    return result


@st.cache_data(ttl=60, show_spinner=False)
def get_last_closing_price(symbol):
    sym = symbol.upper().strip()
    idx_map = {"NIFTY": "^NSEI", "BANKNIFTY": "^NSEBANK", "FINNIFTY": "^CNXFIN"}
    ticker_symbol = idx_map.get(sym, f"{sym}.NS" if not sym.endswith(".NS") else sym)

    try:
        hist = yf.Ticker(ticker_symbol).history(period="5d", auto_adjust=False)

        if hist.empty or "Close" not in hist.columns:
            return 0.05

        close = pd.to_numeric(hist["Close"], errors="coerce").dropna()

        if close.empty:
            return 0.05

        return round(float(close.iloc[-1]), 2)

    except Exception:
        return 0.05


@st.cache_data(ttl=120, show_spinner=False)
def get_history(symbol, period="1mo"):
    sym = symbol.upper().strip()
    idx_map = {"NIFTY":"^NSEI", "BANKNIFTY":"^NSEBANK", "FINNIFTY":"^CNXFIN"}
    ticker_symbol = idx_map.get(sym, f"{sym}.NS" if not sym.endswith(".NS") else sym)
    try:
        hist = yf.Ticker(ticker_symbol).history(period=period, auto_adjust=False)
        if hist.empty:
            return pd.DataFrame()
        df = hist.reset_index()
        df["Date"] = pd.to_datetime(df["Date"]).dt.tz_localize(None)
        cols = [c for c in ["Date","Open","High","Low","Close","Volume"] if c in df.columns]
        return df[cols]
    except Exception:
        return pd.DataFrame()


def calculate_dynamic_strike(symbol, ltp):
    sym=symbol.upper()
    if "NIFTY" in sym and "BANK" not in sym: return round(ltp/50)*50
    if "BANKNIFTY" in sym: return round(ltp/100)*100
    if ltp < 100: return round(ltp)
    if ltp < 500: return round(ltp/5)*5
    if ltp < 1000: return round(ltp/10)*10
    if ltp < 2000: return round(ltp/20)*20
    if ltp < 4000: return round(ltp/40)*40
    if ltp <= 5000: return round(ltp/50)*50
    return round(ltp/100)*100


def get_last_tuesday(year, month):
    last_day=calendar.monthrange(year,month)[1]
    d=date(year,month,last_day)
    return d-pd.Timedelta(days=(d.weekday()-1)%7)


@st.cache_data(ttl=300, show_spinner=False)
def fetch_option_ltp_via_derivatives_df(symbol, expiry_date, strike, opt_type, entry_dt):
    """
    Return the latest available NSE derivatives CLOSE for an index option.

    The original version searched only around the entry date, which caused
    an open option position to keep using its entry-day premium. This version
    searches from the later of the entry window / recent history through today,
    so the position can be marked to the latest available derivatives close.
    """
    if derivatives_df is None:
        return None

    try:
        # Normalize dates.
        if isinstance(entry_dt, datetime):
            start_dt = entry_dt.date()
        elif isinstance(entry_dt, date):
            start_dt = entry_dt
        else:
            start_dt = datetime.strptime(
                str(entry_dt), "%d-%b-%Y"
            ).date()

        today_dt = date.today()

        # Do not query into the future.
        end_dt = max(start_dt, today_dt)

        # Keep a practical look-back window so current positions can be
        # repriced even when the market has no row for today (weekend/holiday).
        from_dt = min(start_dt, end_dt - timedelta(days=10))

        expiry_dt = expiry_date
        if isinstance(expiry_dt, datetime):
            expiry_dt = expiry_dt.date()
        elif not isinstance(expiry_dt, date):
            for fmt in ("%d-%b-%Y", "%d-%B-%Y", "%Y-%m-%d"):
                try:
                    expiry_dt = datetime.strptime(
                        str(expiry_dt), fmt
                    ).date()
                    break
                except ValueError:
                    expiry_dt = None

        df = derivatives_df(
            symbol=symbol.upper().strip(),
            from_date=from_dt,
            to_date=end_dt,
            expiry_date=expiry_dt,
            instrument_type="OPTIDX",
            strike_price=float(strike),
            option_type=opt_type.upper().strip(),
        )

       if df is None or df.empty:
          st.warning(
                     f"No NSE data returned: {symbol} | "
                     f"{expiry_dt} | {strike} | {opt_type} | {entry_date}"
           )
              return None

       if "CLOSE" not in df.columns:
            st.warning(f"NSE data returned, but CLOSE column is missing. Columns: {list(df.columns)}")
             return None

        close_series = pd.to_numeric(df["CLOSE"], errors="coerce").dropna()

        if close_series.empty:
            return None

        return round(float(close_series.iloc[-1]), 2)

    except Exception:
        return None

@st.cache_data(ttl=300, show_spinner=False)
def fetch_option_entry_price(symbol, expiry_date, strike, opt_type, entry_dt):
    """
    Fetch the NSE derivatives CLOSE for the exact selected entry date
    and exact index-option contract using jugaad_data.nse.
    """
    if derivatives_df is None:
        return None

    try:
        if isinstance(entry_dt, datetime):
            entry_date = entry_dt.date()
        elif isinstance(entry_dt, date):
            entry_date = entry_dt
        else:
            entry_date = datetime.strptime(
                str(entry_dt), "%d-%b-%Y"
            ).date()

        expiry_dt = expiry_date
        if isinstance(expiry_dt, datetime):
            expiry_dt = expiry_dt.date()
        elif not isinstance(expiry_dt, date):
            for fmt in ("%d-%b-%Y", "%d-%B-%Y", "%Y-%m-%d"):
                try:
                    expiry_dt = datetime.strptime(
                        str(expiry_dt), fmt
                    ).date()
                    break
                except ValueError:
                    expiry_dt = None

        if expiry_dt is None:
            return None

        df = derivatives_df(
            symbol=symbol.upper().strip(),
            from_date=entry_date,
            to_date=entry_date,
            expiry_date=expiry_dt,
            instrument_type="OPTIDX",
            strike_price=float(strike),
            option_type=opt_type.upper().strip(),
        )

        if df is None or df.empty or "CLOSE" not in df.columns:
            return None

        close_series = pd.to_numeric(
            df["CLOSE"], errors="coerce"
        ).dropna()

        if close_series.empty:
            return None

        return round(float(close_series.iloc[-1]), 2)

    except Exception:
        return None


def get_position_ltp(contract, position, live_price_map):
    """
    Resolve the mark-to-market price for an active position.

    EQ positions use the live stock price map.
    Option positions use the derivatives data for their exact
    symbol/expiry/strike/CE-PE contract.
    """
    avg_price = float(position.get("avg_price", 0) or 0)
    position_type = str(position.get("type", "EQ")).upper()

    # Equity / stock
    if position_type == "EQ":
        base = contract.split("_")[0].upper()
        ltp = live_price_map.get(base, position.get("last_price", avg_price))
        try:
            ltp = float(ltp)
        except (TypeError, ValueError):
            ltp = avg_price
        return ltp if ltp > 0 else avg_price

    # Option contract format:
    # SYMBOL_EXPIRY_STRIKE_CE/PE
    parts = contract.split("_")

    if len(parts) >= 4:
        symbol = parts[0].upper()
        expiry_text = parts[1]
        strike_text = parts[2]
        opt_type = parts[3].upper()

        try:
            strike = float(strike_text)

            expiry = datetime.strptime(
                expiry_text, "%d-%b-%Y"
            ).date()

            entry_text = str(
                position.get("entry_date", date.today())
            )

            try:
                entry_dt = datetime.strptime(
                    entry_text, "%d-%b-%Y"
                ).date()
            except ValueError:
                entry_dt = date.today()

            option_ltp = fetch_option_ltp_via_derivatives_df(
                symbol=symbol,
                expiry_date=expiry,
                strike=strike,
                opt_type=opt_type,
                entry_dt=entry_dt,
            )

            if option_ltp is not None and option_ltp > 0:
                return float(option_ltp)

        except (TypeError, ValueError):
            pass

    # Safe fallback for an option if derivatives data is unavailable.
    stored_ltp = position.get("last_price", avg_price)
    try:
        stored_ltp = float(stored_ltp)
    except (TypeError, ValueError):
        stored_ltp = avg_price

    return stored_ltp if stored_ltp > 0 else avg_price

# ============================================================
# HEADER
# ============================================================
st.markdown(f"""
<div class="app-header">
  <div>
    <div class="brand-title"><span class="brand-bar"></span>PUSHYAMI CAPITALS PAPER TRADING TERMINAL</div>
    <div class="brand-sub">Classic professional paper trading · {st.session_state.user_name} · Google Sheets persistence</div>
  </div>
  <div class="header-tag">PAPER TRADING</div>
</div>
""", unsafe_allow_html=True)

# ============================================================
# TICKER
# ============================================================
base_watchlist=["HDFCBANK","ICICIBANK","SBIN","AXISBANK","KOTAKBANK","BAJFINANCE","JIOFIN","TCS","INFY","HCLTECH","TECHM","WIPRO","RELIANCE","ONGC","NTPC","COALINDIA","POWERGRID","M&M","MARUTI","TATAMOTORS","BAJAJ-AUTO","EICHERMOT","ITC","HINDUNILVR","NESTLEIND","TATACONSUM","TRENT","LT","TATASTEEL","HINDALCO","JSWSTEEL","ULTRACEMCO","SUNPHARMA","CIPLA","APOLLOHOSP","MAXHEALTH","ADANIENT","ADANIPORTS","ASIANPAINT","BRITANNIA","BPCL","GRASIM","HEROMOTOCO","INDUSINDBK","JSWENERGY","LTIM","SHRIRAMFIN","TITAN"]
active_symbols=[key.split("_")[0] for key in st.session_state.positions.keys()]
ticker_symbols=list(dict.fromkeys(base_watchlist+active_symbols))

# Fetch the complete ticker in one cached request.
live_data_map=get_live_prices_batch(tuple(ticker_symbols))
live_price_map={sym: live_data_map.get(sym,(0.0,0.0))[0] for sym in ticker_symbols}

ticker_items=[]
for sym in ticker_symbols:
    price,chg=live_data_map.get(sym,(0.0,0.0))
    color="#3FB950" if chg>=0 else "#F85149"
    arrow="▲" if chg>=0 else "▼"
    ticker_items.append(
        f'<span class="ticker-item"><a href="?symbol={sym}">{sym}</a> '
        f'₹{price:,.2f} <span style="color:{color}">{arrow} {chg:+.2f}%</span></span>'
    )

ticker_markup="".join(ticker_items)
st.markdown(
    f'<div class="ticker-wrap"><div class="ticker-move">{ticker_markup}{ticker_markup}</div></div>',
    unsafe_allow_html=True,
)

selected_ticker_from_marquee=st.query_params.get("symbol",None)

# ============================================================
# PORTFOLIO CALC
# ============================================================
def portfolio_values():
    invested = 0.0
    value = 0.0

    for contract, position in st.session_state.positions.items():
        ltp = get_position_ltp(
            contract,
            position,
            live_price_map,
        )

        qty = float(position.get("qty", 0) or 0)
        avg_price = float(position.get("avg_price", 0) or 0)

        invested += qty * avg_price
        value += qty * ltp

    total = st.session_state.cash_balance + value
    pnl = total - st.session_state.initial_capital

    return invested, value, total, pnl

invested,position_value,portfolio_value,total_pnl=portfolio_values()

k1,k2,k3,k4=st.columns(4)
k1.metric("Cash Balance",f"₹{st.session_state.cash_balance:,.2f}")
k2.metric("Portfolio Value",f"₹{portfolio_value:,.2f}")
k3.metric("Invested",f"₹{invested:,.2f}")
k4.metric("Overall P&L",f"₹{total_pnl:,.2f}",delta=f"₹{total_pnl:,.2f}")

# ============================================================
# SIDEBAR ORDER PANEL
# ============================================================
st.sidebar.markdown("## ⚡ Place Paper Order")
asset_class=st.sidebar.selectbox("Asset Class",["EQ (Equity)","F&O Option"])
entry_date=st.sidebar.date_input("Entry Date",value=date.today(),format="DD/MM/YYYY")
entry_date_str=entry_date.strftime("%d-%b-%Y")
selected_expiry_str=""; active_symbol_for_chain="NIFTY"; underlying_spot=24500.0

if asset_class=="EQ (Equity)":
    symbol=(selected_ticker_from_marquee or "SBIN").upper()
    symbol=st.sidebar.text_input("Trading Symbol",value=symbol).upper().strip()
    trade_key=symbol; asset_type="EQ"
    last_close_price=get_last_closing_price(symbol)
    default_price=last_close_price
    st.sidebar.info(f"Last Closing Price: ₹{last_close_price:,.2f}")
else:
    opts=["NIFTY","BANKNIFTY","FINNIFTY"]
    default_idx=opts.index(selected_ticker_from_marquee) if selected_ticker_from_marquee in opts else 0
    symbol=st.sidebar.selectbox("Symbol / Index",opts,index=default_idx)
    active_symbol_for_chain=symbol
    option_type=st.sidebar.selectbox("Option Type",["CE","PE"])
    last_close_price=get_last_closing_price(symbol); underlying_spot=last_close_price
    st.sidebar.success(f"Underlying Last Close: ₹{last_close_price:,.2f}")
    suggested_strike=calculate_dynamic_strike(symbol,last_close_price)
    today=date.today(); default_expiry=get_last_tuesday(today.year,today.month)
    selected_expiry=st.sidebar.date_input("Expiry Date",value=default_expiry,format="DD/MM/YYYY")
    selected_expiry_str=selected_expiry.strftime("%d-%b-%Y").upper()
    strike_price=st.sidebar.number_input("Strike Price",min_value=1.0,value=float(suggested_strike),step=1.0)
    trade_key=f"{symbol}_{selected_expiry_str}_{int(strike_price)}_{option_type}"; asset_type=option_type
    price_key=f"fetched_price_{trade_key}"
    if price_key not in st.session_state: st.session_state[price_key]=150.0
    if st.sidebar.button("🔄 Fetch Option LTP",use_container_width=True):
        with st.sidebar:
            with st.spinner("Fetching option premium..."):
                fetched=fetch_option_entry_price(
                                                    symbol,
                                                    selected_expiry,
                                                        strike_price,
                                                       option_type,
                                                      entry_date
                                                  )
                if fetched: st.session_state[price_key]=fetched; st.success(f"Fetched LTP: ₹{fetched:,.2f}")
                else: st.warning("No archive tick found. Enter price manually.")
    default_price=st.session_state[price_key]

action=st.sidebar.radio("Action",["BUY","SELL"],horizontal=True)
qty=st.sidebar.number_input("Quantity / Lots",min_value=1,value=50)
price=st.sidebar.number_input("Execution Price (₹)",min_value=0.05,value=float(default_price),step=0.5)
execute_clicked=st.sidebar.button("EXECUTE ORDER",type="primary",use_container_width=True)
reset_clicked=st.sidebar.button("Reset Portfolio",use_container_width=True)

if execute_clicked:
    total_value=float(qty)*float(price)
    if action=="BUY":
        if st.session_state.cash_balance < total_value:
            st.sidebar.error("Insufficient virtual cash balance.")
        else:
            st.session_state.cash_balance-=total_value
            if trade_key in st.session_state.positions:
                p=st.session_state.positions[trade_key]; nq=p["qty"]+qty
                p["avg_price"]=((p["qty"]*p["avg_price"])+(qty*price))/nq; p["qty"]=nq
            else:
                st.session_state.positions[trade_key]={"qty":qty,"avg_price":price,"type":asset_type,"entry_date":entry_date_str,"side":"LONG"}
            append_trade(worksheets["Trades"],user_id,"BUY",trade_key,qty,price,asset_type)
            save_positions(worksheets["Positions"],user_id,st.session_state.positions,live_price_map)
            inv,val,pv,pnl=portfolio_values(); save_account(worksheets["Accounts"],user_id,st.session_state.initial_capital,st.session_state.cash_balance,pv,pnl)
            st.sidebar.success(f"BOUGHT {qty} {trade_key} @ ₹{price:,.2f}")
    else:
        st.session_state.cash_balance+=total_value
        if trade_key in st.session_state.positions:
            p=st.session_state.positions[trade_key]
            if p.get("side")=="LONG":
                p["qty"]-=qty
                if p["qty"]<=0: del st.session_state.positions[trade_key]
            else:
                nq=p["qty"]+qty; p["avg_price"]=((p["qty"]*p["avg_price"])+(qty*price))/nq; p["qty"]=nq
        else:
            st.session_state.positions[trade_key]={"qty":qty,"avg_price":price,"type":asset_type,"entry_date":entry_date_str,"side":"SHORT"}
        append_trade(worksheets["Trades"],user_id,"SELL",trade_key,qty,price,asset_type)
        save_positions(worksheets["Positions"],user_id,st.session_state.positions,live_price_map)
        inv,val,pv,pnl=portfolio_values(); save_account(worksheets["Accounts"],user_id,st.session_state.initial_capital,st.session_state.cash_balance,pv,pnl)
        st.sidebar.success(f"SOLD {qty} {trade_key} @ ₹{price:,.2f}")

if reset_clicked:
    st.session_state.cash_balance=st.session_state.initial_capital
    st.session_state.positions={}
    save_positions(worksheets["Positions"],user_id,{},live_price_map)
    inv,val,pv,pnl=portfolio_values(); save_account(worksheets["Accounts"],user_id,st.session_state.initial_capital,st.session_state.cash_balance,pv,pnl)
    st.rerun()

# ============================================================
# MAIN TABS
# ============================================================
tab_dash,tab_positions,tab_chart,tab_chain,tab_trades=st.tabs(["📊 Dashboard","💼 Positions","📈 Charts","⛓ Option Chain","📜 Trades"])

with tab_dash:
    st.subheader("Trading Dashboard")
    d1,d2=st.columns([1.4,1])
    with d1:
        st.markdown("### Portfolio Snapshot")
        st.dataframe(pd.DataFrame([{
            "Account":st.session_state.user_id,
            "Cash":round(st.session_state.cash_balance,2),
            "Invested":round(invested,2),
            "Portfolio Value":round(portfolio_value,2),
            "P&L":round(total_pnl,2),
            "Open Positions":len(st.session_state.positions),
        }]),use_container_width=True,hide_index=True)
    with d2:
        st.markdown("### Account")
        st.info(f"**{st.session_state.user_name}**\n\nRole: **{st.session_state.user_role}**\n\nInitial capital: **₹{st.session_state.initial_capital:,.2f}**")

with tab_positions:
    st.subheader("Active Open Positions")

    if st.session_state.positions:

        # ====================================================
        # POSITION ACTIONS
        # ====================================================
        st.markdown("### Position Actions")

        position_keys = list(st.session_state.positions.keys())

        selected_position = st.selectbox(
            "Select Position",
            position_keys,
            format_func=lambda k: k,
            key="position_action_selector",
        )

        selected_position_data = st.session_state.positions[selected_position]

        current_position_ltp = get_position_ltp(
            selected_position,
            selected_position_data,
            live_price_map,
        )

        action_col1, action_col2, action_col3 = st.columns([1, 1, 1.2])

        with action_col1:
            add_qty = st.number_input(
                "➕ Add Quantity",
                min_value=1,
                value=1,
                step=1,
                key="add_position_qty",
            )

        with action_col2:
            add_price = st.number_input(
                "Add Price",
                min_value=0.05,
                value=float(current_position_ltp),
                step=0.05,
                key="add_position_price",
            )

        with action_col3:
            st.write("")
            st.write("")

            add_quantity_clicked = st.button(
                "➕ ADD QUANTITY",
                type="primary",
                use_container_width=True,
            )

        if add_quantity_clicked:

            p = st.session_state.positions.get(selected_position)

            if p is None:
                st.error("Position no longer exists. Refresh the page.")
            else:

                add_qty = int(add_qty)
                add_price = float(add_price)

                side = str(
                    p.get("side", "LONG")
                ).upper()

                add_value = add_qty * add_price

                # --------------------------------------------
                # LONG POSITION
                # Adding quantity means BUY
                # --------------------------------------------
                if side == "LONG":

                    if st.session_state.cash_balance < add_value:

                        st.error(
                            f"Insufficient virtual cash. "
                            f"Required: ₹{add_value:,.2f}"
                        )

                    else:

                        old_qty = float(p.get("qty", 0))
                        old_avg = float(p.get("avg_price", 0))

                        new_qty = old_qty + add_qty

                        new_avg = (
                            (old_qty * old_avg)
                            + (add_qty * add_price)
                        ) / new_qty

                        st.session_state.cash_balance -= add_value

                        p["qty"] = new_qty
                        p["avg_price"] = new_avg
                        p["last_price"] = current_position_ltp

                        append_trade(
                            worksheets["Trades"],
                            user_id,
                            "BUY",
                            selected_position,
                            add_qty,
                            add_price,
                            p.get("type", "EQ"),
                        )

                        save_positions(
                            worksheets["Positions"],
                            user_id,
                            st.session_state.positions,
                            live_price_map,
                        )

                        inv, val, pv, pnl = portfolio_values()

                        save_account(
                            worksheets["Accounts"],
                            user_id,
                            st.session_state.initial_capital,
                            st.session_state.cash_balance,
                            pv,
                            pnl,
                        )

                        st.success(
                            f"Added {add_qty} quantity to "
                            f"{selected_position} @ ₹{add_price:,.2f}"
                        )

                        st.rerun()

                # --------------------------------------------
                # SHORT POSITION
                # Adding quantity means SELL
                # --------------------------------------------
                else:

                    old_qty = float(p.get("qty", 0))
                    old_avg = float(p.get("avg_price", 0))

                    new_qty = old_qty + add_qty

                    new_avg = (
                        (old_qty * old_avg)
                        + (add_qty * add_price)
                    ) / new_qty

                    st.session_state.cash_balance += add_value

                    p["qty"] = new_qty
                    p["avg_price"] = new_avg
                    p["last_price"] = current_position_ltp

                    append_trade(
                        worksheets["Trades"],
                        user_id,
                        "SELL",
                        selected_position,
                        add_qty,
                        add_price,
                        p.get("type", "EQ"),
                    )

                    save_positions(
                        worksheets["Positions"],
                        user_id,
                        st.session_state.positions,
                        live_price_map,
                    )

                    inv, val, pv, pnl = portfolio_values()

                    save_account(
                        worksheets["Accounts"],
                        user_id,
                        st.session_state.initial_capital,
                        st.session_state.cash_balance,
                        pv,
                        pnl,
                    )

                    st.success(
                        f"Added {add_qty} quantity to "
                        f"SHORT {selected_position} @ ₹{add_price:,.2f}"
                    )

                    st.rerun()

        # ====================================================
        # SQUARE OFF
        # ====================================================
        st.markdown("### Square Off Position")

        sq_col1, sq_col2 = st.columns([1.5, 1])

        with sq_col1:
            st.info(
                f"Current LTP: ₹{current_position_ltp:,.2f}  |  "
                f"Quantity: {selected_position_data.get('qty', 0):,.0f}  |  "
                f"Side: {selected_position_data.get('side', 'LONG')}"
            )

        with sq_col2:
            square_off_clicked = st.button(
                "⛔ SQUARE OFF",
                use_container_width=True,
            )

        if square_off_clicked:

            p = st.session_state.positions.get(selected_position)

            if p is None:
                st.error("Position no longer exists. Refresh the page.")
            else:

                square_qty = float(p.get("qty", 0))
                square_price = get_position_ltp(
                    selected_position,
                    p,
                    live_price_map,
                )

                side = str(
                    p.get("side", "LONG")
                ).upper()

                avg_price = float(
                    p.get("avg_price", 0)
                )

                # --------------------------------------------
                # LONG → SELL TO CLOSE
                # SHORT → BUY TO CLOSE
                # --------------------------------------------
                if side == "LONG":

                    realized_pnl = (
                        square_price - avg_price
                    ) * square_qty

                    st.session_state.cash_balance += (
                        square_qty * square_price
                    )

                    close_action = "SELL"

                else:

                    realized_pnl = (
                        avg_price - square_price
                    ) * square_qty

                    st.session_state.cash_balance -= (
                        square_qty * square_price
                    )

                    close_action = "BUY"

                # --------------------------------------------
                # RECORD SQUARE-OFF TRADE
                # --------------------------------------------
                append_trade(
                    worksheets["Trades"],
                    user_id,
                    close_action,
                    selected_position,
                    square_qty,
                    square_price,
                    p.get("type", "EQ"),
                )

                # Remove completely from open positions
                del st.session_state.positions[selected_position]

                # Save updated positions
                save_positions(
                    worksheets["Positions"],
                    user_id,
                    st.session_state.positions,
                    live_price_map,
                )

                # Save updated account
                inv, val, pv, pnl = portfolio_values()

                save_account(
                    worksheets["Accounts"],
                    user_id,
                    st.session_state.initial_capital,
                    st.session_state.cash_balance,
                    pv,
                    pnl,
                )

                pnl_label = (
                    f"+₹{realized_pnl:,.2f}"
                    if realized_pnl >= 0
                    else f"-₹{abs(realized_pnl):,.2f}"
                )

                st.success(
                    f"Squared off {selected_position} | "
                    f"Quantity: {square_qty:,.0f} | "
                    f"Exit: ₹{square_price:,.2f} | "
                    f"Realized P&L: {pnl_label}"
                )

                st.rerun()

    holdings = []
    total_open_pnl = 0.0

    for key, p in st.session_state.positions.items():

        # ----------------------------------------------------
        # CURRENT LTP
        # ----------------------------------------------------
        ltp = get_position_ltp(
            key,
            p,
            live_price_map,
        )

        # ----------------------------------------------------
        # POSITION VALUES
        # ----------------------------------------------------
        qty = float(p.get("qty", 0) or 0)
        avg_price = float(p.get("avg_price", 0) or 0)

        invested_value = qty * avg_price
        market_value = qty * ltp

        # ----------------------------------------------------
        # POSITION P&L
        # ----------------------------------------------------
        if p.get("side", "LONG") == "SHORT":
            pnl = invested_value - market_value
        else:
            pnl = market_value - invested_value

        total_open_pnl += pnl

        # ----------------------------------------------------
        # DISPLAY TYPE / OPTION DETAILS
        # ----------------------------------------------------
        position_type = str(p.get("type", "EQ")).upper()

        if position_type == "EQ":
            display_type = "STOCK"
            option_details = "—"
            symbol = key.split("_")[0]

        else:
            display_type = "OPTION"
            parts = key.split("_")

            if len(parts) >= 4:
                symbol = parts[0]
                option_details = f"{parts[2]} {parts[3]}"
            else:
                symbol = key
                option_details = "—"

        # ----------------------------------------------------
        # P&L %
        # ----------------------------------------------------
        pnl_pct = (
            (pnl / invested_value) * 100
            if invested_value > 0
            else 0.0
        )

        holdings.append(
            {
                "Symbol": symbol,
                "Type": display_type,
                "Option": option_details,
                "Side": p.get("side", "LONG"),
                "Entry Date": p.get("entry_date", "N/A"),
                "Quantity": qty,
                "Avg Price": round(avg_price, 2),
                "Current LTP": round(ltp, 2),
                "P&L (₹)": round(pnl, 2),
                "P&L (%)": round(pnl_pct, 2),
            }
        )

    # --------------------------------------------------------
    # TOTAL OPEN P&L
    # --------------------------------------------------------
    if holdings:

        pnl_color = (
            "#16A34A"
            if total_open_pnl > 0
            else "#DC2626"
            if total_open_pnl < 0
            else "#808080"
        )

        pnl_sign = "+" if total_open_pnl > 0 else ""

        st.metric(
          "Total Open P&L",
           f"₹{total_open_pnl:+,.2f}"
       )

        positions_df = pd.DataFrame(holdings)

        st.dataframe(
            positions_df,
            use_container_width=True,
            hide_index=True,
            height=450,
        )

    else:
        st.info("No active open positions.")

with tab_chart:

    # ========================================================
    # CLEAN PROFESSIONAL MARKET CHART
    # ========================================================
    st.subheader("Market Chart")
    st.caption("Clean price view with OHLC data, timeframe and chart-type controls.")

    # Chart controls
    chart_c1, chart_c2, chart_c3, chart_c4 = st.columns([1.35, 1, 1, 0.75])

    with chart_c1:
        default_chart_symbol = (
            selected_ticker_from_marquee
            or active_symbol_for_chain
            or "NIFTY"
        )

        chart_symbol = st.text_input(
            "Symbol",
            value=str(default_chart_symbol).upper(),
            key="market_chart_symbol",
        ).upper().strip()

    with chart_c2:
        chart_period = st.selectbox(
            "Timeframe",
            ["5d", "1mo", "3mo", "6mo", "1y", "2y", "5y"],
            index=1,
            key="market_chart_period",
        )

    with chart_c3:
        chart_type = st.selectbox(
            "Chart Type",
            ["Candlestick", "Line", "Area"],
            index=0,
            key="market_chart_type",
        )

    with chart_c4:
        st.write("")
        st.write("")
        refresh_chart = st.button(
            "↻ Refresh",
            use_container_width=True,
            key="refresh_market_chart",
        )

    # Refreshing the Streamlit app is enough to pull fresh market data.
    if refresh_chart:
        st.rerun()

    chart_data = get_history(chart_symbol, chart_period)

    if not chart_data.empty:

        chart_data = chart_data.copy()
        chart_data["Date"] = pd.to_datetime(chart_data["Date"])
        chart_data = chart_data.sort_values("Date").drop_duplicates(
            subset=["Date"],
            keep="last",
        )

        # ----------------------------------------------------
        # Latest market snapshot
        # ----------------------------------------------------
        latest = chart_data.iloc[-1]

        previous_close = (
            float(chart_data["Close"].iloc[-2])
            if len(chart_data) > 1
            else float(latest["Close"])
        )

        latest_close = float(latest["Close"])
        day_change = latest_close - previous_close
        day_change_pct = (
            (day_change / previous_close) * 100
            if previous_close
            else 0
        )

        mc1, mc2, mc3, mc4 = st.columns(4)

        mc1.metric(
            "Last Price",
            f"₹{latest_close:,.2f}",
            delta=f"{day_change:+,.2f}",
        )

        mc2.metric(
            "Open",
            f"₹{float(latest['Open']):,.2f}",
        )

        mc3.metric(
            "High / Low",
            f"₹{float(latest['High']):,.2f} / ₹{float(latest['Low']):,.2f}",
        )

        mc4.metric(
            "Change %",
            f"{day_change_pct:+.2f}%",
        )

        # ----------------------------------------------------
        # Professional Plotly chart
        # ----------------------------------------------------
        if PLOTLY_AVAILABLE:

            if chart_type == "Candlestick":

                fig = go.Figure(
                    data=[
                        go.Candlestick(
                            x=chart_data["Date"],
                            open=chart_data["Open"],
                            high=chart_data["High"],
                            low=chart_data["Low"],
                            close=chart_data["Close"],
                            increasing_line_color="#16A34A",
                            decreasing_line_color="#DC2626",
                            increasing_fillcolor="#16A34A",
                            decreasing_fillcolor="#DC2626",
                            name=chart_symbol,
                        )
                    ]
                )

            elif chart_type == "Area":

                fig = go.Figure()

                fig.add_trace(
                    go.Scatter(
                        x=chart_data["Date"],
                        y=chart_data["Close"],
                        mode="lines",
                        name=chart_symbol,
                        line=dict(width=2),
                        fill="tozeroy",
                        fillcolor="rgba(201,162,39,0.10)",
                    )
                )

            else:

                fig = go.Figure()

                fig.add_trace(
                    go.Scatter(
                        x=chart_data["Date"],
                        y=chart_data["Close"],
                        mode="lines",
                        name=chart_symbol,
                        line=dict(width=2),
                    )
                )

            # ------------------------------------------------
            # Theme-aware chart styling
            # ------------------------------------------------
            if st.session_state.theme == "Light":
                chart_bg = "#FFFFFF"
                chart_paper = "#FFFFFF"
                chart_text = "#17202A"
                grid_color = "#E7EBF0"
            else:
                chart_bg = "#12161F"
                chart_paper = "#12161F"
                chart_text = "#E7E9EE"
                grid_color = "#262C3D"

            fig.update_layout(
                height=560,
                margin=dict(l=10, r=20, t=18, b=10),
                paper_bgcolor=chart_paper,
                plot_bgcolor=chart_bg,
                font=dict(
                    family="Inter, sans-serif",
                    color=chart_text,
                    size=12,
                ),
                hovermode="x unified",
                showlegend=False,
                xaxis=dict(
                    title=None,
                    showgrid=False,
                    rangeslider=dict(visible=False),
                    type="date",
                    fixedrange=False,
                ),
                yaxis=dict(
                    title=None,
                    showgrid=True,
                    gridcolor=grid_color,
                    zeroline=False,
                    fixedrange=False,
                    tickprefix="₹",
                    separatethousands=True,
                ),
                dragmode="pan",
            )

            fig.update_xaxes(
                showline=False,
                showspikes=True,
                spikemode="across",
                spikesnap="cursor",
                spikethickness=1,
            )

            fig.update_yaxes(
                showspikes=True,
                spikemode="across",
                spikesnap="cursor",
                spikethickness=1,
            )

            st.plotly_chart(
                fig,
                use_container_width=True,
                config={
                    "displaylogo": False,
                    "responsive": True,
                    "scrollZoom": True,
                    "displayModeBar": True,
                    "modeBarButtonsToRemove": [
                        "lasso2d",
                        "select2d",
                    ],
                },
            )

        else:

            # Safe fallback if Plotly is not installed.
            st.info(
                "Plotly is not installed. Showing the clean Streamlit chart instead."
            )

            st.line_chart(
                chart_data.set_index("Date")[["Close"]],
                height=520,
                use_container_width=True,
            )

        # ----------------------------------------------------
        # Compact OHLC summary
        # ----------------------------------------------------
        st.markdown("### OHLC")

        o1, o2, o3, o4, o5 = st.columns(5)

        o1.metric("Open", f"₹{float(latest['Open']):,.2f}")
        o2.metric("High", f"₹{float(latest['High']):,.2f}")
        o3.metric("Low", f"₹{float(latest['Low']):,.2f}")
        o4.metric("Close", f"₹{latest_close:,.2f}")

        if "Volume" in chart_data.columns:
            volume = latest.get("Volume", 0)
            try:
                volume_text = f"{float(volume):,.0f}"
            except Exception:
                volume_text = "—"
            o5.metric("Volume", volume_text)
        else:
            o5.metric("Period", chart_period)

    else:
        st.warning(
            f"Historical market data is unavailable for {chart_symbol}."
        )

with tab_chain:
    if asset_class!="F&O Option":
        st.info("Select F&O Option in the sidebar to use the option-chain tools.")
    else:
        st.subheader(f"Option Chain Matrix — {active_symbol_for_chain}")
        chain_count=st.selectbox(
            "Strike Range",
            [10,20,40],
            index=1,
            format_func=lambda x:f"±{x} strikes",
            key="chain_range",
        )
        step=100 if active_symbol_for_chain=="BANKNIFTY" else 50
        atm=int(calculate_dynamic_strike(active_symbol_for_chain,underlying_spot))
        strikes=[atm+i*step for i in range(-chain_count,chain_count+1)]

        if st.button("Load Option Chain",type="primary",key="load_option_chain"):
            total=len(strikes)*2
            progress=st.progress(0)
            results={}

            # Fetch CE/PE contracts concurrently. Each exact contract is also
            # cached for 5 minutes, so reopening the chain is much faster.
            jobs={}
            with ThreadPoolExecutor(max_workers=8) as executor:
                for strike in strikes:
                    for opt_type in ("CE","PE"):
                        future=executor.submit(
                            fetch_option_ltp_via_derivatives_df,
                            active_symbol_for_chain,
                            selected_expiry,
                            strike,
                            opt_type,
                            entry_date,
                        )
                        jobs[future]=(strike,opt_type)

                done=0
                for future in as_completed(jobs):
                    strike,opt_type=jobs[future]
                    try:
                        results[(strike,opt_type)]=future.result()
                    except Exception:
                        results[(strike,opt_type)]=None
                    done+=1
                    progress.progress(done/total)

            progress.empty()

            rows=[]
            for strike in strikes:
                rows.append({
                    "CE LTP":results.get((strike,"CE")) or 0.0,
                    "Strike":strike,
                    "PE LTP":results.get((strike,"PE")) or 0.0,
                })

            chain_df=pd.DataFrame(rows)

            # Highlight the ATM strike in the matrix.
            def highlight_atm(row):
                return [
                    "background-color: rgba(201,162,39,.18); font-weight:700;"
                    if row["Strike"] == atm else ""
                    for _ in row
                ]

            st.dataframe(
                chain_df.style.apply(highlight_atm,axis=1),
                use_container_width=True,
                height=500,
                hide_index=True,
            )

with tab_trades:
    st.subheader("Trade History")
    user_trades=[r for r in records(worksheets["Trades"]) if str(r.get("UserID","")).lower()==user_id.lower()]
    if user_trades:
        df=pd.DataFrame(user_trades)
        st.dataframe(df.iloc[::-1],use_container_width=True,hide_index=True)
    else: st.info("No trades recorded yet.")

# ============================================================
# FOOTER
# ============================================================
st.markdown(f"""
<div class="status-bar">
  <div><span class="status-dot"></span> Session Live · Paper Trading</div>
  <div class="mono">User: {st.session_state.user_id}</div>
  <div class="mono">Open Positions: {len(st.session_state.positions)}</div>
  <div class="mono">Cash: ₹{st.session_state.cash_balance:,.2f}</div>
  <div>v2.0 · {datetime.now().strftime('%d %b %Y, %H:%M')}</div>
</div>
""",unsafe_allow_html=True)
