---
name: omarchy-display-sizing
description: >
  Size an Omarchy desktop's display in one deterministic run: monitor scale derived from the
  panel's physical width, bar icons and height, GTK interface font (which also sizes Chrome's
  tabs), GTK text factor and Ghostty font, each through its own control, verified live.
  Built for a fleet of 27" iMacs; works on any single-monitor Omarchy machine. Use for "set
  up the display on this iMac", "size the screen", "bar icons / tabs / terminal too big or
  small", "is this machine's display still right", "audit the iMacs", or after a reinstall.
---

# Omarchy display sizing

One script holds every display size to its target. Run it; do not hand-edit what it owns.

    python3 ~/.claude/skills/omarchy-display-sizing/display_sizing.py --check   # audit
    python3 ~/.claude/skills/omarchy-display-sizing/display_sizing.py           # apply + verify

## What it sets

| Setting | Flag, default | What it sizes | Control |
|---|---|---|---|
| Monitor scale, live and in `~/.config/hypr/monitors.lua` | `--density 130` (or `--scale`) | every app | `omarchy hyprland monitor scaling` |
| `[font] base-size` in `~/.config/omarchy/shell.toml` | `--shell 18` | bar icons and bar text | the script |
| `[bar] size-horizontal` in the same file | `--bar 28` | bar height only | the script |
| Size in GTK `font-name` (family kept) | `--ui-font 9.6` | GTK app UI text, Chrome tab text | `gsettings` |
| GTK `text-scaling-factor` | `--gtk 1.0` | GTK app text, and Ghostty | `gsettings` |
| `font-size` in `~/.config/ghostty/config` | `--terminal 14` | Ghostty | the script, then a reload signal |

The defaults are the owner's sizes on a 27" iMac at 3840x2160, set on 2026-10-15 in screen
pixels: 25px bar icons, 16px Chrome tab text, 23.3px Ghostty text, at scale 1.25.

## Converting a size someone asks for

Sizes are asked for in screen pixels. Convert at the machine's scale, then pass the flag.

- Bar icon glyph = round(13 x shell / 12) x scale. The shell font must be a whole number, so
  not every icon size is reachable; give the nearest and say so.
- Bar text = shell x scale. Bar text and icons move together; there is no separate dial.
- Chrome tab text = ui-font x 4/3 x scale.
- Ghostty text = terminal x 4/3 x scale x gtk.

Example: at scale 1.25, "Ghostty 23.3px" is `--terminal 14`, and "tabs 16px" is `--ui-font 9.6`.

## How to run it

1. Run `--check` first and read every row.
2. IF every row is `ok`, THEN report that the machine conforms and stop.
3. IF a row is `DRIFT`, THEN tell the owner which settings will change, then run without `--check`.
4. IF the exit code is 2, THEN report the refusal message; do not work around it.
5. IF the owner asks for a size, THEN convert it as above and pass the flag; never edit the files.
6. After an apply, paste the final table and the exit code.
7. IF the UI font changed, THEN tell the owner to restart Chrome (`chrome://restart`).

**Why:** each setting has one control. Changing one element per complaint without the
conversion is how the sizes drift out of proportion with each other.

Rows: `ok` matches, `DRIFT` does not, `skip` cannot be checked (a vertical bar, no Ghostty
config). Exit 0 conforms or was applied and verified, 1 something drifts or failed to
verify, 2 could not decide.

## Facts the script rests on

- `omarchy display text size` is not used. It moves the shell font, GTK factor and terminal
  together on fixed anchors (12px = 1.0 = 9pt), so it cannot reach sizes chosen separately.
- `[bar]` accepts only `size-horizontal` and `size-vertical`, so icons grow only with
  `[font] base-size`. Live bar thickness is `size-horizontal x base-size / 12`.
- The bar's scale buttons change the live scale. When `monitors.lua` has custom per-output
  lines, they do not persist it, so live and file can disagree. The script checks both.
- Ghostty multiplies `font-size` by the GTK text-scaling-factor (its own docs say so).
- Ghostty reloads on SIGUSR2. A window zoomed with Ctrl +/- keeps its zoom; Ctrl+0 resets it.
- Chrome's page text is Chrome's own zoom setting, not one of these.

## Across the fleet

The script runs over SSH as the desktop user while that user is logged in to Hyprland. It
fills in `XDG_RUNTIME_DIR`, the D-Bus address and the Hyprland instance from
`/run/user/<uid>`. Claude is not needed on the target:

    for h in imac01 imac02; do
      echo "== $h"
      ssh "$h" python3 .claude/skills/omarchy-display-sizing/display_sizing.py --check
    done

A machine at the login screen has no Hyprland session; it is refused with exit 2.

## Limits

- One monitor only. An iMac with an external display attached is refused, not guessed.
- A monitor that reports no physical width needs `--scale`.
- Ghostty is the only terminal sized.
- `monitors.lua` without Omarchy's catch-all lines cannot be persisted by Omarchy's scaling
  command; the `scale (monitors.lua)` row stays `DRIFT` and the owner edits that file.
- Omarchy's scale rounding is restated in the script, because the command has no dry run.
  After an Omarchy update, a scale row that is `DRIFT` immediately after a successful apply
  means Omarchy changed it.
- Chrome's tab text following the GTK `font-name` is an assumption from how Chromium reads
  its Linux UI font. It was set on the owner's iMac but not yet confirmed by eye; confirm it
  there before relying on `--ui-font` for Chrome.
