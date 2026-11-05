"""
Data access layer: every dashboard filter becomes a SQL query
=============================================================
The Streamlit app never filters raw data itself. It asks this module,
and this module asks the database with a parameterised SQL query.
The database does the WHERE / JOIN / GROUP BY and returns only the
rows the charts need.

Two databases, same SQL
-----------------------
* MySQL (`telangana_pds`): the real one. Load it with
  `python src/load_to_mysql.py` after filling in `.env`.
* SQLite (data/processed/pds_local.sqlite): a local copy that is
  built automatically from the parquet files when MySQL can't be
  reached, so the app still runs on a laptop without MySQL.
  The queries are written in plain ANSI SQL, so both engines run
  exactly the same text.

Choose with PDS_DB_BACKEND in .env: auto (default: MySQL, falling back
to SQLite), mysql (MySQL only, fail loudly) or sqlite.

Concepts
--------
* Parameterised query: values go in as `:year`, `:districts` and are
  sent to the database separately from the SQL text. The driver never
  pastes user input into SQL, so SQL injection is impossible.
* Expanding parameter: `distName IN :districts` with a Python list.
  SQLAlchemy expands it to `IN (%s, %s, ...)` with one slot per item.
* Star schema: dim_shop (who/where), fact_shop_month (what happened),
  shop_year_clusters (model output). Queries JOIN them on the key
  (distCode, shopNo).
* Indexes: (year, persona) and (year, anomaly_flag) on the cluster
  table let the database jump straight to "2024 + this persona"
  instead of scanning every row.
"""
import os
from decimal import Decimal
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import bindparam, create_engine, text
from sqlalchemy.engine import URL, Engine

from config import PROCESSED_DIR, ROOT

load_dotenv(ROOT / ".env")

MYSQL_DB = "telangana_pds"
SQLITE_PATH = PROCESSED_DIR / "pds_local.sqlite"
SOURCE_FILES = [PROCESSED_DIR / "master_monthly.parquet",
                PROCESSED_DIR / "shop_year_clusters.parquet",
                PROCESSED_DIR / "cluster_profiles.csv"]

DIM_COLS = ["distName", "officeCode", "officeName", "area_type", "address",
            "latitude", "longitude", "fpsStatus", "fpsType"]
FACT_COLS = ["distCode", "shopNo", "year", "month", "noOfRcs", "noOfTrans", "totalRcs",
             "totalUnits", "rcNfsaAay", "otherShopTransCnt", "rice", "wheat", "sugar"]
CLUSTER_COLS = ["distCode", "shopNo", "year", "total_trans", "portability_trans",
                "avg_cards", "avg_monthly_trans", "utilization_ratio", "portability_share",
                "rice_per_trans", "wheat_share", "aay_share", "trans_cv", "active_month_share",
                "pc1", "pc2", "pc3", "cluster", "persona", "dbscan_noise",
                "z_utilization_ratio", "z_rice_per_trans", "peer_z_max",
                "peer_outlier", "anomaly_flag", "portability_hub"]
BOOL_COLS = ["dbscan_noise", "peer_outlier", "anomaly_flag", "portability_hub"]
# Only these column names may be put into SQL text (see peer_averages).
FEATURE_COLS = {"utilization_ratio", "portability_share", "rice_per_trans", "wheat_share",
                "aay_share", "trans_cv", "active_month_share", "avg_cards"}

SQLITE_INDEXES = [
    "CREATE UNIQUE INDEX pk_dim ON dim_shop (distCode, shopNo)",
    "CREATE INDEX idx_dist ON dim_shop (distName)",
    "CREATE UNIQUE INDEX pk_fact ON fact_shop_month (distCode, shopNo, year, month)",
    "CREATE UNIQUE INDEX pk_cluster ON shop_year_clusters (distCode, shopNo, year)",
    "CREATE INDEX idx_year_persona ON shop_year_clusters (year, persona)",
    "CREATE INDEX idx_year_flag ON shop_year_clusters (year, anomaly_flag)",
]


# ---------------------------------------------------------------- building
def build_tables() -> dict[str, pd.DataFrame]:
    """Split the pipeline outputs into the star-schema tables."""
    master = pd.read_parquet(PROCESSED_DIR / "master_monthly.parquet")
    shops = pd.read_parquet(PROCESSED_DIR / "shop_year_clusters.parquet")
    profiles = pd.read_csv(PROCESSED_DIR / "cluster_profiles.csv")
    # A shop's name/location can change over time; keep the latest one.
    dim = (master.sort_values(["year", "month"])
           .groupby(["distCode", "shopNo"], as_index=False)[DIM_COLS].last())
    return {"dim_shop": dim,
            "fact_shop_month": master[FACT_COLS],
            "shop_year_clusters": shops[CLUSTER_COLS],
            "cluster_profiles": profiles}


def mysql_url(database: str | None = MYSQL_DB) -> URL:
    # URL.create escapes special characters (@, #, :) in the password.
    return URL.create(
        "mysql+pymysql",
        username=os.getenv("MYSQL_USER", "root"),
        password=os.getenv("MYSQL_PASSWORD", ""),
        host=os.getenv("MYSQL_HOST", "localhost"),
        port=int(os.getenv("MYSQL_PORT", "3306")),
        database=database,
    )


def sqlite_is_fresh(path: Path = SQLITE_PATH) -> bool:
    if not path.exists():
        return False
    return path.stat().st_mtime >= max(f.stat().st_mtime for f in SOURCE_FILES)


def build_sqlite(path: Path = SQLITE_PATH) -> None:
    """Write the star schema into a SQLite file (built in a temp file, then swapped in)."""
    tmp = path.with_suffix(".building")
    tmp.unlink(missing_ok=True)
    eng = create_engine(f"sqlite:///{tmp.as_posix()}")
    for name, df in build_tables().items():
        df.to_sql(name, eng, index=False, chunksize=20_000)
    with eng.begin() as conn:
        for stmt in SQLITE_INDEXES:
            conn.execute(text(stmt))
    eng.dispose()
    tmp.replace(path)


def connect(backend: str | None = None) -> tuple[Engine, str, str]:
    """Return (engine, label, note). `note` explains a fallback, if one happened."""
    backend = (backend or os.getenv("PDS_DB_BACKEND", "auto")).strip().lower()
    note = ""
    if backend in ("auto", "mysql"):
        try:
            eng = create_engine(mysql_url(), pool_pre_ping=True, connect_args={"connect_timeout": 3})
            with eng.connect() as conn:
                n = conn.execute(text("SELECT COUNT(*) FROM shop_year_clusters")).scalar()
            if n:
                return eng, f"MySQL · {MYSQL_DB}", ""
            note = "MySQL tables are empty. Run `python src/load_to_mysql.py`."
        except Exception as e:  # no server, wrong password, no database yet...
            reason = str(getattr(e, "orig", e)).splitlines()[0][:160]
            note = f"MySQL not used: {reason}"
        if backend == "mysql":
            raise RuntimeError(note)

    if not all(f.exists() for f in SOURCE_FILES):
        raise FileNotFoundError("Model outputs not found. Run `python src/run_pipeline.py` first.")
    if not sqlite_is_fresh():
        build_sqlite()
    return create_engine(f"sqlite:///{SQLITE_PATH.as_posix()}"), "SQLite · local copy", note


# ---------------------------------------------------------------- querying
SHOP_SELECT = """
SELECT c.*, s.distName, s.officeName, s.area_type, s.latitude, s.longitude
FROM shop_year_clusters c
JOIN dim_shop s ON s.distCode = c.distCode AND s.shopNo = c.shopNo
"""


def _filters(year=None, districts=(), personas=(), only_anoms=False):
    """Turn sidebar choices into WHERE clauses + parameters (never string-pasted values)."""
    where, params, expanding = [], {}, []
    if year is not None:
        where.append("c.year = :year")
        params["year"] = int(year)
    if districts:
        where.append("s.distName IN :districts")
        params["districts"] = [str(d) for d in districts]
        expanding.append("districts")
    if personas:
        where.append("c.persona IN :personas")
        params["personas"] = [str(p) for p in personas]
        expanding.append("personas")
    if only_anoms:
        where.append("c.anomaly_flag = 1")
    return where, params, expanding


def _where(clauses: list[str]) -> str:
    return ("WHERE " + "\n  AND ".join(clauses)) if clauses else ""


def _fix_types(df: pd.DataFrame) -> pd.DataFrame:
    # MySQL returns DECIMAL/SUM() as Decimal and BOOLEAN as 0/1.
    for col in df.columns:
        if df[col].dtype == object and df[col].map(lambda v: isinstance(v, Decimal)).any():
            df[col] = df[col].astype(float)
    for col in BOOL_COLS:
        if col in df:
            df[col] = df[col].fillna(0).astype(bool)
    return df


class PDSData:
    def __init__(self, engine: Engine):
        self.engine = engine

    def _read(self, sql: str, params: dict | None = None, expanding=()) -> pd.DataFrame:
        stmt = text(sql)
        if expanding:
            stmt = stmt.bindparams(*(bindparam(n, expanding=True) for n in expanding))
        with self.engine.connect() as conn:
            return _fix_types(pd.read_sql(stmt, conn, params=params or {}))

    # -- sidebar
    def filter_options(self) -> dict:
        years = self._read("SELECT DISTINCT year FROM shop_year_clusters ORDER BY year")["year"]
        dists = self._read("SELECT DISTINCT distName FROM dim_shop "
                           "WHERE distName IS NOT NULL ORDER BY distName")["distName"]
        pers = self._read("SELECT DISTINCT persona FROM shop_year_clusters ORDER BY persona")["persona"]
        return {"years": [int(y) for y in years], "districts": list(dists), "personas": list(pers)}

    # -- the main filtered view (map, clusters, anomalies, KPIs)
    def shops_query(self, year, districts=(), personas=(), only_anoms=False):
        where, params, expanding = _filters(year, districts, personas, only_anoms)
        return SHOP_SELECT.strip() + "\n" + _where(where), params, expanding

    def shops(self, year, districts=(), personas=(), only_anoms=False) -> pd.DataFrame:
        return self._read(*self.shops_query(year, districts, personas, only_anoms))

    # -- shop lookup
    def shop_numbers(self, year) -> list[int]:
        df = self._read("SELECT DISTINCT shopNo FROM shop_year_clusters WHERE year = :year "
                        "ORDER BY shopNo", {"year": int(year)})
        return [int(s) for s in df["shopNo"]]

    def shop(self, year, shop_no) -> pd.Series | None:
        df = self._read(SHOP_SELECT + "WHERE c.year = :year AND c.shopNo = :shop LIMIT 1",
                        {"year": int(year), "shop": int(shop_no)})
        return None if df.empty else df.iloc[0]

    def peer_averages(self, year, persona, features) -> pd.Series:
        bad = set(features) - FEATURE_COLS
        if bad:  # column names can't be parameters, so allow-list them
            raise ValueError(f"Unknown feature columns: {bad}")
        cols = ", ".join(f"AVG({f}) AS {f}" for f in features)
        df = self._read(f"SELECT {cols} FROM shop_year_clusters "
                        "WHERE year = :year AND persona = :persona",
                        {"year": int(year), "persona": str(persona)})
        return df.iloc[0]

    def shop_history(self, dist_code, shop_no) -> pd.DataFrame:
        df = self._read("""
            SELECT year, month, noOfTrans, totalRcs, otherShopTransCnt
            FROM fact_shop_month
            WHERE distCode = :dist AND shopNo = :shop
            ORDER BY year, month""", {"dist": int(dist_code), "shop": int(shop_no)})
        return _add_date(df)

    # -- trends (aggregated inside the database: ~500k rows in, ~60 rows out)
    def trend_query(self, districts=(), personas=(), only_anoms=False):
        where, params, expanding = _filters(None, districts, personas, only_anoms)
        join_c = ("JOIN shop_year_clusters c ON c.distCode = f.distCode "
                  "AND c.shopNo = f.shopNo AND c.year = f.year") if (personas or only_anoms) else ""
        sql = f"""
SELECT f.year, f.month, s.area_type,
       SUM(f.noOfTrans)         AS trans,
       SUM(f.otherShopTransCnt) AS port,
       SUM(f.totalRcs)          AS cards
FROM fact_shop_month f
JOIN dim_shop s ON s.distCode = f.distCode AND s.shopNo = f.shopNo
{join_c}
{_where(where)}
GROUP BY f.year, f.month, s.area_type
ORDER BY f.year, f.month"""
        return sql, params, expanding

    def monthly_trend(self, districts=(), personas=(), only_anoms=False) -> pd.DataFrame:
        df = _add_date(self._read(*self.trend_query(districts, personas, only_anoms)))
        df["portability_share"] = df["port"] / df["trans"]
        df["utilization"] = df["trans"] / df["cards"]
        return df

    def persona_transitions(self, y0, y1, area, districts=()) -> pd.DataFrame:
        where, params, expanding = _filters(None, districts)
        where = ["a.year = :y0", "b.year = :y1", "s.area_type = :area"] + where
        params.update(y0=int(y0), y1=int(y1), area=str(area))
        return self._read(f"""
SELECT a.persona AS persona_from, b.persona AS persona_to, COUNT(*) AS shops
FROM shop_year_clusters a
JOIN shop_year_clusters b ON b.distCode = a.distCode AND b.shopNo = a.shopNo
JOIN dim_shop s ON s.distCode = a.distCode AND s.shopNo = a.shopNo
{_where(where)}
GROUP BY a.persona, b.persona""", params, expanding)


def _add_date(df: pd.DataFrame) -> pd.DataFrame:
    df["date"] = pd.to_datetime(dict(year=df["year"], month=df["month"], day=1))
    return df
