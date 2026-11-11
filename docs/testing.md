# 开发验证

## 环境与依赖

后端验证环境为 Python 3.13,前端为 Node.js 26.13.0 / pnpm 12.18.0。前端 Docker 构建和 CI 使用相同版本。

前端 Node 版本见 `frontend/.node-version`,包管理器版本见 `package.json` 的 `packageManager`。升级时同步 Docker 基础镜像与 CI,并验证编辑器和路由的用户操作。当前使用 [Node 官方稳定 LTS](https://nodejs.org/dist/index.json) 与 [nginx 官方稳定镜像](https://github.com/docker-library/official-images/blob/master/library/nginx)。

pnpm 12 的安装脚本许可集中在 `frontend/pnpm-workspace.yaml` 的 `allowBuilds`,目前仅批准 esbuild 的必要构建脚本。新增依赖时先审查其安装脚本,不要直接允许所有包。锁文件由同一 pnpm 版本生成,不手工编辑。

Markdown 编辑器只加载常见编程语言的高亮规则,未识别的语言仍保留代码块原文。React 运行时、共享图标与编辑器高亮代码分别缓存,编辑器及其高亮依赖只在打开人物/设定面板时加载。

前端安全更新使用 Vite 8.3.7、Vitest 5.1.0、React Router DOM 7.18.5、jsdom 31.0.0 和 ESLint 10.14.0。Vite 分包使用当前 `rolldownOptions.output.codeSplitting`,Router 6 的过渡 flags 已移除。[Vite 迁移说明](https://vite.dev/guide/migration)、[Vitest 迁移说明](https://vitest.dev/guide/migration/) 和 [Router 迁移说明](https://reactrouter.com/7.18.5/upgrading/v6) 是升级依据。

`pnpm-workspace.yaml` 临时将 minimatch 统一到 11.0.7,用于修复 [GHSA-g3hv-75wg-m249](https://github.com/advisories/GHSA-g3hv-75wg-m249);上游 ESLint/TypeScript ESLint 仍约束 10.x。完整 lint/test/build 已验证该 override;上游范围更新后应移除此例外。pnpm 为该明确版本记录了发布时间限制例外,不放宽其他包的供应链检查。2026-11-11 更新后 `pnpm audit` 的低、中、高、严重项均为 0;这只是当时已公开公告的扫描结果。

```bash
cd backend
python3.13 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
python -m pip check
```

`requirements.in` 和 `requirements-dev.in` 记录直接依赖,同名 `.txt` 是包含传递依赖及平台标记的锁文件。运行时安装 `requirements.txt`,开发和 CI 安装 `requirements-dev.txt`。

本轮锁定使用已通过项目测试的现有版本,没有批量升级依赖。OpenTelemetry 的稳定 SDK 指定了对应的 `opentelemetry-semantic-conventions` beta 版本,它作为 SDK 的传递依赖保留,不要单独替换为不匹配版本。

新增或升级依赖时,先按 AGENTS.md 解析最新稳定版本并更新 `.in`,再生成锁文件:

```bash
cd backend
uv pip compile requirements.in --python-version 3.13 --universal --no-annotate -o requirements.txt
uv pip compile requirements-dev.in --python-version 3.13 --universal --no-annotate -o requirements-dev.txt
```

生成器默认尽量复用现有锁定版本。仅在明确升级某个包时使用 `--upgrade-package 包名`,并同时验证两个锁文件的公共版本一致。不要把本地绝对路径、包仓库凭据或 `.env` 内容写入锁文件。

## 本地检查

```bash
cd frontend
pnpm install --frozen-lockfile
pnpm lint
pnpm format:check
pnpm test
pnpm build
```

```bash
cd backend
source .venv/bin/activate
ruff check .
basedpyright
python -m pytest -q
python tests/e2e_offline.py
```

后端单元测试和离线 E2E 使用假模型及临时数据库,不得通过测试连接真实书稿数据库或消耗模型额度。新增网络逻辑使用 HTTP mock / ASGI transport,数据库行为使用临时 SQLite;迁移测试应构造真实历史表结构,不能用最新模型冒充旧库。

## 持续集成

`quality.yml` 并行运行前后端检查,只读仓库权限,相同分支的新运行取消旧运行。后端模型地址设为不可用的本地端口,不注入模型密钥。真实模型表现需要独立评估;离线检查通过只证明程序契约和故障恢复,不能证明长篇叙事质量。

工作流 Action 固定到官方发布的完整提交 SHA,便于复现和审查。更新版本时核对 [checkout](https://github.com/actions/checkout/releases)、[setup-node](https://github.com/actions/setup-node/releases)、[setup-python](https://github.com/actions/setup-python/releases) 和 [pnpm/action-setup](https://github.com/pnpm/action-setup/releases) 的发布说明,然后同步版本注释。

## 模型质量评估

`backend/evaluations/` 提供独立、可选的真实模型抽样评估。默认只生成计划,不读取模型配置、不连接网络、不访问数据库或真实书稿:

```bash
cd backend
python -m evaluations.quality --output /tmp/novel-quality-plan.json
```

确认供应商、模型配置和费用后,由操作者显式加 `--live` 执行。下面最多请求两次,每次要求供应商最多输出 512 token,每次最多等待 90 秒;失败即停止,不重试。报告路径必须不存在,防止覆盖旧结果后才发现错误。

```bash
python -m evaluations.quality --live --max-calls 2 --max-output-tokens 512 \
  --request-timeout 90 --output /tmp/novel-quality-run.json
```

四个固定合成样本分别覆盖早期信息边界、远期承诺与伤势、修订后的因果一致性、后期线程收束。请求通过现有适配器传递输出 token 上限,同时受共享模型调用预算约束。调用次数和输出上限不等于货币上限:输入、供应商推理 token 和计费方式仍会影响费用。报告只保存有长度上限的输出、耗时、错误类型和机械检查信号,不保存 API 地址或密钥。

人工需按报告中的五项标准逐项阅读并填写 1–5 分和具体证据。关键词缺失、重复段落等自动信号只是审查线索,不自动判定作品好坏。对候选模型使用相同样本和参数,再比较报告;不要凭一次采样认定稳定质量。

此工具测试的是提供固定历史上下文后的模型响应,并未运行完整生成流水线或真实数百章长篇。生产长篇验收还需由操作者选定独立样书,分阶段人工检查章纲覆盖、已知信息、人物状态和伏笔收束,保留修订前后的样本。CI 只运行评估工具的假模型测试,不会启用 `--live`。
