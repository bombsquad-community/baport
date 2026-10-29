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
from bisect import bisect_right
import builtins
import difflib
import io
from functools import lru_cache
from pathlib import Path
import re
import sys
import tokenize
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
        # the api 7 port can leave hints that don't parse, so find the imports as text
        future = [min(imp.end + 1, len(content)) for imp in from_imports(content, "__future__")]
        first = re.search(r'^(?:import|from)[ \t]+(?!__future__\b)\w', content, flags=re.MULTILINE)
        index = first.start() if first else 0
        return insert_at(content, max([index] + future[-1:]), statement)
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
    # ast counts lines by \n only, unlike str.splitlines()
    index = sum(len(l) + 1 for l in content.split("\n")[: line - 1])
    return insert_at(content, min(index, len(content)), statement)


def insert_at(content: str, index: int, statement: str) -> str:
    """Inserts the statement line at index, the start of a line (or the end of content)."""
    if index and not content[:index].endswith("\n"):
        statement = "\n" + statement
    return content[:index] + statement + "\n" + content[index:]


def ensure_import(content: str, module: str) -> str:
    """Adds `import module` if the plugin doesn't import it yet."""
    if re.search(rf'^\s*import\s+{re.escape(module)}\s*$', content, flags=re.MULTILINE):
        return content
    return add_import(content, f"import {module}")


class Note(NamedTuple):
    """Something baport couldn't port, at these line numbers."""

    lines: list[int]
    message: str
    # still works, until api 9 support ends
    deprecated: bool = False


class FromImport(NamedTuple):
    """A `from module import ...` statement, as from_imports() finds it."""

    # offsets of its first character (the indent) and of the end of its last line
    start: int
    end: int
    indent: str
    module: str
    # ("name" or "name as alias", the comment after it on its line or "")
    entries: list[tuple[str, str]]
    parens: bool
    # comments that belong to the whole statement
    comments: list[str]


def split_comment(line: str) -> tuple[str, str]:
    """Splits an import line into its code and its `# comment` (imports hold no strings)."""
    code, hash_, comment = line.partition("#")
    return code, (hash_ + comment).rstrip()


def from_imports(content: str, module: str | None = None) -> list[FromImport]:
    """The `from module import ...` statements in content (all modules when module is None).

    Handles parenthesized imports over several lines, backslash continuations,
    `as` aliases and comments. Statements with anything else on their line
    (`; x = 1`) are skipped.
    """
    found = []
    for match in re.finditer(r'^([ \t]*)from[ \t]+([\w.]+)[ \t]+import[ \t]*', content, flags=re.MULTILINE):
        if module is not None and match.group(2) != module:
            continue
        pos = match.end()
        entries, comments = [], []
        if content.startswith("(", pos):
            # the closing paren, skipping comments that may hold one
            i = pos + 1
            while i < len(content) and content[i] != ")":
                i = content.find("\n", i) if content[i] == "#" else i + 1
                if i < 0:
                    i = len(content)
            if i >= len(content):
                continue
            for line in content[pos + 1 : i].split("\n"):
                code, comment = split_comment(line)
                names = [n.strip() for n in code.split(",") if n.strip()]
                entries += [(n, "") for n in names]
                if comment and names:
                    entries[-1] = (entries[-1][0], comment)
                elif comment:
                    comments.append(comment)
            end = content.find("\n", i)
            end = len(content) if end < 0 else end
            code, comment = split_comment(content[i + 1 : end])
            parens = True
        else:
            code, end = "", pos
            while True:
                line_end = content.find("\n", end)
                line_end = len(content) if line_end < 0 else line_end
                part, comment = split_comment(content[end:line_end])
                if not comment and part.rstrip().endswith("\\") and line_end < len(content):
                    code += part.rstrip()[:-1] + " "
                    end = line_end + 1
                    continue
                code += part
                end = line_end
                break
            entries = [(n.strip(), "") for n in code.split(",") if n.strip()]
            code = ""
            parens = False
        if code.strip() or not entries or any(not re.fullmatch(r'\w+(\s+as\s+\w+)?', e) for e, _ in entries):
            continue
        if comment:
            comments.append(comment)
        found.append(FromImport(match.start(), end, match.group(1), match.group(2), entries, parens, comments))
    return found


def entry_name(entry: tuple[str, str]) -> str:
    """The imported name of a FromImport entry."""
    return entry[0].split()[0]


def entry_local(entry: tuple[str, str]) -> str:
    """The name a FromImport entry binds (its alias, if any)."""
    return entry[0].split()[-1]


def import_text(imp: FromImport, entries: list[tuple[str, str]]) -> str:
    """The statement imp with entries instead of its own."""
    head = f"{imp.indent}from {imp.module} import "
    comment = "".join("  " + c for c in imp.comments)
    if not imp.parens and not any(c for _, c in entries):
        return head + ", ".join(e for e, _ in entries) + comment
    lines = [head + "(" + comment]
    lines += [f"{imp.indent}    {e},{'  ' + c if c else ''}" for e, c in entries]
    return "\n".join(lines + [imp.indent + ")"])


def replace_import(content: str, imp: FromImport, entries: list[tuple[str, str]]) -> str:
    """Rewrites imp to import only entries, dropping it when none are left."""
    if entries:
        return content[: imp.start] + import_text(imp, entries) + content[imp.end :]
    if imp.indent:
        # an emptied import inside a block still needs a statement there
        return content[: imp.start] + imp.indent + "pass" + content[imp.end :]
    return content[: imp.start] + content[imp.end + 1 if content.startswith("\n", imp.end) else imp.end :]


def imported_as(content: str, module: str, name: str) -> set[str]:
    """The local names `from module import name [as alias]` statements bind."""
    return {entry_local(e) for imp in from_imports(content, module) for e in imp.entries if entry_name(e) == name}


def bare_names(names: set[str]) -> str | None:
    """Regex for bare uses of names (`from x import name as alias` locals), not attributes or defs."""
    if not names:
        return None
    return rf'(?<![\w.])(?<!def )(?:{"|".join(map(re.escape, sorted(names)))})'


def take_imported(content: str, modules: tuple[str, ...], name: str) -> tuple[str, str | None]:
    """Drops name from `from module import ...` for each of modules.

    Returns (content, bare_names() of the local names those imports bound),
    for rewriting the uses that go through them.
    """
    local = set().union(*(imported_as(content, m, name) for m in modules))
    for module in modules:
        content = drop_import_name(content, module, name)
    return content, bare_names(local)


def drop_import_name(content: str, module: str, name: str) -> str:
    """Removes name from `from module import ...` statements, dropping ones left empty."""
    for imp in reversed(from_imports(content, module)):
        kept = [e for e in imp.entries if entry_name(e) != name]
        if len(kept) != len(imp.entries):
            content = replace_import(content, imp, kept)
    return content


def replace_calls(
    content: str, pattern: str, make: Callable[[re.Match, list[str] | None], str | None]
) -> str:
    """Rewrites each call whose start, up to and including its `(`, matches pattern.

    make(match, args) returns the text for the whole call, or None to leave it.
    args is None when the call's parens don't balance, and then the text only
    replaces the match.
    """
    out, pos = [], 0
    for match in re.finditer(pattern, content):
        if match.start() < pos:
            continue
        parsed = call_args(content, match.end())
        text = make(match, parsed[1] if parsed else None)
        if text is None:
            continue
        out += [content[pos : match.start()], text]
        pos = parsed[0] if parsed else match.end()
    return "".join(out) + content[pos:]


def drop_removed_function(content: str, module: str, name: str) -> str:
    """Neutralizes a function removed without replacement.

    Calls become None and other references a no-op lambda, which keeps
    plugins that wrap the function (old = bui.func; ...) loading.
    """
    # through any ba module name, also ones the api 7 port leaves unimported (_bui.x)
    forms = [rf'(?<![\w.])(?:_?ba\w*|_?bui|_?bs|{"|".join(map(re.escape, ba_aliases(content))) or "_?ba"})\.{name}\b']
    # api 7's _ba (now _babase) had all the native functions
    modules = (module, "_" + module, "_babase")
    used = {m for m in modules[1:] if re.search(rf'\b{m}\.', content)}
    content, bare = take_imported(content, modules, name)
    forms += [rf'{bare}\b'] if bare else []
    for form in forms:
        content = replace_calls(content, rf'{form}\s*\(', lambda _, args: "None" if args is not None else None)
        # assigning to it only adds an attribute, so leave that alone
        content = re.sub(rf'{form}(?!\s*=[^=])', "(lambda *args, **kwargs: None)", content)
    # private module imports only this used (the api 7 port imports them for such names)
    for private in used:
        content = drop_module_import(content, private, unless_used=True)
    return content


def module_alias(content: str, *modules: str) -> tuple[str, str]:
    """Returns (content, alias) for the first of modules the plugin imports.

    If it imports none of them the first one gets imported.
    """
    for alias, imported in ba_aliases(content).items():
        if imported in modules:
            return content, alias
    return ensure_import(content, modules[0]), modules[0]


# Public names of bascenev1 and bauiv1 that babase doesn't have (their __all__ in 1.8.5)
SCENE_ONLY_NAMES = frozenset(
    """
    Activity ActivityData Actor ActorHost BaseTime BaseTimer BoolSetting Campaign
    CelebrateMessage CharacterHandle CharacterParts ChoiceSetting Chooser
    ClassicControlsLocalDisplayConfig Collision CollisionMesh CollisionMeshHandle
    CoopGameActivity CoopSession DEFAULT_TEAM_COLORS DEFAULT_TEAM_NAMES Data DeathType
    Depiction DieMessage DropMessage DroppedMessage DualTeamSession EmptyPlayer EmptyTeam
    FeedbackEvent FloatChoiceSetting FloatSetting FreeForAllSession FreezeMessage
    GameActivity GameResults GameTip HealthBarDisplay HitMessage HostInfo HostProbeOutcome
    HostRequirements ImpactDamageMessage InputDevice IntChoiceSetting IntSetting
    JoinActivity JoinInfo Level Lobby LocalDisplay LocalDisplayConfig LocalDisplayConfigSet
    LocalDisplayHandler LocalDisplayTypeID Map Material Mesh MultiTeamSession MusicType Node
    NodeActor OutOfBoundsMessage PickUpMessage PickedUpMessage Player PlayerDiedMessage
    PlayerInfo PlayerProfilesChangedMessage PlayerRecord PlayerScoredMessage
    PowerupAcceptMessage PowerupMessage Quat SceneV1AppSubsystem SceneV1AssetSet SceneViewer
    ScoreConfig ScoreScreenActivity ScoreType Session SessionData SessionPlayer SessionTeam
    Setting ShouldShatterMessage Sound SoundHandle SpazDef StandLocation StandMessage Stats
    Team TeamGameActivity Texture ThawMessage Time Timer UNHANDLED UnknownLocalDisplayConfig
    WinnerGroup animate animate_array apcollisionmeshget apmeshget apsoundget aptextureget
    basetime basetimer broadcastmessage cameraflash camerashake
    capture_game_controller_input capture_keyboard_input chatmessage
    client_info_query_response connect_to_party disconnect_client disconnect_from_host
    emitfx end_host_scanning fetch_host_requirements filter_playlist get_chat_messages
    get_client_ping get_connection_to_host_info get_connection_to_host_info_2
    get_default_free_for_all_playlist get_default_powerup_distribution
    get_default_teams_playlist get_filtered_map_name get_foreground_host_activity
    get_foreground_host_session get_game_port get_game_roster get_input_devices
    get_joined_host_asset_packages get_local_active_input_devices_count
    get_main_ui_input_device get_map_class get_map_display_string get_player_colors
    get_player_profile_colors get_player_profile_icon get_public_party_enabled
    get_public_party_max_size get_random_names get_replay_asset_packages
    get_replay_speed_exponent get_trophy_string getactivity getcollision getcollisionmesh
    getdata getinputdevice getlocaldisplay getmesh getnodes getsession getsound gettexture
    have_connected_clients have_touchscreen_input host_scan_cycle host_ui_send_to_client
    host_ui_send_to_host init_campaigns is_in_replay is_replay_paused launch_replay
    ls_input_devices ls_objects name_text new_host_session new_replay_session newactivity
    newnode pause_replay plain_name_json prepare_replay print_live_object_warnings
    printnodes protocol_version register_local_display_handler register_map
    release_game_controller_input release_keyboard_input reset_random_player_names
    resolve_asset_packages_with_dialog resume_replay seek_replay set_admins
    set_authenticate_clients set_debug_speed_exponent set_enable_default_kick_voting
    set_host_password set_hosting_asset_packages set_internal_music set_map_bounds
    set_master_server_source set_max_players_override set_player_rejoin_cooldown
    set_public_party_enabled set_public_party_max_size set_public_party_name
    set_public_party_public_address_ipv4 set_public_party_public_address_ipv6
    set_public_party_queue_enabled set_public_party_stats_url set_replay_speed_exponent
    set_scene_asset_set set_touchscreen_editing setmusic show_damage_count split_character
    texture_from_ref texture_spec_from_ref time timer
    """.split()
)
UI_ONLY_NAMES = frozenset(
    """
    BasicMainWindowState CharacterHandle Keyboard MainWindow MainWindowAutoRecreateSuppress
    MainWindowState Mesh MeshViewer RootUIUpdatePause Sound SoundHandle Texture UIAssetSet
    UIOpenState UIV1AppSubsystem Viewer ViewerRegistry ViewerSource Widget Window apmeshget
    apsoundget aptextureget buttonwidget checkboxwidget columnwidget containerwidget
    flash_party_icon get_depiction_control get_qrcode_texture get_selected_widget
    get_special_widget getmesh getsound gettexture hscrollwidget imagewidget mesh_from_ref
    play_swish root_ui_pause_updates root_ui_resume_updates rowwidget scrollwidget
    set_party_notice set_ui_asset_set sliderwidget snap_slider_value sound_from_ref
    spinnerwidget texture_from_ref textwidget uibounds uicleanupcheck widget widget_by_id
    """.split()
)


def fix_misplaced_names(content: str, plugin_type: str | None) -> str:
    """Fixes names earlier ports left in the wrong module.

    Earlier api 7 to 8 ports turned every ba.X into babase.X, so babase.X moves
    to bascenev1/bauiv1 for names babase never had. Names in both modules
    (Texture, gettexture, ...) follow the plugin type like the api 7 port does
    (client: bauiv1), or without one whichever of them the plugin imports.
    Some also picked the wrong one of the two: bs.Widget is bui.Widget, and
    bui.X for a bascenev1-only name is bs.X. The party window is
    app.classic.party_window, not app.ui_v1's, and local screenmessage()
    calls lose the args only broadcastmessage() takes.
    """
    content = re.sub(r'\.app\.ui_v1\.party_window\b', ".app.classic.party_window", content)
    aliases = ba_aliases(content)

    def imports(module: str) -> bool:
        return module in aliases.values() or bool(from_imports(content, module))

    if plugin_type:
        shared = "bauiv1" if plugin_type == "client" else "bascenev1"
    else:
        shared = "bauiv1" if imports("bauiv1") and not imports("bascenev1") else "bascenev1"

    def home(source: str, name: str, offset: int | None = None) -> str | None:
        """The module name (used at offset) belongs in, when it isn't source's."""
        scene, ui = name in SCENE_ONLY_NAMES, name in UI_ONLY_NAMES
        if source in ("babase", "_babase"):
            if scene and ui:
                # game code wants scene assets (a UI sound can't take a position)
                return "bascenev1" if offset is not None and scope_of(content).in_game(offset) else shared
            return "bascenev1" if scene else "bauiv1" if ui else None
        if source == "bascenev1":
            return "bauiv1" if ui and not scene else None
        return "bascenev1" if scene and not ui else None

    # _babase too: api 7's _ba held what is now _bascenev1/_bauiv1, exported publicly
    for source in ("babase", "_babase", "bascenev1", "bauiv1"):
        for name in sorted({entry_name(e) for imp in from_imports(content, source) for e in imp.entries}):
            if module := home(source, name):
                content = move_from_imports(content, source, module, name)
        # unimported babase.X too, it survives in annotations
        names = "|".join(filter(None, [module_aliases(content, source), "babase" if source == "babase" else ""]))
        if not names:
            continue
        pattern = rf'(?<![\w.])(?:{names})\.(\w+)\b'
        targets = {}
        for module in sorted({home(source, m.group(1), m.start()) for m in re.finditer(pattern, content)} - {None}):
            content, targets[module] = module_alias(content, module)

        def fix(match: re.Match) -> str:
            module = home(source, match.group(1), match.start())
            if not module:
                return match.group(0)
            # ba_meta lines need the full module name, not an alias
            line = content[content.rfind("\n", 0, match.start()) + 1 : match.start()]
            prefix = module if re.match(r'\s*#\s*ba_meta\b', line) else targets[module]
            return f"{prefix}.{match.group(1)}"

        content = re.sub(pattern, fix, content)
    return local_screenmessage_args(content)


def local_screenmessage_args(content: str) -> str:
    """Drops the args only bascenev1.broadcastmessage() takes from local screenmessage() calls.

    babase/bauiv1.screenmessage() only shows on this device and takes
    message, color, log and literal; top, image, clients and transient were
    api 7's ba.screenmessage() args for messages sent to the clients too.
    """
    local = module_aliases(content, "babase", "_babase", "bauiv1")
    names = [rf'(?<![\w.])(?:{local})\.screenmessage'] if local else []
    if bare := bare_names(set().union(*(imported_as(content, m, "screenmessage") for m in ("babase", "bauiv1")))):
        names.append(bare)
    if not names:
        return content

    def call(match: re.Match, args: list[str] | None) -> str | None:
        if args is None:
            return None
        kept = [a.strip() for a in args if not re.match(r'\s*(top|image|clients|transient)\s*=', a)]
        if len(kept) == len(args):
            return None
        return f"{match.group(0)}{', '.join(kept)})"

    return replace_calls(content, rf'(?:{"|".join(names)})\s*\(', call)


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
        """in_game_thread() is in_logic_thread(), getlog()/get_log_file_path() are get_v1_cloud_log()/get_v1_cloud_log_file_path(), _ba's account and store calls are ba.internal's."""
        content = re.sub(r'\.in_game_thread\b', ".in_logic_thread", content)
        content = re.sub(r'\.getlog\b', ".get_v1_cloud_log", content)
        content = re.sub(r'\.get_log_file_path\b', ".get_v1_cloud_log_file_path", content)
        # they went to the closed source _bainternal, which plugins use through ba.internal
        before = content
        for name in BAINTERNAL_1_7_7:
            content = move_name(content, f"_ba.{name}", f"ba.internal.{name}")
        # a submodule, `import ba` alone doesn't load it
        return ensure_import(content, "ba.internal") if content != before else content

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
            r'^([ \t]*)import ba\b(?![.\w])',
            lambda m: "\n".join(f"{m.group(1)}import {name}" for name in ("babase", "bauiv1", "bascenev1")),
            content,
            flags=re.MULTILINE,
        )
        content = content.replace("babase.app.ui", "bauiv1.app.ui_v1")

        content = content.replace("babase.app.accounts_v1", "bauiv1.app.classic.accounts")

        ###################################################################################
        # Comment out one of these as per your requirements, depending whether to
        # stay local or if it'll also be needed to transmitted to the clients.

        ## For local:
        if plugin_type == "client":
            content = content.replace("_babase.screenmessage", "bauiv1.screenmessage")
            content = content.replace("babase.screenmessage", "bauiv1.screenmessage")
        else:
            ## For transmission:
            content = content.replace("_babase.screenmessage", "bascenev1.broadcastmessage")
            content = content.replace("babase.screenmessage", "bascenev1.broadcastmessage")
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
        # api 7's ba held the scene's private modules, as `ba._x` or `from ba import _x`
        content = campaign_lookups(content)
        content = re.sub(rf'\bbabase\.({"|".join(SCENE_PRIVATE_MODULES)})\b', r'bascenev1.\1', content)
        for module in {entry_name(e) for imp in from_imports(content, "babase") for e in imp.entries} & set(
            SCENE_PRIVATE_MODULES
        ):
            content = move_from_imports(content, "babase", "bascenev1", module)
        content = content.replace(
            "_babase.get_client_public_device_uuid", "_bascenev1.get_client_public_device_uuid"
        )
        content = content.replace("babase.PickedUpMessage", "bascenev1.PickedUpMessage")
        content = content.replace("babase.PowerupMessage", "bascenev1.PowerupMessage")
        content = content.replace("babase.FreezeMessage", "bascenev1.FreezeMessage")
        content = context_refs(content)
        content = content.replace("babase.Chooser", "bascenev1.Chooser")
        content = content.replace("PlayerType", "PlayerT")
        content = content.replace(
            "babase.app.spaz_appearances", "babase.app.classic.spaz_appearances"
        )
        content = content.replace("babase._servermode", "baclassic._servermode")
        content = content.replace("_babase.app.server", "babase.app.classic.server")
        content = content.replace("_babase.chatmessage", "bascenev1.chatmessage")
        content = content.replace("_babase.disconnect_client", "_bascenev1.disconnect_client")
        content = content.replace("_babase.get_game_roster", "bascenev1.get_game_roster")
        content = content.replace(
            "_babase.get_public_party_max_size", "bascenev1.get_public_party_max_size"
        )
        content = content.replace("_babase.new_host_session", "bascenev1.new_host_session")
        content = content.replace("model", "mesh")
        content = drop_timetypes(content)
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
        content = playsound_calls(content)

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
        content = content.replace(
            "bascenev1.time(timeformat=babase.TimeFormat.MILLISECONDS)", "bascenev1.time() * 1000"
        )

        # get_connection_to_host_info() still returns a dict, its _2 variant returns HostInfo | None
        # which would break the dict style code written against it, so leave it alone.

        # ba._store's functions are app.classic.store methods now, not a module to import
        content = drop_module_import(content, "babase._store")
        content = content.replace("babase._store", "bauiv1.app.classic.store")
        content = content.replace("bastd.ui", "bauiv1lib")
        content = content.replace("bastd", "bascenev1lib")
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
        full = {"bs": "bascenev1", "bui": "bauiv1"}
        # `import ba` takes the alias, `import ba._map` and `from ba import x` the module
        content = re.sub(
            r'(?<![\w.])import (bs|bui)\b(?!\.)', lambda m: f"import {full[m.group(1)]} as {m.group(1)}", content
        )
        content = re.sub(r'\b(import|from) (bs|bui)(?=\.|\s+import\b)', lambda m: f"{m.group(1)} {full[m.group(2)]}", content)
        content = content.replace("bslib", "bascenev1lib")
        content = content.replace("builib", "bauiv1lib")
        content = re.sub(
            r'^(\s*#\s*ba_meta export) bs\.GameActivity\b',
            r'\1 bascenev1.GameActivity',
            content,
            flags=re.MULTILINE,
        )
        content = content.replace("_bs", "bs")
        # ba.internal's names all moved; the import goes once nothing uses it
        content = drop_module_import(content, "babase.internal", unless_used=True)

        return content

    @change("1.7.20", needs_type=True)
    def v1_7_20_misplaced(content: str, plugin_type: str | None) -> str:
        """babase.Setting etc. to bascenev1/bauiv1, bs.Widget etc. to bauiv1, app.ui_v1.party_window to app.classic."""
        return fix_misplaced_names(content, plugin_type)

    @change(
        "1.7.20",
        private=True,
        removed=[
            "_babase.bless",
            "_babase.reset_game_activity_tracking",
            "_babase.set_telnet_access_enabled",
            "_babase.time_format_check",
        ],
    )
    def v1_7_20_private(content: str) -> str:
        """private: api 7's _ba names the api 7 port left in _babase move to _bascenev1/_bauiv1/_baclassic, 4 removed."""
        if "_babase" not in content:
            return content
        for module, names in API7_NATIVE_MOVES.items():
            for name in names:
                content = move_name(content, f"_babase.{name}", f"{module}.{name}")
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
        local = imported_as(content, "efro.call", "tpartial")
        content = drop_import_name(content, "efro.call", "tpartial")
        for name in sorted(local):
            content = add_import(content, f"from functools import partial as {name}")
        if "efro.call.tpartial" in content:
            content = content.replace("efro.call.tpartial", "functools.partial")
            content = ensure_import(content, "functools")
        # enum_by_value(MyEnum, foo) is just MyEnum(foo)
        content, bare = take_imported(content, ("efro.util",), "enum_by_value")
        names = "|".join(filter(None, [r'\b(?:[\w.]+\.)?enum_by_value', bare]))
        content = re.sub(rf'(?:{names})\(\s*([\w.]+)\s*,\s*', r'\1(', content)
        # the party icon is always visible now
        content = replace_calls(
            content, r'\b\w+\.is_party_icon_visible\(', lambda _, args: "True" if args is not None else None
        )
        content = drop_removed_function(content, "bauiv1", "set_party_icon_always_visible")
        content = re.sub(r'\b(\w+)\.env\(\)\[["\']ui_scale["\']\]', r'\1.get_ui_scale()', content)
        content = re.sub(r'\bUIV1Subsystem\b', "UIV1AppSubsystem", content)
        content = keyword_only_args(content)
        return re.sub(r'\.threadpool_submit_no_wait\(', ".threadpool.submit_no_wait(", content)

    @change("1.7.37", needs_type=True)
    def v1_7_37_misplaced(content: str, plugin_type: str | None) -> str:
        """The 1.7.20 misplaced names fix again, for plugins already claiming api 9."""
        return fix_misplaced_names(content, plugin_type)

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
        modules = {imp.module for imp in from_imports(content) if re.fullmatch(r'_?ba[\w.]*', imp.module)}
        used = False
        for kind in ("exception", "error"):
            content, bare = take_imported(content, tuple(modules), f"print_{kind}")
            pattern = rf'\b(?:{aliases})\.print_{kind}'
            if bare:
                pattern = rf'(?:{pattern}|{bare})'
            if re.search(rf'{pattern}\(', content):
                content = print_to_logging(content, rf'{pattern}\(', kind)
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
        module = "bascenev1lib.actor.playerspaz"
        imports = [imp for imp in from_imports(content, module) if any(entry_name(e) == "PlayerT" for e in imp.entries)]
        if not imports:
            return content
        for imp in reversed(imports):
            # a TypeVar in its place, in the same block (often `if TYPE_CHECKING:`)
            kept = [e for e in imp.entries if entry_name(e) != "PlayerT"]
            lines = [import_text(imp, kept)] if kept else []
            lines += [
                f"{imp.indent}{entry_local(e)} = typing.TypeVar('PlayerT')"
                for e in imp.entries
                if entry_name(e) == "PlayerT"
            ]
            content = content[: imp.start] + "\n".join(lines) + content[imp.end :]
        content = ensure_import(content, "typing")
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
        content = party_window_open_states(content)
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
        """charstr(SpecialChar.X) is SpecialChar.X.value, textwidget on_return_press_call is on_submit_call, Actor._activity is gone."""
        # also through import aliases (`from bauiv1 import charstr as cs, SpecialChar as sc`)
        modules = ("babase", "_babase", "babase._text", "bauiv1", "bascenev1")

        def through_aliases(pattern: str, name: str) -> str:
            bare = bare_names({n for m in modules for n in imported_as(content, m, name) if n != name})
            return f"{pattern}|{bare}" if bare else pattern

        charstr = through_aliases(r'\b(?:\w+\.)?charstr', "charstr")
        special = through_aliases(r'[\w.]*SpecialChar', "SpecialChar")
        content = re.sub(rf'(?:{charstr})\(\s*((?:{special})\.\w+)\s*\)', r'\1.value', content)
        content = re.sub(
            rf'(?:{charstr})\(\s*(getattr\(\s*(?:{special})\s*,(?:[^()]|\([^()]*\))*\))\s*\)',
            r'\1.value',
            content,
        )
        # drop the import once nothing calls it by the name it binds
        for module in modules:
            local = imported_as(content, module, "charstr")
            if local and not any(re.search(rf'(?<![\w.]){re.escape(n)}\(', content) for n in local):
                content = drop_import_name(content, module, "charstr")
        content = re.sub(r'\bon_return_press_call\s*=', "on_submit_call=", content)
        content = re.sub(r'\binvoke_return_press\s*=', "invoke_submit=", content)
        # the game passes langstr=True to these now (and wants a LangStr back then)
        def add_langstr(m: re.Match) -> str:
            params = m.group(2)
            if re.search(r'\blangstr\b|\*\*', params):
                return m.group(0)
            star = "" if re.search(r'(^|,)\s*\*', params) else "*, "
            return f"{m.group(1)}{params}, {star}langstr: bool = False{m.group(3)}"

        content = re.sub(
            r'(\bdef (?:get_instance_display_string|get_instance_scoreboard_display_string'
            r'|get_display_string|get_team_display_string)\s*\()([^)]*?)(\s*,?\s*\))',
            add_langstr,
            content,
        )
        return actor_activity_refs(content)

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


def drop_module_import(content: str, module: str, unless_used: bool = False) -> str:
    """Removes `import module` lines (unless_used: only when nothing else mentions module)."""
    line = rf'^[ \t]*import {re.escape(module)}[ \t]*(#[^\n]*)?\n'
    if unless_used and re.search(rf'\b{re.escape(module)}\b', re.sub(line, "", content, flags=re.MULTILINE)):
        return content
    return re.sub(line, "", content, flags=re.MULTILINE)


def move_from_imports(content: str, old_module: str, new_module: str, name: str) -> str:
    """Moves name out of `from old_module import ...` into its own `from new_module import`."""
    for imp in reversed(from_imports(content, old_module)):
        moving = [e for e in imp.entries if entry_name(e) == name]
        if not moving:
            continue
        kept = [e for e in imp.entries if e not in moving]
        # the moved names keep their comments, and the statement's when nothing stays
        comments = [c for _, c in moving if c] + ([] if kept else imp.comments)
        moved = f"{imp.indent}from {new_module} import " + ", ".join(e for e, _ in moving)
        moved += "".join("  " + c for c in comments)
        text = import_text(imp, kept) + "\n" + moved if kept else moved
        content = content[: imp.start] + text + content[imp.end :]
    return content


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


def private_removals(content: str, target: tuple[int, ...]) -> list[Note]:
    """Notes for private names the plugin uses that are gone in the target version."""
    aliases = ba_aliases(content)
    lines = content.split("\n")
    gone: dict[str, tuple[int, ...]] = {}
    for step in CHANGES:
        if step.version > target:
            break
        for dotted in step.removed:
            gone[dotted] = step.version
        for dotted in step.restored:
            gone.pop(dotted, None)
    words = set(re.findall(r'\w+', content))
    starts = line_starts(content)
    notes = []
    for dotted, version in gone.items():
        module, name = dotted.rsplit(".", 1)
        if name not in words:
            continue
        patterns = [pattern] if (pattern := name_pattern(aliases, dotted)) else []
        imported = {
            line_number(starts, imp.start)
            for imp in from_imports(content, module)
            if any(entry_name(e) == name for e in imp.entries)
        }
        if hits := hit_lines(lines, patterns, imported):
            notes.append(Note(hits, f"private {dotted} was removed in {version_str(version)}"))
    return notes


# Marks a MANUAL_FIXES entry for something that still works, until api 9 support ends
DEPRECATED = True

# (pattern, game version it became a problem in, what to do about it[, DEPRECATED])
MANUAL_FIXES = [
    (
        r'\bContextRef\((?!\s*\))',
        (1, 7, 20),
        "ContextRef() takes no arguments, use activity.context / session.context or ContextRef.empty()",
    ),
    (
        r'\btimetype\s*=',
        (1, 7, 20),
        "timers take no timetype: sim/base time is the default, real time is babase.AppTimer/apptimer",
    ),
    (
        r'\b_?ba(base)?\.internal\b',
        (1, 7, 20),
        "ba.internal is gone, its names moved to babase/bascenev1/bauiv1 or app.classic/app.plus",
    ),
    (r'\bSpecialChar\.ALIBABA_LOGO\b', (1, 7, 22), "SpecialChar.ALIBABA_LOGO was removed"),
    (r'\bSpecialChar\.OUYA_LOGO\b', (1, 7, 36), "SpecialChar.OUYA_LOGO was removed"),
    (r'\bSpecialChar\.GAME_CIRCLE_LOGO\b', (1, 7, 62), "SpecialChar.GAME_CIRCLE_LOGO was removed"),
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
    (
        r'\.set_main_menu_window\(',
        (1, 7, 37),
        "set_main_menu_window() is gone, the window has to be a bui.MainWindow:"
        " call self.main_window_replace(lambda: MyWin()) from one, or set_main_window()",
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
        DEPRECATED,
    ),
    (
        r'\bProfileBrowserWindow\b|bauiv1lib\.profile\.browser\b'
        r'|^\s*from bauiv1lib\.profile import .*\bbrowser\b',
        (1, 7, 54),
        "the profile browser is part of the doc-ui inventory now, open it with app.classic.profile_browser_window()",
    ),
    (
        r'\bInventoryWindow\b',
        (1, 7, 54),
        "InventoryWindow is a doc-ui page now: bauiv1lib.inventory.InventoryUIController",
    ),
    (
        r'\bCloudUIWindow\b|bauiv1lib\.cloudui\b|^\s*from bauiv1lib import .*\bcloudui\b',
        (1, 7, 54),
        "cloudui was replaced by doc-ui, subclass bauiv1lib.docui.DocUIController",
    ),
    (
        r'\bStoreButton\b|bauiv1lib\.store\.button\b|^\s*from bauiv1lib\.store import .*\bbutton\b',
        (1, 7, 37),
        "StoreButton was removed, the main menu's store button is bui.get_special_widget('store_button')",
    ),
    (
        r'\bPurchaseWindow\b|bauiv1lib\.purchase\b|^\s*from bauiv1lib import .*\bpurchase\b',
        (1, 7, 60),
        "PurchaseWindow was removed, purchases go through the doc-ui store: bauiv1lib.store.StoreUIController",
    ),
    (
        r'\bStoreBrowserWindow\b|bauiv1lib\.store\.(browser|item)\b'
        r'|^\s*from bauiv1lib\.store import .*\b(browser|item)\b',
        (1, 7, 60),
        "the store is a doc-ui page now: bauiv1lib.store.StoreUIController",
    ),
    (
        r'\bPlayWindow\b|bauiv1lib\.play\b|^\s*from bauiv1lib import .*\bplay\b',
        (1, 8, 0),
        "PlayWindow is a doc-ui page now: bauiv1lib.playdocui.PlayController",
    ),
    (
        r'\bPlaylistBrowserWindow\b|bauiv1lib\.playlist\.browser\b'
        r'|^\s*from bauiv1lib\.playlist import .*\bbrowser\b',
        (1, 8, 0),
        "PlaylistBrowserWindow is a doc-ui page now: bauiv1lib.playlist.browserdocui.PlaylistBrowserController",
    ),
    (
        r'\bAllSettingsWindow\b|bauiv1lib\.settings\.allsettings\b'
        r'|^\s*from bauiv1lib\.settings import .*\ballsettings\b',
        (1, 8, 0),
        "AllSettingsWindow is a doc-ui page now: bauiv1lib.settings.allsettingsdocui.AllSettingsController",
    ),
    (
        r'\b((Graphics|Audio|Advanced|Controls|Touchscreen)SettingsWindow|DevToolsWindow'
        r'|BenchmarksAndStressTestsWindow)\b',
        (1, 8, 0),
        "the settings windows are doc-ui pages now, use the controller in the same module"
        " (e.g. bauiv1lib.settings.graphics.GraphicsSettingsController)",
    ),
    (
        r'\bInboxWindow\b|bauiv1lib\.inbox\b|^\s*from bauiv1lib import .*\binbox\b',
        (1, 8, 0),
        "InboxWindow is a doc-ui page now: bauiv1lib.inboxui.InboxUIController",
    ),
    (
        r'\bChestWindow\d?\b|bauiv1lib\.chest\b|^\s*from bauiv1lib import .*\bchest\b',
        (1, 8, 0),
        "ChestWindow is a doc-ui page now: bauiv1lib.chestui.ChestUIController",
    ),
    (
        r'\bSendInfoWindow\b',
        (1, 8, 0),
        "SendInfoWindow is a doc-ui page now: bauiv1lib.sendinfo.SendInfoController",
    ),
    (r'\bNewsDisplay\b', (1, 8, 0), "NewsDisplay was removed from the main menu, with no replacement"),
    (
        r'\b(builtinassets|classicassets|stdassets)\b',
        (1, 8, 0),
        "the engine's asset wrapper modules are private now, bundle your own",
    ),
    (r'\bcharstr\b', (1, 8, 0), "use SpecialChar.X.value instead of charstr()", DEPRECATED),
    (
        r'\bDependencySet\b|\bdepsets\b',
        (1, 8, 0),
        "dependency sets are gone, Session() takes no depsets and activities declare no dependencies",
    ),
    (
        r'\bgetsimplesound\(',
        (1, 8, 0),
        "getsimplesound() is inert now (silent), load the sound with getsound() or an asset-package wrapper",
    ),
    (
        r'preview_texture_name',
        (1, 8, 0),
        "use get_preview_texture() / preview_texture= with an asset-package wrapper instead",
        DEPRECATED,
    ),
    (r'\bclipboard_get_text\(', (1, 8, 0), "use clipboard_get_text_async(callback) instead", DEPRECATED),
    (
        r'\.get_media\(|\bspaz_media\b',
        (1, 8, 1),
        "builtin characters are packaged spaz defs now, so their media parts are empty:"
        " restyle a spaz with SpazFactory.get_look_attrs(character)",
    ),
    (r'\bLstr\(', (1, 8, 0), "Lstr is going away when api 9 ends, migrate to LangStr", DEPRECATED),
    (
        r'\bdef (get_instance_display_string|get_instance_scoreboard_display_string'
        r'|get_display_string|get_team_display_string)\(',
        (1, 8, 0),
        "the game calls this with langstr=True and wants a babase.LangStr then"
        " (LangStr.from_text(...)), the Lstr only without it",
        DEPRECATED,
    ),
    (
        r'\bget_button_name\(',
        (1, 8, 0),
        "returns a LangStr now, it can't be used as an Lstr substitution",
    ),
]


def hit_lines(lines: list[str], patterns: list[str], extra: set[int] = frozenset()) -> list[int]:
    """Numbers of the non comment lines matching any pattern (or in extra)."""
    return [
        n
        for n, line in enumerate(lines, 1)
        if n in extra or (not line.lstrip().startswith('#') and any(re.search(p, line) for p in patterns))
    ]


def manual_fixes(content: str, target: tuple[int, ...]) -> list[Note]:
    notes = []
    lines = content.split("\n")
    for pattern, since, message, *deprecated in MANUAL_FIXES:
        if target < since:
            continue
        if hits := hit_lines(lines, [pattern]):
            notes.append(Note(hits, message, bool(deprecated)))
    # Outside of a game, timers need to be app timers since api 8
    if not is_game(content):
        scene = module_aliases(content, "bascenev1")
        timers = rf'\b({scene})\.(Timer|timer)\('
        if scene and re.search(timers, content):
            in_game = scope_of(content).lines
            lines = ["" if n in in_game else line for n, line in enumerate(lines, 1)]
            if hits := hit_lines(lines, [timers]):
                notes.append(
                    Note(
                        hits,
                        "game timers only work inside a game, for menu/ui/chat code use --app-timers"
                        " (a repeating bs.timer() needs a babase.AppTimer kept in a variable, by hand)",
                    )
                )
    for name, numbers in undefined_names(content).items():
        notes.append(Note(numbers, f"{name} is used but never defined or imported"))
    return notes


def undefined_names(content: str) -> dict[str, list[int]]:
    """Names content reads but never binds anywhere, nor are builtins: {name: line numbers}.

    One flat set for the whole file, so it only finds names missing
    everywhere (often a module or class an old port left behind, like a bare
    _ba). Annotations are skipped, and so are files with a star import.
    """
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return {}
    bound = set(dir(builtins)) | {"__file__", "__name__", "__doc__", "__spec__", "__loader__", "__package__", "__path__"}
    annotations = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and any(a.name == "*" for a in node.names):
            return {}
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            bound |= {(a.asname or a.name).split(".")[0] for a in node.names}
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bound.add(node.name)
        elif isinstance(node, ast.Name) and not isinstance(node.ctx, ast.Load):
            bound.add(node.id)
        elif isinstance(node, ast.arg):
            bound.add(node.arg)
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            bound |= set(node.names)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            bound.add(node.name)
        elif isinstance(node, (ast.MatchAs, ast.MatchStar)) and node.name:
            bound.add(node.name)
        for annotation in (
            getattr(node, "annotation", None) if isinstance(node, (ast.arg, ast.AnnAssign)) else None,
            getattr(node, "returns", None),
        ):
            if annotation is not None:
                annotations |= {id(n) for n in ast.walk(annotation)}
    missing: dict[str, set[int]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and node.id not in bound:
            if id(node) not in annotations:
                missing.setdefault(node.id, set()).add(node.lineno)
    return {name: sorted(lines) for name, lines in sorted(missing.items())}


@lru_cache(maxsize=4)
def parse(content: str) -> ast.Module | None:
    """ast.parse(content), or None if it doesn't parse. Cached, so callers must not edit the tree."""
    try:
        return ast.parse(content)
    except SyntaxError:
        return None


def is_game(content: str) -> bool:
    """True if the file exports a minigame or map, where game timers are right."""
    return bool(
        re.search(
            r'^\s*#\s*ba_meta export bascenev1\.(GameActivity|Map)\b', content, flags=re.MULTILINE
        )
    )


SCENE_LIBRARY = re.compile(r'(?:bascenev1lib|bastd)\.(?:actor|game)\b')


@lru_cache(maxsize=4)
def library_imports(tree: ast.Module) -> set[str]:
    """Names bound to bascenev1lib.actor/.game modules or their classes (api 7: bastd)."""
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and SCENE_LIBRARY.match(node.module or ""):
            imported |= {a.asname or a.name for a in node.names}
        elif isinstance(node, ast.Import):
            imported |= {a.asname for a in node.names if a.asname and SCENE_LIBRARY.match(a.name)}
    return imported


def scene_classes(content: str) -> dict[str, tuple[str, int, int]]:
    """Top level classes of game code: {name: ("actor" or "game", first line, last line)}.

    A class is game code when one of its bases is named like an activity,
    game, session, map, actor or bot, comes from bascenev1lib.actor/.game (or
    api 7's bastd), or is such a class in the same file. "game" is for
    activities, games, sessions and maps, "actor" for everything else.
    """
    tree = parse(content)
    if tree is None:
        return {}
    imported = library_imports(tree)
    classes: dict[str, tuple[str, int, int]] = {}
    pending = [n for n in tree.body if isinstance(n, ast.ClassDef)]
    while True:
        found = False
        for node in list(pending):
            for base in node.bases:
                text = re.sub(r'\[.*', "", ast.unparse(base))
                root, last = text.split(".")[0], text.split(".")[-1]
                game = bool(re.fullmatch(r'\w*(Activity|Game|Session|Map)', last))
                if root in classes:
                    kind = classes[root][0]
                elif game:
                    kind = "game"
                elif SCENE_LIBRARY.match(text) or root in imported or re.search(r'Actor|Spaz', last):
                    kind = "actor"
                else:
                    continue
                classes[node.name] = (kind, node.lineno, node.end_lineno)
                pending.remove(node)
                found = True
                break
        if not found:
            return classes


def actor_activity_refs(content: str) -> str:
    """Rewrites self._activity() to self.getactivity(doraise=False) in actor subclasses.

    Actor kept a private weakref to its activity as self._activity until
    1.8.0, which made it self._host (actors can live in a LocalDisplay now).
    getactivity(doraise=False) is the same: None once the activity is gone.
    Classes that set self._activity themselves keep it.
    """
    lines = content.split("\n")
    for kind, first, last in scene_classes(content).values():
        body = "\n".join(lines[first - 1 : last])
        if kind == "actor" and not re.search(r'\bself\._activity\s*=[^=]', body):
            body = re.sub(r'\bself\._activity\(\s*\)', "self.getactivity(doraise=False)", body)
            lines[first - 1 : last] = body.split("\n")
    return "\n".join(lines)


# Constructors that became keyword-only in 1.7.37: (module, class) ->
# (positional args still allowed, parameter order in 1.7.36)
KEYWORD_ONLY_INITS = {
    ("bascenev1lib.actor.spaz", "Spaz"): (
        0, "color highlight character source_player start_invincible can_accept_powerups powerups_expire demo_mode"
    ),
    ("bascenev1lib.actor.bomb", "Bomb"): (0, "position velocity bomb_type blast_radius bomb_scale source_player owner"),
    ("bascenev1lib.actor.bomb", "Blast"): (
        0, "position velocity blast_radius blast_type source_player hit_type hit_subtype"
    ),
    ("bascenev1lib.actor.flag", "Flag"): (0, "position color materials touchable dropped_timeout"),
    ("bascenev1lib.actor.spawner", "Spawner"): (0, "data pt spawn_time send_spawn_message spawn_callback"),
    ("bascenev1lib.actor.controlsguide", "ControlsGuide"): (0, "position scale delay lifespan bright"),
    ("bascenev1", "HitMessage"): (
        0,
        "srcnode pos velocity magnitude velocity_magnitude radius source_player kick_back flat_damage hit_type"
        " force_direction hit_subtype",
    ),
    ("bascenev1", "Session"): (1, "depsets team_names team_colors min_players max_players submit_score"),
    ("bascenev1lib.game.capturetheflag", "Team"): (
        0,
        "base_pos base_region_material base_region spaz_material_no_flag_physical spaz_material_no_flag_collide"
        " flagmaterial",
    ),
    ("bascenev1lib.actor.playerspaz", "PlayerSpaz"): (1, "player color highlight character powerups_expire"),
    ("bascenev1lib.actor.popuptext", "PopupText"): (1, "text position color random_offset offset scale"),
    ("bascenev1lib.actor.text", "Text"): (
        1,
        "text position h_align v_align color transition transition_delay flash v_attach h_attach scale"
        " transition_out_delay maxwidth shadow flatness vr_depth host_only front",
    ),
    ("bascenev1lib.actor.image", "Image"): (
        1,
        "texture position transition transition_delay attach color scale transition_out_delay mesh_opaque"
        " mesh_transparent vr_depth host_only front",
    ),
    ("bascenev1lib.actor.zoomtext", "ZoomText"): (
        2,
        "text position shiftposition shiftdelay lifespan flash trail h_align color jitter trailcolor scale"
        " project_scale tilt_translate maxwidth",
    ),
    ("bascenev1lib.game.elimination", "Icon"): (
        3, "player position scale show_lives show_death name_scale name_maxwidth flatness shadow"
    ),
    ("bascenev1", "Level"): (4, "name gametype settings preview_texture_name displayname"),
    ("baclassic", "Achievement"): (4, "name icon icon_color level_name award hard_mode_only"),
}


def keyword_only_args(content: str) -> str:
    """Passes the args past KEYWORD_ONLY_INITS' positional limit by name.

    Covers calls to the classes (through imports, module aliases or local
    subclasses without their own __init__), super().__init__(...) in their
    subclasses and Base.__init__(self, ...). Calls with *args are left alone.
    """
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return content
    modules = {module for module, _ in KEYWORD_ONLY_INITS}
    classes, aliases = {}, {}  # local name -> spec, local name -> module
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            for a in node.names:
                if (node.module, a.name) in KEYWORD_ONLY_INITS:
                    classes[a.asname or a.name] = KEYWORD_ONLY_INITS[(node.module, a.name)]
                elif f"{node.module}.{a.name}" in modules:
                    aliases[a.asname or a.name] = f"{node.module}.{a.name}"
        elif isinstance(node, ast.Import):
            aliases.update({a.asname: a.name for a in node.names if a.asname and a.name in modules})

    def spec_of(expr: ast.expr) -> tuple[int, str] | None:
        if isinstance(expr, ast.Name):
            return classes.get(expr.id)
        if isinstance(expr, ast.Attribute):
            module = ast.unparse(expr.value)
            return KEYWORD_ONLY_INITS.get((aliases.get(module, module), expr.attr))
        return None

    # local subclasses: calling one without its own __init__ calls the base's
    top_classes = [n for n in tree.body if isinstance(n, ast.ClassDef)]
    bases = {}
    for node in top_classes:
        base = next((spec for b in node.bases if (spec := spec_of(b))), None)
        if base is None:
            continue
        bases[node.name] = base
        own_init = any(isinstance(f, ast.FunctionDef) and f.name == "__init__" for f in node.body)
        classes.pop(node.name, None)
        if not own_init:
            classes[node.name] = base
    edits = []
    starts = line_starts(content)
    lines = content.split("\n")

    def offset(lineno: int, col: int) -> int:
        # ast columns count utf-8 bytes
        return starts[lineno - 1] + len(lines[lineno - 1].encode()[:col].decode(errors="ignore"))

    def convert(call: ast.Call, spec: tuple[int, str], skip: int) -> None:
        allowed, order = spec[0], spec[1].split()
        args = call.args[skip:]
        extra = args[allowed:]
        given = {k.arg for k in call.keywords}
        if not extra or any(isinstance(a, ast.Starred) for a in args) or len(args) > len(order):
            return
        if given & set(order[allowed : len(args)]):
            return
        previous = call.args[skip + allowed - 1] if skip + allowed else call.func
        for name, arg in zip(order[allowed:], extra):
            # the separator before the arg: the call's paren or the comma after the previous arg
            start = offset(previous.end_lineno, previous.end_col_offset)
            separator = content.index("(" if previous is call.func else ",", start)
            # the arg may sit in parens of its own, `name=` goes before them
            position = cursor = offset(arg.lineno, arg.col_offset)
            while cursor - 1 > separator and content[cursor - 1] in " \t\n(":
                cursor -= 1
                if content[cursor] == "(":
                    position = cursor
            edits.append((position, f"{name}="))
            previous = arg

    for cls in top_classes:
        for node in ast.walk(cls):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "__init__"):
                continue
            value = node.func.value
            if isinstance(value, ast.Call) and getattr(value.func, "id", None) == "super" and cls.name in bases:
                convert(node, bases[cls.name], 0)
            elif spec := spec_of(value):
                convert(node, spec, 1)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and (spec := spec_of(node.func)):
            convert(node, spec, 0)
    for position, text in sorted(set(edits), reverse=True):
        content = content[:position] + text + content[position:]
    return content


def party_window_open_states(content: str) -> str:
    """Rewrites set_party_window_open(True/False) statements in methods to a UIOpenState.

    The engine hands chat messages to the party window only while a
    UIOpenState('classicparty') is held (bauiv1lib.party.PartyWindow keeps
    one as self._uiopenstate), so a custom party window holds one while open.
    """
    if "set_party_window_open" not in content:
        return content
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return content
    statements = {}
    for cls in [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]:
        for method in [n for n in cls.body if isinstance(n, ast.FunctionDef)]:
            if not method.args.args or method.args.args[0].arg != "self":
                continue
            for node in ast.walk(method):
                call = node.value if isinstance(node, ast.Expr) else None
                func = getattr(call, "func", None)
                if (
                    isinstance(call, ast.Call)
                    and (getattr(func, "attr", None) or getattr(func, "id", None)) == "set_party_window_open"
                    and len(call.args) == 1
                    and not call.keywords
                    and isinstance(call.args[0], ast.Constant)
                    and isinstance(call.args[0].value, bool)
                    and node.lineno == node.end_lineno
                ):
                    statements[node.lineno] = call.args[0].value
    if not statements:
        return content
    lines = content.split("\n")
    for number, opened in statements.items():
        indent = re.match(r'[ \t]*', lines[number - 1]).group(0)
        comment = re.search(r'\s*#.*$', lines[number - 1])
        state = "bui.UIOpenState('classicparty')" if opened else "None"
        lines[number - 1] = f"{indent}self._uiopenstate = {state}{comment.group(0) if comment else ''}"
    content = "\n".join(lines)
    if "bui.UIOpenState" in content:
        content, alias = module_alias(content, "bauiv1")
        content = content.replace("bui.UIOpenState('classicparty')", f"{alias}.UIOpenState('classicparty')")
    return content


class Scope:
    """Where content's game code is: the whole file when it exports a game or map,
    else scene_classes() and game_function_lines().

    Use scope_of() to share one per content.
    """

    def __init__(self, content: str):
        self.starts = line_starts(content)
        if is_game(content):
            self.lines = None
            return
        classes = scene_classes(content)
        self.lines = {n for _, first, last in classes.values() for n in range(first, last + 1)}
        if tree := parse(content):
            self.lines |= game_function_lines(tree, classes)

    def in_game(self, offset: int) -> bool:
        return self.lines is None or line_number(self.starts, offset) in self.lines


scope_of = lru_cache(maxsize=4)(Scope)


FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)


def own_nodes(scope: ast.AST):
    """Nodes inside scope that aren't inside a function, lambda or class nested in it (those come themselves)."""
    pending = list(ast.iter_child_nodes(scope))
    while pending:
        node = pending.pop()
        yield node
        if not isinstance(node, (*FUNCTIONS, ast.Lambda, ast.ClassDef)):
            pending += ast.iter_child_nodes(node)


def game_function_lines(tree: ast.Module, classes: dict[str, tuple[str, int, int]]) -> set[int]:
    """Lines of functions that run inside a game, wherever they are defined.

    Those that make nodes, get the activity or take one as an `activity`
    argument, and those patched onto a scene class (`Spaz.curse = f`, also
    through a wrapper: `Bomb.__init__ = wrap(Bomb.__init__)` marks all of wrap).
    """
    imported = library_imports(tree)

    def scene_target(expr: ast.expr) -> bool:
        text = ast.unparse(expr)
        parts = text.split(".")
        return bool(SCENE_LIBRARY.match(text)) or any(
            p in classes or p in imported or re.fullmatch(r'\w*(Activity|Game|Session|Map|Actor|Spaz|Bot)', p)
            for p in parts
        )

    # by scope: the patched name is a function defined next to (or above) the assignment
    patched = set()

    def visit(scope: ast.AST, visible: dict[str, ast.AST]) -> None:
        visible = visible | {f.name: f for f in scope.body if isinstance(f, FUNCTIONS)}
        for node in own_nodes(scope):
            if isinstance(node, (*FUNCTIONS, ast.ClassDef)):
                visit(node, visible)
            elif isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Attribute) and scene_target(t.value) for t in node.targets
            ):
                value = node.value.func if isinstance(node.value, ast.Call) else node.value
                if isinstance(value, ast.Name) and value.id in visible:
                    patched.add(id(visible[value.id]))

    visit(tree, {})

    def own_calls(function: ast.AST) -> set[str]:
        """Names of what function calls itself, not in functions nested in it."""
        return {
            getattr(n.func, "attr", None) or getattr(n.func, "id", None)
            for n in own_nodes(function)
            if isinstance(n, ast.Call)
        }

    lines = set()
    for node in ast.walk(tree):
        if not isinstance(node, FUNCTIONS):
            continue
        args = node.args.posonlyargs + node.args.args + node.args.kwonlyargs
        if (
            id(node) in patched
            or own_calls(node) & {"newnode", "getactivity"}
            or any(a.arg == "activity" for a in args)
        ):
            lines.update(range(node.lineno, node.end_lineno + 1))
    return lines


def line_starts(content: str) -> list[int]:
    """Offsets where each line starts, for line_number()."""
    return [0] + [m.end() for m in re.finditer(r'\n', content)]


def line_number(starts: list[int], offset: int) -> int:
    return bisect_right(starts, offset)


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


# _ba functions that moved to _bainternal (ba.internal) in 1.7.7
BAINTERNAL_1_7_7 = """
    add_transaction game_service_has_leaderboard get_master_server_address get_news_show get_price
    get_public_login_id get_purchased get_purchases_state get_v1_account_display_string
    get_v1_account_misc_read_val get_v1_account_misc_read_val_2 get_v1_account_misc_val
    get_v1_account_name get_v1_account_state get_v1_account_state_num get_v1_account_ticket_count
    get_v1_account_type get_v2_fleet have_outstanding_transactions in_game_purchase is_blessed
    mark_config_dirty power_ranking_query purchase report_achievement reset_achievements
    restore_purchases run_transactions sign_in_v1 sign_out_v1 submit_score tournament_query
""".split()

# Native functions api 7's _ba had that 1.7.20 moved to another native module
# (names in both _bascenev1 and _bauiv1 follow the plugin type, see fix_misplaced_names)
API7_NATIVE_MOVES = {
    "_bascenev1": """
        camerashake capture_gamepad_input capture_keyboard_input chatmessage client_info_query_response
        connect_to_party disconnect_client disconnect_from_host emitfx end_host_scanning getactivity
        get_chat_messages get_client_public_device_uuid get_collision_info get_configurable_game_pads
        get_connection_to_host_info getdata get_foreground_host_activity get_foreground_host_session
        get_game_port get_game_roster getinputdevice get_local_active_input_devices_count getnodes
        get_package_data get_package_sound get_package_texture get_public_party_enabled
        get_public_party_max_size get_random_names get_replay_speed_exponent getsession
        get_ui_input_device have_connected_clients have_touchscreen_input host_scan_cycle is_in_replay
        ls_input_devices ls_objects newactivity new_host_session newnode new_replay_session printnodes
        register_activity register_session release_gamepad_input release_keyboard_input
        reset_random_player_names set_admins set_authenticate_clients set_debug_speed_exponent
        set_enable_default_kick_voting set_map_bounds set_master_server_source set_public_party_enabled
        set_public_party_max_size set_public_party_name set_public_party_queue_enabled
        set_public_party_stats_url set_replay_speed_exponent set_touchscreen_editing time timer
    """.split(),
    "_bauiv1": """
        back_press buttonwidget can_show_ad checkboxwidget columnwidget console_print containerwidget
        focus_window get_qrcode_texture get_special_widget has_video_ads have_incentivized_ad
        hscrollwidget imagewidget is_party_icon_visible open_file_externally open_url rowwidget
        scrollwidget set_party_icon_always_visible set_party_window_open show_ad show_ad_2
        show_app_invite show_online_score_ui textwidget uibounds widget
    """.split(),
    "_baclassic": ["value_test"],
}

# Private modules of bascenev1 at 1.7.20 (api 7 had them in ba)
SCENE_PRIVATE_MODULES = """
    _activity _activitytypes _actor _campaign _collision _coopgame _coopsession _debug _dependency
    _dualteamsession _featureset _freeforallsession _gameactivity _gameresults _gameutils _level
    _lobby _map _messages _multiteamsession _music _nodeactor _player _playlist _powerup _profile
    _score _session _settings _stats _teamgame _team
""".split()


def operand(text: str) -> str:
    """text, in parens unless it can take `.attr` as it is."""
    return text if simple_expression(text) else f"({text})"


def simple_expression(text: str) -> bool:
    """True if text can take `.attr` or `(...)` without parens around it."""
    try:
        node = ast.parse(text.strip(), mode="eval").body
    except SyntaxError:
        return False
    return isinstance(node, (ast.Name, ast.Attribute, ast.Call, ast.Subscript))


def timer_repeats(args: list[str]) -> bool:
    """True if timer() call args repeat it (repeat=..., or a third positional arg)."""
    return len(args) > 2 or any(re.match(r'\s*repeat\s*=', a) for a in args)


def drop_timetypes(content: str) -> str:
    """Removes api 7 timetype args from timers and time() (after ba.X became babase.X/bascenev1.X).

    Real time is babase.AppTimer/apptimer/apptime now and sim/base time the
    default. A repeating timer() stays a game timer, apptimer() can't repeat.
    timetype values baport can't read are left for the timetype note.
    """

    def call(match: re.Match, args: list[str] | None) -> str | None:
        if args is None:
            return None
        kept, real = [], False
        for arg in args:
            value = re.sub(r'^\s*timetype\s*=', "", arg)
            kind = re.fullmatch(r'\s*(?:[\w.]+\.)?TimeType\.(REAL|SIM|BASE)\s*', value)
            if kind:
                real = real or kind.group(1) == "REAL"
            else:
                kept.append(arg.strip())
        if len(kept) == len(args):
            return None
        name, kind = match.group(1), match.group(2)
        if real and not (kind == "timer" and timer_repeats(kept)):
            name = {"Timer": "babase.AppTimer", "timer": "babase.apptimer", "time": "babase.apptime"}[kind]
        return f"{name}({', '.join(kept)})"

    return replace_calls(content, r'(?<![\w.])((?:\w+\.)*(Timer|timer|time))\s*\(', call)


def playsound_calls(content: str) -> str:
    """Rewrites api 7 babase.playsound(sound, ...) calls to sound.play(...), same other args."""

    def call(_: re.Match, args: list[str] | None) -> str | None:
        if not args:
            return None
        args = [a.strip() for a in args]
        sounds = [a for a in args if re.match(r'sound\s*=', a)]
        sound = re.sub(r'^sound\s*=\s*', "", sounds[0]) if sounds else args[0]
        rest = [a for a in args if a not in (sounds[:1] or args[:1])]
        return f"{operand(sound)}.play({', '.join(rest)})"

    def callback(match: re.Match, args: list[str] | None) -> str | None:
        if not args or len(args) < 2 or not re.fullmatch(r'\s*_?babase\.playsound\s*', args[0]):
            return None
        played = [operand(args[1].strip()) + ".play"] + [a.strip() for a in args[2:]]
        return f"{match.group(0)}{', '.join(played)})"

    # passed as a callback: Call(babase.playsound, sound, ...) is Call(sound.play, ...)
    content = replace_calls(content, r'\b(?:\w+\.)?(?:Weak)?Call(?:Partial)?\s*\(', callback)
    return replace_calls(content, r'(?<![\w.])_?babase\.playsound\s*\(', call)


def campaign_lookups(content: str) -> str:
    """Rewrites api 7's getcampaign (ba._campaign's or ba.internal's, after ba.X became babase.X) to babase.app.classic.getcampaign.

    Runs before babase._campaign becomes bascenev1._campaign, which has no getcampaign.
    """
    content, bare = take_imported(content, ("babase._campaign", "babase.internal"), "getcampaign")
    names = "|".join(filter(None, [r'(?<![\w.])babase\.(?:_campaign|internal)\.getcampaign', bare]))
    if not re.search(rf'(?:{names})\b', content):
        return content
    content = re.sub(rf'(?:{names})\b(?!\s*=[^=])', "babase.app.classic.getcampaign", content)
    return ensure_import(content, "babase")


def context_refs(content: str) -> str:
    """Rewrites api 7 babase.Context(x) calls (after ba.X became babase.X, _ba.X _babase.X) to api 8.

    ContextRef() takes no args: 'ui' and 'empty' are ContextRef.empty() now,
    'current' is ContextRef(), and an activity or session gives its .context.
    Other references to the class are (_)babase.ContextRef.
    """

    def call(match: re.Match, args: list[str] | None) -> str | None:
        if args is None or len(args) != 1:
            return None
        arg = args[0].strip()
        try:
            node = ast.parse(arg, mode="eval").body
        except SyntaxError:
            return None
        if isinstance(node, ast.Constant) and node.value in ("ui", "UI", "empty"):
            return f"{match.group(1)}.ContextRef.empty()"
        if isinstance(node, ast.Constant) and node.value == "current":
            return f"{match.group(1)}.ContextRef()"
        return f"{operand(arg)}.context"

    content = replace_calls(content, r'(?<![\w.])(_?babase)\.Context\s*\(', call)
    return re.sub(r'(?<![\w.])(_?babase)\.Context\b', r'\1.ContextRef', content)


def print_to_logging(content: str, pattern: str, kind: str) -> str:
    """Rewrites the print_exception()/print_error() calls pattern matches (up to their `(`) to logging.

    The old functions printed str() of their positional args joined by spaces
    and took once=True (print once per call location), which logging doesn't
    have, so once is dropped and extra args become a '%s %s' format. Calls
    baport can't split keep their args.
    """

    def call(_: re.Match, args: list[str] | None) -> str:
        if args is None:
            return f"logging.{kind}("
        args = [a.strip() for a in args if not re.match(r'\s*once\s*=', a)] or ["'Error'"]
        if len(args) > 1:
            args = [repr(" ".join(["%s"] * len(args)))] + args
        return f"logging.{kind}({', '.join(args)})"

    return replace_calls(content, pattern, call)


def use_app_timers(content: str) -> str:
    """Turns game timers into app timers, for code that runs outside of games.

    bs.Timer() becomes babase.AppTimer() (same arguments). bs.timer() becomes
    babase.apptimer(), unless it repeats, since apptimer() can't. Timers in
    game code (Scope: game, session, actor or bot classes, functions that run
    in a game) are left alone.
    """
    scene = module_aliases(content, "bascenev1")
    if not scene or not re.search(rf'\b({scene})\.(Timer|timer)\(', content):
        return content
    content, base = module_alias(content, "babase")
    scope = scope_of(content)

    def convert(match: re.Match) -> str:
        if scope.in_game(match.start()):
            return match.group(0)
        if match.group(1) == "Timer":
            return f"{base}.AppTimer("
        parsed = call_args(content, match.end())
        if parsed is None:
            return match.group(0)
        return match.group(0) if timer_repeats(parsed[1]) else f"{base}.apptimer("

    return re.sub(rf'\b(?:{scene})\.(Timer|timer)\(', convert, content)


def source_api(content: str) -> int | None:
    """The plugin's `# ba_meta require api`, or None for api 6/7.

    Helper modules have no such line, so their imports decide: only api 8+
    modules (babase, bascenev1, ...) means 8, which keeps an already ported
    helper from getting the api 7 port again.
    """
    match = re.search(r'^\s*#\s*ba_meta require api (\d+)', content, flags=re.MULTILINE)
    if match:
        return int(match.group(1))
    modules = {m.split(".")[0] for m in re.findall(r'^\s*(?:import|from)\s+([\w.]+)', content, flags=re.MULTILINE)}
    if modules & {"ba", "_ba", "bastd", "_bainternal"}:
        return None
    return 8 if modules & {"babase", "_babase", "bascenev1", "_bascenev1", "bauiv1", "_bauiv1"} else None


def target_api(target: tuple[int, ...]) -> int:
    return max(api for api, first in API_FIRST_VERSION.items() if target >= first)


MARKER = re.compile(r'[ \t]*# baport(?:, deprecated)?: [^\n]*$', flags=re.MULTILINE)


def mark_notes(content: str, notes: list[Note]) -> str:
    """Appends a `# baport: ...` comment with the notes to each line they are about.

    Lines that can't take a comment (inside a string, or continued with a
    backslash) are left alone. MARKER finds them again, so a new port
    replaces them.
    """
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(content).readline))
    except (tokenize.TokenError, SyntaxError):
        return content
    # a NEWLINE/NL token on a line means it ends outside of any string
    open_ends = {t.start[0] for t in tokens if t.type in (tokenize.NEWLINE, tokenize.NL)}
    on_line: dict[int, list[Note]] = {}
    for note in notes:
        for n in note.lines:
            if n in open_ends:
                on_line.setdefault(n, []).append(note)
    lines = content.split("\n")
    for n, here in on_line.items():
        # a line with only deprecated notes says so once, mixed ones per note
        if all(note.deprecated for note in here):
            tag, texts = "baport, deprecated", [note.message for note in here]
        else:
            here.sort(key=lambda note: note.deprecated)
            tag, texts = "baport", [("deprecated: " if note.deprecated else "") + note.message for note in here]
        lines[n - 1] = f"{lines[n - 1].rstrip()}  # {tag}: {'; '.join(texts)}"
    return "\n".join(lines)


def print_notes(notes: list[Note]) -> None:
    def shown(numbers: list[int]) -> str:
        return ", ".join(map(str, numbers[:10])) + (", ..." if len(numbers) > 10 else "")

    for deprecated, heading in (
        (False, "must fix (fails on load or when that code runs):"),
        (True, "deprecated (works until api 9 support ends):"),
    ):
        group = [n for n in notes if n.deprecated == deprecated]
        if group:
            print(f"  {heading}")
        for note in sorted(group, key=lambda n: n.lines[0]):
            print(f"    line {shown(note.lines)}: {note.message}")


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
    # markers from an earlier port go, main() marks the new notes
    content = MARKER.sub("", content)
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
        "--mark",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Mark each line baport couldn't port with a '# baport: ...' comment, porting again "
        "replaces them (default: on, --no-mark to leave them out and remove old ones).",
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
        notes = manual_fixes(new_text, args.version)
        if args.private:
            notes += private_removals(new_text, args.version)
        if args.mark:
            new_text = mark_notes(new_text, notes)
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
        print_notes(notes)
    if failed:
        exit(1)


if __name__ == "__main__":
    main()
