from __future__ import annotations

from http import HTTPStatus


class StrategyError(Exception):
    def __init__(self, message: str, *, status: HTTPStatus = HTTPStatus.BAD_REQUEST) -> None:
        super().__init__(message)
        self.status = status
