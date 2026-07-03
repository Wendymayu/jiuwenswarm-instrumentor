# tests/test_install_pth.py
from __future__ import annotations
import os
import sys

import pytest

from jiuwenswarm_instrumentor import _install_pth


def test_install_writes_pth_into_target_dir(monkeypatch, tmp_path):
    """install_pth writes jiuwenswarm_instrumentor.pth with the autoload import line."""
    target = tmp_path / "site-packages"
    monkeypatch.setattr(_install_pth, "_site_packages_dirs", lambda: [str(target)])

    rc = _install_pth.install_pth()

    assert rc == 0
    pth = target / _install_pth._PTH_NAME
    assert pth.exists()
    content = pth.read_text(encoding="utf-8")
    assert "import jiuwenswarm_instrumentor._autoload" in content
    assert "OTEL_ENABLED" in content


def test_install_is_idempotent(monkeypatch, tmp_path):
    """Running twice overwrites cleanly (no duplicate, no error)."""
    target = tmp_path / "sp"
    monkeypatch.setattr(_install_pth, "_site_packages_dirs", lambda: [str(target)])

    _install_pth.install_pth()
    _install_pth.install_pth()

    files = [p for p in target.iterdir() if p.name == _install_pth._PTH_NAME]
    assert len(files) == 1


def test_uninstall_removes_pth(monkeypatch, tmp_path):
    """uninstall_pth deletes the .pth from every site-packages dir it appears in."""
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()
    (a / _install_pth._PTH_NAME).write_text("x", encoding="utf-8")
    (b / _install_pth._PTH_NAME).write_text("x", encoding="utf-8")
    monkeypatch.setattr(_install_pth, "_site_packages_dirs", lambda: [str(a), str(b)])

    rc = _install_pth.uninstall_pth()

    assert rc == 0
    assert not (a / _install_pth._PTH_NAME).exists()
    assert not (b / _install_pth._PTH_NAME).exists()


def test_uninstall_when_absent_is_silent(monkeypatch, tmp_path):
    """No .pth present → uninstall reports nothing to remove, still returns 0."""
    target = tmp_path / "empty"
    target.mkdir()
    monkeypatch.setattr(_install_pth, "_site_packages_dirs", lambda: [str(target)])
    rc = _install_pth.uninstall_pth()
    assert rc == 0


def test_install_falls_back_to_next_writable_dir(monkeypatch, tmp_path):
    """A read-only first dir is skipped; the .pth lands in the next writable one.
    (POSIX-only: chmod read-only is a no-op on Windows, so skip there.)"""
    if sys.platform == "win32":
        pytest.skip("chmod read-only is not enforced on Windows")
    ro = tmp_path / "readonly"
    rw = tmp_path / "writable"
    ro.mkdir()
    rw.mkdir()
    # make ro unwritable
    os.chmod(ro, 0o555)
    monkeypatch.setattr(_install_pth, "_site_packages_dirs", lambda: [str(ro), str(rw)])
    try:
        rc = _install_pth.install_pth()
    finally:
        os.chmod(ro, 0o755)  # restore so tmp_path can be cleaned up

    assert rc == 0
    assert (rw / _install_pth._PTH_NAME).exists()
    assert not (ro / _install_pth._PTH_NAME).exists()


def test_main_dispatches_on_argv(monkeypatch, tmp_path):
    """main() routes --uninstall to uninstall_pth, otherwise install_pth."""
    target = tmp_path / "sp"
    target.mkdir()
    monkeypatch.setattr(_install_pth, "_site_packages_dirs", lambda: [str(target)])

    monkeypatch.setattr(sys, "argv", ["jiuwen-instrument-install-pth"])
    assert _install_pth.main() == 0
    assert (target / _install_pth._PTH_NAME).exists()

    monkeypatch.setattr(sys, "argv", ["jiuwen-instrument-install-pth", "--uninstall"])
    assert _install_pth.main() == 0
    assert not (target / _install_pth._PTH_NAME).exists()


def test_pth_content_matches_wheel_root_pth():
    """The installer must write the SAME import line that ships in the wheel-root .pth,
    so editable and non-editable installs fire identical autoload behavior."""
    repo_root = os.path.join(os.path.dirname(__file__), os.pardir)
    wheel_pth = os.path.normpath(os.path.join(repo_root, "jiuwenswarm_instrumentor.pth"))
    with open(wheel_pth, encoding="utf-8") as f:
        wheel_content = f.read().strip()
    assert wheel_content in _install_pth._PTH_CONTENT.strip()


def test_prefers_real_site_packages_over_venv_root(monkeypatch, tmp_path):
    """getsitepackages() on Windows venvs returns [venv_root, venv_root/Lib/site-packages];
    the .pth MUST land in Lib/site-packages (same dir as the editable finder) so the
    finder is registered before our import line runs at startup."""
    venv_root = tmp_path / "venv"
    site_pkgs = venv_root / "Lib" / "site-packages"
    venv_root.mkdir()
    site_pkgs.mkdir(parents=True)
    monkeypatch.setattr(
        "site.getsitepackages", lambda: [str(venv_root), str(site_pkgs)]
    )
    monkeypatch.setattr("site.getusersitepackages", lambda: None)

    rc = _install_pth.install_pth()

    assert rc == 0
    assert (site_pkgs / _install_pth._PTH_NAME).exists()
    assert not (venv_root / _install_pth._PTH_NAME).exists()
