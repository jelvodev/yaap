from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from app.main import create_app


def test_index_is_revalidated_and_hashed_assets_are_cached(tmp_path, monkeypatch):
    monkeypatch.setenv("YAAP_MASTER_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("YAAP_DATA_DIR", str(tmp_path / "data"))
    static = tmp_path / "static"
    (static / "assets").mkdir(parents=True)
    (static / "index.html").write_text("<script src='/assets/index-abc123.js'></script>")
    (static / "assets" / "index-abc123.js").write_text("console.log(1)")

    with TestClient(create_app(static_dir=static)) as client:
        page = client.get("/chat/14")
        asset = client.get("/assets/index-abc123.js")

    assert page.status_code == 200 and page.headers["cache-control"] == "no-cache"
    assert asset.status_code == 200 and "immutable" in asset.headers["cache-control"]
