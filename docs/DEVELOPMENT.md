# 开发指南

## 1. 开发环境

推荐 Python 3.12、Node.js 24、Git。正常运行前端不需要 Node.js；仓库已包含带许可证的浏览器依赖。Node.js 用于更新依赖、浏览器测试和演示截图。

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
npm ci
npx playwright install chromium
```

开发真实全文阅读时另外安装 `requirements-rag.txt` 并运行 `python -m scripts.setup_embeddings`。纯后端和浏览器回归测试使用模拟模型，不需要 API 密钥、PyTorch 或下载 E5。

Linux / macOS 将 `.venv\Scripts\python.exe` 替换为 `.venv/bin/python`。浏览器测试自动选择对应平台的 Python；也支持 `LIT_TEST_PYTHON` 和 `LIT_BROWSER_PATH` 覆盖可执行文件路径。

## 2. 启动与目录

```powershell
.\.venv\Scripts\python.exe -m scripts.launch
```

只启动一个使用同一 SQLite 和 Qdrant 目录的服务。接口可以在 `http://127.0.0.1:7860/docs` 查看；核心路由在 `api.py`，文献管理旧路由在 `webui.py`。

```text
lit_agent/
  templates/index.html       页面结构与文献管理交互
  static/                    阅读、笔记、PDF、批注脚本和样式
  static/vendor/             浏览器依赖与许可证
  reading_skills/            8 种阅读指令
  api.py / webui.py          HTTP 入口
  store.py / database.py     事务、迁移及模型
  jobs.py / runtime.py       任务与模型执行
  documents.py               PDF 管线
  retrieval.py               原文混合检索
  reading_workflow.py        阅读编排
  notebook.py                笔记与快照
  note_templates.py          模板与字段
  personal.py                批注与个人材料 RAG
tests/                       隔离测试及离线演示材料
scripts/                     启动、运维、评测、发布检查
docs/                        当前文档、技术展示与历史记录
```

默认生成 `lit_agent.db` 和 `data/`。本地密钥、数据库、PDF、向量、备份、日志、截图测试产物均不进入 Git。对外展示的选定截图存入 `docs/images/`。

## 3. 修改规则

### 数据库

在 `store.migrate()` 入口追加兼容迁移；子模块的 `initialize(db)` 应能重复执行。不要通过删库解决升级问题。

SQLAlchemy 管理文献结构，新增阅读业务使用 `store.connect()` 的显式事务。SQLite 是可见性与版本的权威来源，不能只依赖向量库删除是否成功。

修改数据模型后至少验证：已有文献保留、历史笔记读取、迁移幂等、外键删除语义以及新旧版本冲突。

### 新增 Skill

1. 在 `lit_agent/reading_skills/` 增加 Markdown 指令。
2. 在 `skills.REGISTRY` 注册名称、版本和论文数量约束。
3. 在 `skills.WORKFLOWS` 声明取证重点、有限补查主题及默认要求。
4. 检查 `snapshot()` 能固定指令和版本；增加针对材料选择和范围的测试。

不能只加前端按钮而没有服务端注册。论文文本与用户模板不能赋予工具权限。

### 新增模板字段

字段目录与取值都在 `note_templates.py`。同时更新服务端注册和字段值生成，保留一次展开语义。新字段应有缺失值行为，并测试输入数据中的类似占位符不会被再次展开。

### 修改检索

原文与个人材料有不同的证据角色。不要将个人材料直接写入原文 `chunks` 或共享 `[cite:...]` 标记。

模型、分块或索引策略改变时，更新版本标识并明确重建方式。需要真实质量结论时使用人工审核数据集，不能用测试中的模拟编码器计算“召回率提升”。

### 修改前端

延续原生 JS/CSS 结构。输出经过 DOMPurify 后才能插入页面。异步响应、SSE 和 PDF 加载须校验当前论文/任务身份，避免旧响应覆盖新选择。

固定宽度的图标按钮应有 `aria-label` 或 `title`，窄屏不得依赖横向溢出才能操作。

## 4. 自动化检查

```powershell
.\.venv\Scripts\python.exe -m pytest -q
npm run test:ui
npm run test:notebook-ui
npm run test:reading-selection
npm run test:annotations-ui
```

后端测试在临时目录创建数据库；浏览器测试使用独立数据目录与端口 17867、17869、17870、17871。不要让开发服务使用这些测试端口。测试结束会停止自己启动的服务并校验临时目录后清理。

Windows 默认优先使用已安装的 Edge，其他平台使用 Playwright Chromium。CI 不注入模型密钥，不调用线上模型。

测试产物在 `artifacts/ui/`。查看截图后再判断布局是否正确，不能只看测试退出码。

## 5. 前端依赖

```powershell
npm ci
npm run vendor
```

`scripts/vendor.cjs` 从锁定的 npm 包复制脚本、PDF.js 字体/CMap/WASM 和许可证。更新依赖时同时提交 `package-lock.json`、生成文件与第三方声明，不使用运行时 CDN。

## 6. 真实效果评测

参见 [验证与评测](VALIDATION.md)。`scripts/evaluate_reading.py` 的 `pair` 命令默认只导出材料；显式提供 `--allow-model-calls` 才发送付费 API 请求。

评测要保留模型、日期、材料范围、输出预算和样本量；同材料提示词比较只能说明生成阶段差异，不能证明整个检索流程更好。

## 7. 发布前检查

```powershell
git add README.md docs lit_agent scripts tests .github
.\.venv\Scripts\python.exe -m scripts.check_release
git diff --cached --stat
```

发布检查读取 Git 跟踪路径对应的工作区文件，检查敏感模式、禁止路径、大小和 Markdown 本地链接。暂存后不要跳过工作区差异检查；它不能替代对截图与历史文档的人工查看。

公共仓库不要包含 `.env`、真实数据库、论文 PDF、模型权重、备份、聊天导出或个人开发工具状态。截图使用隔离材料生成，并明确其演示属性。

GitHub Actions 在 push / pull request 执行后端和浏览器检查。项目许可证尚未选定；添加第三方代码时必须保留其许可信息。
