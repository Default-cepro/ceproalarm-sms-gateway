import pandas as pd

from src.core.validator import validate_devices


def _df(rows):
    return pd.DataFrame(rows, columns=["Telefono", "Marca", "Modelo"])


COMMANDS = {"wanwaytech": {"gs-900": {}}, "protrack": {"vt08f": {}}}


def test_valid_row_in_valid_list():
    df = _df([["04141234567", "wanwaytech", "gs-900"]])
    valid, invalid = validate_devices(df, COMMANDS)
    assert valid == [0]
    assert invalid == []


def test_empty_phone_invalid():
    df = _df([["", "wanwaytech", "gs-900"]])
    valid, invalid = validate_devices(df, COMMANDS)
    assert valid == []
    assert invalid == [(0, "Telefono vacio")]


def test_empty_brand_or_model_invalid():
    df = _df([["04141234567", "", "gs-900"]])
    valid, invalid = validate_devices(df, COMMANDS)
    assert valid == []
    assert invalid == [(0, "Marca/Modelo vacio o no interpretable")]


def test_unsupported_brand_invalid():
    df = _df([["04141234567", "acme", "gs-900"]])
    valid, invalid = validate_devices(df, COMMANDS)
    assert valid == []
    assert invalid == [(0, "Marca no soportada: acme")]


def test_unsupported_model_invalid():
    df = _df([["04141234567", "wanwaytech", "vt08f"]])
    valid, invalid = validate_devices(df, COMMANDS)
    assert valid == []
    assert invalid == [(0, "Modelo no soportado: wanwaytech vt08f")]


def test_mixed_rows_partition():
    df = _df(
        [
            ["04141234567", "wanwaytech", "gs-900"],
            ["", "protrack", "vt08f"],
            ["04149876543", "protrack", "vt08f"],
        ]
    )
    valid, invalid = validate_devices(df, COMMANDS)
    assert valid == [0, 2]
    assert invalid == [(1, "Telefono vacio")]
