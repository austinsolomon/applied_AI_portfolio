"""The category file is validated on load, and host matching covers subdomains only."""

from pathlib import Path

import pytest
import yaml

from leadgen.category_config import (
    CategoryConfig,
    CategoryConfigError,
    is_on_host,
    load_category_config,
)

DEMO_CATEGORY_PATH = Path(__file__).resolve().parents[1] / "config" / "category.yaml"


@pytest.fixture
def demo_category_document() -> dict:
    return yaml.safe_load(DEMO_CATEGORY_PATH.read_text(encoding="utf-8"))


def write_category(tmp_path: Path, document: dict) -> Path:
    path = tmp_path / "category.yaml"
    path.write_text(yaml.safe_dump(document), encoding="utf-8")
    return path


def test_the_demo_category_loads(monkeypatch: pytest.MonkeyPatch) -> None:
    # The default path is relative to the working directory, as the quickstart runs it.
    monkeypatch.chdir(DEMO_CATEGORY_PATH.parents[1])

    category = load_category_config()

    assert category.name == "mobile_massage"
    assert category.keywords[0] == "mobile massage"
    assert category.expected_qualification_rate == 0.40
    assert "instagram.com" in category.shared_hosts
    assert "yelp.com" in category.aggregator_hosts
    assert "in-home massage" in category.rubric.eligible_services
    assert category.hosts_never_identifying_a_business >= {"instagram.com", "yelp.com"}


def test_an_explicit_path_wins_over_the_setting(tmp_path: Path, demo_category_document) -> None:
    demo_category_document["name"] = "copied_category"
    path = write_category(tmp_path, demo_category_document)

    assert load_category_config(path).name == "copied_category"


@pytest.mark.parametrize(
    ("field", "bad_value", "complaint"),
    [
        ("keywords", [], "keywords"),
        ("keywords", ["mobile massage", "mobile massage"], "duplicate"),
        ("keywords", ["mobile massage", "  "], "blank"),
        ("expected_qualification_rate", 0, "expected_qualification_rate"),
        ("expected_qualification_rate", 1.5, "expected_qualification_rate"),
        ("shared_hosts", ["https://instagram.com"], "shared_hosts"),
        ("shared_hosts", ["www.instagram.com/"], "shared_hosts"),
        ("aggregator_hosts", ["Yelp.com"], "aggregator_hosts"),
        ("name", "Mobile Massage", "name"),
        ("rubric", {"qualified_when": "x"}, "rubric"),
    ],
)
def test_an_invalid_category_names_the_field(
    tmp_path: Path, demo_category_document: dict, field: str, bad_value: object, complaint: str
) -> None:
    demo_category_document[field] = bad_value
    path = write_category(tmp_path, demo_category_document)

    with pytest.raises(CategoryConfigError, match=complaint):
        load_category_config(path)


def test_an_unknown_field_is_refused(tmp_path: Path, demo_category_document: dict) -> None:
    # A misspelt key would otherwise be silently ignored and the intended value never read.
    demo_category_document["qual_rate"] = 0.4
    path = write_category(tmp_path, demo_category_document)

    with pytest.raises(CategoryConfigError, match="qual_rate"):
        load_category_config(path)


def test_a_missing_file_is_a_plain_error(tmp_path: Path) -> None:
    with pytest.raises(CategoryConfigError, match="cannot read"):
        load_category_config(tmp_path / "absent.yaml")


def test_a_non_mapping_file_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "category.yaml"
    path.write_text("- just\n- a list\n", encoding="utf-8")

    with pytest.raises(CategoryConfigError, match="mapping"):
        load_category_config(path)


def test_the_config_is_immutable(demo_category_document: dict) -> None:
    category = CategoryConfig.model_validate(demo_category_document)

    with pytest.raises(ValueError):
        category.name = "other"  # type: ignore[misc]


@pytest.mark.parametrize(
    ("domain", "expected"),
    [
        ("instagram.com", True),
        ("m.facebook.com", True),
        ("l.instagram.com", True),
        ("Instagram.com", True),
        ("notinstagram.com", False),
        ("instagram.com.example", False),
        ("cedarmassage.example", False),
        ("com", False),
    ],
)
def test_a_host_covers_itself_and_its_subdomains_only(domain: str, expected: bool) -> None:
    assert is_on_host(domain, {"instagram.com", "facebook.com"}) is expected
