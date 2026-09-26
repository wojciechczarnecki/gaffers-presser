import app


def test_app_package_imports():
    assert app.__name__ == "app"
