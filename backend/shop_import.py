"""Import a wardrobe item from a shop product link (new in SKI) — POST /api/wardrobe/import {url}.

Supported: Shopify-style product pages (https://<host>/products/<handle>, optionally under
/collections/<c>/ and with ?variant=<id>): we fetch the public `<url>.json`, take the title, price, SKU and
main product photo (preferring a clean flat shot on a light backdrop among the first images), then run the
normal tagger (Qwen-VL) + deterministic garment attributes.

SSRF safety (the server fetches URLs a user typed):
- https only, no userinfo, default port only, URL ≤ 600 chars, path must look like a Shopify product path;
- host must be in the allowlist SHADOW_IMPORT_HOSTS (comma-separated, default "yame.vn,www.yame.vn");
  photos only from those hosts or cdn.shopify.com;
- every host is resolved first and ALL its addresses must be public (no private/loopback/link-local/
  reserved/multicast/CGNAT…); the connection is then pinned to a checked address (curl --resolve /
  httpx + SNI), so DNS rebinding cannot swap in an internal IP;
- redirects are not followed automatically: at most 2, each re-validated with the same rules;
- size caps (JSON 3 MB, photo 8 MB) enforced while downloading, 12 s per request, 25 s per import.

Fetching uses the `curl` CLI when available (HTTP/2; yame.vn answers Python HTTP/1.1 clients with 429 —
same polite approach as wardrobe_seed_shop/build_seed_shop.py, normal browser UA, one product at a time),
else httpx.
"""

from __future__ import annotations

import asyncio
import io
import ipaddress
import json
import os
import re
import shutil
import socket
import subprocess
import tempfile
from dataclasses import dataclass
from typing import Any, Awaitable, Callable
from urllib.parse import parse_qs, urljoin, urlsplit

from wardrobe_store import WardrobeError, WardrobeStore, fold_vi, normalize_category, to_jpeg

DEFAULT_IMPORT_HOSTS = "yame.vn,www.yame.vn"
IMAGE_CDN_HOSTS = frozenset({"cdn.shopify.com"})
MAX_URL_LEN = 600
MAX_JSON_BYTES = 3_000_000
MAX_IMAGE_BYTES = 8 * 1024 * 1024
REQUEST_TIMEOUT_S = 12.0
IMPORT_TIMEOUT_S = 25.0
MAX_REDIRECTS = 2
MAX_IMAGE_CANDIDATES = 3
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
)
BRANDS = {"yame.vn": "YaMe", "www.yame.vn": "YaMe"}

_HOST_RE = re.compile(r"^(?=.{1,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")
_PRODUCT_PATH_RE = re.compile(r"^(?:/collections/[a-z0-9][a-z0-9_-]{0,120})?/products/([a-z0-9][a-z0-9_-]{0,240})(?:\.json)?/?$")


class ShopImportError(WardrobeError):
    """Import failure with an HTTP status and a Vietnamese message (status 422 = bad input)."""

    def __init__(self, message: str, status: int = 422, code: str = "invalid_url") -> None:
        super().__init__(message)
        self.status = status
        self.code = code


@dataclass(frozen=True)
class ProductRef:
    host: str
    handle: str
    variant_id: str | None

    @property
    def product_url(self) -> str:
        return f"https://{self.host}/products/{self.handle}"

    @property
    def json_url(self) -> str:
        return f"{self.product_url}.json"


def import_hosts() -> frozenset[str]:
    raw = os.getenv("SHADOW_IMPORT_HOSTS")
    raw = DEFAULT_IMPORT_HOSTS if raw is None or not raw.strip() else raw
    hosts = {h.strip().lower().rstrip(".") for h in raw.split(",")}
    return frozenset(h for h in hosts if _HOST_RE.match(h))


def _split_https(url: str) -> Any:
    if not isinstance(url, str):
        raise ShopImportError("Link sản phẩm không hợp lệ.")
    url = url.strip()
    if not url or len(url) > MAX_URL_LEN or any(ord(ch) < 33 for ch in url) or "\\" in url:
        raise ShopImportError("Link sản phẩm không hợp lệ.")
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        raise ShopImportError("Link sản phẩm không hợp lệ.") from None
    if parts.scheme.lower() != "https":
        raise ShopImportError("Chỉ nhận link https://.")
    if parts.username or parts.password or "@" in parts.netloc:
        raise ShopImportError("Link không được chứa tên đăng nhập/mật khẩu.")
    if port not in (None, 443):
        raise ShopImportError("Link không được chỉ định cổng riêng.")
    host = (parts.hostname or "").lower().rstrip(".")
    if not _HOST_RE.match(host):
        raise ShopImportError("Tên miền không hợp lệ (không nhận địa chỉ IP).")
    return parts, host


def validate_product_url(url: Any) -> ProductRef:
    """Strict check of a user-supplied product link → ProductRef (raises ShopImportError)."""
    parts, host = _split_https(url)
    allowed = import_hosts()
    if host not in allowed:
        raise ShopImportError(
            f"Chưa hỗ trợ cửa hàng “{host}”. Cửa hàng được phép: {', '.join(sorted(allowed)) or '(chưa cấu hình)'}.",
            code="host_not_allowed",
        )
    match = _PRODUCT_PATH_RE.match(parts.path.lower())
    if not match:
        raise ShopImportError("Link phải là trang sản phẩm dạng https://<shop>/products/<tên-sản-phẩm>.",
                              code="not_a_product_url")
    variant = (parse_qs(parts.query).get("variant") or [None])[0]
    variant = variant if variant and variant.isdigit() and len(variant) <= 20 else None
    return ProductRef(host=host, handle=match.group(1), variant_id=variant)


def _is_public_ip(value: str) -> bool:
    try:
        ip = ipaddress.ip_address(value.split("%")[0])
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return bool(ip.is_global) and not (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast
                                       or ip.is_reserved or ip.is_unspecified)


async def resolve_public(host: str) -> list[str]:
    """All addresses of `host`; refuses if any of them is not a public address."""
    try:
        infos = await asyncio.wait_for(
            asyncio.to_thread(socket.getaddrinfo, host, 443, 0, socket.SOCK_STREAM), timeout=5.0)
    except (OSError, asyncio.TimeoutError):
        raise ShopImportError(f"Không tìm thấy máy chủ “{host}”.", status=502, code="dns_failed") from None
    ips = list(dict.fromkeys(info[4][0] for info in infos))
    if not ips or not all(_is_public_ip(ip) for ip in ips):
        raise ShopImportError("Địa chỉ máy chủ không được phép.", code="blocked_address")
    return ips


Fetcher = Callable[[str, int, str], Awaitable[tuple[int, bytes, str | None]]]


async def _curl_fetch(url: str, host: str, ip: str, max_bytes: int, accept: str) -> tuple[int, bytes, str | None]:
    resolve = f"{host}:443:{'[' + ip + ']' if ':' in ip else ip}"
    with tempfile.TemporaryDirectory(prefix="shadow-import-") as tmp:
        body = os.path.join(tmp, "body")
        cmd = ["curl", "-sS", "--proto", "=https", "--proto-redir", "=https", "--max-redirs", "0",
               "--max-time", str(int(REQUEST_TIMEOUT_S)), "--connect-timeout", "5",
               "--max-filesize", str(max_bytes), "--resolve", resolve, "-A", USER_AGENT,
               "-H", f"Accept: {accept}", "-o", body, "-w", "%{http_code} %{redirect_url}", "--", url]

        def run() -> subprocess.CompletedProcess:
            return subprocess.run(cmd, capture_output=True, timeout=REQUEST_TIMEOUT_S + 3, check=False)

        try:
            proc = await asyncio.to_thread(run)
        except subprocess.TimeoutExpired:
            raise ShopImportError("Cửa hàng phản hồi quá lâu.", status=504, code="timeout") from None
        if proc.returncode == 63:
            raise ShopImportError("Dữ liệu từ cửa hàng quá lớn.", status=502, code="too_large")
        if proc.returncode == 28:
            raise ShopImportError("Cửa hàng phản hồi quá lâu.", status=504, code="timeout")
        if proc.returncode != 0:
            raise ShopImportError("Không kết nối được cửa hàng.", status=502, code="fetch_failed")
        status_text, _, location = proc.stdout.decode("ascii", "replace").partition(" ")
        data = b""
        if os.path.exists(body):
            if os.path.getsize(body) > max_bytes:
                raise ShopImportError("Dữ liệu từ cửa hàng quá lớn.", status=502, code="too_large")
            with open(body, "rb") as handle:
                data = handle.read()
    try:
        status = int(status_text)
    except ValueError:
        status = 0
    return status, data, location.strip() or None


async def _httpx_fetch(url: str, host: str, ip: str, max_bytes: int, accept: str) -> tuple[int, bytes, str | None]:
    import httpx

    parts = urlsplit(url)
    target = f"https://{'[' + ip + ']' if ':' in ip else ip}{parts.path or '/'}" + (f"?{parts.query}" if parts.query else "")
    headers = {"Host": host, "User-Agent": USER_AGENT, "Accept": accept}
    timeout = httpx.Timeout(REQUEST_TIMEOUT_S, connect=5.0)
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False, trust_env=False) as client:
            async with client.stream("GET", target, headers=headers, extensions={"sni_hostname": host}) as response:
                length = response.headers.get("content-length")
                if length and length.isdigit() and int(length) > max_bytes:
                    raise ShopImportError("Dữ liệu từ cửa hàng quá lớn.", status=502, code="too_large")
                chunks, size = [], 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > max_bytes:
                        raise ShopImportError("Dữ liệu từ cửa hàng quá lớn.", status=502, code="too_large")
                    chunks.append(chunk)
                location = response.headers.get("location")
                return response.status_code, b"".join(chunks), urljoin(url, location) if location else None
    except httpx.TimeoutException:
        raise ShopImportError("Cửa hàng phản hồi quá lâu.", status=504, code="timeout") from None
    except httpx.HTTPError:
        raise ShopImportError("Không kết nối được cửa hàng.", status=502, code="fetch_failed") from None


async def safe_fetch(url: str, *, allowed_hosts: frozenset[str], max_bytes: int, accept: str) -> bytes:
    """GET an allowlisted https URL with public-IP pinning and manually re-validated redirects."""
    for _ in range(MAX_REDIRECTS + 1):
        _parts, host = _split_https(url)
        if host not in allowed_hosts:
            raise ShopImportError("Cửa hàng chuyển hướng tới địa chỉ không được phép.", code="host_not_allowed")
        ips = await resolve_public(host)
        fetch = _curl_fetch if shutil.which("curl") else _httpx_fetch
        status, data, location = await fetch(url, host, ips[0], max_bytes, accept)
        if status in (301, 302, 303, 307, 308) and location:
            url = urljoin(url, location)
            continue
        if status == 404:
            raise ShopImportError("Không tìm thấy sản phẩm (link sai hoặc đã gỡ).", status=404, code="not_found")
        if status == 429:
            raise ShopImportError("Cửa hàng đang giới hạn truy cập — thử lại sau ít phút.", status=503,
                                  code="rate_limited_by_shop")
        if status != 200:
            raise ShopImportError(f"Cửa hàng trả lỗi {status}.", status=502, code="fetch_failed")
        return data
    raise ShopImportError("Cửa hàng chuyển hướng quá nhiều lần.", status=502, code="too_many_redirects")


# ---- product parsing -----------------------------------------------------------------------------------------


def _image_url(src: Any, ref: ProductRef) -> str | None:
    if not isinstance(src, str) or not src:
        return None
    if src.startswith("//"):
        src = "https:" + src
    try:
        parts, host = _split_https(src)
    except ShopImportError:
        return None
    if host not in IMAGE_CDN_HOSTS and host not in import_hosts():
        return None
    return src


def _with_width(src: str, width: int) -> str:
    if "cdn.shopify.com" in src or "/cdn/shop/" in src:
        return src + ("&" if "?" in src else "?") + f"width={width}"
    return src


def parse_product(data: bytes, ref: ProductRef) -> dict[str, Any]:
    try:
        payload = json.loads(data.decode("utf-8"))
        product = payload["product"]
        title = str(product["title"]).strip()
    except (ValueError, KeyError, TypeError, UnicodeDecodeError):
        raise ShopImportError("Trang này không phải sản phẩm Shopify (không đọc được dữ liệu).", status=422,
                              code="not_shopify") from None
    variants = [v for v in product.get("variants") or [] if isinstance(v, dict)]
    variant = None
    if ref.variant_id:
        variant = next((v for v in variants if str(v.get("id")) == ref.variant_id), None)
    variant = variant or next((v for v in variants if v.get("available")), None) or (variants[0] if variants else {})
    images: list[str] = []
    featured = (variant.get("featured_image") or {}).get("src") if isinstance(variant.get("featured_image"), dict) else None
    main = (product.get("image") or {}).get("src") if isinstance(product.get("image"), dict) else None
    for src in [featured, main, *[(img or {}).get("src") for img in product.get("images") or [] if isinstance(img, dict)]]:
        url = _image_url(src, ref)
        if url and url not in images:
            images.append(url)
    if not images:
        raise ShopImportError("Sản phẩm không có ảnh dùng được.", status=422, code="no_image")
    try:
        price = int(float(variant.get("price") or 0))
    except (TypeError, ValueError):
        price = 0
    sku = re.sub(r"[^A-Za-z0-9_-]", "", str(variant.get("sku") or ""))[:40]
    tags = product.get("tags")
    tags = [str(t) for t in tags][:20] if isinstance(tags, list) else [t.strip() for t in str(tags or "").split(",")][:20]
    return {
        "title": title[:200],
        "product_type": str(product.get("product_type") or "")[:80],
        "vendor": str(product.get("vendor") or "")[:40],
        "tags": tags,
        "price": price,
        "sku": sku,
        "images": images[:8],
    }


def guess_category(info: dict[str, Any]) -> str | None:
    """Category from the shop's product type / title ("Non Branded" is a YaMe line name, not a hat)."""
    for text in (info.get("product_type"), info.get("title")):
        cleaned = re.sub(r"\bnon branded\b", " ", fold_vi(text))
        category = normalize_category(cleaned)
        if category:
            return category
    return None


def flatness(jpeg: bytes) -> float:
    """How much a photo looks like a flat product shot: bright, uniform border (higher = better)."""
    try:
        from PIL import Image

        with Image.open(io.BytesIO(jpeg)) as image:
            image = image.convert("L")
            image.thumbnail((64, 64))
            w, h = image.size
            border = [image.getpixel((x, 0)) for x in range(w)] + [image.getpixel((x, h - 1)) for x in range(w)]
            border += [image.getpixel((0, y)) for y in range(h)] + [image.getpixel((w - 1, y)) for y in range(h)]
    except Exception:
        return -1e9
    mean = sum(border) / len(border)
    std = (sum((b - mean) ** 2 for b in border) / len(border)) ** 0.5
    return mean - 2.0 * std


async def _pick_image(images: list[str], fetch_image: Callable[[str], Awaitable[bytes]]) -> tuple[str, bytes]:
    best: tuple[float, str, bytes] | None = None
    errors = 0
    for index, src in enumerate(images[:MAX_IMAGE_CANDIDATES]):
        try:
            raw = await fetch_image(_with_width(src, 1024))
            jpeg = await asyncio.to_thread(to_jpeg, raw)
        except (ShopImportError, WardrobeError):
            errors += 1
            continue
        score = flatness(jpeg) - index * 3.0  # prefer the shop's own order when similar
        if best is None or score > best[0]:
            best = (score, src, jpeg)
        if score >= 225:  # clean light backdrop: good enough, stop downloading
            break
    if best is None:
        raise ShopImportError("Không tải được ảnh sản phẩm.", status=502, code="image_failed")
    return best[1], best[2]


async def import_product(
    provider: Any,
    store: WardrobeStore,
    url: Any,
    *,
    fetch_json: Callable[[ProductRef], Awaitable[bytes]] | None = None,
    fetch_image: Callable[[str], Awaitable[bytes]] | None = None,
) -> dict[str, Any]:
    """→ {"item", "duplicate", "product", "tag_source"}. Raises ShopImportError. Fetchers are injectable (tests)."""
    from garment_attrs import tag_item_with_attrs

    ref = validate_product_url(url)
    for existing in store.list_items():
        if existing.get("product_url") == ref.product_url:
            return {"item": existing, "duplicate": True, "product": {"url": ref.product_url}, "tag_source": None}

    async def default_json(r: ProductRef) -> bytes:
        return await safe_fetch(r.json_url, allowed_hosts=import_hosts(), max_bytes=MAX_JSON_BYTES,
                                accept="application/json")

    async def default_image(src: str) -> bytes:
        return await safe_fetch(src, allowed_hosts=IMAGE_CDN_HOSTS | import_hosts(), max_bytes=MAX_IMAGE_BYTES,
                                accept="image/avif,image/webp,image/jpeg,image/png,*/*;q=0.5")

    async def run() -> dict[str, Any]:
        info = parse_product(await (fetch_json or default_json)(ref), ref)
        image_src, jpeg = await _pick_image(info["images"], fetch_image or default_image)
        category = guess_category(info)
        fields, source = await tag_item_with_attrs(
            provider, jpeg, category=category,
            extra_text={"product_name": info["title"], "product_type": info["product_type"]},
        )
        extra: dict[str, Any] = {
            "tag_source": source,
            "import_source": "shop_link",
            "brand": BRANDS.get(ref.host) or info["vendor"] or ref.host,
            "product_name": info["title"],
            "product_url": ref.product_url,
            "image_source_url": image_src[:500],
        }
        if info["sku"]:
            extra["product_code"] = info["sku"][:-3] if ref.host in BRANDS and len(info["sku"]) > 7 else info["sku"]
        if 1000 <= info["price"] < 1_000_000_000:
            extra["price_vnd"] = info["price"]
        item = store.create_item(fields, jpeg, extra=extra)
        product = {"url": ref.product_url, "host": ref.host, "title": info["title"],
                   "product_type": info["product_type"], "price_vnd": extra.get("price_vnd"),
                   "image_url": image_src, "category_hint": category}
        return {"item": item, "duplicate": False, "product": product, "tag_source": source}

    try:
        return await asyncio.wait_for(run(), timeout=IMPORT_TIMEOUT_S + 30.0)  # + tagger time
    except asyncio.TimeoutError:
        raise ShopImportError("Nhập sản phẩm mất quá lâu — thử lại nhé.", status=504, code="timeout") from None
