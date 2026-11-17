"""主程式的樣式（GTK CSS）。

原版每個工具有自己的顏色（tint）；這裡依顏色動態產生 .qs-t-RRGGBB 類別，
放在頁面最外層，裡面的主要按鈕、選取中的選項、進度條都會用那個顏色。
"""

from __future__ import annotations

import logging

from gi.repository import Gdk, Gtk

LOG = logging.getLogger(__name__)
RGB = tuple[float, float, float]

BASE_CSS = """
.qs-hub-title { font-size: 30px; font-weight: 900; }
.qs-hero-title { font-size: 34px; font-weight: 900; }
.qs-result-title { font-size: 30px; font-weight: 900; }
.qs-fail-title { font-size: 28px; font-weight: 900; }
.qs-int-title { font-size: 28px; font-weight: 900; }
.qs-pct { font-size: 36px; font-weight: 900; font-feature-settings: "tnum"; }
.qs-subtitle { font-size: 16px; color: alpha(currentColor, 0.62); }
.qs-dim { color: alpha(currentColor, 0.6); }
.qs-small { font-size: 12px; }
.qs-tiny { font-size: 11px; }
.qs-mono { font-family: monospace; font-feature-settings: "tnum"; }
.qs-bold { font-weight: 700; }
.qs-semibold { font-weight: 600; }
.qs-normal { font-weight: 400; }
.qs-warning { color: @warning_color; }
.qs-success { color: @success_color; }
.qs-error-icon { color: @error_color; }
.qs-ok-icon { color: @success_color; }

entry.qs-search { border-radius: 999px; min-height: 38px; padding: 0 14px; }

button.qs-card {
  background-color: @card_bg_color; border-radius: 14px; padding: 18px;
  border: 1px solid alpha(currentColor, 0.08); box-shadow: 0 2px 4px alpha(black, 0.05);
  margin-top: 3px; margin-bottom: 0; font-weight: 400; transition: all 150ms ease-out;
}
button.qs-card:hover {
  margin-top: 0; margin-bottom: 3px; box-shadow: 0 7px 14px alpha(black, 0.13);
}
.qs-card-title { font-size: 17px; font-weight: 700; }

.qs-tile { color: white; border: 1px solid alpha(white, 0.18); }
.qs-tile image { color: white; }
.qs-badge {
  background-color: white; border-radius: 999px; font-weight: 900; font-size: 9px;
  padding: 0 4px; border: 0.5px solid alpha(black, 0.12);
}

button.qs-primary {
  color: white; border-radius: 12px; min-height: 46px; padding: 0 22px;
  font-size: 16px; font-weight: 600; background-color: @accent_bg_color;
}
button.qs-primary:disabled { background-color: alpha(gray, 0.45); color: white; box-shadow: none; }
button.qs-secondary {
  border-radius: 10px; min-height: 36px; padding: 0 16px; font-weight: 500;
  background-color: @card_bg_color; border: 1px solid alpha(currentColor, 0.14);
}
button.qs-link { background: none; box-shadow: none; color: alpha(currentColor, 0.62);
  font-weight: 500; padding: 4px 6px; }
button.qs-link:hover { color: currentColor; }

.qs-drop {
  border: 3px dashed @accent_color; border-radius: 18px; margin: 14px;
  background-color: alpha(@accent_color, 0.06);
}
.qs-drop-label { font-size: 20px; font-weight: 600; }

button.qs-pill {
  border-radius: 999px; padding: 6px 14px; background-color: @card_bg_color;
  border: 1px solid alpha(currentColor, 0.12); font-weight: 600;
}
button.qs-pill:hover { background-color: mix(@card_bg_color, currentColor, 0.08); }
.qs-count {
  background-color: alpha(currentColor, 0.07); border-radius: 999px; padding: 1px 7px;
  font-weight: 600; color: alpha(currentColor, 0.6);
}
.qs-banner {
  background-color: alpha(@accent_color, 0.09); border: 1px solid alpha(@accent_color, 0.35);
  border-radius: 14px; padding: 14px;
}

.qs-files-bg { background-color: mix(@window_bg_color, currentColor, 0.035); }
.qs-file-card {
  background-color: @card_bg_color; border-radius: 12px; padding: 10px;
  border: 1px solid alpha(currentColor, 0.08); box-shadow: 0 1px 3px alpha(black, 0.05);
}
.qs-file-card:hover { box-shadow: 0 4px 10px alpha(black, 0.12); }
.qs-file-card .qs-remove { opacity: 0; transition: opacity 120ms; }
.qs-file-card:hover .qs-remove { opacity: 1; }
button.qs-remove {
  min-width: 22px; min-height: 22px; padding: 0; border-radius: 999px;
  background-color: alpha(black, 0.65); color: white;
}
.qs-order {
  color: white; border-radius: 999px; min-width: 20px; min-height: 20px;
  font-size: 11px; font-weight: 700; background-color: @accent_bg_color;
}
button.qs-addmore {
  border: 1.5px dashed alpha(currentColor, 0.2); border-radius: 12px; background: none;
  min-height: 236px; font-weight: 500;
}
.qs-plus-circle {
  border-radius: 999px; min-width: 54px; min-height: 54px; color: white;
  background-color: @accent_bg_color;
}
.qs-thumb { border-radius: 4px; }

.qs-panel { background-color: @window_bg_color; }
.qs-group-title { font-weight: 600; }
button.qs-choice-card {
  border-radius: 10px; padding: 10px 12px; background-color: @card_bg_color;
  border: 1px solid alpha(currentColor, 0.12); font-weight: 400;
}
.qs-choice-badge {
  color: white; border-radius: 999px; padding: 0 6px; font-size: 10px; font-weight: 700;
  background-color: @accent_bg_color;
}
button.qs-chip {
  border-radius: 999px; padding: 4px 12px; min-height: 0; font-weight: 400;
  background-color: alpha(currentColor, 0.07);
}
button.qs-dot {
  min-width: 26px; min-height: 26px; padding: 0; border-radius: 999px;
  border: 2px solid alpha(currentColor, 0.15);
}
button.qs-dot.selected { border: 3px solid @accent_color; }
button.qs-grid-cell { min-width: 44px; min-height: 30px; padding: 2px 4px; font-weight: 400; }
button.qs-grid-cell:checked { font-weight: 700; }
.qs-note {
  background-color: alpha(currentColor, 0.05); border-radius: 8px; padding: 8px 10px;
}
.qs-note.warn { background-color: alpha(@warning_color, 0.12); }
.qs-slider-value { font-family: monospace; font-weight: 600; }

.qs-dimmer { background-color: alpha(black, 0.16); }
.qs-run-card {
  background-color: @card_bg_color; border-radius: 20px; padding: 30px;
  box-shadow: 0 10px 30px alpha(black, 0.2);
}
button.qs-next {
  border-radius: 999px; padding: 6px 12px 6px 6px; background-color: @card_bg_color;
  border: 1px solid alpha(currentColor, 0.1); font-weight: 500;
}

.qs-int-card, .qs-cv-card {
  background-color: @card_bg_color; border-radius: 16px; padding: 18px;
  border: 1px solid alpha(currentColor, 0.08);
}
.qs-cv-card { border-radius: 12px; padding: 14px; }
.qs-int-tile { border-radius: 12px; min-width: 48px; min-height: 48px; color: white; }
button.qs-format-chip {
  border-radius: 999px; padding: 4px 12px; min-height: 0; font-weight: 500;
  background-color: alpha(currentColor, 0.06); border: 1px solid alpha(currentColor, 0.1);
}
button.qs-format-chip.selected {
  background-color: @accent_bg_color; color: @accent_fg_color; border-color: transparent;
}
button.qs-format-chip.partial { border: 1.5px dashed @accent_color; }
.qs-job-row {
  border-radius: 10px; padding: 8px; background-color: @card_bg_color;
  border: 1px solid alpha(currentColor, 0.07);
}
.qs-job-row.selected {
  background-color: alpha(@accent_color, 0.12); border-color: alpha(@accent_color, 0.5);
}
.qs-target-pill {
  border-radius: 999px; padding: 2px 9px; font-size: 12px; font-weight: 600;
  background-color: alpha(@accent_color, 0.14); color: @accent_color;
}
.qs-target-pill.empty {
  background: none; border: 1px dashed alpha(currentColor, 0.3); color: alpha(currentColor, 0.6);
}
.qs-target-pill.bad { background-color: alpha(@warning_color, 0.14); color: @warning_color; }
.qs-cv-drop {
  border: 2px dashed alpha(currentColor, 0.22); border-radius: 16px; padding: 24px;
}
.qs-cv-drop.compact { padding: 10px; border-radius: 12px; }
.qs-cv-drop.active { border-color: @accent_color; background-color: alpha(@accent_color, 0.06); }
.qs-cv-header-icon {
  border-radius: 10px; min-width: 38px; min-height: 38px; color: white;
  background-image: linear-gradient(135deg, rgb(82, 140, 255), rgb(140, 92, 250));
}

.qs-org-cell { border-radius: 10px; padding: 8px; border: 1px solid transparent; }
.qs-org-cell:hover { border-color: alpha(currentColor, 0.18); }
.qs-org-page { background-color: white; box-shadow: 0 1px 3px alpha(black, 0.18); }
.qs-org-tools {
  background-color: @card_bg_color; border-radius: 999px; padding: 2px;
  border: 1px solid alpha(currentColor, 0.12);
}
.qs-org-cell .qs-org-tools { opacity: 0; }
.qs-org-cell:hover .qs-org-tools, .qs-org-cell.selected .qs-org-tools { opacity: 1; }
button.qs-org-blank {
  border: 1.5px dashed alpha(currentColor, 0.2); border-radius: 10px; background: none;
}
.qs-stat { font-size: 22px; font-weight: 700; font-feature-settings: "tnum"; }
.qs-video-box { background-color: black; border-radius: 12px; }
.qs-audio-box { border-radius: 12px; }
.qs-range-length { font-size: 18px; font-weight: 700; font-feature-settings: "tnum"; }
.qs-sidebar-heading { font-size: 12px; font-weight: 700; color: alpha(currentColor, 0.55);
  margin-top: 10px; }
"""


def _hex(rgb: RGB) -> str:
    return "".join(f"{int(round(min(max(c, 0.0), 1.0) * 255)):02x}" for c in rgb)


def _rgba(rgb: RGB, alpha: float | None = None) -> str:
    r, g, b = (int(round(min(max(c, 0.0), 1.0) * 255)) for c in rgb)
    return f"rgb({r}, {g}, {b})" if alpha is None else f"rgba({r}, {g}, {b}, {alpha})"


def _tint_rules(name: str, rgb: RGB) -> str:
    c, c88 = _rgba(rgb), _rgba(rgb, 0.88)
    sel = f".{name}"
    return f"""
{sel}.qs-tile {{ background-image: linear-gradient(to bottom, {c88}, {c});
  box-shadow: 0 2px 5px {_rgba(rgb, 0.35)}; }}
{sel} .qs-tinted, {sel}.qs-tinted {{ color: {c}; }}
{sel} .qs-badge, .qs-badge{sel} {{ color: {c}; }}
{sel} button.qs-primary, button.qs-primary{sel} {{ background-color: {c};
  box-shadow: 0 3px 8px {_rgba(rgb, 0.35)}; }}
{sel} button.qs-primary:disabled, button.qs-primary{sel}:disabled {{
  background-color: alpha(gray, 0.45); box-shadow: none; }}
button.qs-card{sel}:hover {{ border: 1.5px solid {_rgba(rgb, 0.7)}; }}
{sel} button.qs-choice-card.selected {{ background-color: {_rgba(rgb, 0.10)};
  border: 1.5px solid {c}; }}
{sel} button.qs-choice-card.selected .qs-radio {{ color: {c}; }}
{sel} .qs-choice-badge {{ background-color: {c}; }}
{sel} button.qs-chip.selected {{ background-color: {c}; color: white; font-weight: 600; }}
{sel} button.qs-dot.selected {{ border-color: {c}; }}
{sel} scale highlight {{ background-color: {c}; }}
{sel} .qs-slider-value {{ color: {c}; }}
{sel} progressbar progress {{ background-color: {c}; }}
{sel} .qs-order {{ background-color: {c}; }}
{sel} .qs-plus-circle {{ background-color: {c}; }}
{sel} button.qs-addmore:hover {{ border-color: {c}; }}
{sel} .qs-file-card:hover {{ border-color: {_rgba(rgb, 0.6)}; }}
{sel} .qs-drop {{ border-color: {c}; background-color: {_rgba(rgb, 0.06)}; }}
{sel}.qs-bg, {sel} .qs-bg {{ background-image: linear-gradient(to bottom, {_rgba(rgb, 0.07)},
  alpha(white, 0) 320px); }}
button.qs-pill{sel}.selected {{ background-color: {c}; color: white; border-color: transparent; }}
{sel} .qs-org-cell.selected {{ background-color: {_rgba(rgb, 0.12)}; border: 2px solid {c}; }}
{sel} .qs-range-length {{ color: {c}; }}
.qs-int-tile{sel} {{ background-color: {c}; }}
"""


class _Styles:
    def __init__(self) -> None:
        self.base = Gtk.CssProvider()
        self.dynamic = Gtk.CssProvider()
        self.rules: dict[str, str] = {}
        self.installed = False
        self.errors: list[str] = []
        for provider in (self.base, self.dynamic):
            provider.connect("parsing-error", self._on_error)

    def _on_error(self, _provider: Gtk.CssProvider, section: Gtk.CssSection, error: object) -> None:
        text = f"{section.to_string()}: {getattr(error, 'message', error)}"
        self.errors.append(text)
        LOG.warning("樣式錯誤：%s", text)

    def install(self) -> None:
        display = Gdk.Display.get_default()
        if self.installed or display is None:
            return
        self.base.load_from_string(BASE_CSS)
        prio = Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        Gtk.StyleContext.add_provider_for_display(display, self.base, prio)
        Gtk.StyleContext.add_provider_for_display(display, self.dynamic, prio + 1)
        self.installed = True
        self._reload()

    def add(self, name: str, css: str) -> str:
        if name not in self.rules:
            self.rules[name] = css
            self._reload()
        return name

    def _reload(self) -> None:
        if self.installed:
            self.dynamic.load_from_string("\n".join(self.rules.values()))


_STYLES = _Styles()


def install() -> None:
    _STYLES.install()


def parse_errors() -> list[str]:
    """樣式解析錯誤（測試用來確認樣式表沒有寫錯）。"""
    return list(_STYLES.errors)


def tint_class(rgb: RGB) -> str:
    """工具顏色的類別名稱（第一次用到時產生規則）。"""
    name = f"qs-t-{_hex(rgb)}"
    if name not in _STYLES.rules:
        _STYLES.add(name, _tint_rules(name, rgb))
    return name


def tile_size_class(size: int) -> str:
    name = f"qs-tile-{size}"
    radius = round(size * 0.26, 1)
    css = f".{name} {{ border-radius: {radius}px; min-width: {size}px; min-height: {size}px; }}"
    return _STYLES.add(name, css)


CHECKERBOARD = """button.qs-dot-transparent {
  background-color: white;
  background-image: linear-gradient(45deg, #bbbbbb 25%, transparent 25%, transparent 75%, #bbbbbb 75%),
    linear-gradient(45deg, #bbbbbb 25%, transparent 25%, transparent 75%, #bbbbbb 75%);
  background-size: 10px 10px;
  background-position: 0 0, 5px 5px;
}"""


def dot_class(color: str) -> str:
    """色點按鈕的底色：#rrggbb，或 "transparent"（棋盤格）。"""
    if color == "transparent":
        return _STYLES.add("qs-dot-transparent", CHECKERBOARD)
    clean = color.lstrip("#").lower()
    if len(clean) != 6 or any(c not in "0123456789abcdef" for c in clean):
        clean = "888888"
    name = f"qs-dot-{clean}"
    return _STYLES.add(name, f"button.{name} {{ background-color: #{clean}; }}")
