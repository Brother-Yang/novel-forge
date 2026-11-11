# 待优化问题

本清单只保留未解决问题，来源于 2026-11-11 的代码审查和离线验证。

维护方式：一次选取一个条目，完成修复、对应回归测试和必要文档更新后，在同一批改动中删除该条目。未完成验收时继续保留；部分完成时写明剩余范围，不标为已完成。编号保持稳定，删除后不重新编号。已完成工作的历史由 Git 保存，不在本文件累积完成列表。

## 数据与生成可靠性

| 编号 | 优先级 | 待解决问题与入口 | 验收条件 |
| --- | --- | --- | --- |
| OPT-001 | P2 | 设定新增/修改/删除、手工修改正文或删除章节后，SQL 与向量记忆不同步。入口：`backend/app/api/worldbuilding.py`、`chapters.py`、`memory/context.py`、`memory/vector_store.py`。 | 新内容可检索；旧内容更新或删除后不再召回；索引失败可重试，正文不丢失。 |
| OPT-002 | P2 | 章摘要、嵌入或卷摘要失败后只有日志，正文已完成，普通续写不会补齐记忆。入口：`backend/app/agents/pipeline.py`、`memory/context.py`。 | 为记忆失败提供可观测、可重试的状态；修复记忆时不重写已完成正文。 |
| OPT-003 | P2 | SQLite 默认未开启外键，删书后设定、剧情线程、任务、轨迹和向量映射等可能残留；运行中的任务与删书缺少协调。入口：`backend/app/database.py`、`models.py`、`api/books.py`、`tasks/book_generation.py`。 | 全部连接启用正确约束；删除后无孤儿数据、无继续写入任务；处理已有孤儿数据并验证迁移兼容性。 |
| OPT-004 | P2 | 章节创建/修改/排序未校验卷是否属于同一本书，导出只按卷筛选章节，可能串书。入口：`backend/app/api/chapters.py`、`export.py`。 | 拒绝不存在或跨书的卷关联；无效批量排序不部分写入；导出不混入其他书籍章节。 |
| OPT-005 | P2 | 生成阶段通过“已有任意数据”判定完成。世界观只提交一条、或卷大纲已提交但剧情线程未提交时崩溃，恢复后会跳过未完成部分。入口：`backend/app/agents/pipeline.py`、`tasks/novel_generation.py`。 | 在阶段内不同提交点模拟崩溃，恢复后补齐缺失内容且不重复已有内容。 |
| OPT-006 | P2 | 模型输出的卷/章纲结构与数量缺少完整校验，部分解析成功仍可能留下不完整计划。入口：`backend/app/agents/pipeline.py`。 | 无结构、缺项和数量不足时有明确错误及恢复方式；不得把少量已解析结果当作整个阶段完成。 |
| OPT-007 | P2 | 长篇规模被静默截断：规划最多 12 卷、每卷 20 章，默认 2500 字/章时 100 万、200 万目标均只规划 60 万字。入口：`backend/app/agents/planner.py`。 | 按目标扩展分阶段计划，或明确拒绝超容量目标并说明上限；覆盖百万字及以上规模的纯函数测试。 |
| OPT-008 | P2 | RAG 章摘要检索不限制时间位置，重写早期章节时可能引用后续剧情；人物状态和剧情线程缺少自动推进/关闭流程。入口：`backend/app/memory/context.py`、`agents/pipeline.py`、`models.py`。 | 重写时仅召回允许的历史；正文事实可以更新人物/线程状态，更新失败可追踪，支持修订后重建。 |
| OPT-009 | P2 | 任务执行登记仅在进程内，启动恢复没有跨进程任务认领；多 worker 可能重复执行同一任务，手工生成与后台任务也缺少统一互斥。入口：`backend/app/tasks/book_generation.py`、`api/generate.py`。 | 明确单进程部署约束，或实现数据库级认领/租约；并发请求和多 worker 测试不重复生成或覆盖正文。 |
| OPT-010 | P2 | 用户手工清空已完成正文后，章节元数据仍可能显示 `done`。入口：`backend/app/api/chapters.py:update_chapter_content`。 | 空白正文保存后状态与实际内容一致；目录和任务进度不显示虚假完成，正常非空保存不受影响。 |

## 前端交互与性能

| 编号 | 优先级 | 待解决问题与入口 | 验收条件 |
| --- | --- | --- | --- |
| OPT-011 | P2 | SSE 在 CRLF 跨网络块时错误拆事件，`trimStart()` 吞掉正文前导空格。入口：`frontend/src/api/client.ts`。 | 覆盖 CR/LF 任意分块、UTF-8 跨块、多行 data、原始空格、结束/取消/中断；显示内容与服务端一致。 |
| OPT-012 | P2 | 当前目录树已移除拖拽排序/移卷，顶栏新建的未归卷章节无法通过界面移卷。入口：`frontend/src/components/CatalogTree.tsx`。 | 恢复排序和移卷；正确持久化顺序与归属，失败可见且不丢章节，补用户操作回归测试。 |
| OPT-013 | P2 | 工作区请求失败静默吞错并持续转圈；目录新增/重命名、卡片名称保存等仍有未处理的请求错误；目录旧加载响应可能覆盖删除后的刷新结果。入口：`frontend/src/pages/BookWorkspace.tsx`、`components/CatalogTree.tsx`、`CharacterPanel.tsx`、`WorldbuildingPanel.tsx`。 | 加载失败有错误和重试入口；所有剩余 CRUD 失败可见，不产生未处理 Promise 拒绝或误报成功；过期加载结果不覆盖新目录。 |
| OPT-014 | P3 | 所有路由及 Markdown 编辑器静态加载；评估时主 JS 约 2.07 MB、gzip 约 687 KB。入口：`frontend/src/router/index.tsx`、人物/设定面板。 | 按路由/重组件延迟加载，比较构建产物确认首页不提前下载编辑器，加载与失败体验完整。 |
| OPT-015 | P3 | Agent 轮询每次拉取完整轨迹，并强制滚到底部，长任务传输累积且打断阅读历史。入口：`frontend/src/pages/AgentChat.tsx`、`backend/app/agents/observability.py`、`api/generate.py`。 | 轨迹增量/分页获取；用户上翻后不强制滚动；长任务下无重复/丢失事件，失败可恢复。 |

## 工程与部署

| 编号 | 优先级 | 待解决问题与入口 | 验收条件 |
| --- | --- | --- | --- |
| OPT-016 | P1（公网前） | 读写、导出、生成接口无鉴权和调用额度限制。入口：`backend/app/main.py`、`api/`。 | 公网部署具备可靠鉴权、资源访问控制和生成限额；未授权调用不能读取/删除书稿或消耗模型额度。 |
| OPT-017 | P2 | 后端依赖多数使用 `>=`，无法复现经过验证的环境。入口：`backend/requirements.txt`、`requirements-dev.txt`。 | 按 AGENTS.md 锁定实际验证通过的稳定版本，保留可复现安装方式；离线 E2E 与单元测试通过。 |
| OPT-018 | P2 | Alembic 没有完整建库基线，应用仍先 `create_all()` 再迁移。入口：`backend/app/database.py`、`migrations/versions/`。 | 空库仅运行迁移即可建立完整结构；旧库升级保留数据；应用启动不掩盖缺失迁移。 |
| OPT-019 | P2 | 缺少 CI；现有测试对 HTTP 契约、故障恢复、网络竞争和真实模型长篇表现覆盖不足。入口：`.github/workflows/`（待建立）、前后端测试。 | CI 跑 lint/test/build 与后端 pytest/离线 E2E；按具体风险补回归，另建可控成本的真实模型质量评估，不能把假模型通过等同成文质量。 |
| OPT-020 | P3 | 本轮 P1 修复后 basedpyright 仍有 54 个存量测试文件错误、6 个警告；初次评估的 Ruff 有 62 条诊断，其中 43 条为 FastAPI Depends 默认参数规则。入口：`backend/tests/`、类型与 lint 配置。 | 修复真实类型问题，合理配置框架规则；不靠整文件禁用检查掩盖问题；检查结果可作为 CI 门禁。数量以修复时实际检查为准。 |
| OPT-021 | P3 | 基线前端格式检查有 6 个文件未通过：BrandMark、CharacterPanel、WorldbuildingPanel、index.css、AgentChat、BookList。 | 使用既有 Prettier 配置清理格式；`pnpm format:check` 通过，不混入行为改动。 |
| OPT-022 | P2 | RAG 检索异常被 `except Exception: pass` 静默忽略，流式接口异常仅返回 SSE 错误，日志定位不足。入口：`backend/app/memory/context.py`、`api/generate.py`。 | 降级/失败日志包含书籍、章节或任务标识及异常；不记录密钥；用户能区分记忆降级与正文失败。 |
