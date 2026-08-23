import streamlit as st
import pandas as pd

# --- PAGE CONFIGURATION ---
st.set_page_config(
    page_title="NSE Equity & Options Paper Trader",
    page_icon="📈",
    layout="wide"
)

# --- INITIALIZE SESSION STATE FOR OMS ---
if "cash_balance" not in st.session_state:
    st.session_state.cash_balance = 100000.0  # Initial Virtual Capital ₹1 Lakh
    st.session_state.initial_capital = 100000.0
    st.session_state.positions = {}          # {symbol: {"qty":, "avg_price":, "type":}}
    st.session_state.trade_history = []      # Log of completed trades

# --- MAIN APP HEADER ---
st.title("📈 NSE Equity & Options Paper Trading Terminal")
st.markdown("Simulate live execution setups for Indian Equities and F&O contracts safely.")

# --- SIDEBAR: TRADING PANEL ---
st.sidebar.header("⚡ Place Paper Order")

asset_class = st.sidebar.selectbox("Asset Class", ["EQ (Equity)", "CE (Call Option)", "PE (Put Option)"])

if "EQ" in asset_class:
    default_symbol = "SBIN"
    asset_type = "EQ"
else:
    default_symbol = "SBIN_26MAR_800_CE"  # Sample option symbol format
    asset_type = "CE" if "CE" in asset_class else "PE"

symbol_input = st.sidebar.text_input("Trading Symbol", value=default_symbol).upper()
order_action = st.sidebar.radio("Action", ["BUY", "SELL"], horizontal=True)
quantity = st.sidebar.number_input("Quantity / Lots", min_value=1, value=10, step=1)
price = st.sidebar.number_input("Execution Price (₹)", min_value=0.05, value=750.00, step=0.5)

if st.sidebar.button("Execute Order"):
    total_cost = quantity * price
    
    if order_action == "BUY":
        if st.session_state.cash_balance < total_cost:
            st.sidebar.error("❌ Insufficient virtual cash balance!")
        else:
            st.session_state.cash_balance -= total_cost
            if symbol_input in st.session_state.positions:
                pos = st.session_state.positions[symbol_input]
                new_qty = pos["qty"] + quantity
                new_avg = ((pos["qty"] * pos["avg_price"]) + (quantity * price)) / new_qty
                pos["qty"] = new_qty
                pos["avg_price"] = new_avg
            else:
                st.session_state.positions[symbol_input] = {
                    "qty": quantity,
                    "avg_price": price,
                    "type": asset_type
                }
            st.session_state.trade_history.insert(0, {
                "Time": pd.Timestamp.now().strftime("%H:%M:%S"),
                "Action": "BUY",
                "Symbol": symbol_input,
                "Qty": quantity,
                "Price": price
            })
            st.sidebar.success(f"✅ BOUGHT {quantity} of {symbol_input} @ ₹{price}")

    elif order_action == "SELL":
        if symbol_input not in st.session_state.positions or st.session_state.positions[symbol_input]["qty"] < quantity:
            st.sidebar.error("❌ Not enough quantity in holdings to sell!")
        else:
            st.session_state.cash_balance += total_cost
            pos = st.session_state.positions[symbol_input]
            pos["qty"] -= quantity
            if pos["qty"] == 0:
                del st.session_state.positions[symbol_input]
                
            st.session_state.trade_history.insert(0, {
                "Time": pd.Timestamp.now().strftime("%H:%M:%S"),
                "Action": "SELL",
                "Symbol": symbol_input,
                "Qty": quantity,
                "Price": price
            })
            st.sidebar.success(f"✅ SOLD {quantity} of {symbol_input} @ ₹{price}")

# Reset Portfolio Button
if st.sidebar.button("🔄 Reset Portfolio"):
    st.session_state.cash_balance = 100000.0
    st.session_state.positions = {}
    st.session_state.trade_history = []
    st.rerun()

# --- CALCULATE PORTFOLIO METRICS ---
invested_value = 0
current_valuation = 0
holdings_list = []

for sym, pos in st.session_state.positions.items():
    inv = pos["qty"] * pos["avg_price"]
    # For paper tracking display, use avg price as baseline current LTP if mock feed isn't wired
    cur_val = pos["qty"] * pos["avg_price"] 
    pnl = cur_val - inv
    
    invested_value += inv
    current_valuation += cur_val
    
    holdings_list.append({
        "Symbol": sym,
        "Type": pos["type"],
        "Quantity": pos["qty"],
        "Avg Price": round(pos["avg_price"], 2),
        "Current LTP": round(pos["avg_price"], 2),
        "P&L (₹)": round(pnl, 2)
    })

total_portfolio_value = st.session_state.cash_balance + current_valuation
total_pnl = total_portfolio_value - st.session_state.initial_capital
pnl_color_class = "normal" if total_pnl >= 0 else "inverse"

# --- MAIN DASHBOARD LAYOUT ---
col1, col2, col3, col4 = st.columns(4)
col1.metric("Virtual Cash Balance", f"₹{st.session_state.cash_balance:,.2f}")
col2.metric("Invested Capital", f"₹{invested_value:,.2f}")
col3.metric("Total Portfolio Value", f"₹{total_portfolio_value:,.2f}")
col4.metric("Overall P&L", f"₹{total_pnl:,.2f}", delta=f"{round(total_pnl, 2)}")

st.markdown("---")

# --- HOLDINGS TABLE ---
st.subheader("📊 Active Open Positions")
if holdings_list:
    df_holdings = pd.DataFrame(holdings_list)
    st.dataframe(df_holdings, use_container_width=True)
else:
    st.info("No active open positions right now. Use the sidebar to execute a paper trade.")

st.markdown("---")

# --- TRADE LOGS ---
st.subheader("📝 Recent Trade Activity Logs")
if st.session_state.trade_history:
    df_history = pd.DataFrame(st.session_state.trade_history)
    st.table(df_history)
else:
    st.write("No trades executed yet in this session.")