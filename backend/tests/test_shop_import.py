"""shop_import: strict URL validation (SSRF), parsing, flat-photo choice, API route (offline fakes)."""

from __future__ import annotations

import asyncio
import io
import json

import pytest

from wardrobe_testlib import wardrobe_dir, wclient  # noqa: F401

import shop_import
from shop_import import ShopImportError, _is_public_ip, guess_category, parse_product, validate_product_url

GOOD = "https://yame.vn/products/ao-polo-basic-non-branded-056-trang-0024651"


def photo(backdrop=(245, 245, 245), garment=(250, 250, 250)) -> bytes:
    from PIL import Image, ImageDraw

    image = Image.new("RGB", (120, 160), backdrop)
    ImageDraw.Draw(image).rectangle((30, 30, 90, 130), fill=garment)
    out = io.BytesIO()
    image.save(out, format="JPEG")
    return out.getvalue()


def product_json(**over) -> bytes:
    product = {
        "title": "Áo Polo Tay Ngắn Basic Colorlock™ Phom Vừa F3 - Non Branded 056 Trắng",
        "product_type": "Áo Thun Cổ Polo & Henley", "vendor": "YaMe", "tags": ["màu trắng"],
        "variants": [{"id": 1, "sku": "0024651S03", "price": "193900.00", "available": True}],
        "images": [{"src": "https://cdn.shopify.com/s/files/1/a/model.jpg?v=1"},
                   {"src": "https://cdn.shopify.com/s/files/1/a/flat.jpg?v=1"},
                   {"src": "https://evil.example/x.jpg"}],
    }
    product.update(over)
    return json.dumps({"product": product}).encode()


@pytest.mark.parametrize("url", [
    "http://yame.vn/products/x",                      # not https
    "https://evil.com/products/x",                    # host not allowed
    "https://yame.vn.evil.com/products/x",
    "https://user:pw@yame.vn/products/x",             # credentials
    "https://yame.vn:8443/products/x",                # custom port
    "https://127.0.0.1/products/x",                   # IP literal
    "https://[::1]/products/x",
    "https://yame.vn/admin",                          # not a product path
    "https://yame.vn/products/../../etc/passwd",
    "https://yame.vn/products/x\nHost: internal",
    "file:///etc/passwd",
    "https://yame.vn/products/" + "a" * 700,
])
def test_rejects_unsafe_urls(url):
    with pytest.raises(ShopImportError) as error:
        validate_product_url(url)
    assert error.value.status == 422


def test_accepts_shopify_product_urls(monkeypatch):
    ref = validate_product_url(GOOD + "?variant=123#reviews")
    assert ref.host == "yame.vn" and ref.variant_id == "123" and ref.json_url == GOOD + ".json"
    assert validate_product_url("https://www.yame.vn/collections/ao/products/abc.json").product_url == \
        "https://www.yame.vn/products/abc"
    monkeypatch.setenv("SHADOW_IMPORT_HOSTS", "shop.example.com")
    assert validate_product_url("https://shop.example.com/products/tee").host == "shop.example.com"
    with pytest.raises(ShopImportError):
        validate_product_url(GOOD)  # yame no longer allowed


def test_blocks_internal_addresses(monkeypatch):
    for ip in ("127.0.0.1", "10.0.0.5", "192.168.1.1", "169.254.169.254", "100.64.0.1", "::1", "fd00::1",
               "::ffff:127.0.0.1", "0.0.0.0"):
        assert not _is_public_ip(ip), ip
    assert _is_public_ip("104.21.69.97")

    def fake_getaddrinfo(host, port, family=0, type=0):
        return [(2, 1, 6, "", ("104.21.69.97", 443)), (2, 1, 6, "", ("10.0.0.7", 443))]

    monkeypatch.setattr(shop_import.socket, "getaddrinfo", fake_getaddrinfo)
    with pytest.raises(ShopImportError) as error:  # DNS answer mixing a private IP → refuse
        asyncio.run(shop_import.resolve_public("yame.vn"))
    assert error.value.code == "blocked_address"


def test_redirect_to_other_host_is_refused(monkeypatch):
    async def fake_resolve(host):
        return ["104.21.69.97"]

    async def fake_fetch(url, host, ip, max_bytes, accept):
        return 302, b"", "https://169.254.169.254/latest/meta-data"

    monkeypatch.setattr(shop_import, "resolve_public", fake_resolve)
    monkeypatch.setattr(shop_import, "_curl_fetch", fake_fetch)
    monkeypatch.setattr(shop_import, "_httpx_fetch", fake_fetch)
    with pytest.raises(ShopImportError):
        asyncio.run(shop_import.safe_fetch(GOOD + ".json", allowed_hosts=frozenset({"yame.vn"}),
                                           max_bytes=1000, accept="application/json"))


def test_parse_product_and_category():
    ref = validate_product_url(GOOD)
    info = parse_product(product_json(), ref)
    assert info["price"] == 193900 and info["sku"] == "0024651S03"
    assert info["images"] == ["https://cdn.shopify.com/s/files/1/a/model.jpg?v=1",
                              "https://cdn.shopify.com/s/files/1/a/flat.jpg?v=1"]  # foreign host dropped
    assert guess_category(info) == "top"  # "Non Branded" is not a hat
    with pytest.raises(ShopImportError):
        parse_product(b"<html>", ref)


def test_import_route_creates_tagged_item(wclient, monkeypatch):
    calls = []

    async def fake_import(provider, store, url):
        async def fetch_json(ref):
            calls.append(ref.json_url)
            return product_json()

        async def fetch_image(src):
            calls.append(src)
            # first image = lifestyle photo (dark busy border), second = flat shot on white
            return photo(backdrop=(40, 60, 30)) if "model" in src else photo()

        return await shop_import.import_product(provider, store, url, fetch_json=fetch_json, fetch_image=fetch_image)

    import wardrobe_api

    monkeypatch.setattr(wardrobe_api, "import_product", fake_import)
    res = wclient.post("/api/wardrobe/import", json={"url": GOOD})
    assert res.status_code == 200, res.text
    body = res.json()
    item = body["item"]
    assert body["duplicate"] is False and body["product"]["price_vnd"] == 193900
    assert item["category"] == "top" and item["subtype"] == "polo" and item["brand"] == "YaMe"
    assert item["product_url"] == GOOD and item["price_vnd"] == 193900 and item["product_code"] == "0024651"
    assert item["image_source_url"].endswith("flat.jpg?v=1") and item["import_source"] == "shop_link"
    assert item["attrs_v"] == 1 and item["color_hex"].startswith("#")
    again = wclient.post("/api/wardrobe/import", json={"url": GOOD + "?variant=1"}).json()
    assert again["duplicate"] is True and again["item"]["id"] == item["id"]


def test_import_route_rejects_bad_hosts(wclient):
    res = wclient.post("/api/wardrobe/import", json={"url": "https://evil.com/products/x"})
    assert res.status_code == 422 and res.json()["detail"]["code"] == "host_not_allowed"
    assert "yame.vn" in res.json()["detail"]["message"]
