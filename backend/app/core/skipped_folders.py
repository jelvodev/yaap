"""The folders YAAP leaves out when it walks a project: the agent's file tools, search, the repository map and folder
attachments. `.yaap` holds YAAP's build copies. The browser keeps the same list in `frontend/src/api/folders.ts`."""

SKIPPED_FOLDERS = frozenset({".git", ".yaap", "node_modules", ".venv", "__pycache__", "dist", "build", ".next"})
