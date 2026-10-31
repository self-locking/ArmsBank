-- ============================================================
--  01_schema_base.sql —— 四张基础表（角色 / 机体 / 职能 / 机体-职能）
--
--  作用：从零建库。建表 + 从 data/ 里的 CSV 灌数据 + 派生关联表。
--  用法：在 submission/ 目录下执行（\copy 的相对路径以 psql 当前目录为准）
--        psql -d armsbank_db -U postgres -f sql/01_schema_base.sql
--
--  说明：
--    * 表结构与 armsbank_app/app.py 里用到的字段严格对应；
--    * "存在感"（presence）站点会展示，但本项目页面不显示，仅保留入库；
--    * 其它三张表（arms_equip / arms_images / arms_skills）见 02~04，
--      它们也可以由应用启动时自动建，二选一即可。
-- ============================================================

BEGIN;
SET client_encoding = 'UTF8';

DROP TABLE IF EXISTS arms_jobs;
DROP TABLE IF EXISTS arms;
DROP TABLE IF EXISTS characters;
DROP TABLE IF EXISTS jobs;

-- ---------------------------------------------------------- 角色
CREATE TABLE characters (
  charid      text PRIMARY KEY,          -- DOL1001
  name        text NOT NULL,             -- KV-1
  rarity      integer,                   -- 星级 1~3
  affiliation text,                      -- 学联
  college     text,                      -- 学院
  number      text,                      -- 编号 43S21
  description text,                      -- 简介（含换行）
  arms_count  integer,                   -- 机体数量
  variants    text,                      -- "原型:ARM100101 / 改型:ARM100102"
  arms_ids    text                       -- 机体ID列表（冗余列，应用未使用，保留原始数据）
);

-- ---------------------------------------------------------- 机体（主力表）
-- 这里存的是「本体值」；页面上的最终面板 = 本体 + 兵装 + 职能，不落库、实时算。
CREATE TABLE arms (
  armsid      text PRIMARY KEY,          -- ARM100101
  name        text NOT NULL,             -- KV-1 / KV-85
  charid      text NOT NULL REFERENCES characters(charid) ON DELETE CASCADE,
  variant     text,                      -- 原型 / 改型 / 特改型
  number      text,
  arms_type   text,                      -- 坦克 / 火炮 / ...
  size        text,                      -- 轻型 / 中型 / 重型
  available_job_ids text,                -- "2 / 3"（文本，应用侧再拆）

  hp          integer,                   -- 生命值
  armor       integer,                   -- 装甲
  mobility    integer,                   -- 机动
  ground_fire integer,                   -- 对地火力
  air_fire    integer,                   -- 对空火力
  hit         integer,                   -- 命中
  penetration integer,                   -- 贯通
  luck        integer,                   -- 幸运
  reaction    integer,                   -- 反应值（站点口径：只取兵装）
  crit_chance numeric(8,2) DEFAULT 0,    -- 暴击概率（站点口径：取兵装）
  crit_damage numeric(8,2) DEFAULT 0,    -- 暴击伤害

  ap          integer,                   -- 行动值 AP
  cp_recovery integer,                   -- CP 恢复值
  presence    integer,                   -- 存在感（页面不展示）

  exclusive_skill text,                  -- 专属技能名（" / " 分隔）
  dolls_skill     text,                  -- DOLLS 技能名
  arms_skill      text                   -- 改造技能名
);

-- ---------------------------------------------------------- 职能
CREATE TABLE jobs (
  jobid            integer PRIMARY KEY,  -- 2 = 突袭Ⅱ
  name             text NOT NULL,
  level            integer,              -- 职能等级

  -- 百分比项：乘在「兵装值」上（不是乘本体）
  hp_pct           numeric(8,2) DEFAULT 0,
  ground_fire_pct  numeric(8,2) DEFAULT 0,
  air_fire_pct     numeric(8,2) DEFAULT 0,
  armor_pct        numeric(8,2) DEFAULT 0,
  penetration_pct  numeric(8,2) DEFAULT 0,

  -- 定值项：直接相加
  hit              numeric(8,2) DEFAULT 0,
  mobility         numeric(8,2) DEFAULT 0,
  luck             numeric(8,2) DEFAULT 0,
  crit_pct         numeric(8,2) DEFAULT 0,
  crit_damage_pct  numeric(8,2) DEFAULT 0,

  presence         numeric(8,2) DEFAULT 0,   -- 站点是覆盖赋值，不参与相加
  cp_recovery_pct  numeric(8,2) DEFAULT 0,   -- 站点计算未使用，仅记录

  skills           text                      -- 职能技能名（" / " 分隔）
);

-- ---------------------------------------------------------- 机体 × 职能
CREATE TABLE arms_jobs (
  armsid text    NOT NULL REFERENCES arms(armsid) ON DELETE CASCADE,
  jobid  integer NOT NULL REFERENCES jobs(jobid)  ON DELETE CASCADE,
  PRIMARY KEY (armsid, jobid)
);

CREATE INDEX arms_charid_idx  ON arms(charid);
CREATE INDEX arms_jobs_job_idx ON arms_jobs(jobid);

-- ---------------------------------------------------------- 灌数据
-- CSV 第一行是表头，\copy 的 HEADER 会跳过它。
-- 角色表末尾的 兵种 / 体型 / 弹药 是给界面「大分组」用的派生列（库里的 arms、
-- arms_ammo 才是原始出处），所以这里显式列出列名，多出来的列直接忽略。
\copy characters (charid, name, rarity, affiliation, college, number, description, arms_count, variants, arms_ids) FROM 'data/armsbank_角色表.csv' CSV HEADER
\copy arms       FROM 'data/armsbank_机体表.csv' CSV HEADER
\copy jobs       FROM 'data/armsbank_职能表.csv' CSV HEADER

-- 关联表由 arms.available_job_ids 拆出来（"2 / 3" → 2、3）
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

COMMIT;

-- 自检：三张表行数应为 266 / 552 / 28
SELECT 'characters' AS 表, count(*) FROM characters
UNION ALL SELECT 'arms',    count(*) FROM arms
UNION ALL SELECT 'jobs',    count(*) FROM jobs
UNION ALL SELECT 'arms_jobs', count(*) FROM arms_jobs;