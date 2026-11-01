"""建数据来源表（source / arms_source），纯 Python，不受 psql 编码影响。

用法（在项目根目录）：
    .venv\\Scripts\\python.exe backend\\seed_source.py

等价于跑 backend/sql/07_source.sql，但 Windows 中文控制台下 psql 常把
UTF-8 文件当 GBK 读，会报「编码 GBK 的字符在编码 UTF8 没有对应值」。
这个脚本绕开 psql，直接走 psycopg。
"""
from __future__ import annotations

import sys

from bootstrap import connect

DDL = [
    """
    CREATE TABLE IF NOT EXISTS source (
      source_id  integer PRIMARY KEY,
      name       text NOT NULL,
      url        text,
      version    text,
      license    text
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS arms_source (
      armsid       text NOT NULL REFERENCES arms(armsid) ON DELETE CASCADE,
      source_id    integer NOT NULL REFERENCES source(source_id) ON DELETE CASCADE,
      original_id  text,
      original_url text,
      fetched_at   timestamptz NOT NULL DEFAULT now(),
      PRIMARY KEY (armsid, source_id)
    );
    """,
]

SEED_SOURCE = """
INSERT INTO source (source_id, name, url, version, license)
VALUES (1, %s, %s, %s, %s)
ON CONFLICT (source_id) DO UPDATE
  SET name = EXCLUDED.name,
      url = EXCLUDED.url,
      version = EXCLUDED.version,
      license = EXCLUDED.license;
"""

SEED_LINKS = """
INSERT INTO arms_source (armsid, source_id, original_id, original_url)
SELECT a.armsid, 1, a.armsid, NULL FROM arms a
ON CONFLICT DO NOTHING;
"""

SOURCE_ROW = (
    "ArmsBank 灰烬战线情报枢纽",
    "https://arms-bank.com/",
    "v2026-11",
    "社区自制情报站；数据与图片版权归原权利人，本项目仅作课程学习使用",
)


def main() -> int:
    conn = connect()
    try:
        with conn.cursor() as cur:
            for ddl in DDL:
                cur.execute(ddl)
            cur.execute(SEED_SOURCE, SOURCE_ROW)
            cur.execute(SEED_LINKS)
        conn.commit()

        with conn.cursor() as cur:
            cur.execute("SELECT count(*) AS n FROM source")
            n_source = cur.fetchone()["n"]
            cur.execute("SELECT count(*) AS n FROM arms_source")
            n_links = cur.fetchone()["n"]
        print(f"source = {n_source} 行, arms_source = {n_links} 行")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
