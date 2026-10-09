import tomllib

from app.presser.render import TEMPLATE_PATH


def leaves(value):
    if isinstance(value, dict):
        for item in value.values():
            yield from leaves(item)
    else:
        yield value


def test_template_is_valid_toml_with_no_empty_leaf():
    template = tomllib.loads(TEMPLATE_PATH.read_text(encoding="utf-8"))
    assert {"title", "header", "whatsapp", "footer", "html"} <= set(template)
    found = list(leaves(template))
    assert found
    assert all(isinstance(leaf, str) and leaf.strip() for leaf in found)
