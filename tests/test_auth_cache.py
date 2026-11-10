"""
Pruebas para FLK-02: verificación de la caché memoizada con TTL de get_auth_status y get_git_global_identity.
"""

from unittest.mock import patch, MagicMock
import pytest
from src.storage.github_sync import GitHubSyncManager


class DummyDB:
    def __init__(self):
        self.settings = {}

    def get_setting(self, key, default=None):
        return self.settings.get(key, default)

    def set_setting(self, key, value):
        self.settings[key] = value

    def delete_setting(self, key):
        self.settings.pop(key, None)

    def get_active_local_node(self):
        return None

    def register_or_update_node(self, **kwargs):
        pass


def test_auth_status_cached_and_invalidated():
    db = DummyDB()
    gh = GitHubSyncManager(db_manager=db)
    gh.gh_bin = "gh"

    call_count = 0

    def fake_run(args, **kwargs):
        nonlocal call_count
        call_count += 1
        res = MagicMock()
        res.returncode = 0
        res.stdout = "Logged in to github.com account testuser"
        res.stderr = ""
        return res

    with patch("src.storage.github_sync.run_hidden", side_effect=fake_run):
        # 1. Primera llamada: ejecuta el proceso
        s1 = gh.get_auth_status()
        assert s1["authenticated"] is True
        assert call_count >= 1
        initial_calls = call_count

        # 2. Segunda llamada inmediata: debe usar caché (cero llamadas de proceso adicionales)
        s2 = gh.get_auth_status()
        assert s2["authenticated"] is True
        assert call_count == initial_calls

        # 3. Llamada con force=True: debe refrescar
        s3 = gh.get_auth_status(force=True)
        assert s3["authenticated"] is True
        assert call_count > initial_calls
        calls_after_force = call_count

        # 4. clear_pat_token debe invalidar la caché
        gh.clear_pat_token()
        s4 = gh.get_auth_status()
        assert call_count > calls_after_force


def test_git_global_identity_cached():
    db = DummyDB()
    gh = GitHubSyncManager(db_manager=db)

    calls = 0

    def fake_run(args, **kwargs):
        nonlocal calls
        calls += 1
        res = MagicMock()
        res.returncode = 0
        res.stdout = "Alice\n" if "user.name" in args else "alice@example.com\n"
        return res

    with patch("src.storage.github_sync.run_hidden", side_effect=fake_run):
        id1 = gh.get_git_global_identity()
        assert id1["name"] == "Alice"
        assert calls == 2

        # Segunda llamada: en caché
        id2 = gh.get_git_global_identity()
        assert id2["name"] == "Alice"
        assert calls == 2

        # Forzado
        id3 = gh.get_git_global_identity(force=True)
        assert calls == 4
