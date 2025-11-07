"""Content-Type analysis and categorization."""

from __future__ import annotations

# Content-Type mapping for fast categorization
CONTENT_TYPE_CATEGORIES = {
    'json': [
        'application/json',
        'application/ld+json',
        'application/vnd.api+json',
        'application/hal+json',
        'application/problem+json',
    ],
    'image': [
        'image/',
    ],
    'media': [
        'video/',
        'audio/',
    ],
    'websocket': [
        'application/websocket',
    ],
    'html': [
        'text/html',
        'application/xhtml+xml',
    ],
    'css': [
        'text/css',
    ],
    'javascript': [
        'application/javascript',
        'application/x-javascript',
        'text/javascript',
        'application/ecmascript',
    ],
    'font': [
        'font/',
        'application/font-woff',
        'application/font-woff2',
        'application/vnd.ms-fontobject',
    ],
    'xml': [
        'application/xml',
        'text/xml',
    ],
    'pdf': [
        'application/pdf',
    ],
    'binary': [
        'application/octet-stream',
    ],
}


def categorize_content_type(content_type: str | None) -> str:
    """Categorize a Content-Type into predefined categories.

    Args:
        content_type: Full Content-Type header value (e.g., "application/json; charset=utf-8")

    Returns:
        Category name: 'json', 'image', 'media', 'websocket', 'html', 'css',
                      'javascript', 'font', 'xml', 'pdf', 'binary', or 'other'

    Examples:
        >>> categorize_content_type("application/json; charset=utf-8")
        'json'
        >>> categorize_content_type("image/png")
        'image'
        >>> categorize_content_type("video/mp4")
        'media'
    """
    if not content_type:
        return 'other'

    # Extract MIME type (before semicolon)
    mime = content_type.split(';')[0].strip().lower()

    # Check each category
    for category, patterns in CONTENT_TYPE_CATEGORIES.items():
        for pattern in patterns:
            if mime.startswith(pattern):
                return category

    return 'other'


def parse_content_type(content_type: str | None) -> tuple[str, dict[str, str]]:
    """Parse Content-Type header into MIME type and parameters.

    Args:
        content_type: Full Content-Type header value

    Returns:
        Tuple of (mime_type, parameters_dict)

    Examples:
        >>> parse_content_type("application/json; charset=utf-8")
        ('application/json', {'charset': 'utf-8'})
        >>> parse_content_type("text/html")
        ('text/html', {})
    """
    if not content_type:
        return '', {}

    parts = [p.strip() for p in content_type.split(';')]
    mime = parts[0].lower()

    params = {}
    for part in parts[1:]:
        if '=' in part:
            key, value = part.split('=', 1)
            params[key.strip().lower()] = value.strip().strip('"')

    return mime, params
