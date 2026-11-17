import repro_agent


def test_package_is_importable() -> None:
    assert repro_agent.__doc__ is not None
