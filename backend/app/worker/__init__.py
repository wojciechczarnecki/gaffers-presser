# Importing this package registers the worker's tables on SQLModel.metadata.

from app.worker.models import JobRun

__all__ = ["JobRun"]
