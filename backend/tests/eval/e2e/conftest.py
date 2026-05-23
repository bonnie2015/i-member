"""E2E tests: 通过 --category 参数选择用例集。"""

import pytest


def pytest_addoption(parser):
    parser.addoption(
        "--category",
        default="capability",
        help="case category: regression or capability",
    )


@pytest.fixture(scope="session")
def category(request):
    return request.config.getoption("--category")
