# 同事一句"Kollab 这种云端 AI 我们用不了"，把我憋出一个本地版

> 公众号 / 小红书 V0.1.0 发版稿。约 2200 字，配 7 张截图。
> 直接复制粘贴用，截图占位符已经标好。

---

上周组里同事在群里转 [Kollab 推文](https://kollab.im) ——「团队 AI 协作工具，共享工作空间，一人优化全团队受益」，文章吹得挺猛。

我点开评论区，第一条赞最高的留言一句话：

> **"这是在线的？不是本地部署的吗？"**

[**配图 1：Kollab 推文截图 + 评论区第一条**]

这条评论戳中了一类用户的真实痛点：

- 法律 / 金融 / 医疗团队 —— 数据合规要求"不出公司内网"
- 创业小公司 —— 不想把客户清单 / 内部讨论交给 SaaS 的服务器
- **产研测协同场景** —— PRD、bug 描述、测试报告一旦上云，等于把公司技术路线全送出去

他们不是不要 AI 协作，他们要的是 **本地部署 + 内嵌 AI + 多人协作**。

主流方案里：

| | 部署 | 多人 | 内嵌 AI | 数据合规 |
|---|---|---|---|---|
| Kollab | 云端 | ✓ | ✓ | 跨境风险 |
| Notion AI | 云端 | ✓ | ✓ | 跨境风险 |
| Obsidian | 本地客户端 | ✗（git 凑） | 插件 | 本地 |
| Confluence | 自建/云 | ✓ | ✗ | 看版本 |

**没有一个完全踩中 toB 技术团队的真需求**：本地 + 多人 + AI 内嵌 + 三者全要。

我花了两周把这个空白填了，叫它 **praxnest** —— 团队栖身的"巢穴"。

---

## 一句话定位

> 本地优先的产研测协同空间。**像 Obsidian 一样本地，但天生为团队设计；像 Notion AI 一样会回答，但所有数据在你内网。**

具体能干啥：

- 共享 markdown 工作空间（双链 + 反向引用 + FTS5 全文搜索）
- AI 三件套：**PRD 助手 / Bug 协同 / 测试报告**（每个 workflow 一键运行）
- 团队记忆：你写新文档时，AI 会主动告诉你"3 周前 X 同学写过类似的"
- docker-compose 一键起，5 分钟内网部署完
- 完全 MIT 开源，可 fork 改

---

## 5 分钟跑起来（实测）

**装 Docker → clone repo → 起服务 → 浏览器登录**：

```bash
git clone https://github.com/ChanningLua/praxnest
cd praxnest
docker-compose up -d                                            # 第一次构建 ~2 分钟
docker-compose exec praxnest python -m praxnest init \
    --admin-username admin                                       # 提示输 admin 密码
```

打开浏览器：

- 同机访问 `http://localhost:7878`
- 局域网访问 `http://<服务器IP>:7878`（docker-compose 默认绑了 0.0.0.0）

[**配图 2：登录页 — 暗色主题，居中卡片，praxnest logo + "本地团队 AI 协作空间"副标题**]

第一次进面板会自动弹「新工作空间」对话框 —— 起个名字（比如 `Engineering`），创建第一篇笔记，故事就从这里开始。

[**配图 3：首屏 — 工作空间下拉打开 + 笔记树 + 编辑器中间 + 右栏三个 tab（反向引用 / AI 助手 / 团队记忆）**]

---

## 实战 demo：5 人小团队做"用户登录"功能

**主角**：1 个 PM、2 个研发、1 个测试、1 个 PM 助理。

### 第一步：PM 写 PRD

PM 在 praxnest 起一篇笔记 `登录功能 PRD`，把需求写进去：

```markdown
# 登录功能 PRD

## 背景
用户反馈忘记密码后没法重置...

## 需求
1. 邮箱 + 密码登录
2. 忘记密码：邮箱重置链接，1 小时过期
3. 错误次数 5 次锁定 15 分钟
...
```

PM 写完，**选中整篇 PRD，点顶栏 "AI 助手 ▾" → "生成测试用例"**。

[**配图 4：编辑器顶栏 AI 助手下拉打开，PRD 三件套高亮**]

10 秒后，AI sidebar 输出：

```markdown
## 测试用例 TC-001 ~ TC-015

### TC-001 正常登录
- 前置条件：用户 alice@example.com 已注册，密码 hunter2hunter
- 操作步骤：
  1. 访问 /login
  2. 输入邮箱、密码
  3. 点击"登录"
- 期望结果：跳转到 /dashboard，session 写入 cookie

### TC-002 邮箱格式错误
- 前置条件：未注册用户
- 操作步骤：输入 "not-an-email" + 任意密码 + 登录
- 期望结果：表单提示"邮箱格式不正确"，不发起请求

### TC-003 锁定后再尝试
...（继续 12 条）
```

[**配图 5：AI sidebar 打开，左半屏 PRD 笔记，右半屏 AI 输出测试用例 markdown**]

PM 把这段直接复制到新笔记 `登录功能测试计划`，分享给测试同学。

### 第二步：测试做完一轮，要写报告

测试同学跑完一轮自动化测试，把 json 结果粘到笔记 `Sprint 5 测试报告` 里：

```json
{
  "total": 42, "passed": 38, "failed": 4,
  "failures": [
    {"case": "TC-007 锁定状态下管理员强制登录", "reason": "401 Unauthorized"},
    {"case": "TC-013 重置链接 24h 后仍可用", "reason": "expired check missing"},
    ...
  ]
}
```

选中 → AI 助手 → **生成执行摘要** → 30 秒后输出可直接发飞书群的格式：

```markdown
📊 总览
- 通过率: 38 / 42 (90.5%)
- 新增失败: 4 条（TC-007 / TC-013 / TC-022 / TC-035）
- 持续失败: TC-013（已连续 3 个 sprint 失败，建议升 P1）

🔴 必须修
- TC-007 管理员强制登录 401 — 看 auth 中间件 admin bypass 逻辑
- TC-013 重置链接过期检查缺失 — schema 里 expires_at 字段没接

🟡 关注
- TC-022 锁定时间偏差 ±2s — 时钟同步可疑

✍ 建议
- TC-013 升 P1 进入下个 sprint
- 考虑加锁定相关的补充测试用例（TC-022 是边界）
```

测试同学复制到飞书群发出，研发当场认领修复。

### 第三步：发现新 bug，AI 帮套模板

第二天测试发现一个奇怪的现象，简单写下：

```
登录有时候点了没反应，重试一下就好了
```

选中 → AI 助手 → **套标准 bug 模板** → AI 输出：

```markdown
## 现象
用户点击登录按钮后，约 10-20% 概率页面无响应（无错误提示，无网络请求发出）。

## 影响范围
[待填] —— 看起来不分用户群，但需要进一步确认

## 复现步骤
1. 访问 /login
2. 输入正确凭据
3. 点击"登录"按钮
4. ⚠ 间歇性出现：按钮无响应，无网络请求

## 期望结果
每次点击都触发登录请求，要么成功要么明确报错。

## 实际结果
约 10-20% 概率按钮事件没触发请求。

## 环境信息
- OS: [待填]
- 版本: [待填]
- 浏览器: [待填]

## 已尝试的排查
[原描述未提及]
```

测试只填三个 [待填]，bug 报告就完整了。

### 第四步（一个月后）：团队记忆显灵

下个 sprint 研发又要做"忘记密码"功能，开始写新 PRD `密码重置 PRD`。

写到一半，praxnest 右栏「**📦 团队记忆**」自动跳出：

```
🔍 找到 3 条相似笔记：

📄 登录功能 PRD                                   (Engineering)
   ...邮箱重置链接，1 小时过期...

📄 Sprint 5 测试报告                              (Engineering)
   🔴 TC-013 重置链接过期检查缺失 — 已连续 3 sprint 失败...

📄 Bug-007 重置链接被人爆破                       (Security)  📦 跨工作空间
   ...建议加 IP 频率限制...
```

[**配图 6：团队记忆 panel 命中三条笔记，第三条带 "📦 跨工作空间" 标签**]

研发一看：

> 哦，原来登录功能的重置链接已经埋过坑（测试报告里 TC-013），而且安全组那边的 bug 报告还提过 IP 频率限制 —— 这次新 PRD 直接把这两条加进去，避免重复踩。

**这是 praxnest 区别于其他工具的真正价值**：不只是一个共享文档库，**团队的脑子开始为团队服务**。

---

## 三大支柱

| | V0.1 | V0.2 |
|---|---|---|
| **协同空间** | markdown / 双链 / FTS5 / LWW 编辑 | Yjs CRDT 实时协作 |
| **AI Workflow** | PRD / Bug / 测试报告 三件套 | 自定义 workflow yaml |
| **团队记忆** | FTS5 关键词 + bigram 相似度 | 向量 embedding（API 不变） |

V0.1 一切都是**端到端做完**才发版的 —— 不是 demo，不是 mock。**101 个单元测试覆盖**，docker-compose 在干净 Mac 上 5 分钟可起。

---

## 数据去哪

```
<data-dir>/
├── praxnest.db       SQLite — 用户 / 工作空间 / 笔记 / audit
├── session-secret    cookie 签名密钥（chmod 600）
└── (V0.2 会加 attachments/ vault 等)
```

docker-compose 默认挂 `./data/`。**没有任何外发请求 —— 不接 LLM 云、不接遥测、不接服务端**。AI 部分通过 [praxagent](https://www.npmjs.com/package/praxagent) 这个 CLI 在容器里调你自己配的 LLM（OpenAI / Anthropic / GLM / 任意中转站）。

---

## 安全 + 局限

V0.1 的明确限制（写在 README 里，不藏着掖着）：

- session cookie + bcrypt 防偶然访问；**不是企业级 SSO**，要 SSO/OIDC 等 V0.2
- 没有 RBAC，admin / member 两档够用；细粒度权限要 V0.2
- 没有 HTTPS（部署方自己 nginx/caddy 反代）
- 实时协作是 LWW（last-write-wins）+ 冲突 modal —— 不是 CRDT，并发改同一篇会撞

适合的部署场景：**公司内网 / 受信团队 5-30 人**。公网暴露请加反代 + 限速。

---

## 立即试试

```bash
# Docker 路线
git clone https://github.com/ChanningLua/praxnest && cd praxnest
docker-compose up -d
docker-compose exec praxnest python -m praxnest init --admin-username admin

# npm 路线（已装 Python 3.10+）
npm install -g praxnest praxagent
praxnest init --admin-username admin
praxnest serve
```

**npm**：[https://www.npmjs.com/package/praxnest](https://www.npmjs.com/package/praxnest)
**GitHub**：[https://github.com/ChanningLua/praxnest](https://github.com/ChanningLua/praxnest)
**Issue / PR**：欢迎来。

[**配图 7：5 分钟 demo asciinema gif —— docker-compose up → init → 浏览器打开 → 创建工作空间 → 写第一篇笔记 → AI 生成测试用例**]

---

## 它**不**解决什么 — 别被吹爆

写到这里有必要泼一盆冷水。**praxnest V0.1 大概只覆盖了团队协作痛点的 30%**。

它**不解决**：

- IM 即时聊天（去用飞书 / 企微）
- 视频会议（去用腾讯会议 / Zoom）
- 任务管理 / 看板（去用 Linear / Jira）
- 代码 review（去用 GitHub）
- 实时秒级协作（V0.1 是 last-write-wins，撞了弹冲突；V0.2 上 CRDT）
- 附件 / 图片（V0.1 纯 markdown，bug 截图贴不了；V0.2 加）
- 通知 / 提醒（V0.2 接 prax notify）
- 移动端 / 离线（不做）
- 细粒度权限 / SSO（V0.3+）
- 历史回滚（V0.1 audit log 只记"谁改了"不存内容快照）

它**只**做一件事：**把产研测三类文档 (PRD / bug / 测试报告) 以及它们之间的关联，从云端收回到你内网，加上 AI 助手 + 团队记忆**。

适合的场景：5-30 人技术团队 + 公司内网部署 + 数据合规要求 + 接受异步（不秒级）协作。

如果你的核心痛点在上面"不解决"那一栏 —— **先别试 praxnest**，那是别的工具的活。

## 一句话收

云端协作工具卷得很厉害，但卷的方向是错的。技术团队对那一块要的不多 —— 就是「**这三类文档别离开我们机房，AI 还能帮上忙**」。

praxnest 把这一块做了。MIT 开源，docker 一键起，101 单元测试。

诚实的话已经说完。剩下你自己决定。

---

🪺 *praxnest is part of the [prax](https://github.com/ChanningLua/prax-agent) family — local-first agent runtime + AI tools for power users.*
