# Needy Streamer Overload Widgets

This directory integrates a Linux-native port of
[`lezzthanthree/Needy-Streamer-Overload`](https://github.com/lezzthanthree/Needy-Streamer-Overload)
with driftwm's existing widget model.

It is not a localhost website, Firefox tab, WebKit dashboard, or standalone web
UI. Each skin is a native GTK/Cairo Wayland toplevel. driftwm turns those
toplevels into borderless, movable canvas windows with native window rules. The
decorative sidebar background windows are intentionally not launched.

## Run

From an installed driftwm flake package:

```sh
driftwm-nso
```

Launch one widget:

```sh
driftwm-nso-widget --widget media-player
```

From the source tree:

```sh
extras/nso/scripts/launch.sh
python3 extras/nso/scripts/nso_widget.py --widget welcome
```

Known widget keys:

```sh
driftwm-nso-widget --list
```

## Enable In driftwm

Merge [`config/driftwm.toml`](config/driftwm.toml) into
`~/.config/driftwm/config.toml`.

The important pieces are:

- `autostart = ["driftwm-nso"]`
- a shared `[[window_rules]]` block matching `app_id = "dev.driftwm.nso.*"`
- `decoration = "none"`
- explicit `position = [x, y]` rules for every widget

After editing config:

```sh
driftwm --check-config
driftwm msg action reload-config
```

Autostart changes only apply on a compositor restart. You can run
`driftwm-nso` manually for the current session.

NSO windows can be moved without holding Alt by dragging their titlebar or an
unused part of the widget surface. Buttons, stickers, media controls, launcher
icons, and editable note text keep their normal click behavior.

## Configuration

Default settings live in [`config/default.toml`](config/default.toml).
User overrides are read from:

```text
~/.config/driftwm/nso.toml
```

Start from [`config/nso.example.toml`](config/nso.example.toml). The default UI
scale is `1.5`, so each native widget is rendered about 50% larger while keeping
the upstream Rainmeter coordinate system for drawing and clicks. Webcam/Ame also
has its own `[ame] scale` multiplier, which defaults to `1.5` and makes only that
widget another 50% larger.

Persistent state is stored in:

```text
~/.local/state/driftwm-nso/state.json
~/.local/share/driftwm-nso/quick-notes.txt
```

Weather follows the original
`@Resources/Settings/Calendar/Settings.inc` model: use an OpenWeatherMap city ID
as `LocationCode`, your OpenWeatherMap key as `ApiKey`, and `Units` as
`metric`, `imperial`, or `standard`.

```toml
[ui]
scale = 1.5

[ame]
scale = 1.5

[calendar]
TimeFormat1224 = 12

[calendar.weather]
enabled = true
LocationCode = "2643743"
ApiKey = "..."
Units = "metric"
```

`Paste Here!` is treated as unset. Once real `LocationCode` and `ApiKey` values
exist, the widget will fetch weather even if an older copied config still has
`enabled = false`.

`NSO_OPENWEATHER_CITY_ID` and `NSO_OPENWEATHER_API_KEY` are also accepted.

## Widget Mapping

| Original skin | driftwm widget | Status |
| --- | --- | --- |
| Welcome | `welcome` | Faithful approximation with mature-content warning and native launch buttons |
| Task Manager | `task-manager` | Live Linux CPU/RAM/disk/uptime with threshold colors |
| Ame | `ame` | Original frame/background/sprites with load and time-of-day state |
| JINE | `jine` | Original JINE frame, stickers, and raw dialogue text |
| Social Media | `social-media` | Original timeline art, tweet images, counters, and next button |
| Media Player | `media-player` | MPRIS via `playerctl`, cover art, controls, NSO title banners |
| Calendar | `calendar` | Date/time, day/night icon fallback, optional weather hover |
| Desktop Icons | `desktop-icons` | Native launchers using the original icon set |
| Quick Notes | `quick-notes` | Editable persistent title/body |
| Sidebars | none | Disabled: the two decorative background windows were removed from the launcher surface |
| Medications | `medications` | Compact Depaz fidget approximation |

## Unsupported Exact Behaviors

Rainmeter `.ini` execution, Rainmeter bangs, Windows-only plugins, and exact
Rainmeter mouse-over measure timing cannot run on Linux. The replacements are
native driftwm/Wayland windows, `/proc` system readers, `playerctl` MPRIS,
freedesktop trash paths, `gio`, `xdg-open`, and local GTK/Cairo interactions.

The upstream assets and raw data are kept under
[`assets/Needy-Streamer-Overload`](assets/Needy-Streamer-Overload) to preserve
names and roles.
