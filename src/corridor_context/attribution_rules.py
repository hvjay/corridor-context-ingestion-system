"""Explicit offline baseline; production ingestion defaults to LLM attribution."""
import re
from .attribution import employer_directory, resolve_target, mentions, email_employer

# Deliberately holds the whole passage: taking a convenient substring out of a
# comparison or former-employer story can remove the very attribution evidence.
UNSAFE_REFERENCE = re.compile(
    r"\b(previous|former|last|another|other|different)\s+(?:\w+\s+){0,2}(company|employer|client|business)\b"
    r"|\b(subsidiary|subsidiaries|parent company|competitor|competitors|compared|whereas|unlike)\b"
    r"|\b(they|their|them)\b", re.I)
NAMED_SUBJECT = re.compile(r"\b([A-Z][\w&'-]*(?: [A-Z][\w&'-]*)*)\s+(?:has|have|prefers?|needs|uses|employs)\b")
FOREIGN_CONTEXT = re.compile(r"\b(?i:at|for|about|with) ([A-Z][\w&'-]*(?: [A-Z][\w&'-]*)*)|\b([A-Z][\w&'-]*(?: [A-Z][\w&'-]*)*)['’]s")
GENERIC_SUBJECTS = {"we", "finance", "payroll", "the client", "client", "it"}


def assess_segments(meeting: dict, target=None, directory=None) -> list[dict]:
    directory = employer_directory() if directory is None else directory
    target = resolve_target(meeting, directory) if target is None else target
    decisions = []
    topic_confirmed = False
    owner_email = meeting.get("owner", {}).get("email", "").casefold()
    for index, segment in enumerate(meeting.get("transcript", [])):
        text = segment.get("text", "")
        foreign = any(row["key"] != target["key"] and mentions(text, row) for row in directory)
        unknown_subject = any(
            match.group(1).casefold() not in GENERIC_SUBJECTS and not mentions(match.group(1), target)
            for match in NAMED_SUBJECT.finditer(text)
        )
        unknown_subject = unknown_subject or any(
            not mentions(match.group(1) or match.group(2), target)
            for match in FOREIGN_CONTEXT.finditer(text)
        )
        speaker_email = segment.get("speaker", {}).get("email", "").casefold()
        speaker = email_employer(speaker_email, directory)
        if foreign or UNSAFE_REFERENCE.search(text) or unknown_subject:
            status, reason = "review", "Other employer, comparison, or ambiguous reference in passage"
            topic_confirmed = False
        elif mentions(text, target):
            status, reason = "accepted", "Explicit target employer in passage"
            topic_confirmed = True
        elif speaker and speaker["key"] == target["key"] and re.search(r"\b(we|our)\b", text, re.I):
            # A topic switch must be explicitly reset before trusting pronouns.
            if topic_confirmed:
                status, reason = "accepted", "Target employer speaker continuing confirmed topic"
            else:
                status, reason = "review", "Pronoun requires an explicit target employer anchor"
        elif topic_confirmed and speaker_email and (speaker_email == owner_email or (speaker and speaker["key"] == target["key"])):
            status, reason = "accepted", "Known meeting participant continuing confirmed target topic"
        else:
            status, reason = "review", "No supported target employer attribution"
            topic_confirmed = False
        decisions.append({"segment_index": index, "status": status, "reason": reason, "excerpt": text})
    return decisions
