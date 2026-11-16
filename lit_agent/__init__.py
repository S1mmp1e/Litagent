"""LitAgent - Literature Management Agent."""

from .database import Category, ChatSession, Paper, PaperCategory, Tag, get_session, init_db
from .category_manager import (
    ai_suggest_categories,
    approve_paper_category,
    create_category,
    delete_category,
    get_papers_in_category,
    get_pending_suggestions,
    get_uncategorized_papers,
    list_categories,
    manually_categorize,
    reject_paper_category,
    remove_from_category,
    rename_category,
)
from .chat_history import (
    create_session,
    delete_session,
    get_messages,
    list_sessions,
    rename_session,
    save_messages,
)
