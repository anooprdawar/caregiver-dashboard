from caregiver import textfmt


def test_escapes_before_formatting():
    out = textfmt.render("<script>alert(1)</script> and **bold**")
    assert "<script>" not in out and "&lt;script&gt;" in out and "<strong>bold</strong>" in out


def test_clinical_headings_and_runins():
    out = textfmt.render("IMPRESSION: No residual cord compression.\n\nFINDINGS\nStable hardware.")
    assert '<h4 class="sec">Impression</h4>' in out
    assert "No residual cord compression." in out
    assert '<h4 class="sec">Findings</h4>' in out


def test_bullets_and_markdown_headers():
    out = textfmt.render("## Plan\n- continue dex\n- recheck labs\n1) call PCP")
    assert "<h4>Plan</h4>" in out
    assert out.count("<li>") == 3 and "<ul>" in out


def test_key_value_lines_kept_distinct():
    out = textfmt.render("Discharge Weight: 68 kg\nRoom: 7 West")
    assert '<p class="kv"><b>Discharge Weight:</b> 68 kg</p>' in out


def test_paragraphs_join_wrapped_lines():
    out = textfmt.render("The patient was admitted\nwith back pain.\n\nSecond para.")
    assert "<p>The patient was admitted with back pain.</p>" in out
    assert out.count("<p>") >= 2


def test_empty_and_none():
    assert "No text." in textfmt.render(None)
    assert "No text." in textfmt.render("   ")


def test_summarize_prefers_impression():
    assert textfmt.summarize("FINDINGS: lots of words.\nIMPRESSION: complete response.").startswith("complete response")
    assert textfmt.summarize("x" * 400).endswith("…")
