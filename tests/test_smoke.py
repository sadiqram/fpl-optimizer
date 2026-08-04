"""Confirms the package installs and imports cleanly. Real pipeline smoke test lands with M1."""

import fpl_optimizer


def test_package_imports():
    assert fpl_optimizer is not None
