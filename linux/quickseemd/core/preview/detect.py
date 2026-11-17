"""依檔名、副檔名、shebang、檔案類型與內容猜語言（對應原版 QSLanguages.detect／sniff／forHint）。"""

from __future__ import annotations

import json
import logging
import os
import re
from collections.abc import Mapping
from types import MappingProxyType

from .langbase import Language
from .languages import BY_ID, value_language
from .page import FileInfo

log = logging.getLogger(__name__)

_EXT_GROUPS: tuple[tuple[str, str], ...] = (
    ("swift", "swift"),
    ("c", "c h m mm cpp cc cxx hpp hh hxx ino cu metal"),
    ("java", "java"),
    ("kotlin", "kt kts"),
    ("javascript", "js mjs cjs jsx"),
    ("typescript", "ts tsx mts cts"),
    ("python", "py pyw pyi"),
    ("ruby", "rb rake gemspec podspec"),
    ("go", "go"),
    ("rust", "rs"),
    ("csharp", "cs csx"),
    ("php", "php phtml"),
    ("shell", "sh bash zsh ksh command bashrc zshrc profile"),
    ("fish", "fish"),
    ("powershell", "ps1 psm1 psd1"),
    ("batch", "bat cmd"),
    ("sql", "sql"),
    ("lua", "lua"),
    ("dart", "dart"),
    ("scala", "scala sc"),
    ("haskell", "hs lhs"),
    ("elixir", "ex exs"),
    ("erlang", "erl hrl"),
    ("clojure", "clj cljs cljc edn el lisp scm"),
    ("julia", "jl"),
    ("zig", "zig"),
    ("nix", "nix"),
    ("hcl", "tf tfvars hcl"),
    ("graphql", "graphql gql"),
    ("protobuf", "proto"),
    ("perl", "pl pm"),
    ("r", "r"),
    ("vb", "vb vbs"),
    ("ocaml", "ml mli fs fsx"),
    ("elm", "elm"),
    ("asm", "asm s"),
    ("cmake", "cmake"),
    ("make", "mk mak make"),
    ("docker", "dockerfile containerfile"),
    ("solidity", "sol"),
    ("latex", "tex sty cls bib"),
    ("css", "css"),
    ("scss", "scss sass less"),
    ("json", "json geojson webmanifest har"),
    ("jsonc", "jsonc json5 code-workspace"),
    ("strings", "strings"),
    (
        "xml",
        "xml plist xsd xsl xslt svg storyboard xib entitlements csproj vcxproj resx rss atom "
        "wsdl kml gpx",
    ),
    ("html", "html htm xhtml"),
    ("vue", "vue svelte astro"),
    ("yaml", "yaml yml"),
    ("toml", "toml cfg"),
    ("ini", "ini conf editorconfig gitconfig gitmodules npmrc desktop"),
    ("properties", "properties"),
    ("dotenv", "env"),
    ("diff", "diff patch"),
    ("log", "log"),
    ("ignore", "gitignore dockerignore gitattributes"),
    ("subtitles", "srt vtt"),
    ("text", "txt text rst adoc org nfo"),
    ("groovy", "groovy gradle"),  # 原版就指向不存在的語言；前面的 groovy／gradle 規則會先處理
)
EXT_MAP: Mapping[str, str] = MappingProxyType(
    {ext: lang_id for lang_id, exts in _EXT_GROUPS for ext in exts.split()}
)

NAME_MAP: Mapping[str, str] = MappingProxyType(
    {
        **dict.fromkeys(("makefile", "gnumakefile"), "make"),
        **dict.fromkeys(("dockerfile", "containerfile"), "docker"),
        **dict.fromkeys(
            (
                "gemfile rakefile podfile fastfile appfile brewfile vagrantfile guardfile "
                "dangerfile matchfile"
            ).split(),
            "ruby",
        ),
        "cmakelists.txt": "cmake",
        "jenkinsfile": "java",
        **dict.fromkeys(
            (
                ".bashrc .bash_profile .zshrc .zprofile .profile .zshenv .bash_aliases "
                ".zlogin .bash_logout"
            ).split(),
            "shell",
        ),
        **dict.fromkeys((".gitconfig", ".gitmodules", ".npmrc", ".editorconfig"), "ini"),
        **dict.fromkeys((".gitignore", ".dockerignore", ".gitattributes"), "ignore"),
        **dict.fromkeys("license readme changelog authors notice copying".split(), "text"),
        "package.resolved": "json",
        "cargo.lock": "toml",
        "podfile.lock": "yaml",
        "pipfile": "toml",
    }
)

SHEBANG_MAP: Mapping[str, str] = MappingProxyType(
    {
        **dict.fromkeys(("python", "python3", "python2"), "python"),
        **dict.fromkeys(("bash", "sh", "zsh", "dash", "ksh"), "shell"),
        "fish": "fish",
        "node": "javascript",
        "deno": "typescript",
        "bun": "javascript",
        "ruby": "ruby",
        "perl": "perl",
        "php": "php",
        "lua": "lua",
        "rscript": "r",
        "swift": "swift",
        "pwsh": "powershell",
    }
)

# 對應原版用 UTType 判斷的那一步：依序比對（含子類型）
MIME_CHECKS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("application/json",), "json"),
    (("application/xml", "text/xml"), "xml"),
    (("text/html",), "html"),
    (("application/yaml", "application/x-yaml", "text/yaml", "text/x-yaml"), "yaml"),
    (("application/x-shellscript", "text/x-shellscript"), "shell"),
    (("text/x-python", "text/x-python3"), "python"),
    (("application/x-ruby", "text/x-ruby"), "ruby"),
    (("text/x-swift",), "swift"),
    (("text/x-csrc", "text/x-chdr", "text/x-c"), "c"),
    (("text/x-objcsrc", "text/x-objchdr"), "c"),
    (("text/x-c++src", "text/x-c++hdr"), "c"),
    (("application/javascript", "text/javascript"), "javascript"),
    (("application/x-plist", "text/x-plist"), "xml"),
)
LOG_MIME = "text/x-log"

HINT_ALIASES: Mapping[str, str] = MappingProxyType(
    {
        **dict.fromkeys(("js", "node", "jsx"), "javascript"),
        **dict.fromkeys(("ts", "tsx"), "typescript"),
        **dict.fromkeys(("py", "python3"), "python"),
        "rb": "ruby",
        **dict.fromkeys(("sh", "bash", "zsh", "console", "shellscript", "terminal"), "shell"),
        **dict.fromkeys(("objc", "objective-c", "cpp", "c++"), "c"),
        "rs": "rust",
        "kt": "kotlin",
        **dict.fromkeys(("cs", "c#"), "csharp"),
        "yml": "yaml",
        "dockerfile": "docker",
        "makefile": "make",
        **dict.fromkeys(("ps1", "pwsh"), "powershell"),
        **dict.fromkeys(("sass", "less"), "scss"),
        **dict.fromkeys(("jsonc", "json5"), "jsonc"),
        **dict.fromkeys(("tf", "terraform"), "hcl"),
        "patch": "diff",
        **dict.fromkeys(("plaintext", "txt"), "text"),
        **dict.fromkeys(("svg", "plist"), "xml"),
        "golang": "go",
        **dict.fromkeys(("elisp", "lisp"), "clojure"),
        **dict.fromkeys(("fsharp", "f#"), "ocaml"),
    }
)

_YAML_FIRST_LINE = re.compile(r"[A-Za-z_][\w-]*:\s")
_WHITESPACE_NL = " \t\n\r\x0b\x0c\x85\xa0        "
_WHITESPACE_NL += "        　"


def _reject_constant(_name: str) -> None:
    raise ValueError("NaN／Infinity 不是合法 JSON")


def parses_as_json(text: str) -> bool:
    """嚴格的 JSON 解析（允許單一值，不允許 NaN／Infinity）。"""
    try:
        json.loads(text, parse_constant=_reject_constant)
    except (ValueError, RecursionError):
        return False
    return True


def sniff(text: str) -> Language | None:
    """只看內容判斷常見格式。"""
    head = text[:4096].strip(_WHITESPACE_NL)
    if head.startswith(("{", "[")):
        if parses_as_json(text):
            return BY_ID["json"]
        if head.startswith("[[") or ("\n[" in head and " = " in head):
            return BY_ID["toml"]
    if head.startswith(("<?xml", "<plist", "<svg")):
        return BY_ID["xml"]
    lower = head.lower()
    if lower.startswith(("<!doctype html", "<html")):
        return BY_ID["html"]
    if head.startswith("diff --git") or (head.startswith("--- ") and "\n+++ " in head):
        return BY_ID["diff"]
    if head.startswith("---\n") or (_YAML_FIRST_LINE.match(head) and "\n" in head):
        return BY_ID["yaml"]
    return None


def _shebang(text: str) -> Language | None:
    if not text.startswith("#!"):
        return None
    first = text.split("\n", 1)[0][2:]
    parts = [p for p in first.split(" ") if p]
    if parts and parts[0].endswith("/env"):
        parts = [p for p in parts[1:] if not p.startswith("-")]
    if not parts:
        return None
    prog = os.path.basename(parts[0].rstrip("/")).lower()
    base = prog.strip("0123456789.")
    lang_id = SHEBANG_MAP.get(prog) or SHEBANG_MAP.get(base)
    return BY_ID.get(lang_id) if lang_id else None


def _is_a(mime: str, parent: str) -> bool:
    if mime == parent:
        return True
    try:
        from gi.repository import Gio  # noqa: PLC0415

        return bool(Gio.content_type_is_a(mime, parent))
    except (ImportError, ValueError):
        return False


def by_mime(mime: str | None) -> Language | None:
    if not mime:
        return None
    for parents, lang_id in MIME_CHECKS:
        if any(_is_a(mime, p) for p in parents):
            return BY_ID[lang_id]
    if mime == LOG_MIME:
        return BY_ID["log"]
    return None


def _by_name(info: FileInfo, text: str) -> Language | None:
    lower = info.name.lower()
    lang_id = NAME_MAP.get(lower)
    if lang_id in BY_ID:
        return BY_ID[lang_id]
    if lower.startswith(".env"):
        return BY_ID["dotenv"]
    if lower.startswith("dockerfile") or lower.endswith(".dockerfile"):
        return BY_ID["docker"]
    if info.ext in ("groovy", "gradle"):
        return BY_ID["java"]
    if info.ext == "lock":
        return sniff(text) or BY_ID["yaml"]
    lang_id = EXT_MAP.get(info.ext)
    return BY_ID.get(lang_id) if lang_id else None


def detect(info: FileInfo, text: str) -> Language:
    """依序：完整檔名 → .env／Dockerfile → 副檔名 → shebang → 檔案類型 → 內容 → 純文字。"""
    return (
        _by_name(info, text) or _shebang(text) or by_mime(info.mime) or sniff(text) or BY_ID["text"]
    )


def for_hint(hint: str | None) -> Language | None:
    """Markdown 程式碼區塊（與筆記本）的語言提示 → 語言；認不出來回傳 None。"""
    if hint is None:
        return None
    h = hint.lower().strip(" \t")
    if not h:
        return None
    cut = min((i for i in (h.find(" "), h.find("{")) if i >= 0), default=-1)
    if cut >= 0:
        h = h[:cut]
    alias = HINT_ALIASES.get(h)
    if alias in BY_ID:
        return BY_ID[alias]
    if h in BY_ID:
        return BY_ID[h]
    found = detect(FileInfo.virtual(f"x.{h}"), "")
    return None if found.id == "text" else found


__all__ = ["detect", "for_hint", "sniff", "value_language", "by_mime", "EXT_MAP", "NAME_MAP"]
