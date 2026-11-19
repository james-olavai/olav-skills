"""Deterministic recorded-evidence checks. Never executes or approves a plan."""
from __future__ import annotations

import ipaddress
import json
import os
import re
import sys
from pathlib import Path

from olav_skills.tools.sql_budget import SqlBudget, SqlBudgetError, run_bounded_query

MAX_INPUT = 98304
MAX_PLAN = 65536
MAX_OUTPUT = 262144
MAX_FINDINGS = 512
BUDGET = SqlBudget(1000, 4000, 262144, 5000, '256MB', 1)
COMMANDS = {'show ip interface brief', 'show ipv6 interface', 'show interfaces terse'}
PLACEHOLDER = re.compile(r'<[^<>\n]{1,128}>|\{\{[^{}\n]{1,128}\}\}|\b(?:TODO|TBD|CHANGEME)\b', re.I)


class PrecheckError(ValueError):
    pass


def _text(value, name):
    if not isinstance(value, str) or not value.strip() or len(value) > 256:
        raise PrecheckError('invalid_' + name)
    return value


def _scoped(root, relative):
    rel = Path(relative)
    if rel.is_absolute() or '..' in rel.parts:
        raise PrecheckError('path_out_of_scope')
    current = root
    for part in rel.parts:
        current = current / part
        if current.is_symlink():
            raise PrecheckError('symlink_refused')
    if not current.resolve().is_relative_to(root):
        raise PrecheckError('path_out_of_scope')
    return current


def _load(request, root):
    if ('plan' in request) == ('plan_path' in request):
        raise PrecheckError('provide_exactly_one_plan')
    if 'plan_path' in request:
        name = _text(request['plan_path'], 'plan_path')
        if not Path(name).is_relative_to('exports/change_plans'):
            raise PrecheckError('path_out_of_scope')
        path = _scoped(root, name)
        with path.open('rb') as stream:
            raw = stream.read(MAX_PLAN + 1)
        if len(raw) > MAX_PLAN:
            raise PrecheckError('plan_budget_exceeded')
        text = raw.decode('utf-8')
        return json.loads(text) if path.suffix == '.json' else text
    plan = request['plan']
    if len(json.dumps(plan, ensure_ascii=False).encode()) > MAX_PLAN:
        raise PrecheckError('plan_budget_exceeded')
    return plan


def _address(value):
    if not isinstance(value, str) or '%' in value:
        raise ValueError('invalid_address')
    parsed = ipaddress.ip_interface(value)
    return parsed.ip, parsed.network, '/' in value


def _source_address(record):
    """Canonical TextFSM lower-case keys plus explicit upper-case compatibility.

    Mixed aliases must agree. No case-folding, guessed fields or VRF defaults.
    """
    values = {}
    for field in ('ip_address', 'prefix_length', 'vrf'):
        upper = field.upper()
        if field in record and upper in record and record[field] != record[upper]:
            raise ValueError('conflicting_' + field + '_aliases')
        values[field] = record[field] if field in record else record.get(upper)
    value = values['ip_address']
    if not isinstance(value, str):
        raise ValueError('missing_or_unsupported_ip_address')
    if value.lower() in {'unassigned', 'none', 'n/a', '-', '--', ''}:
        return None
    prefix = values['prefix_length']
    if prefix is not None and prefix != '':
        if isinstance(prefix, bool) or not isinstance(prefix, (str, int)) or not str(prefix).isdigit():
            raise ValueError('invalid_prefix_length')
        if '/' in value:
            parsed = ipaddress.ip_interface(value)
            if parsed.network.prefixlen != int(prefix):
                raise ValueError('inconsistent_embedded_prefix')
        else:
            value += '/' + str(prefix)
    vrf = values['vrf']
    if vrf is not None and not isinstance(vrf, str):
        raise ValueError('unsupported_vrf_type')
    ip, network, prefix_known = _address(value)
    return ip, network, prefix_known, vrf


def check_change_plan(request):
    """Check explicit project/snapshot; return checks, never an execution permit."""
    if not isinstance(request, dict) or set(request) - {'project', 'snapshot_id', 'plan', 'plan_path'}:
        raise PrecheckError('invalid_request')
    if len(json.dumps(request, ensure_ascii=False).encode()) > MAX_INPUT:
        raise PrecheckError('input_budget_exceeded')
    if os.environ.get('OLAV_SQL_TEMP_DIR'):
        raise PrecheckError('sql_temp_directory_not_supported')
    root = Path(_text(request.get('project') or os.environ.get('OLAV_HOME'), 'project')).expanduser().resolve(strict=True)
    sid = _text(request.get('snapshot_id'), 'snapshot_id')
    if sid.lower() in {'latest', 'newest', 'auto'}:
        raise PrecheckError('explicit_snapshot_required')
    plan = _load(request, root)
    checks = []

    def add(name, status, reason, **evidence):
        if len(checks) >= MAX_FINDINGS:
            raise PrecheckError('findings_budget_exceeded')
        checks.append({'check': name, 'status': status, 'reason': reason, 'evidence': evidence})

    text = plan if isinstance(plan, str) else json.dumps(plan, ensure_ascii=False)
    add('placeholders', 'fail' if PLACEHOLDER.search(text) else 'pass',
        'unresolved_marker_found' if PLACEHOLDER.search(text) else 'no_supported_marker_found')
    if isinstance(plan, str):
        if not plan.strip():
            raise PrecheckError('empty_plan')
        add('markdown_structure', 'pass' if all(x in plan for x in ('#### `', '**Rollback:**', '**Post-checks:**')) else 'fail',
            'generator_sections_only_not_semantics')
        for name in ('targets', 'platform', 'rollback', 'verification', 'addresses'):
            add(name, 'unknown', 'markdown_semantics_not_extracted')
        source = {'status': 'not_read', 'reason': 'markdown_structure_only'}
    else:
        if not isinstance(plan, dict) or plan.get('schema') != 'olav.change-plan/v1':
            raise PrecheckError('invalid_plan_schema')
        targets = plan.get('targets')
        if not isinstance(targets, list) or not 1 <= len(targets) <= 32:
            raise PrecheckError('invalid_targets')
        names = []
        total_steps = 0
        for target in targets:
            if not isinstance(target, dict):
                raise PrecheckError('invalid_target')
            names.append(_text(target.get('device'), 'device'))
            for field in ('steps', 'rollback'):
                values = target.get(field, [])
                if not isinstance(values, list) or any(not isinstance(v, str) or not v.strip() for v in values):
                    raise PrecheckError('invalid_' + field)
                total_steps += len(values)
            ver = target.get('verification', [])
            if not isinstance(ver, list):
                raise PrecheckError('invalid_verification')
            total_steps += len(ver)
            if not isinstance(target.get('addresses', []), list) or len(target.get('addresses', [])) > 32:
                raise PrecheckError('invalid_addresses')
        if total_steps > 256:
            raise PrecheckError('steps_budget_exceeded')
        if len(set(names)) != len(names):
            raise PrecheckError('duplicate_targets')
        db = _scoped(root, '.olav/databases/main.duckdb')
        rows = []
        try:
            query = run_bounded_query(db,
                'SELECT device_name, platform, command, CAST(parsed_data AS VARCHAR) AS parsed_data '
                'FROM netops.parsed_outputs WHERE snapshot_id=? ORDER BY device_name, command', [sid], BUDGET)
            source = {'status': 'ok', 'table': 'netops.parsed_outputs', 'returned_rows': query.returned_rows,
                      'truncated': query.truncated, 'has_more': query.has_more}
            rows = query.rows
            complete = not (query.truncated or query.has_more or query.cell_truncated)
        except SqlBudgetError as exc:
            complete = False
            source = {'status': 'error', 'error_type': exc.error_type, 'table': 'netops.parsed_outputs'}
        add('source', 'pass' if complete and rows else 'unknown',
            'bounded_snapshot_records' if complete and rows else 'missing_empty_error_or_truncated_source', source=source)
        observed = []
        address_issues = {}

        def address_issue(reason):
            address_issues[reason] = address_issues.get(reason, 0) + 1

        for row in rows:
            if row['command'] not in COMMANDS:
                continue
            try:
                parsed = json.loads(row['parsed_data'])
            except (TypeError, ValueError):
                address_issue('invalid_parsed_json')
                continue
            if not isinstance(parsed, list):
                address_issue('unsupported_parsed_layout')
                continue
            if len(parsed) > 1000:
                address_issue('parsed_record_limit')
            for record in parsed[:1000]:
                if not isinstance(record, dict):
                    address_issue('unsupported_record_type')
                    continue
                try:
                    address = _source_address(record)
                except ValueError as exc:
                    reason = str(exc)
                    address_issue(reason if reason in {
                        'conflicting_ip_address_aliases', 'conflicting_prefix_length_aliases',
                        'conflicting_vrf_aliases', 'missing_or_unsupported_ip_address',
                        'invalid_prefix_length', 'inconsistent_embedded_prefix', 'unsupported_vrf_type'
                    } else 'invalid_ip_or_cidr')
                    continue
                if address is None:
                    continue
                ip, network, prefix_known, vrf = address
                observed.append((row['device_name'], row['command'], ip, network, prefix_known, vrf))
                if len(observed) > 1000:
                    raise PrecheckError('address_source_budget_exceeded')
        add('address_source_fields', 'unknown' if address_issues or not observed else 'pass',
            'supported_fields_with_unknown_records' if address_issues else ('supported_field_observations_only_not_coverage' if observed else 'no_supported_field_observations'),
            issues=address_issues, observed_address_records=len(observed))
        proposed = []
        for target in targets:
            name = target['device']
            member = [r for r in rows if r['device_name'] == name]
            add('target', 'pass' if member else ('fail' if complete and rows else 'unknown'),
                'observed_in_snapshot' if member else 'not_observed_in_returned_snapshot', device=name)
            platforms = {r['platform'] for r in member if r['platform']}
            expected = target.get('platform')
            known = complete and len(platforms) == 1 and all(r['platform'] for r in member)
            add('platform', ('pass' if expected in platforms else 'fail') if known else 'unknown',
                'historical_platform_comparison' if known else 'historical_platform_not_established',
                device=name, observed=sorted(platforms), requested=expected)
            add('template', 'pass' if target.get('template') == 'cisco_ios' and expected == 'cisco_ios' else 'unknown',
                'template_identity_only_not_syntax' if target.get('template') == 'cisco_ios' and expected == 'cisco_ios' else 'unsupported_or_unknown_template', device=name)
            for field in ('steps', 'rollback'):
                add(field, 'pass' if target.get(field) else 'fail', 'nonempty_text_only_not_cli_validation', device=name)
            ver = target.get('verification', [])
            valid = bool(ver) and all(isinstance(v, dict) and isinstance(v.get('command'), str) and bool(v['command'].strip())
                                     and isinstance(v.get('expected'), str) and bool(v['expected'].strip()) for v in ver)
            add('verification', 'pass' if valid else 'fail', 'command_and_expected_required_text_only', device=name)
            for entry in target.get('addresses', []):
                if not isinstance(entry, dict):
                    raise PrecheckError('invalid_address_entry')
                try:
                    ip, network, prefix_known = _address(entry.get('address'))
                except ValueError:
                    add('address_format', 'fail', 'invalid_ip_or_cidr', device=name)
                    continue
                proposed.append((name, 'plan', ip, network, prefix_known, entry.get('vrf')))
        for i, candidate in enumerate(proposed):
            name, _, ip, network, prefix_known, vrf = candidate
            for other in observed + proposed[:i]:
                host, command, other_ip, other_net, other_prefix, other_vrf = other
                if ip.version != other_ip.version:
                    continue
                duplicate = ip == other_ip
                overlap = prefix_known and other_prefix and network.overlaps(other_net)
                if duplicate or overlap:
                    same_vrf = isinstance(vrf, str) and bool(vrf) and isinstance(other_vrf, str) and vrf == other_vrf
                    different_vrf = isinstance(vrf, str) and bool(vrf) and isinstance(other_vrf, str) and bool(other_vrf) and vrf != other_vrf
                    add('address_overlap', 'pass', 'recorded_address_intersection_detected_not_conflict',
                        device=name, address=str(ip), other_device=host, other_address=str(other_ip),
                        command=command, kind='duplicate' if duplicate else 'prefix_overlap',
                        vrf_relation='same' if same_vrf else ('different' if different_vrf else 'unknown'))
        add('address_safety', 'unknown', 'coverage_vrf_anycast_intent_and_collection_time_not_established', observed_address_records=len(observed))
    for name in ('cli_syntax', 'reachability', 'simulation', 'collection_time', 'deployment_approval'):
        add(name, 'unknown', 'not_provided_by_this_checker')
    statuses = {c['status'] for c in checks}
    result = {'schema': 'olav.plan-precheck/v1', 'execution_status': 'ok', 'decision': 'not_approved',
              'checks_outcome': 'fail' if 'fail' in statuses else ('unknown' if 'unknown' in statuses else 'pass'),
              'scope': {'project': str(root), 'snapshot_id': sid, 'membership_source': 'netops.parsed_outputs',
                        'collection_time': None, 'format': 'markdown' if isinstance(plan, str) else 'structured'},
              'source': source, 'checks': checks,
              'limits': {'input_bytes': MAX_INPUT, 'plan_bytes': MAX_PLAN, 'targets': 32, 'steps': 256,
                         'findings': MAX_FINDINGS, 'output_bytes': MAX_OUTPUT, 'sql': BUDGET.as_dict()},
              'limitations': ['No command execution or syntax validation', 'No full-source conflict-free claim',
                              'Snapshot labels are not collection time; inventory is not historical membership']}
    if len(json.dumps(result, ensure_ascii=False).encode()) > MAX_OUTPUT:
        raise PrecheckError('output_budget_exceeded')
    return result


def main():
    try:
        raw = sys.stdin.buffer.read(MAX_INPUT + 1)
        if len(raw) > MAX_INPUT:
            raise PrecheckError('input_budget_exceeded')
        result = check_change_plan(json.loads(raw))
        code = 0
    except (PrecheckError, ValueError, OSError, RecursionError, TypeError) as exc:
        result = {'schema': 'olav.plan-precheck/v1', 'execution_status': 'error', 'decision': 'not_approved',
                  'checks_outcome': 'unknown', 'error': str(exc) if isinstance(exc, PrecheckError) else 'invalid_request_or_unavailable_plan'}
        code = 2
    print(json.dumps(result, ensure_ascii=False))
    return code


if __name__ == '__main__':
    raise SystemExit(main())
