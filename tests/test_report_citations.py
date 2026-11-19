"""Synthetic report identity tests; no network, data runtime or model required."""
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from olav_skills.report_citations import CitationError, MAX_REQUEST, run


def fixture():
    body = '{"rows":[{"hostname":"r1","state":"down"}],"has_more":true}\n'
    envelope = {'schema': 'olav.report-evidence/v1', 'scope_id': 'review-1',
                'source': 'interfaces', 'snapshot_id': 's1', 'body': body}
    return {'report_text': '# Recorded review\n\nr1 has one returned down record [evidence:E1]\n',
            'context': {'scope_id': 'review-1', 'sources': [{
                'source': 'interfaces', 'snapshot_id': 's1',
                'sha256': hashlib.sha256(body.encode()).hexdigest()}]},
            'evidence': [{'id': 'E1', 'text': json.dumps(envelope)}],
            'claims': [{'id': 'C1', 'line_start': 1, 'line_end': 3, 'evidence': ['E1']}]}


def codes(result):
    return {row['code'] for row in result['issues']}


def test_exact_raw_payload_hash_and_limits_of_pass():
    request = fixture()
    result = run(request)
    assert result['verdict'] == 'pass'
    assert result['coverage'] == 'all_nonblank_lines_mapped'
    assert result['claim_truth'] == result['source_authenticity'] == 'not_checked'
    assert result['identity_check'] == 'caller_context_only'
    assert result['collection_time'] == 'not_checked'
    # A semantically false claim is deliberately not certified by this linter.
    request['report_text'] = 'All devices are healthy [evidence:E1]\n'
    request['claims'][0]['line_end'] = 1
    assert run(request)['claim_truth'] == 'not_checked'
    assert run(request)['verdict'] == 'pass'


@pytest.mark.parametrize('field,value,code', [
    ('scope_id', 'another-review', 'evidence_scope_mismatch'),
    ('source', 'bgp', 'evidence_source_snapshot_or_hash_mismatch'),
    ('snapshot_id', 's2', 'evidence_source_snapshot_or_hash_mismatch'),
    ('body', '{"fabricated":true}', 'evidence_source_snapshot_or_hash_mismatch'),
    ('body', '', 'empty_evidence_body'),
])
def test_forged_stale_or_cross_scope_payload(field, value, code):
    request = fixture()
    envelope = json.loads(request['evidence'][0]['text'])
    envelope[field] = value
    request['evidence'][0]['text'] = json.dumps(envelope)
    result = run(request)
    assert result['verdict'] == 'fail'
    assert code in codes(result)
    assert 'claim_uses_invalid_evidence' in codes(result)


@pytest.mark.parametrize('kind,expected', [
    ('unmapped', 'unmapped_report_line'), ('missing', 'claim_without_evidence'),
    ('unknown', 'unknown_evidence_reference'), ('mismatch', 'claim_citation_set_mismatch'),
    ('malformed', 'malformed_citation'), ('duplicate_inline', 'duplicate_inline_citation'),
])
def test_claim_coverage_and_reference_failures(kind, expected):
    request = fixture()
    if kind == 'unmapped':
        request['report_text'] += 'Unreviewed fact\n'
    elif kind == 'missing':
        request['claims'][0]['evidence'] = []
    elif kind == 'unknown':
        request['claims'][0]['evidence'] = ['E2']
        request['report_text'] = request['report_text'].replace('E1', 'E2')
    elif kind == 'mismatch':
        request['report_text'] = request['report_text'].replace('[evidence:E1]', '')
    elif kind == 'malformed':
        request['report_text'] += '[evidence:unsafe/id]\n'
        request['claims'][0]['line_end'] = 4
    else:
        request['report_text'] += '[evidence:E1]\n'
        request['claims'][0]['line_end'] = 4
    result = run(request)
    assert result['verdict'] == 'fail'
    assert expected in codes(result)
    if kind == 'unmapped':
        assert result['coverage'] == 'partial'


@pytest.mark.parametrize('kind,error', [
    ('duplicate_id', 'duplicate_evidence_id'),
    ('duplicate_payload', 'duplicate_evidence_identity'),
    ('duplicate_claim', 'duplicate_claim_id'),
    ('duplicate_ref', 'duplicate_claim_evidence'),
    ('overlap', 'overlapping_claim_ranges'),
    ('duplicate_source', 'duplicate_source_identity'),
    ('latest', 'exact_snapshot_required'),
    ('body_object', 'evidence_body_must_be_original_text'),
    ('unknown_field', 'invalid_object_fields'),
    ('bool_range', 'invalid_claim_line_range'),
])
def test_invalid_contracts(kind, error):
    request = fixture()
    if kind.startswith('duplicate_id') or kind == 'duplicate_payload':
        request['evidence'].append(copy.deepcopy(request['evidence'][0]))
        if kind == 'duplicate_payload':
            request['evidence'][-1]['id'] = 'E2'
    elif kind == 'duplicate_claim':
        request['claims'].append(copy.deepcopy(request['claims'][0]))
    elif kind == 'duplicate_ref':
        request['claims'][0]['evidence'] *= 2
    elif kind == 'overlap':
        request['claims'].append({**request['claims'][0], 'id': 'C2'})
    elif kind == 'duplicate_source':
        request['context']['sources'] *= 2
    elif kind == 'latest':
        request['context']['sources'][0]['snapshot_id'] = 'latest'
    elif kind == 'body_object':
        envelope = json.loads(request['evidence'][0]['text'])
        envelope['body'] = {'rows': []}
        request['evidence'][0]['text'] = json.dumps(envelope)
    elif kind == 'bool_range':
        request['claims'][0]['line_start'] = True
    else:
        request['claim_truth'] = True
    with pytest.raises(CitationError, match=error):
        run(request)


def invoke(payload, project):
    return subprocess.run([sys.executable, '-m', 'olav_skills', 'run', 'reporter', 'lint_report'],
                          input=payload, capture_output=True, timeout=10,
                          env={**os.environ, 'OLAV_HOME': str(project)})


def test_real_cli_and_duplicate_json_and_wire_budget(tmp_path):
    process = invoke(json.dumps(fixture()).encode(), tmp_path)
    assert process.returncode == 0, process.stderr
    assert json.loads(process.stdout)['verdict'] == 'pass'
    for payload, expected in ((b'{"context":{},"context":{}}', 'duplicate_json_key'),
                              (b'x' * (MAX_REQUEST + 1), 'request_byte_budget_exceeded'),
                              (b'null', 'invalid_object_fields'),
                              (b'{"x":NaN}', 'invalid_json_number')):
        process = invoke(payload, tmp_path)
        assert process.returncode == 1
        assert json.loads(process.stdout)['error_type'] == expected
        assert not process.stderr


def test_files_fixed_scope_hash_and_symlink_refusal(tmp_path):
    directory = tmp_path / 'exports/evidence_sources'
    directory.mkdir(parents=True)
    request = fixture()
    (directory / 'report.md').write_text(request.pop('report_text'))
    (directory / 'evidence.json').write_text(request['evidence'][0].pop('text'))
    request.update(project=str(tmp_path), report_path='exports/evidence_sources/report.md')
    request['evidence'][0]['path'] = 'exports/evidence_sources/evidence.json'
    assert run(request)['verdict'] == 'pass'
    (directory / 'report.md').unlink()
    (directory / 'report.md').symlink_to(directory / 'evidence.json')
    process = invoke(json.dumps(request).encode(), tmp_path)
    assert process.returncode == 1
    assert json.loads(process.stdout)['status'] == 'error'
    request['report_path'] = '../escape.md'
    assert json.loads(invoke(json.dumps(request).encode(), tmp_path).stdout)['error_type'] == 'invalid_source_path'


def test_issue_budget_and_direct_input_bounds():
    request = fixture()
    request['report_text'] += 'unmapped\n' * 100
    result = run(request)
    assert result['issue_count'] == 100
    assert len(result['issues']) == 64
    assert result['issues_truncated'] is True
    request['report_text'] = 'x' * (64 * 1024 + 1)
    with pytest.raises(CitationError, match='input_byte_budget_exceeded'):
        run(request)


@pytest.mark.parametrize('alias', ['CURRENT', 'newest', 'All'])
def test_other_implicit_snapshot_aliases(alias):
    request = fixture()
    request['context']['sources'][0]['snapshot_id'] = alias
    with pytest.raises(CitationError, match='exact_snapshot_required'):
        run(request)


def test_same_bytes_distinct_sources_are_allowed():
    request = fixture()
    second = json.loads(request['evidence'][0]['text'])
    second['snapshot_id'] = 's2'
    request['context']['sources'].append({**request['context']['sources'][0], 'snapshot_id': 's2'})
    request['evidence'].append({'id': 'E2', 'text': json.dumps(second)})
    request['report_text'] += 'Same returned bytes in s2 [evidence:E2]\n'
    request['claims'].append({'id': 'C2', 'line_start': 4, 'line_end': 4, 'evidence': ['E2']})
    assert run(request)['verdict'] == 'pass'


def test_crlf_body_and_report_original_hashes_preserved():
    request = fixture()
    payload = json.loads(request['evidence'][0]['text'])
    payload['body'] = payload['body'].replace('\n', '\r\n')
    request['evidence'][0]['text'] = json.dumps(payload)
    assert run(request)['verdict'] == 'fail'
    request['context']['sources'][0]['sha256'] = hashlib.sha256(payload['body'].encode()).hexdigest()
    request['report_text'] = request['report_text'].replace('\n', '\r\n')
    result = run(request)
    assert result['verdict'] == 'pass'
    assert result['report_sha256'] == hashlib.sha256(request['report_text'].encode()).hexdigest()
