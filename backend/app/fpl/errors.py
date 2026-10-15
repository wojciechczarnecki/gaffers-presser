from app.core.errors import AppError


class FplUnavailableError(AppError):
    pass


class FplNotFoundError(AppError):
    pass


class JobError(AppError):
    pass


class PayloadError(AppError):
    def __init__(self, endpoint: str, field: str) -> None:
        self.endpoint = endpoint
        self.field = field
        super().__init__(f"{endpoint}: missing or invalid field {field}")
