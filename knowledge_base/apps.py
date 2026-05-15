import os

from django.apps import AppConfig


class KnowledgeBaseConfig(AppConfig):
    name = "knowledge_base"

    def ready(self) -> None:
        """Preload the fastembed ONNX embedding model at startup.

        Only runs when EMBEDDING_PRELOAD=true env var is set (web service only).
        Loads synchronously so gunicorn --preload can share the model across
        workers via copy-on-write fork.

        Skips in reloader child process and test runs.
        Celery worker/beat do NOT preload — they lazy-load only when a
        document processing task actually needs embeddings.
        """
        import sys

        if os.environ.get("EMBEDDING_PRELOAD") != "true":
            return
        if os.environ.get("RUN_MAIN") == "true":
            return
        if any("manage.py test" in arg or "pytest" in arg for arg in sys.argv):
            return

        from .embedding import preload_embedding_model

        preload_embedding_model()
