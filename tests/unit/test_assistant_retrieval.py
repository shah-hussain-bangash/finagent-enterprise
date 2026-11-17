"""Lexical assistant retrieval remains scoped, bounded and transparent."""

from finagent.agents.assistant_retrieval import retrieve_snapshot_excerpts


def item(ref, text):  # type: ignore[no-untyped-def]
    return {"evidence_item_id": ref, "source_type": "DOCUMENT_EXTRACTION", "source_ref": "synthetic", "version_no": 1, "freshness": "CURRENT", "completeness": "COMPLETE", "as_of": None, "content": {"text": text}}


def test_question_matches_words_in_same_record_without_claiming_semantics() -> None:
    evidence = {"items": [item("first", "Expected monthly turnover AED 50000."), item("other", "Monthly rent AED 2000.")]}
    result = retrieve_snapshot_excerpts(evidence, "What is the expected monthly turnover?")
    assert result["matches"]
    assert {match["evidence_ref"] for match in result["matches"]} == {"ev:first"}


def test_requires_two_terms_when_available_and_discloses_dropped_terms() -> None:
    evidence = {"items": [item("first", "Customer turnover"), item("other", "Customer address")]}
    result = retrieve_snapshot_excerpts(evidence, "customer turnover alpha bravo charlie delta")
    assert {match["evidence_ref"] for match in result["matches"]} == {"ev:first"}
    assert result["terms_used"] == 5 and result["search_limited"] is True


def test_no_useful_words_abstains_without_inventing_results() -> None:
    result = retrieve_snapshot_excerpts({"items": []}, "what is the")
    assert result["matches"] == [] and result["terms_used"] == 0


def test_bilingual_aliases_match_in_both_directions_without_double_counting() -> None:
    arabic = item("arabic", "الإيراد الشهري المتوقع ١٠٠٠ درهم")
    english = item("english", "Expected monthly turnover AED 1000")
    english_query = retrieve_snapshot_excerpts({"items": [arabic]}, "monthly turnover")
    arabic_query = retrieve_snapshot_excerpts({"items": [english]}, "الإيراد الشهري")
    assert {match["evidence_ref"] for match in english_query["matches"]} == {"ev:arabic"}
    assert {match["evidence_ref"] for match in arabic_query["matches"]} == {"ev:english"}
    assert english_query["bilingual_aliases_used"] is True
    only_one_concept = item("one", "monthly الشهري")
    assert retrieve_snapshot_excerpts({"items": [only_one_concept]}, "monthly turnover")["matches"] == []


def test_terms_may_match_different_fields_but_never_different_records() -> None:
    first = item("first", "unused")
    first["content"] = {"name": "Customer Example", "turnover": "Turnover AED 50000"}
    second = item("second", "Customer Example")
    third = item("third", "Turnover AED 50000")
    result = retrieve_snapshot_excerpts({"items": [first, second, third]}, "customer turnover")
    assert {match["evidence_ref"] for match in result["matches"]} == {"ev:first"}
    assert {match["field_path"] for match in result["matches"]} == {"content.name", "content.turnover"}


def test_equal_scoring_records_are_disclosed_without_treating_them_as_verified() -> None:
    evidence = {"items": [item("first", "Customer details for Example A."), item("other", "Customer details for an unrelated example.")]}
    result = retrieve_snapshot_excerpts(evidence, "customer details")
    assert result["terms_used"] == 2
    assert result["matching_records"] == 2
    assert result["top_matched_terms"] == 2
    assert result["top_tie_count"] == 2
