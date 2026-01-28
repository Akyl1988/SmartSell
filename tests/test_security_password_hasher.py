import pytest

from app.core.security import _HAS_ARGON2, _password_context_options


def test_password_hasher_uses_test_params_only_in_testing() -> None:
    if not _HAS_ARGON2:
        pytest.skip("argon2 not available")

    test_opts = _password_context_options(True)
    prod_opts = _password_context_options(False)

    assert test_opts != prod_opts
    assert test_opts.get("argon2__time_cost") == 1
    assert test_opts.get("argon2__memory_cost") == 1024
    assert test_opts.get("argon2__parallelism") == 1

    assert "argon2__time_cost" not in prod_opts
    assert "argon2__memory_cost" not in prod_opts
    assert "argon2__parallelism" not in prod_opts
