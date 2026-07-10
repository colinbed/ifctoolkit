import re
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
PUBLIC_TEMPLATE_DIR = ROOT / "templates" / "public"
LIVE_TOOL_LINKS = [
    "/app",
    "/excel",
    "/cleaner",
    "/storeys",
    "/proxy",
    "/tools/cobieqc",
    "/tools/cobie-qa",
    "/ifc-qa/extractor",
    "/tools/reduce-file-size",
    "/tools/purge-area-spaces",
]


def _public_template_text() -> str:
    return "\n".join(path.read_text() for path in PUBLIC_TEMPLATE_DIR.glob("*.html"))


def test_public_get_started_ctas_use_app_route():
    public_templates = _public_template_text()

    assert 'href="/app">Get Started</a>' in public_templates
    assert "primary_href='/app'" in (PUBLIC_TEMPLATE_DIR / "macros.html").read_text()
    assert "{{ ui.hero(page, 'View tools', 'Get Started', '/app', '/tools') }}" in (PUBLIC_TEMPLATE_DIR / "home.html").read_text()


def test_public_ctas_do_not_use_legacy_static_index():
    assert "/static/index.html" not in _public_template_text()


def test_public_tools_overview_links_to_live_tool_routes():
    tools_template = (PUBLIC_TEMPLATE_DIR / "tools.html").read_text()

    for route in LIVE_TOOL_LINKS:
        assert f'href="{route}"' in tools_template
    assert tools_template.count("Open tool →") >= len(LIVE_TOOL_LINKS)


def test_public_nav_resets_desktop_flex_direction_to_row():
    css = (ROOT / "static" / "style.css").read_text()
    public_nav_match = re.search(r"\.public-nav\s*\{(?P<body>[^}]*)\}", css)

    assert public_nav_match is not None
    assert re.search(r"flex-direction\s*:\s*row\s*;", public_nav_match.group("body"))
