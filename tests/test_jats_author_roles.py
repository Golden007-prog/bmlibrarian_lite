# BMLibrarian Lite - Biomedical Literature Research Tool
# Copyright (C) 2024-2026 Dr Horst Herb
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program. If not, see <https://www.gnu.org/licenses/>.

"""An author role declared on the contributor group (bmlib#111).

About 57% of open-access articles declare ``content-type="author"`` on the
``<contrib-group>`` and leave each ``<contrib>`` bare. The rule is bmlib's
``_is_author_contrib``: a contributor's own ``contrib-type`` decides alone,
else the innermost enclosing group that declares a role, else author.
"""

import xml.etree.ElementTree as ET

from bmlibrarian_lite.jats_markdown import _article_authors, jats_to_markdown


def _meta(groups: str) -> ET.Element:
    """Parse an ``<article-meta>`` holding ``groups``."""
    return ET.fromstring(f"<article-meta>{groups}</article-meta>")


EDITOR = 'contrib-type="editor"'
AUTHOR = 'contrib-type="author"'
EMPTY = 'contrib-type=""'


def _contrib(surname: str, given: str = "A", attrs: str = "") -> str:
    """One ``<contrib>`` with a name."""
    return (
        f"<contrib {attrs}><name><surname>{surname}</surname>"
        f"<given-names>{given}</given-names></name></contrib>"
    )


class TestAuthorRoleOnGroup:
    """The group's declared role reaches its bare contributors."""

    def test_a_group_role_makes_bare_contribs_authors(self) -> None:
        """A group role makes bare contribs authors."""
        meta = _meta(
            f'<contrib-group content-type="author">{_contrib("Smith")}'
            f'{_contrib("Jones", "B")}</contrib-group>'
        )
        assert _article_authors(meta) == ["A Smith", "B Jones"]

    def test_an_editor_group_is_not_authors(self) -> None:
        """An editor group is not authors."""
        meta = _meta(
            f'<contrib-group content-type="author">{_contrib("Smith")}</contrib-group>'
            f'<contrib-group content-type="editor">{_contrib("Ed")}</contrib-group>'
        )
        assert _article_authors(meta) == ["A Smith"]

    def test_a_contribs_own_editor_role_wins_inside_an_author_group(self) -> None:
        """A contribs own editor role wins inside an author group."""
        meta = _meta(
            '<contrib-group content-type="author">'
            f'{_contrib("Smith")}{_contrib("Ed", attrs=EDITOR)}'
            "</contrib-group>"
        )
        assert _article_authors(meta) == ["A Smith"]

    def test_role_case_is_folded(self) -> None:
        """Role case is folded."""
        meta = _meta(
            f'<contrib-group content-type="Author">{_contrib("Smith")}</contrib-group>'
            f'<contrib-group content-type="EDITOR">{_contrib("Ed")}</contrib-group>'
        )
        assert _article_authors(meta) == ["A Smith"]

    def test_an_empty_attribute_declares_nothing(self) -> None:
        """An empty attribute declares nothing."""
        meta = _meta(
            f'<contrib-group content-type="">{_contrib("Smith")}'
            f'{_contrib("Jones", "B", attrs=EMPTY)}</contrib-group>'
        )
        assert _article_authors(meta) == ["A Smith", "B Jones"]

    def test_every_group_is_read(self) -> None:
        """Every group is read."""
        meta = _meta(
            f'<contrib-group content-type="author">{_contrib("Smith")}</contrib-group>'
            f'<contrib-group content-type="author">{_contrib("Jones", "B")}</contrib-group>'
        )
        assert _article_authors(meta) == ["A Smith", "B Jones"]

    def test_contrib_type_author_still_reads(self) -> None:
        """Contrib type author still reads."""
        meta = _meta(
            f'<contrib-group>{_contrib("Smith", attrs=AUTHOR)}'
            f'{_contrib("Ed", attrs=EDITOR)}</contrib-group>'
        )
        assert _article_authors(meta) == ["A Smith"]

    def test_a_collab_roster_is_not_listed(self) -> None:
        """A collab roster is not listed."""
        meta = _meta(
            '<contrib-group content-type="author">'
            f'{_contrib("Smith")}'
            '<contrib><collab>Study Group<contrib-group>'
            f'{_contrib("Member")}</contrib-group></collab></contrib>'
            "</contrib-group>"
        )
        assert _article_authors(meta) == ["A Smith"]


def test_the_author_line_reaches_the_markdown() -> None:
    """The author line of the converted markdown names the group-role authors."""
    xml = (
        "<article><front><article-meta>"
        f'<contrib-group content-type="author">{_contrib("Smith")}</contrib-group>'
        "</article-meta></front></article>"
    )
    assert "**Authors:** A Smith" in jats_to_markdown(xml)


def test_a_journal_meta_group_adds_no_author() -> None:
    """The journal's editors, in ``<journal-meta>``, are not the article's authors."""
    xml = (
        "<article><front><journal-meta>"
        f"<contrib-group>{_contrib('Editor')}</contrib-group></journal-meta>"
        f'<article-meta><contrib-group content-type="author">{_contrib("Smith")}'
        "</contrib-group></article-meta></front></article>"
    )
    markdown = jats_to_markdown(xml)
    assert "**Authors:** A Smith" in markdown
    assert "Editor" not in markdown


def test_a_sub_article_front_stub_adds_no_author() -> None:
    """A sub-article's reviewers are not the article's authors."""
    xml = (
        "<article><front><article-meta>"
        f'<contrib-group content-type="author">{_contrib("Smith")}</contrib-group>'
        "</article-meta></front><sub-article><front-stub>"
        f"<contrib-group>{_contrib('Reviewer')}</contrib-group>"
        "</front-stub></sub-article></article>"
    )
    markdown = jats_to_markdown(xml)
    assert "**Authors:** A Smith" in markdown
    assert "Reviewer" not in markdown
