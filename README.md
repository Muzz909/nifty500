# 📈 Nifty 500 Breakout Scanner

A Streamlit dashboard that scans all Nifty 500 stocks and surfaces **high-quality breakouts** in real time — combining price resistance breaks, volume confirmation, and trend alignment.

---

## ✨ Features

| Feature | Detail |
|---|---|
| **Breakout Logic** | Price > 20-day resistance + Volume ≥ 1.5× avg + Close > MA50 |
| **Quality Scoring** | 0–10 score with bonus points for MA200 trend, 3× volume, ATH proximity |
| **Auto-Refresh** | Every 30 minutes, only during market hours (Mon–Fri, 9:15–15:30 IST) |
| **Manual Refresh** | One-click refresh from the sidebar |
| **Toggle** | Enable/disable auto-refresh from UI |
| **Filters** | Slide to filter by minimum score and volume surge |
| **Export** | Download filtered results as CSV |
| **Detail View** | Expandable per-stock panel with all indicators |

---

## 🗂️ Project Structure

```
nifty_breakout_scanner/
├── app.py               ← Streamlit dashboard (UI + refresh logic)
├── scanner.py           ← Core breakout engine (pure Python, no Streamlit)
├── nifty500_symbols.py  ← Nifty 500 ticker list (.NS suffix)
├── requirements.txt
└── README.md
```

---

## 🚀 Local Setup

```bash
# 1. Clone your repo
git clone https://github.com/<your-username>/nifty-breakout-scanner.git
cd nifty-breakout-scanner

# 2. Create a virtual environment (recommended)
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Run the app
streamlit run app.py
```

The app opens at `http://localhost:8501`.

---

## ☁️ Deploy to Streamlit Cloud (Free)

1. Push this folder to a **public GitHub repository**
2. Go to [share.streamlit.io](https://share.streamlit.io)
3. Click **New app** → connect your repo → set **Main file path** to `app.py`
4. Click **Deploy** — done!

> Streamlit Cloud runs the app 24/7. During market hours it auto-refreshes via the meta-refresh tag. Outside market hours, auto-refresh is paused automatically.

---

## 🔧 Customising Thresholds

All parameters live in `scanner.py` inside the `Config` class — no logic changes needed:

```python
class Config:
    RESISTANCE_WINDOW = 20     # Days for resistance high (default: 20-day)
    VOLUME_WINDOW     = 10     # Baseline volume window
    VOLUME_SURGE_MIN  = 1.5    # Minimum vol multiplier (1.5× = 50% above avg)
    TREND_MA          = 50     # MA period for trend filter (MA50)
    DATA_PERIOD       = "6mo"  # yfinance download period
```

---

## 📊 Scoring System

| Condition | Points |
|---|---|
| Passes all 3 core conditions | +3 (base) |
| Volume ≥ 2× average | +2 |
| Volume ≥ 3× average | +3 (replaces above) |
| Close > MA200 | +2 |
| Close ≥ 97% of 52-week high | +2 |
| **Max possible** | **10** |

**Labels:** 🔥 Exceptional (≥9) · ⚡ Strong (≥7) · ✅ Good (≥5) · 📌 Moderate (<5)

---

## 🧩 Extending the Scanner

The scanner is designed for easy extension:

### Add a new filter (e.g., RSI < 70)
```python
# In scanner.py → compute_indicators()
df["RSI"] = compute_rsi(df["Close"])  # plug in your RSI function

# In analyse_stock()
rsi_ok = float(latest["RSI"]) < 70
if not (price_breakout and volume_confirmed and trend_aligned and rsi_ok):
    return None
```

### Add a new score bonus
```python
# In score_breakout()
if float(latest["RSI"]) < 60:   # not yet overbought
    score += 1
```

### Add alerts (e.g., Telegram)
```python
# In app.py, after trigger_scan():
for r in st.session_state.results:
    if r.score >= 8:
        send_telegram_alert(r)  # your function
```

---

## ⚠️ Disclaimer

This tool is for **educational and research purposes only**.  
It is **not financial advice**. Always do your own due diligence before trading.
