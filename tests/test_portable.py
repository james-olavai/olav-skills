"""Installed-wheel portable export, synthetic failure and knowledge contracts."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from olav_skills import portable


@pytest.fixture
def sources(tmp_path, monkeypatch):
    root = tmp_path / 'resources'
    shutil.copytree(portable.source_root(), root)
    monkeypatch.setattr(portable, 'source_root', lambda: root)
    return root


def test_installed_cli_entrypoint_deterministic_and_original_bytes(tmp_path):
    outputs = [tmp_path / 'one', tmp_path / 'two']
    for out in outputs:
        result = subprocess.run([sys.executable, '-I', '-B', '-m', 'olav_skills.portable', '--out', str(out)],
                                cwd=tmp_path, env={**os.environ, 'OLAV_HOME': str(tmp_path / 'home')},
                                text=True, capture_output=True)
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout)['version'] == '0.1.1'
    catalog = json.loads((outputs[0] / 'catalog.json').read_bytes())
    assert catalog['schema'] == 'olav.portable-skills/v1'
    assert catalog['version'] == 1 and catalog['profile'] == 'portable'
    assert [s['id'] for s in catalog['skills']] == list(portable.SKILLS)
    assert len(catalog['workflows']) == 2
    for workflow in catalog['workflows']:
        assert workflow['execution_status'] == 'metadata-only'
        assert workflow['executable'] is False
        assert workflow['missing_capabilities'] == ['agent:simulator']
        assert 'agent:simulator' in workflow['requires_capabilities']
    assert (outputs[0] / 'LICENSE').read_bytes() == portable.license_bytes()
    left = {p.relative_to(outputs[0]).as_posix(): p.read_bytes() for p in outputs[0].rglob('*') if p.is_file()}
    right = {p.relative_to(outputs[1]).as_posix(): p.read_bytes() for p in outputs[1].rglob('*') if p.is_file()}
    assert left == right
    owned = set()
    for skill in catalog['skills']:
        assert skill['skill_path'] in skill['resources']
        assert all(c.startswith('script:') for c in skill['requires_capabilities'])
        assert not any('olav_query' in c or 'bash' in c for c in skill['requires_capabilities'])
        owned.update(skill['resources'])
    assert owned == {r['path'] for r in catalog['resources']}
    for resource in catalog['resources']:
        path = resource['path']
        original = portable.source_root() / path.removeprefix('skills/')
        assert left[path] == original.read_bytes()
        assert len(left[path]) == resource['bytes'] <= portable.MAX_RESOURCE_BYTES
        assert hashlib.sha256(left[path]).hexdigest() == resource['sha256'] == resource['source']['sha256']
        assert resource['source']['path'] == path


def test_reject_existing_and_noncanonical_outputs(tmp_path):
    for output in ('relative', str(tmp_path) + '/missing/../new', str(tmp_path)):
        with pytest.raises(ValueError):
            portable.export(output)
    link = tmp_path / 'link'
    link.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError):
        portable.export(str(link / 'new'))
    with pytest.raises(ValueError):
        portable.export(str(portable.source_root() / 'new'))


@pytest.mark.parametrize('value', ['/etc/file', '../file', 'a/../b', 'a\\b', 'a:b', 'a//b', 'a/./b', 'a\0b', ''])
def test_safe_path_rejects_ambiguous_or_escaping_paths(value):
    with pytest.raises(ValueError):
        portable.safe_relative(value)


@pytest.mark.parametrize('mutation', ['missing_skill', 'symlink', 'hidden_symlink', 'oversize', 'invalid_front', 'invalid_workflow', 'unexpected_skill', 'unexpected_file'])
def test_invalid_sources_fail_before_output(sources, tmp_path, mutation):
    if mutation == 'missing_skill':
        (sources / 'analyzer/SKILL.md').unlink()
    elif mutation == 'symlink':
        target = sources / 'analyzer/SKILL.md'
        target.unlink()
        target.symlink_to(sources / 'netops/SKILL.md')
    elif mutation == 'hidden_symlink':
        (sources / 'analyzer/__pycache__').mkdir(exist_ok=True)
        (sources / 'analyzer/__pycache__/hidden').symlink_to(sources / 'netops/SKILL.md')
    elif mutation == 'oversize':
        (sources / 'analyzer/references/large.md').write_bytes(b'x' * (portable.MAX_RESOURCE_BYTES + 1))
    elif mutation == 'invalid_front':
        (sources / 'analyzer/SKILL.md').write_text('missing frontmatter')
    elif mutation == 'invalid_workflow':
        (sources / 'netops/workflows/change_plan.workflow.yaml').write_text('schema_version: 1\nsteps: []\n')
    elif mutation == 'unexpected_skill':
        (sources / 'secret').mkdir()
    else:
        (sources / 'analyzer/.env').write_text('synthetic, not a credential')
    out = tmp_path / 'not-created'
    with pytest.raises(ValueError):
        portable.export(str(out))
    assert not out.exists()


@pytest.mark.parametrize('link', ['[x](missing.md)', '[x](../../../../outside)', '[x](/etc/passwd)', '[x](%2fetc/passwd)', '[x](file:///etc/passwd)', '[x](javascript:alert)', '[x](//host/file)', '[x](missing.md?x=1)', '[x][unknown]', '[x](<missing.md>)', '[x](broken path.md)'])
def test_dangling_and_unsafe_references_fail_before_output(sources, tmp_path, link):
    path = sources / 'analyzer/SKILL.md'
    path.write_text(path.read_text() + '\n' + link + '\n')
    out = tmp_path / 'not-created'
    with pytest.raises(ValueError):
        portable.export(str(out))
    assert not out.exists()


def test_reference_grammar_supports_external_local_and_code(sources, tmp_path):
    path = sources / 'analyzer/SKILL.md'
    path.write_text(path.read_text() + '''
[local](references/ROUTING_EXPERT_GUIDE.md "title")
[remote](https://example.invalid/guide)
[anchor](#scope)
[mail](mailto:public@example.invalid)
[defined][guide]
[guide]: references/ROUTING_EXPERT_GUIDE.md
`[sample](not-a-real-resource)`
```markdown
[example](not-a-real-resource)
```
''')
    portable.export(str(tmp_path / 'created'))


@pytest.mark.parametrize('limit', ['MAX_TOTAL_BYTES', 'MAX_RESOURCES', 'MAX_ENTRIES', 'MAX_CATALOG_BYTES'])
def test_aggregate_budgets_fail_before_output(sources, tmp_path, monkeypatch, limit):
    monkeypatch.setattr(portable, limit, 1)
    out = tmp_path / 'not-created'
    with pytest.raises(ValueError):
        portable.export(str(out))
    assert not out.exists()
