"""Business errors shared by transactional services."""


class ClaimError(Exception):
    """An invalid operation on a claim or agent run."""

    def __init__(self, status: int, message: str):
        self.status = status
        self.message = message
        super().__init__(message)
