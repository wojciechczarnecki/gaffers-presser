from app.core.errors import CollectorError


class FplUnavailableError(CollectorError):
    pass


class FplNotFoundError(CollectorError):
    pass


class PayloadError(CollectorError):
    pass
