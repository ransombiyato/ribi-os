#!/usr/bin/python3
"""ribi_theme - shared look-and-feel helpers for native Ribi GTK applications.

The minimal Ribi target ships an XPM-only gdk-pixbuf stack, so relying on
themed icons or stock images can abort GTK clients. These helpers draw every
glyph with Cairo instead, which only needs the cairo context GTK already
provides to a widget's draw signal. Nothing here loads an image file.

Import from an installed component:

    import sys
    sys.path.insert(0, "/usr/local/bin")
    import ribi_theme
"""

import math

try:
    import gi

    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk
except Exception:  # pragma: no cover - GTK may be absent on a build host
    Gtk = None


PALETTE = {
    "files": "#39c5ff",
    "terminal": "#66d17a",
    "zen": "#ff4f81",
    "editor": "#ffd43b",
    "screenshot": "#b98cff",
    "control": "#39c5ff",
    "obs": "#ff4f81",
    "calculator": "#ffd43b",
    "image": "#b98cff",
    "media": "#ff4f81",
    "archive": "#ffd43b",
    "text": "#66d17a",
    "pdf": "#ff6b81",
    "audio": "#b98cff",
    "display": "#39c5ff",
    "folder": "#ffd43b",
    "file": "#9fb0c8",
    "back": "#9fb0c8",
    "forward": "#9fb0c8",
    "up": "#9fb0c8",
    "new": "#66d17a",
    "refresh": "#39c5ff",
    "open": "#ffd43b",
    "save": "#66d17a",
    "find": "#39c5ff",
    "menu": "#39c5ff",
    "info": "#39c5ff",
    "warning": "#ffd43b",
    "error": "#ff4f81",
}

DEFAULT_COLOR = "#9fb0c8"

# ---- Design tokens ---------------------------------------------------------
# One source of truth for colour, spacing, radius, and type across every native
# Ribi app. Components read from here (or from base_css()) instead of
# hardcoding values, so the whole desktop restyles from one place.
TOKENS = {
    "bg": "#0e1118",
    "surface": "#161b26",
    "surface_alt": "#1d2431",
    "border": "rgba(255, 255, 255, 0.08)",
    "text": "#e6ebf5",
    "text_muted": "#9fb0c8",
    "accent": "#39c5ff",
    "accent_soft": "rgba(57, 197, 255, 0.18)",
    "accent_press": "rgba(57, 197, 255, 0.32)",
    "success": "#66d17a",
    "warning": "#ffd43b",
    "error": "#ff4f81",
    "radius": 10,
    "spacing": 8,
    "font": "DejaVu Sans, sans-serif",
}


def base_css() -> str:
    """The shared application theme, generated from TOKENS."""
    t = TOKENS
    return f"""
    * {{ font-family: {t['font']}; }}
    window, window.background, .background {{
        background-color: {t['bg']};
        color: {t['text']};
    }}
    .ribi-surface {{ background-color: {t['surface']}; border-radius: {t['radius']}px; }}
    headerbar, .ribi-headerbar {{
        background-color: {t['surface']};
        color: {t['text']};
        border-bottom: 1px solid {t['border']};
        box-shadow: none;
    }}
    notebook, notebook > stack {{ background-color: {t['bg']}; }}
    notebook > header {{ background-color: {t['surface']}; border-color: {t['border']}; }}
    notebook > header tab {{ color: {t['text_muted']}; }}
    notebook > header tab:checked {{ color: {t['text']}; }}
    textview, textview text {{ background-color: {t['surface']}; color: {t['text']}; }}
    label {{ color: {t['text']}; }}
    .ribi-muted {{ color: {t['text_muted']}; }}
    button {{
        background-image: none;
        background-color: {t['surface_alt']};
        color: {t['text']};
        border: 1px solid {t['border']};
        border-radius: {t['radius']}px;
        padding: 6px 12px;
    }}
    button:hover {{ background-color: {t['accent_soft']}; border-color: {t['accent']}; }}
    button:active {{ background-color: {t['accent_press']}; }}
    entry {{
        background-color: {t['surface_alt']};
        color: {t['text']};
        border: 1px solid {t['border']};
        border-radius: {t['radius']}px;
        padding: 8px 12px;
        caret-color: {t['accent']};
    }}
    entry:focus {{ border-color: {t['accent']}; }}
    scrollbar slider {{ background-color: {t['surface_alt']}; border-radius: 6px; }}
    """


def prefer_dark() -> None:
    """Ask GTK for the dark Adwaita variant so base widgets match our theme."""
    if Gtk is None:
        return
    try:
        Gtk.Settings.get_default().set_property("gtk-application-prefer-dark-theme", True)
    except Exception:
        pass


def apply_theme(widget) -> None:
    """Apply the shared Ribi application theme to ``widget``'s screen."""
    apply_css(widget, base_css())


def color(name):
    return PALETTE.get(name, DEFAULT_COLOR)


def _rounded_rect(ctx, x, y, w, h, r):
    ctx.new_sub_path()
    ctx.arc(x + w - r, y + r, r, -math.pi / 2, 0)
    ctx.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
    ctx.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
    ctx.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
    ctx.close_path()


def draw_glyph(ctx, name, size):
    """Draw a small geometric glyph for ``name`` centered in a size x size box."""
    ctx.set_line_width(max(1.5, size * 0.09))
    ctx.set_line_cap(1)  # round
    ctx.set_line_join(1)
    ctx.set_source_rgb(*_rgb(color(name)))
    m = size * 0.22
    s = size - 2 * m
    cx, cy = size / 2, size / 2

    if name == "files" or name == "folder":
        _rounded_rect(ctx, m, m + s * 0.12, s, s * 0.78, s * 0.1)
        ctx.fill()
        ctx.set_source_rgb(*_rgb("#10131d"))
        ctx.rectangle(m + s * 0.16, m + s * 0.30, s * 0.68, s * 0.10)
        ctx.fill()
    elif name == "terminal":
        ctx.move_to(m, m + s * 0.15)
        ctx.line_to(m + s * 0.35, cy)
        ctx.line_to(m, m + s * 0.85)
        ctx.stroke()
        ctx.move_to(m + s * 0.5, m + s * 0.85)
        ctx.line_to(m + s, m + s * 0.85)
        ctx.stroke()
    elif name in ("zen", "obs"):
        ctx.arc(cx, cy, s / 2, 0, 2 * math.pi)
        ctx.stroke()
        ctx.arc(cx, cy, s / 6, 0, 2 * math.pi)
        ctx.fill()
    elif name in ("editor", "file"):
        _rounded_rect(ctx, m + s * 0.15, m, s * 0.7, s, s * 0.08)
        ctx.fill()
        ctx.set_source_rgb(*_rgb("#10131d"))
        for i in range(3):
            y = m + s * (0.25 + i * 0.22)
            ctx.rectangle(m + s * 0.28, y, s * 0.44, s * 0.07)
        ctx.fill()
    elif name == "screenshot":
        _rounded_rect(ctx, m, m + s * 0.2, s, s * 0.65, s * 0.12)
        ctx.fill()
        ctx.arc(cx, cy + s * 0.02, s * 0.2, 0, 2 * math.pi)
        ctx.set_source_rgb(*_rgb("#10131d"))
        ctx.fill()
    elif name == "control":
        ctx.arc(cx, cy, s * 0.32, 0, 2 * math.pi)
        ctx.stroke()
        for k in range(6):
            a = k * math.pi / 3
            ctx.move_to(cx + math.cos(a) * s * 0.32, cy + math.sin(a) * s * 0.32)
            ctx.line_to(cx + math.cos(a) * s * 0.5, cy + math.sin(a) * s * 0.5)
        ctx.stroke()
    elif name in ("back", "forward"):
        d = 1 if name == "forward" else -1
        ctx.move_to(cx + d * s * 0.22, m)
        ctx.line_to(cx - d * s * 0.22, cy)
        ctx.line_to(cx + d * s * 0.22, m + s)
        ctx.stroke()
    elif name == "up":
        ctx.move_to(m, cy - s * 0.1)
        ctx.line_to(cx, m + s * 0.15)
        ctx.line_to(m + s, cy - s * 0.1)
        ctx.stroke()
    elif name == "new":
        ctx.rectangle(cx - s * 0.5, cy - s * 0.06, s, s * 0.12)
        ctx.rectangle(cx - s * 0.06, cy - s * 0.5, s * 0.12, s)
        ctx.fill()
    elif name == "refresh":
        ctx.arc(cx, cy, s * 0.42, math.radians(40), math.radians(320))
        ctx.stroke()
        ctx.move_to(cx + s * 0.42, cy - s * 0.3)
        ctx.line_to(cx + s * 0.2, cy - s * 0.12)
        ctx.line_to(cx + s * 0.55, cy - s * 0.02)
        ctx.fill()
    elif name == "open":
        ctx.move_to(m, m + s * 0.2)
        ctx.line_to(m + s * 0.4, m + s * 0.2)
        ctx.line_to(cx, m + s * 0.75)
        ctx.line_to(m + s, m + s * 0.2)
        ctx.stroke()
    elif name == "save":
        _rounded_rect(ctx, m + s * 0.1, m, s * 0.8, s, s * 0.08)
        ctx.stroke()
        ctx.rectangle(m + s * 0.28, m, s * 0.44, s * 0.35)
        ctx.fill()
    elif name == "find":
        ctx.arc(cx - s * 0.1, cy - s * 0.1, s * 0.32, 0, 2 * math.pi)
        ctx.stroke()
        ctx.move_to(cx + s * 0.12, cy + s * 0.12)
        ctx.line_to(cx + s * 0.45, cy + s * 0.45)
        ctx.stroke()
    elif name == "menu":
        for i in range(3):
            y = m + s * (0.2 + i * 0.3)
            ctx.move_to(m, y)
            ctx.line_to(m + s, y)
        ctx.stroke()
    elif name == "info":
        ctx.arc(cx, cy, s * 0.45, 0, 2 * math.pi)
        ctx.stroke()
        ctx.arc(cx, cy - s * 0.22, s * 0.06, 0, 2 * math.pi)
        ctx.fill()
        ctx.move_to(cx, cy - s * 0.05)
        ctx.line_to(cx, cy + s * 0.3)
        ctx.stroke()
    elif name == "warning":
        ctx.move_to(cx, m)
        ctx.line_to(m + s, m + s * 0.85)
        ctx.line_to(m, m + s * 0.85)
        ctx.close_path()
        ctx.stroke()
        ctx.move_to(cx, cy - s * 0.1)
        ctx.line_to(cx, cy + s * 0.12)
        ctx.stroke()
    elif name == "error":
        ctx.move_to(m, m)
        ctx.line_to(m + s, m + s)
        ctx.move_to(m + s, m)
        ctx.line_to(m, m + s)
        ctx.stroke()
    elif name == "games":
        for dx in (0, 1):
            for dy in (0, 1):
                _rounded_rect(ctx, m + dx * s * 0.55, m + dy * s * 0.55, s * 0.45, s * 0.45, s * 0.1)
                ctx.fill()
    elif name == "install":
        ctx.move_to(cx, m)
        ctx.line_to(cx, m + s * 0.62)
        ctx.stroke()
        ctx.move_to(cx - s * 0.24, m + s * 0.4)
        ctx.line_to(cx, m + s * 0.64)
        ctx.line_to(cx + s * 0.24, m + s * 0.4)
        ctx.stroke()
        ctx.move_to(m, m + s * 0.86)
        ctx.line_to(m + s, m + s * 0.86)
        ctx.stroke()
    elif name == "calculator":
        _rounded_rect(ctx, m, m, s, s, s * 0.12)
        ctx.stroke()
        ctx.rectangle(m + s * 0.12, m + s * 0.12, s * 0.76, s * 0.22)
        ctx.fill()
        for gx in (0.16, 0.42, 0.68):
            for gy in (0.48, 0.74):
                ctx.arc(m + s * gx, m + s * gy, s * 0.06, 0, 2 * math.pi)
                ctx.fill()
    elif name == "image":
        _rounded_rect(ctx, m, m + s * 0.12, s, s * 0.76, s * 0.1)
        ctx.stroke()
        ctx.arc(m + s * 0.3, m + s * 0.34, s * 0.08, 0, 2 * math.pi)
        ctx.fill()
        ctx.move_to(m + s * 0.12, m + s * 0.82)
        ctx.line_to(m + s * 0.42, m + s * 0.5)
        ctx.line_to(m + s * 0.62, m + s * 0.68)
        ctx.line_to(m + s * 0.78, m + s * 0.54)
        ctx.line_to(m + s * 0.92, m + s * 0.82)
        ctx.close_path()
        ctx.fill()
    elif name == "media":
        ctx.move_to(m + s * 0.18, m)
        ctx.line_to(m + s * 0.9, cy)
        ctx.line_to(m + s * 0.18, m + s)
        ctx.close_path()
        ctx.fill()
    elif name == "archive":
        _rounded_rect(ctx, m, m, s, s, s * 0.1)
        ctx.stroke()
        ctx.move_to(cx - s * 0.09, m)
        ctx.line_to(cx - s * 0.09, m + s * 0.5)
        ctx.move_to(cx + s * 0.09, m)
        ctx.line_to(cx + s * 0.09, m + s * 0.5)
        ctx.stroke()
        ctx.arc(cx, m + s * 0.62, s * 0.09, 0, 2 * math.pi)
        ctx.fill()
    elif name == "text":
        _rounded_rect(ctx, m + s * 0.12, m, s * 0.76, s, s * 0.08)
        ctx.stroke()
        for i in range(4):
            y = m + s * (0.2 + i * 0.2)
            ctx.move_to(m + s * 0.26, y)
            ctx.line_to(m + s * 0.74, y)
        ctx.stroke()
    elif name == "pdf":
        _rounded_rect(ctx, m + s * 0.1, m, s * 0.8, s, s * 0.1)
        ctx.stroke()
        ctx.move_to(m + s * 0.28, m + s * 0.62)
        ctx.line_to(m + s * 0.5, m + s * 0.3)
        ctx.line_to(m + s * 0.72, m + s * 0.62)
        ctx.close_path()
        ctx.fill()
    elif name == "audio":
        ctx.move_to(m + s * 0.08, cy)
        ctx.line_to(m + s * 0.34, cy)
        ctx.line_to(m + s * 0.62, m + s * 0.2)
        ctx.line_to(m + s * 0.62, m + s * 0.8)
        ctx.line_to(m + s * 0.34, cy)
        ctx.close_path()
        ctx.fill()
        ctx.arc(m + s * 0.74, cy, s * 0.12, -math.pi / 2, math.pi / 2)
        ctx.stroke()
        ctx.arc(m + s * 0.74, cy, s * 0.24, -math.pi / 2, math.pi / 2)
        ctx.stroke()
    elif name == "display":
        _rounded_rect(ctx, m, m + s * 0.1, s, s * 0.62, s * 0.08)
        ctx.stroke()
        ctx.move_to(cx, m + s * 0.72)
        ctx.line_to(cx, m + s * 0.9)
        ctx.stroke()
        ctx.move_to(cx - s * 0.24, m + s * 0.9)
        ctx.line_to(cx + s * 0.24, m + s * 0.9)
        ctx.stroke()
    else:
        ctx.arc(cx, cy, s * 0.4, 0, 2 * math.pi)
        ctx.stroke()


def _rgb(hex_color):
    h = hex_color.lstrip("#")
    return tuple(int(h[i : i + 2], 16) / 255 for i in (0, 2, 4))


def icon_widget(name, size=22, tooltip=None):
    """Return a realized-safe icon widget drawn with Cairo."""
    if Gtk is None:
        raise RuntimeError("GTK is not available")

    area = Gtk.DrawingArea()
    area.set_size_request(size, size)

    def on_draw(_widget, ctx):
        draw_glyph(ctx, name, size)
        return False

    area.connect("draw", on_draw)
    if tooltip:
        area.set_tooltip_text(tooltip)
    return area


def icon_button(name, tooltip=None, size=20):
    """A flat button containing a Cairo-drawn icon (no icon-theme lookup)."""
    button = Gtk.Button()
    button.set_relief(Gtk.ReliefStyle.NONE)
    button.add(icon_widget(name, size=size))
    if tooltip:
        button.set_tooltip_text(tooltip)
    return button


DOCK_CSS = """
#ribi-dock {
    background-color: rgba(16, 19, 29, 0.94);
    border-top: 1px solid rgba(255, 255, 255, 0.08);
    padding: 4px 10px;
}
#ribi-dock button {
    background: transparent;
    border: none;
    padding: 3px 6px;
    min-width: 0;
}
#ribi-dock button:hover {
    background-color: rgba(57, 197, 255, 0.18);
    border-radius: 8px;
}
#ribi-dock button:active {
    background-color: rgba(57, 197, 255, 0.32);
    border-radius: 8px;
}
#ribi-clock { color: #e6ebf5; font-size: 12px; font-weight: bold; }
#ribi-tray { color: #9fb0c8; font-size: 12px; }
#ribi-task {
    background-color: rgba(29, 36, 49, 0.65);
    border: 1px solid rgba(255, 255, 255, 0.06);
    border-radius: 6px;
    padding: 2px 8px;
    color: #c9d2e3;
    font-size: 12px;
}
#ribi-task:hover { background-color: rgba(57, 197, 255, 0.18); border-color: #39c5ff; }
#ribi-task.ribi-task-active {
    background-color: rgba(57, 197, 255, 0.30);
    border-color: #39c5ff;
    color: #ffffff;
}
"""


def apply_css(widget, css):
    if Gtk is None:
        return
    try:
        import gi

        gi.require_version("Gdk", "3.0")
        from gi.repository import Gdk

        provider = Gtk.CssProvider()
        provider.load_from_data(css.encode("utf-8"))
        screen = None
        if hasattr(widget, "get_screen"):
            screen = widget.get_screen()
        if screen is None:
            screen = Gdk.Screen.get_default()
        if screen is None:
            return
        Gtk.StyleContext.add_provider_for_screen(
            screen, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
    except Exception:
        pass
