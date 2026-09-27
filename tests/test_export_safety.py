from utils.export_safety import safe_export_cell


def test_formula_like_values_are_written_as_text():
    for value in ('=SUM(A1:A2)', '+1', '-1', '@cmd', '  =1'):
        assert safe_export_cell(value).startswith("'")


def test_safe_export_cell_preserves_normal_values():
    assert safe_export_cell('normal text') == 'normal text'
    assert safe_export_cell(42) == 42
