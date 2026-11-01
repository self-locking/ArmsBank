# 部署说明（ArmsBank）

这份文件只讲"怎么把它变成一个能访问的网站"，和 README 里的功能说明分开。

---

## 现在有哪些启动方式

| 方式 | 命令 | 适合 |
| --- | --- | --- |
| 本机开发 | `.venv\Scripts\python.exe wsgi.py`（或 `backend\app.py`） | 改代码、自己看 |
| 本机生产态 | `gunicorn wsgi:application` | 验证部署形态 |
| Docker Compose | `docker compose up --build` | 有 Docker 就能一键起（含数据库） |
| Render | 连 GitHub，见下 | 公网可访问、零服务器维护 |

三种方式用的是同一份 `wsgi.py` 入口，数据库连接全靠环境变量，代码不用改。

---

## 一、本机跑起来

```powershell
cd C:\Users\example_one\Desktop\database\armsbank_app

# 1. 装依赖（第一次）
.venv\Scripts\python.exe -m pip install -r requirements.txt

# 2. 建库（Postgres 里先有 armsbank_db 这个库）
$env:ARMSBANK_DB_PASSWORD = "你的密码"
.venv\Scripts\python.exe backend\bootstrap.py

# 3. 起服务
.venv\Scripts\python.exe wsgi.py
# 打开 http://127.0.0.1:5000
```

`bootstrap.py` 是这一版新加的：它用 Python 直接读 CSV 建表灌数，
不依赖 `psql`，所以在没有 psql 的机器 / 容器 / 云平台上也一样能建库。

首次访问某个分区时，`app.py` 会自己把余下的表（兵装 / 立绘 / 技能 / 插件 /
框架 / 敌人 / 阿兹班石 / 弹药）按 CSV 建好，所以第一次点开会慢一两秒。

---

## 二、部署到 Render（推荐，免费）

1. 把仓库推到 GitHub（已经有了：`self-locking/ArmsBank`）
2. 打开 <https://dashboard.render.com> → **New +** → **Blueprint**
3. 选中这个仓库，Render 会自动读到根目录的 `render.yaml`
4. 点 **Apply**：它会建一个 Web 服务 + 一个 PostgreSQL
5. 等构建完成，打开分配的域名

`render.yaml` 已经配好：Python 3.11、`gunicorn` 启动、新加坡区域、
数据库连接信息自动注入成 `ARMSBANK_DB_*`。

**第一次打开会慢**，因为免费层会休眠；而且第一次访问要等应用把表建出来。

几个免费层的坑，先知道有这回事：

- 免费实例 **15 分钟没人访问就睡**，下一次打开要等 20–50 秒
- 免费 PostgreSQL **30 天后会被删除**。作业演示够用；想长期用，
  可以在 Render 里把它换成 Neon / Supabase 的免费库，再把
  `ARMSBANK_DB_HOST / PORT / NAME / USER / PASSWORD` 改成那边的值
- 仓库里带了 1091 张立绘（约 127 MB），构建会慢一些

---

## 三、部署到自己的服务器（可选）

```bash
# 服务器上（已装 Docker）
git clone https://github.com/self-locking/ArmsBank.git
cd ArmsBank
# 改 docker-compose.yml 里的 POSTGRES_PASSWORD，然后：
docker compose up -d --build
# 打开 http://服务器IP:8000
```

国内机器记得备案；想加域名和 HTTPS，前面挂一层 Nginx 或 Caddy 即可。

---

## 四、数据怎么更新

现在是**这一版**的做法（还没做后台）：

1. 改 `data/` 下的 CSV
2. 提交并推送
3. Render 自动重新部署；数据库是持久的，`bootstrap.py` 只在表为空时灌数据，
   **不会**覆盖已有数据

所以「改 CSV 之后让数据库跟着变」需要一个导入脚本——这是下一步要做的：
后台登录 + 上传 CSV + 一键重建。

---

## 五、下一步（等这一步测通再做）

- 后台登录（密码走环境变量，不走代码）
- 上传 CSV → 落盘 → 触发导入 → 页面显示进度
- 导入做成「先建临时表、再原子替换」，失败不破坏现有数据
