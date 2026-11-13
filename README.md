# 指标口径与分析实验平台

面向业务指标定义、口径管理、聚合查询、分群分析、A/B实验设计、分析方案管理与数据服务发布的数据分析工程平台。

这是一个前后端分离的管理平台：前端 Vue 3 + Vite + TypeScript，后端 FastAPI（Python）。
两边各自独立启动，前端 dev server 已关掉自动打开页面，启动后按终端打印的地址手工打开。

## 目录结构

```text
.
├── frontend/                 Vue 3 + Vite + TypeScript 前端
│   ├── src/views/            每个业务模块一个页面
│   ├── src/api/              统一请求封装
│   ├── src/stores/           会话与筛选状态
│   └── vite.config.ts        dev server 配置（open: false）
├── backend/                  FastAPI（Python） 后端
│   ├── app/routers/          每个业务模块一组接口
│   ├── app/services/         业务规则与状态流转
│   └── app/store.py          内存数据仓库与示例数据
├── .gitignore
└── docker-compose.yml
```

## 启动

### 后端

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
./run.sh
```

健康检查：`curl http://127.0.0.1:8000/api/health`

### 前端

```bash
cd frontend
npm install
npm run dev
```

前端默认监听 `http://127.0.0.1:5173/`，dev server 不会自动打开浏览器，
需要自己访问。`/api` 由 vite 代理到后端 `http://127.0.0.1:8000`。

## 实验结项到分析方案的结论沉淀

实验结项不是只改状态，而是把结论沉淀成分析输出并交给分析方案启用。链路如下：

```text
metric（核心指标口径，可按版本登记）
   │ 口径版本
   ▼
abtest（实验：核心指标、最小样本量）
   │ 结项 + 实际样本量 + 样本范围 + p值
   ▼
experiment_conclusion（结论快照：实验版本、结论版本、显著性结果、对比基准、样本范围）
   │ 同一结论
   ▼
analysis_output（分析输出快照：输出版本、指标口径版本、显著性结果）
   │
   ▼
analysis（分析方案：关联实验编号 + 实验版本 + 结论版本 + 指标口径版本，启用）
```

生效边界：

- **样本量门槛**：实际样本量未达实验的最小样本量、缺样本范围或缺 p 值时只记结论门槛
  不通过，不生成分析输出，实验仍为「已结项」并写入 `待补充分析项`。
- **版本快照**：结论与分析输出都冻结生成时的实验版本、结论版本、指标口径版本、
  对比基准和样本范围；后续任何一边变化都不会回写历史。
- **重复结项**：同一实验再次结项且核心指标未换版时，沿用同一结论编号与版本，
  不新增方案版本、不新增分析输出。
- **多实验共享指标**：每个实验各自生成独立的结论、分析输出和分析方案；即使核心指标
  相同，样本范围与历史结论也分开保存，不共用对比基准。
- **指标换版**：核心指标口径升版后，旧分析输出保留原版本与旧显著性结果，状态改为
  `已失效`；新结论必须重新计算 p 值才能生成新版输出，不能直接复制旧显著性结果。
  旧方案同步退回「方案评审中」并带 `待补充分析项`。
- **启用失败**：分析方案不存在、已归档或缺少必填字段时，实验保持「已结项」，
  `分析状态` 记为 `启用失败`，`待补充分析项` 说明原因；补齐后在重复结项时会重试启用。

### 结项与结论接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/abtest/{id}/actions` | 结项动作，`values` 可带 `实际样本量`、`样本范围`、`p值`、`分析方案编号` |
| GET | `/api/abtest/{id}/detail` | 实验详情：结论、分析输出、关联方案与审计，带版本字段 |
| GET | `/api/abtest/trace` | 按实验/结论/方案/输出版本互查 |
| GET | `/api/analysis/{id}/detail` | 分析方案详情：输出、结论、实验与审计 |
| GET | `/api/analysis/outputs` | 分析输出清单，换版后旧版本保留并标 `已失效` |
| GET | `/api/analysis/outputs/{编号}` | 分析输出详情，可按输出版本回查结论与实验 |
| GET | `/api/analysis/audits` | 审计记录，按实验编号、结论版本、方案编号过滤 |
| POST | `/api/metric/{id}/versions` | 登记同一指标的新口径版本（只新增，不改旧行） |

`ActionResult.extra.settlement` 回传结项沉淀结果：`status` 为
`generated`、`unchanged`、`blocked` 或 `enable_failed`，并带 `conclusion`、
`output`、`analysis_plan` 与顶层 `待补充分析项`。

## 业务模块

| 模块 | 目录 | 业务对象 | 主要字段 |
| --- | --- | --- | --- |
| 指标口径 | `metric` | 指标定义 | 指标编号、指标名称、计算公式 |
| 聚合查询 | `queryboard` | 查询模板 | 模板编号、指标名称、维度字段 |
| 分群分析 | `segment` | 人群分群 | 分群编号、分群名称、筛选规则 |
| 实验设计 | `abtest` | 实验方案 | 方案编号、实验名称、对照组定义、核心指标、最小样本量 |
| 分析方案 | `analysis` | 分析方案 | 方案编号、分析主题、数据集范围、核心指标、关联实验编号 |
| 看板配置 | `dashboard` | 看板页面 | 看板编号、看板名称、包含指标 |
| 数据服务 | `delivery` | 服务版本 | 服务编号、数据集名称、查询参数定义 |
| 漏斗分析 | `funnel` | 漏斗定义 | 漏斗编号、漏斗名称、步骤序列 |
| 同期群分析 | `cohort` | 同期群定义 | 群组编号、群组名称、划分事件 |
| 归因模型 | `attribution` | 归因规则 | 规则编号、归因类型、回溯窗口天 |
| 指标预警 | `alertconfig` | 预警规则 | 预警编号、关联指标、比较运算符 |
| 报表调度 | `reportsch` | 报表任务 | 任务编号、报表名称、包含指标 |
| 维度属性 | `dimprop` | 维度属性 | 属性编号、维度名称、属性名称 |
| 多维下钻 | `datadrill` | 下钻路径 | 路径编号、起始维度、下钻层级 |
| 预测模型 | `forecast` | 预测方案 | 方案编号、预测目标、算法名称 |
| 标签管理 | `tagmgr` | 标签定义 | 标签编号、标签名称、标签分类 |
| 洞察推送 | `insight` | 洞察报告 | 报告编号、报告标题、分析主题 |
| 数据验证 | `dataval` | 验证任务 | 任务编号、验证数据集、验证类型 |

## 约定

- 每个模块的前端页面在 `frontend/src/views/<模块>/index.vue`，后端接口在
  `backend/app/routers/<模块>.py`，业务规则在 `backend/app/services/<模块>.py`。
- 列表接口统一返回 `{ items, total, page, size }`，动作接口统一返回 `{ ok, message }`。
- 状态流转只允许在 `app/services` 里改，路由层不做业务判断。
- 结项沉淀的规则集中在 `backend/app/services/conclusion.py`；实验、结论、分析输出、
  分析方案之间只通过版本字段关联，任何一方换版都不改写历史记录。
- 验收用例在 `backend/tests/test_conclusion_flow.py`，覆盖正向链路与「报错、不生效、
  残留、对不上、显示成旧值、重复显示」的反向案例。运行：

  ```bash
  cd backend
  .venv/bin/pip install -r requirements-dev.txt
  .venv/bin/python -m tests.test_conclusion_flow
  ```

领域：数据工程
子方向：数据分析与数据应用
