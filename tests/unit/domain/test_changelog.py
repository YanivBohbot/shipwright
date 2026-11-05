"""CHANGELOG.md insertion under ## [Unreleased]."""

import pytest

from shipwright.domain.changelog import insert_changelog_entry


@pytest.mark.parametrize(
    ("before", "after"),
    [
        (None, "# Changelog\n\n## [Unreleased]\n\n- New\n"),
        ("", "# Changelog\n\n## [Unreleased]\n\n- New\n"),
        (
            "# Changelog\n\n## [Unreleased]\n\n- Old\n",
            "# Changelog\n\n## [Unreleased]\n\n- New\n- Old\n",
        ),
        (
            "# Changelog\n\n## [Unreleased]\n### Added\n- Old\n",
            "# Changelog\n\n## [Unreleased]\n\n- New\n\n### Added\n- Old\n",
        ),
        (
            "# Changelog\n\nAll notable changes.\n\n## [0.1.0]\n- Initial\n",
            "# Changelog\n\n## [Unreleased]\n\n- New\n\n"
            "All notable changes.\n\n## [0.1.0]\n- Initial\n",
        ),
        ("## [0.1.0]\n- Initial\n", "## [Unreleased]\n\n- New\n\n## [0.1.0]\n- Initial\n"),
        (
            "# Changelog\r\n\r\n## [unreleased]\r\n\r\n- Old\r\n",
            "# Changelog\r\n\r\n## [unreleased]\r\n\r\n- New\r\n- Old\r\n",
        ),
        (  # already present: unchanged (re-publication)
            "# Changelog\n\n## [Unreleased]\n\n- New\n",
            "# Changelog\n\n## [Unreleased]\n\n- New\n",
        ),
    ],
)
def test_insert_changelog_entry(before, after):
    assert insert_changelog_entry(before, "New") == after


def test_insert_changelog_entry_normalises_bullet_and_newlines():
    out = insert_changelog_entry("", "- Fix crash\non empty title")
    assert out == "# Changelog\n\n## [Unreleased]\n\n- Fix crash on empty title\n"
    assert insert_changelog_entry("", "* Starred").endswith("- Starred\n")
