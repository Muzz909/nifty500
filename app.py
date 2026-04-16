# app.py
"""
Nifty 500 Breakout Scanner — Streamlit Dashboard
-------------------------------------------------
Features:
  • Auto-refresh every 30 min during market hours (9:15–15:30 IST)
  • Toggle to enable / disable auto-refresh
  • Manual "Refresh Now" button
  • Score-based filtering & sorting
  • Download results as CSV
"""

import streamlit as st
import pandas as pd
from datetime import datetime
import pytz
import time

from scanner import run_scan, BreakoutResult
from nifty500_symbols import NIFTY500_SYMBOLS

# ─────────────────────────────────────────────
# PAGE CONFIG
# ─────────────────────────────────────────────
st.set_page_config(
    page_title="Nifty 500 Breakout Scanner",
    page_icon="📈",
    layout="wide",
)

# ─────────────────────────────────────────────
# MARKET HOURS HELPER  (IST)
# ─────────────────────────────────────────────
IST = pytz.timezone("Asia/Kolkata")
MARKET_OPEN  = (9, 15)   # 09:15 IST
MARKET_CLOSE = (15, 30)  # 15:30 IST
REFRESH_INTERVAL_SECONDS = 30 * 60   # 30 minutes


def now_ist() -> datetime:
    return datetime.now(IST)


def is_market_open() -> bool:
    now = now_ist()
    if now.weekday() >= 5:          # Saturday / Sunday
        return False
    t = (now.hour, now.minute)
    return MARKET_OPEN <= t <= MARKET_CLOSE


def market_status_text() -> str:
    now = now_ist()
    if now.weekday() >= 5:
        return "🔴 Market Closed (Weekend)"
    if is_market_open():
        return "🟢 Market Open"
    return "🔴 Market Closed"


# ─────────────────────────────────────────────
# SESSION STATE DEFAULTS
# ─────────────────────────────────────────────
if "results" not in st.session_state:
    st.session_state.results: list[BreakoutResult] = []
if "last_scanned" not in st.session_state:
    st.session_state.last_scanned: str = "—"
if "auto_refresh" not in st.session_state:
    st.session_state.auto_refresh: bool = True
if "scan_running" not in st.session_state:
    st.session_state.scan_running: bool = False


# ─────────────────────────────────────────────
# SCAN FUNCTION (with Streamlit progress)
# ─────────────────────────────────────────────
def trigger_scan():
    st.session_state.scan_running = True
    total = len(NIFTY500_SYMBOLS)

    progress_bar = st.progress(0, text="Starting scan…")
    status_text  = st.empty()

    def on_progress(done, total, sym):
        pct = done / total
        progress_bar.progress(pct, text=f"Scanning {sym.replace('.NS','')}…  ({done}/{total})")

    results = run_scan(symbols=NIFTY500_SYMBOLS, progress_callback=on_progress)

    progress_bar.empty()
    status_text.empty()

    st.session_state.results = results
    st.session_state.last_scanned = now_ist().strftime("%d %b %Y, %I:%M %p IST")
    st.session_state.scan_running = False


# ─────────────────────────────────────────────
# HEADER
# ─────────────────────────────────────────────
st.title("📈 Nifty 500 Breakout Scanner")
st.caption("Identifies high-quality intraday breakouts: resistance break + volume surge + trend alignment")

# ─────────────────────────────────────────────
# SIDEBAR — Controls
# ─────────────────────────────────────────────
with st.sidebar:
    st.header("⚙️ Controls")

    # Market status badge
    st.markdown(f"**Status:** {market_status_text()}")
    st.markdown(f"**Time (IST):** {now_ist().strftime('%d %b %Y, %I:%M %p')}")
    st.divider()

    # Auto-refresh toggle
    auto_refresh_enabled = st.toggle(
        "Auto-Refresh (every 30 min)",
        value=st.session_state.auto_refresh,
        help="Auto-refresh only works during market hours (Mon–Fri, 9:15–15:30 IST)",
    )
    st.session_state.auto_refresh = auto_refresh_enabled

    if auto_refresh_enabled:
        if is_market_open():
            st.success("Auto-refresh ON  ✅  (market is open)")
        else:
            st.warning("Auto-refresh is ON but **market is closed** — refresh paused until market opens.")

    st.divider()

    # Manual refresh
    if st.button("🔄 Refresh Now", use_container_width=True, type="primary"):
        trigger_scan()
        st.rerun()

    st.divider()

    # Filters
    st.subheader("🔧 Filters")
    min_score = st.slider("Minimum Score", min_value=3, max_value=10, value=3)
    min_vol_surge = st.slider("Min Volume Surge (×)", min_value=1.5, max_value=5.0, value=1.5, step=0.1)

    st.divider()
    st.markdown(
        "**Scoring guide**\n"
        "- 🔥 Exceptional: ≥ 9\n"
        "- ⚡ Strong: ≥ 7\n"
        "- ✅ Good: ≥ 5\n"
        "- 📌 Moderate: < 5"
    )
    st.divider()
    st.caption("Data via Yahoo Finance · Not financial advice")


# ─────────────────────────────────────────────
# AUTO-REFRESH TRIGGER LOGIC
# ─────────────────────────────────────────────
should_auto_refresh = (
    st.session_state.auto_refresh
    and is_market_open()
    and not st.session_state.scan_running
)

# Kick off first scan if we have no results yet
if not st.session_state.results and not st.session_state.scan_running:
    trigger_scan()
    st.rerun()

# Auto-refresh: inject a meta-refresh tag during market hours
if should_auto_refresh:
    st.markdown(
        f"""
        <meta http-equiv="refresh" content="{REFRESH_INTERVAL_SECONDS}">
        """,
        unsafe_allow_html=True,
    )


# ─────────────────────────────────────────────
# METRICS ROW
# ─────────────────────────────────────────────
results = st.session_state.results

# Apply filters
filtered = [
    r for r in results
    if r.score >= min_score and r.volume_surge >= min_vol_surge
]

col1, col2, col3, col4 = st.columns(4)
col1.metric("Total Breakouts", len(results))
col2.metric("After Filters", len(filtered))
col3.metric("Last Scanned", st.session_state.last_scanned)
col4.metric(
    "Next Auto-Refresh",
    "30 min" if (should_auto_refresh) else "—  (paused)",
)

st.divider()


# ─────────────────────────────────────────────
# RESULTS TABLE
# ─────────────────────────────────────────────
if not filtered:
    if results:
        st.info("No stocks match the current filters. Try lowering the Score or Volume Surge slider.")
    else:
        st.info("Click **Refresh Now** in the sidebar to run the first scan.")
else:
    st.subheader(f"🔥 {len(filtered)} Breakout Stock(s) Found")

    # Build display DataFrame
    rows = []
    for r in filtered:
        rows.append({
            "Symbol":          r.symbol,
            "Score":           r.score,
            "Label":           r.score_label,
            "Close (₹)":       round(r.close, 2),
            "Breakout %":      r.breakout_pct_str,
            "Resistance (₹)":  round(r.resistance_level, 2),
            "Volume":          f"{r.volume:,}",
            "Avg Volume":      f"{r.avg_volume:,}",
            "Vol Surge":       r.volume_surge_str,
            "MA50 (₹)":        round(r.ma50, 2),
            "MA200 (₹)":       round(r.ma200, 2) if r.ma200 else "—",
            "52W High (₹)":    round(r.week52_high, 2) if r.week52_high else "—",
        })

    df_display = pd.DataFrame(rows)

    # Colour the score column
    def score_color(val):
        if val >= 9:
            return "background-color: #1a472a; color: #90ee90"
        elif val >= 7:
            return "background-color: #1a3a5c; color: #87ceeb"
        elif val >= 5:
            return "background-color: #2e2e0a; color: #f0e68c"
        return ""

    styled = df_display.style.applymap(score_color, subset=["Score"])
    st.dataframe(styled, use_container_width=True, hide_index=True)

    # ── DOWNLOAD BUTTON ──────────────────────
    csv_data = df_display.to_csv(index=False).encode("utf-8")
    st.download_button(
        label="⬇️ Download as CSV",
        data=csv_data,
        file_name=f"breakouts_{now_ist().strftime('%Y%m%d_%H%M')}.csv",
        mime="text/csv",
    )

    # ── DETAIL EXPANDERS ─────────────────────
    st.divider()
    st.subheader("📋 Stock Details")
    for r in filtered:
        with st.expander(f"{r.symbol}  —  Score {r.score}  {r.score_label}"):
            c1, c2, c3 = st.columns(3)
            c1.metric("Close", f"₹{r.close:.2f}")
            c1.metric("Breakout Level", f"₹{r.resistance_level:.2f}")
            c1.metric("Breakout %", r.breakout_pct_str)
            c2.metric("Volume", f"{r.volume:,}")
            c2.metric("Avg Volume (10d)", f"{r.avg_volume:,}")
            c2.metric("Volume Surge", r.volume_surge_str)
            c3.metric("MA50", f"₹{r.ma50:.2f}")
            c3.metric("MA200", f"₹{r.ma200:.2f}" if r.ma200 else "N/A")
            c3.metric("52W High", f"₹{r.week52_high:.2f}" if r.week52_high else "N/A")
