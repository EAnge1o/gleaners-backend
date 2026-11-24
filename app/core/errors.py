class DomainError(Exception):
    status_code = 400

    def __init__(self, message: str, code: str | None = None):
        super().__init__(message)
        self.message = message
        self.code = code or self.__class__.__name__


class NotFound(DomainError):
    status_code = 404


class Forbidden(DomainError):
    status_code = 403


class Conflict(DomainError):
    status_code = 409


class IllegalTransition(Conflict):
    pass


class ValidationFailed(DomainError):
    status_code = 422