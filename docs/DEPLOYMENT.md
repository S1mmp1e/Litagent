# 部署与运维

## 1. 运行模式

当前是本机单用户应用。默认监听 `127.0.0.1:7860`，同一数据目录只能运行一个进程。不要使用多个 Uvicorn worker，也不要同时从脚本打开正在使用的 Qdrant 本地目录。

没有登录或用户隔离，不应直接映射到公网。跨来源写请求检查不是认证。

## 2. 配置

将 `.env.example` 复制为本机 `.env`。模型密钥可在启动后的“模型设置”填写，也可使用环境变量或 `.env`。

模型配置优先级：**界面保存的配置 > 进程环境变量 > `.env` > 默认值**。界面配置位于 `LIT_DATA_DIR/model-config.json`，原子写入，不覆盖 `.env`，对后续任务即时生效；“恢复环境配置”移除该文件。该文件含明文密钥，应限制本机访问，不纳入公开材料。其他配置仍由进程环境变量和 `.env` 决定。

| 变量 | 默认值 / 含义 |
|---|---|
| `DEEPSEEK_API_KEY` | 模型密钥；也可通过界面配置，服务端读取 |
| `LIT_API_KEY` | 兼容接口密钥后备项 |
| `LIT_BASE_URL` | `https://api.deepseek.com` |
| `LIT_MODEL` | `deepseek-flash`（已有配置保持其原值） |
| `LIT_HOST` | `127.0.0.1` |
| `LIT_PORT` | `7860` |
| `LIT_DB_PATH` | 项目根目录 `lit_agent.db` |
| `LIT_DATA_DIR` | 项目根目录 `data` |
| `LIT_TASK_SECONDS` | 非阅读任务 180 秒 |
| `LIT_READING_SECONDS` | 阅读任务 600 秒 |
| `LIT_READING_MAX_TOKENS` | 最终输出 8192 token |
| `LIT_ALLOW_MODEL_DOWNLOAD` | `0`，运行时不自动下载 E5 |

修改 `.env` 或进程环境变量后重启后端；通过界面保存模型配置不需要重启。模型服务是否接受某个输出预算取决于配置的服务商；不能只增加数值就假定服务端支持。

模型设置接口需要本机 Host（`localhost`、`127.0.0.1` 或 `::1`）与 `X-LitAgent-Settings: 1` 请求头；跨来源写请求会被拒绝。此约束用于本机浏览器访问，不是多用户认证。Docker 使用映射后的 `127.0.0.1:7860` 入口。连接测试仅查询 `/models`、不跟随重定向；HTTPS 用于远程服务，HTTP 仅允许回环地址。

新安装默认模型为 `deepseek-flash`；可用名称以服务商返回的模型列表为准。针对官方 DeepSeek 地址的 `deepseek-flash` / `deepseek-v4-*`，阅读流程显式使用非思考模式，使分段提取与输出预算保持原有语义；其他兼容服务不附加该专有参数。已有 `.env` 不会自动改写。配置参考：[官方模型列表](https://api-docs.deepseek.com/api/list-models/)、[思考模式](https://api-docs.deepseek.com/guides/thinking_mode/)。

## 3. 模型准备

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-rag.txt
.\.venv\Scripts\python.exe -m scripts.setup_embeddings
.\.venv\Scripts\python.exe -m scripts.maintenance doctor --load-model
```

首次会下载依赖及 E5 权重。后续从 Hugging Face 本地缓存读取；`doctor --load-model` 强制离线验证形状，不进行线上模型调用。

PyTorch 等依赖体积较大。如果网络下载不稳定，应先解决包源与模型站点访问；不要在每个阅读请求中重复下载。

使用另一个 OpenAI 兼容服务，可在“模型设置”中填写该服务的地址、模型名称和密钥。也可恢复环境配置后使用 `LIT_BASE_URL / LIT_MODEL / LIT_API_KEY`；环境配置中 `DEEPSEEK_API_KEY` 优先于 `LIT_API_KEY`，切换服务时不要保留不匹配的旧密钥。本项目没有宣称对任意兼容服务均完成验证，模型流式事件、工具格式和输出限制需要单独测试。

## 4. 启停

```powershell
.\.venv\Scripts\python.exe -m scripts.launch
```

也可以执行 `start_webui.ps1` 或双击 `start_webui.bat`。启动器不自动安装包。

关闭前台窗口或按 `Ctrl+C` 停止；先等待任务完成可减少恢复工作。后台启动时记录 PID，只结束对应服务。更新代码后重启，并在浏览器强制刷新。

独立演示实例必须同时隔离端口、数据库和数据目录：

```powershell
$env:LIT_PORT = "7861"
$env:LIT_DB_PATH = "$PWD\demo-local\demo.db"
$env:LIT_DATA_DIR = "$PWD\demo-local\data"
.\.venv\Scripts\python.exe -m scripts.launch --no-browser
```

不要将该目录加入公共仓库。

## 5. Docker Compose

仓库提供 Python 3.12 镜像配置、非 root 用户、健康检查和本机端口绑定。需要先安装并启动 Docker Engine / Docker Desktop。

```bash
cp .env.example .env
# 密钥可在启动后的“模型设置”中填写
docker compose build
docker compose --profile setup run --rm setup-embeddings
docker compose up -d litagent
docker compose logs --tail=100 litagent
```

停止：

```bash
docker compose down
```

数据保存在 `litagent-data` 卷，模型缓存在 `litagent-model-cache` 卷。不要使用 `down -v` 作为普通停止命令。

Docker 的数据卷与现有 Windows 本地目录不同；启动一个新容器不会自动显示本机的旧文献库。跨环境迁移需要显式备份、恢复和重建索引。

构建会安装较大的 PyTorch 依赖；实际镜像构建与运行的验证状态见 [发布验证](VALIDATION.md)。

## 6. 健康检查与诊断

```powershell
.\.venv\Scripts\python.exe -m scripts.maintenance doctor
.\.venv\Scripts\python.exe -m scripts.maintenance doctor --load-model
Invoke-RestMethod http://127.0.0.1:7860/api/health
```

健康接口确认服务和数据库可用，返回模型配置是否存在，不测试 API 密钥有效性、账户余额或实际回答质量。

任务记录包含阶段、错误、已知 token 用量、耗时和结束原因。不要公开整份任务内容，它可能包含原文、个人档案与笔记。

## 7. 备份与恢复

等待所有任务完成并停止服务，然后执行：

```powershell
.\.venv\Scripts\python.exe -m scripts.maintenance backup
```

备份目录包含数据库快照、相关 PDF 和 `manifest.json`。只有 `complete: true` 才表示备份完成。笔记、批注、版本和事件均在数据库中；仅备份 PDF 会丢失这些信息。

当前备份不包含 `.env`、`model-config.json`、模型缓存和 Qdrant 向量目录。恢复到新机器后，需要重新配置自己的 API；不要把含明文密钥的本机配置混入可分享的阅读包或备份材料。

恢复步骤：

1. 停止服务，保留当前数据库及其 WAL/SHM 文件，避免误覆盖。
2. 将备份数据库放入目标 `LIT_DB_PATH`，将 PDF 放入目标 `LIT_DATA_DIR/pdf`。
3. 恢复非密钥服务配置和模型缓存；启动后在“模型设置”重新填写自己的 API，或单独配置服务端环境密钥。
4. 启动单个服务，对需要使用的论文勾选“重新索引”再处理。
5. 检查文献、笔记、批注和引用。个人材料在后续启用它的阅读任务中按需重建索引。

数据库中的 `ready` 标记不代表恢复后的向量文件已经存在；不要跳过索引重建。
