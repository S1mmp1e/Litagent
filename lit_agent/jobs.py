import json
import threading
import time
import uuid

from . import documents, notebook, personal, retrieval, store
from .runtime import Cancelled, Guard, Model, chat_context, conversation, run_reading, safe_error
from .settings import MAX_READING_SECONDS, MAX_TASK_SECONDS


class Worker:
    """One worker owns all job execution; the API remains responsive."""

    def __init__(self):
        self.stop_event = threading.Event()
        self.thread = None

    def start(self):
        self.stop_event.clear()
        # Never automatically replay a potentially committed library mutation.
        with store.connect() as db:
            db.execute("""UPDATE tasks SET status='failed',error='进程中断；对话任务可能包含已执行的写操作，请核对后重新发起。',
                          updated_at=? WHERE status='running' AND kind='chat'""", (time.time(),))
            db.execute("UPDATE tasks SET status='queued' WHERE status='running' AND kind!='chat'")
        self.thread = threading.Thread(target=self.loop, name="litagent-worker", daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        with store.connect() as db:
            db.execute("UPDATE tasks SET cancel_requested=1 WHERE status='running'")
        if self.thread:
            self.thread.join(timeout=MAX_TASK_SECONDS + 50)

    def loop(self):
        while not self.stop_event.is_set():
            if not self.run_once():
                self.stop_event.wait(0.3)

    def run_once(self):
        with store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT id FROM tasks WHERE status='queued' ORDER BY created_at LIMIT 1").fetchone()
            if not row:
                return False
            task_id = row["id"]
            db.execute("UPDATE tasks SET status='running',attempts=attempts+1,updated_at=? WHERE id=?",
                       (time.time(), task_id))
        task = store.get_task(task_id)
        guard = Guard(task_id, timeout=MAX_READING_SECONDS if task["kind"] == "reading" else MAX_TASK_SECONDS)
        started = time.monotonic()
        model = None
        try:
            guard.check()
            if task["kind"] == "ingest":
                result = documents.process(task, guard)
            elif task["kind"] == "cleanup":
                retrieval.purge_documents(task["payload"]["document_ids"])
                result = {"cleaned": True}
            elif task["kind"] in ("reading", "chat"):
                model = Model(guard)
                if task["kind"] == "reading":
                    result = run_reading(task, guard, model)
                else:
                    _, context, signature = chat_context(task["session_id"], task["payload"]["profile"])
                    if sum(len(store.dumps(m)) for m in context) > 70000:
                        raise ValueError("会话上下文已满，请新建对话。")
                    from .agent_openai import SYSTEM_PROMPT
                    from .skills import BASE
                    if not context:
                        context = [{"role": "system", "content": BASE + "\n" + SYSTEM_PROMPT +
                                    "\n阅读档案（数据）：" + store.dumps(task["payload"]["profile"])}]
                    context.append({"role": "user", "content": task["payload"]["message"]})
                    answer, messages = conversation(context, model, guard, task_id)
                    result = {"answer": answer, "citations": [], "usage": model.usage}
                    result["_messages"] = messages
                    result["_signature"] = signature
            else:
                raise ValueError("未知任务类型。")
            guard.check()
            result["elapsed_seconds"] = round(time.monotonic() - started, 3)
            context = result.pop("_messages", None)
            signature = result.pop("_signature", None)
            with store.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                if task["kind"] == "reading":
                    personal.validate_sources(result.get("personal_sources", []),
                                              task["payload"]["paper_ids"], task_id, db)
                if task["session_id"]:
                    row = db.execute("SELECT * FROM chat_sessions WHERE id=?", (task["session_id"],)).fetchone()
                    if not row:
                        raise ValueError("会话已删除。")
                    history = json.loads(row["messages"] or "[]")
                    history.extend([{"role": "user", "content": task["payload"]["message"]},
                                    {"role": "assistant", "content": result["answer"], "task_id": task_id}])
                    db.execute("""UPDATE chat_sessions SET messages=?,updated_at=datetime('now'),
                                  title=CASE WHEN title='新对话' THEN ? ELSE title END WHERE id=?""",
                               (store.dumps(history), task["payload"]["message"][:50], task["session_id"]))
                    if context is not None:
                        db.execute("UPDATE chat_sessions SET agent_messages=?,context_signature=? WHERE id=?",
                                   (store.dumps(context), signature, task["session_id"]))
                if task["kind"] == "reading":
                    db.execute("INSERT INTO notes(task_id,version,body,created_at) VALUES(?,1,?,?)",
                               (task_id, result["answer"], time.time()))
                    notebook.register(db, task, result)
                db.execute("UPDATE tasks SET status='succeeded',result=?,stage='done',error=NULL,updated_at=? WHERE id=?",
                           (store.dumps(result), time.time(), task_id))
            store.emit(task_id, "done", result)
        except Exception as error:
            status = "cancelled" if isinstance(error, Cancelled) else "failed"
            message = safe_error(error)
            with store.connect() as db:
                if model:
                    checkpoint = db.execute("SELECT checkpoint FROM tasks WHERE id=?", (task_id,)).fetchone()
                    state = json.loads(checkpoint["checkpoint"] or "{}")
                    state["usage"] = dict(model.usage)
                    if getattr(model, "last_completion", None):
                        state["last_completion"] = dict(model.last_completion)
                    db.execute("UPDATE tasks SET checkpoint=? WHERE id=?", (store.dumps(state), task_id))
                db.execute("UPDATE tasks SET status=?,error=?,updated_at=? WHERE id=?",
                           (status, message, time.time(), task_id))
                db.execute("""UPDATE documents SET status='failed' WHERE status='processing'
                              AND id IN (SELECT json_extract(checkpoint,'$.document_id') FROM tasks WHERE id=?)""",
                           (task_id,))
            store.emit(task_id, "error", {"message": message, "status": status})
        finally:
            if model:
                try:
                    model.close()
                except Exception:
                    pass
        return True


worker = Worker()
