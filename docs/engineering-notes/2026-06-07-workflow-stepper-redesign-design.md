# 工作流视图重做:横向阶段步进器 + Stage D 数据迁移

> 2026-06-07 · 设计(brainstorm 收敛)。把 dag tab 从 React Flow「大卡有向图」换成 **5 阶段横向步进器**,同时完成 **Stage D**(前端直吃 `RunStateView`)。两件事天然合体:步进器与原生 5 阶段模型 1:1,Stage D 的取数重写正好用来喂步进器。

## 为什么
工作流视图现在是 React Flow 大卡画布,**视觉/布局观感不佳**(用户反馈)。同时 Stage D(删临时 DAGPlan 投影桥、前端直吃 RunStateView)一直待做。合并成一次干净重做:取数 + 呈现一次到位。

## 布局(主区,去 React Flow,纯 CSS flex)
- **顶部**:5 阶段横向步进器(采集 → 抽取 → 分析 → 撰写 → 质检)
- **下方**:选中阶段的**内联详情面板**,填充主区空白
- **再下(次要,保留)**:执行日志时间线(复用现有 `execution-log-card`,瘦身为底部条)

## 步进器(每步视觉)
- 圆点状态色:✓ success 绿 / ◐ running 蓝(旋转)/ ⚠ rework 橙 / ✗ failed 红 / ○ pending 灰
- 连接线:已完成段实色;running 段渐变/动画;未到段虚线
- 标签:阶段中文短名(信息采集 / 证据入库 / 结构化分析 / 报告撰写 / 质量审查)
- 副信息:**真实时长**(NodeRun 时间戳算,不再用旧版从字符串抠的脆 KPI);**多产品阶段**标「N 产品」;**有返工**标「↻ vN」
- 5 步固定、永不溢出(原生模型就是 5 阶段)
- **默认选中**:当前 running 阶段;无 running 则最后有活动的阶段(done 的 run 落在质检,直接看终判)

## 内联详情(选中阶段)
- **单实例阶段(分析 / 撰写 / 质检)**:agent 名 + 轮次(返工标注)+ token/时长 + self-critique + 产物链接(▸ 打开报告)+ QA 反馈摘要 + 操作按钮(编辑 prompt / 重跑 / override QA / 去 Trace)
- **多产品阶段(采集 / 抽取)**:每产品一行(`● 产品名  状态  指标  ▸ 详情`);点「▸ 详情」打开右侧 320 栏深挖
- **两级模型**:内联面板 = 阶段级摘要;右侧 `WorkspaceDetailsRail`(现有 概览/输入/输出/日志/证据 tab)= 单节点深挖,复用不浪费
- 顺带解决「信息太少」:self-critique / QA 反馈 / 返工 v1↔v2 都在详情里露出

## 数据(Stage D)
- **后端**:`RunStateView` 加 `outputs` 字段(`{run_ref: AgentOutput}`,镜像 `projection` 的 out_map 键法),**additive**,不动现有行为
- **前端**:dag tab 切 `GET /projects/{id}/run-state` 端点;新 stepper 适配器从 `stages[].instances/revisions` + `outputs` 直接映射(替代 130 行布局算法 + 控制节点折叠)
- report/evidence/trace tab 对 outputs 的消费(`findLatestReporter` / `aggregateEvidences`)复用 `RunStateView.outputs`,几乎不改

## 替换 / 删除
- 替换:`dag-canvas` / `dag-node` / `dag-toolbar` / `dag-styles`(React Flow)→ 新 stepper 组件
- `node-detail-sheet`(React Flow 节点抽屉)→ 内联详情 + 右栏接管
- `@xyflow/react` 依赖**可移除**(仅此视图用)
- 投影桥 `projection.py`:**本轮不删**(保留回退);Phase 3 再删

## 护栏
- 后端 `outputs` 字段 additive + pytest 全绿
- 前端:`tsc --noEmit` + `next build` + **登录态 Playwright 走查真实 native run**;跑不干净则 **revert 前端、保持现状,不留破 app**
- ⚠ Playwright 验证需登录(站长配合或给密码)

## 不做(本轮外)
- 删 legacy 引擎(Phase 3)· 提示词业务特化 · 部署(Docker/compose)
