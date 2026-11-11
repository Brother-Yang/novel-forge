# AI 长篇小说生成平台

面向十万级 ~ 百万级字数的长篇小说创作平台。核心不是单次对话生成,而是通过
**分层大纲 + 结构化记忆 + RAG 检索 + 多级摘要压缩**,在超长篇幅下保持人物、
设定与剧情的一致性。优先使用 **Ollama 本地模型**,LLM 适配层可切换到 OpenAI 兼容云端 API。

## 技术栈

- 前端:React + Vite + pnpm + TypeScript + Ant Design + `@uiw/react-md-editor`
- 后端:Python + FastAPI + SQLAlchemy
- 存储:SQLite(结构化) + Chroma(向量检索)
- 模型:通过 LLM 适配层支持三种提供方,`backend/.env` 中 `LLM_PROVIDER` 切换
  - `ollama`(本地,默认):生成 `qwen2.5:14b`,嵌入 `qwen3-embedding`
  - `bailian`(阿里云百炼 / DashScope):生成 `qwen-plus`,嵌入 `text-embedding-v3`
  - `openai`(任意 OpenAI 兼容 API)

## 目录结构

```
novel-forge/
├─ backend/            FastAPI 后端
│  ├─ migrations/     Alembic 完整建库基线与版本迁移
│  ├─ tests/          单元、HTTP、迁移与离线 E2E
│  ├─ evaluations/    显式启用、限制调用量的模型质量抽样
│  └─ app/
│     ├─ api/          REST + SSE 路由
│     ├─ agents/       分层生成 Agent(世界观/人物/大纲/正文/摘要)
│     ├─ memory/       记忆同步队列、正文事实回放、Chroma 与上下文预算
│     ├─ llm/          模型适配、超时、重试与调用预算
│     ├─ tasks/        全书生成、任务互斥与断点续写
│     ├─ models.py     数据模型
│     └─ ...
└─ frontend/           React 前端
   └─ src/
      ├─ pages/        书籍列表、书籍工作区
      ├─ components/   目录树、编辑器、人物/设定/剧情面板、任务面板
      ├─ api/          REST 封装 + SSE 消费(含 sseEndpoints)
      ├─ router/       路由定义与路径常量
      ├─ constants/    状态色/文案等常量
      ├─ hooks/        可复用业务逻辑(如人物卡/设定卡的增删改)
      ├─ utils/        通用工具函数
      └─ store.ts      全局状态(zustand)
```

## 快速开始

### 0. 准备模型(二选一)

**方案 A:本地 Ollama(默认)**

```bash
ollama pull qwen2.5:14b
ollama pull qwen3-embedding
```

**方案 B:阿里云百炼(DashScope)**

在 `backend/.env` 中设置:

```
LLM_PROVIDER=bailian
DASHSCOPE_API_KEY=sk-你的百炼APIKey
# 可选:BAILIAN_GENERATION_MODEL=qwen-max、BAILIAN_EMBEDDING_MODEL=text-embedding-v3
```

模型名与提供方均可在 `backend/.env` 中修改(见 `backend/.env.example`)。

已知限制与后续工作见 [待优化问题清单](docs/optimization-backlog.md)。清单只保留未解决项，修复并验证后删除对应条目。

### 1. 启动后端

```bash
cd backend
python3.13 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # 按需修改模型/预算
uvicorn app.main:app --port 8000 --reload
```

后端默认监听 `http://localhost:8000`,接口文档见 `http://localhost:8000/docs`。

### 2. 启动前端

```bash
cd frontend
pnpm install --frozen-lockfile
pnpm dev
```

前端要求 Node.js 26.13.0 与 pnpm 12.18.0,版本分别记录在 `.node-version` 和 `package.json`。前端默认 `http://localhost:5173`,已配置 `/api` 代理到后端 `8000` 端口。

质量检查:

```bash
pnpm lint          # ESLint
pnpm format:check  # Prettier 检查
pnpm test          # vitest 单测
```

### 3. Docker 部署(前端)

`frontend/Dockerfile` 提供多阶段构建:build 阶段产出静态文件,运行阶段用 nginx 提供静态资源并反代 `/api` 到后端。

```bash
cd frontend
docker build -t novel-frontend --build-arg VITE_API_BASE_URL=/api .
docker run -p 8080:80 -e BACKEND_ORIGIN=http://<后端地址>:8000 novel-frontend
```

### 4. 单进程和对外部署

SQLite 与本地 Chroma 由一个后端进程管理。生产请用 `uvicorn app.main:app --workers 1 --lifespan on`，同一数据目录只启动一个实例。启动时后端会锁住 SQLite 文件和 Chroma 目录对应的锁文件；共享任一存储的第二个进程会拒绝启动。锁文件由系统在进程退出时释放，文件本身应保留。

默认 `APP_ENV=local` 适合在本机使用。公网部署前，将前端和 `/api` 放在同一个 HTTPS 站点，由反向代理提供 TLS，并在后端 `.env` 中配置：

- `APP_ENV=production`；`ACCESS_PASSWORD` 使用至少 12 个字符的随机密码；`SESSION_SECRET` 使用独立的至少 32 字节随机值，可用 `python -c 'import secrets; print(secrets.token_urlsafe(48))'` 生成。
- `CORS_ALLOWED_ORIGINS` 填写实际 HTTPS 来源，例如 `https://novel.example.com`。代理将客户端地址转发给后端；只有在 Uvicorn 的 `--forwarded-allow-ips` 中配置了明确的可信代理地址时才信任该地址头。

工作台打开后输入访问密码；书稿读写、导出和生成均使用浏览器里的签名会话。会话默认 12 小时，可在 `SESSION_TTL_SECONDS` 配置；更换访问密码或签名密钥会使旧会话失效。系统只有一个共享工作台账号，登录者能访问全部书稿。密码和签名密钥只配置在后端，前端构建无需任何密钥。

默认每个来源 5 分钟可尝试登录 5 次；全站每小时可提交 30 次生成、任务继续/重试或记忆修复请求，最多同时生成 2 本书；每小时最多发出 600 次实际模型请求，覆盖正文、摘要、嵌入及重试。可按 `.env.example` 中的 `LOGIN_*`、`GENERATION_*`、`LLM_*` 调整；限额窗口在进程重启后重置，不等同于模型供应商的金额上限。任务因模型限额暂停在报错状态后，可在窗口恢复后重试。

模型 HTTP 请求的默认超时为连接 10 秒、读取 180 秒、写入 30 秒、连接池等待 10 秒，均可在 `.env` 中调整。流式读取超时以等待下一批响应数据的时间计；超时后当前请求关闭，任务面板可重试。

## 生成流程(分层)

1. **世界观**:根据题材/基调生成设定条目,写入向量库。
2. **人物**:每个人物生成一份独立 Markdown 人物卡。
3. **全书大纲**:生成分卷大纲 + 剧情线程(伏笔)。
4. **章节大纲**:将某一卷拆解为逐章细纲。
5. **章节正文**:按「本章大纲 + 相关人物卡 + 向量召回设定/历史 + 前情摘要」
   在 token 预算内构造上下文后流式生成。
6. **一致性回写**:正文与记忆同步任务在同一事务保存,后台提取章摘要和有正文证据的人物/剧情事实,供后续检索与状态回放。

首页「一句话出书」提交一段想法后,后台编排器会自动跑完上述各层
(任务 `type=novel`,支持暂停/继续/重试)。每个阶段先校验模型输出的字段、结构和数量,再把内容与阶段检查点原子保存。恢复时核对实际完整性,保留已有内容并补齐明确的缺项;旧数据结构有歧义时显示错误,由用户整理后重试。进程重启恢复未完成任务,同一本书的手工生成与后台任务共用互斥控制。工作区「生成」面板仍可逐层触发,或只补未完成正文。

Agent 耗尽决策步数或工具调用预算时会标记为错误，可从任务面板重试；只有存在章节且所有章节均有非空的已完成正文，才可确认成书。空正文生成会报错并保留已有正文，避免误报完成或覆盖原稿。

工作区「创作记忆」显示待处理、失败及缺失的摘要/事实/索引,可单独修复,不会重写正文。修改正文、设定或删除来源后,旧版本记忆立即不再用于检索;重写早期章节只读取此前已保存且版本一致的事实。卷的原始大纲和从正文生成的事实摘要分开保存。

人物面板显示正文推导的当前位置、处境、目标与关系,剧情面板显示线程推进/收束状态。事实提取只接受同书已有对象,每条更新需要本章原文证据。人物卡与剧情计划保留作者输入,派生状态按当前章序回放;失败的事实提取可在记忆面板重试。

目录中删除卷仅解除章节归卷，章节、正文和摘要保留在未归卷区域；删除章节或整本书才会删除对应正文。删除操作前会说明影响范围。

完整依赖锁、CI、测试与可选真实模型评估说明见 [开发验证](docs/testing.md)。应用启动仅通过 Alembic 建库或升级,旧库升级前应先备份;运行时不再用 `create_all()` 掩盖缺失迁移。

## 离线回归测试(不调用真实模型)

用假 LLM 端到端验证「世界观 -> 人物 -> 大纲 -> 章纲 -> 全书批量正文 -> 断点续写
-> 导出 -> 拖拽排序 -> 一句话出书编排 -> 中途重试 -> 保留已有完整数据 -> 进程重启恢复」
全链路逻辑,不调用真实模型,全部通过退出码为 0:

```bash
cd backend
source .venv/bin/activate
python tests/e2e_offline.py
```

## 为大规模长篇所做的设计

- 章节正文与元数据分离(`chapters` / `chapter_content`),目录树只返回元数据,不含正文;任务执行轨迹支持游标分页和增量拉取。
- 前情优先使用「章摘要」,并对允许引用的历史卷事实摘要和全部上下文统一控制预算。
- 向量检索只召回与当前章相关且通过 SQL 归属、版本和时间位置校验的设定与历史片段。
- 剧情线程从有证据的章节事实中回放推进/收束状态,修订不会把过期的终局状态带回早期章节。
- 全书生成为异步任务,每章独立落库,崩溃后可从 checkpoint 续写。

规划上限为 40 卷 × 每卷 20 章 × 每章 4000 字,约 320 万字。100 万、200 万字目标会扩展卷数与章数,超出可规划范围时明确拒绝,不静默缩成短篇。这是计划容量,不保证实际成文字数或文学质量。
