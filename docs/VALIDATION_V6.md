# 批注与个人材料功能验收

日期：2026-11-16。

## 自动化检查

- 完整后端测试：`129 passed, 1 skipped`。保留原有 Starlette/AnyIO 弃用提示，未修改第三方依赖。
- `tests/annotations-browser.cjs`：批注保存、修订历史、定位、缩放、刷新恢复、不同论文隔离、延迟响应隔离、删除、笔记版本选择、个人材料引用和手机布局均通过，无浏览器脚本错误。
- `tests/notebook-browser.cjs`：原有笔记库、个人模板、引用和导出检查通过。
- `tests/reading-selection.cjs`：论文切换、历史笔记恢复、对比范围、过期事件及新会话检查通过。
- `tests/browser.cjs`：原有阅读界面、Markdown、安全渲染、引用及桌面/手机布局检查通过。
- 上述测试使用隔离数据库、模拟嵌入和离线模型，不调用真实 DeepSeek 服务。

## 本机升级核对

- 确认没有排队或执行中的任务后停止旧服务，生成完整数据库及 PDF 备份：
  `backups/20261116_224657_ba78f86f/`。
- 启动更新后的单进程服务，保留原端口 7860、数据库和数据目录。
- 数据库迁移版本为 `[1, 2, 3, 4]`，`PRAGMA integrity_check` 返回 `ok`。
- 将备份数据库与升级后数据库的下列表逐行比对，内容均未变化：

| 表 | 升级前 | 升级后 |
|---|---:|---:|
| papers | 35 | 35 |
| categories | 5 | 5 |
| chat_sessions | 13 | 13 |
| notes | 5 | 5 |
| notebooks | 5 | 5 |
| note_sources | 5 | 5 |
| tasks | 19 | 19 |

- `/api/annotations`、`/api/personal/notes/{task_id}`、`/api/personal/evidence/{task_id}/{chunk_id}` 已在本机服务注册。
- 只读浏览器检查实际文献 1、2，15 页和 12 页 PDF 均正常渲染；桌面画布非空，手机页面无横向溢出，无浏览器脚本错误。此检查未创建批注、笔记或模型任务。

## 产物与边界

- `artifacts/ui/annotations-desktop.png`
- `artifacts/ui/annotations-mobile.png`
- `artifacts/ui/personal-answer-desktop.png`
- `artifacts/ui/pdf-live-1.png`
- `artifacts/ui/pdf-live-2.png`
- `artifacts/ui/pdf-live-mobile.png`

这轮检查验证软件行为、迁移完整性和引用来源区分，未测量真实语义召回率或回答质量提升。
原始 PDF 不写入批注；批注、颜色和版本保存在应用数据库中。
