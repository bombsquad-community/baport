# Porting made easier by baport.(https://github.com/bombsquad-community/baport)
# ba_meta require api 9
"""Handwritten api 6 plugin: account, thread and log renames from the api 6 and 7 releases."""
from __future__ import annotations

from typing import TYPE_CHECKING

import babase
import bauiv1 as bui
import bascenev1 as bs
import _babase
from efro.dataclassio import dataclass_from_dict

if TYPE_CHECKING:
    from typing import Any


def describe_account() -> str:
    if bui.app.classic.accounts.have_pro():
        return f'{bui.app.plus.get_v1_account_name()} ({bui.app.plus.get_v1_account_type()})'
    return bui.app.plus.get_v1_account_display_string()


def dump(data: dict[str, Any]) -> None:
    assert _babase.in_logic_thread()
    print(_babase.get_v1_cloud_log())  # baport: private _babase.get_v1_cloud_log was removed in 1.8.0
    bs.ls_objects()
    bui.screenmessage(str(dataclass_from_dict(dict, data)), color=(0, 1, 0))


# ba_meta export babase.Plugin
class AccountInfo(babase.Plugin):
    def on_app_running(self) -> None:
        babase.apptimer(1.0, describe_account)
