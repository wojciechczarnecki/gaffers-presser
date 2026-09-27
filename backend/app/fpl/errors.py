from app.core.errors import CollectorError


class FplUnavailableError(CollectorError):
    pass


class FplNotFoundError(CollectorError):
    pass


class JobError(CollectorError):
    pass


class PayloadError(CollectorError):
    def __init__(self, endpoint: str, field: str) -> None:
        self.endpoint = endpoint
        self.field = field
        super().__init__(f"{endpoint}: missing or invalid field {field}")
