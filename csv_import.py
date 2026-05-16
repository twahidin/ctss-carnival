import csv
import io


class CsvImportError(ValueError):
    pass


def parse_roster_csv(data: bytes) -> list[tuple[str, str]]:
    text = data.decode("utf-8-sig", errors="replace")
    if not text.strip():
        raise CsvImportError("File is empty")
    reader = csv.reader(io.StringIO(text))
    try:
        headers = [h.strip().lower() for h in next(reader)]
    except StopIteration:
        raise CsvImportError("File is empty")
    if headers[:2] != ["name", "class"]:
        raise CsvImportError("First row must be header: name,class")
    rows: list[tuple[str, str]] = []
    for i, raw in enumerate(reader, start=2):
        if not raw or all(not c.strip() for c in raw):
            continue
        if len(raw) < 2:
            raise CsvImportError(f"Row {i}: not enough columns")
        name = raw[0].strip()
        klass = raw[1].strip()
        if not name:
            raise CsvImportError(f"Row {i}: blank name")
        if not klass:
            raise CsvImportError(f"Row {i}: blank class")
        rows.append((name, klass))
    if not rows:
        raise CsvImportError("No student rows found")
    return rows
