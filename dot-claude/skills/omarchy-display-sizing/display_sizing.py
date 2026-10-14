#!/usr/bin/env python3
"""Size an Omarchy desktop's display: scale, bar, GTK interface text and terminal.

Usage: display_sizing.py [--check] [--density PPI | --scale S] [--shell PX] [--bar N]
                         [--ui-font PT] [--gtk F] [--terminal PT]

Reads the one connected monitor's resolution and physical width, works out the scale that
gives --density points per inch, and holds each setting to its target with its own control:

  monitor scale    live in Hyprland and in ~/.config/hypr/monitors.lua (sizes every app)
  shell font       [font] base-size in ~/.config/omarchy/shell.toml (bar text and icons)
  bar              [bar] size-horizontal in the same file (bar height only)
  GTK UI font      the size in org.gnome.desktop.interface font-name (GTK apps, Chrome tabs)
  GTK text factor  org.gnome.desktop.interface text-scaling-factor
  terminal font    font-size in ~/.config/ghostty/config

The controls are independent on purpose. `omarchy display text size` moves the shell font,
the GTK factor and the terminal together on fixed anchors, so it cannot reach sizes chosen
one element at a time.

A screen-pixel size is points x 4/3 x scale for fonts, and logical px x scale for the
shell. At scale 1.25: shell 18 gives 25px bar icons, a 9.6pt UI font gives 16px Chrome tab
text, and Ghostty 14pt gives 23.3px. Ghostty also multiplies its size by the GTK factor.

A setting already at its target is left alone, so a re-run changes nothing. The scale is
set through `omarchy hyprland monitor scaling`, which rounds it to one Hyprland accepts and
persists it; the rest are written here.

--check reports and changes nothing.

Exit: 0 conforms, or was applied and verified; 1 a setting drifts (--check) or failed to
verify after applying; 2 could not decide (no Hyprland session, not exactly one monitor,
no physical width without --scale, a command that failed).

Runs over SSH as the desktop user: a missing session environment is found under
/run/user/<uid>, as long as that user is logged in to Hyprland.
"""

import argparse
import json
import math
import os
import re
import subprocess
import sys
import time
from pathlib import Path

# Defaults are the sizes the owner set on a 27" iMac (3840x2160, 600 mm wide) on
# 2026-10-15, after trying 1.5: scale 1.25, 25px bar icons, 16px Chrome tab text and 23.3px
# Ghostty text. 130 points per inch is what 1.25 gives on that panel.
DENSITY = 130
SHELL_PX = 18
BAR = 28
UI_FONT_PT = 9.6
GTK_FACTOR = 1.0
TERMINAL_PT = 14

SCALE_RANGE = (1, 4)  # what omarchy-hyprland-monitor-scaling accepts

GTK_SCHEMA = "org.gnome.desktop.interface"
SCALE_KEY = re.compile(r"^local omarchy_monitor_scale = (\S+)[ \t]*$", re.M)
SCALE_CATCHALL = re.compile(
    r'^hl\.monitor\(\{ output = "", mode = "preferred", position = "auto", '
    r'scale = ("auto"|[0-9.]+) \}\)[ \t]*$',
    re.M,
)
GHOSTTY_SIZE = re.compile(r"^font-size = (\S+)[ \t]*$", re.M)
FONT_NAME = re.compile(r"^'?(.*?)\s+([0-9.]+)'?\s*$")


class Refusal(Exception):
    """The run cannot decide; exit 2."""


# ---- formulas ----


def clean_scale(scale, width, height):
    """Round a scale up to one Hyprland accepts for this mode.

    Restated from clean_scale() in /usr/share/omarchy/bin/omarchy-hyprland-monitor-scaling,
    because that command has no dry run: a clean scale divides the mode into whole logical
    pixels in 1/120 steps.
    """
    g = math.gcd(width * 120, height * 120)
    k = min(int(scale * 120 + 0.5), g)
    while g % k:
        k += 1
    return k / 120


def target_scale(width_px, width_mm, height_px, density):
    """The clean scale nearest to `density` points per inch on this panel."""
    raw = width_px / (width_mm / 25.4 * density)
    raw = min(SCALE_RANGE[1], max(SCALE_RANGE[0], round(raw, 2)))
    return clean_scale(raw, width_px, height_px)


def bar_height(size, px):
    """Live bar thickness: the size scales with the shell font, 12px being 1x (Style.barToken)."""
    return max(1, math.floor(size * px / 12 + 0.5))


# ---- value helpers ----


def number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def persisted_scale(monitors_lua):
    """The scale monitors.lua applies to every output: a number, a raw value such as
    "auto" (Omarchy's fresh default), or None if it has no catch-all."""
    m = SCALE_KEY.search(monitors_lua) or SCALE_CATCHALL.search(monitors_lua)
    if not m:
        return None
    return number(m.group(1)) if number(m.group(1)) is not None else m.group(1)


def toml_get(text, section, key):
    """A key's raw value inside [section] of a flat TOML file, or None."""
    current = None
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("["):
            current = s.strip("[] ")
        elif current == section and re.match(rf"{re.escape(key)}\s*=", s):
            return re.sub(r"\s*(#.*)?$", "", s.split("=", 1)[1].strip())
    return None


def toml_set(text, section, key, value):
    """Set key = value in [section]: in place, else under the header, else a new section."""
    lines = text.splitlines()
    current = None
    header = None
    for i, line in enumerate(lines):
        s = line.strip()
        if s.startswith("["):
            current = s.strip("[] ")
            if current == section:
                header = i
        elif current == section and re.match(rf"{re.escape(key)}\s*=", s):
            lines[i] = f"{key} = {value}"
            return "\n".join(lines) + "\n"
    if header is not None:
        lines.insert(header + 1, f"{key} = {value}")
        return "\n".join(lines) + "\n"
    body = "\n".join(lines).rstrip("\n")
    return (body + "\n\n" if body else "") + f"[{section}]\n{key} = {value}\n"


def ghostty_size(config):
    """Ghostty's font-size, or None if the config does not set one."""
    m = GHOSTTY_SIZE.search(config)
    return number(m.group(1)) if m else None


def ghostty_set(config, pt):
    """Set font-size in place, or append it."""
    line = f"font-size = {pt:g}"
    if GHOSTTY_SIZE.search(config):
        return GHOSTTY_SIZE.sub(line, config, count=1)
    return config.rstrip("\n") + ("\n" if config.strip() else "") + line + "\n"


def font_name_split(value):
    """('Adwaita Sans', 9.6) from a gsettings font-name such as 'Adwaita Sans 9.6'."""
    m = FONT_NAME.match(value.strip())
    return (m.group(1), float(m.group(2))) if m else (value.strip().strip("'"), None)


def session_env(env, uid):
    """The environment hyprctl, gsettings and omarchy need, filled in when run over SSH."""
    env = dict(env)
    run = env.get("XDG_RUNTIME_DIR") or f"/run/user/{uid}"
    env["XDG_RUNTIME_DIR"] = run
    env.setdefault("DBUS_SESSION_BUS_ADDRESS", f"unix:path={run}/bus")
    if not env.get("HYPRLAND_INSTANCE_SIGNATURE"):
        hypr = Path(run) / "hypr"
        live = sorted(p.parent.name for p in hypr.glob("*/.socket.sock"))
        if len(live) != 1:
            raise Refusal(f"need one running Hyprland under {hypr}, found {len(live)}")
        env["HYPRLAND_INSTANCE_SIGNATURE"] = live[0]
    omarchy_bin = str(Path(env.get("OMARCHY_PATH", "/usr/share/omarchy")) / "bin")
    path = env.get("PATH", "")
    if omarchy_bin not in path.split(":"):
        env["PATH"] = f"{omarchy_bin}:{path}" if path else omarchy_bin
    return env


# ---- the machine ----


class Desktop:
    def __init__(self, env, home):
        self.env = env
        self.monitors_lua = home / ".config/hypr/monitors.lua"
        self.shell_toml = home / ".config/omarchy/shell.toml"
        self.ghostty = home / ".config/ghostty/config"

    def run(self, *argv):
        try:
            p = subprocess.run(argv, env=self.env, capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.TimeoutExpired) as e:
            raise Refusal(f"{argv[0]}: {e}") from e
        if p.returncode:
            raise Refusal(f"{' '.join(argv)} exited {p.returncode}: {p.stderr.strip()}")
        return p.stdout

    def monitor(self):
        monitors = json.loads(self.run("hyprctl", "-j", "monitors"))
        if len(monitors) != 1:
            names = ", ".join(m["name"] for m in monitors) or "none"
            raise Refusal(f"expects exactly one monitor, found {len(monitors)}: {names}")
        return monitors[0]

    def gsetting(self, key):
        return self.run("gsettings", "get", GTK_SCHEMA, key).strip()

    def set_gsetting(self, key, value):
        self.run("gsettings", "set", GTK_SCHEMA, key, value)

    def set_scale(self, scale):
        self.run("omarchy", "hyprland", "monitor", "scaling", f"{scale:g}")

    def signal_ghostty(self):
        """Reload running Ghostty windows, as omarchy-display-text-size does."""
        subprocess.run(["pkill", "-SIGUSR2", "ghostty"], env=self.env, capture_output=True)

    def bar_layer(self):
        """(width, height) of the omarchy-bar layer, or None if it is not mapped."""
        for mon in json.loads(self.run("hyprctl", "-j", "layers")).values():
            for layers in mon.get("levels", {}).values():
                for layer in layers:
                    if layer.get("namespace") == "omarchy-bar":
                        return layer["w"], layer["h"]
        return None

    def read_file(self, path):
        return path.read_text() if path.exists() else ""


def survey(desk, args):
    """One row per setting: (name, current, target, ok); ok is None when not checkable."""
    mon = desk.monitor()
    w, h, mm = mon["width"], mon["height"], mon.get("physicalWidth") or 0
    if args.scale is not None:
        scale = clean_scale(args.scale, w, h)
    elif mm <= 0:
        raise Refusal(f"{mon['name']} reports no physical width; pass --scale")
    else:
        scale = target_scale(w, mm, h, args.density)

    shell = desk.read_file(desk.shell_toml)
    persisted = persisted_scale(desk.read_file(desk.monitors_lua))
    base = toml_get(shell, "font", "base-size")
    bar = toml_get(shell, "bar", "size-horizontal")
    _, ui_pt = font_name_split(desk.gsetting("font-name"))
    factor = number(desk.gsetting("text-scaling-factor"))

    def close(a, b, tol):
        return isinstance(a, (int, float)) and abs(a - b) < tol

    rows = [
        ("scale (live)", mon["scale"], scale, close(mon["scale"], scale, 0.005)),
        ("scale (monitors.lua)", persisted, scale, close(persisted, scale, 0.005)),
        ("shell base-size", base, args.shell, close(number(base), args.shell, 0.5)),
        ("bar size-horizontal", bar, args.bar, close(number(bar), args.bar, 0.5)),
        ("gtk ui font pt", ui_pt, args.ui_font, close(ui_pt, args.ui_font, 0.01)),
        ("gtk text-scaling-factor", factor, args.gtk, close(factor, args.gtk, 0.001)),
    ]
    if desk.ghostty.exists():
        pt = ghostty_size(desk.read_file(desk.ghostty))
        rows.append(("ghostty font-size", pt, args.terminal, close(pt, args.terminal, 0.01)))
    else:
        rows.append(("ghostty font-size", "no config", args.terminal, None))
    layer = desk.bar_layer()
    want_bar = bar_height(args.bar, args.shell)
    if layer is None:
        rows.append(("bar height (live)", None, want_bar, False))
    elif layer[0] < layer[1]:
        rows.append(("bar height (live)", "vertical bar", want_bar, None))
    else:
        rows.append(("bar height (live)", layer[1], want_bar, layer[1] == want_bar))
    return scale, rows


def drifted(rows, *names):
    return any(ok is False for name, _, _, ok in rows if name in names)


def apply(desk, args, scale, rows):
    """Set each drifted setting with its own control; return what was done."""
    done = []
    if drifted(rows, "scale (live)", "scale (monitors.lua)"):
        desk.set_scale(scale)
        done.append(f"omarchy hyprland monitor scaling {scale:g}")
    if drifted(rows, "shell base-size", "bar size-horizontal"):
        text = toml_set(desk.read_file(desk.shell_toml), "font", "base-size", args.shell)
        text = toml_set(text, "bar", "size-horizontal", args.bar)
        desk.shell_toml.parent.mkdir(parents=True, exist_ok=True)
        desk.shell_toml.write_text(text)
        done.append(f"set base-size = {args.shell}, size-horizontal = {args.bar} in {desk.shell_toml}")
    if drifted(rows, "gtk ui font pt"):
        family, _ = font_name_split(desk.gsetting("font-name"))
        desk.set_gsetting("font-name", f"{family} {args.ui_font:g}")
        done.append(f"gsettings set {GTK_SCHEMA} font-name '{family} {args.ui_font:g}'")
    if drifted(rows, "gtk text-scaling-factor"):
        desk.set_gsetting("text-scaling-factor", f"{args.gtk:g}")
        done.append(f"gsettings set {GTK_SCHEMA} text-scaling-factor {args.gtk:g}")
    if drifted(rows, "ghostty font-size"):
        desk.ghostty.write_text(ghostty_set(desk.read_file(desk.ghostty), args.terminal))
        desk.signal_ghostty()
        done.append(f"set font-size = {args.terminal:g} in {desk.ghostty}")
    return done


def report(rows):
    for name, current, target, ok in rows:
        status = {True: "ok", False: "DRIFT", None: "skip"}[ok]
        print(f"{status:<6}{name:<26}{current!s:>14} -> {target}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--check", action="store_true", help="report only; change nothing")
    pick = parser.add_mutually_exclusive_group()
    pick.add_argument("--density", type=float, default=DENSITY,
                      help=f"points per inch to aim for (default {DENSITY})")
    pick.add_argument("--scale", type=float, help="use this scale instead of deriving one")
    parser.add_argument("--shell", type=int, default=SHELL_PX,
                        help=f"shell [font] base-size in px (default {SHELL_PX})")
    parser.add_argument("--bar", type=int, default=BAR,
                        help=f"[bar] size-horizontal at a 12px shell font (default {BAR})")
    parser.add_argument("--ui-font", type=float, default=UI_FONT_PT,
                        help=f"GTK interface font size in points (default {UI_FONT_PT:g})")
    parser.add_argument("--gtk", type=float, default=GTK_FACTOR,
                        help=f"GTK text-scaling-factor (default {GTK_FACTOR:g})")
    parser.add_argument("--terminal", type=float, default=TERMINAL_PT,
                        help=f"Ghostty font-size in points (default {TERMINAL_PT:g})")
    args = parser.parse_args(argv)
    if args.scale is not None and not SCALE_RANGE[0] <= args.scale <= SCALE_RANGE[1]:
        parser.error(f"--scale must be {SCALE_RANGE[0]}-{SCALE_RANGE[1]}")
    for name in ("density", "shell", "bar", "ui_font", "gtk", "terminal"):
        if getattr(args, name) <= 0:
            parser.error(f"--{name.replace('_', '-')} must be positive")

    try:
        desk = Desktop(session_env(os.environ, os.getuid()), Path.home())
        scale, rows = survey(desk, args)
        if not args.check and any(ok is False for *_, ok in rows):
            for done in apply(desk, args, scale, rows):
                print(f"applied: {done}")
            # The shell re-reads shell.toml on change; give the bar a moment to re-flow.
            for _ in range(10):
                scale, rows = survey(desk, args)
                if not any(ok is False for *_, ok in rows):
                    break
                time.sleep(0.3)
    except Refusal as e:
        print(f"display_sizing: {e}", file=sys.stderr)
        return 2
    report(rows)
    return 1 if any(ok is False for *_, ok in rows) else 0


if __name__ == "__main__":
    sys.exit(main())
