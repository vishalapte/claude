"""Tests for dot-claude/skills/omarchy-display-sizing. Run: python3 -m unittest test_display_sizing

Lives at the repo root for the reason test_git_boundary.py gives: a top-level file never
reaches ~/.claude.

The formula tests pin values read off a 27" iMac (3840x2160, 600 mm wide) on 2026-10-15:
the scale Omarchy chose and the bar height Hyprland reported.
The survey and apply tests use a fake desktop and never touch the machine running them.
"""

import importlib.util
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path

SCRIPT = (Path(__file__).resolve().parent / "dot-claude" / "skills"
          / "omarchy-display-sizing" / "display_sizing.py")
spec = importlib.util.spec_from_file_location("display_sizing", SCRIPT)
ds = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ds)

# Omarchy's shipped default, /usr/share/omarchy/config/hypr/monitors.lua, trimmed.
FRESH_MONITORS_LUA = """\
local omarchy_gdk_scale = 2
local omarchy_monitor_scale = "auto"

hl.env("GDK_SCALE", tostring(omarchy_gdk_scale))
hl.monitor({ output = "", mode = "preferred", position = "auto", scale = omarchy_monitor_scale })
"""


class Formulas(unittest.TestCase):
    def test_scale_for_the_owners_imac_is_1_25(self):
        self.assertEqual(ds.target_scale(3840, 600, 2160, ds.DENSITY), 1.25)

    def test_scale_for_a_pre_retina_27_inch_imac_is_1(self):
        self.assertEqual(ds.target_scale(2560, 600, 1440, ds.DENSITY), 1.0)

    def test_scale_for_a_5k_panel_driven_natively_is_5_3rds(self):
        self.assertAlmostEqual(ds.target_scale(5120, 600, 2880, ds.DENSITY), 200 / 120)

    def test_scale_is_clamped_to_what_omarchy_accepts(self):
        self.assertEqual(ds.target_scale(1280, 600, 720, ds.DENSITY), 1.0)
        self.assertEqual(ds.target_scale(3840, 100, 2160, ds.DENSITY), 4.0)

    def test_clean_scale_rounds_up_to_a_hyprland_scale(self):
        # 1.49 is not a divisor in 1/120 steps on 3840x2160; Omarchy rounds it up to 1.5.
        self.assertEqual(ds.clean_scale(1.49, 3840, 2160), 1.5)
        self.assertEqual(ds.clean_scale(1.6, 3840, 2160), 1.6)
        self.assertEqual(ds.clean_scale(1.25, 3840, 2160), 1.25)

    def test_defaults_give_the_owners_screen_pixel_sizes_at_1_25(self):
        scale = 1.25
        self.assertEqual(round(13 * ds.SHELL_PX / 12) * scale, 25)   # bar icon glyph (iconFont 13)
        self.assertAlmostEqual(ds.UI_FONT_PT * 4 / 3 * scale, 16)    # Chrome tab text
        self.assertAlmostEqual(ds.TERMINAL_PT * 4 / 3 * scale * ds.GTK_FACTOR, 23.33, places=2)  # Ghostty

    def test_bar_height_matches_live_layers(self):
        # Each pair was read from `hyprctl layers` after setting it.
        self.assertEqual(ds.bar_height(32, 14), 37)
        self.assertEqual(ds.bar_height(28, 18), 42)
        self.assertEqual(ds.bar_height(28, 15), 35)
        self.assertEqual(ds.bar_height(28, 12), 28)

    def test_font_name_split(self):
        self.assertEqual(ds.font_name_split("'Adwaita Sans 11'\n"), ("Adwaita Sans", 11.0))
        self.assertEqual(ds.font_name_split("'Adwaita Sans 9.6'"), ("Adwaita Sans", 9.6))
        self.assertEqual(ds.font_name_split("'Cantarell'"), ("Cantarell", None))


class Files(unittest.TestCase):
    def test_persisted_scale_reads_omarchys_fresh_default(self):
        self.assertEqual(ds.persisted_scale(FRESH_MONITORS_LUA), '"auto"')

    def test_persisted_scale_reads_a_number(self):
        text = FRESH_MONITORS_LUA.replace('"auto"', "1.5", 1)
        self.assertEqual(ds.persisted_scale(text), 1.5)

    def test_persisted_scale_reads_the_older_catchall_form(self):
        text = 'hl.monitor({ output = "", mode = "preferred", position = "auto", scale = 1.6 })\n'
        self.assertEqual(ds.persisted_scale(text), 1.6)

    def test_persisted_scale_is_none_without_a_catchall(self):
        text = 'hl.monitor({ output = "eDP-1", mode = "preferred", position = "auto", scale = 1.5 })\n'
        self.assertIsNone(ds.persisted_scale(text))

    def test_toml_get(self):
        text = "[font]\nbase-size = 12\n\n[bar]\n# note\nsize-horizontal = 28  # tall\n"
        self.assertEqual(ds.toml_get(text, "bar", "size-horizontal"), "28")
        self.assertEqual(ds.toml_get(text, "font", "base-size"), "12")
        self.assertIsNone(ds.toml_get(text, "font", "size-horizontal"))

    def test_toml_set_replaces_in_place_and_keeps_the_rest(self):
        text = "[font]\nbase-size = 12\n\n[bar]\n# note\nsize-horizontal = 32\n"
        self.assertEqual(ds.toml_set(text, "bar", "size-horizontal", 28),
                         "[font]\nbase-size = 12\n\n[bar]\n# note\nsize-horizontal = 28\n")

    def test_toml_set_inserts_under_an_existing_section(self):
        self.assertEqual(ds.toml_set("[bar]\nscale-with-font = true\n", "bar", "size-horizontal", 28),
                         "[bar]\nsize-horizontal = 28\nscale-with-font = true\n")

    def test_toml_set_does_not_match_a_key_in_another_section(self):
        self.assertEqual(ds.toml_set("[spacing]\nsize-horizontal = 9\n", "bar", "size-horizontal", 28),
                         "[spacing]\nsize-horizontal = 9\n\n[bar]\nsize-horizontal = 28\n")

    def test_ghostty_size_and_set(self):
        config = 'config-file = ?"theme.conf"\nfont-size = 9\n'
        self.assertEqual(ds.ghostty_size(config), 9.0)
        self.assertEqual(ds.ghostty_set(config, 10.5), 'config-file = ?"theme.conf"\nfont-size = 10.5\n')
        self.assertIsNone(ds.ghostty_size("theme = x\n"))
        self.assertEqual(ds.ghostty_set("theme = x\n", 10.5), "theme = x\nfont-size = 10.5\n")
        self.assertEqual(ds.ghostty_set("", 10.5), "font-size = 10.5\n")

    def test_toml_set_creates_a_file_from_nothing(self):
        self.assertEqual(ds.toml_set("", "bar", "size-horizontal", 28), "[bar]\nsize-horizontal = 28\n")


class SessionEnv(unittest.TestCase):
    def test_finds_the_one_running_hyprland(self):
        with tempfile.TemporaryDirectory() as run:
            (Path(run) / "hypr" / "stale").mkdir(parents=True)
            live = Path(run) / "hypr" / "abc_1"
            live.mkdir()
            (live / ".socket.sock").touch()
            env = ds.session_env({"PATH": "/usr/bin", "XDG_RUNTIME_DIR": run}, 1000)
        self.assertEqual(env["HYPRLAND_INSTANCE_SIGNATURE"], "abc_1")
        self.assertEqual(env["DBUS_SESSION_BUS_ADDRESS"], f"unix:path={run}/bus")
        self.assertEqual(env["PATH"], "/usr/share/omarchy/bin:/usr/bin")

    def test_refuses_without_a_running_hyprland(self):
        with tempfile.TemporaryDirectory() as run:
            with self.assertRaises(ds.Refusal):
                ds.session_env({"XDG_RUNTIME_DIR": run}, 1000)

    def test_keeps_an_existing_session(self):
        env = ds.session_env({"HYPRLAND_INSTANCE_SIGNATURE": "x", "XDG_RUNTIME_DIR": "/r",
                              "DBUS_SESSION_BUS_ADDRESS": "d", "PATH": "/usr/share/omarchy/bin"}, 1)
        self.assertEqual((env["HYPRLAND_INSTANCE_SIGNATURE"], env["DBUS_SESSION_BUS_ADDRESS"],
                          env["PATH"]), ("x", "d", "/usr/share/omarchy/bin"))



class FakeDesktop(ds.Desktop):
    """The owner's iMac as data; the scaling command changes it the way Omarchy's does."""

    def __init__(self, home, scale=1.25, persisted="1.25", shell="18", bar="28",
                 font="'Adwaita Sans 9.6'", factor="1.0", pt=14, monitors=1, physical=600):
        super().__init__({}, home)
        self.scale, self.monitors, self.physical = scale, monitors, physical
        self.settings = {"font-name": font, "text-scaling-factor": factor}
        self.monitors_lua.parent.mkdir(parents=True)
        self.monitors_lua.write_text(FRESH_MONITORS_LUA.replace('"auto"', persisted, 1))
        self.shell_toml.parent.mkdir(parents=True)
        toml = ""
        if shell:
            toml = ds.toml_set(toml, "font", "base-size", shell)
        if bar:
            toml = ds.toml_set(toml, "bar", "size-horizontal", bar)
        self.shell_toml.write_text(toml)
        self.ghostty.parent.mkdir(parents=True)
        self.ghostty.write_text(f'config-file = ?"theme.conf"\nfont-size = {pt:g}\n')
        self.ran = []

    def monitor(self):
        if self.monitors != 1:
            raise ds.Refusal("expects exactly one monitor")
        return {"name": "eDP-1", "width": 3840, "height": 2160, "physicalWidth": self.physical,
                "scale": self.scale}

    def gsetting(self, key):
        return self.settings[key]

    def set_gsetting(self, key, value):
        self.ran.append(f"gsettings {key} {value}")
        self.settings[key] = value

    def set_scale(self, scale):
        self.ran.append(f"scaling {scale:g}")
        self.scale = scale
        self.monitors_lua.write_text(FRESH_MONITORS_LUA.replace('"auto"', f"{scale:g}", 1))

    def signal_ghostty(self):
        self.ran.append("signal ghostty")

    def bar_layer(self):
        toml = self.shell_toml.read_text()
        size = int(ds.toml_get(toml, "bar", "size-horizontal") or 26)
        px = int(ds.toml_get(toml, "font", "base-size") or 12)
        return 3072, ds.bar_height(size, px)


ARGS = Namespace(density=ds.DENSITY, scale=None, shell=ds.SHELL_PX, bar=ds.BAR,
                 ui_font=ds.UI_FONT_PT, gtk=ds.GTK_FACTOR, terminal=ds.TERMINAL_PT, check=False)


class SurveyAndApply(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def converge(self, desk):
        scale, rows = ds.survey(desk, ARGS)
        done = ds.apply(desk, ARGS, scale, rows)
        return done, ds.survey(desk, ARGS)[1]

    def assertConforms(self, rows):
        self.assertTrue(all(ok for *_, ok in rows), rows)

    def test_a_conforming_machine_is_left_alone(self):
        desk = FakeDesktop(self.home)
        done, rows = self.converge(desk)
        self.assertEqual((done, desk.ran), ([], []))
        self.assertConforms(rows)

    def test_a_fresh_install_converges_in_one_apply(self):
        # Omarchy's defaults: scale "auto" (1.6 live here), no shell overrides, 11pt UI font,
        # GTK 1.0, Ghostty 9pt.
        desk = FakeDesktop(self.home, scale=1.6, persisted='"auto"', shell=None, bar=None,
                           font="'Adwaita Sans 11'", pt=9)
        done, rows = self.converge(desk)
        self.assertEqual(desk.ran, ["scaling 1.25", "gsettings font-name Adwaita Sans 9.6",
                                    "signal ghostty"])
        self.assertEqual(ds.toml_get(desk.shell_toml.read_text(), "font", "base-size"), "18")
        self.assertEqual(ds.ghostty_size(desk.ghostty.read_text()), 14)
        self.assertEqual(len(done), 4)
        self.assertConforms(rows)

    def test_the_ui_font_keeps_its_family(self):
        desk = FakeDesktop(self.home, font="'Cantarell 11'")
        self.converge(desk)
        self.assertEqual(desk.settings["font-name"], "Cantarell 9.6")

    def test_this_imac_at_mid_session_state_converges(self):
        # Scale 1.5, shell 12, GTK factor 1.1818, Ghostty 10.5: every setting drifts.
        desk = FakeDesktop(self.home, scale=1.5, persisted="1.5", shell="12",
                           font="'Adwaita Sans 11'", factor="1.1818", pt=10.5)
        _, rows = self.converge(desk)
        self.assertIn("gsettings text-scaling-factor 1", desk.ran)
        self.assertConforms(rows)

    def test_a_second_apply_changes_nothing(self):
        desk = FakeDesktop(self.home, scale=1.5, persisted="1.6", shell="14", bar="32",
                           font="'Adwaita Sans 11'", factor="1.1818", pt=9)
        self.converge(desk)
        before = (desk.monitors_lua.read_text(), desk.shell_toml.read_text(),
                  desk.ghostty.read_text(), dict(desk.settings))
        desk.ran.clear()
        done, _ = self.converge(desk)
        self.assertEqual((done, desk.ran), ([], []))
        self.assertEqual((desk.monitors_lua.read_text(), desk.shell_toml.read_text(),
                          desk.ghostty.read_text(), desk.settings), before)

    def test_live_scale_drift_alone_is_repaired(self):
        # The bar's scale buttons moved the live scale and left monitors.lua alone.
        desk = FakeDesktop(self.home, scale=1.6)
        done, rows = self.converge(desk)
        self.assertEqual(done, ["omarchy hyprland monitor scaling 1.25"])
        self.assertConforms(rows)

    def test_no_ghostty_config_is_skipped_not_created(self):
        desk = FakeDesktop(self.home)
        desk.ghostty.unlink()
        done, rows = self.converge(desk)
        self.assertEqual(done, [])
        self.assertIn(("ghostty font-size", "no config", 14, None), rows)
        self.assertFalse(desk.ghostty.exists())

    def test_shell_edit_keeps_other_keys(self):
        desk = FakeDesktop(self.home, shell="14")
        desk.shell_toml.write_text("[font]\nbase-size = 14\n\n[bar]\nsize-horizontal = 28\n"
                                   "scale-with-font = true\n")
        self.converge(desk)
        self.assertEqual(desk.shell_toml.read_text(), "[font]\nbase-size = 18\n\n[bar]\n"
                         "size-horizontal = 28\nscale-with-font = true\n")

    def test_scale_override_wins_over_density(self):
        desk = FakeDesktop(self.home)
        scale, _ = ds.survey(desk, Namespace(**{**vars(ARGS), "scale": 1.6}))
        self.assertEqual(scale, 1.6)

    def test_no_physical_width_is_refused(self):
        with self.assertRaises(ds.Refusal):
            ds.survey(FakeDesktop(self.home, physical=0), ARGS)

    def test_two_monitors_are_refused(self):
        with self.assertRaises(ds.Refusal):
            ds.survey(FakeDesktop(self.home, monitors=2), ARGS)


if __name__ == "__main__":
    unittest.main()
