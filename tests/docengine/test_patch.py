import pytest
from pydantic import ValidationError

from docengine.model import Paragraph, Run, Table
from docengine.patch import PatchError, VersionConflict, apply_patch, parse_ops


def test_ops_parse_from_llm_json_and_apply(letter_en):
    para_id = letter_en.blocks[1].id
    list_id = letter_en.blocks[2].id
    ops = parse_ops([
        {"op": "set_title", "text": "Leave Request"},
        {"op": "update_text", "id": para_id, "text": "I request leave from 26 to 28 October 2026."},
        {"op": "list_insert_item", "id": list_id, "index": 2, "text": "Laptop stays in office"},
        {"op": "set_meta", "key": "subject", "value": "Leave from 26 to 28 October"},
    ])
    res = apply_patch(letter_en, ops, base_version=1)
    doc = res.document
    assert doc.version == 2 and letter_en.version == 1  # original untouched
    assert doc.title == "Leave Request"
    assert doc.block(para_id).text.startswith("I request leave from 26")
    assert doc.block(list_id).items[-1] == "Laptop stays in office"
    assert len(res.changes) == 4 and "title" in res.changes[0]
    assert res.summary.count(";") == 3  # 3 changes + "and 1 more change"


def test_block_ids_are_stable_across_replace(letter_en):
    target = letter_en.blocks[0]
    ops = parse_ops([{"op": "replace_block", "id": target.id,
                      "block": {"type": "paragraph", "runs": [{"text": "Respected Sir,"}]}}])
    doc = apply_patch(letter_en, ops).document
    assert doc.blocks[0].id == target.id
    assert isinstance(doc.blocks[0], Paragraph) and doc.blocks[0].text == "Respected Sir,"


def test_version_conflict(letter_en):
    with pytest.raises(VersionConflict) as e:
        apply_patch(letter_en, parse_ops([{"op": "set_title", "text": "x"}]), base_version=7)
    assert e.value.code == "version_conflict"


def test_unknown_block_and_atomicity(letter_en):
    ops = parse_ops([{"op": "set_title", "text": "Changed"}, {"op": "delete_block", "id": "b_nope00"}])
    with pytest.raises(PatchError) as e:
        apply_patch(letter_en, ops)
    assert e.value.code == "unknown_block"
    assert letter_en.title == "Leave Application"  # first op not applied to the original


def test_table_ops(expenses):
    tbl = next(b for b in expenses.blocks if isinstance(b, Table))
    res = apply_patch(expenses, parse_ops([
        {"op": "table_set_cell", "id": tbl.id, "row": 1, "col": "amount", "value": 2500},
        {"op": "table_add_row", "id": tbl.id, "values": ["Cab", 800, "25-10-2026"]},
        {"op": "table_delete_row", "id": tbl.id, "row": 0},
    ]))
    new_tbl = res.document.block(tbl.id)
    assert [r[0] for r in new_tbl.rows] == ["Train", "Gift", "Cab"]
    assert new_tbl.rows[0][1] == 2500
    with pytest.raises(PatchError) as e:
        apply_patch(expenses, parse_ops([{"op": "table_add_row", "id": tbl.id, "values": ["only one"]}]))
    assert e.value.code == "bad_row"
    with pytest.raises(PatchError) as e:
        apply_patch(expenses, parse_ops([{"op": "table_set_cell", "id": tbl.id, "row": 9, "col": 0, "value": 1}]))
    assert e.value.code == "out_of_range"


def test_insert_move_delete_and_duplicates(letter_en):
    first, sig = letter_en.blocks[0], letter_en.blocks[-1]
    new_para = Paragraph(runs=[Run(text="Thank you.")])
    res = apply_patch(letter_en, parse_ops([
        {"op": "insert_block", "after_id": letter_en.blocks[2].id, "block": new_para.model_dump()},
        {"op": "move_block", "id": sig.id, "after_id": None},
        {"op": "delete_block", "id": first.id},
    ]))
    ids = [b.id for b in res.document.blocks]
    assert ids[0] == sig.id and first.id not in ids and new_para.id in ids
    with pytest.raises(PatchError) as e:
        apply_patch(letter_en, parse_ops([{"op": "insert_block", "block": first.model_dump()}]))
    assert e.value.code == "duplicate_id"


def test_type_mismatch(letter_en):
    with pytest.raises(PatchError) as e:
        apply_patch(letter_en, parse_ops([{"op": "list_set_item", "id": letter_en.blocks[0].id, "index": 0,
                                          "text": "x"}]))
    assert e.value.code == "type_mismatch"


def test_invalid_op_shape_is_rejected():
    with pytest.raises(ValidationError):
        parse_ops([{"op": "set_title"}])  # missing text
    with pytest.raises(ValidationError):
        parse_ops([{"op": "drop_database"}])


def test_move_after_itself_is_noop(letter_en):
    b = letter_en.blocks[1]
    res = apply_patch(letter_en, parse_ops([{"op": "move_block", "id": b.id, "after_id": b.id}]))
    assert [x.id for x in res.document.blocks] == [x.id for x in letter_en.blocks]


def test_details_lines_are_set_added_and_removed_by_key():
    from docengine.model import Document, KeyValue, Paragraph, Run
    from docengine.patch import apply_patch, parse_ops
    doc = Document(id="d", title="Letter", blocks=[
        Paragraph(id="p1", runs=[Run(text="Please close my account.")]),
        KeyValue(id="k1", pairs=[["Account Number", "[Your Account Number]"], ["Branch", "[Branch Name]"]])])
    r = apply_patch(doc, parse_ops([{"op": "kv_set", "id": "k1", "key": "account number", "value": "3124567890"},
                                    {"op": "kv_delete", "id": "k1", "key": "Branch"},
                                    {"op": "kv_set", "id": "k1", "key": "IFSC", "value": "SBIN0001234"}]))
    assert r.document.block("k1").pairs == [["Account Number", "3124567890"], ["IFSC", "SBIN0001234"]]
    assert r.changes == ["set Account Number to “3124567890”", "removed Branch", "added IFSC: “SBIN0001234”"]
    # the last line gone: the section goes; an unknown key is an error the model is shown (one retry)
    empty = apply_patch(doc, parse_ops([{"op": "kv_delete", "id": "k1", "key": "Account Number"},
                                        {"op": "kv_delete", "id": "k1", "key": "Branch"}]))
    assert [b.id for b in empty.document.blocks] == ["p1"]
    with pytest.raises(PatchError, match="unknown_key"):
        apply_patch(doc, parse_ops([{"op": "kv_delete", "id": "k1", "key": "PAN"}]))
