# AGENTS.md

本文件面向在本仓库中工作的 AI 编码助手(Cursor / Claude Code 等),用于统一代码风格、依赖管理与工程规范。修改代码前请先阅读本文档;如与本文档冲突,以用户在对话中的明确指示为准,但应主动指出冲突点。

## 项目概览

- 后端:`backend/`,Python + FastAPI + SQLAlchemy + Chroma(向量库)
- 前端:`frontend/`,React + Vite + TypeScript + Ant Design + zustand
- 详见根目录 `README.md`。

## 一、依赖版本:始终采用最新稳定版

- **新增依赖时,默认安装当前最新的稳定(非 beta/rc)版本**,不要凑合使用记忆中的旧版本号,也不要主观臆造版本号。
  - Python:用 `pip index versions <pkg>` 或直接 `pip install <pkg>`(不加版本号)后回写实际解析到的版本。
  - Node:用 `pnpm add <pkg>`(不加版本号)让包管理器解析最新版本,不要手写旧版本字符串。
- **升级已有依赖时,同样升级到最新稳定版**,除非会引入已知的破坏性变更且当前代码无法适配——此时要明确告知用户版本被锁定的原因。
- **依赖必须可复现**:
  - 后端:直接依赖维护在 `backend/requirements.in` / `requirements-dev.in`,使用 `uv pip compile --python-version 3.13 --universal --no-annotate` 生成对应 `.txt` 锁文件,锁定直接和传递依赖的具体版本。新增/升级后重新生成并验证,不要手改生成的锁文件。
  - 前端:提交 `pnpm-lock.yaml`,不要手动编辑锁文件。
- 修改依赖后必须验证:后端跑一次 `python tests/e2e_offline.py`,前端跑一次 `pnpm build`,确认新版本没有破坏现有功能。

## 二、代码与目录结构:生产级规范

### 通用

- 新代码放入符合现有分层的目录,不要在根目录或错误层级新建文件:
  - 后端按领域分层:`api/`(路由)、`agents/`(生成业务逻辑)、`llm/`(模型适配)、`memory/`(向量库/上下文)、`tasks/`(异步任务)。
  - 前端按职责分层:`api/`(请求层)、`components/`(展示/交互组件)、`pages/`(路由页面)、`router/`(路由定义与路径常量)、`constants/`(状态色/文案等常量)、`hooks/`(可复用业务逻辑)、`utils/`(通用工具函数)、`store.ts`(全局状态)。
- 严禁将编译产物、缓存、临时文件提交到 git(如 `*.tsbuildinfo`、`vite.config.js`/`.d.ts`、`__pycache__/`、`.pytest_cache/`、`data/`)。新增此类产物先检查 `.gitignore` 是否已覆盖,没有则补充规则,而不是删完再手动清理。
- 不要新建空的"占位"目录/文件(例如未接入 Alembic 的空 `migrations/versions/`)。要么完整实现,要么不创建。
- TypeScript 保持 `strict: true`,禁止使用 `any`;Python 保持类型标注,遵循 `pyrightconfig.json` 的检查级别。
- 禁止在业务代码中硬编码密钥、Token、内网地址;一律通过 `backend/app/config.py`(`Settings`)或前端环境变量读取,敏感信息只写入 `.env`(已 gitignore),并同步更新 `.env.example` 的占位说明(不要写入真实值)。

### 后端

- **数据库结构变更必须通过 Alembic 迁移**,禁止仅依赖 `Base.metadata.create_all()` 做生产环境建表。新增/修改 `models.py` 中的表结构时,同步生成对应的迁移脚本到 `backend/migrations/versions/`。
- **测试**:新增功能需要有对应测试。优先使用 `pytest`(需先加入 `requirements-dev.txt`),测试文件遵循 `test_*.py` 命名以便被自动发现;现有的 `tests/e2e_offline.py` 端到端脚本继续保留,但不作为唯一测试手段。
- **安全边界**:
  - `CORSMiddleware` 不允许 `allow_origins=["*"]` 与 `allow_credentials=True` 同时出现;需按环境(开发/生产)配置具体的允许域名列表。
  - 涉及外部可访问的生成类接口,评估是否需要鉴权/限流,避免 API Key 被无限调用消耗额度。
- **可观测性**:关键路径(LLM 调用、异步任务、异常)需要有日志输出,不要静默吞掉异常(`except: pass` 禁止),失败要能在日志中定位到具体请求/任务。

### 前端

- 所有对 `api.*` 的调用必须处理失败情况(`.catch` 或 try/catch),并给用户可见的错误提示(如 antd `message.error`),禁止让请求静默失败导致页面卡在 loading 或白屏。
- 保持 `api/`(请求)、`components/`(UI)、`pages/`(路由)三层分离,不要在组件里直接拼接 fetch URL 或硬编码后端路径字符串,统一走 `src/api/`(REST 用 `api.*`,SSE 路径集中放在 `api/index.ts` 的 `sseEndpoints`)。
- 页面跳转统一用 `src/router/paths.ts` 里的 `paths.*`,不要在组件里手写路由字符串;新增路由需要同时更新 `paths.ts` 与 `router/index.tsx` 的 `routePatterns`。
- 多处组件重复出现的状态色/文案映射放入 `src/constants/`,重复的业务逻辑(如列表增删改+生成的 CRUD 流程)优先抽成 `src/hooks/` 里的自定义 hook,而不是复制粘贴。
- 新增页面/组件遵循已有的 antd + TypeScript 风格,Props 需要显式接口定义,不使用隐式 `any`。
- 新增/修改逻辑需要有对应的 vitest 测试,测试文件与源文件同目录并以 `*.test.ts`/`*.test.tsx` 命名(参考现有的 `store.test.ts`、`api/client.test.ts`、`pages/BookList.test.tsx`);提交前跑 `pnpm lint`、`pnpm test`、`pnpm build` 确认通过。
- 修改前端端口、环境变量或构建方式时,同步更新 `Dockerfile`、`nginx.conf.template`、`.env.example`,避免容器构建与本地开发脱节。

## 三、其他工程规范建议

- **CI**:`.github/workflows/quality.yml` 在 push / pull request 时检查前端 lint、格式、测试、构建和隔离 Chromium 回归,以及后端依赖一致性、Ruff、basedpyright、pytest 和离线 E2E。修改后先通过对应本地检查。CI 不配置模型密钥、不运行真实模型评估;相关操作见 `docs/testing.md`。
- **Lint/格式化**:前端已有 ESLint(`eslint.config.js`)+ Prettier(`.prettierrc.json`)配置,新增代码前跑 `pnpm lint` / `pnpm format:check`,不要绕开或再改动这套配置。
- **Git 提交**:每次改动只做与需求直接相关的最小变更,不顺手"优化"未涉及的代码;提交信息说明改动原因而非罗列改了什么文件。
- **文档同步**:目录结构、启动方式、环境变量发生变化时,同步更新根目录 `README.md`,避免文档与实际结构脱节。
- **待优化清单**:`docs/optimization-backlog.md` 只保留未解决问题。修复一项并通过对应测试及验收后,在同一批改动中删除该条目;部分完成则保留并更新剩余范围,不要累积已完成条目。编号保持稳定,完成历史由 Git 保存。

## 四、修改本文件

当项目规范发生变化(如引入了 CI、迁移到新的包管理器等),请更新本文件对应章节,保持它是当前工程规范的唯一真实来源。
