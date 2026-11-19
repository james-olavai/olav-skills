"""Bounded, deterministic report-to-selected-evidence identity linting.

The caller's context is a trust anchor, not something this module authenticates.
Matching citations cannot establish that prose follows from the selected evidence.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys

from .evidence_delivery import (
    BundleError, _load_json, _project, _read_source, _source_paths,
)

SCHEMA = 'olav.report-citations/v1'
EVIDENCE_SCHEMA = 'olav.report-evidence/v1'
MAX_REQUEST = 512 * 1024
MAX_REPORT = 64 * 1024
MAX_EVIDENCE = 32 * 1024
MAX_TOTAL_EVIDENCE = 256 * 1024
MAX_LINES = 500
MAX_CLAIMS = 128
MAX_EVIDENCES = 16
MAX_ISSUES = 64
LABEL = re.compile(r'[A-Za-z][A-Za-z0-9_.-]{0,63}\Z')
SID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z')
HASH = re.compile(r'[a-f0-9]{64}\Z')
CITATION = re.compile(r'\[evidence:([A-Za-z][A-Za-z0-9_.-]{0,63})\]')
CONTROL = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]')


class CitationError(ValueError):
    """Stable input error, without echoing caller text or paths."""


def _keys(value, required, optional=()):
    if not isinstance(value, dict) or not set(required) <= set(value) or set(value) - set(required) - set(optional):
        raise CitationError('invalid_object_fields')


def _label(value, pattern=LABEL):
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise CitationError('invalid_identity')
    return value


def _identity(value):
    _keys(value, ('source', 'snapshot_id', 'sha256'))
    _label(value['source'])
    _label(value['snapshot_id'], SID)
    if value['snapshot_id'].lower() in {'latest', 'current', 'newest', 'all'}:
        raise CitationError('exact_snapshot_required')
    _label(value['sha256'], HASH)
    return value['source'], value['snapshot_id'], value['sha256']


def _raw(item, text_key, path_key, project, cap):
    if (text_key in item) == (path_key in item):
        raise CitationError('exactly_one_text_or_path_required')
    if text_key in item:
        if not isinstance(item[text_key], str):
            raise CitationError('text_required')
        try:
            data = item[text_key].encode('utf-8')
        except UnicodeError as exc:
            raise CitationError('invalid_utf8') from exc
    else:
        if project is None:
            raise CitationError('project_required_for_files')
        path = _source_paths([item[path_key]])[0]
        with _project(project) as root:
            data = _read_source(root, path, cap)
    if len(data) > cap:
        raise CitationError('input_byte_budget_exceeded')
    return data


def _text(data):
    try:
        result = data.decode('utf-8')
    except UnicodeError as exc:
        raise CitationError('invalid_utf8') from exc
    if CONTROL.search(result):
        raise CitationError('control_characters_refused')
    return result


def _base(**fields):
    return {'schema': SCHEMA, 'status': 'success', 'completed': True,
            'verdict': 'pass', 'claim_truth': 'not_checked',
            'source_authenticity': 'not_checked', 'identity_check': 'caller_context_only',
            'collection_time': 'not_checked',
            'live_state': 'unknown', **fields}


def run(request):
    """Lint an explicit report and manifest; perform no discovery or writes."""
    _keys(request, ('context', 'evidence', 'claims'), ('report_text', 'report_path', 'project'))
    # Also bound direct Python calls; stdin has an independent wire-byte limit.
    try:
        if len(json.dumps(request, ensure_ascii=False, allow_nan=False).encode('utf-8')) > MAX_REQUEST:
            raise CitationError('request_byte_budget_exceeded')
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        if isinstance(exc, CitationError):
            raise
        raise CitationError('invalid_json') from exc
    context = request['context']
    _keys(context, ('scope_id', 'sources'))
    scope = _label(context['scope_id'])
    sources = context['sources']
    if not isinstance(sources, list) or not 1 <= len(sources) <= MAX_EVIDENCES:
        raise CitationError('source_count_out_of_bounds')
    identities = [_identity(row) for row in sources]
    if len(set(identities)) != len(identities) or len({row[:2] for row in identities}) != len(identities):
        raise CitationError('duplicate_source_identity')
    allowed = set(identities)
    report = _raw(request, 'report_text', 'report_path', request.get('project'), MAX_REPORT)
    lines = _text(report).splitlines()
    if not lines or not any(line.strip() for line in lines) or len(lines) > MAX_LINES:
        raise CitationError('report_line_count_out_of_bounds')
    evidence = request['evidence']
    if not isinstance(evidence, list) or not 1 <= len(evidence) <= MAX_EVIDENCES:
        raise CitationError('evidence_count_out_of_bounds')
    selected, used, payloads = {}, 0, set()
    issues, issue_count = [], 0

    def issue(code, **fields):
        nonlocal issue_count
        issue_count += 1
        if len(issues) < MAX_ISSUES:
            issues.append({'code': code, **fields})

    for row in evidence:
        _keys(row, ('id',), ('text', 'path'))
        name = _label(row['id'])
        if name in selected:
            raise CitationError('duplicate_evidence_id')
        raw = _raw(row, 'text', 'path', request.get('project'), MAX_EVIDENCE)
        used += len(raw)
        if used > MAX_TOTAL_EVIDENCE:
            raise CitationError('evidence_byte_budget_exceeded')
        _text(raw)
        payload = _load_json(raw)
        _keys(payload, ('schema', 'scope_id', 'source', 'snapshot_id', 'body'))
        if payload['schema'] != EVIDENCE_SCHEMA:
            raise CitationError('unsupported_evidence_schema')
        _label(payload['scope_id'])
        if not isinstance(payload['body'], str):
            raise CitationError('evidence_body_must_be_original_text')
        body = payload['body'].encode('utf-8')
        digest = hashlib.sha256(body).hexdigest()
        identity = _identity({key: payload[key] for key in ('source', 'snapshot_id')} | {'sha256': digest})
        evidence_identity = (payload['scope_id'], *identity)
        if evidence_identity in payloads:
            raise CitationError('duplicate_evidence_identity')
        payloads.add(evidence_identity)
        valid = True
        if payload['scope_id'] != scope:
            issue('evidence_scope_mismatch', evidence_id=name)
            valid = False
        if identity not in allowed:
            issue('evidence_source_snapshot_or_hash_mismatch', evidence_id=name)
            valid = False
        if not payload['body'].strip():
            issue('empty_evidence_body', evidence_id=name)
            valid = False
        selected[name] = valid

    claims = request['claims']
    if not isinstance(claims, list) or not 1 <= len(claims) <= MAX_CLAIMS:
        raise CitationError('claim_count_out_of_bounds')
    names, covered, cited = set(), set(), set()
    for claim in claims:
        _keys(claim, ('id', 'line_start', 'line_end', 'evidence'))
        name = _label(claim['id'])
        if name in names:
            raise CitationError('duplicate_claim_id')
        names.add(name)
        start, end = claim['line_start'], claim['line_end']
        if type(start) is not int or type(end) is not int or not 1 <= start <= end <= len(lines):
            raise CitationError('invalid_claim_line_range')
        positions = set(range(start, end + 1))
        if covered & positions:
            raise CitationError('overlapping_claim_ranges')
        covered |= positions
        refs = claim['evidence']
        if not isinstance(refs, list) or len(refs) > MAX_EVIDENCES:
            raise CitationError('invalid_claim_evidence')
        for ref in refs:
            _label(ref)
        if len(set(refs)) != len(refs):
            raise CitationError('duplicate_claim_evidence')
        if not refs:
            issue('claim_without_evidence', claim_id=name)
        block = '\n'.join(lines[start-1:end])
        inline = CITATION.findall(block)
        # Any reserved opening that is not exactly a valid marker is an error,
        # including markers in code fences: no Markdown interpretation occurs.
        if '[evidence:' in CITATION.sub('', block):
            issue('malformed_citation', claim_id=name)
        if len(inline) != len(set(inline)):
            issue('duplicate_inline_citation', claim_id=name)
        if set(inline) != set(refs):
            issue('claim_citation_set_mismatch', claim_id=name)
        for ref in sorted(set(refs) | set(inline)):
            cited.add(ref)
            if ref not in selected:
                issue('unknown_evidence_reference', claim_id=name, evidence_id=ref)
            elif not selected[ref]:
                issue('claim_uses_invalid_evidence', claim_id=name, evidence_id=ref)
    for number, line in enumerate(lines, 1):
        if line.strip() and number not in covered:
            issue('unmapped_report_line', line=number)
    for name in sorted(set(selected) - cited):
        issue('unused_evidence', evidence_id=name)
    return _base(verdict='fail' if issue_count else 'pass', scope_id=scope,
                 report_sha256=hashlib.sha256(report).hexdigest(),
                 coverage='all_nonblank_lines_mapped' if all(not line.strip() or n in covered for n, line in enumerate(lines, 1)) else 'partial',
                 checked={'claims': len(claims), 'evidence': len(selected), 'report_lines': len(lines)},
                 issues=issues, issue_count=issue_count, issues_truncated=issue_count > len(issues))


def main():
    try:
        raw = sys.stdin.buffer.read(MAX_REQUEST + 1)
        if len(raw) > MAX_REQUEST:
            raise CitationError('request_byte_budget_exceeded')
        result = run(_load_json(raw))
    except (CitationError, BundleError) as exc:
        result = _base(status='error', completed=False, verdict='unknown', error_type=str(exc), issues=[])
    except OSError:
        result = _base(status='error', completed=False, verdict='unknown', error_type='project_or_file_unavailable', issues=[])
    except (ValueError, RecursionError, UnicodeError):
        result = _base(status='error', completed=False, verdict='unknown', error_type='invalid_json', issues=[])
    print(json.dumps(result, ensure_ascii=True, allow_nan=False))
    return 0 if result['status'] == 'success' and result['verdict'] == 'pass' else 1
