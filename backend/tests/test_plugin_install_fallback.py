from __future__ import annotations

from types import SimpleNamespace

from app.data_providers.custom import loader


def test_python_plugin_install_retries_official_pypi_after_mirror_failure(tmp_path, monkeypatch):
    plugin_dir = tmp_path / "akshare"
    plugin_dir.mkdir()
    (plugin_dir / "requirements.txt").write_text("akshare==1.18.97\n", encoding="utf-8")

    monkeypatch.setattr(loader, "plugin_manifest", lambda name: {"runtime": "python"})
    monkeypatch.setattr(loader, "plugin_dir_of", lambda name: plugin_dir)
    monkeypatch.setattr(loader.shutil, "which", lambda name: "/usr/bin/uv" if name == "uv" else None)
    monkeypatch.setenv("UV_DEFAULT_INDEX", "https://pypi.tuna.tsinghua.edu.cn/simple")
    monkeypatch.setenv(
        "UV_EXTRA_INDEX_URL",
        "https://mirrors.aliyun.com/pypi/simple https://pypi.org/simple",
    )

    calls = []

    def fake_run(args, **kwargs):
        calls.append((list(args), dict(kwargs.get("env") or {})))
        if len(calls) == 1:
            return SimpleNamespace(returncode=1, stderr="No solution found", stdout="")
        return SimpleNamespace(returncode=0, stderr="", stdout="ok")

    monkeypatch.setattr(loader.subprocess, "run", fake_run)

    ok, message = loader.install_plugin("akshare")

    assert ok is True
    assert message == "安装成功"
    assert len(calls) == 2

    first_args, first_env = calls[0]
    assert first_args[:3] == ["/usr/bin/uv", "pip", "install"]
    assert first_env["UV_DEFAULT_INDEX"] == "https://pypi.tuna.tsinghua.edu.cn/simple"
    assert "UV_EXTRA_INDEX_URL" in first_env

    second_args, second_env = calls[1]
    assert "--no-config" in second_args
    assert "https://pypi.org/simple" in second_args
    assert "UV_DEFAULT_INDEX" not in second_env
    assert "UV_EXTRA_INDEX_URL" not in second_env
    assert second_env["UV_HTTP_TIMEOUT"] == "300"
