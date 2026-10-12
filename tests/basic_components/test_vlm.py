"""Opt-in live smoke test for the configured vision-language model."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from dotenv import load_dotenv


@pytest.mark.skipif(
    os.getenv("RUN_LIVE_VLM_TESTS") != "1",
    reason="set RUN_LIVE_VLM_TESTS=1 to run the credentialed VLM smoke test",
)
def test_configured_vlm_accepts_one_image() -> None:
    """Call the configured VLM only when the operator explicitly opts in."""
    load_dotenv()
    from openai import OpenAI

    from src.utils.helper import image_to_base64

    api_key = os.getenv("VLM_API_KEY")
    base_url = os.getenv("VLM_BASE_URL")
    model_name = os.getenv("VLM_MODEL_NAME")
    if not all((api_key, base_url, model_name)):
        pytest.fail("VLM_API_KEY, VLM_BASE_URL and VLM_MODEL_NAME are required")

    image_path = Path(__file__).resolve().parents[2] / "Snipaste.jpg"
    if not image_path.is_file():
        pytest.skip("Snipaste.jpg live-test fixture is not available")

    client = OpenAI(api_key=api_key, base_url=base_url, timeout=30)
    response = client.chat.completions.create(
        model=model_name,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "这是什么图案?"},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/png;base64,{image_to_base64(str(image_path))}"
                        },
                    },
                ],
            }
        ],
    )

    assert response.choices and response.choices[0].message.content
