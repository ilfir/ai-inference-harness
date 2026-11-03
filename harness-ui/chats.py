"""Chat history for the Chat tab: one JSON file, newest chats kept, at most MAX_CHATS.

Stored next to the connection settings (in the app: ~/Library/Application Support/AI Harness/chats.json).
Each chat is {id, title, created, updated, messages}; messages keep what the UI needs to redraw them
(role, content, and for replies the thinking text, web tool lines and the stats line).
"""

import json
import os
import re
import threading
import time
from pathlib import Path

MAX_CHATS = 20
MAX_TITLE = 80
_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class ChatStore:
    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.Lock()

    def _load(self):
        try:
            data = json.loads(self.path.read_text())
            return [c for c in data.get("chats", []) if isinstance(c, dict) and _ID.match(str(c.get("id", "")))]
        except (OSError, ValueError, AttributeError):
            return []

    def _write(self, chats):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump({"version": 1, "chats": chats}, f)
        os.replace(tmp, self.path)

    @staticmethod
    def _summary(c):
        return {"id": c["id"], "title": c.get("title") or "New chat", "created": c.get("created"),
                "updated": c.get("updated"), "count": len(c.get("messages", []))}

    def list(self):
        with self.lock:
            chats = sorted(self._load(), key=lambda c: c.get("updated") or 0, reverse=True)
            return [self._summary(c) for c in chats]

    def get(self, chat_id):
        with self.lock:
            for c in self._load():
                if c["id"] == chat_id:
                    return c
        raise KeyError("no such chat")

    def save(self, chat_id, body):
        """Create or replace a chat; drops the oldest beyond MAX_CHATS. Returns (summary, pruned ids)."""
        if not _ID.match(chat_id):
            raise ValueError("bad chat id")
        messages = body.get("messages")
        if not isinstance(messages, list):
            raise ValueError("messages must be a list")
        now = time.time()
        with self.lock:
            chats = self._load()
            old = next((c for c in chats if c["id"] == chat_id), None)
            title = str(body.get("title") or (old or {}).get("title") or _title(messages))[:MAX_TITLE]
            chat = {"id": chat_id, "title": title, "created": (old or {}).get("created", now),
                    "updated": now, "messages": messages}
            chats = [c for c in chats if c["id"] != chat_id] + [chat]
            chats.sort(key=lambda c: c.get("updated") or 0, reverse=True)
            pruned = [c["id"] for c in chats[MAX_CHATS:]]
            chats = chats[:MAX_CHATS]
            self._write(chats)
            return self._summary(chat), pruned

    def delete(self, chat_id):
        with self.lock:
            chats = self._load()
            kept = [c for c in chats if c["id"] != chat_id]
            if len(kept) == len(chats):
                raise KeyError("no such chat")
            self._write(kept)


def _title(messages):
    for m in messages:
        if isinstance(m, dict) and m.get("role") == "user" and m.get("content"):
            t = " ".join(str(m["content"]).split())
            return t if len(t) <= 60 else t[:57].rstrip() + "…"
    return "New chat"
