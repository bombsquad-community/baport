#!/usr/bin/env python3

"""Port BombSquad plugins from api 6/7/8 to api 9, up to a chosen game version.

Usage: baport.py -f <plugin.py> [<plugin.py> ...] [-t client|server] [-v 1.8.3]

Conversions are picked based on the target game version (-v, defaults to the
latest release baport knows about), so only APIs that exist in that version are
used. Anything that can't be converted automatically is listed per file after
porting, with line numbers. Some common ones:
From:
    with ba.Context(_ba.foreground_host_activity()):
To:
    with _ba.foreground_host_activity().context:
From:
    ba.Timer((POWERUP_WEAR_OFF_TIME - 2000),ba.WeakCall(self._multi_bomb_wear_off_flash),timeformat=ba.TimeFormat.MILLISECONDS)
To:
    ba.Timer((POWERUP_WEAR_OFF_TIME - 2000 / 1000),ba.WeakCall(self._multi_bomb_wear_off_flash))
From:
    ba.playsound(self._dingsound if importance == 1 else self._dingsoundhigh, volume=0.6)
To:
    self._dingsound.play(volume=0.6) if importance == 1 else self._dingsoundhigh.play(volume=0.6)
From:
    bui.app.ui_v1.use_toolbars
To:
    Either True or False, whatever works for you
"""

import argparse
import ast
import difflib
from pathlib import Path
import re
import sys
from typing import Callable, NamedTuple

TRADEMARK = "# Porting made easier by baport.(https://github.com/bombsquad-community/baport)\n"

# First game release of each api version (see CHANGELOG.md in ballistica).
API_FIRST_VERSION = {
    # api 6 is already in ballistica's first public commit (2019), the changelog starts at 1.4.93
    6: (1, 4, 0),
    7: (1, 7, 2),
    8: (1, 7, 20),
    9: (1, 7, 37),
}
# Newest release whose changes baport knows about; the default target.
LATEST_VERSION = (1, 8, 5)
# Last game release of each api version baport can port to.
API_LAST_VERSION = {
    8: (1, 7, 36),
    9: LATEST_VERSION,
}


def detect_file_encoding(file_path) -> str | None:
    encodings = ['utf-8', 'latin-1', 'ascii', 'cp1252']
    for encoding in encodings:
        try:
            with open(file_path, 'rb') as f:
                f.read().decode(encoding)
            return encoding
        except UnicodeDecodeError:
            pass
    return None


def ba_aliases(content: str) -> dict[str, str]:
    """Returns {alias: module} for every top level `import ba...` in content."""
    aliases = {}
    for line in re.findall(r'^\s*import\s+(.+)$', content, flags=re.MULTILINE):
        for part in line.split('#')[0].split(','):
            m = re.fullmatch(r'\s*(_?ba[\w.]*)(?:\s+as\s+(\w+))?\s*', part)
            if m:
                aliases[m.group(2) or m.group(1)] = m.group(1)
    return aliases


def add_import(content: str, statement: str) -> str:
    """Adds an import statement before the plugin's first top level import.

    With no imports it goes after the module docstring and `from __future__`
    imports, never inside a string.
    """
    try:
        body = ast.parse(content).body
    except SyntaxError:
        body = []
    imports = [
        n
        for n in body
        if isinstance(n, (ast.Import, ast.ImportFrom))
        and not (isinstance(n, ast.ImportFrom) and n.module == "__future__")
    ]
    if imports:
        line = imports[0].lineno
    else:
        line = 1
        for node in body:
            is_docstring = node is body[0] and isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
            if is_docstring or (isinstance(node, ast.ImportFrom) and node.module == "__future__"):
                line = node.end_lineno + 1
    lines = content.splitlines(keepends=True)
    index = sum(len(l) for l in lines[: line - 1])
    if index and not content[:index].endswith("\n"):
        statement = "\n" + statement
    return content[:index] + statement + "\n" + content[index:]


def ensure_import(content: str, module: str) -> str:
    """Adds `import module` if the plugin doesn't import it yet."""
    if re.search(rf'^\s*import\s+{re.escape(module)}\s*$', content, flags=re.MULTILINE):
        return content
    return add_import(content, f"import {module}")


def drop_import_name(content: str, module: str, name: str) -> str:
    """Removes name from `from module import ...` statements, dropping ones left empty."""

    def fix(match: re.Match) -> str:
        names = [n.strip() for n in match.group(2).strip("()").split(",")]
        kept = [n for n in names if n and n != name]
        if len(kept) == len([n for n in names if n]):
            return match.group(0)
        indent = match.group(1)[: len(match.group(1)) - len(match.group(1).lstrip())]
        if not kept:
            # an emptied import inside a block still needs a statement there
            return f"{indent}pass{match.group(3)}" if indent else ""
        if match.group(2).startswith("("):
            return (
                f"{match.group(1)}(\n{indent}    "
                + f",\n{indent}    ".join(kept)
                + f",\n{indent})"
                + match.group(3)
            )
        return match.group(1) + ", ".join(kept) + match.group(3)

    return re.sub(
        rf'^([ \t]*from {re.escape(module)} import )(\([^)]*\)|[\w ,]+$)(\n?)',
        fix,
        content,
        flags=re.MULTILINE,
    )


def drop_removed_function(content: str, module: str, name: str) -> str:
    """Neutralizes a function removed without replacement.

    Calls become None and other references a no-op lambda, which keeps
    plugins that wrap the function (old = bui.func; ...) loading.
    """
    # through any ba module name, also ones the api 7 port leaves unimported (_bui.x)
    forms = [rf'(?<![\w.])(?:_?ba\w*|_?bui|_?bs|{"|".join(map(re.escape, ba_aliases(content))) or "_?ba"})\.{name}\b']
    if re.search(rf'^\s*from _?{re.escape(module)} import .*\b{name}\b', content, flags=re.MULTILINE):
        for m in (module, "_" + module):
            content = drop_import_name(content, m, name)
        forms.append(rf'(?<![\w.])(?<!def ){name}\b')
    for form in forms:
        content = re.sub(rf'{form}\([^)]*\)', "None", content)
        # assigning to it only adds an attribute, so leave that alone
        content = re.sub(rf'{form}(?!\s*=[^=])', "(lambda *args, **kwargs: None)", content)
    return content


def module_alias(content: str, *modules: str) -> tuple[str, str]:
    """Returns (content, alias) for the first of modules the plugin imports.

    If it imports none of them the first one gets imported.
    """
    for alias, imported in ba_aliases(content).items():
        if imported in modules:
            return content, alias
    return ensure_import(content, modules[0]), modules[0]


class Change(NamedTuple):
    version: tuple[int, ...]
    apply: Callable[..., str]
    # set on the step that moves code to a new api version, skipped for code already there
    bump_to: int | None
    # the step needs the client/server plugin type
    needs_type: bool
    # private api change, skipped with --no-private
    private: bool = False
    # private names the version removed, and earlier removed ones it brought back
    removed: tuple[str, ...] = ()
    restored: tuple[str, ...] = ()


def change(
    version: str,
    bump: bool = False,
    needs_type: bool = False,
    private: bool = False,
    removed: list[str] = (),
    restored: list[str] = (),
):
    """Marks a method of an api class as (part of) the changes made in a game version.

    bump marks the step that moves code onto the class's api version.
    private marks a private api step (skipped with --no-private), which runs
    after the public ones of its version. removed lists the private names the
    version took away, which can't be ported and get reported after porting,
    and restored the earlier removed ones it brought back.
    """

    def mark(func: Callable[..., str]) -> staticmethod:
        # bump_to holds the bump flag until collect_changes() knows the class's api
        func.change = Change(
            tuple(int(p) for p in version.split('.')),
            func,
            bump,
            needs_type,
            private,
            tuple(removed),
            tuple(restored),
        )
        return staticmethod(func)

    return mark


class Api6:
    """Changes made by the api 6 releases (up to 1.7.1)."""

    API = 6

    @change("1.6.0")
    def v1_6_0(content: str) -> str:
        """efro.dataclasses is efro.dataclassio."""
        return re.sub(r'\befro\.dataclasses\b', "efro.dataclassio", content)

    @change("1.6.4")
    def v1_6_4(content: str) -> str:
        """ba._enums is generated as ba._generated.enums."""
        return content.replace("ba._enums", "ba._generated.enums")

    @change("1.7.0")
    def v1_7_0(content: str) -> str:
        """V1 account calls got a v1 in their name: get_account_id() is get_v1_account_id(), app.accounts is app.accounts_v1, ..."""
        content = re.sub(r'\.app\.accounts\b', ".app.accounts_v1", content)
        content = re.sub(
            r'\bget_(account_(?:id|name|type|state_num|state|display_string'
            r'|misc_read_val_2|misc_read_val|misc_val|ticket_count))\b',
            r'get_v1_\1',
            content,
        )
        return re.sub(r'\b_ba\.sign_(in|out)\(', r'_ba.sign_\1_v1(', content)


class Api7:
    """Changes made by the api 7 releases (1.7.2 - 1.7.19)."""

    API = 7

    @change("1.7.2", bump=True)
    def v1_7_2(content: str) -> str:
        """api 7: plugins have on_app_running() instead of on_app_launch()."""
        content = re.sub(r'^(\s*#\s*ba_meta require api) 6\b', r'\1 7', content, flags=re.MULTILINE)
        return re.sub(r'\bon_app_launch\b', "on_app_running", content)

    @change("1.7.3")
    def v1_7_3(content: str) -> str:
        """android_get_external_storage_path() is android_get_external_files_dir()."""
        return re.sub(
            r'\.android_get_external_storage_path\b', ".android_get_external_files_dir", content
        )

    @change("1.7.7")
    def v1_7_7(content: str) -> str:
        """in_game_thread() is in_logic_thread(), getlog()/get_log_file_path() are get_v1_cloud_log()/get_v1_cloud_log_file_path()."""
        content = re.sub(r'\.in_game_thread\b', ".in_logic_thread", content)
        content = re.sub(r'\.getlog\b', ".get_v1_cloud_log", content)
        return re.sub(r'\.get_log_file_path\b', ".get_v1_cloud_log_file_path", content)

    @change("1.7.14")
    def v1_7_14(content: str) -> str:
        """printobjects() is ls_objects()."""
        return re.sub(r'\.printobjects\b', ".ls_objects", content)

    @change("1.7.17")
    def v1_7_17(content: str) -> str:
        """dump_tracebacks()/log_dumped_tracebacks() are dump_app_state()/log_dumped_app_state()."""
        content = re.sub(r'\.dump_tracebacks\b', ".dump_app_state", content)
        return re.sub(r'\.log_dumped_tracebacks\b', ".log_dumped_app_state", content)


class Api8:
    """Changes made by the api 8 releases (1.7.20 - 1.7.36)."""

    API = 8

    @change("1.7.20", bump=True, needs_type=True)
    def v1_7_20(content: str, plugin_type: str | None) -> str:
        """api 8: ba split into babase/bascenev1/bauiv1 (plus the rest of the api 7 port).

        plugin_type is client or server.
        """
        if plugin_type is None:
            raise ValueError(
                f"porting from api {source_api(content) or 7} needs -t client or -t server"
            )

        content = re.sub(r'^(\s*#\s*ba_meta require api) 7\b', r'\1 8', content, flags=re.MULTILINE)
        content = re.sub(
            r'^(\s*#\s*ba_meta export) game\b', r'\1 bascenev1.GameActivity', content, flags=re.MULTILINE
        )

        content = content.replace("user_agent_string", "legacy_user_agent_string")
        content = re.sub(
            r'^(import\s+[\w]+(\s*,\s*[\w]+)+)',
            lambda match: re.sub(r'\s*,\s*', '\nimport ', match.group()),
            content,
            flags=re.MULTILINE,
        )
        content = content.replace("_ba.", "_babase.")
        content = content.replace("_ba.", "_babase.")
        content = content.replace("ba.", "babase.")
        content = content.replace("import _ba", "import _babase")
        content = content.replace("from ba import", "from babase import")
        content = content.replace("from _ba import", "from _babase import")
        content = re.sub(r'\bimport _ba\b', "import _babase", content)
        content = re.sub(
            r'\bimport ba(\b|\.(\w+))', "import babase\nimport bauiv1\nimport bascenev1", content
        )
        content = content.replace("babase.app.ui", "bauiv1.app.ui_v1")
        content = content.replace(
            "bauiv1.app.ui_v1.set_main_menu_window", "bauiv1.app.ui_v1.set_main_window"
        )

        content = content.replace("babase.app.accounts_v1", "bauiv1.app.classic.accounts")

        ###################################################################################
        # Comment out one of these as per your requirements, depending whether to
        # stay local or if it'll also be needed to transmitted to the clients.

        ## For local:
        if plugin_type == "client":
            content = content.replace("_babase.screenmessage", "bauiv1.screenmessage")
            content = content.replace("babase.screenmessage", "bauiv1.screenmessage")
            content = content.replace("babase.getsound", "bauiv1.getsound")
            content = content.replace("_babase.gettexture", "bauiv1.gettexture")
            content = content.replace("babase.gettexture", "bauiv1.gettexture")
            content = content.replace("babase.getmesh", "bauiv1.getmesh")
            content = content.replace("babase.getcollisionmesh", "bauiv1.getcollisionmesh")
        else:
            ## For transmission:
            content = content.replace("_babase.screenmessage", "bascenev1.broadcastmessage")
            content = content.replace("babase.screenmessage", "bascenev1.broadcastmessage")
            content = content.replace("babase.getsound", "bascenev1.getsound")
            content = content.replace("_babase.gettexture", "bascenev1.gettexture")
            content = content.replace("babase.gettexture", "bascenev1.gettexture")
            content = content.replace("babase.getmesh", "bascenev1.getmesh")
            content = content.replace("babase.getcollisionmesh", "bascenev1.getcollisionmesh")
        ###################################################################################
        content = content.replace("babase.getcollidemesh", "bascenev1.getcollisionmesh")
        content = content.replace("collide_mesh", "collision_mesh")
        content = content.replace("babase.open_url", "bauiv1.open_url")
        content = content.replace("babase.IntSetting", "bascenev1.IntSetting")
        content = content.replace("babase.IntChoiceSetting", "bascenev1.IntChoiceSetting")
        content = content.replace("babase.FloatChoiceSetting", "bascenev1.FloatChoiceSetting")
        content = content.replace("babase.BoolSetting", "bascenev1.BoolSetting")
        content = content.replace("babase.Actor", "bascenev1.Actor")
        content = content.replace("babase.Player", "bascenev1.Player")
        content = content.replace("babase.PlayerDiedMessage", "bascenev1.PlayerDiedMessage")
        content = content.replace("babase.time", "bascenev1.time")
        content = content.replace("babase.Timer", "bascenev1.Timer")
        content = content.replace("babase.newnode", "bascenev1.newnode")
        content = content.replace("babase.Node", "bascenev1.Node")
        content = content.replace("babase.emitfx", "bascenev1.emitfx")
        content = content.replace("babase.animate", "bascenev1.animate")
        content = content.replace("babase.FreeForAllSession", "bascenev1.FreeForAllSession")
        content = content.replace("babase.DualTeamSession", "bascenev1.DualTeamSession")
        content = content.replace("babase.MultiTeamSession", "bascenev1.MultiTeamSession")
        content = content.replace("babase.EndSession", "bascenev1.EndSession")
        content = content.replace("babase.CoopSession", "bascenev1.CoopSession")
        content = content.replace("babase.TeamGameActivity", "bascenev1.TeamGameActivity")
        content = content.replace("babase.Team", "bascenev1.Team")
        content = content.replace("babase.Session", "bascenev1.Session")
        content = content.replace("babase.getsession", "bascenev1.getsession")
        content = content.replace("babase.Material", "bascenev1.Material")
        content = content.replace("babase.WeakCall", "bascenev1.WeakCall")
        content = content.replace("babase.DieMessage", "bascenev1.DieMessage")
        content = content.replace("babase.OutOfBoundsMessage", "bascenev1.OutOfBoundsMessage")
        content = content.replace("babase.DroppedMessage", "bascenev1.DroppedMessage")
        content = content.replace("babase.HitMessage", "bascenev1.HitMessage")
        content = content.replace("babase.ThawMessage", "bascenev1.ThawMessage")
        content = content.replace("babase.NotFoundError", "bascenev1.NotFoundError")
        content = content.replace("babase.getcollision", "bascenev1.getcollision")
        content = content.replace("babase.app.lang", "bascenev1.app.lang")
        content = content.replace("babase.MusicType", "bascenev1.MusicType")
        content = content.replace("babase.getactivity", "bascenev1.getactivity")
        content = content.replace("babase.getactivity", "bascenev1.getactivity")
        content = content.replace("babase.CelebrateMessage", "bascenev1.CelebrateMessage")
        content = content.replace("babase.ScoreConfig", "bascenev1.ScoreConfig")
        content = content.replace("babase.ScoreType", "bascenev1.ScoreType")
        content = content.replace("babase.GameResults", "bascenev1.GameResults")
        content = content.replace("babase.getmaps", "bascenev1.app.classic.getmaps")
        content = content.replace("babase.cameraflash", "bascenev1.cameraflash")
        content = content.replace("babase.getmodel", "bascenev1.getmesh")
        content = content.replace("babase.Map", "bascenev1.Map")
        content = content.replace("babase.DeathType", "bascenev1.DeathType")
        content = content.replace("babase.GameActivity", "bascenev1.GameActivity")
        content = content.replace(
            "_babase.app.stress_test_reset_timer", "_babase.app.classic.stress_test_reset_timer"
        )
        content = content.replace(
            "babase.app.stress_test_reset_timer", "_babase.app.classic.stress_test_reset_timer"
        )
        content = content.replace("babase._map", "bascenev1._map")
        content = content.replace("babase._session.", "bascenev1._session.")
        content = content.replace("babase._activity", "bascenev1._activity")
        content = content.replace(
            "_babase.get_client_public_device_uuid", "_bascenev1.get_client_public_device_uuid"
        )
        content = content.replace("babase.PickedUpMessage", "bascenev1.PickedUpMessage")
        content = content.replace("babase.PowerupMessage", "bascenev1.PowerupMessage")
        content = content.replace("babase.FreezeMessage", "bascenev1.FreezeMessage")
        content = content.replace("with babase.ContextRef(activity):", "with activity.context:")
        content = content.replace("babase.Context", "babase.ContextRef")
        content = content.replace("babase._dualteamsession", "bascenev1._dualteamsession")
        content = content.replace("babase._freeforallsession", "bascenev1._freeforallsession")
        content = content.replace("babase._multiteamsession", "bascenev1._multiteamsession")
        content = content.replace("babase._gameactivity", "bascenev1._gameactivity")
        content = content.replace("babase._powerup", "bascenev1._powerup")
        content = content.replace("babase.Chooser", "bascenev1.Chooser")
        content = content.replace("babase._lobby", "bascenev1._lobby")
        content = content.replace("babase._stats", "bascenev1._stats")
        content = content.replace("babase._team", "bascenev1._team")
        content = content.replace("PlayerType", "PlayerT")
        content = content.replace(
            "babase.app.spaz_appearances", "babase.app.classic.spaz_appearances"
        )
        content = content.replace("babase._coopsession", "bascenev1._coopsession")
        content = content.replace("babase._servermode", "baclassic._servermode")
        content = content.replace("_babase.app.server", "babase.app.classic.server")
        content = content.replace("_babase.chatmessage", "bascenev1.chatmessage")
        content = content.replace("_babase.disconnect_client", "_bascenev1.disconnect_client")
        content = content.replace("_babase.get_game_roster", "bascenev1.get_game_roster")
        content = content.replace(
            "_babase.get_public_party_max_size", "bascenev1.get_public_party_max_size"
        )
        content = content.replace("_babase.new_host_session", "bascenev1.new_host_session")
        content = content.replace("babase._playlist", "bascenev1._playlist")
        content = content.replace("model", "mesh")
        # Real time timers are app timers now, sim/base time is the default
        content = re.sub(
            r'\bbascenev1\.Timer\(((?:[^()]|\([^()]*\))*?)\s*,\s*timetype\s*=\s*babase\.TimeType\.REAL\s*\)',
            r'babase.AppTimer(\1)',
            content,
        )
        content = re.sub(r'\s*,\s*timetype\s*=\s*babase\.TimeType\.(SIM|BASE)\b', '', content)
        content = content.replace(
            "TimeType.REAL", "use `bascenev1.apptimer` in `activity.context` instead"
        )
        content = content.replace(
            "_babase.app.coop_session_args", "babase.app.classic.coop_session_args"
        )
        content = content.replace("_babase.app.campaigns", "babase.app.classic.campaigns")

        content = content.replace("_babase.newactivity", "bascenev1.newactivity")
        content = content.replace("babase.Window", "bauiv1.Window")
        content = content.replace("babase.Widget", "bauiv1.Widget")
        content = content.replace("babase.widget", "bauiv1.widget")
        content = content.replace("babase.containerwidget", "bauiv1.containerwidget")
        content = content.replace("babase.scrollwidget", "bauiv1.scrollwidget")
        content = content.replace("babase.buttonwidget", "bauiv1.buttonwidget")
        content = content.replace("babase.textwidget", "bauiv1.textwidget")
        content = content.replace("babase.checkboxwidget", "bauiv1.checkboxwidget")
        content = content.replace("babase.imagewidget", "bauiv1.imagewidget")
        content = content.replace("babase.uicleanupcheck", "bauiv1.uicleanupcheck")
        content = content.replace(
            "_babase.set_public_party_max_size", "bascenev1.set_public_party_max_size"
        )
        content = content.replace("_bauiv1", "bauiv1")
        content = content.replace("babase.show_damage_count", "bascenev1.show_damage_count")
        content = content.replace("babase._gameutils", "bascenev1._gameutils")
        content = content.replace("babase.StandMessage", "bascenev1.StandMessage")
        content = content.replace("babase.PowerupAcceptMessage", "bascenev1.PowerupAcceptMessage")
        content = content.replace("babase._gameutils", "bascenev1._gameutils")
        content = content.replace("babase.camerashake", "bascenev1.camerashake")
        content = content.replace(
            "babase.app.add_coop_practice_level", "babase.app.classic.add_coop_practice_level"
        )
        content = content.replace("babase._campaign", "bascenev1._campaign")
        content = content.replace("babase.Level", "bascenev1._level.Level")
        content = content.replace(
            "babase.app.cloud.send_message_cb", "bauiv1.app.plus.cloud.send_message_cb"
        )
        content = content.replace("_babase.get_special_widget", "bauiv1.get_special_widget")

        content = content.replace(".app.platform", ".app.classic.platform")
        content = content.replace(".app.subplatform", ".app.classic.subplatform")
        # Converting `ba.playsound(abc)` to `abc.play()` is tricky.
        # Do it manually in case regex substitution fails.# Do it manually in case regex substitution fails. Are you sure!!
        #! FIXME May cause syntax warning on 3.12 with this code below
        content = re.sub(
            r'babase\.playsound\(([^,\n]+)(,\s*([^,\n]+))?(,\s*position=([^,\n]+))?\)',
            r'\1.play(\3\5)',
            content,
            flags=re.MULTILINE,
        )
        # content = re.sub(
        #     r'babase\.playsound\(\s*([^,\n]+),\s*([^,\n]+)\)',
        #     r'\1.play(\2)',
        #     content,
        #     flags=re.MULTILINE
        # )
        # content = re.sub(
        #     r'babase\.playsound\(\s*([^,\n]+),\s*([^,\n]+),\s*position=([^,\n]+)\)',
        #     r'\1.play(\2, position=\3)',
        #     content,
        #     flags=re.MULTILINE
        # )
        # content = re.sub("babase\.playsound\((.+?), (.+?), (.+?)\)", "\\1.play(\\2, \\3)", content)
        # content = re.sub(
        #     r'babase\.playsound\(([^,\n]+),\s*position=([^,\n]+)\)',
        #     r'\1.play(position=\2)',
        #     content
        # )
        # content = re.sub("babase\.playsound\((.*)\)", "\\1.play()", content)

        # Removed in API 8:
        # content = content.replace("babase.internal.set_telnet_access_enabled", "")

        content = content.replace(
            "babase.internal.add_transaction", "bauiv1.app.plus.add_v1_account_transaction"
        )
        content = content.replace(
            "babase.internal.run_transaction", "bauiv1.app.plus.run_v1_account_transaction"
        )
        content = content.replace(
            "_babase.add_transaction", "bauiv1.app.plus.add_v1_account_transaction"
        )
        content = content.replace(
            "_babase.run_transactions", "bauiv1.app.plus.run_v1_account_transactions"
        )
        content = content.replace(
            "babase._store.get_store_layout", "bauiv1.app.classic.store.get_store_layout"
        )
        content = content.replace(
            "babase.internal.get_store_layout", "bauiv1.app.classic.store.get_store_layout"
        )
        content = content.replace("babase.internal.connect_to_party", "bascenev1.connect_to_party")
        content = content.replace(
            "babase.internal.get_default_powerup_distribution",
            "bascenev1._powerup.get_default_powerup_distribution",
        )
        content = content.replace(
            "babase.internal.DEFAULT_REQUEST_TIMEOUT_SECONDS",
            "babase.DEFAULT_REQUEST_TIMEOUT_SECONDS",
        )
        content = content.replace(
            "babase.internal.DEFAULT_TEAM_COLORS", "bascenev1.DEFAULT_TEAM_COLORS"
        )
        content = content.replace(
            "babase.internal.DEFAULT_TEAM_NAMES", "bascenev1.DEFAULT_TEAM_NAMES"
        )
        content = content.replace("babase.internal.JoinActivity", "bascenev1.JoinActivity")
        content = content.replace("babase.internal.LoginAdapter", "babase._login.LoginAdapter")
        content = content.replace(
            "_babase.get_connection_to_host_info", "bascenev1.get_connection_to_host_info"
        )
        content = content.replace(
            "from babase.internal import get_ip_address_type",
            "from babase import get_ip_address_type",
        )
        content = content.replace(
            "babase.internal.PlayerProfilesChangedMessage",
            "bascenev1._messages.PlayerProfilesChangedMessage",
        )
        content = content.replace(
            "babase.internal.ScoreScreenActivity", "bascenev1.ScoreScreenActivity"
        )
        content = content.replace(
            "babase.internal.add_clean_frame_callback", "babase.add_clean_frame_callback"
        )
        content = content.replace(
            "babase.internal.android_get_external_files_dir",
            "babase.android_get_external_files_dir",
        )
        content = content.replace("babase.internal.appname", "babase.appname")
        content = content.replace("babase.internal.appnameupper", "babase.appnameupper")
        content = content.replace(
            "babase.internal.capture_gamepad_input", "bascenev1.capture_gamepad_input"
        )
        content = content.replace(
            "babase.internal.capture_keyboard_input", "bascenev1.capture_keyboard_input"
        )
        content = content.replace("babase.internal.charstr", "babase.charstr")
        content = content.replace("babase.internal.chatmessage", "bascenev1.chatmessage")
        content = content.replace("babase.internal.commit_app_config", "bauiv1.commit_app_config")
        content = content.replace(
            "babase.internal.disconnect_client", "bascenev1.disconnect_client"
        )
        content = content.replace(
            "babase.internal.disconnect_from_host", "bascenev1.disconnect_from_host"
        )
        content = content.replace(
            "babase.internal.do_play_music", "babase.app.classic.music.do_play_music"
        )
        content = content.replace(
            "babase.internal.end_host_scanning", "bascenev1.end_host_scanning"
        )
        content = content.replace("babase.internal.fade_screen", "bauiv1.fade_screen")
        content = content.replace("babase.internal.filter_playlist", "bascenev1.filter_playlist")
        content = content.replace(
            "babase.internal.game_service_has_leaderboard", "_baplus.game_service_has_leaderboard"
        )
        content = content.replace(
            "babase.internal.get_available_purchase_count",
            "bauiv1.app.classic.store.get_available_purchase_count",
        )
        content = content.replace(
            "babase.internal.get_available_sale_time",
            "bauiv1.app.classic.store.get_available_sale_time",
        )
        content = content.replace(
            "babase.internal.get_chat_messages", "bascenev1.get_chat_messages"
        )
        content = content.replace(
            "babase.internal.get_clean_price", "bauiv1.app.classic.store.get_clean_price"
        )
        content = content.replace(
            "babase.internal.get_connection_to_host_info", "bascenev1.get_connection_to_host_info"
        )
        content = content.replace(
            "babase.internal.get_default_free_for_all_playlist",
            "bascenev1._playlist.get_default_free_for_all_playlist",
        )
        content = content.replace(
            "babase.internal.get_default_teams_playlist",
            "bascenev1._playlist.get_default_teams_playlist",
        )
        content = content.replace(
            "babase.internal.get_display_resolution", "babase.get_display_resolution"
        )
        content = content.replace(
            "babase.internal.get_filtered_map_name", "bascenev1._map.get_filtered_map_name"
        )
        content = content.replace(
            "babase.internal.get_foreground_host_session", "bascenev1.get_foreground_host_session"
        )
        content = content.replace("babase.internal.get_game_port", "bascenev1.get_game_port")
        content = content.replace("babase.internal.get_game_roster", "bascenev1.get_game_roster")
        content = content.replace(
            "babase.internal.get_input_device_config", "bauiv1.app.classic.get_input_device_config"
        )
        content = content.replace(
            "babase.internal.get_ip_address_type", "babase.get_ip_address_type"
        )
        content = content.replace(
            "babase.internal.get_local_active_input_devices_count",
            "bascenev1.get_local_active_input_devices_count",
        )
        content = content.replace(
            "babase.internal.get_low_level_config_value", "bauiv1.get_low_level_config_value"
        )
        content = content.replace("babase.internal.get_map_class", "bascenev1.get_map_class")
        content = content.replace(
            "babase.internal.get_map_display_string", "bascenev1.get_map_display_string"
        )
        content = content.replace(
            "babase.internal.get_master_server_address", "bauiv1.app.plus.get_master_server_address"
        )
        content = content.replace(
            "babase.internal.get_max_graphics_quality", "babase.get_max_graphics_quality"
        )
        content = content.replace("babase.internal.get_news_show", "_babase.app.plus.get_news_show")
        content = content.replace(
            "babase.internal.get_next_tip", "bascenev1.app.classic.get_next_tip"
        )
        content = content.replace(
            "babase.internal.get_player_colors", "bascenev1.get_player_colors"
        )
        content = content.replace(
            "babase.internal.get_player_profile_colors", "bascenev1.get_player_profile_colors"
        )
        content = content.replace(
            "babase.internal.get_player_profile_icon", "bascenev1.get_player_profile_icon"
        )
        content = content.replace("babase.internal.get_price", "bauiv1.app.plus.get_price")
        content = content.replace(
            "babase.internal.get_public_party_enabled", "bascenev1.get_public_party_enabled"
        )
        content = content.replace(
            "babase.internal.get_public_party_max_size", "bascenev1.get_public_party_max_size"
        )
        content = content.replace("babase.internal.get_purchased", "bauiv1.app.plus.get_purchased")
        content = content.replace(
            "babase.internal.get_purchases_state", "_baplus.get_purchases_state"
        )
        content = content.replace("babase.internal.get_qrcode_texture", "bauiv1.get_qrcode_texture")
        content = content.replace("babase.internal.get_random_names", "bascenev1.get_random_names")
        content = content.replace(
            "babase.internal.get_remote_app_name", "bascenev1.get_remote_app_name"
        )
        content = content.replace(
            "babase.internal.get_replay_speed_exponent", "bascenev1.get_replay_speed_exponent"
        )
        content = content.replace("babase.internal.get_replays_dir", "babase.get_replays_dir")
        content = content.replace("babase.internal.get_special_widget", "bauiv1.get_special_widget")
        content = content.replace(
            "babase.internal.get_store_item", "babase.app.classic.store.get_store_item"
        )
        content = content.replace(
            "babase.internal.get_store_item_display_size",
            "babase.app.classic.store.get_store_item_display_size",
        )
        content = content.replace(
            "babase.internal.get_store_item_name_translated",
            "babase.app.classic.store.get_store_item_name_translated",
        )
        content = content.replace("babase.internal.get_string_height", "babase.get_string_height")
        content = content.replace("babase.internal.get_string_width", "babase.get_string_width")
        content = content.replace(
            "babase.internal.get_tournament_prize_strings",
            "bascenev1.app.classic.get_tournament_prize_strings",
        )
        content = content.replace(
            "babase.internal.get_trophy_string", "bascenev1.get_trophy_string"
        )
        content = content.replace("babase.internal.get_type_name", "babase.get_type_name")
        content = content.replace(
            "babase.internal.get_ui_input_device", "bascenev1.get_ui_input_device"
        )
        content = content.replace(
            "babase.internal.get_unowned_game_types",
            "babase.app.classic.store.get_unowned_game_types",
        )
        content = content.replace(
            "babase.internal.get_unowned_maps", "babase.app.classic.store.get_unowned_maps"
        )
        content = content.replace(
            "babase.internal.get_v1_account_display_string",
            "bauiv1.app.plus.get_v1_account_display_string",
        )
        content = content.replace(
            "babase.internal.get_v1_account_misc_read_val",
            "bauiv1.app.plus.get_v1_account_misc_read_val",
        )
        content = content.replace(
            "babase.internal.get_v1_account_misc_read_val_2",
            "bauiv1.app.plus.get_v1_account_misc_read_val_2",
        )
        content = content.replace(
            "babase.internal.get_v1_account_misc_val", "bauiv1.app.plus.get_v1_account_misc_val"
        )
        content = content.replace(
            "babase.internal.get_v1_account_name", "bauiv1.app.plus.get_v1_account_name"
        )
        content = content.replace(
            "babase.internal.get_v1_account_state", "bauiv1.app.plus.get_v1_account_state"
        )
        content = content.replace(
            "babase.internal.get_v1_account_state_num", "bauiv1.app.plus.get_v1_account_state_num"
        )
        content = content.replace(
            "babase.internal.get_v1_account_ticket_count",
            "bauiv1.app.plus.get_v1_account_ticket_count",
        )
        content = content.replace(
            "babase.internal.get_v1_account_type", "bauiv1.app.plus.get_v1_account_type"
        )
        content = content.replace("babase.internal.get_v2_fleet", "_baplus.get_v2_fleet")
        content = content.replace("babase.internal.getcampaign", "bauiv1.app.classic.getcampaign")
        content = content.replace("babase.internal.getclass", "babase.getclass")
        content = content.replace("babase.internal.getinputdevice", "bascenev1.getinputdevice")
        content = content.replace("babase.internal.has_gamma_control", "babase.has_gamma_control")
        content = content.replace("babase.internal.has_video_ads", "bauiv1.has_video_ads")
        content = content.replace(
            "babase.internal.have_incentivized_ad", "bauiv1.have_incentivized_ad"
        )
        content = content.replace("babase.internal.have_permission", "babase.have_permission")
        content = content.replace(
            "babase.internal.have_touchscreen_input", "bascenev1.have_touchscreen_input"
        )
        content = content.replace("babase.internal.host_scan_cycle", "bascenev1.host_scan_cycle")
        content = content.replace(
            "babase.internal.in_game_purchase", "bui.app.plus.in_game_purchase"
        )
        content = content.replace(
            "babase.internal.increment_analytics_count", "babase.increment_analytics_count"
        )
        content = content.replace("babase.internal.is_blessed", "bui.app.plus.is_blessed")
        content = content.replace("babase.UISubsystem", "bauiv1.UIV1AppSubsystem")
        content = content.replace(
            "babase.internal.is_browser_likely_available", "bauiv1.is_browser_likely_available"
        )
        content = content.replace("babase.internal.is_in_replay", "bascenev1.is_in_replay")
        content = content.replace(
            "babase.internal.is_party_icon_visible", "bauiv1.is_party_icon_visible"
        )
        content = content.replace(
            "babase.internal.is_running_on_fire_tv", "babase.is_running_on_fire_tv"
        )
        content = content.replace("babase.internal.is_xcode_build", "babase.is_xcode_build")
        content = content.replace("babase.internal.json_prep", "babase.json_prep")
        content = content.replace("babase.internal.lock_all_input", "babase.lock_all_input")
        content = content.replace(
            "babase.internal.mark_config_dirty", "_babase.app.plus.mark_config_dirty"
        )
        content = content.replace("babase.internal.new_host_session", "bascenev1.new_host_session")
        content = content.replace(
            "babase.internal.new_replay_session", "bascenev1.new_replay_session"
        )
        content = content.replace(
            "babase.internal.open_file_externally", "bauiv1.open_file_externally"
        )
        content = content.replace(
            "babase.internal.power_ranking_query", "_baplus.power_ranking_query"
        )
        content = content.replace(
            "babase.internal.preload_map_preview_media",
            "bauiv1.app.classic.preload_map_preview_media",
        )
        content = content.replace("babase.internal.purchase", "_baplus.purchase")
        content = content.replace("babase.internal.register_map", "bascenev1.register_map")
        content = content.replace(
            "babase.internal.release_gamepad_input", "bascenev1.release_gamepad_input"
        )
        content = content.replace(
            "babase.internal.release_keyboard_input", "bascenev1.release_keyboard_input"
        )
        content = content.replace(
            "babase.internal.report_achievement", "babase.app.plus.report_achievement"
        )
        content = content.replace("babase.internal.request_permission", "babase.request_permission")
        content = content.replace(
            "babase.internal.reset_achievements", "_baplus.reset_achievements"
        )
        content = content.replace(
            "babase.internal.reset_random_player_names", "bascenev1.reset_random_player_names"
        )
        content = content.replace("babase.internal.restore_purchases", "_baplus.restore_purchases")
        content = content.replace(
            "babase.internal.run_cpu_benchmark", "baclassic._benchmark.run_cpu_benchmark"
        )
        content = content.replace(
            "babase.internal.run_gpu_benchmark", "baclassic._benchmark.run_gpu_benchmark"
        )
        content = content.replace(
            "babase.internal.run_media_reload_benchmark",
            "baclassic._benchmark.run_media_reload_benchmark",
        )
        content = content.replace(
            "babase.internal.run_stress_test", "babase.app.classic.run_stress_test"
        )
        content = content.replace(
            "babase.internal.set_authenticate_clients", "bascenev1.set_authenticate_clients"
        )
        content = content.replace(
            "babase.internal.set_debug_speed_exponent", "bascenev1.set_debug_speed_exponent"
        )
        content = content.replace(
            "babase.internal.set_low_level_config_value", "babase.set_low_level_config_value"
        )
        content = content.replace(
            "babase.internal.set_party_icon_always_visible", "bauiv1.set_party_icon_always_visible"
        )
        content = content.replace(
            "babase.internal.set_party_window_open", "bauiv1.set_party_window_open"
        )
        content = content.replace(
            "babase.internal.set_public_party_enabled", "bascenev1.set_public_party_enabled"
        )
        content = content.replace(
            "babase.internal.set_public_party_max_size", "bascenev1.set_public_party_max_size"
        )
        content = content.replace(
            "babase.internal.set_public_party_name", "bascenev1.set_public_party_name"
        )
        content = content.replace(
            "babase.internal.set_public_party_queue_enabled",
            "bascenev1.set_public_party_queue_enabled",
        )
        content = content.replace(
            "babase.internal.set_replay_speed_exponent", "bascenev1.set_replay_speed_exponent"
        )
        content = content.replace(
            "babase.internal.set_touchscreen_editing", "bascenev1.set_touchscreen_editing"
        )
        content = content.replace(
            "babase.internal.set_ui_input_device", "babase.set_ui_input_device"
        )
        content = content.replace(
            "babase.internal.should_submit_debug_info", "babase._apputils.should_submit_debug_info"
        )
        content = content.replace(
            "babase.internal.show_online_score_ui", "bauiv1.show_online_score_ui"
        )
        content = content.replace("babase.internal.sign_in_v1", "babase.app.plus.sign_in_v1")
        content = content.replace("babase.internal.sign_out_v1", "babase.app.plus.sign_out_v1")
        content = content.replace("babase.internal.submit_score", "bascenev1.app.plus.submit_score")
        content = content.replace("babase.internal.tournament_query", "_baplus.tournament_query")
        content = content.replace("babase.internal.unlock_all_input", "babase.unlock_all_input")
        content = content.replace("babase.internal.value_test", "bauiv1.app.classic.value_test")
        content = content.replace("babase.internal.workspaces_in_use", "babase.workspaces_in_use")
        content = content.replace(
            "babase.internal.dump_app_state", "babase._apputils.dump_app_state"
        )
        content = content.replace("babase.internal.show_app_invite", "_bauiv1.show_app_invite")
        content = content.replace(
            "babase.internal.master_server_get", "babase.app.classic.master_server_v1_get"
        )
        content = content.replace(
            "babase.internal.master_server_post", "babase.app.classic.master_server_v1_post"
        )
        content = content.replace(
            "babase.internal.log_dumped_app_state", "babase._apputils.log_dumped_app_state"
        )
        content = content.replace(
            "babase.internal.have_outstanding_transactions",
            "bauiv1.app.plus.have_outstanding_v1_account_transactions",
        )
        content = content.replace(
            "babase.internal.get_public_login_id", "bauiv1.app.plus.get_v1_account_public_login_id"
        )
        content = content.replace(
            "babase.internal.get_input_map_hash", "bauiv1.app.classic.get_input_device_map_hash"
        )
        content = content.replace(
            "babase.internal.get_device_value", "bauiv1.app.classic.get_input_device_mapped_value"
        )
        content = content.replace("babase.internal.", "bascenev1")
        # content = content.replace("babase.internal", "")

        content = content.replace("babase._generated", "babase._mgen")
        content = content.replace("_babase.disconnect_from_host", "bascenev1.disconnect_from_host")
        content = content.replace("babase.disconnect_from_host", "bascenev1.disconnect_from_host")
        content = content.replace("_babase.connect_to_party", "bascenev1.connect_to_party")
        content = content.replace("babase.connect_to_party", "bascenev1.connect_to_party")
        content = content.replace("babase.set_party_window_open", "bauiv1.set_party_window_open")
        content = content.replace("babase.set_party_window_open", "bauiv1.set_party_window_open")
        content = content.replace("babase.getcollidemesh", "bascenev1.getcollisionmesh")
        content = content.replace("collide_mesh", "collision_mesh")
        content = content.replace("babase.FloatSetting", "bascenev1.FloatSetting")
        content = content.replace("babase.playsound", "bascenev1.playsound")
        content = content.replace(
            "bascenev1.time(timeformat=babase.TimeFormat.MILLISECONDS)", "bascenev1.time() * 1000"
        )

        # get_connection_to_host_info() still returns a dict, its _2 variant returns HostInfo | None
        # which would break the dict style code written against it, so leave it alone.

        content = content.replace("babase._store", "bauiv1.app.classic.store")
        content = content.replace("bastd.ui", "bauiv1lib")
        content = content.replace("bastd", "bascenev1lib")
        content = content.replace("timetype=", "")
        content = content.replace("babase.columnwidget", "bauiv1.columnwidget")
        content = content.replace("_babase.get_game_port", "bascenev1.get_game_port")
        content = content.replace("_babase.get_chat_messages", "bascenev1.get_chat_messages")
        content = content.replace(
            "_babase.get_foreground_host_session", "bascenev1.get_foreground_host_session"
        )
        content = content.replace(
            "_babase.get_foreground_host_activity", "bascenev1.get_foreground_host_activity"
        )
        content = content.replace(
            "bascenev1.SessionPlayerNotFoundError", "babase.SessionPlayerNotFoundError"
        )
        content = content.replace("bascenev1", "bs")
        content = content.replace("bauiv1", "bui")
        content = content.replace("import bs", "import bascenev1 as bs")
        content = content.replace("import bui", "import bauiv1 as bui")
        content = content.replace("bslib", "bascenev1lib")
        content = content.replace("builib", "bauiv1lib")
        content = content.replace("from bs.", "from bascenev1.")
        content = content.replace("from bui.", "from bauiv1.")
        content = content.replace("import bascenev1 as bascenev1lib", "import bascenev1lib")
        content = content.replace("import bauiv1 as bauiv1lib", "import bauiv1lib")
        content = re.sub(
            r'^(\s*#\s*ba_meta export) bs\.GameActivity\b',
            r'\1 bascenev1.GameActivity',
            content,
            flags=re.MULTILINE,
        )
        content = content.replace("_bs", "bs")

        return content

    @change("1.7.21")
    def v1_7_21(content: str) -> str:
        """PotentialPlugin is PluginSpec, app.plugins.potential_plugins is plugin_specs."""
        content = re.sub(r'\bPotentialPlugin\b', "PluginSpec", content)
        return re.sub(r'\.plugins\.potential_plugins\b', ".plugins.plugin_specs", content)

    @change(
        "1.7.21",
        private=True,
        removed=[
            "_babase.apply_config",
            "babase._plugin.PotentialPlugin",
        ],
    )
    def v1_7_21_private(content: str) -> str:
        """private: 2 removed (reported after porting)."""
        return content

    @change(
        "1.7.24",
        private=True,
        removed=[
            "_babase.android_show_wifi_settings",
            "babase._apputils.on_too_many_file_descriptors",
            "babase._env.setup_env_for_app_run",
            "babase._hooks.gear_vr_controller_warning",
            "babase._hooks.no_game_circle_message",
        ],
    )
    def v1_7_24_private(content: str) -> str:
        """private: 5 removed (reported after porting)."""
        return content

    @change(
        "1.7.26",
        private=True,
        removed=[
            "babase._env.on_app_launching",
            "babase._hooks.on_app_bootstrapping_complete",
            "babase._hooks.on_app_pause",
            "babase._hooks.on_app_resume",
            "babase._hooks.read_config",
            "babase._hooks.shutdown",
        ],
    )
    def v1_7_26_private(content: str) -> str:
        """private: 6 removed (reported after porting)."""
        return content

    # app attribute -> where 1.7.27 moved it
    APP_ENV_1_7_27 = {
        "build_number": "env.build_number",
        "device_name": "env.device_name",
        "config_file_path": "env.config_file_path",
        "version": "env.version",
        "debug_build": "env.debug",
        "test_build": "env.test",
        "data_directory": "env.data_directory",
        "python_directory_user": "env.python_directory_user",
        "python_directory_app": "env.python_directory_app",
        "python_directory_app_site": "env.python_directory_app_site",
        "api_version": "env.api_version",
        "on_tv": "env.tv",
        "vr_mode": "env.vr",
        "toolbar_test": "ui_v1.use_toolbars",
        "arcade_mode": "env.arcade",
        "headless_mode": "env.headless",
        "demo_mode": "env.demo",
    }

    @change("1.7.27")
    def v1_7_27(content: str) -> str:
        """app.version, app.build_number, app.debug_build, ... moved to app.env."""
        names = "|".join(Api8.APP_ENV_1_7_27)
        content = re.sub(
            rf'\.app\.({names})\b', lambda m: f".app.{Api8.APP_ENV_1_7_27[m.group(1)]}", content
        )
        if re.search(r'\.app\.protocol_version\b', content):
            content, scene = module_alias(content, "bascenev1")
            content = re.sub(r'[\w.]*\.app\.protocol_version\b', f"{scene}.protocol_version()", content)
        return content

    @change(
        "1.7.27",
        private=True,
        removed=[
            "babase._meta.CURRENT_API_VERSION",
        ],
    )
    def v1_7_27_private(content: str) -> str:
        """private: 1 removed (reported after porting)."""
        return content

    @change(
        "1.7.28",
        private=True,
        removed=[
            "_babase.display_log",
            "_babase.empty_app_mode_activate",
            "_babase.empty_app_mode_deactivate",
            "_babase.has_gamma_control",
            "_babase.mac_music_app_get_library_source",
            "_bascenev1.app_mode_activate",
            "_bascenev1.app_mode_deactivate",
            "_bauiv1.focus_window",
            "_bauiv1.show_app_invite",
            "babase._general.json_prep",
            "babase._hooks.connecting_to_party_message",
        ],
    )
    def v1_7_28_private(content: str) -> str:
        """private: _babase.set_stress_testing -> _baclassic.set_stress_testing."""
        content = move_name(content, "_babase.set_stress_testing", "_baclassic.set_stress_testing")
        return content

    @change("1.7.31")
    def v1_7_31(content: str) -> str:
        """babase.Keyboard moved to bauiv1.Keyboard."""
        return move_name(content, "babase.Keyboard", "bauiv1.Keyboard")

    @change(
        "1.7.31",
        private=True,
        removed=[
            "_babase.is_running_on_fire_tv",
            "_babase.is_running_on_ouya",
            "_bauiv1.can_show_ad",
            "_bauiv1.console_print",
            "_bauiv1.has_video_ads",
            "_bauiv1.have_incentivized_ad",
            "_bauiv1.show_ad",
            "_bauiv1.show_ad_2",
            "_bauiv1.show_online_score_ui",
        ],
    )
    def v1_7_31_private(content: str) -> str:
        """private: _bauiv1.open_file_externally -> _babase.open_file_externally, babase._keyboard.Keyboard -> bauiv1._keyboard.Keyboard."""
        content = move_name(content, "_bauiv1.open_file_externally", "_babase.open_file_externally")
        content = move_name(content, "babase._keyboard.Keyboard", "bauiv1._keyboard.Keyboard")
        return content

    @change(
        "1.7.32",
        private=True,
        removed=[
            "bascenev1._session.REJOIN_COOLDOWN",
        ],
    )
    def v1_7_32_private(content: str) -> str:
        """private: 1 removed (reported after porting)."""
        return content

    @change(
        "1.7.33",
        private=True,
        removed=[
            "baclassic._benchmark.start_stress_test",
        ],
    )
    def v1_7_33_private(content: str) -> str:
        """private: babase._cloud.CloudSubsystem -> baplus._cloud.CloudSubsystem, babase._cloud.DEBUG_LOG -> baplus._cloud.DEBUG_LOG, babase._cloud.cloud_console_exec -> baplus._cloud.cloud_console_exec."""
        content = move_name(content, "babase._cloud.CloudSubsystem", "baplus._cloud.CloudSubsystem")
        content = move_name(content, "babase._cloud.DEBUG_LOG", "baplus._cloud.DEBUG_LOG")
        content = move_name(content, "babase._cloud.cloud_console_exec", "baplus._cloud.cloud_console_exec")
        return content

    @change("1.7.35")
    def v1_7_35(content: str) -> str:
        """app.env.version and app.env.build_number are engine_version and engine_build_number."""
        content = re.sub(r'\.env\.version\b', ".env.engine_version", content)
        return re.sub(r'\.env\.build_number\b', ".env.engine_build_number", content)

    @change(
        "1.7.35",
        private=True,
        removed=[
            "babase._app.Tsub",
        ],
    )
    def v1_7_35_private(content: str) -> str:
        """private: _bauiv1.open_url -> _babase.open_url."""
        content = move_name(content, "_bauiv1.open_url", "_babase.open_url")
        return content


class Api9:
    """Changes made by the api 9 releases (1.7.37 onwards)."""

    API = 9

    @change("1.7.37", bump=True)
    def v1_7_37_api(content: str) -> str:
        """`# ba_meta require api 9`."""
        return re.sub(r'^(\s*#\s*ba_meta require api) 8\b', r'\1 9', content, flags=re.MULTILINE)

    @change("1.7.37")
    def v1_7_37(content: str) -> str:
        """efro.log, tpartial, enum_by_value, party icon, ui_scale, UIV1Subsystem, threadpool changes."""
        content = re.sub(r'\befro\.log\b', "efro.logging", content)
        if re.search(r'^\s*from efro\.call import .*\btpartial\b', content, flags=re.MULTILINE):
            content = drop_import_name(content, "efro.call", "tpartial")
            content = add_import(content, "from functools import partial as tpartial")
        if "efro.call.tpartial" in content:
            content = content.replace("efro.call.tpartial", "functools.partial")
            content = ensure_import(content, "functools")
        # enum_by_value(MyEnum, foo) is just MyEnum(foo)
        content = drop_import_name(content, "efro.util", "enum_by_value")
        content = re.sub(r'\b(?:[\w.]+\.)?enum_by_value\(\s*([\w.]+)\s*,\s*', r'\1(', content)
        # the party icon is always visible now
        content = re.sub(r'\b\w+\.is_party_icon_visible\([^)]*\)', "True", content)
        content = drop_removed_function(content, "bauiv1", "set_party_icon_always_visible")
        content = re.sub(r'\b(\w+)\.env\(\)\[["\']ui_scale["\']\]', r'\1.get_ui_scale()', content)
        content = re.sub(r'\bUIV1Subsystem\b', "UIV1AppSubsystem", content)
        return re.sub(r'\.threadpool_submit_no_wait\(', ".threadpool.submit_no_wait(", content)

    @change(
        "1.7.37",
        private=True,
        removed=[
            "_babase.can_display_full_unicode",
            "_babase.empty_app_mode_handle_intent_default",
            "_babase.empty_app_mode_handle_intent_exec",
            "_babase.on_empty_app_mode_activate",
            "_babase.on_empty_app_mode_deactivate",
            "_bascenev1.handle_app_intent_default",
            "_bascenev1.handle_app_intent_exec",
            "_bascenev1.on_app_mode_activate",
            "_bascenev1.on_app_mode_deactivate",
            "_bauiv1.back_press",
            "_bauiv1.is_party_icon_visible",
            "_bauiv1.set_party_icon_always_visible",
            "_bauiv1.toolbar_test",
            "babase._accountv2.DEBUG_LOG",
            "babase._appconfig.read_app_config",
            "babase._login.DEBUG_LOG",
            "baclassic._appdelegate.AppDelegate",
            "baclassic._benchmark.run_gpu_benchmark",
            "baclassic._subsystem.ClassicSubsystem",
            "baplus._cloud.DEBUG_LOG",
            "baplus._subsystem.PlusSubsystem",
            "bascenev1._appmode.SceneV1AppMode",
            "bauiv1._hooks.back_button_press",
            "bauiv1._hooks.coin_icon_press",
            "bauiv1._hooks.friends_button_press",
            "bauiv1._hooks.level_icon_press",
            "bauiv1._hooks.party_icon_activate",
            "bauiv1._hooks.ticket_icon_press",
            "bauiv1._hooks.trophy_icon_press",
            "bauiv1._subsystem.UIV1Subsystem",
            "bauiv1._uitypes.UIController",
            "bauiv1._uitypes.UIEntry",
            "bauiv1._uitypes.UILocation",
            "bauiv1._uitypes.UILocationWindow",
        ],
        restored=[
            "_babase.empty_app_mode_activate",
            "_babase.empty_app_mode_deactivate",
        ],
    )
    def v1_7_37_private(content: str) -> str:
        """private: _babase.lifecyclelog -> babase._logging.lifecyclelog, babase._devconsole.DevConsoleTabPython -> babase._devconsoletabs.DevConsoleTabPython, babase._devconsole.DevConsoleTabTest -> babase._devconsoletabs.DevConsoleTabTest."""
        content = move_name(content, "_babase.lifecyclelog", "babase._logging.lifecyclelog")
        content = move_name(content, "babase._devconsole.DevConsoleTabPython", "babase._devconsoletabs.DevConsoleTabPython")
        content = move_name(content, "babase._devconsole.DevConsoleTabTest", "babase._devconsoletabs.DevConsoleTabTest")
        return content

    @change("1.7.39")
    def v1_7_39(content: str) -> str:
        """App.State is AppState, print_exception()/print_error() are gone (use logging)."""
        content = re.sub(r'\b(\w+)\.App\.State\b', r'\1.AppState', content)
        aliases = "|".join(re.escape(a) for a in ba_aliases(content)) or "babase"
        # `from babase._error import print_exception` also allows bare calls, but only
        # for the names imported (a plugin may define its own print_error)
        imported = set()
        for module, names in re.findall(
            r'^\s*from (_?ba[\w.]*) import (.*)$', content, flags=re.MULTILINE
        ):
            for kind in ("exception", "error"):
                if re.search(rf'\bprint_{kind}\b', names):
                    imported.add(kind)
                    content = drop_import_name(content, module, f"print_{kind}")
        used = False
        for kind in ("exception", "error"):
            prefix = rf'\b(?:{aliases})\.'
            if kind in imported:
                prefix = rf'(?:{prefix}|(?<![\w.])(?<!def ))'
            if re.search(rf'{prefix}print_{kind}\(', content):
                if kind == "exception":
                    content = re.sub(rf'{prefix}print_exception\(\)', "logging.exception('Error')", content)
                content = re.sub(rf'{prefix}print_{kind}\(', f"logging.{kind}(", content)
                used = True
        if used:
            content = ensure_import(content, "logging")
        return content

    @change(
        "1.7.39",
        private=True,
        removed=[
            "_babase.have_chars",
            "babase._apputils.AppHealthMonitor",
            "babase._error.print_error",
            "babase._error.print_exception",
            "babase._general.utf8_all",
        ],
    )
    def v1_7_39_private(content: str) -> str:
        """private: 5 removed (reported after porting)."""
        return content

    @change("1.7.40")
    def v1_7_40(content: str) -> str:
        """bascenev1lib.actor.playerspaz no longer has PlayerT (PEP 695 generics)."""
        if re.search(
            r'^from bascenev1lib\.actor\.playerspaz import .*\bPlayerT\b',
            content,
            flags=re.MULTILINE,
        ):
            content = drop_import_name(content, "bascenev1lib.actor.playerspaz", "PlayerT")
            content = ensure_import(content, "typing")
            content = re.sub(
                r'^(from bascenev1lib\.actor\.playerspaz import .*)$',
                r"\1\nPlayerT = typing.TypeVar('PlayerT')",
                content,
                count=1,
                flags=re.MULTILINE,
            )
        return content

    @change(
        "1.7.40",
        private=True,
        removed=[
            "babase._app.T",
            "babase._appcomponent.T",
            "babase._devconsoletabs.T",
            "babase._general.ExistableT",
            "babase._general.T",
            "babase._meta.T",
            "bascenev1._activity.PlayerT",
            "bascenev1._activity.TeamT",
            "bascenev1._actor.ActorT",
            "bascenev1._coopgame.PlayerT",
            "bascenev1._coopgame.TeamT",
            "bascenev1._dependency.T",
            "bascenev1._gameactivity.PlayerT",
            "bascenev1._gameactivity.TeamT",
            "bascenev1._messages.PlayerT",
            "bascenev1._player.PlayerT",
            "bascenev1._player.TeamT",
            "bascenev1._team.PlayerT",
            "bascenev1._teamgame.PlayerT",
            "bascenev1._teamgame.TeamT",
        ],
    )
    def v1_7_40_private(content: str) -> str:
        """private: 20 removed (reported after porting)."""
        return content

    @change("1.7.42")
    def v1_7_42(content: str) -> str:
        """app.env.debug is app.env.debug_build."""
        return re.sub(r'\.env\.debug\b', ".env.debug_build", content)

    @change(
        "1.7.42",
        private=True,
        removed=[
            "_babase.get_volatile_data_directory",
        ],
    )
    def v1_7_42_private(content: str) -> str:
        """private: 1 removed (reported after porting)."""
        return content

    @change("1.7.44")
    def v1_7_44(content: str) -> str:
        """'# ba_meta export plugin' is deprecated, use '# ba_meta export babase.Plugin'."""
        return re.sub(
            r'^(\s*#\s*ba_meta export) plugin\b', r'\1 babase.Plugin', content, flags=re.MULTILINE
        )

    @change(
        "1.7.44",
        private=True,
        removed=[
            "_babase.commit_config",
            "_babase.do_apply_app_config",
            "_babase.invoke_main_menu",
            "_babase.push_back_press",
            "_babase.set_ui_input_device",
            "_bascenev1.capture_gamepad_input",
            "_bascenev1.get_configurable_game_pads",
            "_bascenev1.get_ui_input_device",
            "_bascenev1.release_gamepad_input",
            "babase._accountv2.logger",
            "babase._apputils.garbage_collect",
            "babase._apputils.garbage_collect_session_end",
            "babase._env._pycache_cleanup_old_builds",
            "babase._env._pycache_gen_pycs",
            "babase._login.logger",
            "bauiv1._hooks.device_menu_press",
        ],
    )
    def v1_7_44_private(content: str) -> str:
        """private: baclassic._ads.AdsSubsystem -> baplus._ads.AdsSubsystem."""
        content = move_name(content, "baclassic._ads.AdsSubsystem", "baplus._ads.AdsSubsystem")
        return content

    @change("1.7.45")
    def v1_7_45(content: str) -> str:
        """get_v1_account_ticket_count() and get_v1_account_product_purchased() are app.classic.tickets/purchases."""
        if "get_v1_account_ticket_count()" in content:
            content, app = module_alias(content, "babase", "bauiv1", "bascenev1")
            content = re.sub(
                r'[\w.]*\.get_v1_account_ticket_count\(\)', f"{app}.app.classic.tickets", content
            )
        if "get_v1_account_product_purchased(" in content:
            content, app = module_alias(content, "babase", "bauiv1", "bascenev1")
            content = re.sub(
                r'[\w.]*\.get_v1_account_product_purchased\(([^()]*)\)',
                rf'(\1 in {app}.app.classic.purchases)',
                content,
            )
        return content

    @change(
        "1.7.45",
        private=True,
        removed=[
            "_babase.set_ui_account_state",
            "_baclassic.get_account_display_state",
            "_baclassic.set_account_display_state",
            "babase._discord.Discord",
            "babase._hooks.uuid_str",
            "babase._logging.accountv2log",
        ],
    )
    def v1_7_45_private(content: str) -> str:
        """private: 6 removed (reported after porting)."""
        return content

    @change(
        "1.7.48",
        private=True,
    )
    def v1_7_48_private(content: str) -> str:
        """private: babase._net.DEFAULT_REQUEST_TIMEOUT_SECONDS -> babase._env.DEFAULT_REQUEST_TIMEOUT_SECONDS."""
        content = move_name(content, "babase._net.DEFAULT_REQUEST_TIMEOUT_SECONDS", "babase._env.DEFAULT_REQUEST_TIMEOUT_SECONDS")
        return content

    @change("1.7.51")
    def v1_7_51(content: str) -> str:
        """uicleanupcheck() is app.ui_v1.add_ui_cleanup_check(), main_window_replace() takes a callable."""
        content = re.sub(
            r'\b(\w+)\.uicleanupcheck\(', r'\1.app.ui_v1.add_ui_cleanup_check(', content
        )
        return re.sub(
            r'\.main_window_replace\(\s*(?!lambda\b)(?=[A-Za-z_][\w.]*\s*\()',
            ".main_window_replace(lambda: ",
            content,
        )

    @change(
        "1.7.51",
        private=True,
        removed=[
            "_bauiv1.is_available",
            "bauiv1._uitypes.UICleanupCheck",
            "bauiv1._uitypes.ui_upkeep",
        ],
    )
    def v1_7_51_private(content: str) -> str:
        """private: bauiv1._uitypes.BasicMainWindowState -> bauiv1._window.BasicMainWindowState, bauiv1._uitypes.DEBUG_UI_CLEANUP_CHECKS -> bauiv1._appsubsystem.DEBUG_UI_CLEANUP_CHECKS, bauiv1._uitypes.MainWindow -> bauiv1._window.MainWindow, bauiv1._uitypes.MainWindowAutoRecreateSuppress -> bauiv1._window.MainWindowAutoRecreateSuppress, bauiv1._uitypes.MainWindowState -> bauiv1._window.MainWindowState, bauiv1._uitypes.Window -> bauiv1._window.Window."""
        content = move_name(content, "bauiv1._uitypes.BasicMainWindowState", "bauiv1._window.BasicMainWindowState")
        content = move_name(content, "bauiv1._uitypes.DEBUG_UI_CLEANUP_CHECKS", "bauiv1._appsubsystem.DEBUG_UI_CLEANUP_CHECKS")
        content = move_name(content, "bauiv1._uitypes.MainWindow", "bauiv1._window.MainWindow")
        content = move_name(content, "bauiv1._uitypes.MainWindowAutoRecreateSuppress", "bauiv1._window.MainWindowAutoRecreateSuppress")
        content = move_name(content, "bauiv1._uitypes.MainWindowState", "bauiv1._window.MainWindowState")
        content = move_name(content, "bauiv1._uitypes.Window", "bauiv1._window.Window")
        return content

    @change("1.7.53")
    def v1_7_53(content: str) -> str:
        """Call/WeakCall will act like CallStrict, use CallPartial/WeakCallPartial; set_party_window_open() is gone."""
        core = module_aliases(content, "babase", "bauiv1", "bascenev1")
        if core:
            content = re.sub(rf'\b({core})\.(WeakCall|Call)\b', r'\1.\2Partial', content)
        return drop_removed_function(content, "bauiv1", "set_party_window_open")

    @change(
        "1.7.53",
        private=True,
        removed=[
            "_bauiv1.set_party_window_open",
            "babase._general._Call",
            "babase._general._WeakCall",
        ],
    )
    def v1_7_53_private(content: str) -> str:
        """private: 3 removed (reported after porting)."""
        return content

    @change("1.7.61")
    def v1_7_61(content: str) -> str:
        """get_v1_account_id() is get_account_id(), IOAttrs(float_times=True) is time_format='float'."""
        content = content.replace(".get_v1_account_id(", ".get_account_id(")
        return re.sub(r'\bfloat_times\s*=\s*True\b', "time_format='float'", content)

    @change(
        "1.7.61",
        private=True,
        removed=[
            "babase._asyncio._asyncio_event_loop",
            "babase._asyncio._asyncio_timer",
            "bascenev1._session._max_players_override",
        ],
    )
    def v1_7_61_private(content: str) -> str:
        """private: 3 removed (reported after porting)."""
        return content

    @change(
        "1.7.62",
        private=True,
        removed=[
            "_babase.discord_add_button",
            "_babase.discord_is_ready",
            "_babase.discord_join_lobby",
            "_babase.discord_leave_lobby",
            "_babase.discord_richpresence",
            "_babase.discord_send_lobby_message",
            "_babase.discord_set_party",
            "_babase.discord_shutdown",
            "_babase.discord_start",
            "babase._discord.DiscordSubsystem",
            "babase._discord.ENABLE_DISCORD",
        ],
    )
    def v1_7_62_private(content: str) -> str:
        """private: 11 removed (reported after porting)."""
        return content

    @change("1.7.63")
    def v1_7_63(content: str) -> str:
        """ba*._mgen.enums is gone, the enums are exported by the modules themselves."""
        return re.sub(r'\b(_?ba\w*)\._mgen\.enums\b', r'\1', content)

    @change("1.8.0")
    def v1_8_0(content: str) -> str:
        """charstr(SpecialChar.X) is SpecialChar.X.value, textwidget on_return_press_call is on_submit_call."""
        content = re.sub(
            r'\b(?:\w+\.)?charstr\(\s*([\w.]*SpecialChar\.\w+)\s*\)', r'\1.value', content
        )
        content = re.sub(
            r'\b(?:\w+\.)?charstr\(\s*(getattr\(\s*[\w.]*SpecialChar\s*,(?:[^()]|\([^()]*\))*\))\s*\)',
            r'\1.value',
            content,
        )
        if not re.search(r'\bcharstr\(', content):
            for module in ("babase", "bauiv1", "bascenev1"):
                content = drop_import_name(content, module, "charstr")
        content = re.sub(r'\bon_return_press_call\s*=', "on_submit_call=", content)
        return re.sub(r'\binvoke_return_press\s*=', "invoke_submit=", content)

    @change(
        "1.8.0",
        private=True,
        removed=[
            "_babase.get_low_level_config_value",
            "_babase.get_v1_cloud_log",
            "_babase.get_v1_cloud_log_file_path",
            "_babase.is_log_full",
            "_babase.mark_log_sent",
            "_babase.set_internal_language_keys",
            "_babase.set_low_level_config_value",
            "_babase.v1_cloud_log",
            "_bascenev1.get_package_collision_mesh",
            "_bascenev1.get_package_data",
            "_bascenev1.get_package_mesh",
            "_bascenev1.get_package_sound",
            "_bascenev1.get_package_texture",
            "babase._appconfig._g_pending_apply",
            "babase._apputils._handle_leftover_v1_cloud_log_file",
            "babase._apputils.get_remote_app_name",
            "babase._apputils.handle_leftover_v1_cloud_log_file",
            "babase._apputils.handle_v1_cloud_log",
            "babase._assetmanager.AssetGather",
            "babase._assetmanager.AssetManager",
            "babase._assetmanager.FileValue",
            "babase._assetmanager.State",
            "babase._assetmanager.fetch_url",
            "babase._asyncio.DEBUG_TIMING",
            "babase._asyncio._g_asyncio_timer",
            "babase._hooks.connection_failed_message",
            "babase._hooks.play_gong_sound",
            "babase._hooks.rejecting_invite_already_in_party_message",
            "babase._language.AttrDict",
            "babase._language._add_to_attr_dict",
            "babase._meta.DirectoryScan",
            "babase._meta.EXPORT_CLASS_NAME_SHORTCUTS",
            "babase._meta.ScanResults",
            "baclassic._net.MasterServerV1CallThread",
            "bascenev1._dependency.AssetPackage",
            "bascenev1._dependency.Dependency",
            "bascenev1._dependency.DependencyComponent",
            "bascenev1._dependency.DependencyEntry",
            "bascenev1._dependency.DependencyError",
            "bascenev1._dependency.DependencySet",
            "bascenev1._dependency.TestClass",
            "bascenev1._dependency.TestClassFactory",
            "bascenev1._dependency.TestClassObj",
            "bascenev1._dependency.test_depset",
        ],
    )
    def v1_8_0_private(content: str) -> str:
        """private: _babase.SimpleSound -> babase.SimpleSound, _babase.charstr -> babase._text.charstr, _bascenev1.CollisionMesh -> bascenev1.CollisionMesh, _bascenev1.Data -> bascenev1.Data, _bascenev1.Mesh -> bascenev1.Mesh, _bascenev1.Sound -> bascenev1.Sound, _bascenev1.Texture -> bascenev1.Texture, _bauiv1.Mesh -> bauiv1.Mesh, _bauiv1.Sound -> bauiv1.Sound, _bauiv1.Texture -> bauiv1.Texture, babase._devconsoletabs.DevConsoleTabUI -> babase._devconsoletabui.DevConsoleTabUI."""
        content = move_name(content, "_babase.SimpleSound", "babase.SimpleSound")
        content = move_name(content, "_babase.charstr", "babase._text.charstr")
        content = move_name(content, "_bascenev1.CollisionMesh", "bascenev1.CollisionMesh")
        content = move_name(content, "_bascenev1.Data", "bascenev1.Data")
        content = move_name(content, "_bascenev1.Mesh", "bascenev1.Mesh")
        content = move_name(content, "_bascenev1.Sound", "bascenev1.Sound")
        content = move_name(content, "_bascenev1.Texture", "bascenev1.Texture")
        content = move_name(content, "_bauiv1.Mesh", "bauiv1.Mesh")
        content = move_name(content, "_bauiv1.Sound", "bauiv1.Sound")
        content = move_name(content, "_bauiv1.Texture", "bauiv1.Texture")
        content = move_name(content, "babase._devconsoletabs.DevConsoleTabUI", "babase._devconsoletabui.DevConsoleTabUI")
        return content

    @change(
        "1.8.2",
        private=True,
        removed=[
            "baclassic._music.ASSET_SOUNDTRACK_ENTRIES",
            "baclassic._music._audioref",
        ],
    )
    def v1_8_2_private(content: str) -> str:
        """private: 2 removed (reported after porting)."""
        return content

    @change(
        "1.8.4",
        private=True,
        removed=[
            "bascenev1._net._REQS_PAGE_ATTEMPTS_ALIVE",
        ],
    )
    def v1_8_4_private(content: str) -> str:
        """private: _bascenev1.set_admins -> bascenev1._net.set_admins, _bascenev1.set_enable_default_kick_voting -> bascenev1._net.set_enable_default_kick_voting."""
        content = move_name(content, "_bascenev1.set_admins", "bascenev1._net.set_admins")
        content = move_name(content, "_bascenev1.set_enable_default_kick_voting", "bascenev1._net.set_enable_default_kick_voting")
        return content


def collect_changes(*api_classes: type) -> list[Change]:
    """Returns the changes marked in the api classes, oldest game version first."""
    changes = []
    for cls in api_classes:
        for attr in vars(cls).values():
            step = getattr(getattr(attr, "__func__", None), "change", None)
            if step is not None:
                changes.append(step._replace(bump_to=cls.API if step.bump_to else None))
    # public changes of a version before its private ones
    return sorted(changes, key=lambda c: (c.version, c.private))


# Every change baport knows about, keyed by the game version that made it. Taken
# from ballistica's CHANGELOG.md, or its git history where the changelog is silent
# (private api changes come from diffing every release). port() applies them
# oldest first, from the first release of the plugin's api version up to the
# target version, public changes of a version before its private ones.
CHANGES = collect_changes(Api6, Api7, Api8, Api9)


def name_pattern(aliases: dict[str, str], dotted: str) -> str | None:
    """Regex for references to a dotted name through the plugin's imports.

    For bascenev1._map.foo with `import bascenev1 as bs` that is bs._map.foo.
    """
    forms = [
        re.escape(alias + dotted[len(module) :])
        for alias, module in aliases.items()
        if dotted.startswith(module + ".")
    ]
    return rf'(?<![\w.])(?:{"|".join(forms)})\b' if forms else None


def reference(content: str, dotted: str) -> tuple[str, str]:
    """Returns (content, an expression for dotted using the plugin's imports).

    The name's package gets imported if the plugin doesn't import it yet.
    """
    best = max(
        ((a, m) for a, m in ba_aliases(content).items() if dotted.startswith(m + ".")),
        key=lambda am: len(am[1]),
        default=None,
    )
    if best:
        return content, best[0] + dotted[len(best[1]) :]
    return ensure_import(content, dotted.split(".")[0]), dotted


def move_from_imports(content: str, old_module: str, new_module: str, name: str) -> str:
    """Moves name out of `from old_module import ...` into its own `from new_module import`."""

    def fix(match: re.Match) -> str:
        entries = [e.strip() for e in match.group(2).split("#")[0].strip("()").split(",")]
        moving = [e for e in entries if e and e.split()[0] == name]
        if not moving:
            return match.group(0)
        kept = [e for e in entries if e and e not in moving]
        lines = [f"{match.group(1)}from {old_module} import {', '.join(kept)}"] if kept else []
        lines.append(f"{match.group(1)}from {new_module} import {', '.join(moving)}")
        return "\n".join(lines)

    return re.sub(
        rf'^([ \t]*)from {re.escape(old_module)} import (\([^)]*\)|[^\n]+)$',
        fix,
        content,
        flags=re.MULTILINE,
    )


def move_name(content: str, old: str, new: str) -> str:
    """Rewrites a name that moved, e.g. _bauiv1.open_url to _babase.open_url.

    Follows the plugin's imports, so bs._map.x and `from bascenev1._map import x` work too.
    """
    old_module, name = old.rsplit(".", 1)
    if name not in content:
        return content
    content = move_from_imports(content, old_module, new.rsplit(".", 1)[0], name)
    pattern = name_pattern(ba_aliases(content), old)
    if pattern and re.search(pattern, content):
        content, expression = reference(content, new)
        content = re.sub(pattern, lambda _: expression, content)
    return content


def private_removals(content: str, target: tuple[int, ...]) -> list[str]:
    """Notes for private names the plugin uses that are gone in the target version."""
    aliases = ba_aliases(content)
    lines = content.splitlines()
    gone: dict[str, tuple[int, ...]] = {}
    for step in CHANGES:
        if step.version > target:
            break
        for dotted in step.removed:
            gone[dotted] = step.version
        for dotted in step.restored:
            gone.pop(dotted, None)
    words = set(re.findall(r'\w+', content))
    notes = []
    for dotted, version in gone.items():
        module, name = dotted.rsplit(".", 1)
        if name not in words:
            continue
        patterns = [rf'^\s*from {re.escape(module)} import .*\b{name}\b']
        if pattern := name_pattern(aliases, dotted):
            patterns.append(pattern)
        if shown := hit_lines(lines, patterns):
            notes.append(f"line {shown}: private {dotted} was removed in {version_str(version)}")
    return notes


# (pattern, game version it became a problem in, what to do about it)
MANUAL_FIXES = [
    (r'\.Context\(', (1, 7, 20), "ba.Context() is gone, use `with activity.context:`"),
    (
        r'use `bascenev1\.apptimer`',
        (1, 7, 20),
        "real time timer baport couldn't convert, use babase.AppTimer or bascenev1.apptimer",
    ),
    (
        r'timeformat\s*=|TimeFormat\.',
        (1, 7, 20),
        "timers take seconds now, drop timeformat and divide milliseconds by 1000",
    ),
    (r'\bplaysound\(', (1, 7, 20), "playsound() is gone, call sound.play() instead"),
    (
        r'\bTimeType\b',
        (1, 7, 20),
        "TimeType is gone, timers take seconds (use babase.AppTimer for real time)",
    ),
    (
        r'\b(is_ouya_build|android_media_scan_file)\(',
        (1, 7, 14),
        "is_ouya_build() and android_media_scan_file() were removed",
    ),
    (
        r'\bquit\(\s*(soft|back)\s*=',
        (1, 7, 28),
        "quit() takes a babase.QuitType now instead of soft/back",
    ),
    (
        r'bauiv1lib\.promocode\b|^from bauiv1lib import .*\bpromocode\b',
        (1, 7, 35),
        "this ui module was removed",
    ),
    (
        r'\buse_toolbars\b|toolbar_test\b',
        (1, 7, 37),
        "use_toolbars is gone, replace it with True or False",
    ),
    (r'\bUILocation|\bUIEntry\b|\bUIController\b', (1, 7, 37), "removed from bauiv1"),
    (
        r'bauiv1lib\.(specialoffer|debug)\b|^from bauiv1lib import .*\b(specialoffer|debug)\b',
        (1, 7, 37),
        "this ui module was removed",
    ),
    (
        r'_refresh_in_game',
        (1, 7, 37),
        "the in-game menu is bauiv1lib.ingamemenu.InGameMenuWindow now",
    ),
    (r'\bSceneV1AppMode\b', (1, 7, 37), "is baclassic.ClassicAppMode now"),
    (r'\bget_purchased\(', (1, 7, 37), "removed, use app.classic.purchases"),
    (
        r'\.lang\.default_language\b',
        (1, 7, 40),
        "removed, use app.lang.language or app.locale",
    ),
    (
        r'\.env\.(test|arcade|demo|android)\b',
        (1, 7, 42),
        "removed, use app.env.variant or app.env.platform",
    ),
    (
        r'\.main_window_replace\(\s*(?!lambda\b)',
        (1, 7, 51),
        "main_window_replace() needs a callable that makes the window, e.g. lambda: MyWin()",
    ),
    (
        r'\.set_main_window\(',
        (1, 7, 51),
        "set_main_window() needs back_state and extra_type_id passed explicitly",
    ),
    (
        r'\b(WeakCall|Call)\(',
        (1, 7, 53),
        "Call/WeakCall change behavior when api 9 ends, use CallPartial/WeakCallPartial",
    ),
    (
        r'bauiv1lib\.profile\.browser\b|^\s*from bauiv1lib\.profile import .*\bbrowser\b',
        (1, 7, 54),
        "this ui module was removed",
    ),
    (
        r'bauiv1lib\.(purchase|store\.browser)\b|^from bauiv1lib import .*\bpurchase\b',
        (1, 7, 60),
        "this ui module was removed",
    ),
    (
        r'bauiv1lib\.(play|playlist\.browser|settings\.allsettings)\b|^from bauiv1lib import .*\bplay\b'
        r'|\bNewsDisplay\b|\bSendInfoWindow\b',
        (1, 8, 0),
        "this ui module/class was removed or replaced by a doc-ui version",
    ),
    (
        r'\b(builtinassets|classicassets|stdassets)\b',
        (1, 8, 0),
        "the engine's asset wrapper modules are private now, bundle your own",
    ),
    (r'\bcharstr\(', (1, 8, 0), "charstr() is deprecated, use SpecialChar.X.value"),
    (
        r'\bgetsimplesound\(',
        (1, 8, 0),
        "getsimplesound() is inert now (silent), load the sound with getsound() or an asset-package wrapper",
    ),
    (
        r'preview_texture_name',
        (1, 8, 0),
        "deprecated, use get_preview_texture() / preview_texture= with an asset-package wrapper",
    ),
    (r'\bclipboard_get_text\(', (1, 8, 0), "deprecated, use clipboard_get_text_async(callback)"),
    (r'\bLstr\(', (1, 8, 0), "Lstr is going away when api 9 ends, migrate to LangStr"),
    (
        r'\bget_button_name\(',
        (1, 8, 0),
        "returns a LangStr now, it can't be used as an Lstr substitution",
    ),
]


def hit_lines(lines: list[str], patterns: list[str]) -> str | None:
    """Comma separated numbers of the non comment lines matching any pattern, or None."""
    hits = [
        str(n)
        for n, line in enumerate(lines, 1)
        if not line.lstrip().startswith('#') and any(re.search(p, line) for p in patterns)
    ]
    if not hits:
        return None
    return ", ".join(hits[:10]) + (", ..." if len(hits) > 10 else "")


def manual_fixes(content: str, target: tuple[int, ...]) -> list[str]:
    notes = []
    lines = content.splitlines()
    for pattern, since, message in MANUAL_FIXES:
        if target < since:
            continue
        if shown := hit_lines(lines, [pattern]):
            notes.append(f"line {shown}: {message}")
    # Outside of a game, timers need to be app timers since api 8
    if not is_game(content):
        scene = module_aliases(content, "bascenev1")
        if scene and (shown := hit_lines(lines, [rf'\b({scene})\.(Timer|timer)\('])):
            notes.append(
                f"line {shown}: game timers only work inside a game, for menu/ui/chat code "
                "use --app-timers (a repeating bs.timer() needs a babase.AppTimer kept in a variable, by hand)"
            )
    return notes


def is_game(content: str) -> bool:
    """True if the file exports a minigame or map, where game timers are right."""
    return bool(
        re.search(
            r'^\s*#\s*ba_meta export bascenev1\.(GameActivity|Map)\b', content, flags=re.MULTILINE
        )
    )


def module_aliases(content: str, *modules: str) -> str:
    """Regex alternation of the names the given modules are imported as."""
    return "|".join(re.escape(a) for a, m in ba_aliases(content).items() if m in modules)


def call_args(content: str, start: int) -> tuple[int, list[str]] | None:
    """Splits the call arguments starting after the `(` at start.

    Returns (index after the closing paren, top level args) or None if unbalanced.
    """
    depth, args, current, quote = 0, [], "", None
    for i in range(start, len(content)):
        char = content[i]
        if quote:
            if char == quote and content[i - 1] != "\\":
                quote = None
        elif char in "'\"":
            quote = char
        elif char in "([{":
            depth += 1
        elif char in ")]}":
            if depth == 0:
                args.append(current)
                return i + 1, [a for a in args if a.strip()]
            depth -= 1
        elif char == "," and depth == 0:
            args.append(current)
            current = ""
            continue
        current += char
    return None


def use_app_timers(content: str) -> str:
    """Turns game timers into app timers, for code that runs outside of games.

    bs.Timer() becomes babase.AppTimer() (same arguments). bs.timer() becomes
    babase.apptimer(), unless it repeats, since apptimer() can't.
    """
    scene = module_aliases(content, "bascenev1")
    if not scene or not re.search(rf'\b({scene})\.(Timer|timer)\(', content):
        return content
    content, base = module_alias(content, "babase")
    content = re.sub(rf'\b(?:{scene})\.Timer\(', f"{base}.AppTimer(", content)

    def timer(match: re.Match) -> str:
        parsed = call_args(content, match.end())
        if parsed is None:
            return match.group(0)
        _, args = parsed
        repeats = len(args) > 2 or any(re.match(r'\s*repeat\s*=', a) for a in args)
        return match.group(0) if repeats else f"{base}.apptimer("

    return re.sub(rf'\b(?:{scene})\.timer\(', timer, content)


def source_api(content: str) -> int | None:
    match = re.search(r'^\s*#\s*ba_meta require api (\d+)', content, flags=re.MULTILINE)
    return int(match.group(1)) if match else None


def target_api(target: tuple[int, ...]) -> int:
    return max(api for api, first in API_FIRST_VERSION.items() if target >= first)


def port(
    content: str,
    plugin_type: str | None,
    target: tuple[int, ...],
    app_timers: bool = False,
    private: bool = True,
) -> tuple[str, list[tuple[int, ...]]]:
    """Returns content ported to the target game version, and the versions whose changes touched it.

    Changes are applied one game version at a time, oldest first, starting at
    the first release of the plugin's api version. app_timers turns game timers
    into app timers, for plugins that run outside of games. private includes
    the private api moves.
    """
    api = source_api(content) or 7
    start = API_FIRST_VERSION.get(api, (0,))
    applied = []
    for step in CHANGES:
        if not start <= step.version <= target:
            continue
        if step.bump_to is not None and api >= step.bump_to:
            continue
        if step.private and not private:
            continue
        new = step.apply(content, plugin_type) if step.needs_type else step.apply(content)
        if new != content and step.version not in applied:
            applied.append(step.version)
        content = new
    if app_timers and not is_game(content):
        content = use_app_timers(content)
    if not content.startswith(TRADEMARK):
        content = TRADEMARK + content
    return content, applied


def version_str(version: tuple[int, ...]) -> str:
    return ".".join(map(str, version))


def list_changes(target: tuple[int, ...], private: bool = True) -> None:
    api = None
    for step in CHANGES:
        if step.version > target:
            break
        if step.private and not private:
            continue
        if target_api(step.version) != api:
            api = target_api(step.version)
            print(f"api {api}")
        print(f"  {version_str(step.version):8} {step.apply.__doc__.splitlines()[0]}")


def parse_version(text: str) -> tuple[int, ...]:
    if text == "latest":
        return LATEST_VERSION
    if not re.fullmatch(r'\d+\.\d+\.\d+', text):
        raise argparse.ArgumentTypeError(f"'{text}' is not a version like 1.8.3")
    version = tuple(int(part) for part in text.split('.'))
    if version < API_FIRST_VERSION[8]:
        raise argparse.ArgumentTypeError("baport can only port to 1.7.20 (api 8) or newer")
    return version


def main() -> None:
    latest = ".".join(map(str, LATEST_VERSION))
    parser = argparse.ArgumentParser(
        description="Port BombSquad plugins from api 6/7/8 to api 9.", exit_on_error=False
    )
    parser.add_argument("-f", "--file", nargs="+", type=Path, help="File path(s) of the plugin(s).")
    parser.add_argument(
        "-t",
        "--type",
        choices=["client", "server"],
        help="Type of plugin (client or server). Needed when porting from api 6/7.",
    )
    target = parser.add_mutually_exclusive_group()
    target.add_argument(
        "-v",
        "--version",
        default=latest,
        type=parse_version,
        help=f"BombSquad version to port to, like 1.8.3 (default: {latest}).",
    )
    target.add_argument(
        "-a",
        "--api",
        type=int,
        choices=sorted(API_LAST_VERSION),
        help="Api version to port to, ports to the last game version with that api "
        + "("
        + ", ".join(f"{api}: {version_str(v)}" for api, v in API_LAST_VERSION.items())
        + ").",
    )
    parser.add_argument(
        "--app-timers",
        action="store_true",
        help="For plugins that run outside of games (menus, ui, chat): turn game timers "
        "(bs.Timer/bs.timer) into app timers (babase.AppTimer/babase.apptimer). "
        "Minigames and maps are left alone.",
    )
    parser.add_argument(
        "--private",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Also port private api (_babase, babase._apputils, ...) that moved, and report private "
        "names that were removed (default: on, --no-private to turn it off).",
    )
    parser.add_argument(
        "-l",
        "--list-changes",
        action="store_true",
        help="List the changes baport applies for each game version, up to -v.",
    )
    parser.add_argument(
        "-n", "--dry-run", action="store_true", help="Print a diff instead of writing the file."
    )
    if len(sys.argv) == 1:
        parser.print_help()
        return
    try:
        args = parser.parse_args()
    except argparse.ArgumentError as error:
        print(error)
        parser.print_help()
        exit(1)

    if args.api is not None:
        args.version = API_LAST_VERSION[args.api]
    version = version_str(args.version)
    if args.list_changes:
        list_changes(args.version, private=args.private)
        return
    if not args.file:
        parser.print_usage()
        print("baport.py: error: -f/--file is required")
        exit(1)
    failed = False
    for file_path in args.file:
        if not file_path.exists():
            print(f"Error: The file '{file_path}' does not exist.")
            failed = True
            continue
        encoding = detect_file_encoding(file_path)
        if not encoding:
            print(f"Error: Could not detect encoding of '{file_path}'")
            failed = True
            continue
        file_text = file_path.read_text(encoding=encoding)
        api = source_api(file_text)
        if api is not None and api > target_api(args.version):
            print(f"Skipping {file_path.name}: it already targets api {api}, newer than {version}")
            continue
        try:
            new_text, applied = port(
                file_text,
                args.type,
                args.version,
                app_timers=args.app_timers,
                private=args.private,
            )
        except ValueError as error:
            print(f"Error: {file_path.name}: {error}")
            failed = True
            continue
        print(
            f"Porting {file_path.name} (api {api or '6/7'}) to {version} (api {target_api(args.version)})"
        )
        if args.app_timers and is_game(file_text):
            print("  --app-timers ignored, this is a minigame or map and needs game timers")
        if applied:
            print("  changes from: " + ", ".join(version_str(v) for v in applied))
        if args.dry_run:
            sys.stdout.writelines(
                difflib.unified_diff(
                    file_text.splitlines(keepends=True),
                    new_text.splitlines(keepends=True),
                    fromfile=str(file_path),
                    tofile=str(file_path),
                )
            )
        else:
            with open(file_path, 'w', encoding=encoding) as write_f:
                write_f.write(new_text)
        notes = manual_fixes(new_text, args.version)
        if args.private:
            notes += private_removals(new_text, args.version)
        for note in notes:
            print(f"  manual fix needed, {note}")
    if failed:
        exit(1)


if __name__ == "__main__":
    main()
