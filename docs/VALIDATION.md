# 验证与评测

本页区分软件行为测试、真实服务检查、模型与检索质量评测。前两类检查通过不意味着回答质量优于通用 DeepSeek 提示词。

## 本次发布验证

日期：2026-11-17，Windows、Python 3.12。

| 检查 | 当前结果 | 解释 |
|---|---|---|
| 完整后端回归 | 132 passed, 1 skipped | 51.31 秒；包含 3 项新发布检查；跳过合成性能测试 |
| 发布清单检查 | 通过 | 323 个候选文件；常见密钥模式、私人路径、文档目标和随附许可证 |
| 四组浏览器检查 | 全部通过 | 阅读界面、笔记模板、论文切换、批注；无页面脚本错误 |
| 公共演示截图 | 八张已生成并检查 | PDF 可见深色像素 11,385；手机宽度 390，无横向溢出 |
| Docker Compose 配置 | 通过 | `docker compose config --quiet` 返回 0 |
| Docker 构建与运行 | 未执行 | Docker Desktop Linux 引擎未运行 |
| Linux GitHub Actions | 每次提交生成 Checks 记录 | 固定 Ubuntu 24.04、Python 3.12、Node 24；以对应提交运行结果为准 |
| 真实模型质量 / 召回率 | 未提供实测分数 | 需要独立审核集与匹配预算的实验 |

前一轮迁移、只读真实 PDF 验证和详细范围保存在 [v6 验收记录](VALIDATION_V6.md)。历史记录不自动代表后续版本的状态。

首次上传的 Linux 后端检查通过，浏览器检查在安装阶段遇到 `ubuntu-latest` 切换至 Ubuntu 26.04 后与 Playwright 1.56.1 不兼容的问题。CI 已固定为 Ubuntu 24.04；未执行的浏览器步骤不计为通过。

## 可运行的软件检查

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m scripts.check_release
npm ci
npx playwright install chromium
npm run test:ui
npm run test:notebook-ui
npm run test:reading-selection
npm run test:annotations-ui
npm run showcase
```

首次提交前可用 `python -m scripts.check_release --include-untracked` 检查尚未加入 Git、且未被忽略的文件；CI 只检查 Git 跟踪文件。检查不会读取忽略的 `.env`、用户 PDF、数据库或备份。

后端测试使用临时数据库、模拟嵌入与模型替身，PDF 提取使用合成文字 PDF。浏览器脚本创建自己的目录与服务并在退出时清理。这些测试不能证明真实 E5 语义召回质量。

| 测试入口 | 主要覆盖 |
|---|---|
| `test_documents.py` | PDF 去重、解析、索引状态、版本与删除 |
| `test_reading_workflow.py` | 全文覆盖、追问、预算、恢复、输出完整性 |
| `test_notebook.py` | 笔记版本、来源快照、导出与模板 |
| `test_personal.py` | 批注、版本选择、有效性、撤回和个人证据 |
| `test_runtime.py` / `test_api.py` | 参数、超时、上下文、接口与事件 |
| `test_maintenance_eval.py` | 备份、身份、标注与配对条件 |
| `test_release.py` | 发布路径、敏感值不回显、链接 |
| `annotations-browser.cjs` | 缩放、刷新、跨论文隔离、引用与手机布局 |

## 真实使用验收

使用自己的文字型论文完成连续路径：

1. 导入并处理全文，检查页数和内容是否合理。
2. 用“精读”生成笔记，打开至少三条引用，对照方法和实验所在页。
3. 在 PDF 上添加研究条件批注，开启个人材料后针对性提问。
4. 点击个人引用，确认自己的评论版本，同时保留原文来源。
5. 编辑笔记保存新版；确认旧选择失效，再明确选择新版参与问答。
6. 撤回个人材料并重新提问，确认新结果不继续使用已撤回来源。
7. 刷新、切换论文与笔记，确认对应关系保持一致。

记录论文、任务 ID、问题、引用页码、预期与实际差异。先把失败归类为提取、检索、生成、引用或界面问题，再选择优化方向。

## 检索评测

入口为 [evaluate_reading.py](../scripts/evaluate_reading.py) 的 `init` 与 `retrieval` 命令。先查看 `--help`，生成草稿并人工确认问题、是否可从材料回答及证据页。未审核条目不用于正式计分。

- 从未用于调提示词的论文建立问题集，标注论文身份、证据文本、页码与可回答性。
- 在相同文档与分块下比较关键词、向量、混合检索。
- 对可回答问题统计证据命中与排名；无法回答的问题单独评估。
- 报告样本量、模型、索引版本、检索参数与实测延迟，不从演示数据估计收益。

[benchmark.py](../scripts/benchmark.py) 单独运行确定性合成性能测试，不创建真实人工评测集，其结果不能当作真实论文检索质量。

## 阅读质量评测

入口为 [evaluate_reading.py](../scripts/evaluate_reading.py)。配对实验使用同一材料、模型和预算比较通用提示词与个性化 Skill，显式授权在线调用后才执行。

| 维度 | 人工核对内容 |
|---|---|
| 事实与依据 | 结论、数字与条件是否符合原文 |
| 方法解释 | 输入输出、机制、假设与实现是否讲清 |
| 实验覆盖 | 主要实验、消融与比较条件 |
| 局限 | 区分作者陈述、读者推断与信息缺失 |
| 个性化 | 结合背景与研究条件，同时保持论文事实 |
| 可读性 | 组织、术语解释与篇幅 |

个人材料应增加冲突案例：笔记与原文不一致时，分别指出来源，不能覆盖原文。多次生成有随机性，应记录配置与重复次数。

人工证据标注属于评测数据建设；整理合格输入输出样本并实际优化模型参数才属于 SFT。本项目目前没有完成这一训练步骤。
