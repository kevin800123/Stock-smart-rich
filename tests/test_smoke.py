import importlib


def test_package_imports():
    assert importlib.import_module("stocks_power_rich") is not None
