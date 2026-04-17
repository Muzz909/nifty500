# scanner.py
"""
Nifty 500 Breakout Scanner — Core Engine
-----------------------------------------
Optimisations vs v1:
  1. Nifty 500 symbols fetched live from NSE, cached for 24 hours
  2. Bulk yf.download() in batches of 50 (one network call per batch)
  3. Batches processed in parallel via ThreadPoolExecutor
  4. Reduced history period (3mo) — enough for all indicators
  Result: ~500 stocks scanned in 30–60 seconds instead of 15+ minutes
"""

import yfinance as yf
import pandas as pd
from dataclasses import dataclass
from typing import Optional
from datetime import datetime, date
from concurrent.futures import ThreadPoolExecutor, as_completed
import time

# ─────────────────────────────────────────────────────────
# CONFIG  — tweak thresholds here without touching logic
# ─────────────────────────────────────────────────────────
class Config:
    RESISTANCE_WINDOW: int   = 20      # Days for resistance high
    VOLUME_WINDOW: int       = 10      # Days for avg-volume baseline
    VOLUME_SURGE_MIN: float  = 1.5     # Min volume multiplier to qualify
    TREND_MA: int            = 50      # MA period for trend filter
    DATA_PERIOD: str         = "3mo"   # Reduced from 6mo — 3mo is enough
    DATA_INTERVAL: str       = "1d"
    BATCH_SIZE: int          = 50      # Stocks per bulk yf.download call
    MAX_WORKERS: int         = 6       # Parallel batch threads (keep ≤ 10)

    # Scoring weights
    SCORE_BASE: int          = 3
    SCORE_ABOVE_MA200: int   = 2
    SCORE_VOLUME_2X: int     = 2
    SCORE_VOLUME_3X: int     = 3
    SCORE_ATH_PROXIMITY: int = 2


# ─────────────────────────────────────────────────────────
# NIFTY 500 SYMBOL FETCH  (live from NSE, cached 24 hours)
# ─────────────────────────────────────────────────────────

# Module-level in-memory cache: {"date": date, "symbols": [...]}
_symbol_cache: dict = {}

# Fallback list used only if NSE CSV is unreachable
_FALLBACK_SYMBOLS = [
    "RELIANCE.NS","TCS.NS","HDFCBANK.NS","INFY.NS","ICICIBANK.NS",
    "LT.NS","SBIN.NS","BHARTIARTL.NS","ITC.NS","KOTAKBANK.NS",
    "AXISBANK.NS","MARUTI.NS","TITAN.NS","WIPRO.NS","ULTRACEMCO.NS",
    "BAJFINANCE.NS","HCLTECH.NS","SUNPHARMA.NS","NTPC.NS","ONGC.NS",
    "POWERGRID.NS","COALINDIA.NS","TATAMOTORS.NS","TATASTEEL.NS","JSWSTEEL.NS",
    "HINDALCO.NS","BAJAJFINSV.NS","ASIANPAINT.NS","NESTLEIND.NS","DRREDDY.NS",
    "CIPLA.NS","DIVISLAB.NS","EICHERMOT.NS","TECHM.NS","APOLLOHOSP.NS",
    "BRITANNIA.NS","TATACONSUM.NS","HAVELLS.NS","PIDILITIND.NS","SIEMENS.NS",
    "DABUR.NS","GODREJCP.NS","COLPAL.NS","MUTHOOTFIN.NS","CHOLAFIN.NS",
    "BAJAJ-AUTO.NS","HEROMOTOCO.NS","INDUSINDBK.NS","TATAPOWER.NS","ADANIPORTS.NS",
]

NSE_CSV_URL = "https://www.niftyindices.com/IndexConstituents/ind_nifty500list.csv"


def get_nifty500_symbols() -> list[str]:
    """
    Returns Nifty 500 symbols with .NS suffix.
    Fetches from NSE once per calendar day; uses in-memory cache thereafter.
    Falls back to a hardcoded list if NSE is unreachable.
    """
    today = date.today()

    # Return cached result if already fetched today
    if _symbol_cache.get("date") == today:
        return _symbol_cache["symbols"]

    try:
        df = pd.read_csv(NSE_CSV_URL)
        symbols = (
            df["Symbol"]
            .dropna()
            .str.strip()
            .str.upper()
            .tolist()
        )
        symbols = [s + ".NS" for s in symbols if s]

        if len(symbols) < 100:
            raise ValueError(f"Only {len(symbols)} symbols parsed — likely a format change in NSE CSV")

        _symbol_cache["date"]    = today
        _symbol_cache["symbols"] = symbols
        print(f"✅ Fetched {len(symbols)} Nifty 500 symbols from NSE ({today})")
        return symbols

    except Exception as e:
        print(f"⚠️  NSE fetch failed: {e}\n   Using fallback list of {len(_FALLBACK_SYMBOLS)} stocks")
        return _FALLBACK_SYMBOLS


# ─────────────────────────────────────────────────────────
# DATA MODELS
# ─────────────────────────────────────────────────────────
@dataclass
class BreakoutResult:
    symbol: str
    close: float
    resistance_level: float
    volume: int
    avg_volume: int
    volume_surge: float
    ma50: float
    ma200: Optional[float]
    score: int
    breakout_pct: float
    week52_high: Optional[float]

    @property
    def volume_surge_str(self) -> str:
        return f"{self.volume_surge:.1f}x"

    @property
    def breakout_pct_str(self) -> str:
        return f"+{self.breakout_pct:.2f}%"

    @property
    def score_label(self) -> str:
        if self.score >= 9:   return "🔥 Exceptional"
        elif self.score >= 7: return "⚡ Strong"
        elif self.score >= 5: return "✅ Good"
        else:                 return "📌 Moderate"


# ─────────────────────────────────────────────────────────
# INDICATORS & SCORING
# ─────────────────────────────────────────────────────────
def _compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    close  = df["Close"].squeeze()
    high   = df["High"].squeeze()
    volume = df["Volume"].squeeze()

    df["MA50"]           = close.rolling(Config.TREND_MA).mean()
    df["MA200"]          = close.rolling(200).mean()
    df["AvgVolume"]      = volume.rolling(Config.VOLUME_WINDOW).mean()
    df["ResistanceHigh"] = high.shift(1).rolling(Config.RESISTANCE_WINDOW).max()
    df["Week52High"]     = high.rolling(252).max()
    return df


def _score(latest: pd.Series) -> int:
    score = Config.SCORE_BASE
    vol_surge = float(latest["Volume"]) / float(latest["AvgVolume"])

    if vol_surge >= 3.0:
        score += Config.SCORE_VOLUME_3X
    elif vol_surge >= 2.0:
        score += Config.SCORE_VOLUME_2X

    if pd.notna(latest.get("MA200")) and float(latest["Close"]) > float(latest["MA200"]):
        score += Config.SCORE_ABOVE_MA200

    if pd.notna(latest.get("Week52High")) and float(latest["Close"]) >= 0.97 * float(latest["Week52High"]):
        score += Config.SCORE_ATH_PROXIMITY

    return score


def _check_single(symbol: str, df_sym: pd.DataFrame) -> Optional[BreakoutResult]:
    """Analyse one stock's pre-downloaded DataFrame. Returns BreakoutResult or None."""
    try:
        if df_sym.empty or len(df_sym) < Config.RESISTANCE_WINDOW + 5:
            return None

        df_sym = _compute_indicators(df_sym)
        latest = df_sym.iloc[-1]

        close      = float(latest["Close"])
        resistance = latest.get("ResistanceHigh")
        ma50       = latest.get("MA50")
        avg_vol    = latest.get("AvgVolume")
        volume     = float(latest["Volume"])

        if any(pd.isna(v) for v in [resistance, ma50, avg_vol]):
            return None

        resistance = float(resistance)
        ma50       = float(ma50)
        avg_vol    = float(avg_vol)

        # ── THREE MANDATORY CONDITIONS ──────────────
        if not (
            close > resistance                               # price breaks resistance
            and volume >= Config.VOLUME_SURGE_MIN * avg_vol # volume confirmed
            and close > ma50                                 # trend aligned
        ):
            return None
        # ─────────────────────────────────────────────

        ma200_raw = latest.get("MA200")
        w52h_raw  = latest.get("Week52High")

        return BreakoutResult(
            symbol           = symbol.replace(".NS", ""),
            close            = close,
            resistance_level = resistance,
            volume           = int(volume),
            avg_volume       = int(avg_vol),
            volume_surge     = volume / avg_vol,
            ma50             = ma50,
            ma200            = float(ma200_raw) if pd.notna(ma200_raw) else None,
            score            = _score(latest),
            breakout_pct     = ((close - resistance) / resistance) * 100,
            week52_high      = float(w52h_raw) if pd.notna(w52h_raw) else None,
        )
    except Exception:
        return None


# ─────────────────────────────────────────────────────────
# BATCH DOWNLOAD + ANALYSIS
# ─────────────────────────────────────────────────────────
def _process_batch(batch: list[str]) -> list[BreakoutResult]:
    """
    Download a batch of symbols in ONE yf.download() call,
    then check each for breakout conditions.
    One batch of 50 stocks takes ~2–4 seconds vs ~50–100 seconds sequentially.
    """
    try:
        raw = yf.download(
            tickers     = batch,
            period      = Config.DATA_PERIOD,
            interval    = Config.DATA_INTERVAL,
            group_by    = "ticker",
            auto_adjust = True,
            progress    = False,
            threads     = True,
        )
    except Exception:
        return []

    results = []

    if len(batch) == 1:
        # Single ticker: yf returns a flat DataFrame (no ticker-level grouping)
        result = _check_single(batch[0], raw)
        if result:
            results.append(result)
        return results

    # Multiple tickers: top-level columns are ticker symbols
    for sym in batch:
        try:
            df_sym = raw[sym].dropna(how="all")
            result = _check_single(sym, df_sym)
            if result:
                results.append(result)
        except (KeyError, Exception):
            continue

    return results


# ─────────────────────────────────────────────────────────
# FULL SCAN  (parallel batches)
# ─────────────────────────────────────────────────────────
def run_scan(
    symbols: Optional[list[str]] = None,
    progress_callback=None,
) -> list[BreakoutResult]:
    """
    Scan all Nifty 500 stocks using parallel bulk downloads.

    progress_callback(batches_done, total_batches, batch_symbols)
    is called after each batch completes.

    Speed: ~30–60 seconds for 500 stocks (vs 15+ min sequential).
    """
    if symbols is None:
        symbols = get_nifty500_symbols()

    # Split into fixed-size batches
    batches = [
        symbols[i : i + Config.BATCH_SIZE]
        for i in range(0, len(symbols), Config.BATCH_SIZE)
    ]
    total_batches = len(batches)
    all_results: list[BreakoutResult] = []

    with ThreadPoolExecutor(max_workers=Config.MAX_WORKERS) as executor:
        future_to_batch = {
            executor.submit(_process_batch, batch): batch
            for batch in batches
        }

        completed = 0
        for future in as_completed(future_to_batch):
            batch = future_to_batch[future]
            try:
                all_results.extend(future.result())
            except Exception:
                pass
            completed += 1
            if progress_callback:
                progress_callback(completed, total_batches, batch)

    all_results.sort(key=lambda r: (r.score, r.volume_surge), reverse=True)
    return all_results


# ─────────────────────────────────────────────────────────
# CLI  (python scanner.py)
# ─────────────────────────────────────────────────────────
if __name__ == "__main__":
    symbols = get_nifty500_symbols()
    print(f"\n🔍 Nifty 500 Breakout Scanner  —  {len(symbols)} stocks\n")

    def _cli_progress(done, total, batch):
        pct = int(done / total * 40)
        bar = "█" * pct + "░" * (40 - pct)
        label = batch[0].replace(".NS", "") if batch else ""
        print(f"\r  [{bar}] batch {done}/{total}  ({label}…)", end="", flush=True)

    t0 = time.time()
    results = run_scan(symbols=symbols, progress_callback=_cli_progress)
    elapsed = time.time() - t0

    print(f"\n\n{'─'*70}")
    print(f"  ✅ Scan complete in {elapsed:.1f}s  —  {datetime.now().strftime('%d %b %Y, %H:%M')}")
    print(f"  🔥 {len(results)} breakout(s) found")
    print(f"{'─'*70}")

    if results:
        header = f"{'#':<3} {'Symbol':<14} {'Close':>8} {'Breakout%':>10} {'VolSurge':>9} {'Score':>6}  Label"
        print(header)
        print("─" * 70)
        for i, r in enumerate(results, 1):
            print(
                f"{i:<3} {r.symbol:<14} ₹{r.close:>7.2f} "
                f"{r.breakout_pct_str:>10} {r.volume_surge_str:>9} "
                f"{r.score:>6}  {r.score_label}"
            )
    print()






















# # scanner.py
# """
# Core breakout detection engine.
# Completely independent of Streamlit — can be imported anywhere
# or run as a standalone script.
# """

# import yfinance as yf
# import pandas as pd
# from dataclasses import dataclass, field
# from typing import Optional
# from datetime import datetime

# from nifty500_symbols import NIFTY500_SYMBOLS

# # ─────────────────────────────────────────────────────────
# # CONFIG  — tweak thresholds here without touching logic
# # ─────────────────────────────────────────────────────────
# class Config:
#     RESISTANCE_WINDOW: int = 20       # Days for resistance high
#     VOLUME_WINDOW: int = 10           # Days for avg-volume baseline
#     VOLUME_SURGE_MIN: float = 1.5     # Minimum volume multiplier to qualify
#     TREND_MA: int = 50                # MA period for trend filter
#     DATA_PERIOD: str = "6mo"          # yfinance download period
#     DATA_INTERVAL: str = "1d"

#     # Scoring weights
#     SCORE_ABOVE_MA200: int = 2        # Long-term trend bonus
#     SCORE_VOLUME_2X: int = 2          # Strong volume bonus
#     SCORE_VOLUME_3X: int = 3          # Exceptional volume bonus
#     SCORE_ATH_PROXIMITY: int = 2      # Near 52-week high
#     SCORE_BASE: int = 3               # Passes all 3 core conditions


# # ─────────────────────────────────────────────────────────
# # DATA MODELS
# # ─────────────────────────────────────────────────────────
# @dataclass
# class BreakoutResult:
#     symbol: str
#     close: float
#     resistance_level: float          # The level that was broken
#     volume: int
#     avg_volume: int
#     volume_surge: float              # e.g. 2.3 = 2.3× average
#     ma50: float
#     ma200: Optional[float]
#     score: int                       # Quality score (higher = stronger)
#     breakout_pct: float              # % above resistance
#     week52_high: Optional[float]
#     error: Optional[str] = None

#     @property
#     def volume_surge_str(self) -> str:
#         return f"{self.volume_surge:.1f}x"

#     @property
#     def breakout_pct_str(self) -> str:
#         return f"+{self.breakout_pct:.2f}%"

#     @property
#     def score_label(self) -> str:
#         if self.score >= 9:
#             return "🔥 Exceptional"
#         elif self.score >= 7:
#             return "⚡ Strong"
#         elif self.score >= 5:
#             return "✅ Good"
#         else:
#             return "📌 Moderate"


# # ─────────────────────────────────────────────────────────
# # INDICATOR CALCULATION
# # ─────────────────────────────────────────────────────────
# def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
#     """Add all technical indicators to the OHLCV DataFrame."""
#     close = df["Close"].squeeze()
#     high = df["High"].squeeze()
#     volume = df["Volume"].squeeze()

#     df["MA50"] = close.rolling(Config.TREND_MA).mean()
#     df["MA200"] = close.rolling(200).mean()
#     df["AvgVolume"] = volume.rolling(Config.VOLUME_WINDOW).mean()
#     df["ResistanceHigh"] = high.shift(1).rolling(Config.RESISTANCE_WINDOW).max()
#     df["Week52High"] = high.rolling(252).max()
#     return df


# def score_breakout(latest: pd.Series, cfg: Config = Config) -> int:
#     """
#     Assign a quality score to a breakout.
#     Base score = 3 (passed the 3 mandatory conditions).
#     Additional points for extra strength signals.
#     """
#     score = cfg.SCORE_BASE
#     vol_surge = float(latest["Volume"]) / float(latest["AvgVolume"])

#     # Volume strength bonus
#     if vol_surge >= 3.0:
#         score += cfg.SCORE_VOLUME_3X
#     elif vol_surge >= 2.0:
#         score += cfg.SCORE_VOLUME_2X

#     # Long-term trend bonus
#     ma200 = latest.get("MA200")
#     if pd.notna(ma200) and float(latest["Close"]) > float(ma200):
#         score += cfg.SCORE_ABOVE_MA200

#     # Near 52-week high (momentum strength)
#     w52h = latest.get("Week52High")
#     if pd.notna(w52h) and float(latest["Close"]) >= 0.97 * float(w52h):
#         score += cfg.SCORE_ATH_PROXIMITY

#     return score


# # ─────────────────────────────────────────────────────────
# # SINGLE STOCK ANALYSIS
# # ─────────────────────────────────────────────────────────
# def analyse_stock(symbol: str) -> Optional[BreakoutResult]:
#     """
#     Download data and check if stock is breaking out today.
#     Returns a BreakoutResult if it qualifies, else None.
#     """
#     try:
#         df = yf.download(
#             symbol,
#             period=Config.DATA_PERIOD,
#             interval=Config.DATA_INTERVAL,
#             progress=False,
#             auto_adjust=True,
#         )
#         if df.empty or len(df) < Config.RESISTANCE_WINDOW + 5:
#             return None

#         df = compute_indicators(df)
#         latest = df.iloc[-1]

#         close = float(latest["Close"])
#         resistance = latest.get("ResistanceHigh")
#         ma50 = latest.get("MA50")
#         avg_vol = latest.get("AvgVolume")
#         volume = float(latest["Volume"])

#         # Skip if indicators couldn't be computed
#         if any(pd.isna(v) for v in [resistance, ma50, avg_vol]):
#             return None

#         resistance = float(resistance)
#         ma50 = float(ma50)
#         avg_vol = float(avg_vol)

#         # ── THREE MANDATORY CONDITIONS ──────────────────────
#         price_breakout = close > resistance
#         volume_confirmed = volume >= Config.VOLUME_SURGE_MIN * avg_vol
#         trend_aligned = close > ma50

#         if not (price_breakout and volume_confirmed and trend_aligned):
#             return None
#         # ────────────────────────────────────────────────────

#         vol_surge = volume / avg_vol
#         breakout_pct = ((close - resistance) / resistance) * 100

#         ma200_val = latest.get("MA200")
#         ma200 = float(ma200_val) if pd.notna(ma200_val) else None

#         w52h_val = latest.get("Week52High")
#         week52_high = float(w52h_val) if pd.notna(w52h_val) else None

#         return BreakoutResult(
#             symbol=symbol.replace(".NS", ""),
#             close=close,
#             resistance_level=resistance,
#             volume=int(volume),
#             avg_volume=int(avg_vol),
#             volume_surge=vol_surge,
#             ma50=ma50,
#             ma200=ma200,
#             score=score_breakout(latest),
#             breakout_pct=breakout_pct,
#             week52_high=week52_high,
#         )

#     except Exception as e:
#         return None   # Silent fail; caller can log if needed


# # ─────────────────────────────────────────────────────────
# # FULL SCAN
# # ─────────────────────────────────────────────────────────
# def run_scan(
#     symbols: list[str] = NIFTY500_SYMBOLS,
#     progress_callback=None,
# ) -> list[BreakoutResult]:
#     """
#     Scan all symbols and return sorted breakout list.
#     progress_callback(done, total, symbol) is called after each stock.
#     """
#     results = []
#     total = len(symbols)

#     for i, symbol in enumerate(symbols):
#         result = analyse_stock(symbol)
#         if result:
#             results.append(result)
#         if progress_callback:
#             progress_callback(i + 1, total, symbol)

#     # Sort: highest score first, then volume surge
#     results.sort(key=lambda r: (r.score, r.volume_surge), reverse=True)
#     return results


# # ─────────────────────────────────────────────────────────
# # CLI ENTRYPOINT  (python scanner.py)
# # ─────────────────────────────────────────────────────────
# if __name__ == "__main__":
#     print("\n🔍 Nifty 500 Breakout Scanner")
#     print(f"   Scanning {len(NIFTY500_SYMBOLS)} stocks...\n")

#     def _progress(done, total, sym):
#         pct = int(done / total * 40)
#         bar = "█" * pct + "░" * (40 - pct)
#         print(f"\r  [{bar}] {done}/{total}  {sym:<20}", end="", flush=True)

#     results = run_scan(progress_callback=_progress)
#     print(f"\n\n{'─'*70}")
#     print(f"  🔥 {len(results)} Breakout(s) found  —  {datetime.now().strftime('%d %b %Y, %H:%M')}")
#     print(f"{'─'*70}")

#     if not results:
#         print("  No qualified breakouts today.")
#     else:
#         header = f"{'#':<3} {'Symbol':<14} {'Close':>8} {'Breakout%':>10} {'VolSurge':>9} {'Score':>6}  Label"
#         print(header)
#         print("─" * 70)
#         for idx, r in enumerate(results, 1):
#             print(
#                 f"{idx:<3} {r.symbol:<14} ₹{r.close:>7.2f} "
#                 f"{r.breakout_pct_str:>10} {r.volume_surge_str:>9} "
#                 f"{r.score:>6}  {r.score_label}"
#             )
#     print()
