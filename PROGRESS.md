# 项目进度

> 工作日志：记录每次修改的内容与当前状态，改完即更新（新条目加在最上面）。
> 从 2026-07-02 起记录，不回溯历史（历史改动见 `git log`）。

---

## 当前状态

- 线上版本：**v1.2.0**（at1as.tech，`deploy.sh <tag>` 部署，`/version` 可查）
- 当前分支：`fix/run-lifecycle`（run 生命周期收尾统一 + 控制面并发/单进程保护，工作区未提交）

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

- [x] native 引擎不消费 Planner 产物：AdaptivePlanner 白烧 LLM、官网种子/维度/超时配置全部静默失效 —— 已修（分支 `fix/native-consumes-plan`）：`extract_plan_directives` 把 plan 元数据折叠进 `RunState.plan_directives`，collector 官网种子/维度经 Send payload 生效，超时/重试优先 plan、缺省回退下限表
- [x] REVIEWS 维度证据是 LLM 合成文本，硬编码 identity confirmed，无联网 provider 时可能整条幻觉 —— 已修（分支 `fix/evidence-chain-integrity`）：fetch_method=llm_synthesis + ambiguous + authority 0.4 + 禁伪造 G2 URL，QA 校正不再抬回高权威
- [x] `_is_official` 产品名子串匹配可被伪冒域名利用 —— 已修（同分支）：注册域主标签精确等值匹配，伪冒域（notion-fans.xyz / evilnotion.so）判非官方
- [x] fuzzy 命中时 Evidence.content 是 LLM 转述仍标 VERIFIED —— 已修（分支 `fix/evidence-chain-integrity`）：content 一律落 linker 定位的原文切片，fuzzy 置信封顶 0.85，offset 用规范化映射精确回定位
- [x] ~~无任何 CI~~ 最小 CI 已随 P0 落地（`fix/p0-hardening`），lint/format 已收紧为阻断（`chore/ruff-cleanup`）
- [x] restart/retry/edit-prompt 的 run 永远没有 final_status 和快照，`/runs/{id}/state` 404 —— 已修（分支 `fix/run-lifecycle`）：收尾抽成共享 `backend/api/run_lifecycle.py`，start/restart/retry/edit-prompt/evidence-rework/resume 统一走，legacy evidence-rework 补建 RunRef
- [x] run 控制面单进程假设无保护，多 worker 下防重/暂停/停止静默失效 —— 已修（同分支）：spawn 锁原子化「检查→create_task→登记」堵 TOCTOU；lifespan 检测多 worker 环境变量即拒启，约束写入 docs/DEPLOY_PROD.md
- [x] 前端 runId 路由段未消费，run 回放名存实亡；listRuns/getRunSnapshot 死代码（`client-workspace.tsx:243-263`）—— 已修（分支 `fix/run-lifecycle`，只动 `frontend/src/`）：runId 生效（最新 run/别名→实时视图，历史 run→快照只读回放），运行历史下拉可点击切换，`listRuns`/`getRunSnapshot` 转正为 `useRunHistory`/`useRunSnapshot` 的数据源

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

- **修 P1×2：run 生命周期收尾统一 + 控制面并发/单进程保护**（分支 `fix/run-lifecycle`，只动 `backend/api/` + docs）：① 收尾不一致——原本只有 `start_run` 结束时回写 `RunRef.final_status` 并落 `RunSnapshot`，restart/retry/edit-prompt/evidence auto-rework 只改 project status，其 run 的 RunRef 永远 `final_status=None`、无快照、`/runs/{id}/state` 404。收尾逻辑抽成新模块 `backend/api/run_lifecycle.py`（`finalize_run` 回写 RunRef 终态 + 落快照 + 异常兜底；`drive_run_to_completion` 为所有后台 run 的统一主体），六条路径统一走它：start / restart / retry / edit-prompt（收敛进 `_start_fresh_run`）/ evidence auto-rework（native 延续同一 run 身份、快照按同主键刷新为含返工产物的终态；legacy 补建 RunRef——修复「真实执行的 run 无 RunRef」缺口）/ resume（延续当前 RunRef）。取消（stop/pause/顶替）不触发收尾，保持既有语义；「超时不重试」语义不受影响。`_read_native_run_state`/`read_native_run_history` 一并迁入并支持显式 `run_id`。② 并发与单进程假设——`start_run` 的「检查 running_tasks → create_task」间隔多个 await，并发双击可起两个 run（TOCTOU）；新增进程级 `app.state.run_spawn_lock` 把所有 spawn 路径的「检查/取消旧任务→建 RunRef→create_task→登记」原子化，409 语义不变；`running_tasks`/环形缓冲的单进程假设从注释升级为启动闸门：lifespan 里 `ensure_single_worker()` 检测 `UVICORN_WORKERS`/`WEB_CONCURRENCY`/`GUNICORN_WORKERS`/`GUNICORN_CMD_ARGS --workers N` >1 即拒启（中文报错说明约束与原因），约束同步写进 `docs/DEPLOY_PROD.md` §7。TDD：新增 `backend/api/tests/test_run_lifecycle.py` 11 用例（restart/retry/edit-prompt 收尾不变式、evidence-rework 快照刷新、并发双击恰一个 409、多 worker 拒启×4、单 worker 放行、报错可操作）；全量 466 passed（基线 455+11）/ 15 skipped / 8 deselected，ruff check/format 干净
- **修 P1：native 引擎消费 Planner 产物**（分支 `fix/native-consumes-plan`）：新增 `backend/orchestrator/plan_directives.py`——`extract_plan_directives` 把 DAGPlan（模板/adaptive 两种形状）折叠成 JSON-可序列化指令集 `{products: {显示名: {official_url, collect_dims}}, nodes: {agent: {timeout_ms, max_retries}}}`，`_run_native` 写进新声明的 `RunState.plan_directives`（进 checkpoint，resume/rework 自动带回；旧 checkpoint 无键回退空 dict）。消费侧：`collect_dispatch`/`extract_dispatch` 把官网种子/维度/超时重试打进 Send payload（Send-target 看不到全局 state），collector 不再永远 `official_url=None` 走搜索兜底（「抓错产品」上游诱因之一）；analyst/reporter/qa 直接从 state 解析。超时优先取 plan、缺省回退 `NODE_TIMEOUT_FLOOR_MS` 下限表（原 `nodes.py` `_NODE_TIMEOUT_MS` 挪入，事故精调下限语义不变；低于下限的 plan 值提取时钳底），「超时不重试」语义保持。配置源头对齐：4 张模板 YAML 的 collect 180s→300s / analyst 120s→240s / qa 60s→180s，adaptive collect 180s→300s（reporter 600s 等更高值保留）。fail-soft：plan 为 None/提取失败 → 空指令集，一切回退现状。TDD：新增 `test_plan_directives.py` 18 用例（提取/钳底/维度过滤/序列化往返/dispatch payload/节点消费回退/官网种子 e2e/旧 checkpoint 向后兼容）；全量 455 passed（基线 437+18），ruff check/format 干净。docs/DAG.md（§1/§4/§5/§6.2/§6.3/§9/§10）与 docs/ARCHITECTURE.md 同步对齐事实，顺手修正 §6.2「native 节点不做指数退避重试」的失实描述
- **修 H1：REVIEWS 维度 LLM 合成证据可区分、可降权**（分支 `fix/evidence-chain-integrity`）：`_reviews_finding_to_docs` 的产物改标 `fetch_method="llm_synthesis"`（schema/前端类型/docs 同步加枚举值，向后兼容）；身份不再硬编码 confirmed 0.85——合成文本上跑身份校验是循环论证，一律 ambiguous（带引用 URL 置信 0.5 / 纯聚合 0.3），QA identity_consistency 自动浮出为 minor；权威度从评论站正典 0.92 压到 0.4（新常量 `LLM_SYNTHESIS_AUTHORITY`，低于 QA 弱源阈值 0.7）；LLM 未给引用 URL 时不再伪造 G2 URL，改用 RFC 2606 `.invalid` 合成标记 URI；QA `evidence_completeness` 的跨维度权威校正改取 `min(矩阵值, 存值)`，防止合成证据被校正「抬回」高权威。TDD：新增 `collector/tests/test_reviews_synthesis.py` 5 用例 + QA 侧 1 用例
- **修 H3：`_is_official` 伪冒域名直通官方判定**（同分支）：产品名子串匹配 + `host.endswith(官方域)` 两条模糊路径删除（`notion-fans.xyz`/`evilnotion.so` 过去可直通 confirmed 0.9 + authority 0.95）；改为注册域主标签精确等值（`_domain_label` 重写：剥 TLD + 常见双后缀 co.uk/com.cn，不引 tldextract），官方 URL 子域仍放行。TDD：`test_identity.py` 新增 5 用例。验收：collector+extractor+qa 134 passed，全 backend（除 storage e2e 的既有环境泄漏问题）413 passed，ruff check/format 干净
- **修 H4：证据原文逐字承诺**（分支 `fix/evidence-chain-integrity`，只动 `backend/agents/extractor/`）：EvidenceLinker 改用「规范化位置 → 原文位置」映射——精确命中的 char_start/char_end 不再靠 12 字符锚点近似（空白不一致时会切错）；fuzzy 命中时 Evidence.content 改存**原文窗口逐字文本**（LLM 转述 quote 不再落库），置信封顶 0.85 与精确命中（1.0）拉开；字段置信取 min(claim, link)；consolidation 占位 source 的隐式依赖写成显式注释。TDD：新增 `tests/test_evidence_verbatim.py` 5 个用例，extractor+qa+reporter 126 passed，ruff check/format 干净
- **ruff 存量清零 + format 统一 + CI 收紧**（分支 `chore/ruff-cleanup`，堆叠在 fix/p0-hardening 上）：5696 → 0（自动修 304 + 手工 62 + 配置豁免带论证）；顺手修掉 `inputs.py` 缺 import（F821）与 `sanitizer.py` 闭包晚绑定（B023）两处潜在问题；`ruff format` 统一 111 文件；CI 的 ruff/format 从观察模式改为阻断门禁
- **修完全部 6 项 P0**（分支 `fix/p0-hardening`）：JWT 硬失败闸门、导出双修（去 mock + 带鉴权下载）、pytest e2e 守卫、discovery 事件循环阻塞、超时不重试（双引擎）、最小 CI。验收：裸跑 pytest 421 passed / 8 deselected（e2e 被正确拦截）；前端 tsc/eslint/build 全绿
- 同步事实性文档修正：`docs/COMPLIANCE.md` JWT 描述、`docs/DEPLOY_PROD.md`、`.env.example`、`CONTRIBUTING.md`；本机 `.env`（不入仓）已配随机 `JWT_SECRET`
- 行为变化注意：显式跑真实 e2e 现在必须带 `-m e2e`（如 `RUN_REAL_LLM_TESTS=1 pytest <file> -m e2e`）
- 完成全项目专家级分析评审（编排器 / Agent 实现 / API·存储·安全 / 前端 / 工程实践 五路并行），结论按 P0/P1/P2 填入「待办」
- 建立本进度文档，约定维护方式：每次改动后更新「当前状态 / 进行中 / 待办」并在此追加条目
- **修 P1：前端 runId 路由段生效 —— run 回放落地**（分支 `fix/run-lifecycle`，只动 `frontend/src/`）：① `ClientWorkspace` 按 runId 分发——等于 project_id（向导/项目卡「当前运行」别名）、`current` 或最新 run_id 走原实时视图（/run-state + WS + 轮询，行为不变）；其余走历史 run 只读回放：新 `useRunSnapshot` 消费 `getRunSnapshot`（已从死代码转正，指向后端既有 `GET /runs/{run_id}/view`——不可变 RunSnapshot 的 RunStateView 投影，复用 `run-view-to-state` 与全部现有组件，不另写投影层），不开 WS/不轮询；顶栏加「历史运行」标示 + 回放提示条（带「回到最新运行」链接），状态/指标取快照终态不混入当前项目实时值。② 干预动作在回放中全禁：顶栏 pause/stop/rerun 等隐藏（`historical` prop），节点级重跑/编辑 prompt/跳过（WorkspaceApi 新增 `readOnly` 标志，stepper 按此不渲染动作）、报告段落编辑入口、证据异议（单条/批量）一并隐藏或拒绝，导出/复制类只读动作保留。③ 快照缺失（404，老数据/异常中止）→ `WorkspaceEmpty` 风格空态「该运行无快照记录」，不静默回落到最新 run。④ 运行历史下拉从只读列表改为可点击切换（Link 到 `/projects/{id}/runs/{run_id}` 保留当前 tab），数据源转正 `listRuns`（`useRunHistory`，project.runs 兜底、展开时刷新），文案全中文（当前查看/最新/进行中）。⑤ 向导跳转保持 project_id 别名并注释说明（后端 `RunStartedResponse` 不携带 run_id）。验收：`npx tsc --noEmit` / `npx eslint src` / `npm run build` 三绿；全库 grep 确认 `listRuns`/`getRunSnapshot` 均有消费方，未引入 any/@ts-ignore
