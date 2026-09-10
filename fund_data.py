from __future__ import annotations

from datetime import datetime
from io import StringIO
from pathlib import Path
import sqlite3
from typing import TypeAlias

import pandas as pd


Bundle: TypeAlias = dict[str, tuple[pd.DataFrame, str, str]]


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    return connection


def init_db(path: Path) -> None:
    with connect(path) as connection:
        connection.executescript("""
        CREATE TABLE IF NOT EXISTS cache (
          cache_key TEXT NOT NULL, dataset TEXT NOT NULL, as_of TEXT NOT NULL,
          source TEXT NOT NULL, fetched_at TEXT NOT NULL, payload TEXT NOT NULL,
          PRIMARY KEY (cache_key, dataset)
        );
        CREATE TABLE IF NOT EXISTS updates (
          id INTEGER PRIMARY KEY AUTOINCREMENT, cache_key TEXT NOT NULL,
          finished_at TEXT NOT NULL, status TEXT NOT NULL, message TEXT NOT NULL
        );
        """)


def save_bundle(path: Path, key: str, bundle: Bundle) -> None:
    if any(frame.empty for frame, _, _ in bundle.values()):
        raise ValueError("不能用空数据覆盖缓存")
    fetched_at = datetime.now().astimezone().isoformat(timespec="seconds")
    rows = [{"cache_key": key, "dataset": dataset, "as_of": as_of,
             "source": source, "fetched_at": fetched_at,
             "payload": frame.to_json(orient="table", date_format="iso", force_ascii=False)}
            for dataset, (frame, as_of, source) in bundle.items()]
    with connect(path) as connection:
        connection.executemany("""INSERT INTO cache(cache_key,dataset,as_of,source,fetched_at,payload)
        VALUES(:cache_key,:dataset,:as_of,:source,:fetched_at,:payload)
        ON CONFLICT(cache_key,dataset) DO UPDATE SET as_of=excluded.as_of,
        source=excluded.source,fetched_at=excluded.fetched_at,payload=excluded.payload""", rows)


def load_frame(path: Path, key: str, dataset: str) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    with connect(path) as connection:
        row = connection.execute("SELECT payload FROM cache WHERE cache_key=? AND dataset=?", (key, dataset)).fetchone()
    return pd.DataFrame() if row is None else pd.read_json(StringIO(row["payload"]), orient="table")


def record_update(path: Path, key: str, status: str, message: str) -> None:
    with connect(path) as connection:
        connection.execute("INSERT INTO updates(cache_key,finished_at,status,message) VALUES(?,?,?,?)",
                           (key, datetime.now().astimezone().isoformat(timespec="seconds"), status, message))


def latest_update(path: Path, key: str, successful_only: bool = False) -> dict | None:
    if not path.exists():
        return None
    with connect(path) as connection:
        sql = "SELECT * FROM updates WHERE cache_key=?"
        params = [key]
        if successful_only:
            sql += " AND status IN ('success','partial')"
        row = connection.execute(sql + " ORDER BY id DESC LIMIT 1", params).fetchone()
    return None if row is None else dict(row)
