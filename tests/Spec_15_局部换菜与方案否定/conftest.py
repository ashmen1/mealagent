from __future__ import annotations

import importlib
from types import SimpleNamespace
from typing import Any, Callable

import pytest


@pytest.fixture
def menu_change_contract() -> SimpleNamespace:
    """在测试体调用时加载接口，避免缺实现被计为setup error。"""

    def delayed(name: str) -> Callable[..., Any]:
        def invoke(*args: Any, **kwargs: Any) -> Any:
            try:
                module = importlib.import_module("backend.services.menu_change")
                action = getattr(module, name)
            except (ModuleNotFoundError, AttributeError) as exc:
                pytest.fail(
                    f"缺少 Spec_15 换菜业务接口 {name}：{exc}",
                    pytrace=False,
                )
            return action(*args, **kwargs)

        return invoke

    return SimpleNamespace(
        resolve=delayed("resolve_menu_change"),
        filter_candidates=delayed("filter_excluded_candidates"),
        finalize=delayed("finalize_menu_state"),
    )
