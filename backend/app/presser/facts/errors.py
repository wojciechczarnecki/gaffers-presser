from app.core.errors import CollectorError


class NoFactsError(CollectorError):
    pass


class FactSheetError(CollectorError):
    pass
