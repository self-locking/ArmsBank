"""建库向导：不依赖 psql，只用 Python + CSV 把四张基础表建好并灌好数据。

为什么需要它：backend/sql/01_schema_base.sql 用了 psql 专有的 \\copy 元命令，
在 Render / Docker / 任何没有 psql 客户端的环境里跑不了。这个脚本做同一件事：
建表 → 从 data/*.csv 读数据 → 派生 arms_jobs。

其余表（arms_equip / arms_images / arms_skills / plugins / frameworks /
enemies / enemy_skills / azbans / arms_ammo）仍然由 backend/app.py 在首次
访问时按 CSV 自动建表。

用法：
    python backend/bootstrap.py            # 建表 + 灌数（已建过就跳过）
    python backend/bootstrap.py --force    # 先清空四张基础表再重灌
"""
from __future__ import annotations

import csv
import os
import sys

import psycopg
from psycopg.rows import dict_row

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.environ.get("ARMSBANK_DATA_DIR") or os.path.join(ROOT_DIR, "data")

DDL = [
    """
    CREATE TABLE IF NOT EXISTS characters (
      charid      text PRIMARY KEY,
      name        text NOT NULL,
      rarity      integer,
      affiliation text,
      college     text,
      number      text,
      description text,
      arms_count  integer,
      variants    text,
      arms_ids    text
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS arms (
      armsid      text PRIMARY KEY,
      name        text NOT NULL,
      charid      text NOT NULL REFERENCES characters(charid) ON DELETE CASCADE,
      variant     text,
      number      text,
      arms_type   text,
      size        text,
      available_job_ids text,
      hp          integer,
      armor       integer,
      mobility    integer,
      ground_fire integer,
      air_fire    integer,
      hit         integer,
      penetration integer,
      luck        integer,
      reaction    integer,
      crit_chance numeric(8,2) DEFAULT 0,
      crit_damage numeric(8,2) DEFAULT 0,
      ap          integer,
      cp_recovery integer,
      presence    integer,
      exclusive_skill text,
      dolls_skill     text,
      arms_skill      text
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS jobs (
      jobid            integer PRIMARY KEY,
      name             text NOT NULL,
      level            integer,
      hp_pct           numeric(8,2) DEFAULT 0,
      ground_fire_pct  numeric(8,2) DEFAULT 0,
      air_fire_pct     numeric(8,2) DEFAULT 0,
      armor_pct        numeric(8,2) DEFAULT 0,
      penetration_pct  numeric(8,2) DEFAULT 0,
      hit              numeric(8,2) DEFAULT 0,
      mobility         numeric(8,2) DEFAULT 0,
      luck             numeric(8,2) DEFAULT 0,
      crit_pct         numeric(8,2) DEFAULT 0,
      crit_damage_pct  numeric(8,2) DEFAULT 0,
      presence         numeric(8,2) DEFAULT 0,
      cp_recovery_pct  numeric(8,2) DEFAULT 0,
      skills           text
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS arms_jobs (
      armsid text    NOT NULL REFERENCES arms(armsid) ON DELETE CASCADE,
      jobid  integer NOT NULL REFERENCES jobs(jobid)  ON DELETE CASCADE,
      PRIMARY KEY (armsid, jobid)
    );
    """,
    "CREATE INDEX IF NOT EXISTS arms_charid_idx   ON arms(charid);",
    "CREATE INDEX IF NOT EXISTS arms_jobs_job_idx ON arms_jobs(jobid);",
]

CHARACTER_COLS = ["charid", "name", "rarity", "affiliation", "college",
                  "number", "description", "arms_count", "variants", "arms_ids"]
ARMS_COLS = ["armsid", "name", "charid", "variant", "number", "arms_type", "size",
             "available_job_ids", "hp", "armor", "mobility", "ground_fire", "air_fire",
             "hit", "penetration", "luck", "reaction", "crit_chance", "crit_damage",
             "ap", "cp_recovery", "presence", "exclusive_skill", "dolls_skill", "arms_skill"]
JOB_COLS = ["jobid", "name", "level", "hp_pct", "ground_fire_pct", "air_fire_pct",
            "armor_pct", "penetration_pct", "hit", "mobility", "luck", "crit_pct",
            "crit_damage_pct", "presence", "cp_recovery_pct", "skills"]
INT_COLS = {"rarity", "arms_count", "hp", "armor", "mobility", "ground_fire", "air_fire",
            "hit", "penetration", "luck", "reaction", "ap", "cp_recovery", "presence",
            "jobid", "level"}
NUM_COLS = {"crit_chance", "crit_damage", "hp_pct", "ground_fire_pct", "air_fire_pct",
            "armor_pct", "penetration_pct", "crit_pct", "crit_damage_pct", "cp_recovery_pct"}

DERIVE_ARMS_JOBS = """
INSERT INTO arms_jobs (armsid, jobid)
SELECT a.armsid, j.jobid
FROM arms a
CROSS JOIN LATERAL unnest(
         string_to_array(regexp_replace(a.available_job_ids, '[^0-9]+', ' ', 'g'), ' ')
       ) AS x(jid)
JOIN jobs j ON j.jobid = NULLIF(x.jid, '')::integer
WHERE a.available_job_ids IS NOT NULL
  AND a.available_job_ids <> ''
ON CONFLICT DO NOTHING;
"""

def connect():
    params = {
        "host": os.environ.get("ARMSBANK_DB_HOST", "localhost"),
        "port": int(os.environ.get("ARMSBANK_DB_PORT", "5432")),
        "dbname": os.environ.get("ARMSBANK_DB_NAME", "armsbank_db"),
        "user": os.environ.get("ARMSBANK_DB_USER", "postgres"),
        "connect_timeout": 10,
        "row_factory": dict_row,
    }
    pwd = os.environ.get("ARMSBANK_DB_PASSWORD")
    if pwd:
        params["password"] = pwd
    return psycopg.connect(**params)

def _cast(col, raw):
    v = (raw or "").strip()
    if v == "":
        return None
    if col in NUM_COLS:
        try:
            return float(v)
        except ValueError:
            return None
    if col in INT_COLS:
        try:
            return int(float(v))
        except ValueError:
            return None
    return raw

def read_csv(name):
    path = os.path.join(DATA_DIR, name)
    if not os.path.exists(path):
        raise FileNotFoundError("找不到数据文件：" + path)
    with open(path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.reader(fh)
        header = next(reader, None)
        if not header:
            raise ValueError(name + " 是空文件")
        return [row for row in reader if row and any(c.strip() for c in row)]

def load_table(cur, table, csv_name, cols):
    rows = read_csv(csv_name)
    placeholders = ", ".join(["%s"] * len(cols))
    sql = "INSERT INTO {} ({}) VALUES ({}) ON CONFLICT DO NOTHING".format(
        table, ", ".join(cols), placeholders)
    payload = []
    for row in rows:
        if len(row) < len(cols):
            row = row + [""] * (len(cols) - len(row))
        payload.append(tuple(_cast(c, row[i]) for i, c in enumerate(cols)))
    cur.executemany(sql, payload)
    return len(payload)

def table_count(cur, table):
    cur.execute("SELECT count(*) AS n FROM " + table)
    return int(cur.fetchone()["n"])

def bootstrap(force=False, verbose=True):
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_lock(987654321) AS got")
            if not cur.fetchone()["got"]:
                if verbose:
                    print("另一个进程正在初始化，跳过")
                return False
            try:
                for ddl in DDL:
                    cur.execute(ddl)
                conn.commit()
                if force:
                    cur.execute("TRUNCATE arms_jobs, arms, characters, jobs CASCADE")
                    conn.commit()
                wrote = False
                if table_count(cur, "characters") == 0:
                    n = load_table(cur, "characters", "armsbank_角色表.csv", CHARACTER_COLS)
                    if verbose:
                        print("characters  +{}".format(n))
                    wrote = True
                if table_count(cur, "jobs") == 0:
                    n = load_table(cur, "jobs", "armsbank_职能表.csv", JOB_COLS)
                    if verbose:
                        print("jobs        +{}".format(n))
                    wrote = True
                if table_count(cur, "arms") == 0:
                    n = load_table(cur, "arms", "armsbank_机体表.csv", ARMS_COLS)
                    if verbose:
                        print("arms        +{}".format(n))
                    wrote = True
                cur.execute(DERIVE_ARMS_JOBS)
                conn.commit()
                if verbose:
                    print("行数：characters={} arms={} jobs={} arms_jobs={}".format(
                        table_count(cur, "characters"), table_count(cur, "arms"),
                        table_count(cur, "jobs"), table_count(cur, "arms_jobs")))
                return wrote
            finally:
                cur.execute("SELECT pg_advisory_unlock(987654321)")
                conn.commit()
    finally:
        conn.close()

def main():
    force = "--force" in sys.argv
    try:
        bootstrap(force=force)
    except Exception as exc:
        print("初始化失败：{}".format(exc), file=sys.stderr)
        return 1
    return 0

if __name__ == "__main__":
    raise SystemExit(main())

