import pytest

from app.services import ai_service


@pytest.fixture(autouse=True)
def _fresh_model_queue():
    """Each test starts with no Gemini models marked as recently failed."""
    ai_service._resting.clear()
    yield
    ai_service._resting.clear()
