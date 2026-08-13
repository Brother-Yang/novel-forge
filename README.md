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
novel/
├─ backend/            FastAPI 后端
│  └─ app/
│     ├─ api/          REST + SSE 路由
│     ├─ agents/       分层生成 Agent(世界观/人物/大纲/正文/摘要)
│     ├─ memory/       Chroma 向量库 + 上下文构造(token 预算)
│     ├─ tasks/        全书批量生成 + 断点续写
│     ├─ models.py     数据模型
│     └─ ...
└─ frontend/           React 前端
   └─ src/
      ├─ pages/        书籍列表、书籍工作区
      ├─ components/   目录树、章节编辑器、人物/设定面板、任务面板
      └─ api/          REST 封装 + SSE 消费
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

### 1. 启动后端

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # 按需修改模型/预算
uvicorn app.main:app --port 8000 --reload
```

后端默认监听 `http://localhost:8000`,接口文档见 `http://localhost:8000/docs`。

### 2. 启动前端

```bash
cd frontend
pnpm install
pnpm dev
```

前端默认 `http://localhost:5173`,已配置 `/api` 代理到后端 `8000` 端口。

## 生成流程(分层)

1. **世界观**:根据题材/基调生成设定条目,写入向量库。
2. **人物**:每个人物生成一份独立 Markdown 人物卡。
3. **全书大纲**:生成分卷大纲 + 剧情线程(伏笔)。
4. **章节大纲**:将某一卷拆解为逐章细纲。
5. **章节正文**:按「本章大纲 + 相关人物卡 + 向量召回设定/历史 + 前情摘要」
   在 token 预算内构造上下文后流式生成。
6. **一致性回写**:正文生成后自动提取章摘要并写入记忆库,供后续检索。

在前端「书籍工作区 → 生成」面板中可逐层触发,或一键「全书批量生成正文」
(后台任务,支持进度/暂停/继续/重试与断点续写)。

## 离线回归测试(不调用真实模型)

用假 LLM 端到端验证「世界观 -> 人物 -> 大纲 -> 章纲 -> 全书批量正文 -> 断点续写
-> 导出 -> 拖拽排序」全链路逻辑,不消耗算力,全部通过退出码为 0:

```bash
cd backend
source .venv/bin/activate
python tests/e2e_offline.py
```

## 为大规模长篇所做的设计

- 章节正文与元数据分离(`chapters` / `chapter_content`),目录接口分页且不含正文。
- 前情使用「章摘要」而非原文,超预算时降级为「卷摘要」,控制上下文规模。
- 向量检索只召回与当前章相关的设定与历史片段。
- 剧情线程(`plot_threads`)状态机跟踪伏笔的开启/进行/关闭。
- 全书生成为异步任务,每章独立落库,崩溃后可从 checkpoint 续写。

## 回归测试(离线,不调用真实模型)

使用假 LLM 端到端验证「世界观→人物→大纲→章纲→全书批量正文→断点续写→导出→拖拽排序」
的全部逻辑,不消耗算力、数据写入临时目录并自动清理:

```bash
cd backend
source .venv/bin/activate
python tests/e2e_offline.py    # 全部通过退出码 0,失败为 1
```
