"""結構化資料互轉（對應原版 CVDataConverter.convert）：JSON、Plist、YAML（只輸出）、
CSV、TSV、Excel (XLSX)、HTML 表格。
"""

from __future__ import annotations

from pathlib import Path

from .. import errors, naming
from . import plistdata, structured, tables, textcodec, xlsx
from .formats import Format
from .profile import Profile
from .structured import Obj, Value


def _save(out: Path, text: str) -> Path:
    try:
        out.write_text(text, "utf-8", newline="")
    except OSError as exc:
        raise errors.write_failed(exc.strerror or str(exc)) from exc
    return out


def _read_json(path: Path, options: dict) -> Value:
    forced = textcodec.forced_codec(options.get("source_encoding"))
    return structured.parse_json(textcodec.decode_file(path, forced).text)


def _read_plist(path: Path) -> Value:
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise errors.unreadable(exc.strerror or str(exc)) from exc
    return plistdata.read_plist(data)


def _write_value(value: Value, fid: str, out: Path, base: str, ctx) -> None:  # noqa: ANN001
    if fid in ("json", "json-pretty"):
        _save(out, structured.write_json(value, pretty=True) + "\n")
    elif fid == "json-min":
        _save(out, structured.write_json(value, pretty=False))
    elif fid == "plist":
        _save(out, plistdata.xml_plist(value))
    elif fid == "bplist":
        out.write_bytes(plistdata.binary_plist(value))
    elif fid == "yaml":
        _save(out, structured.write_yaml(value))
    elif fid == "csv":
        _save(out, tables.delimited(tables.table_from_json(value, base), ","))
    elif fid == "tsv":
        _save(out, tables.delimited(tables.table_from_json(value, base), "\t"))
    elif fid == "xlsx":
        xlsx.write_xlsx([tables.table_from_json(value, base)], out, ctx.check_cancelled)
    elif fid == "html-table":
        _save(out, tables.html_tables([tables.table_from_json(value, base)], base))
    else:
        raise KeyError(fid)


def _write_table(table: tables.Table, fid: str, out: Path, base: str, ctx) -> None:  # noqa: ANN001
    if fid in ("json", "json-pretty"):
        _save(out, structured.write_json(tables.json_from_table(table), pretty=True) + "\n")
    elif fid == "json-min":
        _save(out, structured.write_json(tables.json_from_table(table), pretty=False))
    elif fid == "yaml":
        _save(out, structured.write_yaml(tables.json_from_table(table)))
    elif fid in ("csv", "tsv"):
        _save(out, tables.delimited(table, "," if fid == "csv" else "\t"))
    elif fid == "xlsx":
        xlsx.write_xlsx([table], out, ctx.check_cancelled)
    elif fid == "html-table":
        _save(out, tables.html_tables([table], base))
    else:
        raise KeyError(fid)


def _from_workbook(
    sheets: list[tables.Table], fmt: Format, work: Path, out: Path, base: str
) -> Path:
    fid = fmt.id
    if fid in ("csv", "tsv"):
        delimiter = "," if fid == "csv" else "\t"
        if len(sheets) == 1:
            return _save(out, tables.delimited(sheets[0], delimiter))
        # 多個工作表：各存一檔，放進「檔名-CSV」資料夾
        folder = work / f"{base}-{fid.upper()}"
        folder.mkdir()
        for t in sheets:
            name = naming.join_name(naming.sanitize(t.name), fid)
            _save(naming.unique_path(folder, name), tables.delimited(t, delimiter))
        return folder
    if fid in ("json", "json-pretty", "json-min", "yaml"):
        if len(sheets) == 1:
            value: Value = tables.json_from_table(sheets[0])
        else:
            value = Obj(tuple((t.name, tables.json_from_table(t)) for t in sheets))
        if fid == "yaml":
            return _save(out, structured.write_yaml(value))
        if fid == "json-min":
            return _save(out, structured.write_json(value, pretty=False))
        return _save(out, structured.write_json(value, pretty=True) + "\n")
    if fid == "html-table":
        return _save(out, tables.html_tables(sheets, base))
    raise KeyError(fid)


def convert(
    path: Path, profile: Profile, fmt: Format, options: dict, work: Path, ctx
) -> list[Path]:  # noqa: ANN001, E501
    kind = profile.data_kind
    base = naming.base_name(path)
    out = work / naming.join_name(base, fmt.ext)
    try:
        if kind in ("json", "plist"):
            value = _read_json(path, options) if kind == "json" else _read_plist(path)
            _write_value(value, fmt.id, out, base, ctx)
        elif kind in ("csv", "tsv"):
            forced = textcodec.forced_codec(options.get("source_encoding"))
            table = tables.read_delimited(path, kind == "tsv", forced)
            _write_table(table, fmt.id, out, base, ctx)
        elif kind == "xlsx":
            sheets = xlsx.read_xlsx(path, ctx.check_cancelled)
            out = _from_workbook(sheets, fmt, work, out, base)
        else:
            raise KeyError(fmt.id)
    except KeyError as exc:
        raise errors.unsupported_target(path.name, fmt.display_name) from exc
    ctx.progress(1)
    return [out]
