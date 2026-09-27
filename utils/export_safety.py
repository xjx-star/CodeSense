"""Helpers for exporting user-controlled text to spreadsheet formats."""


_FORMULA_PREFIXES = ('=', '+', '-', '@')


def safe_export_cell(value):
    """Prevent spreadsheet applications from evaluating text as a formula."""

    if isinstance(value, str) and value.lstrip(' \t\r\n').startswith(
        _FORMULA_PREFIXES
    ):
        return "'" + value
    return value
