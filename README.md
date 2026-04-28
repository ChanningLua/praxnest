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

V0.1 已落地（**三大支柱全部端到端**）：

**① 协同空间**
- ✓ 多用户登录 + bcrypt + session
- ✓ 多工作空间，按成员隔离
- ✓ markdown 笔记 CRUD（CodeMirror 6 编辑器）
- ✓ `[[双链]]` 解析 + 反向引用面板
- ✓ FTS5 全文搜索（中英文都行）
- ✓ 多人编辑冲突检测（last-write-wins，UI 提示）
- ✓ append-only audit log（admin 可查）

**② AI Workflow 三件套**（需要 `npm install -g praxagent`）
- ✓ AI 助手 sidebar — 选中笔记后问任意问题
- ✓ **PRD 助手**：生成测试用例 / 提取需求点 / 验收清单
- ✓ **Bug 协同**：评估严重度 + 推荐排查方向 / 套标准 bug 模板
- ✓ **测试报告**：从原始测试输出生成可发群的执行摘要

**③ 团队记忆**
- ✓ 工作空间内相似笔记检索（FTS5 + 关键词 bigram）
- ✓ 跨工作空间检索（按用户成员资格隔离）
- ✓ 中英文混排原生支持
- *V0.1 backend 用 FTS5 关键词相似度；V0.2 升级到向量 embedding 不改 API*

✓ docker-compose 单文件部署 · ✓ **101 个单元测试**

V0.2 路线：实时协作（Yjs CRDT）/ 笔记 embed（vector）/ 跨笔记 push notification（接飞书 / 企业微信 / 个人微信）/ Linux crontab 支持。

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

| 支柱 | V0.1（现在） | V0.2+ |
|---|---|---|
| **协同空间** | ✓ markdown / 双链 / 搜索 / LWW 编辑 | Yjs CRDT 实时协同 / graph view |
| **AI Workflow** | ✓ PRD / bug / 测试报告 三件套 | 自定义 workflow yaml / 跨笔记自动联动 |
| **团队记忆** | ✓ FTS5 关键词相似度（工作空间内 + 跨空间） | 向量 embedding（保持 API 不变） |

V0.1 的目标是把三个支柱都端到端做出来。"团队记忆" V0.1 用 FTS5 而不是 embedding 是务实选择 —— 关键词 bigram + bm25 对小团队（<1k 笔记）的演示效果接近向量；V0.2 切换 backend 时 API 不变。

## AI workflow 实战

**前提**：装 praxagent —— `npm install -g praxagent` —— praxnest 通过 `subprocess` 调 `prax prompt` 跑 LLM，所以你想用哪个 LLM 就在 `~/.prax/models.yaml` 里配（OpenAI / Claude / GLM / 任意中转站都行）。

**流程**：

1. 选中一篇 PRD 笔记 → 顶栏 **AI 助手** 下拉 → **生成测试用例**
2. AI 在右栏输出测试用例 markdown
3. 复制到一篇新笔记（`Sprint 5 测试计划`）
4. 测试跑完后把 json 结果粘到笔记 → AI 助手 → **生成执行摘要**
5. 摘要直接粘飞书群

**每个 workflow 是只读 LLM 调用** —— prax 用 `--permission-mode read-only` 起，不会编辑文件、不会跑 shell 命令。安全审计友好。

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
├── memory.py          FTS5 keyword similarity (V0.2 swaps to vectors)
├── ai/                praxagent subprocess wrapper
│   └── client.py      run_prompt(...) → PromptResult
├── workflows/         AI workflow plugins
│   ├── prd.py         test cases / requirements / checklist
│   ├── bug.py         severity / template-fill
│   └── test_report.py wechat-ready execution summary
├── routes/
│   ├── auth.py        /api/auth/{login, logout, me}
│   ├── workspaces.py  /api/workspaces*
│   ├── notes.py       /api/workspaces/{ws}/notes*
│   ├── ai.py          /api/workspaces/{ws}/ai/{ask, status, workflows/...}
│   ├── memory.py      /api/workspaces/{ws}/memory/similar  +  /api/memory/similar-across-workspaces
│   └── audit.py       /api/audit  (admin only)
└── web/index.html     SPA 入口（Vue 3 + CodeMirror via CDN）
```

---

## License

MIT
