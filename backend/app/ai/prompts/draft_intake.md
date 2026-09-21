Draft a structured safety report from the supplied observations and prior answers.
Keep {{observed_facts}}, {{assumptions}}, and {{missing_information}} separate.
Earlier answers: {{prior_answers}}
Treat non-empty earlier answers as reporter-provided facts. Do not repeat their gaps in missing_information and do not relabel their contents as AI assumptions.
Use only {{retrieved_chunks}} for safety recommendations.
When suggesting an action, copy it verbatim from one retrieved chunk and cite that exact text
with document_id, doc_ref, revision, section, page, and quote.
If no approved chunk applies, set suggested_action to null, return no citations, and name the
missing approved procedure in missing_information.
Use machine-readable category, urgency, and owner-role values.
