"""59 個語言的設定表（逐字照搬原版 QSLanguages.all，順序相同）。"""

from __future__ import annotations

from .langbase import C_BLOCK, CSS_LITERALS, DQ, SQ, Language, lc, log_tokens, rule, words

L = Language
TRIPLE_DQ = rule('"""', multiline=True)
TRIPLE_SQ = rule("'''", multiline=True)
CHAR8 = rule("'", max_length=8)
JS_STRINGS = (DQ, SQ, rule("`", multiline=True))

_SWIFT_KW = (
    "associatedtype class deinit enum extension fileprivate func import init inout internal let "
    "open operator private precedencegroup protocol public rethrows static struct subscript "
    "typealias var break case catch continue default defer do else fallthrough for guard if in "
    "repeat return throw switch where while as is super self Self throws async await actor some "
    "any nonisolated isolated consume borrowing consuming mutating nonmutating override required "
    "convenience final lazy weak unowned dynamic indirect optional get set willSet didSet macro "
    "package sending try"
)
_C_KW = (
    "auto break case char const continue default do double else enum extern float for goto if "
    "inline int long register restrict return short signed sizeof static struct switch typedef "
    "union unsigned void volatile while _Bool bool class namespace template typename public "
    "private protected virtual override final new delete this throw try catch using operator "
    "friend explicit constexpr noexcept decltype static_cast dynamic_cast reinterpret_cast "
    "const_cast mutable co_await co_return co_yield concept requires export import module self "
    "super id instancetype nonatomic strong weak assign copy readonly readwrite atomic nullable "
    "nonnull size_t int8_t int16_t int32_t int64_t uint8_t uint16_t uint32_t uint64_t"
)
_JAVA_KW = (
    "abstract assert boolean break byte case catch char class const continue default do double "
    "else enum extends final finally float for goto if implements import instanceof int "
    "interface long native new package private protected public return short static strictfp "
    "super switch synchronized this throw throws transient try void volatile while var record "
    "sealed permits yield"
)
_KOTLIN_KW = (
    "package import class interface fun object val var typealias constructor by companion init "
    "this super typeof is in out throw return break continue when if else do while for try "
    "catch finally as data sealed enum annotation inner open abstract final override private "
    "protected public internal lateinit suspend inline noinline crossinline reified vararg "
    "tailrec operator infix external const expect actual value"
)
_JS_KW = (
    "break case catch class const continue debugger default delete do else export extends "
    "finally for function if import in instanceof let new return super switch this throw try "
    "typeof var void while with yield async await of static get set from as"
)
_TS_KW = _JS_KW + (
    " type interface enum implements namespace declare abstract private protected public "
    "readonly keyof infer is satisfies unique override module"
)
_CSHARP_KW = (
    "abstract as base bool break byte case catch char checked class const continue decimal "
    "default delegate do double else enum event explicit extern finally fixed float for foreach "
    "goto if implicit in int interface internal is lock long namespace new object operator out "
    "override params private protected public readonly ref return sbyte sealed short sizeof "
    "stackalloc static string struct switch this throw try typeof uint ulong unchecked unsafe "
    "ushort using virtual void volatile while var async await dynamic get set value yield "
    "record init required global nameof when where partial"
)
_PHP_KW = (
    "abstract and array as break callable case catch class clone const continue declare "
    "default do echo else elseif empty enddeclare endfor endforeach endif endswitch endwhile "
    "enum extends final finally fn for foreach function global goto if implements include "
    "include_once instanceof insteadof interface isset list match namespace new or print "
    "private protected public readonly require require_once return static switch throw trait "
    "try unset use var while xor yield php"
)
_SQL_KW = (
    "select from where insert into values update set delete create table drop alter add column "
    "index view join inner left right outer full cross on as and or not is in exists between "
    "like ilike limit offset order by group having distinct union all case when then else end "
    "primary key foreign references unique default check constraint if begin commit rollback "
    "transaction with recursive returning asc desc trigger procedure function returns declare "
    "cascade autoincrement pragma replace temporary temp database schema grant revoke using"
)
_DART_KW = (
    "abstract as assert async await break case catch class const continue covariant default "
    "deferred do dynamic else enum export extends extension external factory final finally for "
    "Function get hide if implements import in interface is late library mixin new on operator "
    "part required rethrow return sealed set show static super switch sync this throw try "
    "typedef var void when while with yield base"
)
_SCALA_KW = (
    "abstract case catch class def do else extends final finally for forSome if implicit import "
    "lazy match new object override package private protected return sealed super this throw "
    "trait try type val var while with yield given using enum then export extension end derives "
    "opaque inline transparent"
)
_ZIG_KW = (
    "addrspace align allowzero and anyframe anytype asm async await break callconv catch "
    "comptime const continue defer else enum errdefer error export extern fn for if inline "
    "linksection noalias noinline nosuspend opaque or orelse packed pub resume return struct "
    "suspend switch test threadlocal try union unreachable usingnamespace var volatile while"
)
_CMAKE_KW = (
    "if else elseif endif foreach endforeach while endwhile function endfunction macro endmacro "
    "set unset project cmake_minimum_required add_executable add_library "
    "target_link_libraries include find_package option message install "
    "target_include_directories target_compile_options add_subdirectory return"
)
_SOLIDITY_KW = (
    "pragma solidity contract interface library function modifier event struct enum mapping "
    "public private internal external pure view payable returns return if else for while emit "
    "require revert memory storage calldata constructor import is using override virtual"
)
_SHELL_BUILTINS = (
    "echo printf cd pwd exit test read eval exec trap set type which command builtin wait kill "
    "jobs umask ulimit getopts let mkdir rm cp mv ls cat grep sed awk find chmod chown curl git "
    "sudo brew npm xargs tee sort head tail"
)


def _c_family() -> tuple[Language, ...]:
    return (
        L(
            "swift",
            "Swift",
            keywords=words(_SWIFT_KW),
            literals=words("true false nil"),
            line_comments=lc("//"),
            block_comments=C_BLOCK,
            strings=(TRIPLE_DQ, DQ),
            capitalized_types=True,
            decorators=True,
            preprocessor=True,
        ),
        L(
            "c",
            "C / C++ / Objective-C",
            keywords=words(_C_KW),
            literals=words("true false NULL nullptr nil Nil YES NO"),
            line_comments=lc("//"),
            block_comments=C_BLOCK,
            strings=(DQ, CHAR8),
            preprocessor=True,
            at_keywords=True,
            capitalized_types=True,
        ),
        L(
            "java",
            "Java",
            keywords=words(_JAVA_KW),
            literals=words("true false null"),
            line_comments=lc("//"),
            block_comments=C_BLOCK,
            strings=(TRIPLE_DQ, DQ, CHAR8),
            capitalized_types=True,
            decorators=True,
        ),
        L(
            "kotlin",
            "Kotlin",
            keywords=words(_KOTLIN_KW),
            literals=words("true false null"),
            line_comments=lc("//"),
            block_comments=C_BLOCK,
            strings=(TRIPLE_DQ, DQ, CHAR8),
            capitalized_types=True,
            decorators=True,
            sigils=b"$",
        ),
        L(
            "javascript",
            "JavaScript",
            keywords=words(_JS_KW),
            literals=words("true false null undefined NaN Infinity"),
            builtins=words("console window document require module exports process globalThis"),
            line_comments=lc("//"),
            block_comments=C_BLOCK,
            strings=JS_STRINGS,
            ident_extra=b"$",
            ident_start_extra=b"$",
            decorators=True,
            capitalized_types=True,
        ),
        L(
            "typescript",
            "TypeScript",
            keywords=words(_TS_KW),
            types=words("string number boolean object symbol bigint any unknown never void"),
            literals=words("true false null undefined NaN Infinity"),
            line_comments=lc("//"),
            block_comments=C_BLOCK,
            strings=JS_STRINGS,
            ident_extra=b"$",
            ident_start_extra=b"$",
            decorators=True,
            capitalized_types=True,
        ),
    )


def _scripting() -> tuple[Language, ...]:
    return (
        L(
            "python",
            "Python",
            keywords=words(
                "and as assert async await break class continue def del elif else "
                "except finally for from global if import in is lambda nonlocal not "
                "or pass raise return try while with yield match case"
            ),
            literals=words("True False None"),
            builtins=words(
                "print len range int str float list dict set tuple open isinstance "
                "super enumerate zip map filter sorted min max sum any all abs type "
                "object self cls"
            ),
            line_comments=lc("#"),
            strings=(TRIPLE_DQ, TRIPLE_SQ, DQ, SQ),
            decorators=True,
            capitalized_types=True,
        ),
        L(
            "ruby",
            "Ruby",
            keywords=words(
                "BEGIN END alias and begin break case class def defined? do else "
                "elsif end ensure for if in module next not or redo rescue retry "
                "return super then undef unless until when while yield require "
                "require_relative attr_accessor attr_reader attr_writer private "
                "protected public include extend lambda proc"
            ),
            literals=words("true false nil self"),
            builtins=words("puts print p raise"),
            line_comments=lc("#"),
            block_comments=((b"=begin", b"=end"),),
            sigils=b"@$",
            symbols=True,
            capitalized_types=True,
        ),
        L(
            "go",
            "Go",
            keywords=words(
                "break case chan const continue default defer else fallthrough for "
                "func go goto if import interface map package range return select "
                "struct switch type var"
            ),
            types=words(
                "bool byte complex64 complex128 error float32 float64 int int8 int16 "
                "int32 int64 rune string uint uint8 uint16 uint32 uint64 uintptr any "
                "comparable"
            ),
            literals=words("true false nil iota"),
            builtins=words(
                "append cap close complex copy delete imag len make new panic print "
                "println real recover"
            ),
            line_comments=lc("//"),
            block_comments=C_BLOCK,
            strings=(DQ, rule("`", escape=False, multiline=True), CHAR8),
        ),
        L(
            "rust",
            "Rust",
            keywords=words(
                "as async await break const continue crate dyn else enum extern fn "
                "for if impl in let loop match mod move mut pub ref return self Self "
                "static struct super trait type unsafe use where while macro_rules "
                "union"
            ),
            types=words(
                "i8 i16 i32 i64 i128 isize u8 u16 u32 u64 u128 usize f32 f64 bool char " "str"
            ),
            literals=words("true false"),
            line_comments=lc("//"),
            block_comments=C_BLOCK,
            strings=(rule('"', multiline=True), rule("'", max_length=6)),
            capitalized_types=True,
            hash_attributes=True,
            macro_bang=True,
        ),
        L(
            "csharp",
            "C#",
            keywords=words(_CSHARP_KW),
            literals=words("true false null"),
            line_comments=lc("//"),
            block_comments=C_BLOCK,
            strings=(TRIPLE_DQ, DQ, CHAR8),
            preprocessor=True,
            capitalized_types=True,
        ),
        L(
            "php",
            "PHP",
            keywords=words(_PHP_KW),
            literals=words("true false null TRUE FALSE NULL"),
            line_comments=lc("//", "#"),
            block_comments=C_BLOCK,
            sigils=b"$",
            capitalized_types=True,
        ),
    )


def _shells() -> tuple[Language, ...]:
    return (
        L(
            "shell",
            "Shell",
            keywords=words(
                "if then else elif fi for while until do done case esac function in "
                "select return local export declare readonly unset shift source alias "
                "typeset"
            ),
            builtins=words(_SHELL_BUILTINS),
            literals=words("true false"),
            line_comments=lc("#"),
            hash_comment_needs_space=True,
            strings=(
                rule('"', multiline=True),
                rule("'", escape=False, multiline=True),
                rule("`", multiline=True),
            ),
            sigils=b"$",
            ident_extra=b"-",
        ),
        L(
            "fish",
            "Fish",
            keywords=words(
                "function end if else switch case for in while begin return set and " "or not"
            ),
            builtins=words("echo printf cd set_color string math test contains"),
            literals=words("true false"),
            line_comments=lc("#"),
            hash_comment_needs_space=True,
            sigils=b"$",
        ),
        L(
            "powershell",
            "PowerShell",
            keywords=words(
                "begin break catch class continue data define do dynamicparam else "
                "elseif end exit filter finally for foreach from function if in param "
                "process return switch throw trap try until using var while"
            ),
            literals=words("$true $false $null"),
            case_insensitive=True,
            line_comments=lc("#"),
            block_comments=((b"<#", b"#>"),),
            sigils=b"$",
            ident_extra=b"-",
        ),
        L(
            "batch",
            "Batch",
            keywords=words(
                "echo set if else goto call exit for in do not exist defined "
                "errorlevel setlocal endlocal pause shift start cd copy del move"
            ),
            case_insensitive=True,
            line_comments=lc("::", "rem "),
            strings=(DQ,),
            sigils=b"%",
            line_labels=False,
        ),
        L(
            "sql",
            "SQL",
            keywords=words(_SQL_KW),
            types=words(
                "integer int bigint smallint text varchar char boolean bool real float "
                "double numeric decimal date time timestamp datetime blob json jsonb uuid "
                "serial"
            ),
            literals=words("null true false"),
            case_insensitive=True,
            line_comments=lc("--"),
            block_comments=C_BLOCK,
            strings=(SQ, DQ),
        ),
        L(
            "lua",
            "Lua",
            keywords=words(
                "and break do else elseif end for function goto if in local not or "
                "repeat return then until while"
            ),
            literals=words("true false nil"),
            block_comments=((b"--[[", b"]]"),),
            line_comments=lc("--"),
            strings=(rule("[[", "]]", escape=False, multiline=True), DQ, SQ),
        ),
    )


def _functional() -> tuple[Language, ...]:
    return (
        L(
            "dart",
            "Dart",
            keywords=words(_DART_KW),
            literals=words("true false null"),
            line_comments=lc("//"),
            block_comments=C_BLOCK,
            strings=(TRIPLE_DQ, TRIPLE_SQ, DQ, SQ),
            capitalized_types=True,
            decorators=True,
            sigils=b"$",
        ),
        L(
            "scala",
            "Scala",
            keywords=words(_SCALA_KW),
            literals=words("true false null"),
            line_comments=lc("//"),
            block_comments=C_BLOCK,
            strings=(TRIPLE_DQ, DQ, CHAR8),
            capitalized_types=True,
            decorators=True,
        ),
        L(
            "haskell",
            "Haskell",
            keywords=words(
                "case class data default deriving do else foreign if import in infix "
                "infixl infixr instance let module newtype of then type where "
                "qualified as hiding"
            ),
            literals=words("True False Nothing Just"),
            line_comments=lc("--"),
            block_comments=((b"{-", b"-}"),),
            strings=(DQ, rule("'", max_length=6)),
            capitalized_types=True,
            ident_extra=b"'",
        ),
        L(
            "elixir",
            "Elixir",
            keywords=words(
                "def defp defmodule defmacro defstruct defprotocol defimpl do end fn "
                "if else unless case cond when with for receive after try catch rescue "
                "raise import require alias use quote unquote and or not in"
            ),
            literals=words("true false nil"),
            line_comments=lc("#"),
            strings=(TRIPLE_DQ, DQ, SQ),
            symbols=True,
            sigils=b"@",
            capitalized_types=True,
        ),
        L(
            "erlang",
            "Erlang",
            keywords=words(
                "after begin case catch end fun if of receive try when andalso orelse "
                "div rem band bor bxor bnot bsl bsr not and or xor module export "
                "import record define"
            ),
            literals=words("true false undefined ok error"),
            line_comments=lc("%"),
            strings=(DQ,),
            capitalized_types=True,
            at_keywords=False,
        ),
        L(
            "clojure",
            "Clojure / Lisp",
            keywords=words(
                "def defn defn- defmacro defrecord defprotocol let fn if do loop recur "
                "when when-not cond case ns require import quote try catch finally "
                "throw and or not lambda define setq defun progn"
            ),
            literals=words("nil true false t"),
            line_comments=lc(";"),
            strings=(rule('"', multiline=True),),
            ident_extra=b"-?!*+<>=/.",
            symbols=True,
        ),
        L(
            "julia",
            "Julia",
            keywords=words(
                "function end if elseif else for while begin let return break "
                "continue module using import export struct mutable abstract type "
                "primitive macro quote do try catch finally const global local in "
                "where"
            ),
            literals=words("true false nothing missing"),
            block_comments=((b"#=", b"=#"),),
            line_comments=lc("#"),
            strings=(TRIPLE_DQ, DQ),
            capitalized_types=True,
            decorators=True,
        ),
        L(
            "zig",
            "Zig",
            keywords=words(_ZIG_KW),
            types=words(
                "i8 i16 i32 i64 u8 u16 u32 u64 usize isize f32 f64 bool void type " "anyerror"
            ),
            literals=words("true false null undefined"),
            line_comments=lc("//"),
            decorators=True,
            capitalized_types=True,
        ),
    )


def _config_like() -> tuple[Language, ...]:
    return (
        L(
            "nix",
            "Nix",
            keywords=words("let in with rec inherit if then else assert import"),
            literals=words("true false null"),
            line_comments=lc("#"),
            block_comments=C_BLOCK,
            strings=(rule("''", escape=False, multiline=True), rule('"', multiline=True)),
            ident_extra=b"-",
        ),
        L(
            "hcl",
            "HCL / Terraform",
            keywords=words(
                "resource data variable output module provider locals terraform for "
                "in if for_each count depends_on"
            ),
            literals=words("true false null"),
            line_comments=lc("#", "//"),
            block_comments=C_BLOCK,
            strings=(DQ,),
            key_separators=b"",
        ),
        L(
            "graphql",
            "GraphQL",
            keywords=words(
                "query mutation subscription fragment on type interface union enum "
                "input scalar schema extend directive implements"
            ),
            literals=words("true false null"),
            line_comments=lc("#"),
            strings=(TRIPLE_DQ, DQ),
            decorators=True,
            sigils=b"$",
            capitalized_types=True,
        ),
        L(
            "protobuf",
            "Protocol Buffers",
            keywords=words(
                "syntax package import option message enum service rpc returns "
                "repeated optional required oneof map reserved extend stream edition"
            ),
            types=words(
                "double float int32 int64 uint32 uint64 sint32 sint64 fixed32 fixed64 "
                "sfixed32 sfixed64 bool string bytes"
            ),
            literals=words("true false"),
            line_comments=lc("//"),
            block_comments=C_BLOCK,
            capitalized_types=True,
        ),
        L(
            "perl",
            "Perl",
            keywords=words(
                "my our local sub if elsif else unless while until for foreach do "
                "last next redo return use no package require BEGIN END print qw eq ne "
                "lt gt le ge and or not"
            ),
            line_comments=lc("#"),
            sigils=b"$@%",
        ),
        L(
            "r",
            "R",
            keywords=words(
                "if else repeat while function for in next break library require " "return"
            ),
            literals=words("TRUE FALSE NULL Inf NaN NA"),
            line_comments=lc("#"),
            ident_extra=b".",
        ),
        L(
            "vb",
            "Visual Basic",
            keywords=words(
                "dim as if then else elseif end sub function return for each next "
                "while wend do loop select case class module public private protected "
                "friend shared new imports namespace property get set byval byref "
                "optional integer string boolean double object and or not is"
            ),
            literals=words("true false nothing"),
            case_insensitive=True,
            line_comments=lc("'"),
            strings=(rule('"', escape=False),),
        ),
        L(
            "ocaml",
            "OCaml / F#",
            keywords=words(
                "let rec in match with fun function if then else type of module "
                "struct sig end open begin val mutable and or not when"
            ),
            literals=words("true false"),
            block_comments=((b"(*", b"*)"),),
            line_comments=lc("//"),
            strings=(DQ,),
            capitalized_types=True,
        ),
        L(
            "elm",
            "Elm",
            keywords=words(
                "module exposing import as type alias case of if then else let in " "port"
            ),
            line_comments=lc("--"),
            block_comments=((b"{-", b"-}"),),
            strings=(TRIPLE_DQ, DQ),
            capitalized_types=True,
        ),
    )


def _build_tools() -> tuple[Language, ...]:
    return (
        L(
            "asm",
            "Assembly",
            line_comments=lc(";", "#", "//"),
            block_comments=C_BLOCK,
            dot_directives=True,
            line_labels=True,
            strings=(DQ,),
        ),
        L(
            "cmake",
            "CMake",
            keywords=words(_CMAKE_KW),
            case_insensitive=True,
            line_comments=lc("#"),
            sigils=b"$",
            strings=(rule('"', multiline=True),),
        ),
        L(
            "make",
            "Makefile",
            keywords=words(
                "ifeq ifneq ifdef ifndef else endif include define endef export "
                "override unexport vpath"
            ),
            line_comments=lc("#"),
            sigils=b"$",
            line_labels=True,
            ident_extra=b"-.",
            ident_start_extra=b".",
            strings=(DQ, SQ),
        ),
        L(
            "docker",
            "Dockerfile",
            keywords=words(
                "from run cmd label maintainer expose env add copy entrypoint volume "
                "user workdir arg onbuild stopsignal healthcheck shell as"
            ),
            case_insensitive=True,
            line_comments=lc("#"),
            sigils=b"$",
        ),
        L(
            "solidity",
            "Solidity",
            keywords=words(_SOLIDITY_KW),
            types=words("uint int address bool string bytes uint256 int256 bytes32 uint8"),
            literals=words("true false"),
            line_comments=lc("//"),
            block_comments=C_BLOCK,
            capitalized_types=True,
        ),
        L(
            "latex",
            "LaTeX",
            line_comments=lc("%"),
            strings=(),
            sigils=b"\\",
            sigil_tok="k",
            wrap=True,
        ),
    )


def _markup_and_data() -> tuple[Language, ...]:
    return (
        L(
            "css",
            "CSS",
            mode="css",
            literals=CSS_LITERALS,
            block_comments=C_BLOCK,
            at_keywords=True,
            ident_extra=b"-",
            ident_start_extra=b"-",
        ),
        L(
            "scss",
            "SCSS / Sass / Less",
            mode="css",
            literals=CSS_LITERALS,
            block_comments=C_BLOCK,
            line_comments=lc("//"),
            at_keywords=True,
            ident_extra=b"-",
            ident_start_extra=b"-",
            sigils=b"$",
        ),
        L("json", "JSON", literals=words("true false null"), strings=(DQ,), key_separators=b":"),
        L(
            "jsonc",
            "JSON（含註解）",
            literals=words("true false null Infinity NaN"),
            line_comments=lc("//"),
            block_comments=C_BLOCK,
            strings=(DQ, SQ),
            key_separators=b":",
        ),
        L(
            "strings",
            "Strings",
            line_comments=lc("//"),
            block_comments=C_BLOCK,
            strings=(DQ,),
            key_separators=b"=",
        ),
        L("xml", "XML", mode="markup"),
        L("html", "HTML", mode="markup", embedded=(("script", "javascript"), ("style", "css"))),
        L(
            "vue",
            "Vue / Svelte",
            mode="markup",
            embedded=(("script", "typescript"), ("style", "scss")),
        ),
        L("yaml", "YAML", mode="keyValue", line_comments=lc("#"), key_separators=b":"),
        L("toml", "TOML", mode="keyValue", line_comments=lc("#"), key_separators=b"="),
        L("ini", "INI / 設定檔", mode="keyValue", line_comments=lc("#", ";"), key_separators=b"=:"),
        L(
            "properties",
            "Properties",
            mode="keyValue",
            line_comments=lc("#", "!"),
            key_separators=b"=:",
        ),
        L("dotenv", "環境變數 (.env)", mode="keyValue", line_comments=lc("#"), key_separators=b"="),
        L("diff", "Diff / Patch", mode="diff"),
        L("log", "記錄檔", mode="log", wrap=True, word_toks=log_tokens(), strings=(DQ,)),
        L("ignore", "忽略規則", line_comments=lc("#"), strings=()),
        L("subtitles", "字幕", strings=(), wrap=True),
        L("text", "純文字", mode="plain", wrap=True),
    )


def table() -> tuple[Language, ...]:
    """全部語言（順序與原版相同）。"""
    return (
        _c_family()
        + _scripting()
        + _shells()
        + _functional()
        + _config_like()
        + _build_tools()
        + _markup_and_data()
    )


__all__ = ["table", "DQ", "SQ"]
