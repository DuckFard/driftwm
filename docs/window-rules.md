# Window Rules

Window rules let you apply per-window overrides based on a window's identity.
Rules are declared as `[[window_rules]]` sections in your config file.

## How matching works

**All matching rules are applied, not just the first one.** Rules are processed
in config order and merged together:

- **Scalar fields** (`decoration`, `opacity`, `position`, `size`,
  `border_width`, `border_color`, `border_color_focused`, `corner_radius`,
  `shadow`): last-wins — a later rule overrides an earlier one.
- **Boolean flags** (`widget`, `blur`): sticky-on — once set by
  any matching rule, the flag stays set regardless of later rules.
- **`pass_keys`**: `All` is sticky-on; `Only` lists are unioned across
  rules (see [pass_keys details](#pass_keys-details)).

This lets you compose independent rules for the same window:

```toml
# Rule 1: make kitty blur its background
[[window_rules]]
app_id = "kitty"
blur   = true

# Rule 2: also make it semi-transparent (blur from Rule 1 is kept)
[[window_rules]]
app_id  = "kitty"
opacity = 0.85
```

## Match criteria

At least one criterion is required. All specified criteria must match.

| Field    | Matches                                                                                                                 |
| -------- | ----------------------------------------------------------------------------------------------------------------------- |
| `app_id` | Wayland app_id (X11 apps via xwayland-satellite arrive with `app_id` set from `WM_CLASS` instance, typically lowercase) |
| `title`  | Window title                                                                                                            |

### Finding a window's identifiers

```sh
cat $XDG_RUNTIME_DIR/driftwm/state   # look for the "windows=" line
```

To get titles and app ids of all current non-widget windows:

```sh
sed -n 's/^windows=//p' $XDG_RUNTIME_DIR/driftwm/state | \
jq '.[] | select(.is_widget == false) | {app_id, title}'
```

## Pattern syntax

All match fields support three syntaxes:

| Syntax       | Example                | Meaning                                 |
| ------------ | ---------------------- | --------------------------------------- |
| Exact string | `"kitty"`              | Exact match (case-sensitive)            |
| Glob         | `"steam_app_*"`        | `*` matches any sequence of chars       |
| Regex        | `"/^steam_app_\\d+$/"` | Full regular expression (wrap in `/…/`) |

Multiple `*` wildcards are allowed in glob patterns: `"*terminal*"`.

Regex patterns use the `regex` crate (RE2-compatible, no backreferences).

## Effect fields

| Field                  | Type                     | Default   | Description                                                                                                                                              |
| ---------------------- | ------------------------ | --------- | -------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `position`             | `[x, y]`                 | —         | Place window at canvas coordinates (window center, Y-up)                                                                                                 |
| `size`                 | `[w, h]`                 | —         | Force window dimensions in pixels                                                                                                                        |
| `widget`               | `bool`                   | `false`   | Pin window: immovable, below normal windows, excluded from navigation/alt-tab                                                                            |
| `decoration`           | string                   | inherited | Override decoration mode (see below)                                                                                                                     |
| `blur`                 | `bool`                   | `false`   | Blur compositor background behind this window                                                                                                            |
| `opacity`              | `0.0`–`1.0`              | `1.0`     | Window transparency (1.0 = fully opaque)                                                                                                                 |
| `border_width`         | px                       | inherited | Border width override. Set on a `decoration = "none"` rule to give a widget a border. Set to `0` to disable the border even when global width is `> 0`.  |
| `border_color`         | `"#rrggbb[aa]"`          | inherited | Per-window unfocused border color                                                                                                                        |
| `border_color_focused` | `"#rrggbb[aa]"`          | inherited | Per-window focused border color                                                                                                                          |
| `corner_radius`        | px                       | inherited | Per-window corner radius override. Affects content clip, border shape, and shadow. On `decoration = "none"`, only the border shape uses it.              |
| `shadow`               | `bool`                   | mode-dep. | Per-window shadow toggle. Default: on for `client`/`server`/`minimal`, off for `none`. Set `true` on `decoration = "none"` to give a widget a shadow.    |
| `pass_keys`            | `bool` or `["combo", …]` | `false`   | Forward keys to the app — see below                                                                                                                      |

### `decoration` values

| Value          | Description                                        |
| -------------- | -------------------------------------------------- |
| `"client"`     | CSD — client draws its own titlebar (default)      |
| `"server"`     | SSD — driftwm draws a titlebar with a close button |
| `"minimal"`    | SSD — no titlebar, but shadow + corner clipping    |
| `"none"`       | SSD — completely bare, no chrome at all            |

### `pass_keys` details

`pass_keys` controls which compositor keybindings are forwarded to the focused
window instead of being handled by the compositor:

| Value                 | Behaviour                                                                         |
| --------------------- | --------------------------------------------------------------------------------- |
| `false` (or omit)     | Compositor handles all keybindings normally (default)                             |
| `true`                | **All** keys forwarded — no compositor shortcuts fire while this window has focus |
| `["mod+q", "ctrl+q"]` | **Only** the listed combos are forwarded; all other shortcuts stay active         |

VT switching (`Ctrl+Alt+F1`–`F12`) **always stays in the compositor** regardless
of `pass_keys`.

Key combo syntax is the same as in `[keybindings]`: `mod+key`, `ctrl+shift+key`, etc.

When multiple rules match the same window:

- `true` is sticky-on: if **any** rule sets `pass_keys = true`, the result is `true`.
- `["combo", …]` lists are **unioned** across all matching rules.
- `true` overrides a list: if one rule says `true` and another says `["mod+q"]`, the result is `true`.

## Examples

### Desktop widget (pinned clock/info panel)

```toml
[[window_rules]]
app_id     = "my-widget"
position   = [0, 0]
widget     = true
decoration = "none"
```

### Transparent blurred terminal

```toml
[[window_rules]]
app_id  = "kitty"
opacity = 0.85
blur    = true
```

### Game: pass all keys through (Wayland-native)

```toml
[[window_rules]]
app_id    = "steam_app_*"
pass_keys = true
```

### Game: only let specific keys through

Keep `mod+q` and other compositor shortcuts active, but pass `ctrl+q` to the game:

```toml
[[window_rules]]
app_id    = "factorio"
pass_keys = ["ctrl+q", "ctrl+s"]
```

### Match any Steam game by regex

```toml
[[window_rules]]
app_id    = "/^steam_app_\\d+$/"
pass_keys = true
```

### Force size and position for a floating panel

```toml
[[window_rules]]
app_id   = "myapp-panel"
size     = [400, 800]
position = [960, 0]
widget   = true
```

### Match by both app_id and title

Both criteria must match simultaneously:

```toml
[[window_rules]]
app_id = "firefox"
title  = "Picture-in-Picture"
widget = true
```

### Composing rules (multi-rule merge)

```toml
# All three rules below apply to the same kitty window and are merged:

[[window_rules]]
app_id = "kitty"
blur   = true        # sticky-on: cannot be unset by later rules

[[window_rules]]
app_id  = "kitty"
opacity = 0.85       # blur from above is preserved

[[window_rules]]
title   = "*nvim*"   # title match narrows to nvim windows only
opacity = 1.0        # override opacity for nvim (blur still applies)
```

### Widget with a custom border and shadow

`decoration = "none"` skips all compositor chrome by default. Window rules can
add it back per-app:

```toml
[[window_rules]]
app_id               = "my-clock"
widget               = true
decoration           = "none"
border_width         = 2
border_color         = "#5c5c5c"
border_color_focused = "#7aa2f7"
corner_radius        = 8       # rounded border shape
shadow               = true    # off by default for decoration = "none"
```

### Disable shadow on a specific app

```toml
[[window_rules]]
app_id = "firefox"
shadow = false
```

### Suppress iced/libcosmic utility popups

Some apps (cosmic-term, etc.) open small utility windows that share the main
app_id but have a generic title:

```toml
[[window_rules]]
title  = "winit window"
widget = true
```

## Debugging

Enable debug logging to see which rules matched a window at map time:

```sh
RUST_LOG=debug driftwm 2>&1 | grep -i "window rule\|app_id"
```
