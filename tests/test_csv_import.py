import pytest

from csv_import import parse_roster_csv, CsvImportError


def test_parses_basic_csv() -> None:
    data = b"name,class\nJohn Tan,3E1\nMary Lim,3E1\n"
    rows = parse_roster_csv(data)
    assert rows == [("John Tan", "3E1"), ("Mary Lim", "3E1")]


def test_handles_utf8_bom() -> None:
    data = "﻿name,class\nJohn Tan,3E1\n".encode()
    rows = parse_roster_csv(data)
    assert rows == [("John Tan", "3E1")]


def test_handles_crlf() -> None:
    data = b"name,class\r\nJohn Tan,3E1\r\n"
    rows = parse_roster_csv(data)
    assert rows == [("John Tan", "3E1")]


def test_skips_blank_lines() -> None:
    data = b"name,class\n\nJohn Tan,3E1\n\n"
    rows = parse_roster_csv(data)
    assert rows == [("John Tan", "3E1")]


def test_trims_whitespace() -> None:
    data = b"name,class\n  John Tan  ,  3E1 \n"
    rows = parse_roster_csv(data)
    assert rows == [("John Tan", "3E1")]


def test_rejects_missing_headers() -> None:
    with pytest.raises(CsvImportError, match="header"):
        parse_roster_csv(b"foo,bar\nx,y\n")


def test_rejects_empty_file() -> None:
    with pytest.raises(CsvImportError):
        parse_roster_csv(b"")


def test_rejects_row_with_blank_name() -> None:
    with pytest.raises(CsvImportError, match="blank name"):
        parse_roster_csv(b"name,class\n,3E1\n")
