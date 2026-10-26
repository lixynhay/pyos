"""Unit-тесты login(): отказ после неудачных паролей (P1 auth-bypass)."""

import builtins

import pyos.main as main_module
from pyos.main import login
from pyos.users import hash_password


def _set_password(kernel, name="user", password="secret"):
    kernel.users.get_by_name(name).password_hash = hash_password(password)


def _patch_input(monkeypatch, values):
    calls = []
    it = iter(values)

    def fake_input(prompt=""):
        calls.append(prompt)
        return next(it)

    monkeypatch.setattr(builtins, "input", fake_input)
    return calls


def _patch_getpass(monkeypatch, values):
    calls = []
    it = iter(values)

    def fake_getpass(prompt=""):
        calls.append(prompt)
        return next(it)

    monkeypatch.setattr("getpass.getpass", fake_getpass)
    return calls


def test_login_correct_password_returns_user(kernel, monkeypatch):
    _set_password(kernel)
    _patch_input(monkeypatch, ["user"])
    _patch_getpass(monkeypatch, ["secret"])

    user = login(kernel, max_attempts=3)

    assert user is not None
    assert user.name == "user"
    assert not user.is_root


def test_login_wrong_password_retries_up_to_max_attempts(kernel, monkeypatch):
    _set_password(kernel)
    inputs = _patch_input(monkeypatch, ["user", "user", "user"])
    _patch_getpass(monkeypatch, ["bad", "bad", "secret"])

    user = login(kernel, max_attempts=3)

    assert user is not None
    assert user.name == "user"
    assert len(inputs) == 3


def test_login_three_wrong_passwords_returns_none_not_root(kernel, monkeypatch):
    _set_password(kernel)
    _patch_input(monkeypatch, ["user"] * 3)
    _patch_getpass(monkeypatch, ["bad"] * 3)

    user = login(kernel, max_attempts=3)

    assert user is None


def test_login_eof_fallback_still_returns_root_if_current_behavior_requires_it(
        kernel, monkeypatch):
    # pyos/README.md документирует auto-root при EOF на неинтерактивном stdin.
    def eof(prompt=""):
        raise EOFError

    monkeypatch.setattr(builtins, "input", eof)

    user = login(kernel)

    assert user is not None
    assert user.is_root


def test_login_unknown_user_does_not_return_root(kernel, monkeypatch):
    _patch_input(monkeypatch, ["ghost"] * 3)

    user = login(kernel, max_attempts=3)

    assert user is None


def test_login_does_not_print_root_fallback_after_wrong_passwords(
        kernel, monkeypatch, capsys):
    _set_password(kernel)
    _patch_input(monkeypatch, ["user"] * 3)
    _patch_getpass(monkeypatch, ["bad"] * 3)

    user = login(kernel, max_attempts=3)
    captured = capsys.readouterr()

    assert user is None
    assert "falling back" not in captured.out + captured.err
    assert "authentication failure" in captured.err


def test_main_returns_nonzero_without_starting_shell(kernel, monkeypatch):
    monkeypatch.setattr(main_module, "login", lambda kernel: None)

    def fail_if_started(*args, **kwargs):
        raise AssertionError("shell must not start after failed login")

    monkeypatch.setattr(main_module, "Shell", fail_if_started)

    assert main_module.main() == 1
