"""Seed-integrity rules: no fabricated resource metadata may enter the catalogue.

This is the test that makes the hard rule enforceable. It asserts that every entry
has a unique ``https`` URL on a reviewed domain, that the trust level matches what
the domain can legitimately carry, that the vocabulary is the published one, and
that no ``(title, publisher)`` pair is duplicated. Every failure message names the
offending entry, because "the catalogue is wrong" is not actionable.

The domain categorisation is written out here rather than derived from the module,
so that changing a domain's trust level requires changing the test too: the
categorisation is a review decision, not an implementation detail.
"""

from __future__ import annotations

from datetime import UTC, datetime
from urllib.parse import urlparse

import pytest

from coursellm.recommend.schemas import (
    MAX_DIFFICULTY,
    MIN_DIFFICULTY,
    ResourceType,
    SourceTrust,
)
from coursellm.recommend.seed import (
    CONCEPT_SLUGS,
    MIN_RESOURCE_YEAR,
    SEED_CATALOGUE,
    SOURCE_TRUST_BY_DOMAIN,
    domain_for_host,
    host_of,
    trust_for_url,
)
from coursellm.tools.web import DEFAULT_ALLOWED_DOMAINS, effective_domains, is_allowlisted

pytestmark = pytest.mark.unit

#: A project's or institution's own documentation domain. ``official`` is only
#: ever used for these.
OFFICIAL_DOMAINS = frozenset(
    {
        "developer.mozilla.org",
        "docker.com",
        "git-scm.com",
        "numpy.org",
        "postgresql.org",
        "pydata.org",
        "python.org",
        "pytorch.org",
        "redis.io",
        "rfc-editor.org",
        "scikit-learn.org",
    }
)

#: Universities, publishers and preprint servers. ``academic`` is only ever used
#: for these.
ACADEMIC_DOMAINS = frozenset(
    {
        "arxiv.org",
        "cs.cmu.edu",
        "cs186berkeley.net",
        "cs61a.org",
        "manning.com",
        "mit.edu",
        "mlr.press",
        "openstax.org",
        "springer.com",
        "stanford.edu",
        "usenix.org",
    }
)

#: Platforms and free community material.
COMMUNITY_DOMAINS = frozenset({"coursera.org", "freecodecamp.org", "khanacademy.org"})

#: A minimum catalogue size, so an accidental truncation of the tuple fails.
MIN_CATALOGUE_SIZE = 30


def test_catalogue_is_a_meaningful_size() -> None:
    assert len(SEED_CATALOGUE) >= MIN_CATALOGUE_SIZE, (
        f"the catalogue has only {len(SEED_CATALOGUE)} entries; the brief asks for "
        f"at least {MIN_CATALOGUE_SIZE} authoritative resources"
    )


def test_urls_are_unique() -> None:
    seen: dict[str, str] = {}
    for entry in SEED_CATALOGUE:
        assert entry.url not in seen, (
            f"duplicate URL {entry.url!r}: {seen.get(entry.url)!r} and {entry.title!r}"
        )
        seen[entry.url] = entry.title


@pytest.mark.parametrize("entry", SEED_CATALOGUE, ids=lambda entry: entry.title)
def test_url_is_wellformed_https(entry) -> None:
    parsed = urlparse(entry.url)
    assert parsed.scheme == "https", f"{entry.title!r} must use https, got {entry.url!r}"
    assert parsed.hostname, f"{entry.title!r} has no host in {entry.url!r}"
    assert " " not in entry.url, f"{entry.title!r} has whitespace in its URL"


@pytest.mark.parametrize("entry", SEED_CATALOGUE, ids=lambda entry: entry.title)
def test_host_is_on_the_allowlist(entry) -> None:
    host = host_of(entry.url)
    domain = domain_for_host(host)
    assert domain is not None, (
        f"{entry.title!r} uses host {host!r}, which is not on SOURCE_TRUST_BY_DOMAIN. "
        f"Add the domain with a deliberate trust level, or omit the resource."
    )


@pytest.mark.parametrize("entry", SEED_CATALOGUE, ids=lambda entry: entry.title)
def test_trust_is_consistent_with_the_host(entry) -> None:
    expected = trust_for_url(entry.url)
    assert expected is not None, f"{entry.title!r} has an allowlisted host but no trust"
    assert entry.trust is expected, (
        f"{entry.title!r} declares trust {entry.trust.value!r} but its host allows "
        f"only {expected.value!r}"
    )


def test_no_duplicate_title_and_publisher() -> None:
    seen: dict[tuple[str, str | None], str] = {}
    for entry in SEED_CATALOGUE:
        key = (entry.title, entry.publisher)
        assert key not in seen, (
            f"duplicate (title, publisher) {key!r}: {seen[key]!r} and {entry.url!r}"
        )
        seen[key] = entry.url


@pytest.mark.parametrize("entry", SEED_CATALOGUE, ids=lambda entry: entry.title)
def test_every_entry_has_a_non_empty_description(entry) -> None:
    assert entry.description.strip(), f"{entry.title!r} has an empty description"


@pytest.mark.parametrize("entry", SEED_CATALOGUE, ids=lambda entry: entry.title)
def test_every_concept_slug_is_published(entry) -> None:
    published = set(CONCEPT_SLUGS)
    unknown = sorted(set(entry.concept_slugs) - published)
    assert not unknown, (
        f"{entry.title!r} uses concept slugs that are not in CONCEPT_SLUGS: {unknown}"
    )
    assert entry.concept_slugs, f"{entry.title!r} covers no concepts"


@pytest.mark.parametrize("entry", SEED_CATALOGUE, ids=lambda entry: entry.title)
def test_year_is_within_a_sensible_range(entry) -> None:
    if entry.year is None:
        return
    latest = datetime.now(UTC).year + 1
    assert MIN_RESOURCE_YEAR <= entry.year <= latest, (
        f"{entry.title!r} has implausible year {entry.year}"
    )


@pytest.mark.parametrize("entry", SEED_CATALOGUE, ids=lambda entry: entry.title)
def test_difficulty_and_type_are_known(entry) -> None:
    assert MIN_DIFFICULTY <= entry.difficulty <= MAX_DIFFICULTY, (
        f"{entry.title!r} has difficulty {entry.difficulty} outside 1-5"
    )
    assert entry.resource_type in set(ResourceType), (
        f"{entry.title!r} has unknown resource type {entry.resource_type!r}"
    )
    assert entry.publisher, f"{entry.title!r} has no publisher or institution"


def test_trust_mapping_is_exactly_the_reviewed_categorisation() -> None:
    """The allowlist and the trust decision are one reviewed mapping."""
    expected = {
        **dict.fromkeys(OFFICIAL_DOMAINS, SourceTrust.OFFICIAL),
        **dict.fromkeys(ACADEMIC_DOMAINS, SourceTrust.ACADEMIC),
        **dict.fromkeys(COMMUNITY_DOMAINS, SourceTrust.COMMUNITY),
    }
    assert expected == SOURCE_TRUST_BY_DOMAIN, (
        "SOURCE_TRUST_BY_DOMAIN differs from the reviewed categorisation. official is "
        "only for a project's or institution's own documentation domain, academic only "
        "for universities, publishers and preprint servers, community only for "
        "platforms and free community material."
    )


def test_no_resource_is_seeded_as_secondary() -> None:
    """``secondary`` is for pages fetched at request time, which this PR does not do."""
    secondary = [entry.title for entry in SEED_CATALOGUE if entry.trust is SourceTrust.SECONDARY]
    assert not secondary, f"seeded resources must not claim secondary trust: {secondary}"


def test_paid_publisher_books_are_not_marked_free() -> None:
    """``is_free`` is metadata too; a paid book must not be advertised as free."""
    paid_domains = {"springer.com", "manning.com"}
    offenders = [
        entry.title
        for entry in SEED_CATALOGUE
        if domain_for_host(host_of(entry.url)) in paid_domains and entry.is_free
    ]
    assert not offenders, f"paid-publisher resources marked is_free: {offenders}"


def test_every_allowlist_domain_is_used() -> None:
    """An allowlisted domain with no resource is an unreviewed widening."""
    used = {domain_for_host(host_of(entry.url)) for entry in SEED_CATALOGUE}
    unused = sorted(set(SOURCE_TRUST_BY_DOMAIN) - used)
    assert not unused, f"allowlisted domains with no seeded resource: {unused}"


def test_catalogue_tables_are_global_and_not_tenant_scoped() -> None:
    """Both directions of the tenancy decision, asserted together.

    The catalogue is identical for every tenant, so it must be declared global and
    must not carry ``tenant_id``; and it must be absent from the RLS list, because
    a policy keyed on the tenant would hide the shared rows.
    """
    import coursellm.db.models  # noqa: F401 - registers the metadata
    from coursellm.db.base import GLOBAL_TABLES, TENANT_SCOPED_TABLES, Base

    catalogue_tables = {"resources", "resource_concepts"}
    assert catalogue_tables <= GLOBAL_TABLES, (
        f"the catalogue tables must be global: missing {sorted(catalogue_tables - GLOBAL_TABLES)}"
    )
    assert catalogue_tables.isdisjoint(TENANT_SCOPED_TABLES), (
        "the catalogue is not tenant data and must not be RLS-protected"
    )
    for name in sorted(catalogue_tables):
        table = Base.metadata.tables[name]
        assert "tenant_id" not in table.columns, f"{name} must not carry tenant_id"


# ---------------------------------------------------------------------------
# The allowlist is also the ``search_web_sources`` control. A caller may narrow
# it but must never be able to widen it.
# ---------------------------------------------------------------------------


def test_default_allowlist_is_the_curated_domain_set() -> None:
    assert set(DEFAULT_ALLOWED_DOMAINS) == set(SOURCE_TRUST_BY_DOMAIN)


def test_effective_domains_cannot_widen_the_allowlist() -> None:
    assert effective_domains(["arxiv.org", "evil.example"]) == frozenset({"arxiv.org"})
    assert effective_domains(["evil.example"]) == frozenset()


def test_effective_domains_defaults_to_the_whole_curated_set() -> None:
    assert effective_domains(None) == frozenset(DEFAULT_ALLOWED_DOMAINS)


def test_is_allowlisted_accepts_subdomains_and_rejects_unknown_hosts() -> None:
    assert is_allowlisted("https://docs.python.org/3/tutorial/")
    assert is_allowlisted("https://ocw.mit.edu/courses/18-06-linear-algebra-spring-2010/")
    assert not is_allowlisted("https://evil.example/lesson")
    assert not is_allowlisted("http://python.org.evil.example/")
