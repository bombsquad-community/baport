# ba_meta require api 6
"""Handwritten api 6 plugin: account, thread and log renames from the api 6 and 7 releases."""
from __future__ import annotations

from typing import TYPE_CHECKING

import ba
import _ba
from efro.dataclasses import dataclass_from_dict

if TYPE_CHECKING:
    from typing import Any


def describe_account() -> str:
    if ba.app.accounts.have_pro():
        return f'{_ba.get_account_name()} ({_ba.get_account_type()})'
    return _ba.get_account_display_string()


def dump(data: dict[str, Any]) -> None:
    assert _ba.in_game_thread()
    print(_ba.getlog())
    _ba.printobjects()
    ba.screenmessage(str(dataclass_from_dict(dict, data)), color=(0, 1, 0))


# ba_meta export plugin
class AccountInfo(ba.Plugin):
    def on_app_launch(self) -> None:
        ba.timer(1.0, describe_account, timetype=ba.TimeType.REAL)
