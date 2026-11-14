"""Persistencia en SQLite y almacén de capturas.

Un *store* es una carpeta con ``monitoreo.db`` y ``capturas/``. Las referencias de imagen
(``image_ref``) son rutas relativas al store, de modo que el store completo puede moverse
o versionarse sin romper vínculos. No existe ninguna rutina de expiración ni borrado.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS configs (
  config_ref TEXT PRIMARY KEY,
  config_id TEXT NOT NULL,
  source_path TEXT,
  created_at REAL NOT NULL,
  canonical_json TEXT NOT NULL,
  raw_yaml TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS runs (
  run_id TEXT PRIMARY KEY,
  scenario TEXT,
  started_at REAL NOT NULL,
  ended_at REAL,
  config_ref TEXT NOT NULL REFERENCES configs(config_ref),
  model_ref TEXT NOT NULL,
  model_json TEXT NOT NULL,
  source_id TEXT NOT NULL,
  source_json TEXT NOT NULL,
  git_commit TEXT,
  git_dirty INTEGER,
  status TEXT NOT NULL DEFAULT 'running',
  notes TEXT
);
-- Cada frame recibido de la fuente, se infiera o no.
CREATE TABLE IF NOT EXISTS frames (
  frame_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES runs(run_id),
  seq INTEGER NOT NULL,
  received_at REAL NOT NULL,
  origin TEXT,
  outcome TEXT NOT NULL,      -- inferred | dropped_sampling | dropped_saturation | read_error
  event_id TEXT
);
CREATE INDEX IF NOT EXISTS ix_frames_run ON frames(run_id, seq);
-- Evento de inferencia (uno por frame procesado por el modelo).
CREATE TABLE IF NOT EXISTS events (
  event_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES runs(run_id),
  frame_id TEXT NOT NULL REFERENCES frames(frame_id),
  seq INTEGER NOT NULL,
  timestamp REAL NOT NULL,     -- = received_at del frame (instante de captura)
  source_id TEXT NOT NULL,
  task TEXT NOT NULL,
  model_ref TEXT NOT NULL,
  config_ref TEXT NOT NULL,
  status TEXT NOT NULL,        -- ok | error | no_score
  class_id INTEGER,
  class_name TEXT,
  score REAL,
  zone TEXT,                   -- accepted | doubt | low | NULL (si status != ok)
  top5_json TEXT,
  lower_threshold REAL NOT NULL,
  upper_threshold REAL NOT NULL,
  image_ref TEXT,
  image_sha256 TEXT,
  enqueued_at REAL,
  dequeued_at REAL,
  done_at REAL,
  queue_ms REAL,
  preprocess_ms REAL,
  inference_ms REAL,
  e2e_ms REAL,
  error TEXT
);
CREATE INDEX IF NOT EXISTS ix_events_run_ts ON events(run_id, timestamp);
CREATE TABLE IF NOT EXISTS source_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT NOT NULL,
  timestamp REAL NOT NULL,
  kind TEXT NOT NULL,
  detail_json TEXT
);
CREATE TABLE IF NOT EXISTS resource_samples (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT NOT NULL,
  timestamp REAL NOT NULL,
  process_cpu_pct REAL,        -- % de un núcleo (psutil Process.cpu_percent), puede superar 100
  process_rss_mb REAL,
  system_cpu_pct REAL,
  system_ram_pct REAL,
  queue_len INTEGER,
  disk_free_gb REAL
);
CREATE TABLE IF NOT EXISTS window_evaluations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT NOT NULL,
  rule_id TEXT NOT NULL,
  window_start REAL NOT NULL,
  window_end REAL NOT NULL,
  n_eligible INTEGER NOT NULL,
  n_low INTEGER NOT NULL,
  low_rate REAL,
  mean_score REAL,
  state TEXT NOT NULL,         -- insufficient | normal | breach
  streak INTEGER,
  detail_json TEXT
);
CREATE TABLE IF NOT EXISTS alerts (
  alert_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  rule_id TEXT NOT NULL,
  timestamp REAL NOT NULL,     -- instante en que se disparó
  last_seen REAL NOT NULL,
  resolved_at REAL,
  status TEXT NOT NULL,        -- active | resolved
  severity TEXT NOT NULL,
  reason TEXT NOT NULL,
  suggested_action TEXT NOT NULL,
  config_ref TEXT NOT NULL,
  window_start REAL,
  window_end REAL,
  evidence_json TEXT,          -- valores calculados que dispararon la regla
  event_ids_json TEXT NOT NULL,
  query TEXT                   -- consulta SQL reproducible de los eventos de la ventana
);
"""


class Store:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "capturas").mkdir(exist_ok=True)
        self.db_path = self.root / "monitoreo.db"
        self.lock = threading.RLock()
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    # ---- escritura -------------------------------------------------------------
    def insert(self, table: str, row: dict) -> None:
        cols = ",".join(row)
        qs = ",".join("?" for _ in row)
        with self.lock:
            self.conn.execute(f"INSERT INTO {table} ({cols}) VALUES ({qs})", list(row.values()))
            self.conn.commit()

    def upsert(self, table: str, row: dict) -> None:
        cols = ",".join(row)
        qs = ",".join("?" for _ in row)
        with self.lock:
            self.conn.execute(f"INSERT OR REPLACE INTO {table} ({cols}) VALUES ({qs})", list(row.values()))
            self.conn.commit()

    def execute(self, sql: str, params: tuple | list = ()) -> None:
        with self.lock:
            self.conn.execute(sql, params)
            self.conn.commit()

    # ---- lectura ---------------------------------------------------------------
    def query(self, sql: str, params: tuple | list = ()) -> list[dict]:
        with self.lock:
            return [dict(r) for r in self.conn.execute(sql, params).fetchall()]

    def one(self, sql: str, params: tuple | list = ()) -> dict | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def image_path(self, image_ref: str) -> Path:
        return self.root / image_ref

    def close(self) -> None:
        with self.lock:
            self.conn.close()


def dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True)
