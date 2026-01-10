# excel_logger.py
# ============================================================
# Simple Excel logger (append-only)
# Each call appends one row to a sheet.
# ============================================================

from pathlib import Path
from typing import Dict, Any

import pandas as pd


def append_excel(path: Path, sheet: str, row: Dict[str, Any]) -> None:
    """
    Append a single row to an Excel sheet.
    Creates file/sheet if missing.
    """
    path.parent.mkdir(parents=True, exist_ok=True)

    if path.exists():
        with pd.ExcelWriter(path, engine="openpyxl", mode="a", if_sheet_exists="overlay") as writer:
            try:
                existing = pd.read_excel(path, sheet_name=sheet)
                df = pd.concat([existing, pd.DataFrame([row])], ignore_index=True)
            except ValueError:
                df = pd.DataFrame([row])
            df.to_excel(writer, sheet_name=sheet, index=False)
    else:
        with pd.ExcelWriter(path, engine="openpyxl") as writer:
            pd.DataFrame([row]).to_excel(writer, sheet_name=sheet, index=False)
