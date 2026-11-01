"""ArmsBank 本地查询面板。

数据表：characters / arms / arms_equip / arms_jobs / jobs（数据库 armsbank_db）。
依赖：Flask + psycopg(3)。

运行：
    $env:ARMSBANK_DB_PASSWORD = "你的密码"
    .venv\\Scripts\\python app.py         # 然后打开 http://127.0.0.1:5000

第一次运行前，先执行兵装表的建表脚本（否则面板数值只有本体）：
    psql -d armsbank_db -U postgres -f setup_arms_equip.sql

可用环境变量覆盖连接参数：
    ARMSBANK_DB_HOST / ARMSBANK_DB_PORT / ARMSBANK_DB_NAME / ARMSBANK_DB_USER

接口：
    GET /                      搜索页
    GET /c/<charid>            角色详情页
    GET /api/overview          三张表的规模
    GET /api/search?q=xxx      模糊搜索「角色名 / 机体名 / 编号」，结果按角色聚合
    GET /api/character/<id>    单个角色的完整数据
    GET /api/jobs              全部职能及其数值修正
"""

from __future__ import annotations

import csv
import os
import re
from decimal import Decimal

import psycopg
from flask import Flask, jsonify, render_template, request, send_from_directory
from psycopg.rows import dict_row

# ---------------------------------------------------------------- 目录
# 本文件在 backend/ 里：项目根是它的上一层，前端在 frontend/，数据在 data/
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FRONTEND_DIR = os.path.join(ROOT_DIR, "frontend")

app = Flask(
    __name__,
    template_folder=os.path.join(FRONTEND_DIR, "templates"),
    static_folder=os.path.join(FRONTEND_DIR, "static"),
    static_url_path="/static",
)
app.json.ensure_ascii = False
app.json.sort_keys = False

# ---------------------------------------------------------------- 字段定义

# 面板数值：(字段, 显示名, 单位, 计算方式, 兵装表列)
#   rate  = 本体 + 兵装 + trunc(兵装 × 职能%)   站点 databank.js:953-957
#   flat  = 本体 + 兵装 + 职能定值              站点 :950-952
#   equip = 兵装 + 职能定值（本体没有这一项）    站点 :948 / :961
#   base  = 本体（兵装与职能都不参与）
STAT_FIELDS = [
    ("hp", "生命", "", "rate", "hp"),
    ("armor", "装甲", "", "rate", "armor"),
    ("mobility", "机动", "", "flat", "dodge"),
    ("ground_fire", "对地火力", "", "rate", "power_ag"),
    ("air_fire", "对空火力", "", "rate", "power_aa"),
    ("hit", "命中", "", "flat", "hit"),
    ("penetration", "贯通", "", "rate", "penetration"),
    ("luck", "幸运", "", "flat", "luck"),
    ("reaction", "反应值", "", "equip", "reaction"),
    ("crit_chance", "暴击概率", "%", "equip", "crit_chance"),
    ("crit_damage", "暴击伤害", "%", "flat", "crit_damage"),
    ("ap", "行动值AP", "", "base", None),
    ("cp_recovery", "CP恢复值", "", "base", None),
]

STAT_KEYS = [f[0] for f in STAT_FIELDS]

# 职能修正：对应面板字段, 数据库列, 显示名, 类型(pct=百分比/flat=定值),
#           作用方式(mul=乘兵装 / add=直接加 / unused=站点未使用)
JOB_FIELDS = [
    ("hp", "hp_pct", "生命", "pct", "mul"),
    ("ground_fire", "ground_fire_pct", "对地火力", "pct", "mul"),
    ("air_fire", "air_fire_pct", "对空火力", "pct", "mul"),
    ("armor", "armor_pct", "装甲", "pct", "mul"),
    ("penetration", "penetration_pct", "贯通", "pct", "mul"),
    ("hit", "hit", "命中", "flat", "add"),
    ("mobility", "mobility", "机动", "flat", "add"),
    ("luck", "luck", "幸运", "flat", "add"),
    ("crit_chance", "crit_pct", "暴击概率", "pct", "add"),
    ("crit_damage", "crit_damage_pct", "暴击伤害", "pct", "add"),
    ("cp_recovery", "cp_recovery_pct", "CP恢复率", "pct", "unused"),
]

JOB_SPLIT = re.compile(r"[,，/、\s]+")
MAX_TOKENS = 6

BASE_DIR = ROOT_DIR  # 数据都在项目根的 data/ 下

# 兵装表：(列名, 类型) —— 与 setup_arms_equip.sql 一致
EQUIP_SCHEMA = [
    ("armsid", "text PRIMARY KEY"),
    ("hp", "integer NOT NULL DEFAULT 0"),
    ("power_ag", "integer NOT NULL DEFAULT 0"),
    ("power_aa", "integer NOT NULL DEFAULT 0"),
    ("armor", "integer NOT NULL DEFAULT 0"),
    ("dodge", "integer NOT NULL DEFAULT 0"),
    ("hit", "integer NOT NULL DEFAULT 0"),
    ("penetration", "integer NOT NULL DEFAULT 0"),
    ("luck", "integer NOT NULL DEFAULT 0"),
    ("reaction", "integer NOT NULL DEFAULT 0"),
    ("crit_chance", "integer NOT NULL DEFAULT 0"),
    ("crit_damage", "integer NOT NULL DEFAULT 0"),
]
EQUIP_COLUMNS = [c for c, _ in EQUIP_SCHEMA]

_equip_ready = None  # None=还没查过, True=表在, False=表缺

# 立绘目录：图片不进库，只把文件名存进 arms_images，由 Flask 从磁盘送出。
# 图片就在项目的 data/arms_images/ 里，和敌人贴图 data/enemy_art/ 并排。
IMAGE_DIR = os.environ.get("ARMSBANK_IMAGE_DIR") or os.path.join(BASE_DIR, "data", "arms_images")

_images_ready = None


class DBError(RuntimeError):
    """连接配置缺失等可读错误。"""


# ---------------------------------------------------------------- 数据库

def get_conn():
    params = {
        "host": os.environ.get("ARMSBANK_DB_HOST", "localhost"),
        "port": int(os.environ.get("ARMSBANK_DB_PORT", "5432")),
        "dbname": os.environ.get("ARMSBANK_DB_NAME", "armsbank_db"),
        "user": os.environ.get("ARMSBANK_DB_USER", "postgres"),
        "connect_timeout": 5,
        "row_factory": dict_row,
    }
    # 密码可选：未设置时交给 libpq 自己找（pgpass.conf / trust 认证）
    password = os.environ.get("ARMSBANK_DB_PASSWORD")
    if password:
        params["password"] = password
    return psycopg.connect(**params)


def _hint_for(message: str) -> str:
    low = message.lower()
    if "password" in low or "authentication" in low:
        return "　→ 请设置环境变量 ARMSBANK_DB_PASSWORD（例：$env:ARMSBANK_DB_PASSWORD=\"你的密码\"）"
    if "arms_equip" in low:
        return "　→ 面板数值需要兵装表，先执行 setup_arms_equip.sql"
    return ""


@app.errorhandler(DBError)
def handle_db_error(exc):
    return jsonify({"error": str(exc)}), 500


@app.errorhandler(psycopg.Error)
def handle_pg_error(exc):
    message = str(exc)
    return jsonify({"error": f"数据库错误：{message}{_hint_for(message)}"}), 500


def _find_equip_csv() -> str | None:
    """兵装表数据来源：环境变量 > 项目里的 data/。"""
    return _find_csv("armsbank_兵装表.csv", "ARMSBANK_EQUIP_CSV")


def _find_csv(name: str, env_var: str) -> str | None:
    candidates = []
    env = os.environ.get(env_var)
    if env:
        candidates.append(env)
    candidates += [
        os.path.join(BASE_DIR, name),
        os.path.join(BASE_DIR, "data", name),
        os.path.join(BASE_DIR, "..", "data", "csv", name),
        os.path.join(os.path.dirname(BASE_DIR), "data", "csv", name),
    ]
    for path in candidates:
        if os.path.isfile(path):
            return path
    return None


def ensure_images_table(conn) -> bool:
    """立绘映射表缺失（或为空）时，用随附 CSV 就地建表灌数。"""
    global _images_ready
    if _images_ready:
        return True

    cur = conn.cursor()
    exists = bool(cur.execute("SELECT to_regclass('arms_images') AS t").fetchone()["t"])
    if exists:
        if cur.execute("SELECT count(*) AS n FROM arms_images").fetchone()["n"]:
            _images_ready = True
            return True

    path = _find_csv("armsbank_立绘图.csv", "ARMSBANK_IMAGES_CSV")
    if not path:
        return False

    rows = []
    with open(path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.reader(fh)
        next(reader, None)
        for rec in reader:
            if len(rec) >= 3 and rec[0].strip():
                rows.append((rec[0].strip(), rec[1].strip(), rec[2].strip()))
    if not rows:
        return False

    ddl = ("CREATE TABLE IF NOT EXISTS arms_images (\n"
           "  armsid      text PRIMARY KEY,\n"
           "  normal_file text NOT NULL DEFAULT '',\n"
           "  battle_file text NOT NULL DEFAULT ''\n"
           ")")
    with conn.transaction():
        cur.execute(ddl)
        cur.executemany(
            "INSERT INTO arms_images (armsid, normal_file, battle_file) VALUES (%s, %s, %s) "
            "ON CONFLICT (armsid) DO NOTHING",
            rows,
        )

    _images_ready = True
    print(f"[立绘] 已建表 arms_images，写入 {len(rows)} 行（来源 {path}；图片目录 {IMAGE_DIR}）")
    return True


SKILLS_SCHEMA = [
    ("armsid", "text NOT NULL"),
    ("kind", "text NOT NULL"),
    ("jobid", "integer NOT NULL DEFAULT 0"),
    ("skillid", "text NOT NULL"),
    ("name", "text NOT NULL"),
    ("description", "text NOT NULL DEFAULT ''"),
    ("ap", "integer NOT NULL DEFAULT 0"),
    ("cp", "integer NOT NULL DEFAULT 0"),
    ("damage", "integer NOT NULL DEFAULT 0"),
    ("seq", "integer NOT NULL DEFAULT 0"),
]

_skills_ready = None


def ensure_skills_table(conn) -> bool:
    """技能表缺失（或为空）时，用随附 CSV 就地建表灌数。"""
    global _skills_ready
    if _skills_ready:
        return True

    cur = conn.cursor()
    exists = bool(cur.execute("SELECT to_regclass('arms_skills') AS t").fetchone()["t"])
    if exists and cur.execute("SELECT count(*) AS n FROM arms_skills").fetchone()["n"]:
        _skills_ready = True
        return True

    path = _find_csv("armsbank_技能表.csv", "ARMSBANK_SKILLS_CSV")
    if not path:
        return False

    rows = []
    with open(path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.reader(fh)
        next(reader, None)
        for rec in reader:
            if len(rec) < 9 or not rec[0].strip():
                continue
            kind = "job" if rec[1].strip() == "职能" else "other"
            rows.append((
                rec[0].strip(), kind, int(float(rec[2] or 0)), rec[3].strip(),
                rec[4].strip(), rec[5],
                int(float(rec[6] or 0)), int(float(rec[7] or 0)), int(float(rec[8] or 0)),
                int(float(rec[9] or 0)) if len(rec) > 9 else 0,
            ))
    if not rows:
        return False

    cols = ",\n  ".join(f"{name:<12}{typ}" for name, typ in SKILLS_SCHEMA)
    ddl = ("CREATE TABLE IF NOT EXISTS arms_skills (\n  " + cols +
           ",\n  PRIMARY KEY (armsid, kind, jobid, skillid)\n)")
    with conn.transaction():
        cur.execute(ddl)
        cur.executemany(
            "INSERT INTO arms_skills (armsid, kind, jobid, skillid, name, description,"
            " ap, cp, damage, seq) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
            " ON CONFLICT (armsid, kind, jobid, skillid) DO NOTHING",
            rows,
        )

    _skills_ready = True
    print(f"[技能] 已建表 arms_skills，写入 {len(rows)} 行（来源 {path}）")
    return True


# ---------------------------------------------------------------- 插件 / 框架

# 站点 ItemSet 里 kind=3_2 是性格插件、3_1 是知性框架（见 site/js/databank.js）
PLUGIN_STAT_FIELDS = [
    ("hitp", "命中", "%"),
    ("dodgep", "机动", "%"),
    ("luckp", "幸运", "%"),
    ("armorp", "装甲", "%"),
    ("armorpenetrationp", "贯通", "%"),
    ("critical", "暴击概率", "%"),
    ("critdamage", "暴击伤害", "%"),
]

PLUGIN_SCHEMA = [
    ("itemid", "text PRIMARY KEY"),
    ("base_name", "text NOT NULL"),
    ("name", "text NOT NULL"),
    ("tier", "integer NOT NULL DEFAULT 0"),
    ("rarity", "integer NOT NULL DEFAULT 0"),
    ("skill_level", "integer NOT NULL DEFAULT 0"),
    ("slot_type", "integer NOT NULL DEFAULT 0"),
    *[(col, "numeric NOT NULL DEFAULT 0") for col, _, _ in PLUGIN_STAT_FIELDS],
    ("description", "text NOT NULL DEFAULT ''"),
]

FRAMEWORK_SCHEMA = [
    ("itemid", "text PRIMARY KEY"),
    ("base_name", "text NOT NULL"),
    ("name", "text NOT NULL"),
    ("tier", "integer NOT NULL DEFAULT 0"),
    ("rarity", "integer NOT NULL DEFAULT 0"),
    ("skill_level", "integer NOT NULL DEFAULT 0"),
    ("char_level_req", "integer NOT NULL DEFAULT 0"),
    ("lock_types", "text NOT NULL DEFAULT ''"),
    ("unlock_levels", "text NOT NULL DEFAULT ''"),
    ("passive_name", "text NOT NULL DEFAULT ''"),
    ("description", "text NOT NULL DEFAULT ''"),
]

_plugins_ready = None
_frameworks_ready = None


def _ensure_lookup_table(conn, table: str, csv_name: str, env_var: str,
                         schema: list[tuple], parse_row, label: str) -> bool:
    """插件 / 框架这类小字典表：缺表或空表时用随附 CSV 建表灌数。"""
    cur = conn.cursor()
    exists = bool(cur.execute(f"SELECT to_regclass('{table}') AS t").fetchone()["t"])
    if exists:
        have = {r["column_name"] for r in cur.execute(
            "SELECT column_name FROM information_schema.columns"
            " WHERE table_schema = current_schema() AND table_name = %s", (table,)).fetchall()}
        # have 为空说明拿不到该表的列信息（比如非默认 schema），这时不做任何迁移，
        # 免得把好好的表清空。
        missing = [(name, typ) for name, typ in schema if name not in have] if have else []
        if missing:
            # 早期版本建的表少了列（比如插件后来加的 skill_level）：补列后清空重灌，
            # 数据源还是同一个 CSV，所以结果和全新安装一致。
            with conn.transaction():
                for name, typ in missing:
                    safe_typ = typ.replace("PRIMARY KEY", "")
                    cur.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {name} {safe_typ}")
                cur.execute(f"TRUNCATE {table}")
            print(f"[{label}] {table} 补列 {[m[0] for m in missing]}，重新灌数")
            exists = False
        elif cur.execute(f"SELECT count(*) AS n FROM {table}").fetchone()["n"]:
            return True

    path = _find_csv(csv_name, env_var)
    if not path:
        return False

    rows = []
    with open(path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.reader(fh)
        next(reader, None)
        for rec in reader:
            if not rec or not rec[0].strip():
                continue
            parsed = parse_row(rec)
            if parsed:
                rows.append(parsed)
    if not rows:
        return False

    cols = ",\n  ".join(f"{name:<20}{typ}" for name, typ in schema)
    cols_short = ", ".join(name for name, _ in schema)
    marks = ", ".join(["%s"] * len(schema))
    # 主键就是 schema 第一列（itemid / enemyid / skillid……），冲突目标得跟着走，
    # 写死 itemid 会让后加的表（敌人、敌人技能）建表直接报错。
    names = [name for name, _ in schema]
    pk = names[0]
    base_col = "base_name" if "base_name" in names else None
    with conn.transaction():
        cur.execute(f"CREATE TABLE IF NOT EXISTS {table} (\n  {cols}\n)")
        if base_col:
            cur.execute(f"CREATE INDEX IF NOT EXISTS {table}_base_idx ON {table}({base_col})")
        cur.executemany(
            f"INSERT INTO {table} ({cols_short}) VALUES ({marks})"
            f" ON CONFLICT ({pk}) DO NOTHING",
            rows,
        )

    print(f"[{label}] 已建表 {table}，写入 {len(rows)} 行（来源 {path}）")
    return True


def ensure_plugins_table(conn) -> bool:
    global _plugins_ready
    if _plugins_ready:
        return True

    def parse(rec):
        if len(rec) < len(PLUGIN_SCHEMA):
            return None
        return (
            rec[0].strip(), rec[1].strip(), rec[2].strip(),
            int(float(rec[3] or 0)), int(float(rec[4] or 0)), int(float(rec[5] or 0)),
            int(float(rec[6] or 0)),
            *(float(x or 0) for x in rec[7:7 + len(PLUGIN_STAT_FIELDS)]),
            rec[7 + len(PLUGIN_STAT_FIELDS)],
        )

    ok = _ensure_lookup_table(conn, "plugins", "armsbank_插件表.csv",
                              "ARMSBANK_PLUGINS_CSV", PLUGIN_SCHEMA, parse, "插件")
    if ok:
        _plugins_ready = True
    return ok


def ensure_frameworks_table(conn) -> bool:
    global _frameworks_ready
    if _frameworks_ready:
        return True

    def parse(rec):
        if len(rec) < len(FRAMEWORK_SCHEMA):
            return None
        return (
            rec[0].strip(), rec[1].strip(), rec[2].strip(),
            int(float(rec[3] or 0)), int(float(rec[4] or 0)),
            int(float(rec[5] or 0)), int(float(rec[6] or 0)),
            rec[7].strip(), rec[8].strip(), rec[9].strip(), rec[10],
        )

    ok = _ensure_lookup_table(conn, "frameworks", "armsbank_框架表.csv",
                              "ARMSBANK_FRAMEWORKS_CSV", FRAMEWORK_SCHEMA, parse, "框架")
    if ok:
        _frameworks_ready = True
    return ok


# ---------------------------------------------------------------- 敌人

ENEMY_SCHEMA = [
    ("enemyid", "text PRIMARY KEY"),
    ("base_name", "text NOT NULL"),
    ("name", "text NOT NULL"),
    ("level", "integer NOT NULL DEFAULT 0"),
    ("class_id", "integer NOT NULL DEFAULT 0"),
    ("class_name", "text NOT NULL DEFAULT ''"),
    ("rarity", "integer NOT NULL DEFAULT 0"),
    ("is_boss", "integer NOT NULL DEFAULT 0"),
    ("hp", "integer NOT NULL DEFAULT 0"),
    ("armor", "integer NOT NULL DEFAULT 0"),
    ("dodge", "integer NOT NULL DEFAULT 0"),
    ("ag_power", "integer NOT NULL DEFAULT 0"),
    ("aa_power", "integer NOT NULL DEFAULT 0"),
    ("hit", "integer NOT NULL DEFAULT 0"),
    ("penetration", "integer NOT NULL DEFAULT 0"),
    ("luck", "integer NOT NULL DEFAULT 0"),
    ("reaction", "integer NOT NULL DEFAULT 0"),
    ("ap", "integer NOT NULL DEFAULT 0"),
    ("skill_ids", "text NOT NULL DEFAULT ''"),
    ("skin_id", "text NOT NULL DEFAULT ''"),
    ("description", "text NOT NULL DEFAULT ''"),
]

ENEMY_SKILL_SCHEMA = [
    ("skillid", "text PRIMARY KEY"),
    ("name", "text NOT NULL DEFAULT ''"),
    ("skill_type", "integer NOT NULL DEFAULT 0"),
    ("role", "text NOT NULL DEFAULT ''"),
    ("ap_cost", "integer NOT NULL DEFAULT 0"),
    ("cp_cost", "integer NOT NULL DEFAULT 0"),
    ("damage", "numeric NOT NULL DEFAULT 0"),
    ("description", "text NOT NULL DEFAULT ''"),
]

_enemies_ready = None
_enemy_skills_ready = None


def ensure_enemies_table(conn) -> bool:
    """敌人表（5265 条，495 个基础名）：缺表时用随附 CSV 建表灌数。"""
    global _enemies_ready
    if _enemies_ready:
        return True

    def parse(rec):
        if len(rec) < len(ENEMY_SCHEMA):
            return None
        return (
            rec[0].strip(), rec[1].strip(), rec[2].strip(),
            int(float(rec[3] or 0)), int(float(rec[4] or 0)), rec[5].strip(),
            int(float(rec[6] or 0)), int(float(rec[7] or 0)),
            *(int(float(rec[i] or 0)) for i in range(8, 18)),
            rec[18].strip(), rec[19].strip(), rec[20],
        )

    ok = _ensure_lookup_table(conn, "enemies", "armsbank_敌人表.csv",
                              "ARMSBANK_ENEMIES_CSV", ENEMY_SCHEMA, parse, "敌人")
    if ok:
        _enemies_ready = True
    return ok


def ensure_enemy_skills_table(conn) -> bool:
    """敌人技能字典（2052 条）：敌人表只存 id，名字和描述在这里。"""
    global _enemy_skills_ready
    if _enemy_skills_ready:
        return True

    def parse(rec):
        if len(rec) < len(ENEMY_SKILL_SCHEMA):
            return None
        return (
            rec[0].strip(), rec[1].strip(), int(float(rec[2] or 0)), rec[3].strip(),
            int(float(rec[4] or 0)), int(float(rec[5] or 0)), float(rec[6] or 0), rec[7],
        )

    ok = _ensure_lookup_table(conn, "enemy_skills", "armsbank_敌人技能表.csv",
                              "ARMSBANK_ENEMY_SKILLS_CSV", ENEMY_SKILL_SCHEMA, parse, "敌人技能")
    if ok:
        _enemy_skills_ready = True
    return ok


# ---------------------------------------------------------------- 阿兹班石 / 机体弹药

# 站点 ItemSet.tbItemClassSuitList：90 条 = 31 条通用（LF/HF/… 按兵种+体型）+ 59 条专属（绑一台机体）
AZBAN_SCHEMA = [
    ("itemid", "text PRIMARY KEY"),
    ("base_name", "text NOT NULL"),
    ("name", "text NOT NULL"),
    ("tier", "integer NOT NULL DEFAULT 0"),
    ("class_id", "integer NOT NULL DEFAULT 0"),
    ("class_name", "text NOT NULL DEFAULT ''"),
    ("body", "text NOT NULL DEFAULT ''"),
    ("exclusive", "integer NOT NULL DEFAULT 0"),
    ("arms_id", "text NOT NULL DEFAULT ''"),
    ("char_name", "text NOT NULL DEFAULT ''"),
    ("hp", "integer NOT NULL DEFAULT 0"),
    ("power_ag", "integer NOT NULL DEFAULT 0"),
    ("power_aa", "integer NOT NULL DEFAULT 0"),
    ("armor", "integer NOT NULL DEFAULT 0"),
    ("hit", "integer NOT NULL DEFAULT 0"),
    ("dodge", "integer NOT NULL DEFAULT 0"),
    ("penetration", "integer NOT NULL DEFAULT 0"),
    ("luck", "integer NOT NULL DEFAULT 0"),
    ("reaction", "integer NOT NULL DEFAULT 0"),
    ("ap_recovery", "integer NOT NULL DEFAULT 0"),
    ("cp_recovery", "integer NOT NULL DEFAULT 0"),
    ("add_skills", "text NOT NULL DEFAULT ''"),
    ("repl_skills", "text NOT NULL DEFAULT ''"),
    ("rebate", "integer NOT NULL DEFAULT 0"),
    ("icon", "text NOT NULL DEFAULT ''"),
    ("description", "text NOT NULL DEFAULT ''"),
]

# 阿兹班石面板上的 11 项加成（站点 _azbanHtml 的顺序）
AZBAN_STAT_FIELDS = [
    ("hp", "生命值"),
    ("power_ag", "对地火力"),
    ("power_aa", "对空火力"),
    ("armor", "装甲"),
    ("hit", "命中"),
    ("dodge", "机动"),
    ("penetration", "贯通"),
    ("luck", "幸运"),
    ("reaction", "反应值"),
    ("ap_recovery", "AP恢复值"),
    ("cp_recovery", "CP恢复值"),
]

_azbans_ready = None
_arms_ammo_ready = None


def ensure_azbans_table(conn) -> bool:
    """阿兹班石（90 条，74 个基础名）：缺表时用随附 CSV 建表灌数。"""
    global _azbans_ready
    if _azbans_ready:
        return True

    def parse(rec):
        if len(rec) < len(AZBAN_SCHEMA):
            return None
        return (
            rec[0].strip(), rec[1].strip(), rec[2].strip(),
            int(float(rec[3] or 0)), int(float(rec[4] or 0)), rec[5].strip(),
            rec[6].strip(), int(float(rec[7] or 0)), rec[8].strip(), rec[9].strip(),
            *(int(float(rec[i] or 0)) for i in range(10, 21)),
            rec[21], rec[22], int(float(rec[23] or 0)), rec[24].strip(), rec[25],
        )

    ok = _ensure_lookup_table(conn, "azbans", "armsbank_阿兹班石表.csv",
                              "ARMSBANK_AZBANS_CSV", AZBAN_SCHEMA, parse, "阿兹班石")
    if ok:
        _azbans_ready = True
    return ok


def ensure_arms_ammo_table(conn) -> bool:
    """机体弹药（730 行，552 台机体）：一台机体可以带多种弹，所以是复合主键。"""
    global _arms_ammo_ready
    if _arms_ammo_ready:
        return True

    cur = conn.cursor()
    exists = bool(cur.execute("SELECT to_regclass('arms_ammo') AS t").fetchone()["t"])
    if exists and cur.execute("SELECT count(*) AS n FROM arms_ammo").fetchone()["n"]:
        _arms_ammo_ready = True
        return True

    path = _find_csv("armsbank_机体弹药表.csv", "ARMSBANK_ARMS_AMMO_CSV")
    if not path:
        return False

    rows = []
    with open(path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.reader(fh)
        next(reader, None)
        for rec in reader:
            if len(rec) < 2 or not rec[0].strip():
                continue
            rows.append((rec[0].strip(), rec[1].strip()))
    if not rows:
        return False

    with conn.transaction():
        cur.execute(
            "CREATE TABLE IF NOT EXISTS arms_ammo (\n"
            "  armsid text NOT NULL,\n"
            "  ammo   text NOT NULL,\n"
            "  PRIMARY KEY (armsid, ammo)\n"
            ")")
        cur.executemany(
            "INSERT INTO arms_ammo (armsid, ammo) VALUES (%s, %s) ON CONFLICT DO NOTHING",
            rows,
        )
    print(f"[弹药] 已建表 arms_ammo，写入 {len(rows)} 行（来源 {path}）")
    _arms_ammo_ready = True
    return True


# 弹药显示顺序：通常 → 穿甲 → 高爆 → 燃烧（与站点 bulletType 0~3 一致）
AMMO_ORDER = ("通常弹", "穿甲弹", "高爆弹", "燃烧弹")

AMMO_SQL = """
    SELECT ax.charid, m.ammo
    FROM arms ax
    JOIN arms_ammo m ON m.armsid = ax.armsid
    WHERE ax.charid = ANY(%s)
"""


def sort_ammo(names) -> str:
    """把角色名下机体的弹药合成 "通常弹 / 穿甲弹" 这种固定顺序的串。"""
    picked = [name for name in AMMO_ORDER if name in names]
    unknown = [name for name in names if name not in AMMO_ORDER]
    return " / ".join(picked + sorted(unknown))


def serialize_azban_group(base: str, rows: list[dict]) -> dict:
    """同一颗石头的 α/β/θ 型号合成一组。"""
    variants = []
    for r in rows:
        stats = [
            {"label": label, "value": int(r[col])}
            for col, label in AZBAN_STAT_FIELDS
            if r[col]
        ]
        adds = [x.strip() for x in str(r["add_skills"] or "").split(";") if x.strip()]
        repls = []
        for part in str(r["repl_skills"] or "").split(";"):
            if "→" not in part:
                continue
            old, new = part.split("→", 1)
            if old.strip() and new.strip():
                repls.append({"old": old.strip(), "new": new.strip()})
        variants.append({
            "itemid": r["itemid"],
            "name": r["name"],
            "tier": r["tier"],
            "stats": stats,
            "adds": adds,
            "repls": repls,
            "rebate": r["rebate"],
            "icon": r["icon"] or "",
            "description": r["description"] or "",
        })
    top = variants[-1]
    return {
        "base": base,
        "name": base,
        "rarity": 0,
        "class_id": rows[0]["class_id"],
        "class_name": rows[0]["class_name"] or "",
        "body": rows[0]["body"] or "",
        "exclusive": bool(rows[0]["exclusive"]),
        "char_name": rows[0]["char_name"] or "",
        "arms_id": rows[0]["arms_id"] or "",
        "variant_count": len(variants),
        "rebate": top["rebate"],
        "variants": variants,
    }


def serialize_enemy_group(base: str, rows: list[dict], skill_map: dict) -> dict:
    """同一个基础名下的所有 Lv 型号合成一组，rows 已按等级从高到低排。"""
    variants = []
    for r in rows:
        skills = []
        for sid in str(r["skill_ids"] or "").split(","):
            sid = sid.strip()
            s = skill_map.get(sid)
            if not s:
                continue
            skills.append({
                "skillid": sid,
                "name": s["name"],
                "role": s["role"],
                "ap": s["ap_cost"],
                "cp": s["cp_cost"],
                "damage": float(s["damage"]),
                "description": s["description"] or "",
            })
        variants.append({
            "enemyid": r["enemyid"],
            "name": r["name"],
            "level": r["level"],
            "rarity": r["rarity"],
            "class_id": r["class_id"],
            "class_name": r["class_name"],
            "boss": bool(r["is_boss"]),
            "ap": r["ap"],
            "stats": [
                {"label": "生命值", "value": r["hp"]},
                {"label": "装甲", "value": r["armor"]},
                {"label": "机动", "value": r["dodge"]},
                {"label": "对地火力", "value": r["ag_power"]},
                {"label": "对空火力", "value": r["aa_power"]},
                {"label": "命中", "value": r["hit"]},
                {"label": "贯通", "value": r["penetration"]},
                {"label": "幸运", "value": r["luck"]},
                {"label": "反应值", "value": r["reaction"]},
            ],
            "portrait": (str(r["skin_id"] or "") + ".webp") if r["skin_id"] else "",
            "description": r["description"] or "",
            "skills": skills,
        })
    variants.sort(key=lambda v: (-v["level"], v["enemyid"]))
    top = variants[0]
    return {
        "base": base,
        "name": base,
        "rarity": max(v["rarity"] for v in variants),
        "class_name": top["class_name"],
        "boss": any(v["boss"] for v in variants),
        "variant_count": len(variants),
        "portrait": top["portrait"],
        "variants": variants,
    }


def _int_list(text) -> list[int]:
    out = []
    for piece in str(text or "").replace("，", ",").split(","):
        piece = piece.strip()
        if piece.lstrip("-").isdigit():
            out.append(int(piece))
    return out


def _stage_list(text) -> list[dict]:
    """把 unlockLevels 拆成「升级阶梯」。

    原站格式是 `a,b,c,d,e|a,b,c,d,e|…`：一行 = 一级，行内 5 个数按
    插件类型 1~5 排，表示「该类型插件的等级累计要到多少」。
    满足第 1 行就是 Ⅰ 级，连续满足到第几行就是几级。
    """
    stages = []
    for row_text in str(text or "").split("|"):
        nums = _int_list(row_text)
        if not nums:
            continue
        stages.append({
            "level": len(stages) + 1,
            "reqs": [{"type": i + 1, "need": n} for i, n in enumerate(nums) if n > 0],
        })
    return stages


def serialize_plugin_group(base: str, rows: list[dict]) -> dict:
    tiers = []
    for r in rows:
        stats = [
            {"label": label, "value": float(r[col]), "unit": unit}
            for col, label, unit in PLUGIN_STAT_FIELDS
            if r[col]
        ]
        tiers.append({
            "tier": r["tier"],
            "itemid": r["itemid"],
            "name": r["name"],
            "rarity": r["rarity"],
            "level": r["skill_level"],
            "slot_type": r["slot_type"],
            "stats": stats,
            "description": r["description"] or "",
        })
    top = tiers[-1]
    return {
        "base": base,
        "name": base,
        "rarity": max(t["rarity"] for t in tiers),
        "level": top["level"],
        "slot_type": top["slot_type"],
        "tier_count": len(tiers),
        "summary": "  ".join(f"{s['label']} +{s['value']:g}{s['unit']}" for s in top["stats"]),
        "tiers": tiers,
    }


def serialize_framework_group(base: str, rows: list[dict]) -> dict:
    tiers = []
    for r in rows:
        slots = _int_list(r["lock_types"])
        stages = _stage_list(r["unlock_levels"])
        tiers.append({
            "tier": r["tier"],
            "itemid": r["itemid"],
            "name": r["name"],
            "rarity": r["rarity"],
            "skill_level": r["skill_level"],
            "char_level_req": r["char_level_req"],
            "slots": slots,
            "stages": stages,
            "max_level": len(stages),
            "locked_slots": sum(1 for x in slots if x),
            "unlock_text": str(r["unlock_levels"] or ""),
            "passive": r["passive_name"] or "",
            "description": r["description"] or "",
        })
    top = tiers[-1]
    return {
        "base": base,
        "name": base,
        "rarity": max(t["rarity"] for t in tiers),
        "tier_count": len(tiers),
        "max_level": top["max_level"],
        "slots": top["slots"],
        "slot_text": " · ".join("不限" if not x else f"类型{x}" for x in top["slots"]),
        "passive": top["passive"],
        "tiers": tiers,
    }


def _grouped_lookup(table: str, ensure, serialize) -> dict:
    keyword = (request.args.get("q") or "").strip()
    with get_conn() as conn:
        ready = ensure(conn)
        if not ready:
            return {"ready": False, "count": 0, "rows": 0, "items": []}
        sql = f"SELECT * FROM {table}"
        params: list = []
        if keyword:
            sql += " WHERE name ILIKE %s ESCAPE '\\'"
            params.append(like_pattern(keyword))
        sql += " ORDER BY base_name, tier, itemid"
        rows = conn.execute(sql, params).fetchall()

    groups: dict[str, list[dict]] = {}
    for r in rows:
        groups.setdefault(r["base_name"], []).append(r)
    items = [serialize(base, rs) for base, rs in groups.items()]
    items.sort(key=lambda it: (-it["rarity"], it["base"]))
    return {"ready": True, "count": len(items), "rows": len(rows), "items": items}


def _read_equip_csv(path: str) -> list[tuple]:
    rows = []
    with open(path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.reader(fh)
        next(reader, None)
        for rec in reader:
            if not rec or not rec[0].strip() or len(rec) < len(EQUIP_COLUMNS):
                continue
            rows.append((rec[0].strip(), *(int(float(x or 0)) for x in rec[1:len(EQUIP_COLUMNS)])))
    return rows


def ensure_equip_table(conn) -> bool:
    """兵装表缺失时，用随附 CSV 就地建表灌数（只做一次）。"""
    global _equip_ready
    if _equip_ready:
        return True

    cur = conn.cursor()
    exists = bool(cur.execute("SELECT to_regclass('arms_equip') AS t").fetchone()["t"])
    if exists:
        filled = cur.execute("SELECT count(*) AS n FROM arms_equip").fetchone()["n"]
        if filled:
            _equip_ready = True
            return True

    path = _find_equip_csv()
    if not path:
        return False
    rows = _read_equip_csv(path)
    if not rows:
        return False

    ddl = "CREATE TABLE IF NOT EXISTS arms_equip (\n  " + ",\n  ".join(
        f"{c} {t}" for c, t in EQUIP_SCHEMA) + "\n)"
    placeholders = ", ".join(["%s"] * len(EQUIP_COLUMNS))
    insert = (f"INSERT INTO arms_equip ({', '.join(EQUIP_COLUMNS)}) "
              f"VALUES ({placeholders}) ON CONFLICT (armsid) DO NOTHING")
    with conn.transaction():
        cur.execute(ddl)
        cur.executemany(insert, rows)

    _equip_ready = True
    print(f"[兵装表] 已{'建表并' if not exists else '补'}写入 arms_equip {len(rows)} 行（来源 {path}）")
    return True


# ---------------------------------------------------------------- 小工具

def num(value):
    """Decimal -> int/float，其它原样返回。"""
    if value is None:
        return None
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    return value


def num0(value):
    """None 当 0。"""
    v = num(value)
    return 0 if v is None else v


def like_pattern(token: str) -> str:
    escaped = token.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def split_tokens(q: str) -> list[str]:
    return [t for t in q.replace("　", " ").split() if t][:MAX_TOKENS]


# 角色库里可以模糊搜的技能名：只搜机体上的 专属 / DOLLS / 改造 技能，职能技能不参与
SEARCH_SKILL_COLUMNS = [
    ("exclusive_skill", "专属"),
    ("dolls_skill", "DOLLS"),
    ("arms_skill", "改造"),
]


def search_conditions(tokens: list[str]) -> tuple[str, list[str]]:
    """每个词都要命中：角色名 / 编号 / 机体名 / 机体编号 / 三种技能名之一。"""
    conditions, params = [], []
    for token in tokens:
        pattern = like_pattern(token)
        conditions.append(
            """
            (c.name ILIKE %s ESCAPE '\\'
             OR c.number ILIKE %s ESCAPE '\\'
             OR EXISTS (
                 SELECT 1 FROM arms ax
                 WHERE ax.charid = c.charid
                   AND (ax.name ILIKE %s ESCAPE '\\'
                        OR ax.number ILIKE %s ESCAPE '\\'
                        OR ax.exclusive_skill ILIKE %s ESCAPE '\\'
                        OR ax.dolls_skill     ILIKE %s ESCAPE '\\'
                        OR ax.arms_skill      ILIKE %s ESCAPE '\\')
             ))
            """
        )
        params.extend([pattern] * 7)

    where = "WHERE " + " AND ".join(conditions) if conditions else ""
    return where, params


def arm_skill_hits(arm: dict, lowered: list[str]) -> list[dict]:
    """这台机体上被关键词命中的技能名（专属 / DOLLS / 改造）。"""
    hits = []
    for key, label in SEARCH_SKILL_COLUMNS:
        for name in split_names(arm.get(key)):
            if any(token in name.lower() for token in lowered):
                item = {"kind": label, "name": name}
                if item not in hits:
                    hits.append(item)
    return hits


def parse_job_ids(text) -> list[int]:
    if not text:
        return []
    seen, ids = set(), []
    for part in JOB_SPLIT.split(str(text)):
        if part.isdigit():
            jid = int(part)
            if jid not in seen:
                seen.add(jid)
                ids.append(jid)
    return ids


def split_names(text) -> list[str]:
    if not text:
        return []
    return [part.strip() for part in JOB_SPLIT.split(str(text)) if part.strip()]


def equip_select() -> str:
    """生成 arms_equip 的列（LEFT JOIN 缺表/缺行都当 0）。"""
    parts = []
    for key, _, _, _, eq in STAT_FIELDS:
        if eq:
            parts.append(f"COALESCE(e.{eq}, 0) AS eq_{key}")
        else:
            parts.append(f"0 AS eq_{key}")
    return ",\n               ".join(parts)


EQUIP_SELECT = equip_select()
ARMS_COLUMNS = ",\n               ".join(f"a.{k}" for k in STAT_KEYS)


def serialize_stats(row) -> list[dict]:
    return [
        {
            "key": key,
            "label": label,
            "unit": unit,
            "apply": apply,
            "base": num(row.get(key)),
            "equip": num0(row.get(f"eq_{key}")),
        }
        for key, label, unit, apply, _ in STAT_FIELDS
    ]


# 角色列表允许排序的七项属性
SORT_STAT_KEYS = (
    "hp",
    "armor",
    "mobility",
    "ground_fire",
    "air_fire",
    "hit",
    "penetration",
)


def calculate_sort_stats(row, job):
    """
    计算一台机体在指定默认职能下的最终面板。
    计算规则与 character.html 中的 calc() 保持一致。
    """
    result = {}
    mods = job["mods"] if job else []

    for key, _, _, apply, _ in STAT_FIELDS:
        if key not in SORT_STAT_KEYS:
            continue

        base = num(row.get(key)) or 0
        equip = num0(row.get(f"eq_{key}"))

        mod = next(
            (
                m for m in mods
                if m["stat"] == key
                and m["value"] is not None
                and m["mode"] != "unused"
            ),
            None,
        )

        if apply == "rate":
            value = base + equip
            if mod:
                value += int(equip * mod["value"] / 100)

        elif apply == "flat":
            value = base + equip + (mod["value"] if mod else 0)

        else:
            value = base

        result[key] = num(value)

    return result


def serialize_job(row) -> dict:
    return {
        "jobid": row["jobid"],
        "name": row["name"],
        "level": row["level"],
        "skills": split_names(row.get("skills")),
        "mods": [
            {
                "stat": stat,
                "label": label,
                "kind": kind,
                "mode": mode,
                "value": num(row.get(column)),
            }
            for stat, column, label, kind, mode in JOB_FIELDS
        ],
    }


def _skill_entry(r) -> dict:
    return {
        "skillid": r["skillid"],
        "name": r["name"],
        "description": r["description"] or "",
        "ap": r["ap"] or 0,
        "cp": r["cp"] or 0,
        "damage": r["damage"] or 0,
    }


def serialize_arms(row, job_ids, jobs_by_id, skill_rows=None) -> dict:
    skills = {"other": [], "job": {}}
    if skill_rows:
        for r in skill_rows:
            entry = _skill_entry(r)
            if r["kind"] == "job":
                skills["job"].setdefault(str(r["jobid"]), []).append(entry)
            else:
                skills["other"].append(entry)
    else:
        # 技能表还没建好时退回纯名字（没有描述，页面不折叠）
        for key in ("exclusive_skill", "dolls_skill", "arms_skill"):
            for name in str(row.get(key) or "").split(" / "):
                if name.strip():
                    skills["other"].append({"skillid": "", "name": name.strip(),
                                            "description": "", "ap": 0, "cp": 0, "damage": 0})

    return {
        "armsid": row["armsid"],
        "name": row["name"],
        "variant": row["variant"],
        "number": row["number"],
        "arms_type": row["arms_type"],
        "size": row["size"],
        "job_ids": job_ids,
        "job_names": [jobs_by_id[j]["name"] for j in job_ids if j in jobs_by_id],
        "stats": serialize_stats(row),
        "portrait": {
            "normal": row.get("portrait_normal") or "",
            "battle": row.get("portrait_battle") or "",
        },
        "skills": skills,
    }


def arms_query(where: str, with_equip: bool = True, with_images: bool = True) -> str:
    if with_equip:
        join = "LEFT JOIN arms_equip e ON e.armsid = a.armsid"
        equip_cols = EQUIP_SELECT
    else:
        join = ""
        equip_cols = ",\n               ".join(f"0 AS eq_{k}" for k in STAT_KEYS)

    if with_images:
        join += "\n        LEFT JOIN arms_images i ON i.armsid = a.armsid"
        image_cols = ("COALESCE(i.normal_file, '') AS portrait_normal,\n"
                      "               COALESCE(i.battle_file, '') AS portrait_battle")
    else:
        image_cols = "'' AS portrait_normal,\n               '' AS portrait_battle"

    return f"""
        SELECT a.armsid, a.name, a.charid, a.variant, a.number, a.arms_type, a.size,
               a.available_job_ids,
               {ARMS_COLUMNS},
               {equip_cols},
               {image_cols},
               a.exclusive_skill, a.dolls_skill, a.arms_skill
        FROM arms a
        {join}
        {where}
    """


def fetch_jobs(cur) -> dict:
    cur.execute("SELECT * FROM jobs ORDER BY jobid")
    return {row["jobid"]: serialize_job(row) for row in cur.fetchall()}


def job_ids_for(row, link_jobs, jobs_by_id) -> list[int]:
    # arms_jobs 与 available_job_ids 取并集，两边任何一处有数都不丢
    ids = list(dict.fromkeys(
        link_jobs.get(row["armsid"], []) + parse_job_ids(row["available_job_ids"])
    ))
    return [jid for jid in ids if jid in jobs_by_id]


# ---------------------------------------------------------------- 页面

@app.get("/")
def index():
    return render_template("index.html")


@app.get("/c/<charid>")
def character_page(charid: str):
    return render_template("character.html", charid=charid)


@app.get("/stats")
def stats_page():
    return render_template("stats.html")


@app.get("/help")
def help_page():
    """系统内帮助页：数据来源、字段口径、检索用法。"""
    return render_template("help.html")


ENEMY_ART_DIR = os.environ.get("ARMSBANK_ENEMY_ART_DIR") or os.path.join(BASE_DIR, "data", "enemy_art")

# 页面背景图（随机）与立绘框的学联背景：都放在 data/ 下，不再放 frontend/static/
#   data/bg/      → /bg/<文件名>
#   data/nation/  → /nation/<文件名>
# 想放别处就设环境变量 ARMSBANK_BG_DIR / ARMSBANK_NATION_DIR
BG_DIR = os.environ.get("ARMSBANK_BG_DIR") or os.path.join(BASE_DIR, "data", "bg")
NATION_DIR = os.environ.get("ARMSBANK_NATION_DIR") or os.path.join(BASE_DIR, "data", "nation")


@app.get("/bg/<path:filename>")
def bg_file(filename: str):
    """页面随机背景图（webp，放在 data/bg）。"""
    if not os.path.isdir(BG_DIR):
        return jsonify({"error": f"背景图目录不存在：{BG_DIR}"}), 404
    return send_from_directory(BG_DIR, filename, max_age=604800)


@app.get("/nation/<path:filename>")
def nation_file(filename: str):
    """角色立绘框的学联背景（webp，放在 data/nation）。"""
    if not os.path.isdir(NATION_DIR):
        return jsonify({"error": f"学联背景目录不存在：{NATION_DIR}"}), 404
    return send_from_directory(NATION_DIR, filename, max_age=604800)


@app.get("/enemy_art/<path:filename>")
def enemy_art_file(filename: str):
    """敌人战斗贴图（webp，随附在 data/enemy_art）。"""
    if not os.path.isdir(ENEMY_ART_DIR):
        return jsonify({"error": f"敌人贴图目录不存在：{ENEMY_ART_DIR}"}), 404
    return send_from_directory(ENEMY_ART_DIR, filename, max_age=604800)


@app.get("/img/<path:filename>")
def portrait_file(filename: str):
    """立绘：图片留在磁盘上，这里只负责按文件名送出（带长缓存）。"""
    if not os.path.isdir(IMAGE_DIR):
        return jsonify({"error": f"立绘目录不存在：{IMAGE_DIR}"}), 404
    return send_from_directory(IMAGE_DIR, filename, max_age=604800)


# ---------------------------------------------------------------- 接口

@app.get("/api/overview")
def api_overview():
    with get_conn() as conn:
        ready = ensure_equip_table(conn)
        row = conn.execute(
            """
            SELECT (SELECT count(*) FROM characters) AS characters,
                   (SELECT count(*) FROM arms)       AS arms,
                   (SELECT count(*) FROM jobs)       AS jobs
            """
        ).fetchone()
        if ready:
            row["equip"] = conn.execute("SELECT count(*) AS n FROM arms_equip").fetchone()["n"]
        plugins_ok = ensure_plugins_table(conn)
        frameworks_ok = ensure_frameworks_table(conn)
        if plugins_ok:
            row["plugin_rows"] = conn.execute("SELECT count(*) AS n FROM plugins").fetchone()["n"]
            row["plugins"] = conn.execute(
                "SELECT count(DISTINCT base_name) AS n FROM plugins").fetchone()["n"]
        if frameworks_ok:
            row["framework_rows"] = conn.execute("SELECT count(*) AS n FROM frameworks").fetchone()["n"]
            row["frameworks"] = conn.execute(
                "SELECT count(DISTINCT base_name) AS n FROM frameworks").fetchone()["n"]
        enemies_ok = ensure_enemies_table(conn) and ensure_enemy_skills_table(conn)
        if enemies_ok:
            row["enemy_rows"] = conn.execute("SELECT count(*) AS n FROM enemies").fetchone()["n"]
            row["enemies"] = conn.execute(
                "SELECT count(DISTINCT base_name) AS n FROM enemies").fetchone()["n"]
        azbans_ok = ensure_azbans_table(conn)
        if azbans_ok:
            row["azban_rows"] = conn.execute("SELECT count(*) AS n FROM azbans").fetchone()["n"]
            row["azbans"] = conn.execute(
                "SELECT count(DISTINCT base_name) AS n FROM azbans").fetchone()["n"]
    return jsonify({**row, "equip_ready": bool(ready),
                    "plugins_ready": bool(plugins_ok),
                    "frameworks_ready": bool(frameworks_ok),
                    "enemies_ready": bool(enemies_ok),
                    "azbans_ready": bool(azbans_ok)})


# ---------------------------------------------------------------- 图表数据
# 只统计角色 / 机体 / 专属阿兹班石；敌人、框架、插件、通用阿兹班石不参与。

NATION_ORDER = ["黑十字帝国学联", "王立白蔷薇学联", "星尘联邦学联",
                "红十月同盟学联", "极东重钢学联", "维修会"]
BODY_ORDER = ["轻型", "中型", "重型"]


def _main_nation(raw) -> str:
    """「红十月同盟学联（可选：星尘联邦学联/维修会）」只取括号前的主学联。"""
    text = str(raw or "").split("（")[0].split("(")[0].strip()
    return text or "未知"


def _ordered(counter, order=()) -> list[dict]:
    """按给定顺序在前，其余按数量从多到少排在后面。"""
    head = [{"label": k, "value": counter[k]} for k in order if k in counter]
    rest = sorted(((k, v) for k, v in counter.items() if k not in order),
                  key=lambda kv: (-kv[1], str(kv[0])))
    return head + [{"label": k, "value": v} for k, v in rest]


@app.get("/api/stats")
def api_stats():
    from collections import Counter, defaultdict

    with get_conn() as conn:
        cur = conn.cursor()
        chars = cur.execute(
            """
            SELECT c.charid, c.name, c.rarity, c.affiliation,
                   (SELECT max(ax.arms_type) FROM arms ax WHERE ax.charid = c.charid) AS arms_type,
                   (SELECT max(ax.size)      FROM arms ax WHERE ax.charid = c.charid) AS body
            FROM characters c
            """
        ).fetchall()
        arms_total = cur.execute("SELECT count(*) AS n FROM arms").fetchone()["n"]

        ammo_rows = []
        if ensure_arms_ammo_table(conn):
            ammo_rows = cur.execute(
                """
                SELECT DISTINCT ax.charid, m.ammo
                FROM arms ax JOIN arms_ammo m ON m.armsid = ax.armsid
                """
            ).fetchall()

        ex_rows = []
        if ensure_azbans_table(conn):
            ex_rows = cur.execute(
                """
                SELECT z.base_name, z.char_name, ax.charid, c.affiliation
                FROM azbans z
                LEFT JOIN arms ax       ON ax.armsid = z.arms_id
                LEFT JOIN characters c  ON c.charid  = ax.charid
                WHERE z.exclusive = 1
                """
            ).fetchall()

    # —— 角色：学联 / 体型 / 兵种 / 稀有度 / 学联×体型
    nation_c, body_c, type_c, rarity_c = Counter(), Counter(), Counter(), Counter()
    nation_body: dict[str, Counter] = defaultdict(Counter)
    nation_total: Counter = Counter()
    for ch in chars:
        nation = _main_nation(ch["affiliation"])
        body = (ch["body"] or "").strip() or "未知"
        nation_c[nation] += 1
        nation_total[nation] += 1
        body_c[body] += 1
        type_c[(ch["arms_type"] or "").strip() or "未知"] += 1
        nation_body[nation][body] += 1
        if ch["rarity"] is not None:
            rarity_c[int(ch["rarity"])] += 1

    nations = [d["label"] for d in _ordered(nation_c, NATION_ORDER)]
    bodies = [d["label"] for d in _ordered(body_c, BODY_ORDER)]
    stacked = {
        "categories": nations,
        "series": [{"name": b, "values": [nation_body[n].get(b, 0) for n in nations]}
                   for b in bodies],
    }

    rarity = [{"label": "★" * r, "value": rarity_c[r]} for r in sorted(rarity_c, reverse=True)]

    # —— 弹药：一个角色带哪几种弹就在哪几种上各算一次
    ammo_c = Counter(r["ammo"] for r in ammo_rows if r["ammo"])
    ammo = _ordered(ammo_c, AMMO_ORDER)

    # —— 专属阿兹班石：按「持有者所在学联」统计
    by_name = {ch["name"]: ch for ch in chars}
    ex_stones: dict[str, set] = defaultdict(set)
    ex_chars: dict[str, set] = defaultdict(set)
    for r in ex_rows:
        aff = r["affiliation"] or (by_name.get(r["char_name"]) or {}).get("affiliation")
        nation = _main_nation(aff)
        ex_stones[nation].add(r["base_name"])
        ex_chars[nation].add(r["charid"] or r["char_name"])

    ex_nations = [n for n in NATION_ORDER if n in ex_chars] + sorted(
        (n for n in ex_chars if n not in NATION_ORDER), key=lambda n: -len(ex_chars[n]))
    ex_by_nation = [{"label": n, "value": len(ex_chars[n]), "extra": f"{len(ex_stones[n])} 颗"}
                    for n in ex_nations]
    ex_coverage = [{"label": n, "total": nation_total.get(n, 0), "with_ex": len(ex_chars.get(n, ()))}
                   for n in nations]

    return jsonify({
        "totals": {
            "characters": len(chars),
            "arms": arms_total,
            "exclusive_stones": sum(len(v) for v in ex_stones.values()),
            "exclusive_chars": sum(len(v) for v in ex_chars.values()),
        },
        "nation": _ordered(nation_c, NATION_ORDER),
        "body": _ordered(body_c, BODY_ORDER),
        "arms_type": _ordered(type_c),
        "rarity": rarity,
        "ammo": ammo,
        "nation_body": stacked,
        "ex_by_nation": ex_by_nation,
        "ex_coverage": ex_coverage,
    })


@app.get("/api/jobs")
def api_jobs():
    with get_conn() as conn:
        jobs = fetch_jobs(conn.cursor())
    return jsonify({"count": len(jobs), "jobs": list(jobs.values())})


@app.get("/api/plugins")
def api_plugins():
    """性格插件：按基础名合并档位（15 组 / 75 条）。只按名称匹配。"""
    return jsonify(_grouped_lookup("plugins", ensure_plugins_table, serialize_plugin_group))


@app.get("/api/frameworks")
def api_frameworks():
    """知性框架：按基础名合并档位（33 组 / 109 条）。只按名称匹配。"""
    return jsonify(_grouped_lookup("frameworks", ensure_frameworks_table, serialize_framework_group))


@app.get("/api/enemies")
def api_enemies():
    """敌人：按基础名合并 Lv 型号（495 组 / 5265 条）。只按名称匹配。"""
    keyword = (request.args.get("q") or "").strip()
    with get_conn() as conn:
        if not ensure_enemies_table(conn) or not ensure_enemy_skills_table(conn):
            return jsonify({"ready": False, "count": 0, "rows": 0, "items": []})
        sql = "SELECT * FROM enemies"
        params: list = []
        if keyword:
            sql += " WHERE name ILIKE %s ESCAPE '\\'"
            params.append(like_pattern(keyword))
        sql += " ORDER BY base_name, level DESC, enemyid"
        rows = conn.execute(sql, params).fetchall()
        wanted = sorted({sid.strip() for r in rows
                         for sid in str(r["skill_ids"] or "").split(",") if sid.strip()})
        skill_map = {}
        for chunk_start in range(0, len(wanted), 2000):
            chunk = wanted[chunk_start:chunk_start + 2000]
            for s in conn.execute(
                    "SELECT * FROM enemy_skills WHERE skillid = ANY(%s)", (chunk,)).fetchall():
                skill_map[s["skillid"]] = s

    groups: dict[str, list[dict]] = {}
    for r in rows:
        groups.setdefault(r["base_name"], []).append(r)
    items = [serialize_enemy_group(base, rs, skill_map) for base, rs in groups.items()]
    items.sort(key=lambda it: (-it["rarity"], -it["variant_count"], it["base"]))
    return jsonify({"ready": True, "count": len(items), "rows": len(rows), "items": items})


@app.get("/api/azbans")
def api_azbans():
    """阿兹班石：按基础名合并 α/β/θ 型号（74 组 / 90 条）。只按名称匹配。"""
    data = _grouped_lookup("azbans", ensure_azbans_table, serialize_azban_group)
    if data.get("ready"):
        # 通用的排前面：按兵种 id（战斗机→火炮）、体型（重→轻），专属的按名字跟在后面
        body_rank = {"重型": 1, "中型": 2, "轻型": 3}
        data["items"].sort(key=lambda it: (
            1 if it["exclusive"] else 0,
            it["class_id"],
            body_rank.get(it["body"], 9),
            it["base"]))
    return jsonify(data)


@app.get("/api/skill")
def api_skill():
    """按技能名取一条效果说明：阿兹班石抽屉里点技能名时用。数据在 azban_skills 表。"""
    name = (request.args.get("name") or "").strip()
    if not name:
        return jsonify({"found": False, "error": "缺少 name"}), 400
    try:
        with get_conn() as conn:
            row = conn.execute(
                "SELECT name, description, ap, cp, damage, target"
                " FROM azban_skills WHERE name = %s", (name,)).fetchone()
    except DBError:
        return jsonify({"found": False, "name": name, "hint": "数据库没配好，读不到技能效果。"})
    except psycopg.Error:
        return jsonify({"found": False, "name": name,
                        "hint": "库里还没有 azban_skills 表：跑一次 backend/sql/06_azban_skills.sql。"})
    if not row:
        return jsonify({"found": False, "name": name,
                        "hint": "这张表里没有这个技能名。"})
    return jsonify({
        "found": True,
        "name": row["name"],
        "desc": row["description"] or "",
        "ap": row["ap"] or 0,
        "cp": row["cp"] or 0,
        "dmg": row["damage"] or 0,
        "target": row["target"] or "",
    })


@app.get("/api/search")
def api_search():
    keyword = (request.args.get("q") or "").strip()
    tokens = split_tokens(keyword)
    try:
        limit = max(1, min(int(request.args.get("limit", 60)), 600))
    except ValueError:
        limit = 60

    where, params = search_conditions(tokens)
    arms_by_char: dict[str, list[dict]] = {}
    ammo_by_char: dict[str, list[str]] = {}
    sort_stats_by_char: dict[str, dict] = {}

    # 所有数据库读取都在连接上下文内完成；离开 with 后只处理 Python 数据。
    with get_conn() as conn:
        cur = conn.cursor()
        has_ammo = ensure_arms_ammo_table(conn)

        char_sql = f"""
            SELECT c.charid, c.name, c.rarity, c.affiliation, c.college,
                   c.number, c.description, c.arms_count, c.variants,
                   (SELECT max(ax.arms_type) FROM arms ax WHERE ax.charid = c.charid) AS arms_type,
                   (SELECT max(ax.size)      FROM arms ax WHERE ax.charid = c.charid) AS body
            FROM characters c
            {where}
            ORDER BY c.rarity DESC NULLS LAST, c.name
        """
        chars = cur.execute(char_sql, params).fetchall()

        if chars:
            charids = [row["charid"] for row in chars]

            # 读取兵装数值和职能数据，供角色卡片排序使用。
            equip_ready = ensure_equip_table(conn)
            jobs_by_id = fetch_jobs(cur)

            detailed_arms = cur.execute(
                arms_query(
                    """
                    WHERE a.charid = ANY(%s)
                    ORDER BY a.charid,
                             CASE a.variant
                                 WHEN '原型' THEN 0
                                 WHEN '改型' THEN 1
                                 WHEN '特改型' THEN 2
                                 ELSE 3
                             END,
                             a.armsid
                    """,
                    with_equip=equip_ready,
                    with_images=False,
                ),
                (charids,),
            ).fetchall()

            # 与角色详情接口使用相同的职能关联规则和顺序。
            link_jobs: dict[str, list[int]] = {}
            arm_ids = [row["armsid"] for row in detailed_arms]
            if arm_ids:
                links = cur.execute(
                    """
                    SELECT armsid, jobid
                    FROM arms_jobs
                    WHERE armsid = ANY(%s)
                    ORDER BY armsid, jobid
                    """,
                    (arm_ids,),
                ).fetchall()
                for link in links:
                    link_jobs.setdefault(link["armsid"], []).append(int(link["jobid"]))

            for row in detailed_arms:
                ids = job_ids_for(row, link_jobs, jobs_by_id)
                default_job = jobs_by_id[ids[0]] if ids else None

                arm_data = {
                    "armsid": row["armsid"],
                    "name": row["name"],
                    "variant": row["variant"],
                    "number": row["number"],
                    "exclusive_skill": row["exclusive_skill"] or "",
                    "dolls_skill": row["dolls_skill"] or "",
                    "arms_skill": row["arms_skill"] or "",
                    "sort_stats": calculate_sort_stats(row, default_job),
                }
                arms_by_char.setdefault(row["charid"], []).append(arm_data)

            # 默认机体口径与 api_character() 保持一致：改型 > 原型 > 第一台。
            for charid, arms in arms_by_char.items():
                default_arm = (
                    next((a for a in arms if a["variant"] == "改型"), None)
                    or next((a for a in arms if a["variant"] == "原型"), None)
                    or (arms[0] if arms else None)
                )
                if default_arm:
                    sort_stats_by_char[charid] = default_arm["sort_stats"]

            if has_ammo:
                for row in cur.execute(AMMO_SQL, (charids,)).fetchall():
                    bucket = ammo_by_char.setdefault(row["charid"], [])
                    if row["ammo"] not in bucket:
                        bucket.append(row["ammo"])

    lowered = [t.lower() for t in tokens]

    def name_hit(ch) -> bool:
        name = (ch["name"] or "").lower()
        return any(t in name for t in lowered)

    def arms_hit(ch) -> int:
        return sum(
            1 for arm in arms_by_char.get(ch["charid"], [])
            if any(t in (arm["name"] or "").lower() for t in lowered)
        )

    def skill_hit(ch) -> int:
        return sum(
            len(arm_skill_hits(arm, lowered))
            for arm in arms_by_char.get(ch["charid"], [])
        )

    ranked = sorted(
        chars,
        key=lambda ch: (
            not name_hit(ch),
            -arms_hit(ch),
            -skill_hit(ch),
            -(ch["rarity"] or 0),
            ch["name"] or "",
        ),
    )
    total = len(ranked)
    page = ranked[:limit]

    characters = []
    for ch in page:
        arms = arms_by_char.get(ch["charid"], [])
        skill_matched: list[dict] = []
        for arm in arms:
            arm["matched"] = any(t in (arm["name"] or "").lower() for t in lowered)
            arm["skill_hits"] = arm_skill_hits(arm, lowered)
            for hit in arm["skill_hits"]:
                if hit not in skill_matched:
                    skill_matched.append(hit)

        characters.append({
            "charid": ch["charid"],
            "name": ch["name"],
            "rarity": ch["rarity"],
            "affiliation": ch["affiliation"],
            "college": ch["college"],
            "number": ch["number"],
            "description": ch["description"],
            "arms_count": ch["arms_count"],
            "variants": ch["variants"],
            "arms_type": ch["arms_type"] or "",
            "body": ch["body"] or "",
            "ammo": sort_ammo(ammo_by_char.get(ch["charid"], [])),
            "sort_stats": sort_stats_by_char.get(ch["charid"], {}),
            "name_matched": name_hit(ch),
            "skill_matched": skill_matched,
            "arms": arms,
        })

    return jsonify({
        "keyword": keyword,
        "tokens": tokens,
        "total": total,
        "count": len(characters),
        "limit": limit,
        "characters": characters,
    })


@app.get("/api/character/<charid>")
def api_character(charid: str):
    with get_conn() as conn:
        cur = conn.cursor()
        ch = cur.execute(
            """
            SELECT charid, name, rarity, affiliation, college, number,
                   description, arms_count, variants
            FROM characters WHERE charid = %s
            """,
            (charid,),
        ).fetchone()
        if ch is None:
            return jsonify({"error": f"没有角色 {charid}"}), 404

        jobs_by_id = fetch_jobs(cur)
        ready = ensure_equip_table(conn)
        has_images = ensure_images_table(conn)
        has_skills = ensure_skills_table(conn)
        has_ammo = ensure_arms_ammo_table(conn)
        arms_rows = cur.execute(
            arms_query("WHERE a.charid = %s ORDER BY CASE a.variant "
                       "WHEN '原型' THEN 0 WHEN '改型' THEN 1 "
                       "WHEN '特改型' THEN 2 ELSE 3 END, a.armsid",
                       with_equip=ready, with_images=has_images),
            (charid,),
        ).fetchall()

        arms_ids = [row["armsid"] for row in arms_rows]
        link_jobs: dict[str, list[int]] = {}
        skill_map: dict[str, list[dict]] = {}
        ammo_map: dict[str, list[str]] = {}
        if arms_ids:
            for link in cur.execute(
                "SELECT armsid, jobid FROM arms_jobs WHERE armsid = ANY(%s) ORDER BY armsid, jobid",
                (arms_ids,),
            ).fetchall():
                link_jobs.setdefault(link["armsid"], []).append(int(link["jobid"]))
            if has_skills:
                for r in cur.execute(
                    "SELECT armsid, kind, jobid, skillid, name, description, ap, cp, damage"
                    "  FROM arms_skills WHERE armsid = ANY(%s)"
                    " ORDER BY armsid, kind DESC, jobid, seq",
                    (arms_ids,),
                ).fetchall():
                    skill_map.setdefault(r["armsid"], []).append(r)
            if has_ammo:
                for r in cur.execute(
                    """
                    SELECT armsid, ammo
                    FROM arms_ammo
                    WHERE armsid = ANY(%s)
                    """,
                    (arms_ids,),
                ).fetchall():
                    ammo_map.setdefault(r["armsid"], []).append(r["ammo"])

    arms = []
    used_jobs: dict[int, dict] = {}

    for row in arms_rows:
        ids = job_ids_for(row, link_jobs, jobs_by_id)

        for jid in ids:
            used_jobs[jid] = jobs_by_id[jid]

        arm_data = serialize_arms(
            row,
            ids,
            jobs_by_id,
            skill_map.get(row["armsid"])
        )

        # 按固定顺序排列弹药
        ammo_names = ammo_map.get(row["armsid"], [])
        arm_data["ammo"] = (
            [name for name in AMMO_ORDER if name in ammo_names]
            + sorted(name for name in ammo_names if name not in AMMO_ORDER)
        )

        arms.append(arm_data)

    # 默认看改型；没有改型就看原型
    default_arm = next((a for a in arms if a["variant"] == "改型"), None) \
        or next((a for a in arms if a["variant"] == "原型"), None) \
        or (arms[0] if arms else None)

    return jsonify(
        {
            "charid": ch["charid"],
            "name": ch["name"],
            "rarity": ch["rarity"],
            "affiliation": ch["affiliation"],
            "college": ch["college"],
            "number": ch["number"],
            "description": ch["description"],
            "arms_count": ch["arms_count"],
            "variants": ch["variants"],
            "arms": arms,
            "default_armsid": default_arm["armsid"] if default_arm else None,
            "jobs": used_jobs,
            "equip_ready": bool(ready),
            "images_ready": bool(has_images),
            "skills_ready": bool(has_skills),
        }
    )


if __name__ == "__main__":
    if not os.environ.get("ARMSBANK_DB_PASSWORD"):
        print("[警告] 未设置 ARMSBANK_DB_PASSWORD，页面会提示连接失败。")
    app.run(debug=True, port=int(os.environ.get("PORT", "5000")))