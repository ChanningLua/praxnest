"""Team memory — find notes related to what you're writing right now.

V0.1 implementation: keyword-based similarity over the existing FTS5
index. Extract distinctive terms from the input body, OR-query FTS5,
rank by bm25. Good enough to surface "someone else wrote about this
3 weeks ago" without adding a new dependency.

V0.2 will swap the backend to real vector embeddings (sentence-
transformers in-process, or shell-out to a side service). The public
function signature here — ``find_similar(workspace_id, body_md, ...) -> rows``
— is what the route + UI consume, and won't change.
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from typing import Any

from . import db


# Stopwords we drop before keyword extraction. Just enough to avoid
# matching everything by "the / and / 的 / 是" — not a complete list.
_STOPWORDS_EN = {
    "the", "a", "an", "and", "or", "but", "if", "in", "on", "at", "to", "of",
    "for", "with", "as", "is", "are", "was", "were", "be", "been", "being",
    "this", "that", "these", "those", "it", "its", "i", "you", "we", "they",
    "from", "by", "not", "no", "yes", "do", "does", "did", "have", "has", "had",
    "will", "would", "could", "should", "may", "might", "can", "must",
}
_STOPWORDS_ZH = {
    "的", "了", "是", "在", "和", "也", "就", "都", "但", "或", "及", "与",
    "把", "被", "让", "给", "对", "向", "由", "如", "因", "所", "这", "那",
    "我", "你", "他", "她", "它", "我们", "你们", "他们", "什么", "怎么",
    "可以", "可能", "需要", "应该", "已经",
}
_STOPWORDS = _STOPWORDS_EN | _STOPWORDS_ZH


_ASCII_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_-]{2,}", re.UNICODE)
_CJK_RUN_RE = re.compile(r"[\u4e00-\u9fff]+")
_CJK_CHAR_RE = re.compile(r"[\u4e00-\u9fff]")


def extract_keywords(text: str, *, top_n: int = 8) -> list[str]:
    """Pick distinctive terms from `text`.

    Tokenization rules:
    - ASCII: 3+ char alphanumeric runs (lowercase). 'login', 'bcrypt'.
    - CJK: bigrams from each contiguous Chinese run. '登录功能' →
      '登录', '录功', '功能'. Bigrams are imperfect but match the
      tokenization most Chinese-aware FTS engines use as a fallback,
      and avoid pulling in jieba as a dependency for v0.1.

    Stopwords (English + common Chinese fillers) get filtered before
    counting. Returns the top_n most frequent.

    V0.2 will replace this whole function with vector embeddings;
    callers don't need to care.
    """
    if not text or not text.strip():
        return []

    tokens: list[str] = []

    # English/ASCII word tokens.
    for m in _ASCII_TOKEN_RE.finditer(text):
        tokens.append(m.group(0).lower())

    # CJK bigrams, per contiguous run.
    for run in _CJK_RUN_RE.findall(text):
        if len(run) < 2:
            continue
        for i in range(len(run) - 1):
            tokens.append(run[i:i + 2])

    counts = Counter(t for t in tokens if t not in _STOPWORDS)
    return [tok for tok, _ in counts.most_common(top_n)]


def find_similar(
    data_dir: Path, *, workspace_id: int, body_md: str, exclude_note_id: int | None = None, top_k: int = 5,
) -> list[dict[str, Any]]:
    """Return up to `top_k` notes in this workspace whose content
    overlaps with `body_md`'s distinctive terms.

    Returns the same shape as `notes.search` (id, folder_path, title,
    snippet, rank) so the UI can reuse the rendering.
    """
    keywords = extract_keywords(body_md, top_n=8)
    if not keywords:
        return []

    # FTS5 OR query — quote each keyword to disable mini-language.
    # We want broad recall, then bm25 picks the best.
    fts_query = " OR ".join(f'"{kw}"' for kw in keywords)
    top_k = max(1, min(int(top_k), 50))

    conn = db.connect(data_dir)
    try:
        sql_args: list[Any] = [fts_query, workspace_id]
        sql = """
            SELECT n.id, n.folder_path, n.title,
                   snippet(notes_fts, 1, '<mark>', '</mark>', '…', 12) AS snippet,
                   bm25(notes_fts, 2.0, 1.0) AS rank
              FROM notes_fts
              JOIN notes n ON n.id = notes_fts.rowid
             WHERE notes_fts MATCH ? AND n.workspace_id = ?
        """
        if exclude_note_id is not None:
            sql += " AND n.id != ?"
            sql_args.append(int(exclude_note_id))
        sql += " ORDER BY rank LIMIT ?"
        sql_args.append(top_k)

        rows = conn.execute(sql, tuple(sql_args)).fetchall()
    finally:
        conn.close()

    return [{**dict(r), "matched_keywords": keywords} for r in rows]


def find_similar_across_workspaces(
    data_dir: Path, *, user_id: int, body_md: str, top_k: int = 5,
) -> list[dict[str, Any]]:
    """Cross-workspace recall — search all workspaces this user is in.

    Adds ``workspace_id`` + ``workspace_name`` to each result so the UI
    can surface "found in workspace X". Useful when a single user
    works on multiple projects and wants the AI to remember decisions
    from a past project.
    """
    keywords = extract_keywords(body_md, top_n=8)
    if not keywords:
        return []
    fts_query = " OR ".join(f'"{kw}"' for kw in keywords)
    top_k = max(1, min(int(top_k), 50))

    conn = db.connect(data_dir)
    try:
        rows = conn.execute(
            """
            SELECT n.id, n.workspace_id, w.name AS workspace_name,
                   n.folder_path, n.title,
                   snippet(notes_fts, 1, '<mark>', '</mark>', '…', 12) AS snippet,
                   bm25(notes_fts, 2.0, 1.0) AS rank
              FROM notes_fts
              JOIN notes n ON n.id = notes_fts.rowid
              JOIN workspaces w ON w.id = n.workspace_id
              JOIN workspace_members m ON m.workspace_id = n.workspace_id
             WHERE notes_fts MATCH ? AND m.user_id = ?
             ORDER BY rank
             LIMIT ?
            """,
            (fts_query, user_id, top_k),
        ).fetchall()
    finally:
        conn.close()
    return [{**dict(r), "matched_keywords": keywords} for r in rows]
