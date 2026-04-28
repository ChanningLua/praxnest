# Changelog

All notable changes to praxnest will be documented in this file.

## [0.1.0] - 2026-04-28

Initial release — V0.1 骨架。三大支柱里的第一个（协同空间）落地，
AI Workflow + 团队记忆 留作 V0.2。

### Added

- **多用户认证**：bcrypt + signed session cookies。`praxnest init` 创建首个 admin。
- **多工作空间**：每个工作空间独立的 markdown 文件树 + 成员列表。访问控制通过 `workspace_members` 表，跨空间操作返回 404 而不是 403（避免泄露空间存在性）。
- **笔记 CRUD**：folder_path / title / body_md。文件夹路径在创建时归一化（拒绝 `..` 路径穿越）。
- **`[[双链]]` 解析 + 反向引用面板**：选中笔记后右栏列出所有引用它的笔记。中文标题原生支持。
- **FTS5 全文搜索**：SQLite 内置；标题命中权重高于正文（bm25 加权）；用户输入的特殊字符自动 quote 不会引发 syntax error。
- **LWW 冲突检测**：客户端发来 `expected_version`，服务器对比；不一致返回 409 + 当前版本内容，前端弹冲突合并对话框。
- **Audit log**：所有写操作（login/logout/note.create/note.update/note.delete/workspace.create）记录到 audit 表；admin-only 通过 `GET /api/audit` 查询。
- **CodeMirror 6 编辑器**：via CDN（无构建步骤）。1 秒 debounce 自动保存。
- **docker-compose 部署**：单文件 `docker-compose.yml` + `Dockerfile`，5 分钟内网起服。

### Tests

71 单元测试覆盖 auth / audit / workspaces / notes / 双链 / 搜索 / LWW / HTTP 路由。

### Known limitations (V0.1)

- 实时协作 — 仅 LWW，不是 CRDT。多人快速并发改同一篇会触发冲突弹窗。
- 没有 GUI 邀请成员 — 暂时通过直接改 SQLite 加成员，V0.2 会加邀请界面。
- 没有 SSO/OIDC、没有 CSRF token、没有 HTTPS（依赖部署方反向代理）。
- 没有 AI workflow 和团队记忆 — V0.2。
