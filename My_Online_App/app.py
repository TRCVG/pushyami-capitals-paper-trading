import streamlit as st
from jugaad_data.nse import derivatives_df
from datetime import date
import pandas as pd
import plotly.graph_objects as go
import time
import io
from docx import Document
from docx.shared import Inches

# ==========================================
# PAGE CONFIGURATION & HEADER
# ==========================================
st.set_page_config(layout="wide", page_title="NSE Options Pro Terminal")
st.title("⚙️ Unified Options Backtester & Price Comparator")
st.write("Configure your strikes and trade setup below. A single click will generate both the Price Action chart and the P&L Backtest chart.")

# Track unique blocks instead of just a simple count
if 'blocks' not in st.session_state:
    st.session_state.blocks = [0] # Start with one block (ID 0)
    st.session_state.next_id = 1

# ==========================================
# ADD / REMOVE BUTTON LOGIC
# ==========================================
if st.button("➕ Add Another Series"):
    # If there is at least one block, copy its exact data to the new block!
    if len(st.session_state.blocks) > 0:
        last_id = st.session_state.blocks[-1]
        new_id = st.session_state.next_id
        
        # We tell Streamlit's memory to duplicate these specific fields
        keys_to_copy = ["sym", "opt", "strk", "exp", "from", "to", "lot", "act", "ref"]
        for key in keys_to_copy:
            old_key = f"{key}_{last_id}"
            new_key = f"{key}_{new_id}"
            if old_key in st.session_state:
                st.session_state[new_key] = st.session_state[old_key]
                
    st.session_state.blocks.append(st.session_state.next_id)
    st.session_state.next_id += 1
    st.rerun()

st.markdown("---")

# ==========================================
# 1. DYNAMIC CONFIGURATION BLOCKS
# ==========================================
all_series_inputs = []

# Loop through our specific block IDs
for block_idx, block_id in enumerate(st.session_state.blocks):
    
    # Header with the specific Delete button on the right
    h_col1, h_col2 = st.columns([11, 1])
    with h_col1:
        st.markdown(f"#### 🔍 Option Configuration Block #{block_idx+1}")
    with h_col2:
        if st.button("❌ Remove", key=f"del_btn_{block_id}"):
            st.session_state.blocks.remove(block_id)
            st.rerun()
            
    c1, c2, c3 = st.columns(3)
    
    with c1:
        symbol = st.selectbox("Symbol", ["NIFTY", "BANKNIFTY", "FINNIFTY"], key=f"sym_{block_id}")
        option_type = st.selectbox("Option Type", ["CE", "PE"], key=f"opt_{block_id}")
        default_strikes = "24000, 24100" if block_idx == 0 else "24200, 24300"
        strikes_input = st.text_input("Strike Prices (comma separated)", default_strikes, key=f"strk_{block_id}")
        
    with c2:
        expiry_dt = st.date_input("Expiry Date", date(2026, 7, 28), key=f"exp_{block_id}")
        from_dt = st.date_input("Entry Date (From Date)", date(2026, 7, 1), key=f"from_{block_id}")
        to_dt = st.date_input("Exit Date (To Date)", date(2026, 7, 16), key=f"to_{block_id}")
        
    with c3:
        default_lot = 25 if symbol == "NIFTY" else 15 if symbol == "BANKNIFTY" else 40
        lot_size = st.number_input(f"Lot Size", value=default_lot, step=5, key=f"lot_{block_id}")
        trade_direction = st.selectbox("Action", ["Buy (Long)", "Sell (Short)"], key=f"act_{block_id}")
        entry_price_ref = st.selectbox("Entry Trigger (Day 1)", ["OPEN", "HIGH", "LOW", "CLOSE"], index=3, key=f"ref_{block_id}")
        
    strike_list = [int(s.strip()) for s in strikes_input.split(",") if s.strip().isdigit()]
    
    all_series_inputs.append({
        "symbol": symbol,
        "option_type": option_type,
        "strikes": strike_list,
        "expiry_dt": expiry_dt,
        "from_dt": from_dt,
        "to_dt": to_dt,
        "lot_size": lot_size,
        "trade_direction": trade_direction,
        "entry_price_ref": entry_price_ref
    })
    st.markdown("---")

# ==========================================
# 2. GLOBAL SETTINGS (DISPLAY ONLY)
# ==========================================
st.markdown("### ⚙️ Global Price Display Preferences")
st.write("Select which price points to draw on the upper Premium Chart:")
col_o, col_h, col_l, col_c = st.columns(4)
with col_o: show_open = st.checkbox("Open (O)", value=False)
with col_h: show_high = st.checkbox("High (H)", value=False)
with col_l: show_low = st.checkbox("Low (L)", value=False)
with col_c: show_close = st.checkbox("Close (C)", value=True) 

st.markdown("---")

# ==========================================
# 3. MASTER FETCH & PROCESS BUTTON
# ==========================================
if st.button("🚀 Fetch, Compare & Backtest All", type="primary"):
    
    if len(all_series_inputs) == 0:
        st.error("You need at least one Option Configuration Block to run a backtest!")
        st.stop()
        
    if not (show_open or show_high or show_low or show_close):
        st.error("Please select at least one price checkbox (Open, High, Low, or Close) to draw the price chart!")
        st.stop()
        
    fig_price = go.Figure()
    fig_pnl_individual = go.Figure()
    data_found = False
    all_combined_dfs = []
    max_retries = 3
    
    with st.spinner("Fetching market data, building charts, and calculating complex P&L..."):
        for block_idx, config in enumerate(all_series_inputs):
            for strike in config["strikes"]:
                label_base = f"Block #{block_idx+1} | {config['symbol']} {strike} {config['option_type']}"
                action_short = "Buy" if config["trade_direction"] == "Buy (Long)" else "Sell"
                
                for attempt in range(max_retries):
                    try:
                        df = derivatives_df(
                            symbol=config["symbol"],
                            from_date=config["from_dt"],
                            to_date=config["to_dt"],
                            expiry_date=config["expiry_dt"],
                            instrument_type="OPTIDX",
                            strike_price=strike,
                            option_type=config["option_type"]
                        )
                        
                        if not df.empty:
                            df['DATE'] = pd.to_datetime(df['DATE'])
                            df = df.sort_values('DATE').reset_index(drop=True)
                            
                            if show_open: fig_price.add_trace(go.Scatter(x=df['DATE'], y=df['OPEN'], mode='lines+markers', name=f"{label_base} (O)", line=dict(dash='dot')))
                            if show_high: fig_price.add_trace(go.Scatter(x=df['DATE'], y=df['HIGH'], mode='lines+markers', name=f"{label_base} (H)", line=dict(dash='dash')))
                            if show_low: fig_price.add_trace(go.Scatter(x=df['DATE'], y=df['LOW'], mode='lines+markers', name=f"{label_base} (L)", line=dict(dash='dashdot')))
                            if show_close: fig_price.add_trace(go.Scatter(x=df['DATE'], y=df['CLOSE'], mode='lines+markers', name=f"{label_base} (C)", line=dict(dash='solid')))
                            
                            entry_price = df[config["entry_price_ref"]].iloc[0]
                            
                            if config["trade_direction"] == "Buy (Long)":
                                df['P&L (Points)'] = df['CLOSE'] - entry_price
                            else:
                                df['P&L (Points)'] = entry_price - df['CLOSE']
                                
                            df['TOTAL P&L (₹)'] = df['P&L (Points)'] * config["lot_size"]
                            
                            fig_pnl_individual.add_trace(go.Scatter(
                                x=df['DATE'], 
                                y=df['TOTAL P&L (₹)'], 
                                mode='lines+markers', 
                                name=f"{label_base} ({action_short} P&L)"
                            ))
                            
                            df['SERIES_SOURCE'] = f"Block #{block_idx+1}"
                            df['STRIKE'] = strike
                            df['SYMBOL'] = config["symbol"]
                            df['OPTION_TYPE'] = config["option_type"]
                            df['TRADE_ACTION'] = action_short
                            df['ENTRY_PRICE_REF'] = entry_price
                            
                            all_combined_dfs.append(df[['DATE', 'SERIES_SOURCE', 'SYMBOL', 'STRIKE', 'OPTION_TYPE', 'TRADE_ACTION', 'OPEN', 'HIGH', 'LOW', 'CLOSE', 'P&L (Points)', 'TOTAL P&L (₹)']])
                            
                            data_found = True
                            break
                        else:
                            break
                            
                    except Exception as e:
                        if attempt < max_retries - 1:
                            time.sleep(1.5)
                        else:
                            st.warning(f"Failed to fetch data for {label_base}. Exact Error: {e}")
                
                time.sleep(1) 
                
    # ==========================================
    # 4. OUTPUT RENDERING (THREE CHARTS)
    # ==========================================
    if data_found:
        st.success("Successfully generated Analysis & Backtest!")
        
        master_df = pd.concat(all_combined_dfs, ignore_index=True)
        
        st.markdown("### 📈 1. Multi-Series Premium Price Chart")
        fig_price.update_layout(title="<b>Historical Premium Movement</b>", xaxis_title="Trading Date", yaxis_title="Premium Price (₹)", yaxis=dict(side="right"), hovermode="x unified", template="plotly_white", legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1))
        st.plotly_chart(fig_price, use_container_width=True)
        
        st.markdown("### 📊 2. Individual Strategy Profit & Loss (P&L)")
        fig_pnl_individual.add_hline(y=0, line_dash="dash", line_color="black", annotation_text="Breakeven (₹0)")
        fig_pnl_individual.update_layout(title="<b>Cumulative P&L (Broken Down by Strategy)</b>", xaxis_title="Trading Date", yaxis_title="Net P&L (₹)", yaxis=dict(side="right"), hovermode="x unified", template="plotly_white", legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1))
        st.plotly_chart(fig_pnl_individual, use_container_width=True)
        
        st.markdown("### 🏦 3. Net Portfolio Profit & Loss (Combined Total)")
        
        portfolio_pnl = master_df.groupby('DATE')['TOTAL P&L (₹)'].sum().reset_index()
        pnl_colors = ['green' if val >= 0 else 'red' for val in portfolio_pnl['TOTAL P&L (₹)']]
        
        fig_portfolio = go.Figure()
        fig_portfolio.add_trace(go.Scatter(
            x=portfolio_pnl['DATE'], 
            y=portfolio_pnl['TOTAL P&L (₹)'], 
            mode='lines+markers', 
            name="NET PORTFOLIO P&L",
            line=dict(color='gray', width=2),
            marker=dict(color=pnl_colors, size=8, line=dict(width=1, color='white')),
            fill='tozeroy', 
            fillcolor='rgba(128, 128, 128, 0.1)'
        ))
        
        fig_portfolio.add_hline(y=0, line_dash="solid", line_width=2, line_color="black", annotation_text="Breakeven (₹0)")
        fig_portfolio.update_layout(title="<b>Total Net Portfolio P&L (All Blocks Combined)</b>", xaxis_title="Trading Date", yaxis_title="Total Net P&L (₹)", yaxis=dict(side="right"), hovermode="x unified", template="plotly_white", showlegend=False)
        st.plotly_chart(fig_portfolio, use_container_width=True)
        
        with st.expander("View Consolidated Daily Ledger & Trade Data"):
            st.dataframe(master_df.sort_values(by=['DATE', 'SERIES_SOURCE', 'STRIKE'], ascending=[False, True, True]))

        # ==========================================
        # 5. UNIFIED EXPORT HUB (WORD DOC & CSV)
        # ==========================================
        st.markdown("---")
        st.markdown("### 💾 Export Analysis & Reports")
        st.write("Download your complete options ledger or export an instant Microsoft Word document containing all your charts, perfect for writing articles.")

        dl_col1, dl_col2 = st.columns(2)

        with dl_col1:
            csv_data = master_df.to_csv(index=False).encode('utf-8')
            st.download_button(
                label="📊 Download Raw Data Ledger (CSV)",
                data=csv_data,
                file_name=f"options_portfolio_ledger_{date.today()}.csv",
                mime="text/csv",
                use_container_width=True
            )

        with dl_col2:
            try:
                with st.spinner("Generating Microsoft Word Document..."):
                    # 1. Create a new blank Word Document
                    doc = Document()
                    doc.add_heading(f'Options Strategy Executive Report - {date.today()}', 0)
                    doc.add_paragraph("This report contains the automated backtest results and historical premium charts for the configured options portfolio.")

                    # 2. Convert Plotly Charts to Image Bytes
                    img1_bytes = fig_price.to_image(format="png", width=900, height=500, scale=1.5)
                    img2_bytes = fig_pnl_individual.to_image(format="png", width=900, height=500, scale=1.5)
                    img3_bytes = fig_portfolio.to_image(format="png", width=900, height=500, scale=1.5)

                    # 3. Paste Chart 1 into Word
                    doc.add_heading('1. Multi-Series Premium Price Chart', level=1)
                    doc.add_picture(io.BytesIO(img1_bytes), width=Inches(6.5))
                    doc.add_paragraph("Analysis of historical option premium trends across all configured strikes.")
                    
                    doc.add_page_break()

                    # 4. Paste Chart 2 into Word
                    doc.add_heading('2. Individual Strategy Profit & Loss (P&L)', level=1)
                    doc.add_picture(io.BytesIO(img2_bytes), width=Inches(6.5))
                    doc.add_paragraph("Breakdown of independent Profit and Loss for each configured option strategy.")

                    # 5. Paste Chart 3 into Word
                    doc.add_heading('3. Net Portfolio Profit & Loss (Combined)', level=1)
                    doc.add_picture(io.BytesIO(img3_bytes), width=Inches(6.5))
                    doc.add_paragraph("Total cumulative Profit and Loss for the combined portfolio strategy.")

                    # 6. Save the Word Doc to memory
                    doc_buffer = io.BytesIO()
                    doc.save(doc_buffer)
                    doc_bytes = doc_buffer.getvalue()
                    
                    # 7. Create the Download Button
                    st.download_button(
                        label="📝 Download as Microsoft Word (.docx)",
                        data=doc_bytes,
                        file_name=f"Options_Article_Draft_{date.today()}.docx",
                        mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                        use_container_width=True
                    )
            except ValueError as e:
                if "kaleido" in str(e).lower():
                    st.error("⚠️ **Missing Package:** To download photos/Word docs, you must stop the app, run `py -m pip install kaleido` in your terminal, and restart.")
                else:
                    st.error(f"Failed to generate Word document: {e}")
            
    else:
        st.error("Could not find data matches for any of the configured options. Check that your dates match valid trading days.")