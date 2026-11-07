from zipfile import ZipFile

from qa_parser.document import NS, read_document


def sample(tmp_path, shape_id):
    path = tmp_path / "object.docx"
    namespaces = " ".join(f'xmlns:{p}="{u}"' for p, u in NS.items())
    with ZipFile(path, "w") as z:
        z.writestr("word/document.xml", f'''<w:document {namespaces}><w:body><w:p><w:r><w:object>
        <v:shape id="decoy"><v:imagedata r:id="wrong"/></v:shape>
        <v:shape id="formula"><v:imagedata r:id="correct"/></v:shape>
        <o:OLEObject ProgID="Equation.DSMT4" ShapeID="{shape_id}" r:id="ole"/>
        </w:object></w:r></w:p></w:body></w:document>''')
        z.writestr("word/_rels/document.xml.rels", '''<Relationships>
        <Relationship Id="wrong" Target="media/decoy.png"/>
        <Relationship Id="correct" Target="media/formula.png"/>
        <Relationship Id="ole" Target="embeddings/formula.bin"/>
        </Relationships>''')
        z.writestr("word/media/decoy.png", b"\x89PNG\r\n\x1a\nWRONG")
        z.writestr("word/media/formula.png", b"\x89PNG\r\n\x1a\nCORRECT")
        z.writestr("word/embeddings/formula.bin", b"SOURCE OLE")
    return path


def test_shape_id_selects_own_preview_not_first_image(tmp_path):
    token = read_document(sample(tmp_path, "formula"))["blocks"][0]["tokens"][0]
    assert token["preview"].endswith(b"CORRECT")
    assert token["preview_binding"]["relationship_id"] == "correct"


def test_missing_shape_id_target_never_borrows_other_preview(tmp_path):
    token = read_document(sample(tmp_path, "absent"))["blocks"][0]["tokens"][0]
    assert token["preview"] is None
    assert not token["preview_binding"]["verified"]
