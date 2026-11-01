-- ============================================================
--  07_source.sql —— 数据来源与可追溯（第 4 章 4.6 节）
--
--  source        数据源登记表
--  arms_source   机体 ↔ 来源 的关联（记录原始编号与采集时间）
--
--  在 01_schema_base.sql 之后执行：
--      psql -d armsbank_db -U postgres -f backend/sql/07_source.sql
--  也可以由 backend/seed_source.py 幂等执行。
-- ============================================================

\encoding UTF8
BEGIN;

CREATE TABLE IF NOT EXISTS source (
  source_id  integer PRIMARY KEY,
  name       text NOT NULL,
  url        text,
  version    text,
  license    text
);

CREATE TABLE IF NOT EXISTS arms_source (
  armsid       text NOT NULL REFERENCES arms(armsid) ON DELETE CASCADE,
  source_id    integer NOT NULL REFERENCES source(source_id) ON DELETE CASCADE,
  original_id  text,
  original_url text,
  fetched_at   timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (armsid, source_id)
);

INSERT INTO source (source_id, name, url, version, license)
VALUES (1,
        'ArmsBank 灰烬战线情报枢纽',
        'https://arms-bank.com/',
        'v2026-11',
        '社区自制情报站；数据与图片版权归原权利人，本项目仅作课程学习使用')
ON CONFLICT (source_id) DO NOTHING;

-- 逐台机体登记来源（原始编号 = 机体ID；原站未提供可直链的单体地址，故 original_url 留空）
INSERT INTO arms_source (armsid, source_id, original_id, original_url)
SELECT a.armsid, 1, a.armsid, NULL
FROM arms a
ON CONFLICT DO NOTHING;

COMMIT;

SELECT 'source' AS 表, count(*) FROM source
UNION ALL SELECT 'arms_source', count(*) FROM arms_source;
