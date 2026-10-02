# Importing this package registers the alert log tables on SQLModel.metadata.

from app.alerts.models import Alert, AlertPost

__all__ = ["Alert", "AlertPost"]
