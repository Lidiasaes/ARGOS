"""Haiku fallback for ambiguous source role — metadata only, no domain knowledge."""

SOURCE_ROLE_CLASSIFIER = """
Classify this document's structural role from metadata only. Do not use domain expertise.

TITLE: {title}
AUTHOR: {author}
URL: {url}
CONTENT PREVIEW (first ~200 chars): {preview}

Choose exactly one role:
- primary_research — original empirical/theoretical research article
- review — systematic review, meta-analysis, literature survey
- commentary — essay, blog, newsletter, opinion analysis
- debate_transcript — debate, interview, oral argument transcript
- judge_decision — judicial ruling or arbitrator decision
- rebuttal — response to another author/paper (reply, rebuttal)
- unknown — cannot determine from metadata

Return JSON only:
{{"role": "...", "reason": "one sentence"}}
"""
