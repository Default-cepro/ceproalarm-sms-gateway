import pytest

from src.core import phones


def test_normalize_phone_digits_only():
    assert phones.normalize_phone("+58 412-257-1528") == "584122571528"


def test_normalize_phone_trailing_decimal():
    assert phones.normalize_phone("4122571528.0") == "4122571528"


def test_normalize_phone_scientific_notation():
    assert phones.normalize_phone("4.122571528e9") == "4122571528"


def test_normalize_phone_empty():
    assert phones.normalize_phone("") == ""
    assert phones.normalize_phone(None) == ""
    assert phones.normalize_phone("   ") == ""


def test_format_phone_for_local_api_leading_zero(monkeypatch):
    monkeypatch.setattr(phones.settings, "local_api_country_code", "58")
    assert phones.format_phone_for_local_api("04122571528") == "+584122571528"


def test_format_phone_for_local_api_double_zero(monkeypatch):
    monkeypatch.setattr(phones.settings, "local_api_country_code", "58")
    assert phones.format_phone_for_local_api("00584122571528") == "+584122571528"


def test_format_phone_for_local_api_already_with_country(monkeypatch):
    monkeypatch.setattr(phones.settings, "local_api_country_code", "58")
    assert phones.format_phone_for_local_api("584122571528") == "+584122571528"


def test_format_phone_for_local_api_ten_digits(monkeypatch):
    monkeypatch.setattr(phones.settings, "local_api_country_code", "58")
    assert phones.format_phone_for_local_api("4122571528") == "+584122571528"


def test_format_phone_for_local_api_plus_passthrough(monkeypatch):
    monkeypatch.setattr(phones.settings, "local_api_country_code", "58")
    assert phones.format_phone_for_local_api("+584122571528") == "+584122571528"


def test_format_phone_for_local_api_country_code_override(monkeypatch):
    monkeypatch.setattr(phones.settings, "local_api_country_code", "57")
    assert phones.format_phone_for_local_api("04122571528") == "+574122571528"


def test_format_phone_for_local_api_empty():
    assert phones.format_phone_for_local_api("") == ""
    assert phones.format_phone_for_local_api(None) == ""


def test_phones_equivalent_leading_zero():
    assert phones.phones_equivalent("04122571528", "4122571528")


def test_phones_equivalent_country_prefix():
    assert phones.phones_equivalent("584122571528", "4122571528")


def test_phones_equivalent_last_ten():
    assert phones.phones_equivalent("+584122571528", "4122571528")


def test_phones_equivalent_non_matching():
    assert not phones.phones_equivalent("04122571528", "04125551234")


def test_phones_equivalent_empty():
    assert not phones.phones_equivalent("", "4122571528")
    assert not phones.phones_equivalent(None, None)
