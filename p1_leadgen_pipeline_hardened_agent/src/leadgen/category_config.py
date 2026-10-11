"""The one category this deployment sources, read from `config/category.yaml`.

The file is part of the repository and changes by pull request. Every stage that needs category
knowledge (planner keywords, grader and judge rubric, dedupe and lookalike host lists) reads it
through `load_category_config`, so there is one definition of the category.
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

    @field_validator("keywords", "shared_hosts", "aggregator_hosts")
    @classmethod
    def entries_are_unique_and_non_blank(cls, entries: list[str]) -> list[str]:
        stripped = [entry.strip() for entry in entries]
        if any(entry == "" for entry in stripped):
            raise ValueError("entries must not be blank")
        duplicates = {entry for entry in stripped if stripped.count(entry) > 1}
        if duplicates:
            raise ValueError(f"duplicate entries: {sorted(duplicates)}")
        return stripped

    @property
    def hosts_never_identifying_a_business(self) -> frozenset[str]:
        """Shared and aggregator hosts together: a domain on any of them names a platform, not
        the business, so it can neither match a CRM lead nor seed a lookalike search."""
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
