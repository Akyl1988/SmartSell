# sitecustomize.py
# Автоматически подхватывается Python при старте процесса, если лежит на sys.path.
# Чинит совместимость old-anyio-backed → new-trio: добавляет trio.MultiError alias.

try:
    import trio  # noqa: F401
except Exception:
    trio = None

if trio is not None and not hasattr(trio, "MultiError"):
    # Trio >= 0.22 перешёл на ExceptionGroup; anyio ожидает trio.MultiError
    try:
        # Python 3.11+: BaseExceptionGroup доступен
        BaseExcGroup = BaseExceptionGroup  # type: ignore[name-defined]
    except Exception:  # pragma: no cover
        # на всякий случай для очень старых интерпретаторов
        class BaseExcGroup(Exception):  # type: ignore
            pass

    class _MultiError(BaseExcGroup):  # type: ignore
        """Базовый shim, совместимый по имени; плагину anyio этого достаточно."""

        pass

    try:
        setattr(trio, "MultiError", _MultiError)
    except Exception:
        # если trio — proxy-модуль с защитой, создадим тонкий shim-модуль и подменим sys.modules
        import sys
        import types  # noqa: PLC0415

        m = types.ModuleType("trio")
        for attr in dir(trio):
            try:
                setattr(m, attr, getattr(trio, attr))
            except Exception:
                pass
        setattr(m, "MultiError", _MultiError)
        sys.modules["trio"] = m
