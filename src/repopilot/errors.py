"""Safe, structured errors across runtime boundaries."""


class RepoPilotError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
