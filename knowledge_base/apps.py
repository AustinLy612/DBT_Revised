import os
import threading

from django.apps import AppConfig


class KnowledgeBaseConfig(AppConfig):
    name = "knowledge_base"

    def ready(self) -> None:
        """Preload the bge-m3 embedding model in a background thread.

        This avoids a ~9-second cold-start delay (model load + network
        checks) on the first user request that triggers semantic search.
        The model is typically ready by the time a user logs in and
        navigates to the teaching page.

        Only runs in the main process (skip the Django auto-reloader's
        child process) to avoid double-loading the model.
        Also skips during test runs since all RAG calls are mocked.
        """
        import sys
        if os.environ.get("RUN_MAIN") == "true":
            return  # Django auto-reloader child process — skip
        if any("manage.py test" in arg or "pytest" in arg for arg in sys.argv):
            return  # Test run — skip, all RAG calls are mocked

        from .services import preload_embedding_model

        t = threading.Thread(target=preload_embedding_model, daemon=True)
        t.start()
