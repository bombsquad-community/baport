# ba_meta require api 8
"""Handwritten api 8 plugin with rules the real plugins in tests/cases don't cover."""
from __future__ import annotations

from typing import TYPE_CHECKING

import babase
import bauiv1 as bui
import bascenev1 as bs
from bascenev1lib.actor.spaz import Spaz
from bascenev1lib.actor.popuptext import PopupText
from efro.call import tpartial
from efro.util import enum_by_value
from babase._error import print_exception

if TYPE_CHECKING:
    from typing import Any


class Glow(bs.Actor):
    def __init__(self, position: tuple[float, float, float]):
        super().__init__()
        self.node = bs.newnode('light', attrs={'position': position})

    def owner(self) -> Any:
        return self._activity()


class Popup(PopupText):
    pass


def show(position: Any) -> None:
    Popup('hi', position, (1, 0, 0), 1.0)
    Spaz((1, 1, 1), (0, 0, 0), 'Spaz')
    bs.timer(1.0, tpartial(print, 'later'))
    print(enum_by_value(bs.InputType, 3))
    try:
        bui.set_party_icon_always_visible(True)
    except Exception:
        print_exception('no party icon')


class Party(bui.Window):
    def __init__(self) -> None:
        super().__init__(bui.containerwidget(size=(200, 200)))
        bui.set_party_window_open(True)

    def close(self) -> None:
        bui.set_party_window_open(False)


class Game(bs.TeamGameActivity[bs.Player, bs.Team]):
    def get_instance_display_string(self) -> babase.Lstr:
        return babase.Lstr(value='Game')


# ba_meta export plugin
class Rules(babase.Plugin):
    def on_app_running(self) -> None:
        bui.textwidget(text=babase.charstr(babase.SpecialChar.TICKET), on_return_press_call=print)
