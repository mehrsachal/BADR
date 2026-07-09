"""History management for BADR — saves and loads chat sessions."""
import os
import json
import uuid
from datetime import datetime

class HistoryManager:
    def __init__(self):
        # Determine storage path
        if os.name == "nt":
            self.base_dir = os.path.join(os.environ.get("APPDATA", "."), "BADR", "history")
        else:
            self.base_dir = os.path.expanduser("~/.local/share/BADR/history")
        
        try:
            os.makedirs(self.base_dir, exist_ok=True)
        except Exception:
            # Fallback to plugin directory if permission denied
            self.base_dir = os.path.join(os.path.dirname(__file__), "history")
            os.makedirs(self.base_dir, exist_ok=True)

    def save_session(self, session_id, title, messages):
        """Save a session to a JSON file."""
        if not session_id:
            session_id = uuid.uuid4().hex[:12]
            
        path = os.path.join(self.base_dir, f"{session_id}.json")
        data = {
            "id": session_id,
            "title": title or "Untitled Chat",
            "timestamp": datetime.now().isoformat(),
            "messages": messages
        }
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass
        return session_id

    def list_sessions(self):
        """Return a list of sessions, sorted by recency."""
        sessions = []
        if not os.path.exists(self.base_dir):
            return []
            
        for filename in os.listdir(self.base_dir):
            if filename.endswith(".json"):
                try:
                    with open(os.path.join(self.base_dir, filename), "r", encoding="utf-8") as f:
                        data = json.load(f)
                        sessions.append({
                            "id": data.get("id"),
                            "title": data.get("title", "Untitled Chat"),
                            "timestamp": data.get("timestamp", ""),
                            "msg_count": len(data.get("messages", []))
                        })
                except Exception:
                    continue
        
        # Sort by timestamp (ISO format allows string sorting)
        return sorted(sessions, key=lambda x: x["timestamp"], reverse=True)

    def load_session(self, session_id):
        """Load a session's messages."""
        path = os.path.join(self.base_dir, f"{session_id}.json")
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                return None
        return None

    def delete_session(self, session_id):
        """Delete a session file."""
        path = os.path.join(self.base_dir, f"{session_id}.json")
        if os.path.exists(path):
            try:
                os.remove(path)
            except Exception:
                pass
