"""Smoke test for the function_app circular-import contract (code10-MN-03 close).

`function_app/function_app.py` instantiates `app = func.FunctionApp(...)` and
then imports the trigger modules from `function_app/triggers/`. Each trigger
module imports the `app` symbol back from `function_app.function_app` to
register its decorators. This is a controlled circular import — the order
inside `function_app.py` (instantiate-then-import) makes it safe — but the
contract is implicit. Any reordering (e.g. moving the imports above the
`func.FunctionApp(...)` line) would break trigger registration at Function
App boot time, AFTER the Bicep deploy succeeded but BEFORE the first cron
invocation.

This test pins the contract: a clean import of `function_app.function_app`
must succeed, and the five expected triggers must be registered on the
shared `app` instance.

If this test fails, the fix is in `function_app/function_app.py`: confirm
that `app = func.FunctionApp(...)` is on its own line, BEFORE the
`from function_app.triggers import ...` line. The order is load-bearing.
"""

from __future__ import annotations

import importlib
import sys

import pytest


@pytest.fixture
def fresh_function_app(monkeypatch: pytest.MonkeyPatch) -> object:
    """Force a clean (re-)import of `function_app.function_app`.

    Tests in the broader suite already imported `function_app.triggers.*`
    transitively, so `sys.modules` is hot. Wipe the cached entries before
    re-importing so this test exercises the cold-start ordering, which is
    what a Functions worker actually sees on first invocation.
    """
    cached = [
        name
        for name in list(sys.modules)
        if name == "function_app.function_app"
        or name.startswith("function_app.triggers")
        or name == "function_app"
    ]
    for name in cached:
        monkeypatch.delitem(sys.modules, name, raising=False)
    module = importlib.import_module("function_app.function_app")
    return module


def test_function_app_module_imports_cleanly(fresh_function_app: object) -> None:
    """A fresh import of function_app.function_app must succeed (no ImportError)."""
    # The fixture already executed the import; reaching this assertion
    # means no ImportError / RecursionError was raised during the import
    # chain. The positive `app` attribute check pins the contract that
    # the FunctionApp instance is exposed at module scope.
    assert hasattr(fresh_function_app, "app"), (
        "function_app.function_app must expose an `app` attribute (the "
        "FunctionApp instance the trigger decorators register against)."
    )


def test_all_five_triggers_register(fresh_function_app: object) -> None:
    """The five documented triggers must all be present on the app's function list.

    The Azure Functions Python v2 runtime collects registered functions on
    `app._FunctionApp__functions` (private; structure stabilised across
    `azure-functions>=1.18`). We probe via `get_functions()` which is the
    documented public method.
    """
    app = fresh_function_app.app  # type: ignore[attr-defined]
    functions = app.get_functions()
    names = {f.get_function_name() for f in functions}
    expected = {
        "daily_generator",
        "warmup",
        "bacpac_export",
        "ping",
        "ask",
    }
    missing = expected - names
    assert not missing, (
        f"Expected triggers {sorted(missing)} were not registered. "
        f"Got: {sorted(names)}. The `from function_app.triggers import ...` "
        "block in function_app.py may have reordered or lost an entry."
    )
