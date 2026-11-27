"""The one category this deployment sources, read from `config/category.yaml`.

The file is part of the repository and changes by pull request. Every stage that needs category
knowledge reads it through `load_category_config`, so there is one definition of the category:
planner keywords, Exa queries and match terms, Maps grader terms, the judge rubric and
requirement, evidence searches, CRM disqualifiers, and the dedupe and sourcing host lists.
"""

from pathlib import Path
from typing import Annotated

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from leadgen.settings import get_settings

# A host as it appears in a normalized website domain: lowercase labels joined by dots, no
# scheme, no "www.", no path. The same shape `crm_leads.website_domain` holds.
HOST_PATTERN = r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$"
Host = Annotated[str, Field(pattern=HOST_PATTERN)]

# Failure-mode tags the eval review page offers when a judge decision fails, for a category file
# that names none of its own. Generic on purpose; each category lists the ways its judge goes wrong.
DEFAULT_EVAL_FAILURE_MODES: tuple[str, ...] = (
    "quote not on the page",
    "wrong market",
    "should have been review",
    "directory or aggregator",
    "not in the category",
    "missed a disqualifier",
)


class CategoryRubric(BaseModel):
    """What qualifies, what does not, and what needs a closer look."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    qualified_when: str = Field(min_length=1)
    rejected_when: str = Field(min_length=1)
    review_when: str = Field(min_length=1)
    eligible_services: list[str] = Field(min_length=1)


class CategoryConfig(BaseModel):
    """`config/category.yaml`, validated: an invalid file fails at load time, naming the field."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    label: str = Field(min_length=1)
    keywords: list[str] = Field(min_length=1)
    expected_qualification_rate: float = Field(gt=0, le=1)
    rubric: CategoryRubric
    shared_hosts: list[Host]
    aggregator_hosts: list[Host]
    # Plain-language Exa queries, one per phrasing; `{city}` is replaced by each planned city.
    exa_queries: list[str] = Field(min_length=1, max_length=5)
    # Words that mark an Exa result's title or domain as this category.
    signal_terms: list[str] = Field(min_length=1)
    # Maps sample grading: a category containing a term, and none of the excludes, is on-category.
    maps_category_terms: list[str] = Field(min_length=1)
    maps_category_excludes: list[str] = []
    # Evidence searches; `{business}` and `{market}` are filled in per lead.
    site_search: str = Field(min_length=1)
    evidence_search: str = Field(min_length=1)
    # What the judge must see before a lead can qualify; added to its instructions.
    judge_requirement: str = Field(min_length=1)
    # Phrases the CRM write flags when a qualified lead's reason affirms them.
    disqualifier_phrases: list[str] = Field(min_length=1)
    # Tags a reviewer picks from when a judge decision fails (eval review page): the ways this
    # category's judge goes wrong, counted across labels to find the most common failure.
    eval_failure_modes: list[str] = Field(default=list(DEFAULT_EVAL_FAILURE_MODES), min_length=1)

    @field_validator("exa_queries")
    @classmethod
    def queries_name_the_city(cls, queries: list[str]) -> list[str]:
        if any("{city}" not in query for query in queries):
            raise ValueError("every query must contain {city}")
        return queries

    @field_validator(
        "keywords",
        "shared_hosts",
        "aggregator_hosts",
        "exa_queries",
        "signal_terms",
        "maps_category_terms",
        "disqualifier_phrases",
        "eval_failure_modes",
    )
    @classmethod
    def entries_are_unique_and_non_blank(cls, entries: list[str]) -> list[str]:
        stripped = [entry.strip() for entry in entries]
        if any(entry == "" for entry in stripped):
            raise ValueError("entries must not be blank")
        duplicates = {entry for entry in stripped if stripped.count(entry) > 1}
        if duplicates:
            raise ValueError(f"duplicate entries: {sorted(duplicates)}")
        return stripped

    @field_validator("site_search")
    @classmethod
    def site_search_names_the_business(cls, template: str) -> str:
        if "{business}" not in template:
            raise ValueError("site_search must contain {business}")
        return template

    @field_validator("evidence_search")
    @classmethod
    def evidence_search_names_business_and_market(cls, template: str) -> str:
        if "{business}" not in template or "{market}" not in template:
            raise ValueError("evidence_search must contain {business} and {market}")
        return template

    @property
    def hosts_never_identifying_a_business(self) -> frozenset[str]:
        """Shared and aggregator hosts together: a domain on any of them names a platform, not
        the business, so it can never match a CRM lead or be sourced as a lead."""
        return frozenset(self.shared_hosts) | frozenset(self.aggregator_hosts)


class CategoryConfigError(Exception):
    """The category file is missing or does not describe a category."""


def load_category_config(path: Path | None = None) -> CategoryConfig:
    """Read and validate the category file; by default the one named by CATEGORY_CONFIG_PATH."""
    config_path = path if path is not None else get_settings().category_config_path
    try:
        raw_text = config_path.read_text(encoding="utf-8")
    except OSError as error:
        raise CategoryConfigError(f"cannot read {config_path}: {error.strerror}") from error
    try:
        document = yaml.safe_load(raw_text)
    except yaml.YAMLError as error:
        raise CategoryConfigError(f"{config_path} is not valid YAML: {error}") from error
    if not isinstance(document, dict):
        raise CategoryConfigError(f"{config_path} must hold a mapping of category fields")
    try:
        return CategoryConfig.model_validate(document)
    except ValueError as error:
        raise CategoryConfigError(f"{config_path} is not a valid category: {error}") from error


def is_on_host(domain: str, hosts: frozenset[str] | set[str]) -> bool:
    """True when `domain` is one of `hosts` or a subdomain of one, as m.facebook.com is."""
    labels = domain.lower().split(".")
    return any(".".join(labels[start:]) in hosts for start in range(len(labels) - 1))
