"""The APIM product's policy, API links and tags share provider concurrency state."""
from pathlib import Path


def test_product_child_writes_are_serialized():
    text = (Path(__file__).resolve().parents[2] / "modules" / "client-product.bicep").read_text("utf-8")
    assert "@batchSize(1)\nresource productApis " in text
    assert "@batchSize(1)\nresource productTagLinks " in text
    apis = text.split("resource productApis ", 1)[1].split("\n]", 1)[0]
    tags = text.split("resource productTagLinks ", 1)[1].split("\n]", 1)[0]
    subscription = text.split("resource subscription ", 1)[1].split("resource productTagLinks", 1)[0]
    assert "dependsOn: [productPolicy]" in apis
    assert "dependsOn: [productApis]" in subscription
    assert "dependsOn: [subscription]" in tags
