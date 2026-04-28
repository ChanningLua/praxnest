# praxnest

> **本地优先的产研测协同空间** —— 共享 markdown 工作空间 + Obsidian 风
> 双链 + 全文搜索，加上预留给 AI workflow 的扩展点。所有数据**只在你内网**，
> docker-compose 一键部署。

[![npm version](https://img.shields.io/npm/v/praxnest.svg)](https://www.npmjs.com/package/praxnest)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

---

## 这是什么

```
┌──────────────────────────────────────────────────────────┐
│  浏览器 (产/研/测多人)  ←──→  praxnest 服务（内网某台机器）  │
│                                ↓                         │
│                         SQLite + markdown 文件            │
└──────────────────────────────────────────────────────────┘
```

V0.1 已落地：
- ✓ 多用户登录 + bcrypt + session
- ✓ 多工作空间，按成员隔离
- ✓ markdown 笔记 CRUD（CodeMirror 6 编辑器）
- ✓ `[[双链]]` 解析 + 反向引用面板
- ✓ FTS5 全文搜索（中英文都行）
- ✓ 多人编辑冲突检测（last-write-wins，UI 提示）
- ✓ append-only audit log（admin 可查）
- ✓ docker-compose 单文件部署
- ✓ 71 个单元测试

V0.2 计划：AI sidebar（接 praxagent）+ PRD/bug/测试报告 三类内置 workflow + 团队记忆（向量检索）。

---

## 5 分钟内网部署（docker-compose）

需要：装了 Docker 的一台机器（macOS / Linux 都行；Windows 用 Docker Desktop 也行）。

### 1. 拿到代码

```bash
git clone https://github.com/ChanningLua/praxnest
cd praxnest
```

### 2. 启动

```bash
docker-compose up -d
```

第一次会构建镜像（~2 分钟）。

### 3. 创建 admin 账号

```bash
docker-compose exec praxnest python -m praxnest init --admin-username admin
# 会提示输入密码（输入两次）
```

### 4. 打开浏览器

- 同机访问：`http://127.0.0.1:7878`
- 局域网访问：`http://<服务器内网 IP>:7878` （docker-compose 默认绑了 0.0.0.0）

用第 3 步创建的 admin 账号登录 → 看到首屏会自动弹「新工作空间」对话框 → 起个名字（比如 `Engineering`）→ 创建第一篇笔记。

### 5. 加同事进来

每个同事自己起个账号：

```bash
docker-compose exec praxnest python -m praxnest init --admin-username alice
docker-compose exec praxnest python -m praxnest init --admin-username bob
# 留意：默认 init 创建的是 admin。如果想要 member，目前要在 SQLite 里手改 role 字段，
# v0.2 会加 GUI 管理界面。
```

把工作空间分享给他们：暂时通过共享 SQLite 行（`workspace_members` 表）实现，v0.2 会加 GUI 邀请按钮。

---

## 命令行（不想用 Docker 也行）

如果你已经装了 Python 3.10+，可以直接 pip 安装：

```bash
pip install -e ".[dev]"     # 在 clone 出来的目录里
python -m praxnest init --admin-username admin --data-dir ./data
python -m praxnest serve --data-dir ./data
```

或者从 npm 装（拉 wheel）：

```bash
npm install -g praxnest
praxnest init --admin-username admin
praxnest serve
```

可用命令：

```bash
praxnest serve [--host HOST] [--port N] [--data-dir PATH] [--no-open]
praxnest init  [--admin-username NAME] [--admin-password PASS] [--data-dir PATH]
```

`--host 0.0.0.0` = 团队模式（绑到所有网卡，局域网能访问）；默认 `127.0.0.1` = 单人本地用。

---

## 数据存哪里

```
<data-dir>/
├── praxnest.db       SQLite — 用户 / 工作空间 / 笔记 / audit 全在这
├── session-secret    Session cookie 签名密钥（chmod 600，不要 commit）
└── (后续会加 attachments/ 之类)
```

docker-compose 默认挂的是项目目录下的 `./data/`。备份 = 打包整个 `data/` 目录。

数据**不离开你的容器**。没有任何外发请求 — 没接 LLM、没接云服务、没遥测。

---

## 三大支柱（路线图）

| 支柱 | V0.1 | V0.2+ |
|---|---|---|
| **协同空间** | ✓ markdown / 双链 / 搜索 / LWW 编辑 | 实时协同（Yjs CRDT）/ graph view |
| **AI Workflow** | （未实装 — 留了路由 stub） | PRD / bug / 测试报告 三件套；接 praxagent 作 LLM runtime |
| **团队记忆** | （未实装） | 笔记 embed → 向量索引 → 跨工作空间检索 + 主动相似提示 |

V0.1 的目标是「**先把骨架做扎实**」：多用户能写、能搜、能链接、能看到对方改过。AI 部分在 V0.2 接，因为接错就是又一个「换皮 Notion AI」。

---

## 与同类工具对比

| | praxnest | Kollab | Obsidian | Notion AI | Confluence |
|---|---|---|---|---|---|
| 部署 | **on-prem Docker** | 云端 SaaS | 本地客户端 | 云端 SaaS | 自建/云 |
| 多人协作 | LWW（v0.1）→ CRDT（v0.2） | ✓ 实时 | ✗ | ✓ 实时 | ✓ |
| 数据合规 | **零云端依赖** | 跨境风险 | 本地 | 跨境风险 | 看版本 |
| 内嵌 AI | v0.2 | ✓ | 插件 | ✓ | ✗ |
| 团队记忆 | v0.2（向量） | 项目隔离 | 个人 | 部分 | 死 wiki |
| 开源 | **MIT** | 闭源 | 客户端开源、付费插件 | 闭源 | 部分 |

---

## 安全 + 局限性（V0.1）

- **session cookie + bcrypt**：足以挡住偶然访问；**不是**企业级 SSO。
- **没有 RBAC**：每个用户在工作空间里要么 admin 要么 member，没有"只读访客"等角色。
- **没有 HTTPS**：自己用 nginx / caddy 做反向代理 + Let's Encrypt。
- **没有 CSRF token**：依赖 SameSite=Lax cookie 兜底；如果你的部署有跨域需求，加反向代理层 CSRF。
- **没有 GDPR 删除路径**：audit log 是 append-only，要 GDPR-compliant 删用户得手动 SQL。

部署在公司内网受信环境是 V0.1 的预期场景；公网暴露请加反向代理层 + 限速。

---

## 开发

```bash
git clone https://github.com/ChanningLua/praxnest
cd praxnest
pip install -e ".[dev]"
PYTHONPATH=src python3 -m pytest -q   # 71 个测试
PYTHONPATH=src python3 -m praxnest init --admin-username admin --data-dir ./.praxnest
PYTHONPATH=src python3 -m praxnest serve --data-dir ./.praxnest
```

代码布局：

```
src/praxnest/
├── app.py             FastAPI factory + serve()
├── cli.py             argparse → serve / init / etc.
├── db.py              SQLite schema + connection
├── auth.py            bcrypt hash/verify + create/get user
├── audit.py           append-only event log
├── workspaces.py      workspace CRUD + membership
├── notes.py           markdown CRUD + 双链 + FTS5 search
├── routes/
│   ├── auth.py        /api/auth/{login, logout, me}
│   ├── workspaces.py  /api/workspaces*
│   ├── notes.py       /api/workspaces/{ws}/notes*
│   └── audit.py       /api/audit  (admin only)
└── web/index.html     SPA 入口（Vue 3 + CodeMirror via CDN）
```

---

## License

MIT
