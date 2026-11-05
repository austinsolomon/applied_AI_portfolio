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
MASSAGE_CATEGORY_PATH = Path(__file__).parent / "fixtures" / "mobile_massage_category.yaml"


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

    assert category.name == "private_chef"
    assert category.keywords[0] == "private chef"
    assert category.expected_qualification_rate == 0.30
    assert "instagram.com" in category.shared_hosts
    assert "yelp.com" in category.aggregator_hosts
    assert "private chef" in category.rubric.eligible_services
    assert "restaurant" in category.rubric.rejected_when
    assert category.hosts_never_identifying_a_business >= {"instagram.com", "yelp.com"}


def test_an_explicit_path_wins_over_the_setting(tmp_path: Path, demo_category_document) -> None:
    demo_category_document["name"] = "copied_category"
    path = write_category(tmp_path, demo_category_document)

    assert load_category_config(path).name == "copied_category"


@pytest.mark.parametrize(
    ("field", "bad_value", "complaint"),
    [
        ("keywords", [], "keywords"),
        ("keywords", ["private chef", "private chef"], "duplicate"),
        ("keywords", ["private chef", "  "], "blank"),
        ("expected_qualification_rate", 0, "expected_qualification_rate"),
        ("expected_qualification_rate", 1.5, "expected_qualification_rate"),
        ("shared_hosts", ["https://instagram.com"], "shared_hosts"),
        ("shared_hosts", ["www.instagram.com/"], "shared_hosts"),
        ("aggregator_hosts", ["Yelp.com"], "aggregator_hosts"),
        ("name", "Private Chef", "name"),
        ("rubric", {"qualified_when": "x"}, "rubric"),
        ("exa_queries", ["private chef therapists"], "city"),
        ("exa_queries", [], "exa_queries"),
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
        ("cedartable.example", False),
        ("com", False),
    ],
)
def test_a_host_covers_itself_and_its_subdomains_only(domain: str, expected: bool) -> None:
    assert is_on_host(domain, {"instagram.com", "facebook.com"}) is expected


@pytest.mark.parametrize(
    "domain",
    [
        "linkedin.com",
        "arelaxingtouchmassage.glossgenius.com",
        "www.qdexx.com",
        "excellenttherapists.com",
        "carenary.com",
        "www.stedi.com",
    ],
)
def test_platforms_seen_in_live_exa_results_never_count_as_a_business(domain):
    # Seen in the 2026-11-04 massage run; the massage category (a test fixture) excludes them.
    category = load_category_config(MASSAGE_CATEGORY_PATH)
    assert is_on_host(domain, category.hosts_never_identifying_a_business)


@pytest.mark.parametrize(
    "domain", ["takeachef.com", "www.cozymeal.com", "opentable.com", "www.thumbtack.com"]
)
def test_chef_marketplaces_and_restaurant_platforms_never_count_as_a_business(domain):
    assert is_on_host(
        domain, load_category_config(DEMO_CATEGORY_PATH).hosts_never_identifying_a_business
    )


@pytest.mark.parametrize(
    ("maps_category", "counts"),
    [
        ("Personal chef service", True),
        ("Chef", True),
        ("Restaurant", False),
        ("Chef's restaurant", False),
        ("Cooking school", False),
        ("Caterer", False),
    ],
)
def test_maps_categories_count_only_independent_chef_listings(maps_category, counts):
    from leadgen.sourcing_maps import on_category

    assert on_category(maps_category, load_category_config(DEMO_CATEGORY_PATH)) is counts


@pytest.mark.parametrize(
    ("title", "matches"),
    [
        ("Saffron Table | Private chef in Phoenix", True),
        ("Culinary Nomad", True),
        ("Pho King", False),
    ],
)
def test_exa_results_need_a_category_signal_term(title, matches):
    from leadgen.sourcing_exa import category_signal

    assert category_signal(title, load_category_config(DEMO_CATEGORY_PATH)) is matches


def test_evidence_search_templates_must_name_their_placeholders(tmp_path, demo_category_document):
    demo_category_document["evidence_search"] = "{business} private chef"
    with pytest.raises(CategoryConfigError, match="evidence_search"):
        load_category_config(write_category(tmp_path, demo_category_document))
