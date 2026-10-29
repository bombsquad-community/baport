# ba_meta require api 7
"""Handwritten api 7 plugin with rules the real plugins in tests/cases don't cover."""
from __future__ import annotations

from typing import TYPE_CHECKING

import ba
import _ba
from ba import _map
from ba._campaign import getcampaign
from ba._generated.enums import SpecialChar as SC
from ba import charstr as cs
from bastd.actor.bomb import Bomb

if TYPE_CHECKING:
    pass


def ui_hint() -> None:
    with _ba.Context('ui'):
        ba.screenmessage(cs(SC.TICKET) + ' tickets', top=True, image=None)
    ba.playsound(ba.getsound('ding'), volume=0.5,
                 position=(0, 1, 0))
    ba.timer(0.5, ba.Call(ba.playsound, ba.getsound('swish')))
    if _ba.get_ui_input_device() is not None:
        _ba.chatmessage('hi')


def campaign_levels() -> list[str]:
    return [level.name for level in getcampaign('Default').levels]


def shake(activity: ba.Activity) -> None:
    with ba.Context(activity):
        ba.timer(0.1, lambda: _ba.camerashake(1.0), timetype=ba.TimeType.SIM)
        ba.Timer(2.0, ui_hint, timetype=ba.TimeType.REAL)
    print(ba.time(timetype=ba.TimeType.REAL), _map.Map)


class SlowBomb(Bomb):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        ba.timer(0.3, self.handlemessage)


def setup() -> None:
    if True:
        import ba
        print(ba.app.ui.uiscale)


# ba_meta export plugin
class Rules(ba.Plugin):
    def on_app_running(self) -> None:
        ba.timer(1.0, setup)
