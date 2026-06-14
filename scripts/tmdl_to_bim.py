"""TMDL → TOM JSON (.bim) converter for the TCP Trading Central Panel project.

The official TabularEditor 2.28 TMDL parser is too strict for the modern TMDL
files in `powerbi/model/`. This converter reads the TMDL semantically (lenient,
indentation-based) and emits a TOM JSON `.bim` that PowerBI Service accepts.

Scope (tied to what the project's TMDL actually uses, not all of TMDL):
- database.tmdl: name + compatibilityLevel
- model.tmdl: culture, defaultMode, discourageImplicitMeasures, expressions, dataSources
- tables/*.tmdl: columns (with dataType/sourceColumn/formatString/summarizeBy/flags),
  partitions (M expressions), measures (DAX, formatString, displayFolder)
- relationships.tmdl: relationship blocks (fromTable/fromColumn → toTable/toColumn)
- roles.tmdl: roles with tablePermissions (DAX filter expressions)
- cultures/*.tmdl: linguistic metadata + translations

Output: powerbi/build/dataset.bim (UTF-8, no BOM, TOM JSON).

Reference: https://learn.microsoft.com/en-us/analysis-services/tom/json-representation-of-tabular-objects
"""
from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ----------------------------------------------------------------------------
# Lenient TMDL parser
# ----------------------------------------------------------------------------

INDENT = 4  # TMDL uses 4-space indentation

# Block-declaration heads (indented at parent indent + 0)
BLOCK_KINDS = {
    "database", "model", "table", "column", "measure", "partition",
    "relationship", "role", "dataSource", "expression", "culture",
    "translations", "annotations", "annotation", "connectionDetails",
    "address", "credential", "tablePermissions", "tablePermission",
    "linguisticMetadata", "hierarchy", "level", "changedProperty",
    "extendedProperty",
}
BLOCK_RE = re.compile(
    r"^(?P<indent>\s*)"
    r"(?P<kind>database|model|table|column|measure|partition|relationship|role|dataSource|expression|culture|translations|annotations|annotation|connectionDetails|address|credential|tablePermissions|tablePermission|linguisticMetadata|hierarchy|level|changedProperty|extendedProperty)"
    r"(?:\s+(?P<name>[^=\n]+?))?"
    r"(?:\s*=\s*(?P<inline_value>.*))?\s*$"  # .* (not .+) so `measure NAME =` (multi-line DAX follows) matches
)

# Property `key =` (with `=`, not `:`) that may have multi-line value.
PROPERTY_EQUALS_RE = re.compile(r"^(?P<indent>\s*)(?P<key>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?P<value>.*?)\s*$")
PROPERTY_RE = re.compile(r"^(?P<indent>\s*)(?P<key>[A-Za-z_][A-Za-z0-9_]*)\s*:\s*(?P<value>.+?)\s*$")
FLAG_RE = re.compile(r"^(?P<indent>\s*)(?P<key>[A-Za-z_][A-Za-z0-9_]*)\s*$")
COMMENT_RE = re.compile(r"^\s*///\s?(?P<text>.*)$")


@dataclass
class Block:
    kind: str
    name: str = ""
    inline_value: str = ""
    indent: int = 0
    properties: dict[str, str] = field(default_factory=dict)
    flags: set[str] = field(default_factory=set)
    children: list["Block"] = field(default_factory=list)
    description: list[str] = field(default_factory=list)  # /// comments
    raw_source: list[str] = field(default_factory=list)  # for measures' DAX / partitions' M

    def find(self, kind: str, name: str | None = None) -> list["Block"]:
        out = []
        for c in self.children:
            if c.kind == kind and (name is None or c.name == name):
                out.append(c)
        return out


# Known property keywords per block kind. Lines matching `key:` at the
# correct indent that DO match these are treated as properties. Anything else
# at that indent (e.g. multi-line DAX expression) is treated as raw_source
# (DAX/M continuation).
KNOWN_PROPS: dict[str, set[str]] = {
    "measure": {
        "displayFolder", "formatString", "description", "displayName",
        "isHidden", "dataType", "lineageTag", "kpi", "detailRowsExpression",
        "formatStringDefinition", "fkLineageTag", "isSimpleMeasure",
    },
    "column": {
        "dataType", "sourceColumn", "formatString", "summarizeBy",
        "isHidden", "isKey", "isNullable", "dataCategory", "lineageTag",
        "displayFolder", "description", "annotation", "sortByColumn",
        "isAvailableInMdx", "isUnique", "isActiveInDataIsland",
        "encodingHint", "type", "isDataTypeInferred", "sortOrder",
        "displayName",
    },
    "partition": {
        "mode", "source", "queryGroup", "description", "annotation",
        "dataView", "expression",
    },
    "relationship": {
        "fromTable", "fromColumn", "toTable", "toColumn",
        "crossFilteringBehavior", "isActive", "joinOnDateBehavior",
        "fromCardinality", "toCardinality", "name", "description",
        "securityFilteringBehavior", "relyOnReferentialIntegrity",
    },
    "role": {"permission", "description", "modelPermission", "name"},
    "table": {"dataCategory", "description", "lineageTag", "annotation",
              "isHidden", "isPrivate", "showAsVariationsOnly",
              "excludeFromModelRefresh"},
    "model": {"culture", "defaultMode", "discourageImplicitMeasures",
              "annotation", "defaultPowerBIDataSourceVersion",
              "sourceQueryCulture", "description"},
    "database": {"compatibilityLevel", "culture", "defaultMode",
                 "discourageImplicitMeasures", "name"},
    "expression": {"kind", "lineageTag", "annotation", "description"},
    "dataSource": {"type", "annotation"},
    "connectionDetails": {"protocol"},
    "address": {"server", "database"},
    "credential": {"AuthenticationKind", "Tenant"},
    "culture": {"name"},
    "tablePermissions": set(),
    "linguisticMetadata": set(),
    "translations": set(),
    "annotation": set(),
    "annotations": set(),
}

# Properties that take a quoted string value (preserve quotes)
QUOTED_VALUE_PROPS = {"description", "displayName", "displayFolder", "formatString"}


def _is_property_line(stripped: str, current_kind: str) -> tuple[str, str] | None:
    """Return (key, value) if the line is a known property of the current block kind."""
    m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\s*[:=]\s*(.+?)\s*$", stripped)
    if not m:
        return None
    key = m.group(1)
    value = m.group(2)
    known = KNOWN_PROPS.get(current_kind, set())
    if key in known:
        return key, value
    return None


def parse_tmdl(text: str, source_name: str = "") -> Block:
    """Parse TMDL text into a Block tree. Returns a synthetic root Block."""
    lines = text.split("\n")
    root = Block(kind="<root>", name=source_name)
    stack: list[Block] = [root]
    pending_description: list[str] = []
    # State for multi-line DAX / M after `measure NAME =` or `source =`
    capturing_expr_for: Block | None = None
    capturing_expr_indent: int = 0

    def parent_for_indent(indent: int) -> Block:
        # Pop stack while next-to-top indent >= current indent
        while len(stack) > 1 and stack[-1].indent >= indent:
            stack.pop()
        return stack[-1]

    for raw_line in lines:
        line = raw_line.rstrip("\n").rstrip("\r")
        stripped = line.lstrip()
        indent_spaces = len(line) - len(stripped)
        indent_level = indent_spaces // INDENT

        # Skip blank lines
        if not stripped:
            continue

        # Skip non-doc comments (// style)
        if stripped.startswith("//") and not stripped.startswith("///"):
            continue

        # /// doc comments — accumulate for next block
        m_comment = COMMENT_RE.match(line)
        if m_comment:
            pending_description.append(m_comment.group("text"))
            continue

        # If we're capturing an expression body (DAX or M), decide if this line continues it
        if capturing_expr_for is not None:
            # Check if line indent > expression's owner indent (continuation) OR
            # if the line is a known property of the owner's kind (end of expression)
            owner = capturing_expr_for
            owner_indent = owner.indent
            # First check for property line at owner_indent + 1
            if indent_level == owner_indent + 1:
                prop = _is_property_line(stripped, owner.kind)
                if prop is not None:
                    # End expression capture; set as inline_value for the owner
                    owner.properties["__expression__"] = "\n".join(
                        owner.raw_source).strip()
                    owner.raw_source = []
                    capturing_expr_for = None
                    # Fall through to process this line normally below
                else:
                    # Treat as part of DAX/M
                    owner.raw_source.append(line)
                    continue
            elif indent_level > owner_indent:
                # Deeper — part of expression
                owner.raw_source.append(line)
                continue
            else:
                # Outdent — end expression
                owner.properties["__expression__"] = "\n".join(
                    owner.raw_source).strip()
                owner.raw_source = []
                capturing_expr_for = None
                # Fall through

        # Try block declaration
        m_block = BLOCK_RE.match(line)
        if m_block and m_block.group("kind") in BLOCK_KINDS:
            parent = parent_for_indent(indent_level)
            kind = m_block.group("kind")
            name = (m_block.group("name") or "").strip()
            inline_value = (m_block.group("inline_value") or "").strip()
            blk = Block(kind=kind, name=name, inline_value=inline_value, indent=indent_level)
            blk.description = pending_description
            pending_description = []
            parent.children.append(blk)
            stack.append(blk)

            # Start expression capture for blocks with empty inline_value
            # (multi-line DAX/M follows). E.g. `measure NAME =` or
            # `tablePermission table_name =`.
            if kind in {"measure", "tablePermission"} and not inline_value:
                capturing_expr_for = blk
                capturing_expr_indent = indent_level
            elif kind == "expression" and inline_value:
                # M-parameter expressions are single-line inline
                blk.properties["__expression__"] = inline_value
            continue

        # Try property
        # Find the current block from stack at indent_level - 1 (parent of this property)
        parent = parent_for_indent(indent_level)
        m_prop = PROPERTY_RE.match(line)
        if m_prop:
            key = m_prop.group("key")
            value = m_prop.group("value").strip()
            parent.properties[key] = value
            continue

        # Try property with `=` (may be multi-line)
        m_prop_eq = PROPERTY_EQUALS_RE.match(line)
        if m_prop_eq:
            key = m_prop_eq.group("key")
            value = m_prop_eq.group("value").strip()
            if value:
                parent.properties[key] = value
            else:
                # Multi-line property value follows. Create a synthetic child block
                # carrying the property key so the existing capture mechanism works.
                pseudo = Block(kind="__property_expr__", name=key, indent=indent_level)
                parent.children.append(pseudo)
                stack.append(pseudo)
                capturing_expr_for = pseudo
                capturing_expr_indent = indent_level
            continue

        # Bare flag (no `:` no `=`)
        m_flag = FLAG_RE.match(line)
        if m_flag:
            parent.flags.add(m_flag.group("key"))
            continue

        # Unrecognised — record raw
        parent.raw_source.append(line)

    # Finalise any pending expression
    if capturing_expr_for is not None:
        capturing_expr_for.properties["__expression__"] = "\n".join(
            capturing_expr_for.raw_source).strip()
        capturing_expr_for.raw_source = []

    return root


def parse_tmdl_folder(folder: Path) -> dict[str, Block]:
    """Parse all .tmdl files in a folder + subdirs. Returns dict[relative_path → Block tree]."""
    out: dict[str, Block] = {}
    for f in sorted(folder.rglob("*.tmdl")):
        rel = str(f.relative_to(folder)).replace("\\", "/")
        text = f.read_text(encoding="utf-8")
        out[rel] = parse_tmdl(text, source_name=rel)
    return out


# ----------------------------------------------------------------------------
# TOM JSON emitter
# ----------------------------------------------------------------------------

def _unquote(s: str) -> str:
    """Strip leading/trailing single or double quotes (TMDL identifier quoting)."""
    s = s.strip()
    if len(s) >= 2 and s[0] in "\"'" and s[-1] == s[0]:
        return s[1:-1]
    return s


def _typed_value(value: str) -> Any:
    """Convert TMDL string value to Python typed value for JSON."""
    v = value.strip()
    if v in ("true", "True"):
        return True
    if v in ("false", "False"):
        return False
    if v.isdigit():
        return int(v)
    try:
        return int(v)
    except ValueError:
        pass
    try:
        return float(v)
    except ValueError:
        pass
    # Quoted string — strip quotes
    if len(v) >= 2 and v[0] in "\"'" and v[-1] == v[0]:
        return v[1:-1]
    return v


def _summarize_by(value: str) -> str:
    """Normalise summarizeBy value to TOM JSON form."""
    return value.strip().lower()


def _emit_column(col: Block) -> dict:
    props = col.properties
    out: dict[str, Any] = {
        "name": _unquote(col.name),
        "dataType": _typed_value(props.get("dataType", "string")),
        "sourceColumn": _typed_value(props.get("sourceColumn", _unquote(col.name))),
    }
    if "formatString" in props:
        out["formatString"] = _typed_value(props["formatString"])
    if "summarizeBy" in props:
        out["summarizeBy"] = _summarize_by(props["summarizeBy"])
    if "dataCategory" in props:
        out["dataCategory"] = _typed_value(props["dataCategory"])
    if "displayFolder" in props:
        out["displayFolder"] = _typed_value(props["displayFolder"])
    if "description" in props:
        out["description"] = _typed_value(props["description"])
    if "isHidden" in col.flags or props.get("isHidden") == "true":
        out["isHidden"] = True
    if "isKey" in col.flags or props.get("isKey") == "true":
        out["isKey"] = True
    if "isNullable" in col.flags or props.get("isNullable") == "true":
        out["isNullable"] = True
    if "isUnique" in col.flags:
        out["isUnique"] = True
    return out


def _emit_measure(m: Block) -> dict:
    name = _unquote(m.name)
    out: dict[str, Any] = {
        "name": name,
        "expression": m.properties.get("__expression__", "0"),
    }
    if "formatString" in m.properties:
        out["formatString"] = _typed_value(m.properties["formatString"])
    if "displayFolder" in m.properties:
        out["displayFolder"] = _typed_value(m.properties["displayFolder"])
    if "description" in m.properties:
        out["description"] = _typed_value(m.properties["description"])
    if "isHidden" in m.flags:
        out["isHidden"] = True
    return out


def _emit_partition(p: Block, table_name: str) -> dict:
    name = _unquote(p.name) or f"{table_name}_partition"
    mode = _typed_value(p.properties.get("mode", "import"))
    # Determine partition source. Two sources of the M:
    #  - Block's `source = ...` captured as a `__property_expr__` child
    #  - Block's properties['source'] (single-line M)
    source_expr = None
    for child in p.children:
        if child.kind == "__property_expr__" and child.name == "source":
            source_expr = child.properties.get("__expression__", "").strip()
            break
    if not source_expr:
        source_expr = p.properties.get("source", "").strip()
    # If inline_value of partition declaration was "calculated", it's a calc table
    inline = p.inline_value.strip().lower()
    if inline == "calculated":
        return {
            "name": name,
            "mode": mode,
            "source": {"type": "calculated", "expression": source_expr or "{0}"},
        }
    return {
        "name": name,
        "mode": mode,
        "source": {"type": "m", "expression": source_expr or ""},
    }


def _emit_table(t: Block) -> dict:
    name = _unquote(t.name)
    columns = [_emit_column(c) for c in t.find("column")]
    measures = [_emit_measure(m) for m in t.find("measure")]
    partitions = [_emit_partition(p, name) for p in t.find("partition")]
    out: dict[str, Any] = {"name": name, "columns": columns}
    if measures:
        out["measures"] = measures
    if partitions:
        out["partitions"] = partitions
    if "dataCategory" in t.properties:
        out["dataCategory"] = _typed_value(t.properties["dataCategory"])
    if "description" in t.properties:
        out["description"] = _typed_value(t.properties["description"])
    if "isHidden" in t.flags:
        out["isHidden"] = True
    return out


def _emit_relationship(r: Block) -> dict:
    p = r.properties
    out: dict[str, Any] = {
        "name": _unquote(r.name),
        "fromTable": _typed_value(p["fromTable"]),
        "fromColumn": _typed_value(p["fromColumn"]),
        "toTable": _typed_value(p["toTable"]),
        "toColumn": _typed_value(p["toColumn"]),
    }
    if p.get("crossFilteringBehavior"):
        out["crossFilteringBehavior"] = _typed_value(p["crossFilteringBehavior"])
    if p.get("isActive") in ("false", "False"):
        out["isActive"] = False
    return out


def _emit_role(r: Block) -> dict:
    name = _unquote(r.name)
    out: dict[str, Any] = {
        "name": name,
        "modelPermission": _typed_value(r.properties.get("modelPermission", "read")),
    }
    table_perms = []
    for tp in r.find("tablePermission"):
        filter_expr = tp.properties.get("__expression__", "")
        table_perms.append({
            "name": _unquote(tp.name),
            "filterExpression": filter_expr,
        })
    if table_perms:
        out["tablePermissions"] = table_perms
    return out


def _emit_expression(e: Block) -> dict:
    return {
        "name": _unquote(e.name),
        "kind": "m",
        "expression": e.properties.get("__expression__", e.inline_value or ""),
    }


def _emit_datasource(ds: Block) -> dict:
    name = _unquote(ds.name)
    out: dict[str, Any] = {
        "type": _typed_value(ds.properties.get("type", "structured")),
        "name": name,
    }
    # connectionDetails block
    cd_blocks = ds.find("connectionDetails")
    if cd_blocks:
        cd = cd_blocks[0]
        cd_out: dict[str, Any] = {}
        if "protocol" in cd.properties:
            cd_out["protocol"] = _typed_value(cd.properties["protocol"])
        # address block
        addr_blocks = cd.find("address")
        if addr_blocks:
            addr = addr_blocks[0]
            cd_out["address"] = {k: _typed_value(v) for k, v in addr.properties.items()}
        if cd_out:
            out["connectionDetails"] = cd_out
    return out


def emit_bim(parsed: dict[str, Block]) -> dict:
    """Convert parsed TMDL tree dict → TOM JSON Database dict."""
    # Top-level: database
    db_tree = parsed.get("database.tmdl") or parsed.get("database")
    if db_tree is None:
        raise ValueError("database.tmdl not found in parsed folder")
    db_block = db_tree.children[0]  # `database <name>`
    db_name = _unquote(db_block.name)
    compat_level = int(db_block.properties.get("compatibilityLevel", "1604"))

    # Model: from model.tmdl
    model_tree = parsed.get("model.tmdl")
    model_block = model_tree.children[0] if model_tree else None

    model_out: dict[str, Any] = {}
    if model_block:
        if "culture" in model_block.properties:
            model_out["culture"] = _typed_value(model_block.properties["culture"])
        if "defaultMode" in model_block.properties:
            model_out["defaultMode"] = _typed_value(model_block.properties["defaultMode"])
        if "discourageImplicitMeasures" in model_block.flags or model_block.properties.get("discourageImplicitMeasures") == "true":
            model_out["discourageImplicitMeasures"] = True

    # Some properties may be at database level instead (per the project's original TMDL)
    if "culture" in db_block.properties and "culture" not in model_out:
        model_out["culture"] = _typed_value(db_block.properties["culture"])
    if "defaultMode" in db_block.properties and "defaultMode" not in model_out:
        model_out["defaultMode"] = _typed_value(db_block.properties["defaultMode"])
    if "discourageImplicitMeasures" in db_block.flags and "discourageImplicitMeasures" not in model_out:
        model_out["discourageImplicitMeasures"] = True

    # Expressions (M parameters) — from model.tmdl
    expressions = []
    if model_block:
        for e in model_block.find("expression"):
            expressions.append(_emit_expression(e))
    if expressions:
        model_out["expressions"] = expressions

    # DataSources — from database.tmdl (legacy placement)
    data_sources = []
    for ds in db_block.find("dataSource"):
        data_sources.append(_emit_datasource(ds))
    # Also check model.tmdl
    if model_block:
        for ds in model_block.find("dataSource"):
            data_sources.append(_emit_datasource(ds))
    if data_sources:
        model_out["dataSources"] = data_sources

    # Tables — from tables/*.tmdl
    tables = []
    for path, tree in parsed.items():
        if path.startswith("tables/"):
            for t in tree.children:
                if t.kind == "table":
                    tables.append(_emit_table(t))
    if tables:
        model_out["tables"] = tables

    # Relationships — from relationships.tmdl
    rel_tree = parsed.get("relationships.tmdl")
    relationships = []
    if rel_tree:
        for r in rel_tree.children:
            if r.kind == "relationship":
                relationships.append(_emit_relationship(r))
    if relationships:
        model_out["relationships"] = relationships

    # Roles — from roles.tmdl
    roles_tree = parsed.get("roles.tmdl")
    roles = []
    if roles_tree:
        for r in roles_tree.children:
            if r.kind == "role":
                roles.append(_emit_role(r))
    if roles:
        model_out["roles"] = roles

    bim = {
        "name": db_name,
        "compatibilityLevel": compat_level,
        "model": model_out,
    }
    return bim


# ----------------------------------------------------------------------------
# Diagnostic: print the parsed tree compactly
# ----------------------------------------------------------------------------

def dump_block(blk: Block, depth: int = 0) -> None:
    pad = "  " * depth
    name_disp = f" {blk.name}" if blk.name else ""
    val_disp = f" = {blk.inline_value[:40]!r}" if blk.inline_value else ""
    print(f"{pad}{blk.kind}{name_disp}{val_disp}  props={len(blk.properties)} flags={len(blk.flags)} children={len(blk.children)}")
    for c in blk.children[:5]:
        dump_block(c, depth + 1)
    if len(blk.children) > 5:
        print(f"{pad}  ... +{len(blk.children) - 5} more children")


if __name__ == "__main__":
    folder = Path(sys.argv[1] if len(sys.argv) > 1 else "powerbi/model")
    out_path = Path(sys.argv[2] if len(sys.argv) > 2 else "powerbi/build/dataset.bim")

    if not folder.exists():
        print(f"ERROR: folder not found: {folder}", file=sys.stderr)
        sys.exit(1)

    print(f"=== Parsing {folder} ===")
    parsed = parse_tmdl_folder(folder)
    print(f"Loaded {len(parsed)} TMDL files")

    print(f"\n=== Emitting TOM JSON .bim ===")
    bim = emit_bim(parsed)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(bim, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote: {out_path} ({out_path.stat().st_size} bytes)")

    # Summary
    model = bim.get("model", {})
    print(f"\nTOM JSON summary:")
    print(f"  compatibilityLevel: {bim.get('compatibilityLevel')}")
    print(f"  culture:            {model.get('culture')}")
    print(f"  defaultMode:        {model.get('defaultMode')}")
    print(f"  tables:             {len(model.get('tables', []))}")
    print(f"  total measures:     {sum(len(t.get('measures', [])) for t in model.get('tables', []))}")
    print(f"  total columns:      {sum(len(t.get('columns', [])) for t in model.get('tables', []))}")
    print(f"  relationships:      {len(model.get('relationships', []))}")
    print(f"  roles:              {len(model.get('roles', []))}")
    print(f"  expressions:        {len(model.get('expressions', []))}")
    print(f"  dataSources:        {len(model.get('dataSources', []))}")
