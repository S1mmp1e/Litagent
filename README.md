# LitAgent

**以论文原文为依据，结合你的阅读方式与个人笔记。**

LitAgent 是面向个人研究工作流的论文阅读 Agent：导入 PDF 或 arXiv 论文，进行全文精读和证据追问，检查引用位置，将结果沉淀为可编辑笔记，再让经过你选择的笔记与批注参与下一次阅读。

项目使用 **DeepSeek API、全文混合 RAG、版本化 Skills、个人笔记模板和可恢复任务流程**。当前面向本机单用户使用，不依赖训练过的专用模型。

![LitAgent 阅读工作台](docs/images/reading-workspace.png)

> 截图由独立演示数据库生成，展示真实界面与交互；其中的论文片段和回答为明确标注的演示材料，不代表真实研究结论或模型效果评测。仓库不包含个人文献库、API 密钥和用户笔记。

## 能做什么

| 工作环节 | 当前实现 |
|---|---|
| 收集论文 | arXiv 搜索与导入、本地 PDF 上传、分类夹、标签、BibTeX 导出 |
| 阅读全文 | 逐页解析、分块取证、汇总生成；具体问题走混合检索 |
| 检查依据 | 原文片段与 PDF 页码链接、引用 ID 校验、有范围的自动依据检查 |
| 调整阅读方式 | 8 种阅读 Skill、持久化研究档案、个人模板、20 个动态字段 |
| 沉淀笔记 | 笔记库、正文版本、收藏、回收站、Markdown / PDF 阅读包导出 |
| 融入个人理解 | PDF 高亮与批注、历史修订、明确选择的笔记版本参与 RAG |
| 追踪任务 | SQLite 任务和事件、SSE 阶段与输出、取消、重试、检查点恢复 |

### 从批注到下一次问答

![PDF 批注与原文](docs/images/pdf-annotations.png)

在 PDF 中选中文字并写下自己的疑问或研究条件。阅读时开启“结合已选个人笔记与批注”，系统分别检索论文和个人材料，回答中区分 **[原文]** 与 **[个人笔记]**。

笔记正文编辑后，需要重新选择最新版本；停用、删除或过期的材料不参与新的检索。已有答案保留生成时的来源快照。

### 你的笔记，按你的结构

![个人笔记模板](docs/images/note-template.png)

内置“论文精读”和“复现分析”两版模板，也支持从空白创建自己的章节结构。模板负责输出组织，Skill 负责分析流程，阅读档案负责解释深度与研究条件；三者都不能覆盖原文证据规则。

## 技术结构

```mermaid
flowchart LR
    UI["阅读工作台 / PDF.js"] --> API["FastAPI"]
    API --> DB[("SQLite\n文献 / 笔记 / 任务 / 事件")]
    API --> Worker["单后台执行器"]
    Worker --> Parse["pypdf 逐页解析"]
    Parse --> RAG["E5 + FTS5 + Qdrant\nRRF 混合检索"]
    Worker --> Full["全文逐段取证"]
    RAG --> Evidence["带来源的材料"]
    Full --> Evidence
    Notes["已选笔记 / 批注"] --> Personal["独立个人材料索引"]
    Personal --> Evidence
    Evidence --> Model["DeepSeek API\nSkill + 档案 + 模板"]
    Model --> Check["引用校验 / 自动依据检查"]
    Check --> DB
    DB --> SSE["可恢复 SSE"]
    SSE --> UI
```

| 层 | 技术 |
|---|---|
| 服务与数据 | Python 3.12、FastAPI、Pydantic、SQLAlchemy、SQLite WAL / FTS5 |
| 全文处理 | pypdf、SHA-256 文件标识、解析与索引版本 |
| 检索 | multilingual-e5-small、sentence-transformers、Qdrant 本地持久化、BM25、RRF |
| 模型与执行 | DeepSeek OpenAI 兼容接口、服务端工具参数校验、执行预算、任务检查点 |
| 前端 | 原生 JavaScript / CSS、PDF.js、Lucide、Marked、DOMPurify |
| 验证与运行 | pytest、Playwright、Docker Compose 配置、GitHub Actions |

具体分块、检索配额、版本失效和恢复逻辑见 [技术设计](docs/TECHNICAL_DESIGN.md)。

## 快速开始

推荐 Python 3.12。完整阅读需要一个可用的 DeepSeek API 密钥，以及首次下载本地嵌入模型所需的网络和磁盘空间。

### Windows PowerShell

```powershell
git clone https://github.com/S1mmp1e/Litagent.git
cd Litagent
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-rag.txt
Copy-Item .env.example .env
```

在本机 `.env` 中设置 `DEEPSEEK_API_KEY`，然后执行：

```powershell
.\.venv\Scripts\python.exe -m scripts.setup_embeddings
.\.venv\Scripts\python.exe -m scripts.launch
```

打开 `http://127.0.0.1:7860`。以后也可双击 `start_webui.bat`。启动器会检查依赖、端口及数据库身份，**不会自动安装依赖或下载模型**。

### Linux / macOS

```bash
git clone https://github.com/S1mmp1e/Litagent.git
cd Litagent
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements-rag.txt
cp .env.example .env
# 在 .env 中填写自己的 DEEPSEEK_API_KEY
.venv/bin/python -m scripts.setup_embeddings
.venv/bin/python -m scripts.launch --no-browser
```

Windows 本机流程已有实际验证；跨平台测试由 CI 检查，Docker 配置与平台说明见 [部署指南](docs/DEPLOYMENT.md)。

> 只安装 `requirements.txt` 可以运行文献管理和界面；全文索引与语义检索需要 `requirements-rag.txt` 及本地模型。首次安装会下载 PyTorch 等较大依赖，运行时不会静默下载模型，也不会用模拟向量代替真实向量。

## 第一次完整使用

1. 在“论文阅读”导入文字型 PDF，等待“处理全文”完成。
2. 保存阅读档案，选择“精读”等阅读模式和笔记模板。
3. 开始全文阅读，检查结果中的引用并对照 PDF。
4. 在“我的笔记”修改、保存自己的理解；需要复用时勾选“将此版本用于后续问答”。
5. 在 PDF 中添加批注，切换“证据追问”，开启个人材料后继续提问。

完整操作、双论文对比、备份和故障处理见 [用户手册](docs/USER_GUIDE.md)。

## 开发与验证

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest -q
npm ci
npx playwright install chromium
npm run test:annotations-ui
```

其他浏览器检查：`npm run test:ui`、`npm run test:notebook-ui`、`npm run test:reading-selection`。测试创建独立数据库，不读取你的密钥，不调用线上模型。

本次发布在 Windows 重新运行后端测试，**132 项通过、1 项跳过**，四组浏览器回归通过；另有桌面、手机、PDF 非空检查和跨论文状态隔离。这些结果不能当作真实回答正确率或性能提升。详细记录见 [验证说明](docs/VALIDATION.md)。

真实质量评测工具支持人工证据标注、关键词/向量/混合检索比较，以及同材料、同模型、同预算下的提示词对比；仓库没有预填虚构分数。

## 文档导航

| 文档 | 内容 |
|---|---|
| [用户手册](docs/USER_GUIDE.md) | 安装后的完整使用流程与常见问题 |
| [技术设计](docs/TECHNICAL_DESIGN.md) | 架构、RAG、Skills、个人材料、数据模型与一致性 |
| [开发指南](docs/DEVELOPMENT.md) | 本地开发、目录职责、扩展方式、测试和提交检查 |
| [部署指南](docs/DEPLOYMENT.md) | 配置、单进程限制、Docker、模型准备、备份恢复 |
| [API 参考](docs/API_REFERENCE.md) | 接口分组、请求示例、SSE、冲突与错误语义 |
| [演示与技术展示](docs/SHOWCASE.md) | 关键画面、可复现演示、技术讲解路线 |
| [验证与评测](docs/VALIDATION.md) | 软件检查、真实质量评测与证据边界 |
| [第三方声明](THIRD_PARTY_NOTICES.md) | 随仓库分发的前端依赖及许可证位置 |

## 当前边界

- 面向本机单用户；没有账号、租户隔离和公网访问认证。一个数据目录只启动一个服务进程。
- pypdf 处理文字型 PDF；不支持 OCR，也不承诺复杂公式、表格或图片的可靠理解。
- 阅读选择最多两篇论文；不是任意规模的跨库综述系统。
- 自动引用检查可能出错，不替代人工核对。
- 已有审核样本导出入口，**没有完成 SFT 训练或部署微调模型**。
- 项目尚未声明覆盖全部自有代码的开源许可证；随附第三方代码遵循各自许可证。
