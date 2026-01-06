"""
05 - Excel Logger
Logs all trading data to Excel files (trades, signals, market data, performance)

What gets logged:
- trades.xlsx: Every completed trade with full details
- signals.xlsx: Every signal generated (traded or skipped)
- market_data.xlsx: Market snapshots for analysis
- performance.xlsx: Daily performance metrics

No SQL database needed - just simple Excel files!

FIX ADDED (TZ-SAFE):
- Excel/openpyxl cannot write timezone-aware datetimes.
- This logger now sanitizes any datetime fields (dict payloads + dataframe columns)
  by converting tz-aware datetimes -> tz-naive before writing to Excel.
"""

import pandas as pd
import os
from datetime import datetime, date
from pathlib import Path
from typing import Dict, Any, Optional, List


# =============================
# Excel-safe datetime helpers
# =============================
def _excel_safe_dt(x: Any) -> Any:
    """
    Excel/openpyxl cannot handle timezone-aware datetimes.
    Convert tz-aware datetime -> tz-naive (keep same wall time).
    """
    if isinstance(x, datetime):
        if x.tzinfo is not None:
            return x.replace(tzinfo=None)
        return x
    return x


def _excel_sanitize_payload(d: Dict[str, Any]) -> Dict[str, Any]:
    """
    Convert any datetime objects inside a dict into Excel-safe datetimes.
    """
    out: Dict[str, Any] = {}
    for k, v in d.items():
        out[k] = _excel_safe_dt(v)
    return out


def _excel_sanitize_df(df: pd.DataFrame) -> pd.DataFrame:
    """
    Ensure any datetime-like columns are timezone-naive before writing to Excel.
    This catches cases where datetimes are already in the DataFrame.
    """
    if df is None or len(df) == 0:
        return df

    for col in df.columns:
        try:
            if pd.api.types.is_datetime64_any_dtype(df[col]):
                # If tz-aware, drop tz; if tz-naive, keep as is.
                # This works for datetime64[ns, tz] and datetime64[ns]
                if getattr(df[col].dt, "tz", None) is not None:
                    df[col] = df[col].dt.tz_localize(None)
        except Exception:
            # Column may not be datetime-like; ignore
            pass

    return df


class ExcelLogger:
    """
    Log all trading data to Excel files
    Simple, portable, easy to analyze
    """

    def __init__(self, log_dir: str = 'logs'):
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(exist_ok=True)

        # File paths
        self.trades_file = self.log_dir / 'trades.xlsx'
        self.signals_file = self.log_dir / 'signals.xlsx'
        self.market_data_file = self.log_dir / 'market_data.xlsx'
        self.performance_file = self.log_dir / 'performance.xlsx'

        # Initialize files if they don't exist
        self._init_files()

        print(f"✅ [05] Excel logger initialized (logs in: {self.log_dir}/)")

    def _init_files(self):
        """Create Excel files with headers if they don't exist"""

        # === TRADES FILE ===
        if not self.trades_file.exists():
            df = pd.DataFrame(columns=[
                'Trade_ID',
                'Entry_Time',
                'Exit_Time',
                'Market_Slug',
                'Market_Question',
                'Token_ID',
                'Outcome',
                'Side',
                'Entry_Price',
                'Exit_Price',
                'Position_Size',
                'PnL',
                'PnL_Pct',
                'Exit_Reason',
                'Hold_Duration_Sec',
                'BTC_Price_Entry',
                'BTC_Price_Exit',
                'Regime_Score',
                'Overreaction_Score',
                'Spread_Entry',
                'Spread_Exit',
                'Notes'
            ])
            df.to_excel(self.trades_file, index=False, engine='openpyxl')

        # === SIGNALS FILE ===
        if not self.signals_file.exists():
            df = pd.DataFrame(columns=[
                'Signal_ID',
                'Timestamp',
                'Market_Slug',
                'Market_Question',
                'Token_ID',
                'Outcome',
                'Signal_Type',
                'Side',
                'Confidence',
                'Regime_OK',
                'Regime_Score',
                'Overreaction_Score',
                'BTC_ATR',
                'BTC_BB_Width',
                'Poly_Spread',
                'Poly_Volume',
                'Traded',
                'Skip_Reason'
            ])
            df.to_excel(self.signals_file, index=False, engine='openpyxl')

        # === MARKET DATA FILE ===
        if not self.market_data_file.exists():
            df = pd.DataFrame(columns=[
                'Timestamp',
                'Market_Slug',
                'Token_ID',
                'BTC_Price',
                'Poly_Price',
                'Poly_Best_Bid',
                'Poly_Best_Ask',
                'Spread_Pct',
                'Bid_Depth',
                'Ask_Depth',
                'Recent_Volume',
                'BTC_ATR',
                'BTC_BB_Width',
                'Time_Until_Expiry_Min'
            ])
            df.to_excel(self.market_data_file, index=False, engine='openpyxl')

        # === PERFORMANCE FILE ===
        if not self.performance_file.exists():
            df = pd.DataFrame(columns=[
                'Date',
                'Total_Trades',
                'Winning_Trades',
                'Losing_Trades',
                'Win_Rate_Pct',
                'Total_PnL',
                'Total_PnL_Pct',
                'Avg_Win',
                'Avg_Loss',
                'Largest_Win',
                'Largest_Loss',
                'Profit_Factor',
                'Max_Drawdown_Pct',
                'Sharpe_Ratio',
                'Avg_Hold_Time_Sec',
                'Total_Volume'
            ])
            df.to_excel(self.performance_file, index=False, engine='openpyxl')

    # =============================
    # TRADE LOGGING
    # =============================
    def log_trade(self, trade_data: Dict[str, Any]) -> str:
        """
        Log a completed trade

        Args:
            trade_data: dict with trade information
                Required: entry_time, exit_time, market_slug, token_id, outcome,
                         side, entry_price, exit_price, position_size, exit_reason
                Optional: all other fields

        Returns:
            trade_id (e.g., "T_0001")
        """
        try:
            df = pd.read_excel(self.trades_file, engine='openpyxl')
        except Exception as e:
            print(f"⚠️  [05] Error reading trades file: {e}")
            df = pd.DataFrame()

        # Generate trade ID
        trade_id = f"T_{len(df) + 1:04d}"

        # Calculate PnL
        entry = trade_data.get('entry_price', 0)
        exit_price = trade_data.get('exit_price', 0)
        size = trade_data.get('position_size', 0)

        pnl = (exit_price - entry) * size
        pnl_pct = ((exit_price - entry) / entry * 100) if entry > 0 else 0

        # Calculate hold duration
        entry_time = trade_data.get('entry_time')
        exit_time = trade_data.get('exit_time')

        if isinstance(entry_time, datetime) and isinstance(exit_time, datetime):
            hold_duration = (exit_time - entry_time).total_seconds()
        else:
            hold_duration = trade_data.get('hold_duration_sec', 0)

        # TZ-safe sanitize times
        entry_time = _excel_safe_dt(entry_time)
        exit_time = _excel_safe_dt(exit_time)

        row_payload = {
            'Trade_ID': trade_id,
            'Entry_Time': entry_time,
            'Exit_Time': exit_time,
            'Market_Slug': trade_data.get('market_slug', 'N/A'),
            'Market_Question': trade_data.get('market_question', 'N/A'),
            'Token_ID': trade_data.get('token_id', 'N/A'),
            'Outcome': trade_data.get('outcome', 'N/A'),
            'Side': trade_data.get('side', 'N/A'),
            'Entry_Price': entry,
            'Exit_Price': exit_price,
            'Position_Size': size,
            'PnL': pnl,
            'PnL_Pct': pnl_pct,
            'Exit_Reason': trade_data.get('exit_reason', 'N/A'),
            'Hold_Duration_Sec': hold_duration,
            'BTC_Price_Entry': trade_data.get('btc_price_entry'),
            'BTC_Price_Exit': trade_data.get('btc_price_exit'),
            'Regime_Score': trade_data.get('regime_score'),
            'Overreaction_Score': trade_data.get('overreaction_score'),
            'Spread_Entry': trade_data.get('spread_entry'),
            'Spread_Exit': trade_data.get('spread_exit'),
            'Notes': trade_data.get('notes', '')
        }

        row_payload = _excel_sanitize_payload(row_payload)

        # Create new row
        new_row = pd.DataFrame([row_payload])

        # Append and save
        df = pd.concat([df, new_row], ignore_index=True)
        df = _excel_sanitize_df(df)
        df.to_excel(self.trades_file, index=False, engine='openpyxl')

        # Print summary
        emoji = "💰" if pnl > 0 else "📉"
        print(f"{emoji} [05] Trade {trade_id} logged: {trade_data.get('side')} | PnL: ${pnl:.2f} ({pnl_pct:+.2f}%)")

        return trade_id

    # =============================
    # SIGNAL LOGGING
    # =============================
    def log_signal(self, signal_data: Dict[str, Any]) -> str:
        """
        Log a trading signal (whether traded or not)

        Args:
            signal_data: dict with signal information
                Required: market_slug, token_id, signal_type, side
                Optional: all other fields

        Returns:
            signal_id (e.g., "S_0001")
        """
        try:
            df = pd.read_excel(self.signals_file, engine='openpyxl')
        except Exception as e:
            print(f"⚠️  [05] Error reading signals file: {e}")
            df = pd.DataFrame()

        signal_id = f"S_{len(df) + 1:04d}"

        row_payload = {
            'Signal_ID': signal_id,
            'Timestamp': datetime.utcnow(),  # tz-naive
            'Market_Slug': signal_data.get('market_slug', 'N/A'),
            'Market_Question': signal_data.get('market_question', 'N/A'),
            'Token_ID': signal_data.get('token_id', 'N/A'),
            'Outcome': signal_data.get('outcome', 'N/A'),
            'Signal_Type': signal_data.get('signal_type', 'N/A'),
            'Side': signal_data.get('side', 'N/A'),
            'Confidence': signal_data.get('confidence', 0),
            'Regime_OK': signal_data.get('regime_ok', False),
            'Regime_Score': signal_data.get('regime_score'),
            'Overreaction_Score': signal_data.get('overreaction_score'),
            'BTC_ATR': signal_data.get('btc_atr'),
            'BTC_BB_Width': signal_data.get('btc_bb_width'),
            'Poly_Spread': signal_data.get('poly_spread'),
            'Poly_Volume': signal_data.get('poly_volume'),
            'Traded': signal_data.get('traded', False),
            'Skip_Reason': signal_data.get('skip_reason', '')
        }

        row_payload = _excel_sanitize_payload(row_payload)

        new_row = pd.DataFrame([row_payload])
        df = pd.concat([df, new_row], ignore_index=True)
        df = _excel_sanitize_df(df)
        df.to_excel(self.signals_file, index=False, engine='openpyxl')

        traded_status = "✅ TRADED" if signal_data.get('traded') else f"⏭️  SKIPPED ({signal_data.get('skip_reason', 'N/A')})"
        print(f"📊 [05] Signal {signal_id} logged: {signal_data.get('signal_type')} | {traded_status}")

        return signal_id

    # =============================
    # MARKET DATA LOGGING
    # =============================
    def log_market_snapshot(self, snapshot_data: Dict[str, Any]) -> None:
        """
        Log a market data snapshot
        Used for analysis and debugging
        """
        try:
            df = pd.read_excel(self.market_data_file, engine='openpyxl')
        except Exception:
            df = pd.DataFrame()

        row_payload = {
            'Timestamp': datetime.utcnow(),  # tz-naive
            'Market_Slug': snapshot_data.get('market_slug', 'N/A'),
            'Token_ID': snapshot_data.get('token_id', 'N/A'),
            'BTC_Price': snapshot_data.get('btc_price'),
            'Poly_Price': snapshot_data.get('poly_price'),
            'Poly_Best_Bid': snapshot_data.get('poly_best_bid'),
            'Poly_Best_Ask': snapshot_data.get('poly_best_ask'),
            'Spread_Pct': snapshot_data.get('spread_pct'),
            'Bid_Depth': snapshot_data.get('bid_depth'),
            'Ask_Depth': snapshot_data.get('ask_depth'),
            'Recent_Volume': snapshot_data.get('recent_volume'),
            'BTC_ATR': snapshot_data.get('btc_atr'),
            'BTC_BB_Width': snapshot_data.get('btc_bb_width'),
            'Time_Until_Expiry_Min': snapshot_data.get('time_until_expiry_min')
        }

        row_payload = _excel_sanitize_payload(row_payload)

        new_row = pd.DataFrame([row_payload])
        df = pd.concat([df, new_row], ignore_index=True)

        # Keep only last 10,000 rows (prevent file bloat)
        if len(df) > 10000:
            df = df.tail(10000)

        df = _excel_sanitize_df(df)
        df.to_excel(self.market_data_file, index=False, engine='openpyxl')

    # =============================
    # PERFORMANCE TRACKING
    # =============================
    def update_daily_performance(self) -> None:
        """
        Calculate and update daily performance metrics
        Call this at end of day or after each trade
        """
        try:
            trades_df = pd.read_excel(self.trades_file, engine='openpyxl')
        except Exception:
            print("⚠️  [05] No trades to analyze")
            return

        if len(trades_df) == 0:
            return

        # Filter today's trades
        today = date.today()
        trades_df['Exit_Time'] = pd.to_datetime(trades_df['Exit_Time'], errors='coerce')
        today_trades = trades_df[trades_df['Exit_Time'].dt.date == today]

        if len(today_trades) == 0:
            return

        # Calculate metrics
        total_trades = len(today_trades)
        winning_trades = len(today_trades[today_trades['PnL'] > 0])
        losing_trades = len(today_trades[today_trades['PnL'] <= 0])
        win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0

        total_pnl = today_trades['PnL'].sum()
        total_pnl_pct = today_trades['PnL_Pct'].mean()

        wins = today_trades[today_trades['PnL'] > 0]['PnL']
        losses = today_trades[today_trades['PnL'] <= 0]['PnL']

        avg_win = wins.mean() if len(wins) > 0 else 0
        avg_loss = abs(losses.mean()) if len(losses) > 0 else 0
        largest_win = wins.max() if len(wins) > 0 else 0
        largest_loss = losses.min() if len(losses) > 0 else 0

        profit_factor = (avg_win * winning_trades) / (avg_loss * losing_trades) if losing_trades > 0 and avg_loss > 0 else 0

        # Calculate max drawdown
        cumulative_pnl = today_trades['PnL'].cumsum()
        running_max = cumulative_pnl.cummax()
        drawdown = (cumulative_pnl - running_max) / running_max * 100
        max_drawdown = abs(drawdown.min()) if len(drawdown) > 0 else 0

        # Calculate Sharpe ratio (simplified daily)
        returns = today_trades['PnL_Pct'].values
        sharpe = (returns.mean() / returns.std() * (252 ** 0.5)) if len(returns) > 1 and returns.std() > 0 else 0

        # Average hold time
        avg_hold_time = today_trades['Hold_Duration_Sec'].mean()

        # Total volume
        total_volume = today_trades['Position_Size'].sum()

        # Update performance file
        try:
            perf_df = pd.read_excel(self.performance_file, engine='openpyxl')
        except Exception:
            perf_df = pd.DataFrame()

        # Check if today already exists
        if len(perf_df) > 0:
            perf_df['Date'] = pd.to_datetime(perf_df['Date'], errors='coerce').dt.date
            existing = perf_df[perf_df['Date'] == today]
        else:
            existing = pd.DataFrame()

        new_row = {
            'Date': today,  # date is Excel-safe
            'Total_Trades': total_trades,
            'Winning_Trades': winning_trades,
            'Losing_Trades': losing_trades,
            'Win_Rate_Pct': win_rate,
            'Total_PnL': total_pnl,
            'Total_PnL_Pct': total_pnl_pct,
            'Avg_Win': avg_win,
            'Avg_Loss': avg_loss,
            'Largest_Win': largest_win,
            'Largest_Loss': largest_loss,
            'Profit_Factor': profit_factor,
            'Max_Drawdown_Pct': max_drawdown,
            'Sharpe_Ratio': sharpe,
            'Avg_Hold_Time_Sec': avg_hold_time,
            'Total_Volume': total_volume
        }

        if len(existing) > 0:
            for key, value in new_row.items():
                perf_df.loc[perf_df['Date'] == today, key] = value
        else:
            perf_df = pd.concat([perf_df, pd.DataFrame([new_row])], ignore_index=True)

        perf_df = _excel_sanitize_df(perf_df)
        perf_df.to_excel(self.performance_file, index=False, engine='openpyxl')

        print(f"\n📈 [05] Daily Performance Updated ({today}):")
        print(f"   Trades: {total_trades} | Win Rate: {win_rate:.1f}%")
        print(f"   PnL: ${total_pnl:.2f} | Sharpe: {sharpe:.2f}")
        print(f"   Profit Factor: {profit_factor:.2f} | Max DD: {max_drawdown:.2f}%\n")

    # =============================
    # ANALYSIS HELPERS
    # =============================
    def get_recent_trades(self, limit: int = 20) -> pd.DataFrame:
        """Get last N trades"""
        try:
            df = pd.read_excel(self.trades_file, engine='openpyxl')
            return df.tail(limit)
        except Exception:
            return pd.DataFrame()

    def get_win_rate(self, last_n_trades: int = 100) -> float:
        """Calculate win rate from recent trades"""
        try:
            df = pd.read_excel(self.trades_file, engine='openpyxl').tail(last_n_trades)
            if len(df) == 0:
                return 0.0
            return len(df[df['PnL'] > 0]) / len(df) * 100
        except Exception:
            return 0.0

    def get_total_pnl(self) -> float:
        """Get total PnL across all trades"""
        try:
            df = pd.read_excel(self.trades_file, engine='openpyxl')
            return df['PnL'].sum()
        except Exception:
            return 0.0

    def get_trade_count(self) -> int:
        """Get total number of trades"""
        try:
            df = pd.read_excel(self.trades_file, engine='openpyxl')
            return len(df)
        except Exception:
            return 0


print("✅ [05] Excel logger loaded")


# =============================
# Test Runner
# =============================
if __name__ == "__main__":
    print("\n🧪 Testing [05] - Excel Logger\n" + "="*80)

    # Initialize logger
    logger = ExcelLogger(log_dir='logs_test')

    print("\n" + "="*80)
    print("Test 1: Logging a trade")
    print("="*80)

    # Log a sample trade
    trade_data = {
        'entry_time': datetime.utcnow(),
        'exit_time': datetime.utcnow(),
        'market_slug': 'btc-updown-15m-test',
        'market_question': 'Will Bitcoin go up or down in next 15 minutes?',
        'token_id': '0xtest123',
        'outcome': 'UP',
        'side': 'BUY',
        'entry_price': 0.48,
        'exit_price': 0.52,
        'position_size': 100,
        'exit_reason': 'TAKE_PROFIT',
        'btc_price_entry': 95000,
        'btc_price_exit': 95100,
        'regime_score': 0.85,
        'overreaction_score': 65,
        'spread_entry': 0.025,
        'notes': 'Test trade'
    }

    trade_id = logger.log_trade(trade_data)
    print(f"✅ Trade logged with ID: {trade_id}")

    print("\n" + "="*80)
    print("Test 2: Logging a signal")
    print("="*80)

    # Log a sample signal
    signal_data = {
        'market_slug': 'btc-updown-15m-test',
        'market_question': 'Will Bitcoin go up or down in next 15 minutes?',
        'token_id': '0xtest123',
        'outcome': 'UP',
        'signal_type': 'OVERREACTION',
        'side': 'BUY',
        'confidence': 75,
        'regime_ok': True,
        'regime_score': 0.85,
        'overreaction_score': 65,
        'traded': True
    }

    signal_id = logger.log_signal(signal_data)
    print(f"✅ Signal logged with ID: {signal_id}")

    print("\n" + "="*80)
    print("Test 3: Updating performance")
    print("="*80)

    logger.update_daily_performance()

    print("\n" + "="*80)
    print("Test 4: Analysis helpers")
    print("="*80)

    recent = logger.get_recent_trades(limit=5)
    print(f"✅ Recent trades: {len(recent)}")

    win_rate = logger.get_win_rate(last_n_trades=100)
    print(f"✅ Win rate: {win_rate:.1f}%")

    total_pnl = logger.get_total_pnl()
    print(f"✅ Total PnL: ${total_pnl:.2f}")

    trade_count = logger.get_trade_count()
    print(f"✅ Total trades: {trade_count}")

    print("\n" + "="*80)
    print("✅ All tests passed!")
    print(f"📁 Test files created in: logs_test/")
    print("="*80 + "\n")
