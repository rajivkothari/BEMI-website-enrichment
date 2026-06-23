"""Read and write tabular practice data (CSV / XLSX).

These helpers are intentionally format-agnostic: the file type is inferred
from the path extension so the CLI can accept and emit either CSV or Excel.
"""
from __future__ import annotations

from pathlib import Path
from typing import Union

import pandas as pd

PathLike = Union[str, Path]

_CSV_SUFFIXES = {".csv"}
_EXCEL_SUFFIXES = {".xlsx", ".xls"}


def load_table(path: PathLike) -> pd.DataFrame:
    """Load a practices spreadsheet into a DataFrame of strings.

    All cells are read as strings and missing values replaced with ``""`` so
    downstream code never has to special-case ``NaN``. Column names are
    stripped of surrounding whitespace.

    Args:
        path: Path to a ``.csv``, ``.xlsx``, or ``.xls`` file.

    Returns:
        A :class:`pandas.DataFrame` with string columns.

    Raises:
        ValueError: If the file extension is not supported.
    """
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix in _CSV_SUFFIXES:
        df = pd.read_csv(path, dtype=str)
    elif suffix in _EXCEL_SUFFIXES:
        df = pd.read_excel(path, dtype=str)
    else:
        raise ValueError(
            f"Unsupported input format: {suffix!r} (expected .csv, .xlsx, or .xls)"
        )

    df.columns = [str(col).strip() for col in df.columns]
    return df.fillna("")


def write_table(df: pd.DataFrame, path: PathLike) -> Path:
    """Write a DataFrame to CSV or XLSX, inferring the type from the path.

    Parent directories are created automatically if they do not exist.

    Args:
        df: The DataFrame to write.
        path: Destination ``.csv``, ``.xlsx``, or ``.xls`` path.

    Returns:
        The resolved output :class:`~pathlib.Path`.

    Raises:
        ValueError: If the file extension is not supported.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    suffix = path.suffix.lower()
    if suffix in _CSV_SUFFIXES:
        df.to_csv(path, index=False)
    elif suffix in _EXCEL_SUFFIXES:
        df.to_excel(path, index=False)
    else:
        raise ValueError(
            f"Unsupported output format: {suffix!r} (expected .csv, .xlsx, or .xls)"
        )
    return path
