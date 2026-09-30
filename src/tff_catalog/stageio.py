"""Stage files: how one stage hands its dataclasses to a later one on disk. Frozen contract.

Every stage is its own command, so stages meet only through files under
``build/stage/``. ``STAGE_FILES`` names each file that one stage writes and
another reads: its producer, its shape and its row type. Producers write with
``dump_stage`` and consumers read with ``load_stage``, so both code against
the same table while the other side is still a stub.

Encoding (``encode``/``decode``), the same for every file:

- a dataclass is a JSON object of every field by name (keys sorted on disk);
- a record (``records.RECORD_TYPES``) uses ``records.to_json``, with its "type";
- tuples are arrays, dates are ISO strings, None is null; map keys are strings;
- decoding is strict: an unknown or missing field, a wrong type or a value
  outside a ``Literal`` raises ``StageFileError`` naming the JSON path.

Shapes: ``doc`` one row; ``map`` {key: row}; ``map2`` {key: {key: row}};
``map3`` three levels; ``list`` a JSON array of rows; ``rows`` JSON Lines, one
row per line. Producers sort ``list`` and ``rows`` output so the bytes are
stable (maps are sorted by ``jsonio``).
"""

import collections.abc
import dataclasses
import importlib
import math
import types
import typing
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from tff_catalog import jsonio, records

if TYPE_CHECKING:
    from tff_catalog.paths import Paths

Shape = Literal["doc", "map", "map2", "map3", "list", "rows"]


class StageFileError(ValueError):
    """A stage file does not match its row type."""


@dataclass(frozen=True, slots=True)
class StageFile:
    path: str  # under build/stage/
    producer: str  # the stage that writes it (stages.STAGES)
    shape: Shape
    row: str  # "module:Class", or "float"
    schema: str | None = None  # under schemas/, when the file has a JSON Schema too


# Frozen contract: a new file, or a change of shape or row type, goes through the lead.
STAGE_FILES: dict[str, StageFile] = {
    "stale": StageFile("stale.json", "parse", "map", "tff_catalog.parse:StaleSource"),
    "snapshots": StageFile("snapshots.json", "parse", "map", "tff_catalog.parse:SourceSnapshot"),
    "universe": StageFile("universe.json", "universe", "doc", "tff_catalog.universe:Universe"),
    "latin": StageFile("latin.json", "latin", "map", "tff_catalog.latin:LatinResult"),
    "facts": StageFile("facts.json", "facts", "map", "tff_catalog.facts:Facts"),
    "licenses": StageFile("licenses.json", "licenses", "map", "tff_catalog.licenses:Verdict"),
    "alias_index": StageFile(
        "alias_index.json", "aliases", "list", "tff_catalog.mapping:IndexEntry"
    ),
    "mapped": StageFile("mapped.jsonl", "map", "rows", "tff_catalog.mapping:Mapped"),
    "terms": StageFile(
        "terms.json",
        "correct",
        "map3",
        "tff_catalog.corrections:Term",
        schema="stage/terms.schema.json",
    ),
    "ruler_counts": StageFile(
        "ruler_counts.json", "correct", "map", "float", schema="stage/ruler_counts.schema.json"
    ),
    "tags": StageFile("tags.json", "correct", "map", "tff_catalog.corrections:Tags"),
    "ruler": StageFile("ruler.json", "rank", "map", "float"),
    "scores": StageFile("scores.json", "rank", "map", "tff_catalog.surveys:SurveyScores"),
    "ranks": StageFile("ranks.json", "rank", "map2", "tff_catalog.engine.order:Placement"),
    "membership": StageFile(
        "membership.json", "membership", "doc", "tff_catalog.membership:Membership"
    ),
    "l3": StageFile("l3.json", "verify", "map", "tff_catalog.license_l3:L3Result"),
    "confidence": StageFile(
        "confidence.json", "confidence", "map2", "tff_catalog.confidence:Confidence"
    ),
    "links": StageFile("links.json", "links", "map", "tff_catalog.links:Links"),
    "previews": StageFile(
        "previews.json", "specimens", "map", "tff_catalog.specimens.stage:Preview"
    ),
}

_RECORD_CLASSES = frozenset(records.RECORD_TYPES.values())


# --- the stage files --------------------------------------------------------------------------


def row_type(name: str) -> Any:
    """The row class of ``STAGE_FILES[name]`` (``float`` for plain numbers)."""
    row = STAGE_FILES[name].row
    if row == "float":
        return float
    module, _, cls = row.partition(":")
    return getattr(importlib.import_module(module), cls)


def file_type(name: str) -> Any:
    """The type of the whole file (``rows``: of one line)."""
    spec, row = STAGE_FILES[name], row_type(name)
    match spec.shape:
        case "doc" | "rows":
            return row
        case "map":
            return dict[str, row]
        case "map2":
            return dict[str, dict[str, row]]
        case "map3":
            return dict[str, dict[str, dict[str, row]]]
        case "list":
            return tuple[row, ...]
    raise AssertionError(spec.shape)


def stage_path(paths: Paths, name: str) -> Path:
    return paths.stage / STAGE_FILES[name].path


def dump_stage(paths: Paths, name: str, obj: object) -> None:
    """Write stage file ``name`` (for ``rows``, ``obj`` is an iterable of rows)."""
    path = stage_path(paths, name)
    if STAGE_FILES[name].shape == "rows":
        dump_rows(typing.cast("Iterable[object]", obj), path)
    else:
        dump(obj, path)


def load_stage(paths: Paths, name: str) -> Any:
    """Read stage file ``name`` strictly (for ``rows``, a list of rows)."""
    path = stage_path(paths, name)
    if STAGE_FILES[name].shape == "rows":
        return load_rows(path, file_type(name))
    return load(path, file_type(name))


# --- any file -----------------------------------------------------------------------------------


def dump(obj: object, path: Path) -> None:
    """Write ``obj`` as pretty canonical JSON (``jsonio.dump``)."""
    jsonio.dump(encode(obj), path)


def load(path: Path, tp: Any) -> Any:
    """Read ``path`` and decode it as ``tp``."""
    return decode(tp, jsonio.load(path), "$")


def dump_rows(rows: Iterable[object], path: Path) -> int:
    """Write one canonical JSON object per row; return the row count."""
    return jsonio.dump_jsonl((encode(row) for row in rows), path)


def load_rows(path: Path, tp: Any) -> list[Any]:
    """Read JSON Lines, decoding each line as ``tp``."""
    return [decode(tp, row, f"line {n}") for n, row in enumerate(jsonio.iter_jsonl(path), 1)]


# --- encoding ------------------------------------------------------------------------------------


def encode(obj: object) -> Any:
    """JSON-ready data for ``obj`` (module docstring)."""
    if type(obj) in _RECORD_CLASSES:
        return records.to_json(typing.cast("records.Record", obj))
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: encode(getattr(obj, f.name)) for f in dataclasses.fields(obj)}
    if isinstance(obj, Mapping):
        out = {}
        for key, value in obj.items():
            if not isinstance(key, str):
                raise TypeError(f"map keys must be strings, got {key!r}")
            out[key] = encode(value)
        return out
    if isinstance(obj, tuple | list):
        return [encode(v) for v in obj]
    if isinstance(obj, float) and not math.isfinite(obj):
        raise ValueError(f"{obj!r} is not valid JSON")
    if obj is None or isinstance(obj, str | int | float | date):
        return obj
    raise TypeError(f"{type(obj).__name__} can't go in a stage file: {obj!r}")


def decode(tp: Any, value: Any, where: str = "$") -> Any:
    """Build a ``tp`` from JSON data, strictly (module docstring)."""
    if isinstance(tp, typing.TypeAliasType):
        tp = tp.__value__
    if tp is Any or tp is object:
        return value
    origin, args = typing.get_origin(tp), typing.get_args(tp)
    if origin is Literal:
        if type(value) not in {type(a) for a in args} or value not in args:
            raise StageFileError(f"{where}: {value!r} is not one of {list(args)}")
        return value
    if origin in (typing.Union, types.UnionType):
        return _union(args, value, where)
    if tp in _RECORD_CLASSES:
        return _record((tp,), value, where)
    if dataclasses.is_dataclass(tp):
        return _dataclass(tp, value, where)
    if origin is tuple:
        return _tuple(args, value, where)
    if origin in (dict, collections.abc.Mapping):
        _, value_type = args
        if not isinstance(value, dict):
            raise StageFileError(f"{where}: expected an object, got {type(value).__name__}")
        return {k: decode(value_type, v, f"{where}.{k}") for k, v in value.items()}
    return _scalar(tp, value, where)


def _union(args: tuple[Any, ...], value: Any, where: str) -> Any:
    if value is None:
        if type(None) in args:
            return None
        raise StageFileError(f"{where}: null is not allowed")
    options = [a for a in args if a is not type(None)]
    if options and all(a in _RECORD_CLASSES for a in options):
        return _record(tuple(options), value, where)
    errors = []
    for option in options:
        try:
            return decode(option, value, where)
        except StageFileError as exc:
            errors.append(str(exc))
    raise StageFileError(" / ".join(errors))


def _record(classes: tuple[Any, ...], value: Any, where: str) -> Any:
    try:
        rec = records.from_json(value)
    except (ValueError, TypeError) as exc:
        raise StageFileError(f"{where}: {exc}") from exc
    if type(rec) not in classes:
        raise StageFileError(f"{where}: a {type(rec).__name__} record is not allowed here")
    return rec


def _dataclass(cls: Any, value: Any, where: str) -> Any:
    if not isinstance(value, dict):
        raise StageFileError(f"{where}: expected an object, got {type(value).__name__}")
    hints = typing.get_type_hints(cls)
    names = [f.name for f in dataclasses.fields(cls) if f.init]
    unknown = sorted(set(value) - set(names))
    missing = [n for n in names if n not in value]
    if unknown or missing:
        raise StageFileError(f"{where}: {cls.__name__} unknown {unknown}, missing {missing}")
    return cls(**{n: decode(hints[n], value[n], f"{where}.{n}") for n in names})


def _tuple(args: tuple[Any, ...], value: Any, where: str) -> tuple[Any, ...]:
    if not isinstance(value, list):
        raise StageFileError(f"{where}: expected an array, got {type(value).__name__}")
    if len(args) == 2 and args[1] is Ellipsis:
        return tuple(decode(args[0], v, f"{where}[{i}]") for i, v in enumerate(value))
    if len(value) != len(args):
        raise StageFileError(f"{where}: expected {len(args)} items, got {len(value)}")
    return tuple(
        decode(a, v, f"{where}[{i}]") for i, (a, v) in enumerate(zip(args, value, strict=True))
    )


def _scalar(tp: Any, value: Any, where: str) -> Any:
    kind = type(value)
    if tp is bool and kind is bool:
        return value
    if tp is int and kind is int:
        return value
    if tp is float and kind in (int, float):
        return float(value)
    if tp is str and kind is str:
        return value
    if tp is date and kind is str:
        try:
            return date.fromisoformat(value)
        except ValueError as exc:
            raise StageFileError(f"{where}: {exc}") from exc
    if tp is datetime and kind is str:
        try:
            moment = datetime.fromisoformat(value)
        except ValueError as exc:
            raise StageFileError(f"{where}: {exc}") from exc
        if moment.tzinfo is None:
            raise StageFileError(f"{where}: {value!r} has no time zone")
        return moment.astimezone(UTC)
    if tp in (bool, int, float, str, date, datetime):
        raise StageFileError(f"{where}: expected {tp.__name__}, got {value!r}")
    raise TypeError(f"{where}: unsupported type {tp!r} in a stage file")
