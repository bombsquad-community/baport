# Porting made easier by baport.(https://github.com/bombsquad-community/baport)
# ba_meta require api 9
"""Handwritten api 7 plugin with rules the real plugins in tests/cases don't cover."""
from __future__ import annotations

import _bascenev1
from typing import TYPE_CHECKING

import babase
import bauiv1 as bui
import bascenev1 as bs
import _babase
from bascenev1 import _map
from babase import SpecialChar as SC
from bascenev1lib.actor.bomb import Bomb

if TYPE_CHECKING:
    pass


def ui_hint() -> None:
    with _babase.ContextRef.empty():
        bui.screenmessage(SC.TICKET.value + ' tickets')
    bui.getsound('ding').play(volume=0.5, position=(0, 1, 0))
    babase.apptimer(0.5, babase.CallPartial(bui.getsound('swish').play))
    if _bascenev1.get_ui_input_device() is not None:  # baport: private _bascenev1.get_ui_input_device was removed in 1.7.44
        bs.chatmessage('hi')


def campaign_levels() -> list[str]:
    return [level.name for level in babase.app.classic.getcampaign('Default').levels]


def shake(activity: bs.Activity) -> None:
    with activity.context:
        bs.timer(0.1, lambda: bs.camerashake(1.0))
        babase.AppTimer(2.0, ui_hint)
    print(babase.apptime(), _map.Map)


class SlowBomb(Bomb):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        bs.timer(0.3, self.handlemessage)


def setup() -> None:
    if True:
        import babase
        import bauiv1 as bui
        import bascenev1 as bs
        print(bui.app.ui_v1.uiscale)


# ba_meta export babase.Plugin
class Rules(babase.Plugin):
    def on_app_running(self) -> None:
        babase.apptimer(1.0, setup)
