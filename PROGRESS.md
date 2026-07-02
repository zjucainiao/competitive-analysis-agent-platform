# 项目进度

> 工作日志：记录每次修改的内容与当前状态，改完即更新（新条目加在最上面）。
> 从 2026-07-02 起记录，不回溯历史（历史改动见 `git log`）。

---

## 当前状态

- 线上版本：**v1.2.0**（at1as.tech，`deploy.sh <tag>` 部署，`/version` 可查）
- 当前分支：`docs/cleanup-dev-leftovers`（文档清理，工作区干净）

## 进行中

_（无）_

## 待办（来自 2026-07-02 全项目评审，按优先级）

### P0 — 上线阻断 / 正确性（✅ 全部完成于 2026-07-02，分支 `fix/p0-hardening`）

- [x] JWT 密钥硬失败：postgres 形态缺 `JWT_SECRET` 拒绝启动（`ensure_jwt_secret` 接在 lifespan 最前），`JWT_ALLOW_INSECURE_DEV=1` 显式豁免；memory 形态保留开发默认值+告警。新增 5 个测试
- [x] 报告页 Markdown 导出去 mock 硬编码：`renderReportAsMarkdown` 接收真实 report + 证据查找，导出的=屏幕上看到的
- [x] 导出带鉴权：新增 `fetchProjectExport`（Bearer fetch → blob 下载，文件名从 Content-Disposition 解析），删掉会被 Starlette 重复渲染的 HEAD 预检
- [x] pytest 守卫：`addopts = -m "not e2e"` + 补齐 marker + 测试模块级 `load_dotenv` 收进门控 + storage conftest 环境快照防御。裸跑全绿：421 passed / 15 skipped / 8 deselected
- [x] discovery 同步 LLM 调用挪进 `asyncio.to_thread`（全量排查其余 8 个路由无同类问题），新增事件循环探测测试
- [x] 超时不再重试：TimeoutError 直接 FAILED（code=LLM_TIMEOUT），僵尸并发从最多 4 份压到 1 份；legacy executor 同步修，防双引擎漂移。TDD 测试锁死
- [x] 最小 CI：`.github/workflows/ci.yml`（backend: ruff(观察模式)+pytest；frontend: tsc+eslint+build）

### P1 — 架构一致性 / 证据链可信度

- [ ] native 引擎不消费 Planner 产物：AdaptivePlanner 白烧 LLM、官网种子/维度/超时配置全部静默失效（`orchestrator.py:163-215`、`nodes.py:50-71`）
- [ ] REVIEWS 维度证据是 LLM 合成文本，硬编码 identity confirmed，无联网 provider 时可能整条幻觉（`collector/agent.py:1277-1364`）
- [ ] `_is_official` 产品名子串匹配可被伪冒域名利用（`collector/agent.py:196-198`）
- [ ] fuzzy 命中时 Evidence.content 是 LLM 转述仍标 VERIFIED（`extractor/agent.py:970-976`）
- [x] ~~无任何 CI~~ 最小 CI 已随 P0 落地（`fix/p0-hardening`），lint/format 已收紧为阻断（`chore/ruff-cleanup`）
- [ ] restart/retry/edit-prompt 的 run 永远没有 final_status 和快照，`/runs/{id}/state` 404（`interventions.py:106-123,679-691`）
- [ ] run 控制面单进程假设无保护，多 worker 下防重/暂停/停止静默失效（`runs.py:103-121`）
- [ ] 前端 runId 路由段未消费，run 回放名存实亡；listRuns/getRunSnapshot 死代码（`client-workspace.tsx:243-263`）

### P2 — 中期改进

- [ ] legacy 引擎退役计划（已实证两处"native 修了 legacy 没修"的漂移）
- [ ] PII sanitizer 只挂观测层，Evidence/报告落库不脱敏（`backend/tools/sanitizer.py`）
- [ ] 22 个 prompt 仅 4 个有 UNTRUSTED 数据区隔离，报告撰写环节无隔离
- [ ] memory/PG 的 QAVerdict 语义分叉（append vs upsert）；verdict 覆写丢 run_id
- [ ] 登录/LLM 端点无限流；无 refresh token/吊销；WS token 走 query param 进日志
- [ ] checkpoint pickle 落库（DB 写权限 → RCE 面）；无 alembic 迁移
- [ ] 数字验证是拼接 haystack 存在性匹配，跨产品数字互相背书（`reporter/tools.py:166-212`）
- [ ] 前端：进度条恒 99% 的投影缺陷、假按钮（Publish/Investigate/Add note）、指标盘 mock 混排、枚举中文映射 7+ 处复制
- [ ] localdb.py 依赖 pgserver/redislite 未声明，新人按 README 跑必挂；后端无依赖锁文件
- [ ] 前端 2.1 万行零测试；mypy 166 errors 挂账

### P1/P2 追加（P0 修复过程中新发现）

- [x] ruff 存量清零（5696 → 0，含 386 个真实问题）+ ruff format 全仓库统一（111 文件）+ CI 的 ruff/format 收紧为阻断门禁（分支 `chore/ruff-cleanup`，2026-07-02）
- [ ] `backend/api/app.py:38` 模块级 `load_dotenv()` 是 .env 泄漏进测试会话的根源（storage conftest 已做快照防御），根治应挪到应用入口
- [ ] native 引擎 NodeRun 无 error 字段，超时错误码（LLM_TIMEOUT）没透传到前端节点状态（`run_state.py` + `nodes.py`）

---

## 更新日志

### 2026-07-02

- **ruff 存量清零 + format 统一 + CI 收紧**（分支 `chore/ruff-cleanup`，堆叠在 fix/p0-hardening 上）：5696 → 0（自动修 304 + 手工 62 + 配置豁免带论证）；顺手修掉 `inputs.py` 缺 import（F821）与 `sanitizer.py` 闭包晚绑定（B023）两处潜在问题；`ruff format` 统一 111 文件；CI 的 ruff/format 从观察模式改为阻断门禁
- **修完全部 6 项 P0**（分支 `fix/p0-hardening`）：JWT 硬失败闸门、导出双修（去 mock + 带鉴权下载）、pytest e2e 守卫、discovery 事件循环阻塞、超时不重试（双引擎）、最小 CI。验收：裸跑 pytest 421 passed / 8 deselected（e2e 被正确拦截）；前端 tsc/eslint/build 全绿
- 同步事实性文档修正：`docs/COMPLIANCE.md` JWT 描述、`docs/DEPLOY_PROD.md`、`.env.example`、`CONTRIBUTING.md`；本机 `.env`（不入仓）已配随机 `JWT_SECRET`
- 行为变化注意：显式跑真实 e2e 现在必须带 `-m e2e`（如 `RUN_REAL_LLM_TESTS=1 pytest <file> -m e2e`）
- 完成全项目专家级分析评审（编排器 / Agent 实现 / API·存储·安全 / 前端 / 工程实践 五路并行），结论按 P0/P1/P2 填入「待办」
- 建立本进度文档，约定维护方式：每次改动后更新「当前状态 / 进行中 / 待办」并在此追加条目
