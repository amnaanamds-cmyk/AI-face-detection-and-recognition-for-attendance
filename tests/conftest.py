import os
import tempfile

# Must be set before the app is imported: use the model-free backend and a throwaway secret.
os.environ.setdefault("VISION_BACKEND", "fake")
os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("ADMIN_PASSWORD", "admin123")

import cv2  # noqa: E402
import numpy as np  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import database  # noqa: E402
from app.vision import backends  # noqa: E402
from app.vision.matcher import gallery_cache  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_login_lockout():
    from app.routers import auth

    auth._failures.clear()
    yield


@pytest.fixture()
def db_url():
    with tempfile.TemporaryDirectory() as d:
        url = f"sqlite:///{d}/test.db"
        database.configure(url)
        database.init_db()
        gallery_cache.invalidate()
        backends.set_backend(backends.FakeBackend())
        yield url
        database.engine.dispose()


@pytest.fixture()
def db(db_url):
    s = database.SessionLocal()
    yield s
    s.close()


@pytest.fixture()
def client(db_url):
    from app.main import app

    with TestClient(app) as c:
        yield c


def login(client, username="admin", password="admin123"):
    r = client.post("/login", data={"username": username, "password": password}, follow_redirects=False)
    assert r.status_code == 303, r.text
    return client


def face_image(seed: int, size: int = 120) -> np.ndarray:
    """Synthetic "face" for the FakeBackend: a smooth random texture unique per seed."""
    rng = np.random.RandomState(seed)
    small = rng.randint(0, 255, (8, 8)).astype(np.uint8)
    img = cv2.resize(small, (size, size), interpolation=cv2.INTER_CUBIC)
    return cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)


def noisy(img: np.ndarray, seed: int, sigma: float = 6.0) -> np.ndarray:
    rng = np.random.RandomState(seed)
    return np.clip(img.astype(np.float32) + rng.normal(0, sigma, img.shape), 0, 255).astype(np.uint8)


def data_url(img: np.ndarray) -> str:
    import base64

    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 95])
    return "data:image/jpeg;base64," + base64.b64encode(buf.tobytes()).decode()
