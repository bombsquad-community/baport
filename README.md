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

`-t` is only needed when porting from api 6/7. Plugins already on api 8 or 9 can skip it.

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

Minigames and maps are left alone even with this flag. A repeating `bs.timer(..., repeat=True)` can't be converted automatically (`babase.apptimer` can't repeat), so baport points those out for you to change by hand.

### Other options

- `-n`/`--dry-run` prints a diff instead of writing the file.
- After porting, baport lists things it couldn't convert automatically, with line numbers.

## Checking ported plugins

`check_plugins.py` loads plugins in a real BombSquad install (the flatpak by default) with a throwaway config and mods folder, and reports import errors, names that don't exist, and deprecated APIs:

```
$ python check_plugins.py some_plugin.py other_plugin.py
```

A game window opens for a few seconds while it runs. Use `--game-cmd` to point it at a different BombSquad install.
