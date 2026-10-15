import pytest
from open_llm_vtuber.companion.model import LocalModel


def test_v1_rejects_nonlocal_model_endpoint():
    with pytest.raises(ValueError, match="本机"):
        LocalModel("https://example.com/v1", "anything")
