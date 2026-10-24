"""Patch operations on the canonical document.

Edits are small, typed operations (validated) instead of regenerating the whole document:
untouched content can't drift, block IDs stay stable, and "what changed" is derived from the ops
(never from an LLM's free-text claim). Commits are compare-and-swap on `version`.
"""
from __future__ import annotations

from typing import List, Literal, Optional, Union

from pydantic import BaseModel, Field, TypeAdapter
from typing_extensions import Annotated

from .model import Block, Document, Heading, KeyValue, ListBlock, Paragraph, Run, Slide, Table


class PatchError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code


class VersionConflict(PatchError):
    def __init__(self, expected: int, actual: int):
        super().__init__("version_conflict", f"patch based on v{expected}, document is at v{actual}")
        self.expected, self.actual = expected, actual


Cell = Optional[Union[float, int, str]]


class SetTitle(BaseModel):
    op: Literal["set_title"] = "set_title"
    text: str


class SetMeta(BaseModel):
    op: Literal["set_meta"] = "set_meta"
    key: str
    value: Optional[str] = None  # None deletes the key


class InsertBlock(BaseModel):
    op: Literal["insert_block"] = "insert_block"
    block: Block
    after_id: Optional[str] = None
    at: Literal["start", "end"] = "end"  # used only when after_id is None


class ReplaceBlock(BaseModel):
    op: Literal["replace_block"] = "replace_block"
    id: str
    block: Block


class DeleteBlock(BaseModel):
    op: Literal["delete_block"] = "delete_block"
    id: str


class MoveBlock(BaseModel):
    op: Literal["move_block"] = "move_block"
    id: str
    after_id: Optional[str] = None  # None = move to start


class UpdateText(BaseModel):
    op: Literal["update_text"] = "update_text"
    id: str
    text: str


class ListSetItem(BaseModel):
    op: Literal["list_set_item"] = "list_set_item"
    id: str
    index: int
    text: str


class ListInsertItem(BaseModel):
    op: Literal["list_insert_item"] = "list_insert_item"
    id: str
    index: int  # may equal len(items) to append
    text: str


class ListDeleteItem(BaseModel):
    op: Literal["list_delete_item"] = "list_delete_item"
    id: str
    index: int


class TableSetCell(BaseModel):
    op: Literal["table_set_cell"] = "table_set_cell"
    id: str
    row: int
    col: Union[int, str]  # index or column name
    value: Cell


class TableAddRow(BaseModel):
    op: Literal["table_add_row"] = "table_add_row"
    id: str
    values: List[Cell]
    index: Optional[int] = None  # None = append


class TableDeleteRow(BaseModel):
    op: Literal["table_delete_row"] = "table_delete_row"
    id: str
    row: int


class KvSet(BaseModel):
    """A details section's line ("Account number: ..."), found by its key; a new key adds a line."""
    op: Literal["kv_set"] = "kv_set"
    id: str
    key: str
    value: str


class KvDelete(BaseModel):
    op: Literal["kv_delete"] = "kv_delete"
    id: str
    key: str


class AddAssumption(BaseModel):
    op: Literal["add_assumption"] = "add_assumption"
    text: str


PatchOp = Annotated[
    Union[SetTitle, SetMeta, InsertBlock, ReplaceBlock, DeleteBlock, MoveBlock, UpdateText,
          ListSetItem, ListInsertItem, ListDeleteItem, TableSetCell, TableAddRow, TableDeleteRow, KvSet, KvDelete,
          AddAssumption],
    Field(discriminator="op"),
]
_ops_adapter = TypeAdapter(List[PatchOp])


def parse_ops(raw: list) -> List[BaseModel]:
    """Validate LLM/tool output into typed ops (raises pydantic.ValidationError on bad shape)."""
    return _ops_adapter.validate_python(raw)


class PatchResult(BaseModel):
    document: Document
    changes: List[str]

    @property
    def summary(self) -> str:
        if not self.changes:
            return "No changes."
        head = "; ".join(self.changes[:3])
        more = len(self.changes) - 3
        return head + (f"; and {more} more change{'s' if more > 1 else ''}" if more > 0 else "")


def _describe(block) -> str:
    kind = {"heading": "heading", "paragraph": "paragraph", "list": "list", "table": "table",
            "kv": "details section", "signature": "signature", "page_break": "page break",
            "slide": "slide"}[block.type]
    label = ""
    if isinstance(block, (Heading, Slide)):
        label = block.text if isinstance(block, Heading) else block.title
    elif isinstance(block, Paragraph):
        label = block.text
    elif isinstance(block, Table):
        label = block.caption or ", ".join(c.name for c in block.columns[:3])
    elif isinstance(block, ListBlock) and block.items:
        label = block.items[0]
    label = label.strip().replace("\n", " ")
    return f"{kind} “{label[:40]}{'…' if len(label) > 40 else ''}”" if label else kind


def _require(doc: Document, block_id: str, kind=None):
    b = doc.block(block_id)
    if b is None:
        raise PatchError("unknown_block", f"no block with id {block_id}")
    if kind is not None and not isinstance(b, kind):
        raise PatchError("type_mismatch", f"block {block_id} is a {b.type}, expected {kind.__name__}")
    return b


def _col_index(table: Table, col: Union[int, str]) -> int:
    if isinstance(col, int):
        if not 0 <= col < len(table.columns):
            raise PatchError("out_of_range", f"column {col} out of range")
        return col
    for i, c in enumerate(table.columns):
        if c.name.strip().lower() == col.strip().lower():
            return i
    raise PatchError("unknown_column", f"no column named {col!r}")


def _check_row(table: Table, row: int) -> None:
    if not 0 <= row < len(table.rows):
        raise PatchError("out_of_range", f"row {row + 1} out of range (table has {len(table.rows)} rows)")


def apply_patch(doc: Document, ops: List[BaseModel], base_version: Optional[int] = None) -> PatchResult:
    """Apply ops atomically to a copy of `doc`; bump version; return new doc + human-readable changes."""
    if base_version is not None and base_version != doc.version:
        raise VersionConflict(base_version, doc.version)
    new = doc.model_copy(deep=True)
    changes: List[str] = []

    for op in ops:
        if isinstance(op, SetTitle):
            new.title = op.text
            changes.append(f"changed the title to “{op.text}”")
        elif isinstance(op, SetMeta):
            if op.value is None:
                new.meta.pop(op.key, None)
                changes.append(f"removed {op.key}")
            else:
                new.meta[op.key] = op.value
                changes.append(f"set {op.key} to “{op.value}”")
        elif isinstance(op, InsertBlock):
            if new.block(op.block.id) is not None:
                raise PatchError("duplicate_id", f"block id {op.block.id} already exists")
            if op.after_id is not None:
                anchor = _require(new, op.after_id)
                new.blocks.insert(new.index_of(op.after_id) + 1, op.block)
                changes.append(f"added a {_describe(op.block)} after the {_describe(anchor)}")
            elif op.at == "start":
                new.blocks.insert(0, op.block)
                changes.append(f"added a {_describe(op.block)} at the start")
            else:
                new.blocks.append(op.block)
                changes.append(f"added a {_describe(op.block)} at the end")
        elif isinstance(op, ReplaceBlock):
            old = _require(new, op.id)
            replacement = op.block.model_copy(update={"id": op.id})  # IDs are stable across edits
            new.blocks[new.index_of(op.id)] = replacement
            changes.append(f"rewrote the {_describe(old)}")
        elif isinstance(op, DeleteBlock):
            old = _require(new, op.id)
            new.blocks.pop(new.index_of(op.id))
            changes.append(f"removed the {_describe(old)}")
        elif isinstance(op, MoveBlock):
            blk = _require(new, op.id)
            if op.after_id == op.id:
                continue  # moving a block after itself is a no-op
            new.blocks.pop(new.index_of(op.id))
            if op.after_id is None:
                new.blocks.insert(0, blk)
                changes.append(f"moved the {_describe(blk)} to the start")
            else:
                anchor = _require(new, op.after_id)
                new.blocks.insert(new.index_of(op.after_id) + 1, blk)
                changes.append(f"moved the {_describe(blk)} after the {_describe(anchor)}")
        elif isinstance(op, UpdateText):
            blk = _require(new, op.id)
            if isinstance(blk, Heading):
                new.blocks[new.index_of(op.id)] = blk.model_copy(update={"text": op.text})
            elif isinstance(blk, Paragraph):
                new.blocks[new.index_of(op.id)] = blk.model_copy(update={"runs": [Run(text=op.text)]})
            elif isinstance(blk, Slide):
                new.blocks[new.index_of(op.id)] = blk.model_copy(update={"title": op.text})
            else:
                raise PatchError("type_mismatch", f"update_text not supported on {blk.type}")
            changes.append(f"updated the {_describe(blk)}")
        elif isinstance(op, (ListSetItem, ListInsertItem, ListDeleteItem)):
            blk = _require(new, op.id, ListBlock)
            items = list(blk.items)
            if isinstance(op, ListInsertItem):
                if not 0 <= op.index <= len(items):
                    raise PatchError("out_of_range", f"item {op.index + 1} out of range")
                items.insert(op.index, op.text)
                changes.append(f"added point {op.index + 1} “{op.text[:40]}”")
            else:
                if not 0 <= op.index < len(items):
                    raise PatchError("out_of_range", f"item {op.index + 1} out of range")
                if isinstance(op, ListSetItem):
                    items[op.index] = op.text
                    changes.append(f"changed point {op.index + 1} to “{op.text[:40]}”")
                else:
                    removed = items.pop(op.index)
                    changes.append(f"removed point {op.index + 1} “{removed[:40]}”")
            new.blocks[new.index_of(op.id)] = blk.model_copy(update={"items": items})
        elif isinstance(op, TableSetCell):
            blk = _require(new, op.id, Table)
            _check_row(blk, op.row)
            ci = _col_index(blk, op.col)
            rows = [list(r) for r in blk.rows]
            rows[op.row][ci] = op.value
            new.blocks[new.index_of(op.id)] = blk.model_copy(update={"rows": rows})
            changes.append(f"set row {op.row + 1}, {blk.columns[ci].name} to {op.value}")
        elif isinstance(op, TableAddRow):
            blk = _require(new, op.id, Table)
            if len(op.values) != len(blk.columns):
                raise PatchError("bad_row", f"row has {len(op.values)} cells, table has {len(blk.columns)} columns")
            rows = [list(r) for r in blk.rows]
            idx = len(rows) if op.index is None else op.index
            if not 0 <= idx <= len(rows):
                raise PatchError("out_of_range", f"row {idx + 1} out of range")
            rows.insert(idx, list(op.values))
            new.blocks[new.index_of(op.id)] = blk.model_copy(update={"rows": rows})
            changes.append(f"added a row to the {_describe(blk)}")
        elif isinstance(op, TableDeleteRow):
            blk = _require(new, op.id, Table)
            _check_row(blk, op.row)
            rows = [list(r) for r in blk.rows]
            rows.pop(op.row)
            new.blocks[new.index_of(op.id)] = blk.model_copy(update={"rows": rows})
            changes.append(f"removed row {op.row + 1} from the {_describe(blk)}")
        elif isinstance(op, (KvSet, KvDelete)):
            blk = _require(new, op.id, KeyValue)
            pairs = [list(p) for p in blk.pairs]
            at = next((i for i, p in enumerate(pairs) if p and p[0].strip().lower() == op.key.strip().lower()), None)
            if isinstance(op, KvSet):
                if at is None:
                    pairs.append([op.key, op.value])
                    changes.append(f"added {op.key}: “{op.value[:40]}”")
                else:
                    pairs[at] = [pairs[at][0], op.value]
                    changes.append(f"set {pairs[at][0]} to “{op.value[:40]}”")
            else:
                if at is None:
                    raise PatchError("unknown_key", f"no line {op.key!r} in the {_describe(blk)}")
                pairs.pop(at)
                changes.append(f"removed {op.key}")
            if pairs:
                new.blocks[new.index_of(op.id)] = blk.model_copy(update={"pairs": pairs})
            else:  # nothing left in the section
                new.blocks.pop(new.index_of(op.id))
        elif isinstance(op, AddAssumption):
            new.assumptions.append(op.text)
            changes.append(f"noted an assumption: {op.text}")
        else:  # pragma: no cover - parse_ops guarantees the union
            raise PatchError("unknown_op", repr(op))

    # Re-validate the whole document so invariants (row widths, levels, unique ids) still hold.
    new = Document.model_validate(new.model_dump())
    ids = [b.id for b in new.blocks]
    if len(ids) != len(set(ids)):
        raise PatchError("duplicate_id", "block ids must be unique")
    new.version = doc.version + 1
    return PatchResult(document=new, changes=changes)
