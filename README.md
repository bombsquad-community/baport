# baport

Port Bombsquad plugins from API 6, 7 and 8 to API 9, up to the latest game version (1.8.x).

## Usage

For client side plugins/minigames:

```
$ python baport.py -t client -f some_plugin.py
```

For server side plugins/minigames:

```
$ python baport.py -t server -f some_plugin.py
```

`-t` is only needed when porting from api 6/7. Plugins already on api 8 or 9 can skip it. It decides whether `screenmessage()` stays local (client) or goes to every player (server), and which module loads sounds and textures in menu and UI code. Game code (minigames, maps, actors) always gets the game's `bs.getsound()`/`bs.gettexture()`.

### Picking a game version

Changes are applied one game version at a time, following the BombSquad changelog. A plugin on api 8 gets the changes from 1.7.20 (the first api 8 release), then 1.7.21, and so on through the last api 8 release, then the same for api 9, stopping at the version you port to. After porting, baport says which versions' changes touched the file.

By default plugins are ported to the newest game version baport knows about. To stop at an older one, so only APIs that exist in it are used, pass `-v`:

```
$ python baport.py -v 1.7.40 -f some_plugin.py
```

Or pick an api version with `--api`, which ports to the last game version with that api (1.7.36 for api 8):

```
$ python baport.py --api 8 -f some_plugin.py
```

To see every change baport knows about and the version it comes from:

```
$ python baport.py --list-changes
```

Running baport again on an already ported plugin is safe, it only moves it off APIs deprecated by newer game versions.

### Plugins that run outside of games

Game timers (`bs.Timer`, `bs.timer`) only work while a game is running. If your plugin adds things to menus, the party window, chat, etc., pass `--app-timers` to turn them into app timers (`babase.AppTimer`, `babase.apptimer`), which work anywhere:

```
$ python baport.py --app-timers -f party_window_plugin.py
```

Minigames and maps are left alone even with this flag, and so are timers inside classes deriving from a game, activity, session, map, actor or bot (e.g. a plugin that adds a minigame or a bot class), and inside functions that run in a game: ones patched onto such classes (`Spaz.__init__ = new_init`), ones that make nodes or call `getactivity()`, and ones taking an `activity` argument. A repeating `bs.timer(..., repeat=True)` can't be converted automatically (`babase.apptimer` can't repeat), so baport points those out for you to change by hand.

### Private api

Plugins sometimes use private parts of the game (`_babase`, `babase._apputils`, ...). These change without a changelog entry, so baport keeps a list of when private names moved or were removed. This is on by default, `--no-private` turns it off.

### Other options

- `-n`/`--dry-run` prints a diff instead of writing the file.
- After porting, baport lists things it couldn't convert automatically, with line numbers. They come in two groups: "must fix" (fails on load or when that code runs) and "deprecated" (works until api 9 support ends, like `Lstr`).
- Each of those lines also gets a `# baport: ...` comment (`# baport, deprecated: ...` for deprecated ones), so you can find them in your editor. Porting again replaces the markers. `--no-mark` leaves them out, and removes ones an earlier port added.

## Development

baport has no dependencies, and runs on Python 3.10 or newer. `tests/run.py` ports every plugin in `tests/cases` and compares the result with what it ported to before. Each ported file must also compile, and porting it again must change nothing:

```
$ python tests/run.py               # every case
$ python tests/run.py autorun api8  # cases whose name contains one of these
$ python tests/run.py --update      # accept the current output
```

After a change that is meant to alter the output, run `--update` and review the `tests/cases` diff in git before committing. Each case is a directory with `input.py`, an optional `args` file (default `-t client`), and the recorded `expected.py` and `notes.txt`. Most inputs are plugins from [plugin-manager](https://github.com/bombsquad-community/plugin-manager), as they were before being ported to api 8 or 9, under its MIT license (see `tests/cases/LICENSE`). Only plugins that don't state other terms of their own are used. The `api6_basics`, `api7_rules`, `api8_rules` and `api8_rules_unmarked` cases are handwritten, for rules no real plugin covers.
