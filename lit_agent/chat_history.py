"""
Chat history persistence — save/load conversation sessions.
"""

import json
from datetime import datetime, timezone

from .database import ChatSession, get_session


def list_sessions() -> list[dict]:
    """Return all chat sessions, newest first. Avoids full JSON parse for speed."""
    session = get_session()
    try:
        sessions = (
            session.query(ChatSession)
            .order_by(ChatSession.updated_at.desc())
            .all()
        )
        result = []
        for s in sessions:
            # Count messages cheaply without parsing full JSON
            msg_count = s.messages.count('"role"')
            # Title is already set to first user message by save_messages
            preview = s.title if s.title != "新对话" else ""
            result.append({
                "id": s.id,
                "title": s.title,
                "preview": preview[:40],
                "message_count": msg_count,
                "created_at": s.created_at.isoformat() if s.created_at else "",
                "updated_at": s.updated_at.isoformat() if s.updated_at else "",
            })
        return result
    finally:
        session.close()


def create_session(title: str = "新对话") -> int:
    """Create a new chat session. Returns the session id."""
    session = get_session()
    try:
        s = ChatSession(title=title, messages="[]")
        session.add(s)
        session.commit()
        return s.id
    finally:
        session.close()


def get_messages(session_id: int) -> list[dict]:
    """Load messages for a session. Returns [] if session not found."""
    sess = get_session()
    try:
        s = sess.query(ChatSession).get(session_id)
        if not s:
            return []
        return json.loads(s.messages)
    finally:
        sess.close()


def save_messages(session_id: int, messages: list[dict]) -> None:
    """Persist messages for a session and update its timestamp/title."""
    sess = get_session()
    try:
        s = sess.query(ChatSession).get(session_id)
        if not s:
            return
        s.messages = json.dumps(messages, ensure_ascii=False)
        s.updated_at = datetime.now(timezone.utc)
        # Auto-title from first user message
        for m in messages:
            if m["role"] == "user":
                s.title = m["content"][:50]
                break
        sess.commit()
    finally:
        sess.close()


def rename_session(session_id: int, new_title: str) -> str:
    sess = get_session()
    try:
        s = sess.query(ChatSession).get(session_id)
        if not s:
            return "会话不存在。"
        s.title = new_title
        sess.commit()
        return f"已重命名为：{new_title}"
    finally:
        sess.close()


def delete_session(session_id: int) -> str:
    sess = get_session()
    try:
        s = sess.query(ChatSession).get(session_id)
        if not s:
            return "会话不存在。"
        title = s.title
        sess.delete(s)
        sess.commit()
        return f"已删除会话：{title}"
    finally:
        sess.close()
