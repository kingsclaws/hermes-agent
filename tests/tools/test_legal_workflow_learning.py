from tools.legal_workflow_learning import learning_candidates_from_findings


def test_learning_candidate_keys_vary_by_workflow_type():
    findings = [
        {
            "issue_type": "defined_term",
            "translation_text": "Security Trustee",
            "suggestion": "Chargee",
            "finding": "Use the transaction-specific secured-party label.",
        }
    ]

    translation = learning_candidates_from_findings(
        findings,
        workflow_type="translation_quality_review",
    )
    proofread = learning_candidates_from_findings(
        findings,
        workflow_type="proofread_review",
    )

    assert translation[0]["rule_key"] != proofread[0]["rule_key"]
    assert translation[0]["title"] == proofread[0]["title"]
