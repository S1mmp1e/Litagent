# API 参考

默认地址为 `http://127.0.0.1:7860`。运行服务后访问 `/docs` 查看由 Pydantic 生成的字段定义，`/openapi.json` 是接口结构的权威来源。

本服务没有用户认证，仅用于可信本机。示例中的 ID 必须替换为本机实际返回的值；请求不携带模型密钥。

## 1. 接口地图

| 分组 | 主要接口 |
|---|---|
| 健康 | `GET /api/health` |
| 文献库 | `GET /api/library/rows`，`GET /api/library/detail/{paper_id}`，`POST /api/library/add`，`DELETE /api/library/{paper_id}` |
| 搜索与分类 | `GET /api/search`，`GET/POST /api/categories`，`PUT/DELETE /api/categories/{cat_id}` |
| PDF | `POST /api/papers/upload`，`GET/POST /api/papers/{paper_id}/preview`，`GET /api/documents/{document_id}/pdf` |
| 全文处理 | `POST /api/papers/{paper_id}/process`，`GET /api/papers/{paper_id}/documents` |
| 阅读 | `POST /api/chat/runs`，`GET /api/skills`，`GET/PUT /api/profile` |
| 任务 | `GET /api/tasks`，`GET /api/tasks/{task_id}`，`POST /api/tasks/{task_id}/cancel`，`POST /api/tasks/{task_id}/retry` |
| 事件 | `GET /api/tasks/{task_id}/events` |
| 原文检索 | `GET /api/retrieval`，`GET /api/chunks/{chunk_id}` |
| 笔记 | `GET /api/notes`，`GET/PATCH /api/notes/{task_id}`，`PUT /api/tasks/{task_id}/notes`，`GET /api/notes/{task_id}/export` |
| 模板 | `GET/POST /api/note-templates`，`GET/PUT/DELETE /api/note-templates/{template_id}`，`GET /api/note-template-fields` |
| 批注 | `GET/POST /api/annotations`，`GET/PUT/DELETE /api/annotations/{annotation_id}` |
| 个人材料 | `PUT /api/personal/notes/{task_id}`，`GET /api/personal/evidence/{task_id}/{chunk_id}` |
| 评测与审核 | `GET /api/eval/manifest`，`GET /api/tasks/{task_id}/review-input`，`GET /api/exports/sft` |

## 2. 创建阅读任务

```http
POST /api/chat/runs
Content-Type: application/json
```

```json
{
  "message": "解释核心方法、实验条件和适用边界。",
  "paper_ids": [1],
  "skill_id": "deep_read",
  "action": "summary",
  "verify_evidence": true,
  "use_cache": true,
  "use_personal": false,
  "template_id": "paper-reading",
  "template_version": 1
}
```

成功返回 `202` 与任务记录，使用 `id` 查询进度。`action` 可为 `summary`、`question` 或文献管理 `chat`；`compare` Skill 必须提供两篇不同论文。阅读论文需要 ready 索引。

连续追问传入同范围会话的 `session_id`；已有任务未结束时会返回冲突，不能并发向同一会话提交。

## 3. SSE

```javascript
const stream = new EventSource(`/api/tasks/${taskId}/events`);
stream.addEventListener('stage', event => {
  console.log(JSON.parse(event.data).stage);
});
stream.addEventListener('delta', event => {
  // 输出仍需经过安全 Markdown 渲染，不能直接当作 HTML。
  console.log(JSON.parse(event.data).text);
});
stream.addEventListener('done', event => {
  const result = JSON.parse(event.data);
  stream.close();
  console.log(result.citations);
});
```

事件保存在数据库，并带递增 `id`。浏览器重连使用 `Last-Event-ID`；也可用 `after` 查询参数恢复。需同时处理 `error`、`terminal` 和网络断线，并通过任务接口确认最终状态。

`model_response` 事件包含结束原因、请求输出预算、实际已知 usage 和耗时，不包含密钥或完整请求正文。

## 4. 检索与证据

```http
GET /api/retrieval?query=training%20information&paper_id=1&mode=hybrid
```

`mode` 可为 `keyword`、`dense`、`hybrid`。返回原文片段及页码，读取 `/api/chunks/{chunk_id}` 可得到 PDF 链接。

该接口只检索原文。个人材料检索由阅读任务在 `use_personal=true` 时调用，不能将返回的个人材料当作论文事实。

## 5. 笔记修订与选择

```http
PUT /api/tasks/{task_id}/notes
Content-Type: application/json
```

```json
{
  "body": "修订后的 Markdown 正文，保留本任务允许的引用。",
  "feedback": "说明修订内容。",
  "approved": false,
  "expected_version": 1
}
```

`expected_version` 用于防止覆盖其他修订。正文使用未提供的原文或个人材料引用时返回 `422`。

选择笔记参与后续问答：

```http
PUT /api/personal/notes/{task_id}
Content-Type: application/json
```

```json
{"version": 2, "enabled": true}
```

只能选择最新版本，且笔记不能位于回收站。此操作与 `approved` 训练样本审核没有关联。

## 6. PDF 批注

通过 PDF 预览接口取得 `paper_id、file_hash、document_id`。创建批注请求需要：

```json
{
  "paper_id": 1,
  "file_hash": "替换为实际的64位SHA-256",
  "page": 3,
  "quote": "从当前页面选中的原文",
  "prefix": "摘录前的上下文",
  "suffix": "摘录后的上下文",
  "rects": [[60, 500, 350, 520]],
  "comment": "我想核对这里是否需要全局信息。",
  "color": "green",
  "enabled": true
}
```

`rects` 使用 PDF 页面坐标，不是 CSS 像素；应通过 PDF.js viewport 转换，不能直接复制上面的示例矩形用于任意论文。每页从 1 开始。

修改批注需要 `expected_revision、comment、color、enabled`。删除使用：

```http
DELETE /api/annotations/{annotation_id}?expected_revision=2
```

引用快照接口返回 `current`，表示来源是否仍然与生成时版本一致；历史引用不因后续编辑而被静默替换。

## 7. 错误与预算

| 状态 | 常见含义 |
|---|---|
| `404` | 文献、笔记、引用或任务不存在 |
| `409` | 未完成索引、版本冲突、会话繁忙、任务不允许重试 |
| `422` | 参数、数量、坐标或引用标记不合法 |
| 任务 `failed` | 已接受任务，但模型、解析、预算或材料一致性校验失败 |

HTTP 接受任务不代表模型执行成功。客户端应以任务最终状态为准，不把流中的半成品当作已保存笔记。
