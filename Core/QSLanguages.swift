import Foundation
import UniformTypeIdentifiers

enum QSMode { case generic, css, log, markup, keyValue, diff, plain }

struct QSStringRule {
    let open: [UInt8]
    let close: [UInt8]
    let escape: UInt8?
    let multiline: Bool
    let maxLength: Int

    init(_ open: String, _ close: String? = nil, escape: Bool = true, multiline: Bool = false, maxLength: Int = 0) {
        self.open = Array(open.utf8)
        self.close = Array((close ?? open).utf8)
        self.escape = escape ? 92 : nil
        self.multiline = multiline
        self.maxLength = maxLength
    }
}

struct QSLanguage {
    var id: String
    var name: String
    var mode: QSMode = .generic
    var keywords: Set<String> = []
    var types: Set<String> = []
    var literals: Set<String> = []
    var builtins: Set<String> = []
    var wordToks: [String: QSTok] = [:]
    var lineComments: [[UInt8]] = []
    var blockComments: [([UInt8], [UInt8])] = []
    var strings: [QSStringRule] = [QSStringRule("\""), QSStringRule("'")]
    var caseInsensitive = false
    var capitalizedTypes = false
    var preprocessor = false
    var sigils: [UInt8] = []
    var sigilTok: QSTok = .a
    var decorators = false
    var atKeywords = false
    var identExtra: [UInt8] = []
    var identStartExtra: [UInt8] = []
    var keySeparators: [UInt8] = []
    var lineLabels = false
    var hashAttributes = false
    var macroBang = false
    var symbols = false
    var dotDirectives = false
    var hashCommentNeedsSpace = false
    var wrap = false
    var embedded: [String: String] = [:]

    init(_ id: String, _ name: String, _ configure: (inout QSLanguage) -> Void = { _ in }) {
        self.id = id
        self.name = name
        configure(&self)
    }
}

private func words(_ s: String) -> Set<String> { Set(s.split(whereSeparator: { $0 == " " || $0 == "\n" }).map(String.init)) }
private func lc(_ s: String...) -> [[UInt8]] { s.map { Array($0.utf8) } }
private func bc(_ pairs: (String, String)...) -> [([UInt8], [UInt8])] { pairs.map { (Array($0.0.utf8), Array($0.1.utf8)) } }
private let cStyle = bc(("/*", "*/"))

enum QSLanguages {
    static let all: [QSLanguage] = [
        QSLanguage("swift", "Swift") {
            $0.keywords = words("associatedtype class deinit enum extension fileprivate func import init inout internal let open operator private precedencegroup protocol public rethrows static struct subscript typealias var break case catch continue default defer do else fallthrough for guard if in repeat return throw switch where while as is super self Self throws async await actor some any nonisolated isolated consume borrowing consuming mutating nonmutating override required convenience final lazy weak unowned dynamic indirect optional get set willSet didSet macro package sending try")
            $0.literals = words("true false nil")
            $0.lineComments = lc("//"); $0.blockComments = cStyle
            $0.strings = [QSStringRule("\"\"\"", multiline: true), QSStringRule("\"")]
            $0.capitalizedTypes = true; $0.decorators = true; $0.preprocessor = true
        },
        QSLanguage("c", "C / C++ / Objective-C") {
            $0.keywords = words("auto break case char const continue default do double else enum extern float for goto if inline int long register restrict return short signed sizeof static struct switch typedef union unsigned void volatile while _Bool bool class namespace template typename public private protected virtual override final new delete this throw try catch using operator friend explicit constexpr noexcept decltype static_cast dynamic_cast reinterpret_cast const_cast mutable co_await co_return co_yield concept requires export import module self super id instancetype nonatomic strong weak assign copy readonly readwrite atomic nullable nonnull size_t int8_t int16_t int32_t int64_t uint8_t uint16_t uint32_t uint64_t")
            $0.literals = words("true false NULL nullptr nil Nil YES NO")
            $0.lineComments = lc("//"); $0.blockComments = cStyle
            $0.strings = [QSStringRule("\""), QSStringRule("'", maxLength: 8)]
            $0.preprocessor = true; $0.atKeywords = true; $0.capitalizedTypes = true
        },
        QSLanguage("java", "Java") {
            $0.keywords = words("abstract assert boolean break byte case catch char class const continue default do double else enum extends final finally float for goto if implements import instanceof int interface long native new package private protected public return short static strictfp super switch synchronized this throw throws transient try void volatile while var record sealed permits yield")
            $0.literals = words("true false null")
            $0.lineComments = lc("//"); $0.blockComments = cStyle
            $0.strings = [QSStringRule("\"\"\"", multiline: true), QSStringRule("\""), QSStringRule("'", maxLength: 8)]
            $0.capitalizedTypes = true; $0.decorators = true
        },
        QSLanguage("kotlin", "Kotlin") {
            $0.keywords = words("package import class interface fun object val var typealias constructor by companion init this super typeof is in out throw return break continue when if else do while for try catch finally as data sealed enum annotation inner open abstract final override private protected public internal lateinit suspend inline noinline crossinline reified vararg tailrec operator infix external const expect actual value")
            $0.literals = words("true false null")
            $0.lineComments = lc("//"); $0.blockComments = cStyle
            $0.strings = [QSStringRule("\"\"\"", multiline: true), QSStringRule("\""), QSStringRule("'", maxLength: 8)]
            $0.capitalizedTypes = true; $0.decorators = true; $0.sigils = [36]
        },
        QSLanguage("javascript", "JavaScript") {
            $0.keywords = words("break case catch class const continue debugger default delete do else export extends finally for function if import in instanceof let new return super switch this throw try typeof var void while with yield async await of static get set from as")
            $0.literals = words("true false null undefined NaN Infinity")
            $0.builtins = words("console window document require module exports process globalThis")
            $0.lineComments = lc("//"); $0.blockComments = cStyle
            $0.strings = [QSStringRule("\""), QSStringRule("'"), QSStringRule("`", multiline: true)]
            $0.identExtra = [36]; $0.identStartExtra = [36]; $0.decorators = true; $0.capitalizedTypes = true
        },
        QSLanguage("typescript", "TypeScript") {
            $0.keywords = words("break case catch class const continue debugger default delete do else export extends finally for function if import in instanceof let new return super switch this throw try typeof var void while with yield async await of static get set from as type interface enum implements namespace declare abstract private protected public readonly keyof infer is satisfies unique override module")
            $0.types = words("string number boolean object symbol bigint any unknown never void")
            $0.literals = words("true false null undefined NaN Infinity")
            $0.lineComments = lc("//"); $0.blockComments = cStyle
            $0.strings = [QSStringRule("\""), QSStringRule("'"), QSStringRule("`", multiline: true)]
            $0.identExtra = [36]; $0.identStartExtra = [36]; $0.decorators = true; $0.capitalizedTypes = true
        },
        QSLanguage("python", "Python") {
            $0.keywords = words("and as assert async await break class continue def del elif else except finally for from global if import in is lambda nonlocal not or pass raise return try while with yield match case")
            $0.literals = words("True False None")
            $0.builtins = words("print len range int str float list dict set tuple open isinstance super enumerate zip map filter sorted min max sum any all abs type object self cls")
            $0.lineComments = lc("#")
            $0.strings = [QSStringRule("\"\"\"", multiline: true), QSStringRule("'''", multiline: true), QSStringRule("\""), QSStringRule("'")]
            $0.decorators = true; $0.capitalizedTypes = true
        },
        QSLanguage("ruby", "Ruby") {
            $0.keywords = words("BEGIN END alias and begin break case class def defined? do else elsif end ensure for if in module next not or redo rescue retry return super then undef unless until when while yield require require_relative attr_accessor attr_reader attr_writer private protected public include extend lambda proc")
            $0.literals = words("true false nil self")
            $0.builtins = words("puts print p raise")
            $0.lineComments = lc("#"); $0.blockComments = bc(("=begin", "=end"))
            $0.sigils = [64, 36]; $0.symbols = true; $0.capitalizedTypes = true
        },
        QSLanguage("go", "Go") {
            $0.keywords = words("break case chan const continue default defer else fallthrough for func go goto if import interface map package range return select struct switch type var")
            $0.types = words("bool byte complex64 complex128 error float32 float64 int int8 int16 int32 int64 rune string uint uint8 uint16 uint32 uint64 uintptr any comparable")
            $0.literals = words("true false nil iota")
            $0.builtins = words("append cap close complex copy delete imag len make new panic print println real recover")
            $0.lineComments = lc("//"); $0.blockComments = cStyle
            $0.strings = [QSStringRule("\""), QSStringRule("`", escape: false, multiline: true), QSStringRule("'", maxLength: 8)]
        },
        QSLanguage("rust", "Rust") {
            $0.keywords = words("as async await break const continue crate dyn else enum extern fn for if impl in let loop match mod move mut pub ref return self Self static struct super trait type unsafe use where while macro_rules union")
            $0.types = words("i8 i16 i32 i64 i128 isize u8 u16 u32 u64 u128 usize f32 f64 bool char str")
            $0.literals = words("true false")
            $0.lineComments = lc("//"); $0.blockComments = cStyle
            $0.strings = [QSStringRule("\"", multiline: true), QSStringRule("'", maxLength: 6)]
            $0.capitalizedTypes = true; $0.hashAttributes = true; $0.macroBang = true
        },
        QSLanguage("csharp", "C#") {
            $0.keywords = words("abstract as base bool break byte case catch char checked class const continue decimal default delegate do double else enum event explicit extern finally fixed float for foreach goto if implicit in int interface internal is lock long namespace new object operator out override params private protected public readonly ref return sbyte sealed short sizeof stackalloc static string struct switch this throw try typeof uint ulong unchecked unsafe ushort using virtual void volatile while var async await dynamic get set value yield record init required global nameof when where partial")
            $0.literals = words("true false null")
            $0.lineComments = lc("//"); $0.blockComments = cStyle
            $0.strings = [QSStringRule("\"\"\"", multiline: true), QSStringRule("\""), QSStringRule("'", maxLength: 8)]
            $0.preprocessor = true; $0.capitalizedTypes = true
        },
        QSLanguage("php", "PHP") {
            $0.keywords = words("abstract and array as break callable case catch class clone const continue declare default do echo else elseif empty enddeclare endfor endforeach endif endswitch endwhile enum extends final finally fn for foreach function global goto if implements include include_once instanceof insteadof interface isset list match namespace new or print private protected public readonly require require_once return static switch throw trait try unset use var while xor yield php")
            $0.literals = words("true false null TRUE FALSE NULL")
            $0.lineComments = lc("//", "#"); $0.blockComments = cStyle
            $0.sigils = [36]; $0.capitalizedTypes = true
        },
        QSLanguage("shell", "Shell") {
            $0.keywords = words("if then else elif fi for while until do done case esac function in select return local export declare readonly unset shift source alias typeset")
            $0.builtins = words("echo printf cd pwd exit test read eval exec trap set type which command builtin wait kill jobs umask ulimit getopts let mkdir rm cp mv ls cat grep sed awk find chmod chown curl git sudo brew npm xargs tee sort head tail")
            $0.literals = words("true false")
            $0.lineComments = lc("#"); $0.hashCommentNeedsSpace = true
            $0.strings = [QSStringRule("\"", multiline: true), QSStringRule("'", escape: false, multiline: true), QSStringRule("`", multiline: true)]
            $0.sigils = [36]; $0.identExtra = [45]
        },
        QSLanguage("fish", "Fish") {
            $0.keywords = words("function end if else switch case for in while begin return set and or not")
            $0.builtins = words("echo printf cd set_color string math test contains")
            $0.literals = words("true false")
            $0.lineComments = lc("#"); $0.hashCommentNeedsSpace = true; $0.sigils = [36]
        },
        QSLanguage("powershell", "PowerShell") {
            $0.keywords = words("begin break catch class continue data define do dynamicparam else elseif end exit filter finally for foreach from function if in param process return switch throw trap try until using var while")
            $0.literals = words("$true $false $null")
            $0.caseInsensitive = true; $0.lineComments = lc("#"); $0.blockComments = bc(("<#", "#>"))
            $0.sigils = [36]; $0.identExtra = [45]
        },
        QSLanguage("batch", "Batch") {
            $0.keywords = words("echo set if else goto call exit for in do not exist defined errorlevel setlocal endlocal pause shift start cd copy del move")
            $0.caseInsensitive = true; $0.lineComments = lc("::", "rem ")
            $0.strings = [QSStringRule("\"")]; $0.sigils = [37]; $0.lineLabels = false
        },
        QSLanguage("sql", "SQL") {
            $0.keywords = words("select from where insert into values update set delete create table drop alter add column index view join inner left right outer full cross on as and or not is in exists between like ilike limit offset order by group having distinct union all case when then else end primary key foreign references unique default check constraint if begin commit rollback transaction with recursive returning asc desc trigger procedure function returns declare cascade autoincrement pragma replace temporary temp database schema grant revoke using")
            $0.types = words("integer int bigint smallint text varchar char boolean bool real float double numeric decimal date time timestamp datetime blob json jsonb uuid serial")
            $0.literals = words("null true false")
            $0.caseInsensitive = true; $0.lineComments = lc("--"); $0.blockComments = cStyle
            $0.strings = [QSStringRule("'"), QSStringRule("\"")]
        },
        QSLanguage("lua", "Lua") {
            $0.keywords = words("and break do else elseif end for function goto if in local not or repeat return then until while")
            $0.literals = words("true false nil")
            $0.blockComments = bc(("--[[", "]]")); $0.lineComments = lc("--")
            $0.strings = [QSStringRule("[[", "]]", escape: false, multiline: true), QSStringRule("\""), QSStringRule("'")]
        },
        QSLanguage("dart", "Dart") {
            $0.keywords = words("abstract as assert async await break case catch class const continue covariant default deferred do dynamic else enum export extends extension external factory final finally for Function get hide if implements import in interface is late library mixin new on operator part required rethrow return sealed set show static super switch sync this throw try typedef var void when while with yield base")
            $0.literals = words("true false null")
            $0.lineComments = lc("//"); $0.blockComments = cStyle
            $0.strings = [QSStringRule("\"\"\"", multiline: true), QSStringRule("'''", multiline: true), QSStringRule("\""), QSStringRule("'")]
            $0.capitalizedTypes = true; $0.decorators = true; $0.sigils = [36]
        },
        QSLanguage("scala", "Scala") {
            $0.keywords = words("abstract case catch class def do else extends final finally for forSome if implicit import lazy match new object override package private protected return sealed super this throw trait try type val var while with yield given using enum then export extension end derives opaque inline transparent")
            $0.literals = words("true false null")
            $0.lineComments = lc("//"); $0.blockComments = cStyle
            $0.strings = [QSStringRule("\"\"\"", multiline: true), QSStringRule("\""), QSStringRule("'", maxLength: 8)]
            $0.capitalizedTypes = true; $0.decorators = true
        },
        QSLanguage("haskell", "Haskell") {
            $0.keywords = words("case class data default deriving do else foreign if import in infix infixl infixr instance let module newtype of then type where qualified as hiding")
            $0.literals = words("True False Nothing Just")
            $0.lineComments = lc("--"); $0.blockComments = bc(("{-", "-}"))
            $0.strings = [QSStringRule("\""), QSStringRule("'", maxLength: 6)]
            $0.capitalizedTypes = true; $0.identExtra = [39]
        },
        QSLanguage("elixir", "Elixir") {
            $0.keywords = words("def defp defmodule defmacro defstruct defprotocol defimpl do end fn if else unless case cond when with for receive after try catch rescue raise import require alias use quote unquote and or not in")
            $0.literals = words("true false nil")
            $0.lineComments = lc("#")
            $0.strings = [QSStringRule("\"\"\"", multiline: true), QSStringRule("\""), QSStringRule("'")]
            $0.symbols = true; $0.sigils = [64]; $0.capitalizedTypes = true
        },
        QSLanguage("erlang", "Erlang") {
            $0.keywords = words("after begin case catch end fun if of receive try when andalso orelse div rem band bor bxor bnot bsl bsr not and or xor module export import record define")
            $0.literals = words("true false undefined ok error")
            $0.lineComments = lc("%"); $0.strings = [QSStringRule("\"")]
            $0.capitalizedTypes = true; $0.atKeywords = false
        },
        QSLanguage("clojure", "Clojure / Lisp") {
            $0.keywords = words("def defn defn- defmacro defrecord defprotocol let fn if do loop recur when when-not cond case ns require import quote try catch finally throw and or not lambda define setq defun progn")
            $0.literals = words("nil true false t")
            $0.lineComments = lc(";"); $0.strings = [QSStringRule("\"", multiline: true)]
            $0.identExtra = Array("-?!*+<>=/.".utf8); $0.symbols = true
        },
        QSLanguage("julia", "Julia") {
            $0.keywords = words("function end if elseif else for while begin let return break continue module using import export struct mutable abstract type primitive macro quote do try catch finally const global local in where")
            $0.literals = words("true false nothing missing")
            $0.blockComments = bc(("#=", "=#")); $0.lineComments = lc("#")
            $0.strings = [QSStringRule("\"\"\"", multiline: true), QSStringRule("\"")]
            $0.capitalizedTypes = true; $0.decorators = true
        },
        QSLanguage("zig", "Zig") {
            $0.keywords = words("addrspace align allowzero and anyframe anytype asm async await break callconv catch comptime const continue defer else enum errdefer error export extern fn for if inline linksection noalias noinline nosuspend opaque or orelse packed pub resume return struct suspend switch test threadlocal try union unreachable usingnamespace var volatile while")
            $0.types = words("i8 i16 i32 i64 u8 u16 u32 u64 usize isize f32 f64 bool void type anyerror")
            $0.literals = words("true false null undefined")
            $0.lineComments = lc("//"); $0.decorators = true; $0.capitalizedTypes = true
        },
        QSLanguage("nix", "Nix") {
            $0.keywords = words("let in with rec inherit if then else assert import")
            $0.literals = words("true false null")
            $0.lineComments = lc("#"); $0.blockComments = cStyle
            $0.strings = [QSStringRule("''", escape: false, multiline: true), QSStringRule("\"", multiline: true)]
            $0.identExtra = [45]
        },
        QSLanguage("hcl", "HCL / Terraform") {
            $0.keywords = words("resource data variable output module provider locals terraform for in if for_each count depends_on")
            $0.literals = words("true false null")
            $0.lineComments = lc("#", "//"); $0.blockComments = cStyle
            $0.strings = [QSStringRule("\"")]; $0.keySeparators = []
        },
        QSLanguage("graphql", "GraphQL") {
            $0.keywords = words("query mutation subscription fragment on type interface union enum input scalar schema extend directive implements")
            $0.literals = words("true false null")
            $0.lineComments = lc("#"); $0.strings = [QSStringRule("\"\"\"", multiline: true), QSStringRule("\"")]
            $0.decorators = true; $0.sigils = [36]; $0.capitalizedTypes = true
        },
        QSLanguage("protobuf", "Protocol Buffers") {
            $0.keywords = words("syntax package import option message enum service rpc returns repeated optional required oneof map reserved extend stream edition")
            $0.types = words("double float int32 int64 uint32 uint64 sint32 sint64 fixed32 fixed64 sfixed32 sfixed64 bool string bytes")
            $0.literals = words("true false")
            $0.lineComments = lc("//"); $0.blockComments = cStyle; $0.capitalizedTypes = true
        },
        QSLanguage("perl", "Perl") {
            $0.keywords = words("my our local sub if elsif else unless while until for foreach do last next redo return use no package require BEGIN END print qw eq ne lt gt le ge and or not")
            $0.lineComments = lc("#"); $0.sigils = [36, 64, 37]
        },
        QSLanguage("r", "R") {
            $0.keywords = words("if else repeat while function for in next break library require return")
            $0.literals = words("TRUE FALSE NULL Inf NaN NA")
            $0.lineComments = lc("#"); $0.identExtra = [46]
        },
        QSLanguage("vb", "Visual Basic") {
            $0.keywords = words("dim as if then else elseif end sub function return for each next while wend do loop select case class module public private protected friend shared new imports namespace property get set byval byref optional integer string boolean double object and or not is")
            $0.literals = words("true false nothing")
            $0.caseInsensitive = true; $0.lineComments = lc("'"); $0.strings = [QSStringRule("\"", escape: false)]
        },
        QSLanguage("ocaml", "OCaml / F#") {
            $0.keywords = words("let rec in match with fun function if then else type of module struct sig end open begin val mutable and or not when")
            $0.literals = words("true false")
            $0.blockComments = bc(("(*", "*)")); $0.lineComments = lc("//"); $0.strings = [QSStringRule("\"")]
            $0.capitalizedTypes = true
        },
        QSLanguage("elm", "Elm") {
            $0.keywords = words("module exposing import as type alias case of if then else let in port")
            $0.lineComments = lc("--"); $0.blockComments = bc(("{-", "-}")); $0.strings = [QSStringRule("\"\"\"", multiline: true), QSStringRule("\"")]
            $0.capitalizedTypes = true
        },
        QSLanguage("asm", "Assembly") {
            $0.lineComments = lc(";", "#", "//"); $0.blockComments = cStyle
            $0.dotDirectives = true; $0.lineLabels = true; $0.strings = [QSStringRule("\"")]
        },
        QSLanguage("cmake", "CMake") {
            $0.keywords = words("if else elseif endif foreach endforeach while endwhile function endfunction macro endmacro set unset project cmake_minimum_required add_executable add_library target_link_libraries include find_package option message install target_include_directories target_compile_options add_subdirectory return")
            $0.caseInsensitive = true; $0.lineComments = lc("#"); $0.sigils = [36]; $0.strings = [QSStringRule("\"", multiline: true)]
        },
        QSLanguage("make", "Makefile") {
            $0.keywords = words("ifeq ifneq ifdef ifndef else endif include define endef export override unexport vpath")
            $0.lineComments = lc("#"); $0.sigils = [36]; $0.lineLabels = true; $0.identExtra = [45, 46]; $0.identStartExtra = [46]
            $0.strings = [QSStringRule("\""), QSStringRule("'")]
        },
        QSLanguage("docker", "Dockerfile") {
            $0.keywords = words("from run cmd label maintainer expose env add copy entrypoint volume user workdir arg onbuild stopsignal healthcheck shell as")
            $0.caseInsensitive = true; $0.lineComments = lc("#"); $0.sigils = [36]
        },
        QSLanguage("solidity", "Solidity") {
            $0.keywords = words("pragma solidity contract interface library function modifier event struct enum mapping public private internal external pure view payable returns return if else for while emit require revert memory storage calldata constructor import is using override virtual")
            $0.types = words("uint int address bool string bytes uint256 int256 bytes32 uint8")
            $0.literals = words("true false")
            $0.lineComments = lc("//"); $0.blockComments = cStyle; $0.capitalizedTypes = true
        },
        QSLanguage("latex", "LaTeX") {
            $0.lineComments = lc("%"); $0.strings = []; $0.sigils = [92]; $0.sigilTok = .k; $0.wrap = true
        },
        QSLanguage("css", "CSS") {
            $0.mode = .css
            $0.literals = words("auto none inherit initial unset block inline flex grid absolute relative fixed sticky solid transparent bold normal")
            $0.blockComments = cStyle; $0.atKeywords = true
            $0.identExtra = [45]; $0.identStartExtra = [45]
        },
        QSLanguage("scss", "SCSS / Sass / Less") {
            $0.mode = .css
            $0.literals = words("auto none inherit initial unset block inline flex grid absolute relative fixed sticky solid transparent bold normal")
            $0.blockComments = cStyle; $0.lineComments = lc("//"); $0.atKeywords = true
            $0.identExtra = [45]; $0.identStartExtra = [45]; $0.sigils = [36]
        },
        QSLanguage("json", "JSON") {
            $0.literals = words("true false null")
            $0.strings = [QSStringRule("\"")]; $0.keySeparators = [58]
        },
        QSLanguage("jsonc", "JSON（含註解）") {
            $0.literals = words("true false null Infinity NaN")
            $0.lineComments = lc("//"); $0.blockComments = cStyle
            $0.strings = [QSStringRule("\""), QSStringRule("'")]; $0.keySeparators = [58]
        },
        QSLanguage("strings", "Strings") {
            $0.lineComments = lc("//"); $0.blockComments = cStyle
            $0.strings = [QSStringRule("\"")]; $0.keySeparators = [61]
        },
        QSLanguage("xml", "XML") { $0.mode = .markup },
        QSLanguage("html", "HTML") { $0.mode = .markup; $0.embedded = ["script": "javascript", "style": "css"] },
        QSLanguage("vue", "Vue / Svelte") { $0.mode = .markup; $0.embedded = ["script": "typescript", "style": "scss"] },
        QSLanguage("yaml", "YAML") { $0.mode = .keyValue; $0.lineComments = lc("#"); $0.keySeparators = [58] },
        QSLanguage("toml", "TOML") { $0.mode = .keyValue; $0.lineComments = lc("#"); $0.keySeparators = [61] },
        QSLanguage("ini", "INI / 設定檔") { $0.mode = .keyValue; $0.lineComments = lc("#", ";"); $0.keySeparators = [61, 58] },
        QSLanguage("properties", "Properties") { $0.mode = .keyValue; $0.lineComments = lc("#", "!"); $0.keySeparators = [61, 58] },
        QSLanguage("dotenv", "環境變數 (.env)") { $0.mode = .keyValue; $0.lineComments = lc("#"); $0.keySeparators = [61] },
        QSLanguage("diff", "Diff / Patch") { $0.mode = .diff },
        QSLanguage("log", "記錄檔") {
            $0.mode = .log; $0.wrap = true
            $0.wordToks = [
                "ERROR": .le, "ERR": .le, "FATAL": .le, "CRITICAL": .le, "CRIT": .le, "SEVERE": .le, "PANIC": .le, "FAILED": .le, "FAILURE": .le, "Error": .le, "Exception": .le, "error": .le, "fatal": .le,
                "WARN": .lw, "WARNING": .lw, "Warning": .lw, "warning": .lw, "warn": .lw,
                "INFO": .li, "NOTICE": .li, "Info": .li,
                "DEBUG": .ld, "TRACE": .ld, "VERBOSE": .ld, "Debug": .ld, "debug": .ld,
            ]
            $0.strings = [QSStringRule("\"")]
        },
        QSLanguage("ignore", "忽略規則") { $0.lineComments = lc("#"); $0.strings = [] },
        QSLanguage("subtitles", "字幕") { $0.strings = []; $0.wrap = true },
        QSLanguage("text", "純文字") { $0.mode = .plain; $0.wrap = true },
    ]

    static let byID: [String: QSLanguage] = Dictionary(uniqueKeysWithValues: all.map { ($0.id, $0) })

    /// 鍵值設定檔的「值」所用的小型語言。
    static func valueLanguage(for id: String) -> QSLanguage {
        QSLanguage("value-\(id)", "") {
            $0.literals = words(id == "yaml" ? "true false null yes no on off True False Null ~" : "true false")
            $0.strings = [QSStringRule("\""), QSStringRule("'")]
            $0.lineComments = id == "ini" || id == "properties" ? [] : lc("#")
            $0.hashCommentNeedsSpace = true
            $0.sigils = id == "yaml" ? [38, 42] : (id == "dotenv" ? [36] : [])
        }
    }

    private static let extMap: [String: String] = {
        var m: [String: String] = [:]
        func add(_ id: String, _ exts: String) { for e in exts.split(separator: " ") { m[String(e)] = id } }
        add("swift", "swift")
        add("c", "c h m mm cpp cc cxx hpp hh hxx ino cu metal")
        add("java", "java")
        add("kotlin", "kt kts")
        add("javascript", "js mjs cjs jsx")
        add("typescript", "ts tsx mts cts")
        add("python", "py pyw pyi")
        add("ruby", "rb rake gemspec podspec")
        add("go", "go")
        add("rust", "rs")
        add("csharp", "cs csx")
        add("php", "php phtml")
        add("shell", "sh bash zsh ksh command bashrc zshrc profile")
        add("fish", "fish")
        add("powershell", "ps1 psm1 psd1")
        add("batch", "bat cmd")
        add("sql", "sql")
        add("lua", "lua")
        add("dart", "dart")
        add("scala", "scala sc")
        add("haskell", "hs lhs")
        add("elixir", "ex exs")
        add("erlang", "erl hrl")
        add("clojure", "clj cljs cljc edn el lisp scm")
        add("julia", "jl")
        add("zig", "zig")
        add("nix", "nix")
        add("hcl", "tf tfvars hcl")
        add("graphql", "graphql gql")
        add("protobuf", "proto")
        add("perl", "pl pm")
        add("r", "r")
        add("vb", "vb vbs")
        add("ocaml", "ml mli fs fsx")
        add("elm", "elm")
        add("asm", "asm s")
        add("cmake", "cmake")
        add("make", "mk mak make")
        add("docker", "dockerfile containerfile")
        add("solidity", "sol")
        add("latex", "tex sty cls bib")
        add("css", "css")
        add("scss", "scss sass less")
        add("json", "json geojson webmanifest har")
        add("jsonc", "jsonc json5 code-workspace")
        add("strings", "strings")
        add("xml", "xml plist xsd xsl xslt svg storyboard xib entitlements csproj vcxproj resx rss atom wsdl kml gpx")
        add("html", "html htm xhtml")
        add("vue", "vue svelte astro")
        add("yaml", "yaml yml")
        add("toml", "toml cfg")
        add("ini", "ini conf editorconfig gitconfig gitmodules npmrc desktop")
        add("properties", "properties")
        add("dotenv", "env")
        add("diff", "diff patch")
        add("log", "log")
        add("ignore", "gitignore dockerignore gitattributes")
        add("subtitles", "srt vtt")
        add("text", "txt text rst adoc org nfo")
        add("groovy", "groovy gradle")
        return m
    }()

    private static let nameMap: [String: String] = [
        "makefile": "make", "gnumakefile": "make", "dockerfile": "docker", "containerfile": "docker",
        "gemfile": "ruby", "rakefile": "ruby", "podfile": "ruby", "fastfile": "ruby", "appfile": "ruby", "brewfile": "ruby",
        "vagrantfile": "ruby", "guardfile": "ruby", "dangerfile": "ruby", "matchfile": "ruby",
        "cmakelists.txt": "cmake", "jenkinsfile": "java",
        ".bashrc": "shell", ".bash_profile": "shell", ".zshrc": "shell", ".zprofile": "shell", ".profile": "shell",
        ".zshenv": "shell", ".bash_aliases": "shell", ".zlogin": "shell", ".bash_logout": "shell",
        ".gitconfig": "ini", ".gitmodules": "ini", ".npmrc": "ini", ".editorconfig": "ini",
        ".gitignore": "ignore", ".dockerignore": "ignore", ".gitattributes": "ignore",
        "license": "text", "readme": "text", "changelog": "text", "authors": "text", "notice": "text", "copying": "text",
        "package.resolved": "json", "cargo.lock": "toml", "podfile.lock": "yaml", "pipfile": "toml",
    ]

    private static let shebangMap: [String: String] = [
        "python": "python", "python3": "python", "python2": "python", "bash": "shell", "sh": "shell", "zsh": "shell",
        "dash": "shell", "ksh": "shell", "fish": "fish", "node": "javascript", "deno": "typescript", "bun": "javascript",
        "ruby": "ruby", "perl": "perl", "php": "php", "lua": "lua", "rscript": "r", "swift": "swift", "pwsh": "powershell",
    ]

    /// 依檔名、副檔名、shebang、檔案類型、內容猜測語言。
    static func detect(file: QSFile, text: String) -> QSLanguage {
        let lowerName = file.name.lowercased()
        if let id = nameMap[lowerName], let l = byID[id] { return l }
        if lowerName.hasPrefix(".env") { return byID["dotenv"]! }
        if lowerName.hasPrefix("dockerfile") || lowerName.hasSuffix(".dockerfile") { return byID["docker"]! }
        if file.ext == "groovy" || file.ext == "gradle" { return byID["java"]! }
        if file.ext == "lock" { return sniff(text) ?? byID["yaml"]! }
        if let id = extMap[file.ext], let l = byID[id] { return l }

        // shebang
        if text.hasPrefix("#!") {
            let first = text.prefix(while: { $0 != "\n" }).dropFirst(2)
            var parts = first.split(separator: " ").map(String.init)
            if let p = parts.first, p.hasSuffix("/env") {
                parts.removeFirst()
                parts.removeAll(where: { $0.hasPrefix("-") })
            }
            if let prog = parts.first.map({ ($0 as NSString).lastPathComponent.lowercased() }) {
                let base = prog.trimmingCharacters(in: CharacterSet.decimalDigits.union(CharacterSet(charactersIn: ".")))
                if let id = shebangMap[prog] ?? shebangMap[base], let l = byID[id] { return l }
            }
        }

        if let t = file.contentType {
            let checks: [(UTType, String)] = [
                (.json, "json"), (.xml, "xml"), (.html, "html"), (.yaml, "yaml"), (.shellScript, "shell"),
                (.pythonScript, "python"), (.rubyScript, "ruby"), (.swiftSource, "swift"), (.cSource, "c"),
                (.objectiveCSource, "c"), (.cPlusPlusSource, "c"), (.javaScript, "javascript"), (.propertyList, "xml"),
            ]
            for (u, id) in checks where t.conforms(to: u) { return byID[id]! }
            if t.identifier == "com.apple.log" { return byID["log"]! }
        }
        return sniff(text) ?? byID["text"]!
    }

    /// 只看內容判斷常見格式。
    static func sniff(_ text: String) -> QSLanguage? {
        let head = text.prefix(4096).trimmingCharacters(in: .whitespacesAndNewlines)
        if head.hasPrefix("{") || head.hasPrefix("[") {
            if let data = text.data(using: .utf8), (try? JSONSerialization.jsonObject(with: data, options: [.fragmentsAllowed])) != nil { return byID["json"] }
            if head.hasPrefix("[[") || head.contains("\n[") && head.contains(" = ") { return byID["toml"] }
        }
        if head.hasPrefix("<?xml") || head.hasPrefix("<plist") || head.hasPrefix("<svg") { return byID["xml"] }
        if head.lowercased().hasPrefix("<!doctype html") || head.lowercased().hasPrefix("<html") { return byID["html"] }
        if head.hasPrefix("diff --git") || head.hasPrefix("--- ") && head.contains("\n+++ ") { return byID["diff"] }
        if head.hasPrefix("---\n") || head.range(of: #"^[A-Za-z_][\w-]*:\s"#, options: .regularExpression) != nil && head.contains("\n") { return byID["yaml"] }
        return nil
    }
}
