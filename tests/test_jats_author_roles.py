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


class TestBareContributorsAreInferredOnlyFromTheArticlesOwnGroups:
    """The inference starts only at a contrib-group directly in article-meta.

    bmlib#266's scope: a ``<contrib-group>`` inside ``<supplement>``,
    ``<sec-meta>`` or another element nested in ``<article-meta>`` lists
    someone else (a supplement's editors, a section's authors), so its bare
    contributors are not the article's authors.
    """

    def test_a_bare_contrib_in_a_supplement_group_is_not_an_author(self) -> None:
        """article-meta > supplement > contrib-group > bare contrib: no author."""
        meta = _meta(
            f'<contrib-group content-type="author">{_contrib("Smith")}</contrib-group>'
            f"<supplement><contrib-group>{_contrib('SupEd')}</contrib-group></supplement>"
        )
        assert _article_authors(meta) == ["A Smith"]

    def test_a_supplement_group_declaring_author_still_infers_nothing(self) -> None:
        """A nested group's own content-type="author" does not anchor it."""
        meta = _meta(
            '<supplement><contrib-group content-type="author">'
            f"{_contrib('SupAuthor')}</contrib-group></supplement>"
        )
        assert _article_authors(meta) == []

    def test_a_bare_contrib_in_a_sec_meta_group_is_not_an_author(self) -> None:
        """Any element between article-meta and the group ends the inference."""
        meta = _meta(
            f"<sec-meta><contrib-group>{_contrib('SecAuthor')}</contrib-group></sec-meta>"
        )
        assert _article_authors(meta) == []

    def test_an_own_author_role_in_a_nested_group_is_still_read(self) -> None:
        """A contributor's own contrib-type="author" is read where it was before."""
        meta = _meta(
            f"<supplement><contrib-group>{_contrib('Own', attrs=AUTHOR)}"
            f"{_contrib('Bare')}</contrib-group></supplement>"
        )
        assert _article_authors(meta) == ["A Own"]

    def test_the_direct_child_group_still_infers(self) -> None:
        """Control: a bare contrib in a group directly in article-meta is an author."""
        meta = _meta(f"<contrib-group>{_contrib('Smith')}</contrib-group>")
        assert _article_authors(meta) == ["A Smith"]

    def test_a_roster_nested_in_the_articles_group_follows_the_stack_rule(self) -> None:
        """A group inside an anchored group still infers, by its innermost role."""
        meta = _meta(
            '<contrib-group content-type="author">'
            f'<contrib-group>{_contrib("Inner")}</contrib-group>'
            f'<contrib-group content-type="editor">{_contrib("InnerEd")}</contrib-group>'
            "</contrib-group>"
        )
        assert _article_authors(meta) == ["A Inner"]

    def test_the_markdown_omits_a_supplement_groups_contributors(self) -> None:
        """End to end: the author line holds no supplement contributor."""
        xml = (
            "<article><front><article-meta>"
            f'<contrib-group content-type="author">{_contrib("Smith")}</contrib-group>'
            f"<supplement><contrib-group>{_contrib('SupEd')}</contrib-group></supplement>"
            "</article-meta></front></article>"
        )
        markdown = jats_to_markdown(xml)
        assert "**Authors:** A Smith" in markdown
        assert "SupEd" not in markdown


class TestScopeAndNaming:
    """The innermost declared role, the article's own scope, and its names."""

    def test_a_bare_roster_inside_an_editor_group_stays_editors(self) -> None:
        """The innermost *declared* group decides, not the innermost group.

        An undeclared group nested in an editor group inherits ``editor``;
        reading the innermost group even when it declares nothing (Swift's
        rule before PR #570) would make its bare contributors authors.
        """
        meta = _meta(
            '<contrib-group content-type="editor"><contrib-group>'
            f"{_contrib('Ed')}</contrib-group></contrib-group>"
            f'<contrib-group content-type="author">{_contrib("Smith")}</contrib-group>'
        )
        assert _article_authors(meta) == ["A Smith"]

    def test_an_own_author_role_in_a_sub_article_front_stub_adds_no_author(self) -> None:
        """PLOS lists a decision letter's academic editor as contrib-type="author".

        The shape of ``PMC13298931``'s ``aggregated-review-documents``
        sub-article: that editor is not the article's author.
        """
        xml = (
            "<article><front><article-meta>"
            f'<contrib-group content-type="author">{_contrib("Smith")}</contrib-group>'
            "</article-meta></front>"
            '<sub-article article-type="aggregated-review-documents">'
            '<front-stub><contrib-group><contrib contrib-type="author">'
            "<name><surname>Reviewer</surname><given-names>R</given-names></name>"
            "<role>Academic Editor</role></contrib></contrib-group>"
            "</front-stub></sub-article></article>"
        )
        markdown = jats_to_markdown(xml)
        assert "**Authors:** A Smith" in markdown
        assert "Reviewer" not in markdown

    def test_an_own_author_role_in_journal_meta_adds_no_author(self) -> None:
        """Only the article's own ``<article-meta>`` names its authors."""
        xml = (
            "<article><front><journal-meta><contrib-group>"
            f"{_contrib('Editor', attrs=AUTHOR)}</contrib-group></journal-meta>"
            f'<article-meta><contrib-group content-type="author">{_contrib("Smith")}'
            "</contrib-group></article-meta></front></article>"
        )
        markdown = jats_to_markdown(xml)
        assert "**Authors:** A Smith" in markdown
        assert "Editor" not in markdown

    def test_a_front_without_article_meta_names_no_author(self) -> None:
        """No ``<article-meta>``, no contributor list of the article's own.

        As on Swift and Android; none of 1,623 real PMC articles lacks one.
        """
        xml = (
            "<article><front><contrib-group>"
            f"{_contrib('Smith', attrs=AUTHOR)}</contrib-group></front></article>"
        )
        assert "**Authors:**" not in jats_to_markdown(xml)

    def test_name_alternatives_give_the_last_spelling(self) -> None:
        """A contributor named only in ``<name-alternatives>`` is listed.

        The contributor of ``PMC12337215`` (Chinese Journal of Hematology),
        verbatim: nine real articles in the survey corpus name every
        contributor this way and rendered no author line. The last spelling
        is the one bmlib (bmlib#143), Swift and Android keep.
        """
        meta = _meta(
            '<contrib-group><contrib contrib-type="author"><name-alternatives>'
            '<name name-style="eastern"><surname>周</surname>'
            '<given-names initials="诚程">诚程</given-names></name>'
            '<name xml:lang="en" name-style="western"><surname>Zhou</surname>'
            '<given-names initials="C">Chengcheng</given-names></name>'
            '</name-alternatives><xref rid="aff1" ref-type="aff">1</xref>'
            "</contrib></contrib-group>"
        )
        assert _article_authors(meta) == ["Chengcheng Zhou"]

    def test_a_direct_name_is_read_as_before(self) -> None:
        """Control: a contributor with a direct ``<name>`` is read as before."""
        meta = _meta(f"<contrib-group>{_contrib('Smith', attrs=AUTHOR)}</contrib-group>")
        assert _article_authors(meta) == ["A Smith"]
