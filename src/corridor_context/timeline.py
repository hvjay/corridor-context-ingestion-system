"""Valid-time selection. Arrival order never establishes factual precedence."""
from datetime import datetime, timezone
import json

POLICY_VERSION = 'timeline-v2-created-tiebreak'


def instant(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError('A statement/effective timestamp is required')
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError as exc:
        raise ValueError('Invalid timeline timestamp') from exc
    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def now():
    return datetime.now(timezone.utc).isoformat()


def validate_period(start, end, evidence, source):
    for value in (start, end, evidence):
        if value is not None and (not isinstance(value, str) or not value.strip()):
            raise ValueError('Temporal fields must be nonempty strings or null')
    if evidence and not start:
        raise ValueError('Temporal evidence requires an effective date')
    if end and not start:
        raise ValueError('An effective period needs a start date')
    if start:
        instant(start)
        if not evidence or evidence not in source:
            raise ValueError('Effective dates require exact supporting transcript evidence')
    if end and instant(end) <= instant(start):
        raise ValueError('Effective period end must follow its start (exclusive end)')


def select_facts(conn, client_id, as_of=None):
    """Return winners and unresolved ties at valid time, using all known evidence."""
    at = instant(as_of or now())
    rows = conn.execute('SELECT * FROM fact_versions WHERE client_id = ?', (client_id,)).fetchall()
    meetings = {row['id']: row for row in conn.execute('SELECT id, content_hash, created_at FROM meetings')}
    revisions = {identity: meeting['content_hash'] for identity, meeting in meetings.items()}
    retractions = conn.execute('SELECT * FROM fact_retractions WHERE client_id = ?', (client_id,)).fetchall()
    cutoffs = {}
    for row in retractions:
        time = instant(row['retracted_at'])
        if time <= at:
            cutoffs[row['fact_type']] = max(cutoffs.get(row['fact_type'], time), time)
    groups = {}
    for row in rows:
        if row['source_revision'] and row['source_revision'] != revisions.get(row['source_meeting_id']):
            continue
        if not row['statement_known'] or not row['observed_at']:
            continue
        stated = instant(row['observed_at'])
        start = instant(row['effective_from'] or row['observed_at'])
        end = instant(row['effective_to']) if row['effective_to'] else None
        if start > at or end and at >= end:
            continue
        if row['fact_type'] in cutoffs and stated <= cutoffs[row['fact_type']]:
            continue
        groups.setdefault(row['fact_type'], []).append((start, stated, row))
    winners, conflicts = {}, {}
    for kind, candidates in groups.items():
        rank = max((start, stated) for start, stated, _ in candidates)
        latest = [row for start, stated, row in candidates if (start, stated) == rank]
        # Source creation breaks temporal ties; local ingestion order never does.
        # Missing legacy metadata cannot establish precedence, so retain the tie.
        created_times = []
        for row in latest:
            meeting = meetings.get(row['source_meeting_id'])
            try:
                created_times.append(instant(meeting['created_at']) if meeting else None)
            except ValueError:
                created_times.append(None)
        if all(value is not None for value in created_times):
            newest_created = max(created_times)
            latest = [row for row, created in zip(latest, created_times) if created == newest_created]
        values = {json.dumps(json.loads(row['normalized_value_json']), sort_keys=True) for row in latest}
        if len(values) > 1:
            conflicts[kind] = latest
        else:
            # Equivalent evidence: choose stable source identity, never arrival time.
            winners[kind] = min(latest, key=lambda row: (row['source_meeting_id'], row['source_excerpt'], row['id']))
    return winners, conflicts


def reconcile(conn, client_id):
    winners, _ = select_facts(conn, client_id)
    conn.execute('UPDATE fact_versions SET is_current = 0 WHERE client_id = ?', (client_id,))
    conn.executemany('UPDATE fact_versions SET is_current = 1 WHERE id = ?', [(row['id'],) for row in winners.values()])
    return winners
