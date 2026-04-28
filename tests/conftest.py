"""Shared fixtures.

Every test gets a fresh temp data dir + an admin user pre-created. The
fixture order is `data_dir → admin_user → client → logged_in_client`
so tests can pick the level they need.
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    """Empty workspace dir per test — schema gets created by create_app."""
    d = tmp_path / "data"
    d.mkdir()
    return d


@pytest.fixture
def admin_user(data_dir: Path) -> dict:
    from praxnest import db, auth

    db.initialize(data_dir)
    user_id = auth.create_user(data_dir, username="admin", password="testpass123", role="admin")
    return {"id": user_id, "username": "admin", "password": "testpass123", "role": "admin"}


@pytest.fixture
def client(data_dir: Path, admin_user: dict):
    """Anonymous (logged-out) client."""
    from fastapi.testclient import TestClient
    from praxnest.app import create_app

    return TestClient(create_app(data_dir=data_dir))


@pytest.fixture
def logged_in_client(client, admin_user: dict):
    """Client with the admin session cookie set. Use this for any
    test that needs to hit a protected endpoint."""
    r = client.post(
        "/api/auth/login",
        json={"username": admin_user["username"], "password": admin_user["password"]},
    )
    assert r.status_code == 200, f"login failed: {r.text}"
    return client
