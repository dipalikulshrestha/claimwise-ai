"""
Scaffold smoke test — Task 1.6.

Verifies that the backend package structure is importable and the test
framework is operational. No AWS resources or business logic are exercised.
"""


def test_app_package_importable() -> None:
    """The top-level app package must be importable."""
    import app  # noqa: F401


def test_api_package_importable() -> None:
    """The api sub-package must be importable."""
    import app.api  # noqa: F401


def test_domain_package_importable() -> None:
    """The domain sub-package must be importable."""
    import app.domain  # noqa: F401


def test_services_package_importable() -> None:
    """The services sub-package must be importable."""
    import app.services  # noqa: F401


def test_infrastructure_package_importable() -> None:
    """The infrastructure sub-package must be importable."""
    import app.infrastructure  # noqa: F401


def test_pytest_is_working() -> None:
    """Trivial assertion — confirms pytest itself is running correctly."""
    assert 1 + 1 == 2
