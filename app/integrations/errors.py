from __future__ import annotations


class ProviderNotConfiguredError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


__all__ = ["ProviderNotConfiguredError"]
