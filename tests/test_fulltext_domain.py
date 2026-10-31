import pytest

from research_agent.domain.capabilities import Rights
from research_agent.domain.fulltext import FullTextPolicy, FullTextResult, jats_to_text, licence_key

ARTICLE = """<article xmlns:xlink="http://www.w3.org/1999/xlink" xmlns:ali="http://www.niso.org/schemas/ali/1.0/"><front><article-meta>
<title-group><article-title>Migraine <italic>and</italic> sleep</article-title></title-group>
<permissions><license license-type="open-access" xlink:href="https://creativecommons.org/licenses/by-nc/4.0/"><license-p>CC BY-NC</license-p></license></permissions></article-meta></front>
<body><sec><title>Introduction</title><p>Migraine affects many people<xref ref-type="bibr" rid="b1">1</xref>, see <xref ref-type="fig" rid="f1">Figure 1</xref>. Prior work [<xref ref-type="bibr" rid="b2">2</xref>] agrees; dose 5 mg/m<sup>2</sup>.</p>
<fig id="f1"><label>Figure 1</label><caption><p>A plot of days.</p></caption></fig><list><list-item><p>one</p></list-item><list-item><p>two</p></list-item></list></sec>
<sec><title>Methods</title><sec><title>Design</title><p>Randomised.</p><table-wrap><label>Table 1</label><caption><p>Baseline.</p></caption><table><tr><td>x</td></tr></table></table-wrap></sec></sec></body>
<back><ref-list><ref><mixed-citation>Do not include me.</mixed-citation></ref></ref-list></back></article>"""


@pytest.mark.parametrize("raw,key", [
    ("https://creativecommons.org/licenses/by/4.0/", "cc-by"), ("http://creativecommons.org/licenses/by-nc-nd/3.0/", "cc-by-nc-nd"),
    ("https://creativecommons.org/licenses/by-nc-sa/4.0/legalcode", "cc-by-nc-sa"), ("cc-by", "cc-by"), ("CC BY-NC 4.0", "cc-by-nc"),
    ("cc0", "cc0"), ("http://creativecommons.org/publicdomain/zero/1.0/", "cc0"), ("publisher-specific-oa", "other"),
    ("https://www.elsevier.com/tdm/userlicense/1.0/", "other"), (None, None), ("", None),
])
def test_licence_key(raw, key):
    assert licence_key(raw) == key


def test_jats_conversion_keeps_structure_and_drops_references():
    d = jats_to_text(ARTICLE)
    assert d.title == "Migraine and sleep" and d.has_body
    assert licence_key(d.licence_url) == "cc-by-nc"
    lines = d.text.split("\n\n")
    assert lines[0] == "## Introduction" and "### Design" in lines
    assert "Migraine affects many people, see Figure 1." in d.text      # citation number dropped, figure cross-reference kept
    assert "Prior work agrees" in d.text and "[]" not in d.text         # no empty brackets left behind
    assert "[Figure 1: A plot of days.]" in d.text and "[Table 1: Baseline.]" in d.text and "- one" in d.text
    assert "Do not include me" not in d.text and "dose 5 mg/m2" in d.text


def test_jats_inside_a_wrapper_and_with_ali_licence_ref():
    wrapped = "<response><records>" + ARTICLE.replace("<permissions>", "<permissions><ali:license_ref>https://creativecommons.org/licenses/by/4.0/</ali:license_ref>", 1).replace(
        'xlink:href="https://creativecommons.org/licenses/by-nc/4.0/"', "") + "</records></response>"
    d = jats_to_text(wrapped)
    assert d and licence_key(d.licence_url) == "cc-by"


def test_jats_without_a_body_or_not_xml():
    d = jats_to_text("<article><front><article-meta/></front></article>")
    assert d is not None and not d.has_body
    assert jats_to_text("not xml at all") is None and jats_to_text("<response><records/></response>") is None


def test_default_policy_prints_any_text_whatever_the_licence_or_access():
    p = FullTextPolicy()      # owner decision 2026-10-31: licences do not limit printing
    r = lambda lic, rights=Rights.OPEN: FullTextResult("ok", "x", text="t", licence=lic, rights=rights)
    for lic, rights in (("cc-by", Rights.OPEN), ("cc-by-nc", Rights.OPEN), ("cc-by-nc-nd", Rights.OPEN), (None, Rights.UNKNOWN), (None, Rights.ENTITLED), ("other", Rights.ENTITLED)):
        assert p.print_allowed(r(lic, rights))


def test_the_cautious_policy_is_still_available_for_other_profiles():
    p = FullTextPolicy(regardless_of_licence=False)
    r = lambda lic, rights=Rights.OPEN: FullTextResult("ok", "x", text="t", licence=lic, rights=rights)
    assert p.print_allowed(r("cc-by")) and p.print_allowed(r("cc0"))
    assert not p.print_allowed(r("cc-by-nc")) and not p.print_allowed(r("cc-by-nd")) and not p.print_allowed(r(None, Rights.UNKNOWN))
    assert not p.print_allowed(r(None, Rights.ENTITLED))                                     # paid access alone never allows printing here
    assert FullTextPolicy(regardless_of_licence=False, allow_entitled_in_print=True).print_allowed(r(None, Rights.ENTITLED))
    assert not FullTextPolicy(regardless_of_licence=False, allow_entitled_in_print=True).print_allowed(r("cc-by-nc-nd", Rights.ENTITLED))
    assert FullTextPolicy(regardless_of_licence=False, print_licences=("cc-by", "cc-by-nc")).print_allowed(r("cc-by-nc"))
