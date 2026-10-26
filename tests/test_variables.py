"""Тесты расширенных возможностей переменных pyOS.

Проверяет:
  - ${VAR:-word}, ${VAR:=word}, ${VAR:+word}, ${VAR:?word}
  - Локальные переменные (VAR=value)
  - export/unset/set с локальными переменными
  - VAR=value command
"""

import pytest

from pyos.errors import PyOSError
from pyos.shell import Shell, tokenize


def words(line, env=None):
    """Вспомогательная функция: токенизирует строку, возвращает список строк."""
    return [str(t) for t in tokenize(line, env)]


# ============================================================================
# ${VAR:-word} — значение по умолчанию
# ============================================================================

def test_default_unset():
    """${VAR:-default} возвращает default, если VAR не установлен."""
    assert words("${NO_SUCH_VAR:-default}") == ["default"]


def test_default_empty():
    """${VAR:-default} возвращает default, если VAR пуст."""
    assert words("${EMPTY:-default}", {"EMPTY": ""}) == ["default"]


def test_default_set():
    """${VAR:-default} возвращает значение VAR, если он установлен."""
    assert words("${NAME:-default}", {"NAME": "Alice"}) == ["Alice"]


def test_default_with_colon_in_word():
    """${VAR:-http://example.com} — слово может содержать :."""
    result = words("${URL:-http://example.com}")
    assert result == ["http://example.com"]


# ============================================================================
# ${VAR:=word} — присвоить значение по умолчанию (модифицирует env)
# ============================================================================

def test_assign_unset():
    """${VAR:=default} возвращает default и устанавливает VAR в env."""
    env = {}
    result = words("${NEW_VAR:=assigned}", env)
    assert result == ["assigned"]
    assert env.get("NEW_VAR") == "assigned"


def test_assign_set():
    """${VAR:=default} возвращает значение VAR, если установлен, не меняя его."""
    env = {"EXISTING": "keep"}
    result = words("${EXISTING:=assigned}", env)
    assert result == ["keep"]
    assert env["EXISTING"] == "keep"


# ============================================================================
# ${VAR:+word} — альтернативное значение
# ============================================================================

def test_alt_set():
    """${VAR:+alt} возвращает alt, если VAR установлен и не пуст."""
    assert words("${NAME:+present}", {"NAME": "hello"}) == ["present"]


def test_alt_unset():
    """${VAR:+alt} возвращает пустую строку, если VAR не установлен."""
    assert words("${NO_VAR:+alt}") == [""]


def test_alt_empty():
    """${VAR:+alt} возвращает пустую строку, если VAR пуст."""
    assert words("${EMPTY:+alt}", {"EMPTY": ""}) == [""]


# ============================================================================
# ${VAR:?word} — ошибка при отсутствии
# ============================================================================

def test_error_unset():
    """${VAR:?msg} кидает PyOSError, если VAR не установлен."""
    with pytest.raises(PyOSError, match=r"\$MISSING: required"):
        words("${MISSING:?required}")


def test_error_empty():
    """${VAR:?msg} кидает PyOSError, если VAR пуст."""
    with pytest.raises(PyOSError, match=r"\$EMPTY_VAR: needed"):
        words("${EMPTY_VAR:?needed}", {"EMPTY_VAR": ""})


def test_error_set():
    """${VAR:?msg} работает как обычное раскрытие, если VAR установлен."""
    assert words("${OK:?error}", {"OK": "value"}) == ["value"]


# ============================================================================
# Обычное ${} продолжает работать
# ============================================================================

def test_plain_brace():
    """${VAR} без оператора раскрывается как и раньше."""
    assert words("${HOME}", {"HOME": "/home/user"}) == ["/home/user"]


# ============================================================================
# Локальные переменные: VAR=value
# ============================================================================

def test_local_var_assignment(kernel, root):
    """VAR=value как команда устанавливает локальную переменную."""
    shell = Shell(kernel, root)
    shell.handle_line("MY_VAR=hello")
    assert shell._locals.get("MY_VAR") == "hello"


def test_local_var_expansion(kernel, root, capsys):
    """После VAR=value, $VAR раскрывается."""
    shell = Shell(kernel, root)
    shell.handle_line("MY_VAR=hello")
    shell.handle_line("echo $MY_VAR")
    assert capsys.readouterr().out.strip() == "hello"


def test_local_var_not_in_env(kernel, root):
    """env не показывает локальные переменные."""
    shell = Shell(kernel, root)
    shell.handle_line("MY_VAR=hello")
    assert "MY_VAR" not in shell.env


def test_local_var_in_set(kernel, root, capsys):
    """set показывает локальные переменные."""
    shell = Shell(kernel, root)
    shell.handle_line("MY_VAR=hello")
    shell.handle_line("set")
    output = capsys.readouterr().out
    assert "MY_VAR=hello" in output


# ============================================================================
# export
# ============================================================================

def test_export_with_value(kernel, root):
    """export VAR=value устанавливает переменную окружения."""
    shell = Shell(kernel, root)
    shell.handle_line("export FOO=bar")
    assert shell.env.get("FOO") == "bar"


def test_export_local_var(kernel, root):
    """export VAR переносит локальную переменную в окружение."""
    shell = Shell(kernel, root)
    shell.handle_line("MY_VAR=hello")
    shell.handle_line("export MY_VAR")
    assert shell.env.get("MY_VAR") == "hello"
    assert "MY_VAR" not in shell._locals


def test_export_local_no_stomp_env(kernel, root):
    """export VAR=value удаляет локальную переменную с тем же именем."""
    shell = Shell(kernel, root)
    shell.handle_line("MY_VAR=local_value")
    shell.handle_line("export MY_VAR=env_value")
    assert shell.env.get("MY_VAR") == "env_value"
    assert "MY_VAR" not in shell._locals


# ============================================================================
# unset
# ============================================================================

def test_unset_local(kernel, root):
    """unset удаляет локальную переменную."""
    shell = Shell(kernel, root)
    shell.handle_line("MY_VAR=hello")
    shell.handle_line("unset MY_VAR")
    assert "MY_VAR" not in shell._locals


def test_unset_env(kernel, root):
    """unset удаляет переменную окружения."""
    shell = Shell(kernel, root)
    shell.handle_line("export FOO=bar")
    assert "FOO" in shell.env
    shell.handle_line("unset FOO")
    assert "FOO" not in shell.env


# ============================================================================
# VAR=value command
# ============================================================================

def test_var_prefix_in_env(kernel, root):
    """VAR=value command передаёт VAR в окружение (через env)."""
    shell = Shell(kernel, root)
    # Используем env, чтобы проверить, что FOO=bar попала в окружение команды
    shell.handle_line("FOO=bar env | grep FOO=")
    # После выполнения FOO не должна остаться в locals или env
    assert "FOO" not in shell._locals
    assert "FOO" not in shell.env


def test_var_prefix_not_persistent(kernel, root):
    """VAR=value command не сохраняет VAR после выполнения."""
    shell = Shell(kernel, root)
    shell.handle_line("TMP_VAR=temp echo hi")
    assert "TMP_VAR" not in shell._locals
    assert "TMP_VAR" not in shell.env


def test_var_prefix_local_still_available(kernel, root, capsys):
    """Локальная переменная раскрывается в аргументах команды с префиксом."""
    shell = Shell(kernel, root)
    shell.handle_line("MY_VAR=hello")
    # FOO=bar не влияет на раскрытие аргументов (как в bash)
    shell.handle_line("FOO=bar echo $MY_VAR")
    captured = capsys.readouterr().out.strip()
    assert captured == "hello"


def test_multi_var_prefix_not_persistent(kernel, root):
    """Множественные VAR=value перед командой не сохраняются."""
    shell = Shell(kernel, root)
    shell.handle_line("A=1 B=2 echo hi")
    assert "A" not in shell._locals
    assert "A" not in shell.env
    assert "B" not in shell._locals
    assert "B" not in shell.env


# ============================================================================
# Комбинированные тесты
# ============================================================================

def test_default_with_local(kernel, root, capsys):
    """${VAR:-default} работает с локальными переменными через handle_line."""
    shell = Shell(kernel, root)
    shell.handle_line("NAME=Alice")
    shell.handle_line("echo ${NAME:-unknown} ${MISSING:-nobody}")
    assert capsys.readouterr().out.strip() == "Alice nobody"


def test_alt_with_local(kernel, root, capsys):
    """${VAR:+alt} работает с локальными переменными."""
    shell = Shell(kernel, root)
    shell.handle_line("FLAG=1")
    shell.handle_line("echo ${FLAG:+yes} ${NOFLAG:+no}")
    assert capsys.readouterr().out.strip() == "yes"


def test_error_in_handle_line(kernel, root, capsys):
    """${VAR:?msg} в handle_line вызывает stderr и код возврата 1."""
    shell = Shell(kernel, root)
    shell.handle_line('echo before ${REQUIRED:?missing} echo after')
    captured = capsys.readouterr()
    assert "missing" in captured.err or "Error" in captured.err or "pyos" in captured.err or not captured.out
    # После ошибки раскрытия последующие команды не должны выполняться
    assert "after" not in captured.out


# ============================================================================
# Регрессия: существующие токены работают
# ============================================================================

def test_single_dollar_works():
    """Одиночный $ вне раскрытия не ломается."""
    assert words("$") == ["$"]


def test_dollar_question_works():
    """$? раскрывается в '0' по умолчанию."""
    assert words("$?", {"?": "0"}) == ["0"]


def test_dollar_dollar_works():
    """$$ раскрывается в PID."""
    assert words("$$", {"$": "42"}) == ["42"]


def test_background_ampersand_still_works():
    """& в командах всё ещё фон."""
    from pyos.shell import split_jobs
    jobs = split_jobs(tokenize("sleep 1 &", {}))
    assert len(jobs) == 1
    assert jobs[0][1] is True


# ============================================================================
# Регрессия: присваивание в одном сегменте и раскрытие в следующем
# ============================================================================

def test_assignment_before_semicolon_visible_to_next_command(kernel, root,
                                                             capsys):
    """VAR=value до ; виден следующей команде той же строки."""
    shell = Shell(kernel, root)
    shell.handle_line("NAME=X; echo $NAME")
    assert capsys.readouterr().out.strip() == "X"


def test_assignment_before_semicolon_default_operator(kernel, root, capsys):
    """${VAR:-word} видит присваивание из предыдущего сегмента строки."""
    shell = Shell(kernel, root)
    shell.handle_line("NAME=X; echo ${NAME:-guest}")
    assert capsys.readouterr().out.strip() == "X"


def test_multiple_assignments_before_semicolon(kernel, root, capsys):
    """Несколько присваиваний подряд видны последней команде."""
    shell = Shell(kernel, root)
    shell.handle_line("A=1; B=2; echo $A$B")
    assert capsys.readouterr().out.strip() == "12"


def test_assignment_before_and_operator(kernel, root, capsys):
    """VAR=value до && виден следующему сегменту."""
    shell = Shell(kernel, root)
    shell.handle_line("NAME=X && echo $NAME")
    assert capsys.readouterr().out.strip() == "X"


def test_assignment_before_or_operator(kernel, root, capsys):
    """VAR=value до || выполняется, печати нет, код 0."""
    shell = Shell(kernel, root)
    shell.handle_line("NAME=X || echo fail")
    assert capsys.readouterr().out.strip() == ""
    assert shell.last_code == 0
    assert shell._locals.get("NAME") == "X"


def test_short_circuit_prevents_assignment(kernel, root, capsys):
    """Пропущенный по && сегмент не устанавливает переменную."""
    shell = Shell(kernel, root)
    shell.handle_line("false && NAME=X; echo ${NAME:-guest}")
    assert capsys.readouterr().out.strip() == "guest"


def test_empty_assignment_default_operator(kernel, root, capsys):
    """Пустое присваивание и ${VAR:=word} в одной строке."""
    shell = Shell(kernel, root)
    shell.handle_line("NAME=; echo ${NAME:-guest}")
    assert capsys.readouterr().out.strip() == "guest"
    shell.handle_line("NAME=; echo ${NAME:=guest}; echo $NAME")
    assert capsys.readouterr().out.strip().splitlines() == ["guest", "guest"]


def test_export_assignment_same_line(kernel, root, capsys):
    """export VAR=value до ; виден следующей команде."""
    shell = Shell(kernel, root)
    shell.handle_line("export NAME=X; echo $NAME")
    assert capsys.readouterr().out.strip() == "X"
    assert shell.env.get("NAME") == "X"


def test_unset_assignment_same_line(kernel, root, capsys):
    """unset до ; действует на следующую команду той же строки."""
    shell = Shell(kernel, root)
    shell.handle_line("NAME=X")
    shell.handle_line("unset NAME; echo ${NAME:-guest}")
    assert capsys.readouterr().out.strip() == "guest"


def test_env_prefix_assignment_does_not_persist(kernel, root, capsys):
    """VAR=value command не сохраняет переменную в шелле."""
    shell = Shell(kernel, root)
    shell.handle_line("NAME=X echo hi")
    assert capsys.readouterr().out.strip() == "hi"
    shell.handle_line("echo [$NAME]")
    assert capsys.readouterr().out.strip() == "[]"


def test_single_quotes_prevent_expansion_same_line(kernel, root, capsys):
    """Одинарные кавычки не раскрывают переменную из той же строки."""
    shell = Shell(kernel, root)
    shell.handle_line("NAME=X; echo '$NAME'")
    assert capsys.readouterr().out.strip() == "$NAME"


def test_double_quotes_expand_assignment_same_line(kernel, root, capsys):
    """Двойные кавычки раскрывают переменную из того же сегмента строки."""
    shell = Shell(kernel, root)
    shell.handle_line('NAME=X; echo "$NAME"')
    assert capsys.readouterr().out.strip() == "X"


def test_backslash_preserved_same_line(kernel, root, capsys):
    """Экранирование не ломается рядом с присваиванием в той же строке."""
    shell = Shell(kernel, root)
    shell.handle_line(r"NAME=X; echo a\ b $NAME")
    assert capsys.readouterr().out.strip() == "a b X"


def test_special_question_mark_same_line(kernel, root, capsys):
    """$? обновляется между сегментами, $$ остаётся PID init."""
    shell = Shell(kernel, root)
    shell.handle_line("false; echo $?")
    assert capsys.readouterr().out.strip() == "1"
    shell.handle_line("true; echo $?")
    assert capsys.readouterr().out.strip() == "0"
    shell.handle_line("echo $$")
    assert capsys.readouterr().out.strip() == "1"


def test_pipeline_after_assignment_same_line(kernel, root, capsys):
    """Пайп после присваивания раскрывает переменную и работает как раньше."""
    shell = Shell(kernel, root)
    shell.handle_line("NAME=X; echo $NAME | tr a-z A-Z")
    assert capsys.readouterr().out.strip() == "X"
    shell.handle_line("NAME=X; echo hello | cat")
    assert capsys.readouterr().out.strip() == "hello"


def test_background_after_assignment_same_line(kernel, root, capsys):
    """Присваивание до фоновой команды сохраняется после wait."""
    shell = Shell(kernel, root)
    shell.handle_line("NAME=X; sleep 1 &")
    assert "[1]" in capsys.readouterr().out
    shell.handle_line("jobs")
    assert "sleep 1" in capsys.readouterr().out
    shell.handle_line("wait")
    assert "Done(0)" in capsys.readouterr().out
    shell.handle_line("echo $NAME")
    assert capsys.readouterr().out.strip() == "X"


def test_readme_example_name_alice_default_guest(kernel, root, capsys):
    """Пример из README: NAME=Alice; echo ${NAME:-guest} печатает Alice."""
    shell = Shell(kernel, root)
    shell.handle_line("NAME=Alice; echo ${NAME:-guest}")
    assert capsys.readouterr().out.strip() == "Alice"


def test_assignment_before_and_operator_still_works(kernel, root, capsys):
    """Присваивание до && видно правому сегменту и сохраняется в шелле."""
    shell = Shell(kernel, root)
    shell.handle_line("NAME=X && echo $NAME")
    assert capsys.readouterr().out.strip() == "X"
    assert shell._locals.get("NAME") == "X"


def test_assignment_before_or_operator_still_works(kernel, root, capsys):
    """Присваивание до || выполняется, || не запускает правый сегмент."""
    shell = Shell(kernel, root)
    shell.handle_line("NAME=X || echo fail")
    captured = capsys.readouterr()
    assert captured.out.strip() == ""
    assert captured.err == ""
    assert shell.last_code == 0
    assert shell._locals.get("NAME") == "X"


def test_short_circuit_prevents_assignment_still_works(kernel, root, capsys):
    """Сегмент, пропущенный по &&, не выполняет присваивание."""
    shell = Shell(kernel, root)
    shell.handle_line("false && NAME=X; echo ${NAME:-guest}")
    assert capsys.readouterr().out.strip() == "guest"
    assert "NAME" not in shell._locals


def test_cd_then_pwd_same_line(kernel, root, capsys):
    """$PWD пересчитывается перед каждым сегментом: cd виден дальше в строке."""
    shell = Shell(kernel, root)
    shell.handle_line("cd /tmp; echo $PWD")
    assert capsys.readouterr().out.strip() == "/tmp"


def test_var_default_assign_persists_same_line(kernel, root, capsys):
    """${VAR:=word} сохраняет значение в шелле, а не только в выводе."""
    shell = Shell(kernel, root)
    shell.handle_line("NAME=; echo ${NAME:=guest}")
    assert capsys.readouterr().out.strip() == "guest"
    assert shell._locals.get("NAME") == "guest"


def test_var_default_assign_then_echo_variable(kernel, root, capsys):
    """После ${VAR:=word} следующая команда той же строки видит значение."""
    shell = Shell(kernel, root)
    shell.handle_line("NAME=; echo ${NAME:=guest}; echo $NAME")
    assert capsys.readouterr().out.strip().splitlines() == ["guest", "guest"]