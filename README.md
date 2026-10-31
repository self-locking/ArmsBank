# ArmsBank 本地数据站

用 **Flask + PostgreSQL** 复刻 <https://arms-bank.com/>（灰烬战线情报枢纽）的机体数据查询站：
266 名角色、552 台机体、28 个职能、15 组性格插件、33 组知性框架、495 组敌人（5265 个型号）、
74 组阿兹班石（90 条）、730 条机体弹药。

支持五个分区检索、**按角色名或技能名模糊搜索**、角色库四维分组筛选、职能切换、
面板数值实时计算、立绘（Normal / Battle）切换、技能描述展开，以及插件 / 框架 / 敌人 / 阿兹班石抽屉详情。

目录按职责分成三块：`backend/`（后端）、`frontend/`（前端）、`data/`（数据），
运行用项目自带的 `.venv/`。这份 README 是项目唯一的说明文档。

## 一、目录结构

```
armsbank_app/
├─ backend/                     ← 后端
│  ├─ app.py                    全部后端：路由 / SQL / 自动建表
│  └─ sql/                      建库脚本（从项目根目录用 psql 跑）
│     ├─ 01_schema_base.sql     4 张基础表 + 灌数据 + 派生 arms_jobs
│     ├─ 02_arms_equip.sql      arms_equip 建表 + 552 行
│     ├─ 03_arms_images.sql     arms_images 建表 + 552 行
│     ├─ 04_arms_skills.sql     arms_skills 建表 + 6833 行
│     ├─ 05_plugins_frameworks.sql  plugins + frameworks 建表与灌数
│     └─ 06_azban_skills.sql      azban_skills 建表 + 317 条阿兹班石技能效果
├─ frontend/                    ← 前端
│  ├─ templates/                base.html / index.html / character.html
│  └─ static/app.css            全部样式
├─ data/                        ← 数据
│  ├─ armsbank_角色表.csv        266 行
│  ├─ armsbank_机体表.csv        552 行（主力表）
│  ├─ armsbank_职能表.csv         28 行
│  ├─ armsbank_兵装表.csv        552 行（→ arms_equip）
│  ├─ armsbank_立绘图.csv        552 行（→ arms_images，只存文件名）
│  ├─ armsbank_技能表.csv       6833 行（→ arms_skills）
│  ├─ armsbank_插件表.csv         75 行（→ plugins）
│  ├─ armsbank_框架表.csv        109 行（→ frameworks）
│  ├─ armsbank_敌人表.csv       5265 行（→ enemies）
│  ├─ armsbank_敌人技能表.csv    2052 行（→ enemy_skills）
│  ├─ armsbank_阿兹班石表.csv      90 行（→ azbans）
│  ├─ armsbank_机体弹药表.csv     730 行（→ arms_ammo）
│  ├─ arms_images/              1091 张角色立绘（Normal / Battle，webp）
│  └─ enemy_art/                194 张敌人战斗贴图（webp）
├─ requirements.txt             依赖清单（Flask 3.1 + psycopg 3.3）
├─ .venv/                       运行环境（Flask 3.1 + psycopg 3.3）
└─ README.md                    本文件
```

> 立绘和敌人贴图都不入库：库里只存文件名，`/img/<文件名>` 与 `/enemy_art/<文件名>`
> 直接从 `data/arms_images/`、`data/enemy_art/` 读磁盘。

---

## 二、环境要求

- Python 3.10+
- PostgreSQL 13+
- 依赖：`pip install -r requirements.txt`（Flask 3.1、psycopg 3.3）

---

## 三、建库（两种方式，选一种）

> 已经有库、只想更新表和数据的，跳过这一节：`python backend/app.py` 启动时会自动补齐
> `plugins` / `frameworks` / `enemies` / `enemy_skills` / `azbans` / `arms_ammo`，
> 以及 `arms_equip` / `arms_images` / `arms_skills` 缺的部分。

### 方式 A：一条脚本全建好（推荐）

```bash
createdb -U postgres armsbank_db

# 必须在 armsbank_app/ 目录下执行：\copy 的相对路径以 psql 当前目录为准
psql -d armsbank_db -U postgres -f backend/sql/01_schema_base.sql
psql -d armsbank_db -U postgres -f backend/sql/02_arms_equip.sql
psql -d armsbank_db -U postgres -f backend/sql/03_arms_images.sql
psql -d armsbank_db -U postgres -f backend/sql/04_arms_skills.sql
psql -d armsbank_db -U postgres -f backend/sql/05_plugins_frameworks.sql
psql -d armsbank_db -U postgres -f backend/sql/06_azban_skills.sql
```

脚本结尾自带自检，正常情况下应输出：

| 表 | 行数 |
| --- | --- |
| characters | 266 |
| arms | 552 |
| jobs | 28 |
| arms_jobs | 1179 |
| arms_equip | 552 |
| arms_images | 552 |
| arms_skills | 6833 |
| plugins | 75（15 个基础名 × 5 档） |
| frameworks | 109（33 个基础名） |
| azban_skills | 317（阿兹班石涉及的技能名） |

> 敌人两张表（`enemies` / `enemy_skills`）没放进 sql/——5265 行 INSERT 太笨重。
> 它们和其它「后建表」一样，**第一次访问敌人分区时由 CSV 自动建表灌数**，见方式 B。
> 阿兹班石（`azbans`）与机体弹药（`arms_ammo`）同理：都是应用首次启动时按 CSV 建表，
> 不需要跑 SQL；`arms_ammo` 主键是 `(armsid, ammo)`，一台机体可以带两种弹。

### 方式 B：只建基础表，后三张交给应用

只跑 `01_schema_base.sql`，然后直接启动应用——
`arms_equip` / `arms_images` / `arms_skills` / `plugins` / `frameworks` / `enemies` / `enemy_skills` /
`azbans` / `arms_ammo`
会在**第一次访问对应分区时自动建表并灌数**
（`to_regclass()` 探测 → `CREATE TABLE IF NOT EXISTS` → `INSERT … ON CONFLICT DO NOTHING`）。
这样换机器、换库都不会因为缺表报 500。

---

## 四、启动

```bash
# Linux / macOS
export ARMSBANK_DB_PASSWORD=你的密码

# Windows (cmd)
set ARMSBANK_DB_PASSWORD=你的密码

.venv\Scripts\python.exe backend\app.py
# 打开 http://127.0.0.1:5000
# （在项目根目录 armsbank_app/ 下执行；模板、静态文件、数据都按绝对路径找）
```

连接参数都可以用环境变量覆盖（默认连本机）：

| 变量 | 默认值 |
| --- | --- |
| `ARMSBANK_DB_HOST` | `localhost` |
| `ARMSBANK_DB_PORT` | `5432` |
| `ARMSBANK_DB_NAME` | `armsbank_db` |
| `ARMSBANK_DB_USER` | `postgres` |
| `ARMSBANK_DB_PASSWORD` | 无（必须设置） |
| `PORT` | `5000` |

### 立绘（可选）

立绘图片**不入库**（1091 张 webp 共约 127 MB，进库会拖慢备份），库里只存文件名，
由 `/img/<文件名>` 从磁盘读取。图片就在 `data/arms_images/`（1091 张，约 127 MB），
和敌人贴图 `data/enemy_art/` 并排；想用别的目录就设置：

```bash
# Linux / macOS
export ARMSBANK_IMAGE_DIR=/path/to/arms_images/images
# Windows (cmd)
set ARMSBANK_IMAGE_DIR=C:\path\to\arms_images\images
```

不设置也能用，只是页面不显示图（其余功能不受影响）。

---

## 五、功能

| 页面 / 接口 | 说明 |
| --- | --- |
| `/` | 首页顶部五个分区：**角色 266 ｜ 性格插件 15 ｜ 知性框架 33 ｜ 敌人 495 ｜ 阿兹班石 74**，点哪个就在哪个分区里查 |
| `/?nation=&body=&arms=&ammo=` | 角色库的分组筛选：学联 / 轻中重型 / 兵种 / 弹药，可叠加，选中的条件会写进地址栏 |
| `/?cat=plugin&q=` | 性格插件分区；搜索**只匹配插件名称**，按基础名合并成 15 张卡 |
| `/?cat=framework&q=` | 知性框架分区；搜索**只匹配框架名称**，按基础名合并成 33 张卡 |
| `/c/<charid>` | 角色详情：默认显示**改型**（无改型则原型）的第一个职能 |
| `GET /api/search?q=` | 模糊搜索：角色名 / 编号 / 机体名 + 机体上的**专属·DOLLS·改造技能名** |
| `GET /api/character/<charid>` | 单个角色的全部机体 + 职能 + 技能 |
| `GET /api/jobs` | 全部职能 |
| `GET /api/plugins?q=` | 性格插件（75 条按基础名合并成 15 组，含 Ⅰ~Ⅴ 各档） |
| `GET /api/frameworks?q=` | 知性框架（109 条按基础名合并成 33 组） |
| `GET /api/enemies?q=` | 敌人（5265 条按基础名合并成 495 组） |
| `GET /api/azbans?q=` | 阿兹班石（90 条按基础名合并成 74 组，通用的在前、专属的在后） |
| `GET /api/overview` | 各类数据条数（用于首页提示数据是否就绪） |
| `GET /img/<filename>` | 立绘文件（带 7 天缓存） |

详情页的交互：

- **立绘**：默认 Normal，点一下切 Battle。两种图比例差异很大（竖/横），
  所以框高固定 520px、宽度随图片比例自适应，避免 Battle 被压成一条。
- **变体**：原型 / 改型 / 特改型 标签切换，立绘和数值一起换。
- **职能**：标签切换，13 项面板**实时重算**，鼠标悬停能看到「本体 + 兵装 + 职能」的拆分。
- **技能**：职能技能单独一块（跟着职能变），其余技能（专属/DOLLS/改造）合并一块且不标注类型；
  名称常显，描述默认折叠，点击展开。

插件 / 框架分区的交互：

- **分区不混**：五个分区各自独立查询，插件区不会搜到角色，框架区不会搜到插件，敌人/阿兹班石区也不会混进别的。
- **按基础名合并**：`狂热Ⅰ~Ⅴ` 在列表里是一张卡，卡上标「5 档」；点开后在抽屉顶部用 Ⅰ~Ⅴ 按钮切档，
  数值随档位变。
- **右侧抽屉**：从右边滑出 420px 面板（窄屏占满），点遮罩或按 Esc 关闭；列表不动、不跳页。
- **插件**：卡片上只标等级 **+N**（类型不放在卡片上），副标题是属性加成；点开抽屉才看到「类型 N（颜色）· 等级 +N」，下面列 7 类百分比加成（命中/机动/幸运/装甲/贯通/暴击概率/暴击伤害）与说明。等级就是插到框架上贡献的那个 +N，**与名字里的 Ⅰ~Ⅴ 无关**（狂热Ⅰ 是 1、英勇Ⅰ 是 2、愤怒Ⅰ 是 0）。
- **框架**：固定 5 格插槽，每格标它**限定装哪一类插件**（`0` = 不限类型，任选）；下面是**升级阶梯**——第 Ⅰ/Ⅱ/Ⅲ 级各自要求「某类插件的等级累计到多少」，插满够数就升级，级数上限 = 阶梯行数。再往下是附带被动技能名和完整的被动效果文本。
- **敌人**：以上三类之外还有一个「敌人」分区，495 组 / 5265 条（同名不同 Lv 合成一张卡，卡上标「N 型」和最高 Lv）。搜索**只匹配名称**（子串，不分大小写）。抽屉里是战斗贴图 + 9 项数值（生命值/装甲/机动/对地火力/对空火力/命中/贯通/幸运/反应值）+ 主动/被动技能（名字、AP/CP、描述），顶部「型号」下拉切 Lv。
- **阿兹班石**：74 组 / 90 条。通用石在卡上标「体型 + 兵种」（如「重型 · 战斗机」），专属石标「专属 · 角色名」；同名不同档（`LF-α/β/θ型`）合成一张卡，卡上标「N 型」。抽屉里用 α/β/θ 按钮切档，下面是 11 项属性加成、替换技能（`旧 → 新`）、新增技能，以及「专属：某机体」/「通用：某体型某兵种」的适用范围。**技能名可以直接点**——旧技能、新技能、新增技能都能点，点一下就地展开效果原文（带 AP / CP / 伤害 / 目标），再点收起；效果原文存在 `azban_skills` 表里（由 `sql/06_azban_skills.sql` 建）。
- **角色库分组筛选**：只有角色分区顶部有筛选条，四个维度 **学联 / 体型 / 兵种 / 弹药**。点其中一个维度会展开它的取值（每个值后面是当前结果里符合条件的角色数），点某个值，下面的卡片立刻只剩这类角色；再点别的维度可以继续叠加，范围越缩越小。选中的维度会变蓝并显示当前值，右侧出现「清除筛选」一键回到全部；点空白处收起展开的面板。筛选条件会写进地址栏（`?nation=红十月同盟学联`），直接分享就是这个筛选视图。
  搜索与筛选是叠加关系：先按关键词搜，再在结果里按维度缩小；清掉筛选不会清掉搜索词。角色分区一次取满 600 条（266 个角色全在手里），所以筛选、计数都是即时的，不再发请求。

---

## 六、搜索规则

| 分区 | 搜什么 |
| --- | --- |
| 角色 | 角色名、角色编号、机体名、机体编号，以及机体上的 **专属技能 / DOLLS 技能 / 改造技能** 名称（子串，不分大小写） |
| 性格插件 | 只匹配插件名称 |
| 知性框架 | 只匹配框架名称 |
| 敌人 | 只匹配敌人名称 |
| 阿兹班石 | 只匹配阿兹班石名称 |

- 角色分区的技能搜索**只覆盖机体上的三类技能**：`arms.exclusive_skill`（专属）、
  `arms.dolls_skill`（DOLLS）、`arms.arms_skill`（改造），**不含职能技能**——
  职能技能在 `arms_skills` 里跟着职能走，不参与搜索。
- 多个词用空格分开是「且」的关系：每个词都要命中（名字、编号、机体名或技能名都算）。
- 命中技能时，卡片名字下面会多一行「技能 专属 · xxx / 改造 · yyy」，一眼看出来是因为哪个技能搜到的。
- 排序：名字直接命中的排前面，其余按机体名命中数、技能命中数、稀有度排。
- 搜索与筛选是叠加关系：先按关键词（含技能名）搜，再在结果里按维度缩小。

---

## 七、数值口径（这是复刻的关键）

站点面板 = **本体值 + 兵装值 + 职能修正**，三者分三类计算：

```
生命 / 对地火力 / 对空火力 / 装甲 / 贯通   = 本体 + 兵装 + trunc(兵装 × 职能%)
命中 / 机动 / 幸运 / 暴击概率 / 暴击伤害   = 本体 + 兵装 + 职能定值
反应值                                     = 兵装（本体不参与）
```

- 库里只存「本体」和「兵装」两份原料，**最终面板不落库**，前端按上式实时算——
  职能一换整张表跟着变，不需要回写数据库。
- 兵装口径：Lv100、改造全开、每个槽位选最强的已解锁部件。
- 暴击概率取兵装（站点原始数据里机体的暴击概率字段是 0，实际值由兵装给出）。
- CP 恢复率站点本身未参与计算，只记录不展示。

---

## 八、SQL 一览（作业要求「用到数据库」的部分）

**建表脚本**：`sql/01~05`。

**应用内嵌 SQL**（`app.py`，共 28 处 `SELECT`、3 处 `LEFT JOIN`、4 处 `CREATE TABLE`、4 处 `INSERT … ON CONFLICT`）：

| SQL 技术 | 用在哪 |
| --- | --- |
| 多表 `LEFT JOIN` | 机体查询一次连 `arms` + `arms_equip` + `arms_images` |
| `CASE … ORDER BY` | 原型 / 改型 / 特改型 固定顺序 |
| `ILIKE … ESCAPE` + 参数化 | 模糊搜索（值走占位符，不拼字符串，防注入） |
| `EXISTS` 子查询 | 按机体名搜角色（角色名没命中时，用其机体名兜底） |
| `ANY(%s)` 数组参数 | 一次查多台机体的技能，避免 N+1 |
| `to_regclass()` | 探测表是否存在，缺表自动补建 |
| `CREATE TABLE IF NOT EXISTS` + `ON CONFLICT DO NOTHING` | 幂等建表灌数，重启不重复插入 |
| `regexp_replace` / `unnest` / `string_to_array` | 把 `"2 / 3"` 拆成 `arms_jobs` 关联行 |
| 主键 / 外键 / 索引 | `charid`、`armsid`、`(arms_skills 复合主键)`、`arms(charid)`、`plugins(base_name)`、`frameworks(base_name)` |
| 分组聚合（Python 侧） | 75/109 条按 `base_name` 归组成 15/33 张卡，`ORDER BY base_name, tier` 保证档位有序 |

---

## 九、查询是怎么执行的

两条查询路径，机制完全不同——这也是「数据库不是遍历找关键字」最直观的说明。

### 9.1 一条 SQL 的两种走法

| | 详情页取机体 | 搜索框 |
| --- | --- | --- |
| SQL | `WHERE a.charid = %s` | `WHERE c.name ILIKE '%KV%'` |
| 访问方式 | 主键 B-tree 索引查找（Index Scan） | 顺序扫描（Seq Scan） |
| 复杂度 | O(log n) | O(n) |
| 本项目数据量 | 552 行 → 约 0.1 ms | 266 行 → 约 0.3 ms |
| 能否靠加索引变快 | 已经是索引 | 普通索引无效，需 pg_trgm |

### 9.2 精确查询：B-tree 不是「逐行比对」

`charid text PRIMARY KEY` 除了约束唯一，PostgreSQL 还会在同一次建表里建一棵**有序 B 树**。
查 `charid = 'DOL1001'` 时，从根页开始按字典序折半下沉，3~4 层就定位到目标行所在的页——
不是把 266 行读一遍再逐个比对。

`LEFT JOIN arms_equip e ON e.armsid = a.armsid` 同理：规划器会选择**嵌套循环连接（Nested Loop）**，
对每台机体拿 `armsid` 去兵装表的主键索引里查一次。552 台机体 = 552 次索引探查，
而不是把两张表并排逐行比对。

### 9.3 模糊查询：这里真的在逐行匹配

`ILIKE '%KV%'` 的模式串前面带 `%`，而 B 树只能做**左前缀**检索（`LIKE 'KV%'` 能用索引），
前面一出现通配符就失效，只能全表**顺序扫描**，逐行跑模式匹配。

数据量小的时候这恰恰是最优解：读 266 行的成本远低于「走索引 + 回表」。
所以这不是遗漏，是有意的取舍。

### 9.4 自己验证（两条命令）

```sql
-- ① 精确查询：预期 Index Scan
EXPLAIN ANALYZE
SELECT * FROM arms WHERE charid = 'ARM100101';

-- ② 模糊查询：预期 Seq Scan
EXPLAIN ANALYZE
SELECT * FROM characters WHERE name ILIKE '%KV%';
```

输出大致长这样（具体耗时随机器不同）：

```
Index Scan using arms_pkey on arms  (cost=0.28..8.29 rows=1 width=...)
                                    (actual time=0.015..0.016 rows=1 loops=1)
  Index Cond: (armsid = 'ARM100101'::text)
Planning Time: 0.083 ms
Execution Time: 0.031 ms
```

```
Seq Scan on characters  (cost=0.00..12.66 rows=2 width=...)
                        (actual time=0.008..0.061 rows=2 loops=1)
  Filter: (name ~~* '%KV%'::text)
  Rows Removed by Filter: 264        ← 逐行比对，扫了全部 266 行
Execution Time: 0.089 ms
```

几个读法：

- `Index Scan` / `Seq Scan` —— 有没有用上索引；
- `Rows Removed by Filter` —— 模糊查询逐行淘汰的行数，接近表的总行数就说明是全表扫；
- `cost` 是估算代价，`actual time` 是实测耗时；
- 表很小的时候规划器可能**主动放弃索引**（估算全表扫更便宜）。看到精确查询也走 Seq Scan
  不一定是坏事，表变大后它会自动切回索引。

加分项，再跑一条 JOIN：

```sql
EXPLAIN ANALYZE
SELECT a.armsid, a.name, e.hp
FROM arms a
LEFT JOIN arms_equip e ON e.armsid = a.armsid
WHERE a.charid = 'DOL1001';
```

预期能看到 `Nested Loop` 加两次 `Index Scan`，说明连接不是靠「两边全扫再比对」完成的。

### 9.5 让 `%词%` 也能用索引：pg_trgm

数据量涨到十万级时，顺序扫描才会成为瓶颈。PostgreSQL 提供**三元组倒排索引**：

```sql
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE INDEX characters_name_trgm ON characters USING gin (name gin_trgm_ops);
```

原理：把字符串切成连续 3 字符片段（`KV-85` → `kv-`、`v-8`、`-85`）建倒排表，
「包含 KV」就变成「哪些行的三元组里出现过 `kv-`」，先用索引缩小候选集再逐行确认。

本项目（266 + 552 行）用不到，属于可选优化，写在报告里体现「知道瓶颈在哪、怎么解决」即可。

### 9.6 为什么参数化查询更安全

代码里所有值都走 `%s` 占位符，SQL 文本与值分两次送到服务器：
服务器先把**结构**编译成执行计划，再把值绑上去。值永远不会被当成语法解析——
搜 `' OR 1=1 --` 只会被当作一个普通字符串去匹配。

### 9.7 本项目建的索引

| 索引 | 表 | 被谁用到 |
| --- | --- | --- |
| 主键 | `characters(charid)` | 详情页、搜索里的 `EXISTS` 子查询 |
| 主键 | `arms(armsid)` | `LEFT JOIN` 兵装/立绘、`ANY(%s)` 批量取技能 |
| 主键 | `jobs(jobid)` | 每次打开页面都会 `SELECT * FROM jobs` |
| 主键 | `arms_equip` / `arms_images` / `arms_skills` | `LEFT JOIN` 的连接键 |
| 复合主键 | `arms_jobs(armsid, jobid)` | 机体 → 职能的关联 |
| 普通索引 | `arms(charid)` | 按角色取全部机体（原型/改型/特改型） |

## 十、数据表清单（14 张）

### 总览

| # | 表名 | 含义 | 行数 | 谁建的 |
| --- | --- | --- | --- | --- |
| 1 | `characters` | 角色 | 266 | `sql/01_schema_base.sql` |
| 2 | `arms` | 机体（本体值） | 552 | `sql/01_schema_base.sql` |
| 3 | `jobs` | 职能（百分比 / 定值） | 28 | `sql/01_schema_base.sql` |
| 4 | `arms_jobs` | 机体 × 可用职能 | 1179 | `sql/01_schema_base.sql`（从 `arms.available_job_ids` 派生） |
| 5 | `arms_equip` | 兵装值 | 552 | `sql/02_arms_equip.sql`（缺失时应用也会补建） |
| 6 | `arms_images` | 立绘文件名 | 552 | `sql/03_arms_images.sql`（同上） |
| 7 | `arms_skills` | 机体技能 | 6833 | `sql/04_arms_skills.sql`（同上） |
| 8 | `plugins` | 性格插件档位 | 75 | `sql/05_plugins_frameworks.sql`（CSV 驱动，可自动建） |
| 9 | `frameworks` | 知性框架档位 | 109 | `sql/05_plugins_frameworks.sql`（同上） |
| 10 | `enemies` | 敌人型号 | 5265 | 启动自动建（`data/armsbank_敌人表.csv`） |
| 11 | `enemy_skills` | 敌人技能字典 | 2052 | 启动自动建（`data/armsbank_敌人技能表.csv`） |
| 12 | `azbans` | 阿兹班石 | 90 | 启动自动建（`data/armsbank_阿兹班石表.csv`） |
| 13 | `arms_ammo` | 机体 × 弹药 | 730 | 启动自动建（`data/armsbank_机体弹药表.csv`） |
| 14 | `azban_skills` | 阿兹班石技能效果 | 317 | `sql/06_azban_skills.sql` |

> `enemies` 的 5265 行是 495 个基础名 × 若干等级 / 型号；`azbans` 的 90 条是 15 组通用石（31 条）+ 59 张专属石。
> 行数以 `data/` 里的 CSV 和 SQL 脚本为准，每个 `sql/*.sql` 结尾都带自检。
> `azban_skills` 只由 06 脚本建（应用不会自动建它），因为它的数据直接写在 SQL 里，没有对应 CSV。

### 字段

#### 1. `characters` —— 角色（主键 `charid`，text）

`charid`(PK) · `name` · `rarity`（1–3 星）· `affiliation`（学联，可能带「（可选：…）」）· `college`（学院）· `number`（编号）· `description` · `arms_count` · `variants`（“原型:ARM100101 / 改型:…”）· `arms_ids`（冗余列，应用未使用）

#### 2. `arms` —— 机体（主键 `armsid`，text；`charid` 外键 → `characters`，`ON DELETE CASCADE`）

- 身份：`armsid` · `name` · `charid` · `variant`（原型 / 改型 / 特改型）· `number` · `arms_type`（坦克 / 火炮 / 歼击车 / 防空炮 / 战斗机 / 攻击机 / 轰炸机）· `size`（轻型 / 中型 / 重型）· `available_job_ids`（文本，如 “2 / 3”）
- 本体面板：`hp` · `armor` · `mobility` · `ground_fire` · `air_fire` · `hit` · `penetration` · `luck` · `reaction` · `crit_chance` · `crit_damage` · `ap` · `cp_recovery` · `presence`（页面不展示）
- 技能名：`exclusive_skill` · `dolls_skill` · `arms_skill`

索引 `arms_charid_idx`。共 25 列。页面上的最终面板 = 本体 + 兵装 + 职能，实时算，不落库。

#### 3. `jobs` —— 职能（主键 `jobid`，integer）

`jobid`(PK) · `name` · `level`
百分比项（乘在**兵装值**上）：`hp_pct` · `ground_fire_pct` · `air_fire_pct` · `armor_pct` · `penetration_pct`
定值项（直接相加）：`hit` · `mobility` · `luck` · `crit_pct` · `crit_damage_pct`
`presence`（站点是覆盖赋值）· `cp_recovery_pct`（站点计算未用，只记录）· `skills`（职能技能名，“ / ” 分隔）

#### 4. `arms_jobs` —— 机体 × 职能（复合主键 `(armsid, jobid)`，两端外键 CASCADE）

只有两列，索引 `arms_jobs_job_idx`。

#### 5. `arms_equip` —— 兵装（主键 `armsid`）

`armsid`(PK) · `hp` · `power_ag` · `power_aa` · `armor` · `dodge` · `hit` · `penetration` · `luck` · `reaction` · `crit_chance` · `crit_damage`（全 integer，默认 0）

#### 6. `arms_images` —— 立绘文件名（主键 `armsid`）

`armsid`(PK) · `normal_file` · `battle_file`（文件名，图片本身在 `data/arms_images/`）

#### 7. `arms_skills` —— 机体技能（复合主键 `(armsid, kind, jobid, skillid)`）

`armsid` · `kind`（其他 / 职能）· `jobid`（0 = 非职能技能）· `skillid` · `name` · `description` · `ap` · `cp` · `damage` · `seq`（排序）

#### 8. `plugins` —— 性格插件（主键 `itemid`）

`itemid`(PK) · `base_name` · `name` · `tier`（Ⅰ–Ⅴ）· `rarity` · `skill_level` · `slot_type` · `hitp` · `dodgep` · `luckp` · `armorp` · `armorpenetrationp` · `critical` · `critdamage` · `description`
索引 `plugins_base_idx`（`base_name`）。

#### 9. `frameworks` —— 知性框架（主键 `itemid`）

`itemid`(PK) · `base_name` · `name` · `tier` · `rarity` · `skill_level` · `char_level_req` · `lock_types` · `unlock_levels` · `passive_name` · `description`
索引 `frameworks_base_idx`。

#### 10. `enemies` —— 敌人（主键 `enemyid`，21 列）

`enemyid`(PK) · `base_name` · `name` · `level` · `class_id` · `class_name` · `rarity` · `is_boss` · `hp` · `armor` · `dodge` · `ag_power` · `aa_power` · `hit` · `penetration` · `luck` · `reaction` · `ap` · `skill_ids` · `skin_id` · `description`

#### 11. `enemy_skills` —— 敌人技能（主键 `skillid`）

`skillid`(PK) · `name` · `skill_type` · `role`（用途）· `ap_cost` · `cp_cost` · `damage` · `description`

#### 12. `azbans` —— 阿兹班石（主键 `itemid`，26 列）

`itemid`(PK) · `name` · `base_name` · `tier` · `class_id` · `class_name` · `body` · `exclusive`（1 = 专属）· `arms_id` · `char_name`
11 项加成：`hp` · `power_ag` · `power_aa` · `armor` · `hit` · `dodge` · `penetration` · `luck` · `reaction` · `ap_recovery` · `cp_recovery`
其它：`add_skills` · `repl_skills` · `rebate`（返还 50）· `icon` · `description`

#### 13. `arms_ammo` —— 机体 × 弹药（复合主键 `(armsid, ammo)`）

一台机体最多两种弹，所以独立成小表，`characters` 里不存弹药。`ammo` ∈ 通常弹 / 穿甲弹 / 高爆弹 / 燃烧弹。

#### 14. `azban_skills` —— 阿兹班石技能效果（主键 `name`，text）

`name`（技能名，唯一）· `description`（效果原文）· `ap` · `cp` · `damage`（伤害 %）· `target`（目标）
阿兹班石「替换技能」的新旧名字和「新增技能」都在这里，前端点技能名时按名字查这一行。

### 关系

```
characters 1 ──< arms 1 ──< arms_jobs >── 1 jobs
                   ├── 1:1 arms_equip
                   ├── 1:1 arms_images
                   ├── 1:n arms_skills
                   └── 1:n arms_ammo
jobs / plugins / frameworks / enemies / enemy_skills / azbans   独立字典表
```

### 一条 SQL 核对全库

```sql
SELECT 'characters' 表, count(*) 行 FROM characters
UNION ALL SELECT 'arms',        count(*) FROM arms
UNION ALL SELECT 'jobs',        count(*) FROM jobs
UNION ALL SELECT 'arms_jobs',   count(*) FROM arms_jobs
UNION ALL SELECT 'arms_equip',  count(*) FROM arms_equip
UNION ALL SELECT 'arms_images', count(*) FROM arms_images
UNION ALL SELECT 'arms_skills', count(*) FROM arms_skills
UNION ALL SELECT 'plugins',     count(*) FROM plugins
UNION ALL SELECT 'frameworks',  count(*) FROM frameworks
UNION ALL SELECT 'enemies',     count(*) FROM enemies
UNION ALL SELECT 'enemy_skills',count(*) FROM enemy_skills
UNION ALL SELECT 'azbans',      count(*) FROM azbans
UNION ALL SELECT 'arms_ammo',   count(*) FROM arms_ammo
ORDER BY 1;
```

应为 266 / 552 / 28 / 1179 / 552 / 552 / 6833 / 75 / 109 / 5265 / 2052 / 90 / 730。

## 十一、数据来源

| 环节 | 内容 |
| --- | --- |
| 入口 | `https://arms-bank.com/` → `js/app.js?v=491` |
| 取数清单 | `js/loader.js` 的 `DATA_NAMES` |
| 数据源 | `https://data.arms-bank.com/manifest.json`，`generated_at` = 2026-10-25T18:26:21Z，`game_versions.cn` = **1.0.129**（目前只有 CN 服） |
| 属性算法 | `js/databank.js`：`base_stats()` / `upgrade_stats()` / `equip_stats()` / `character_gates()` / `select_job()` |
| 展示面板 | `js/ui/database.js` → 角色详情面板（生命值 / 装甲 / 机动 / 对地 / 对空 / 命中 / 贯通 / 幸运 / 反应值 / 暴击概率 / 暴击伤害 / 行动值AP / CP恢复值） |

用到的原始表：`ArmsSet`、`CharacterSet`、`ArmsDataDetail`、`UpgradeSet`、`JobSet`、`SkillSet`、`ImprovementSet`（仅用于确认兵装被排除）。
共 266 个角色（charid）、552 个机体、28 个在用职能。

## 十二、已知限制

- 只读站点，没有用户系统与增删改界面。
- 立绘 1091 张 webp（约 127 MB）随项目放在 `data/arms_images/`，项目目录（不含 .venv）约 150 MB。
- 数据为抓取站点 JSON 后离线清洗的静态快照，站点更新后需要重新生成 CSV。
