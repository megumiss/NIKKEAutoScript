"""Whitelisted web links proxied for embedding inside the SPA.

The SPA embeds external pages in an <iframe>.  Direct embedding is blocked by
X-Frame-Options / CSP frame-ancestors on most sites, so the backend fetches
the page server-side, strips the embedding-inhibiting headers, and returns
the HTML to the iframe (same-origin, no CORS involved).

Only hosts listed in _ALLOWED_HOSTS may be proxied (SSRF guard); the fixed
links are defined in PROXY_LINKS below.  Edit that list to add/remove entries.
"""

import os
import re
from urllib.parse import urlparse

import httpx
import yaml
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse

from module.logger import logger

# Links shown on the SPA "web" page live in proxy_links.yaml next to this
# file.  name is used as a lookup key for the frontend i18n table and falls
# back to the raw string when missing; see the yaml header for direct=.
_LINKS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'proxy_links.yaml')


def _load_links() -> list:
    """Read the link list from proxy_links.yaml, dropping invalid entries."""
    try:
        with open(_LINKS_FILE, encoding='utf-8') as f:
            data = yaml.safe_load(f)
    except (OSError, yaml.YAMLError) as e:
        logger.error(f'Failed to load proxy links: {e}')
        return []
    links = []
    for item in data or []:
        if not isinstance(item, dict) or not item.get('name') or not item.get('url'):
            logger.warning(f'Proxy link skipped, missing name/url: {item}')
            continue
        # direct 缺省按 False 处理，未知的站点一律走代理更安全。
        link = {
            'name': str(item['name']),
            'url': str(item['url']),
            'direct': bool(item.get('direct', False)),
        }
        i18n = item.get('i18n')
        if isinstance(i18n, dict):
            link['i18n'] = {str(lang): str(text) for lang, text in i18n.items()}
        links.append(link)
    return links


PROXY_LINKS = _load_links()

# Hosts (and their subdomains) allowed to be proxied, derived from the link
# list so the two cannot drift apart.  Hosts stay pinned to exactly what the
# urls use (e.g. nikke-db.pages.dev, not pages.dev).
_ALLOWED_HOSTS = tuple(
    host for host in (urlparse(link['url']).hostname for link in PROXY_LINKS) if host
)

# Response headers that would prevent embedding inside an iframe.
_STRIP_HEADERS = {
    'x-frame-options',
    'content-security-policy',
    'content-security-policy-report-only',
}

# Some sites reject default client user agents; mimic a common browser.
_USER_AGENT = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
    '(KHTML, like Gecko) Chrome/120.0 Safari/537.36'
)


def _json_error(message: str, status: int = 400):
    return JSONResponse({'status': 'error', 'message': message}, status_code=status)


def _is_allowed(url: str) -> bool:
    """Whitelist check: scheme http(s) and host within _ALLOWED_HOSTS."""
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    if parsed.scheme not in ('http', 'https') or not parsed.netloc:
        return False
    host = (parsed.hostname or '').lower()
    return any(host == allowed or host.endswith('.' + allowed) for allowed in _ALLOWED_HOSTS)


def _inject_base(body: bytes, url: str) -> bytes:
    """Insert <base href=url> so relative assets resolve against the origin.

    Without it, relative css/js/img paths inside the proxied HTML would be
    resolved against the SPA origin and 404.  The base tag makes the browser
    load them directly from the original site.
    """
    base_tag = f'<base href="{url}">'
    text = body.decode('utf-8', errors='replace')
    head_match = re.search(r'(?is)(<head[^>]*>)', text)
    if head_match:
        pos = head_match.end()
    else:
        html_match = re.search(r'(?is)(<html[^>]*>)', text)
        pos = html_match.end() if html_match else 0
    return text[:pos] + base_tag + text[pos:]


async def proxy_links(_: Request):
    """Return the fixed whitelisted links for the SPA web page."""
    return JSONResponse({'links': PROXY_LINKS})


async def proxy(request: Request):
    """Fetch an allowed page server-side and return it for iframe embedding."""
    url = request.query_params.get('url')
    if not url:
        return _json_error('Missing url parameter.')
    if not _is_allowed(url):
        logger.warning(f'Proxy blocked: {url}')
        return _json_error('URL is not in the whitelist.', 403)

    try:
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=15,
            headers={'User-Agent': _USER_AGENT},
        ) as client:
            resp = await client.get(url)
    except httpx.HTTPError as e:
        logger.warning(f'Proxy fetch failed {url}: {e}')
        return _json_error(f'Failed to fetch page: {e}', 502)

    content_type = resp.headers.get('content-type', '')
    if 'html' not in content_type:
        return _json_error('Only HTML pages can be embedded.', 415)

    body = _inject_base(resp.content, str(resp.url))
    response = HTMLResponse(body, status_code=resp.status_code)
    # Belt and suspenders: make sure no embedding-inhibiting header leaks in.
    for header in _STRIP_HEADERS:
        try:
            del response.headers[header]
        except KeyError:
            pass
    return response
