"""
Optional step - load the cleaned data into MySQL (view it in MySQL Workbench)
============================================================================
1. Copy `.env.example` to `.env` and fill in your MySQL password.
2. Run the pipeline first:   python src/run_pipeline.py
3. Then:                     python src/load_to_mysql.py

Concepts
--------
* SQLAlchemy engine: one object that knows how to talk to MySQL. pandas'
  `to_sql` uses it to insert a DataFrame as rows.
* We create the tables with our own schema (sql/01_schema.sql) instead of
  letting pandas guess, so we get proper keys, types and indexes.
* Credentials live in `.env`, which is git-ignored, so the password never
  ends up on GitHub.
* Once this has run, the Streamlit app reads from MySQL automatically
  (see src/db.py). Restart the app after loading.
"""
from pathlib import Path

from sqlalchemy import create_engine, text

from config import ROOT
from db import MYSQL_DB as DB
from db import build_tables, mysql_url


def run_sql_file(engine, path: Path) -> None:
    statements = [s.strip() for s in path.read_text().split(";")]
    with engine.begin() as conn:
        for s in statements:
            body = "\n".join(l for l in s.splitlines() if not l.strip().startswith("--")).strip()
            if body:
                conn.execute(text(body))


def main() -> None:
    server = create_engine(mysql_url(database=None))
    run_sql_file(server, ROOT / "sql" / "01_schema.sql")
    engine = create_engine(mysql_url())

    # Same table split the app's SQLite fallback uses (src/db.py), so both
    # databases always hold identical data. Order matters: dim_shop first,
    # because the other tables have foreign keys pointing at it.
    for name, df in build_tables().items():
        df.to_sql(name, engine, if_exists="append", index=False, chunksize=5000, method="multi")
        print(f"Loaded {name}: {len(df):,} rows")
    print(f"\nDone. Open MySQL Workbench -> schema '{DB}' and run sql/02_analysis_queries.sql")
    print("Restart the Streamlit app and the sidebar will say 'Data source: MySQL'.")


if __name__ == "__main__":
    main()
