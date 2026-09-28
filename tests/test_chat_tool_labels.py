"""Codex runs plain shell instead of the platform's own list_files/read_file/... tools (see
agent/codex_runtime.py's bridge instructions), so _tool_status_label/_friendly_command_label have
to reconstruct a friendly Russian label from the raw command text. Regression coverage for a real
user complaint (2026-07-23): the activity feed was showing raw `/bin/bash -lc '...'` commands,
bare "exit 0"/"exit 1" statuses, and English "changed ..." text in an otherwise-Russian UI.
"""

from src.api.routers.chat import _friendly_command_label, _tool_status_label


def test_strips_bash_wrapper_and_quoting():
    label = _friendly_command_label("/bin/bash -lc 'pwd && echo hi'")
    assert "/bin/bash" not in label
    assert "-lc" not in label


def test_docker_build_gets_a_friendly_label():
    assert _friendly_command_label("/bin/bash -lc 'docker build -t alfa-romeo-service .'") == (
        "Собираю Docker-образ"
    )


def test_docker_run_gets_a_friendly_label():
    assert (
        _friendly_command_label(
            "/bin/bash -lc 'docker run --rm -d --name test -p 18080:8080 alfa-romeo-service'"
        )
        == "Запускаю тестовый контейнер"
    )


def test_structure_exploration_gets_a_friendly_label():
    assert (
        _friendly_command_label("/bin/bash -lc 'rg --files /workspace'")
        == "Изучаю структуру проекта"
    )
    compound = '/bin/bash -lc "pwd && ls -la && find . -maxdepth 3 -type f | sort | head -200"'
    assert _friendly_command_label(compound) == "Изучаю структуру проекта"


def test_file_reading_gets_a_friendly_label():
    assert (
        _friendly_command_label("/bin/bash -lc \"sed -n '1,220p' app.py\"") == "Читаю файлы проекта"
    )


def test_pip_install_is_not_misclassified_as_generic_python():
    # python3 -m pip install ... contains both "python3" and "pip install" - the more specific
    # dependency-install label must win over the generic "checking code" one.
    assert (
        _friendly_command_label("/bin/bash -lc 'python3 -m pip install requests'")
        == "Устанавливаю зависимости"
    )


def test_unrecognized_command_falls_back_to_cleaned_text_not_raw_wrapper():
    label = _friendly_command_label("/bin/bash -lc 'echo hello world'")
    assert label == "Выполняю: echo hello world"


def test_long_unrecognized_command_is_truncated():
    long_arg = "x" * 200
    label = _friendly_command_label(f"/bin/bash -lc 'echo {long_arg}'")
    assert label.startswith("Выполняю: echo ")
    assert label.endswith("…")
    assert len(label) < len(long_arg)


def test_tool_status_label_routes_command_execution_through_friendly_label():
    label = _tool_status_label("command_execution", {"command": "/bin/bash -lc 'docker build .'"})
    assert label == "Собираю Docker-образ"


def test_tool_status_label_empty_command_execution_has_a_default():
    assert _tool_status_label("command_execution", {"command": ""}) == "Выполняю команду"
