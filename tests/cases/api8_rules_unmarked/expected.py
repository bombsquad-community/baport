# Porting made easier by baport.(https://github.com/bombsquad-community/baport)
# ba_meta require api 9
"""Handwritten api 8 plugin with rules the real plugins in tests/cases don't cover."""
from __future__ import annotations

import logging
from functools import partial as tpartial
from typing import TYPE_CHECKING

import babase
import bauiv1 as bui
import bascenev1 as bs
from bascenev1lib.actor.spaz import Spaz
from bascenev1lib.actor.popuptext import PopupText

if TYPE_CHECKING:
    from typing import Any


class Glow(bs.Actor):
    def __init__(self, position: tuple[float, float, float]):
        super().__init__()
        self.node = bs.newnode('light', attrs={'position': position})

    def owner(self) -> Any:
        return self.getactivity(doraise=False)


class Popup(PopupText):
    pass


def show(position: Any) -> None:
    Popup('hi', position=position, color=(1, 0, 0), random_offset=1.0)
    Spaz(color=(1, 1, 1), highlight=(0, 0, 0), character='Spaz')
    bs.timer(1.0, tpartial(print, 'later'))
    print(bs.InputType(3))
    try:
        None
    except Exception:
        logging.exception('no party icon')


class Party(bui.Window):
    def __init__(self) -> None:
        super().__init__(bui.containerwidget(size=(200, 200)))
        self._uiopenstate = bui.UIOpenState('classicparty')

    def close(self) -> None:
        self._uiopenstate = None


class Game(bs.TeamGameActivity[bs.Player, bs.Team]):
    def get_instance_display_string(self, *, langstr: bool = False) -> babase.Lstr:
        return babase.Lstr(value='Game')


# ba_meta export babase.Plugin
class Rules(babase.Plugin):
    def on_app_running(self) -> None:
        bui.textwidget(text=babase.SpecialChar.TICKET.value, on_submit_call=print)
