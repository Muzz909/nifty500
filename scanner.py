# scanner.py
"""
Core breakout detection engine.
Completely independent of Streamlit — can be imported anywhere
or run as a standalone script.
"""

import yfinance as yf
import pandas as pd
from dataclasses import dataclass, field
from typing import Optional
from datetime import datetime

from nifty500_symbols import NIFTY500_SYMBOLS

# ─────────────────────────────────────────────────────────
# CONFIG  — tweak thresholds here without touching logic
# ─────────────────────────────────────────────────────────
class Config:
    RESISTANCE_WINDOW: int = 20       # Days for resistance high
    VOLUME_WINDOW: int = 10           # Days for avg-volume baseline
    VOLUME_SURGE_MIN: float = 1.5     # Minimum volume multiplier to qualify
    TREND_MA: int = 50                # MA period for trend filter
    DATA_PERIOD: str = "6mo"          # yfinance download period
    DATA_INTERVAL: str = "1d"

    # Scoring weights
    SCORE_ABOVE_MA200: int = 2        # Long-term trend bonus
    SCORE_VOLUME_2X: int = 2          # Strong volume bonus
    SCORE_VOLUME_3X: int = 3          # Exceptional volume bonus
    SCORE_ATH_PROXIMITY: int = 2      # Near 52-week high
    SCORE_BASE: int = 3               # Passes all 3 core conditions


# ─────────────────────────────────────────────────────────
# DATA MODELS
# ─────────────────────────────────────────────────────────
@dataclass
class BreakoutResult:
    symbol: str
    close: float
    resistance_level: float          # The level that was broken
    volume: int
    avg_volume: int
    volume_surge: float              # e.g. 2.3 = 2.3× average
    ma50: float
    ma200: Optional[float]
    score: int                       # Quality score (higher = stronger)
    breakout_pct: float              # % above resistance
    week52_high: Optional[float]
    error: Optional[str] = None

    @property
    def volume_surge_str(self) -> str:
        return f"{self.volume_surge:.1f}x"

    @property
    def breakout_pct_str(self) -> str:
        return f"+{self.breakout_pct:.2f}%"

    @property
    def score_label(self) -> str:
        if self.score >= 9:
            return "🔥 Exceptional"
        elif self.score >= 7:
            return "⚡ Strong"
        elif self.score >= 5:
            return "✅ Good"
        else:
            return "📌 Moderate"


# ─────────────────────────────────────────────────────────
# INDICATOR CALCULATION
# ─────────────────────────────────────────────────────────
def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Add all technical indicators to the OHLCV DataFrame."""
    close = df["Close"].squeeze()
    high = df["High"].squeeze()
    volume = df["Volume"].squeeze()

    df["MA50"] = close.rolling(Config.TREND_MA).mean()
    df["MA200"] = close.rolling(200).mean()
    df["AvgVolume"] = volume.rolling(Config.VOLUME_WINDOW).mean()
    df["ResistanceHigh"] = high.shift(1).rolling(Config.RESISTANCE_WINDOW).max()
    df["Week52High"] = high.rolling(252).max()
    return df


def score_breakout(latest: pd.Series, cfg: Config = Config) -> int:
    """
    Assign a quality score to a breakout.
    Base score = 3 (passed the 3 mandatory conditions).
    Additional points for extra strength signals.
    """
    score = cfg.SCORE_BASE
    vol_surge = float(latest["Volume"]) / float(latest["AvgVolume"])

    # Volume strength bonus
    if vol_surge >= 3.0:
        score += cfg.SCORE_VOLUME_3X
    elif vol_surge >= 2.0:
        score += cfg.SCORE_VOLUME_2X

    # Long-term trend bonus
    ma200 = latest.get("MA200")
    if pd.notna(ma200) and float(latest["Close"]) > float(ma200):
        score += cfg.SCORE_ABOVE_MA200

    # Near 52-week high (momentum strength)
    w52h = latest.get("Week52High")
    if pd.notna(w52h) and float(latest["Close"]) >= 0.97 * float(w52h):
        score += cfg.SCORE_ATH_PROXIMITY

    return score


# ─────────────────────────────────────────────────────────
# SINGLE STOCK ANALYSIS
# ─────────────────────────────────────────────────────────
def analyse_stock(symbol: str) -> Optional[BreakoutResult]:
    """
    Download data and check if stock is breaking out today.
    Returns a BreakoutResult if it qualifies, else None.
    """
    try:
        df = yf.download(
            symbol,
            period=Config.DATA_PERIOD,
            interval=Config.DATA_INTERVAL,
            progress=False,
            auto_adjust=True,
        )
        if df.empty or len(df) < Config.RESISTANCE_WINDOW + 5:
            return None

        df = compute_indicators(df)
        latest = df.iloc[-1]

        close = float(latest["Close"])
        resistance = latest.get("ResistanceHigh")
        ma50 = latest.get("MA50")
        avg_vol = latest.get("AvgVolume")
        volume = float(latest["Volume"])

        # Skip if indicators couldn't be computed
        if any(pd.isna(v) for v in [resistance, ma50, avg_vol]):
            return None

        resistance = float(resistance)
        ma50 = float(ma50)
        avg_vol = float(avg_vol)

        # ── THREE MANDATORY CONDITIONS ──────────────────────
        price_breakout = close > resistance
        volume_confirmed = volume >= Config.VOLUME_SURGE_MIN * avg_vol
        trend_aligned = close > ma50

        if not (price_breakout and volume_confirmed and trend_aligned):
            return None
        # ────────────────────────────────────────────────────

        vol_surge = volume / avg_vol
        breakout_pct = ((close - resistance) / resistance) * 100

        ma200_val = latest.get("MA200")
        ma200 = float(ma200_val) if pd.notna(ma200_val) else None

        w52h_val = latest.get("Week52High")
        week52_high = float(w52h_val) if pd.notna(w52h_val) else None

        return BreakoutResult(
            symbol=symbol.replace(".NS", ""),
            close=close,
            resistance_level=resistance,
            volume=int(volume),
            avg_volume=int(avg_vol),
            volume_surge=vol_surge,
            ma50=ma50,
            ma200=ma200,
            score=score_breakout(latest),
            breakout_pct=breakout_pct,
            week52_high=week52_high,
        )

    except Exception as e:
        return None   # Silent fail; caller can log if needed


# ─────────────────────────────────────────────────────────
# FULL SCAN
# ─────────────────────────────────────────────────────────
def run_scan(
    symbols: list[str] = NIFTY500_SYMBOLS,
    progress_callback=None,
) -> list[BreakoutResult]:
    """
    Scan all symbols and return sorted breakout list.
    progress_callback(done, total, symbol) is called after each stock.
    """
    results = []
    total = len(symbols)

    for i, symbol in enumerate(symbols):
        result = analyse_stock(symbol)
        if result:
            results.append(result)
        if progress_callback:
            progress_callback(i + 1, total, symbol)

    # Sort: highest score first, then volume surge
    results.sort(key=lambda r: (r.score, r.volume_surge), reverse=True)
    return results


# ─────────────────────────────────────────────────────────
# CLI ENTRYPOINT  (python scanner.py)
# ─────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("\n🔍 Nifty 500 Breakout Scanner")
    print(f"   Scanning {len(NIFTY500_SYMBOLS)} stocks...\n")

    def _progress(done, total, sym):
        pct = int(done / total * 40)
        bar = "█" * pct + "░" * (40 - pct)
        print(f"\r  [{bar}] {done}/{total}  {sym:<20}", end="", flush=True)

    results = run_scan(progress_callback=_progress)
    print(f"\n\n{'─'*70}")
    print(f"  🔥 {len(results)} Breakout(s) found  —  {datetime.now().strftime('%d %b %Y, %H:%M')}")
    print(f"{'─'*70}")

    if not results:
        print("  No qualified breakouts today.")
    else:
        header = f"{'#':<3} {'Symbol':<14} {'Close':>8} {'Breakout%':>10} {'VolSurge':>9} {'Score':>6}  Label"
        print(header)
        print("─" * 70)
        for idx, r in enumerate(results, 1):
            print(
                f"{idx:<3} {r.symbol:<14} ₹{r.close:>7.2f} "
                f"{r.breakout_pct_str:>10} {r.volume_surge_str:>9} "
                f"{r.score:>6}  {r.score_label}"
            )
    print()
