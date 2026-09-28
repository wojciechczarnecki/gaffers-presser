import importlib.metadata


def test_twscrape_pinned_version():
    assert importlib.metadata.version("twscrape") == "0.20.1"
