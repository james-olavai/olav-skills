"""Import/resource boundaries of the maintained standalone source."""
import ast
import importlib.util
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_no_legacy_namespace_imports():
    for tree in ('src', 'skills'):
        for path in (ROOT / tree).rglob('*.py'):
            parsed = ast.parse(path.read_text())
            for node in ast.walk(parsed):
                names = []
                if isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
                    names = [node.module]
                assert all(n.split('.')[0] not in {'olav', 'olav_netops', 'olav_presales'} for n in names), path
    assert not (ROOT / 'runtime').exists()


def test_builtin_data_resolves_inside_package():
    from olav_skills.netops.core.config_files import resolve_netops_init_config
    from olav_skills.netops.core.recipe_files import builtin_recipes_dir
    config = resolve_netops_init_config('discovery_protocols.yaml')
    assert config.is_file()
    import olav_skills.netops as netops_package
    package_data = Path(netops_package.__file__).resolve().parent / 'data'
    assert package_data in config.resolve().parents
    recipes = builtin_recipes_dir()
    assert list(recipes.glob('*.yaml'))
    assert package_data in recipes.resolve().parents


def test_local_export_uses_only_repository_resources(tmp_path):
    spec = importlib.util.spec_from_file_location('export_skills', ROOT / 'tools/export_skills.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    out = tmp_path / 'export'
    names = module.export(out)
    assert names == ['analyzer', 'importer', 'learner', 'netops', 'reporter', 'topology', 'writer']
    assert (out / 'importer/scripts/ingest_snapshot.py').read_bytes() == (ROOT / 'skills/importer/scripts/ingest_snapshot.py').read_bytes()
    with pytest.raises(ValueError):
        module.export(ROOT / 'skills')
    with pytest.raises(ValueError):
        module.export(out)


def test_domain_plugin_discovery_does_not_load_old_platform(monkeypatch):
    import importlib.metadata
    from olav_skills.core import ingest_manager
    groups = []
    def entries(*, group):
        groups.append(group)
        return []
    monkeypatch.setattr(importlib.metadata, 'entry_points', entries)
    monkeypatch.setattr(ingest_manager, '_TABLE_PLUGINS_LOADED', False)
    ingest_manager._load_table_plugins()
    assert groups == ['olav_skills.ingest_tables']


def test_every_script_imports_without_platform(tmp_path):
    import os
    import subprocess
    import sys
    code = '''import runpy, sys
from pathlib import Path
root = Path(sys.argv[1])
for script in sorted(root.glob('*/scripts/*.py')):
    sys.path.insert(0, str(script.parent))
    try:
        runpy.run_path(str(script), run_name='_standalone_import_check')
    finally:
        sys.path.pop(0)
assert not any(n == 'olav' or n.startswith('olav.') or n == 'olav_netops' or n.startswith('olav_netops.') for n in sys.modules)
'''
    env = dict(os.environ, OLAV_HOME=str(tmp_path))
    proc = subprocess.run([sys.executable, '-c', code, str(ROOT / 'skills')],
                          cwd=tmp_path, env=env, text=True, capture_output=True,
                          timeout=60)
    assert proc.returncode == 0, proc.stderr


def test_standard_skill_frontmatter():
    import re
    import yaml
    allowed = {'name', 'description', 'license', 'compatibility', 'metadata', 'allowed-tools'}
    for path in (ROOT / 'skills').glob('*/SKILL.md'):
        _, frontmatter, _ = path.read_text().split('---', 2)
        data = yaml.safe_load(frontmatter)
        assert set(data) <= allowed
        assert re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', data['name'])
        assert data['name'] == path.parent.name and len(data['name']) <= 64
        assert isinstance(data['description'], str) and 0 < len(data['description']) <= 1024
        if 'compatibility' in data:
            assert isinstance(data['compatibility'], str) and len(data['compatibility']) <= 500
            assert 'Python >=3.11' in data['compatibility']
        assert isinstance(data['metadata'], dict)
        assert all(isinstance(k, str) and isinstance(v, str) for k, v in data['metadata'].items())
        from olav_skills import __version__
        assert data['metadata']['runtime'] == f'olav-skills>={__version__}'
        assert 'allowed-tools' not in data


def test_unsupported_model_and_memory_implementations_are_absent():
    forbidden = {'openai', 'sentence_transformers', 'langchain_text_splitters',
                 'langchain', 'langchain_core', 'langgraph', 'deepagents', 'lancedb'}
    for path in (ROOT / 'src').rglob('*.py'):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Import):
                assert not any(a.name.split('.')[0] in forbidden for a in node.names), path
            elif isinstance(node, ast.ImportFrom) and node.module:
                assert node.module.split('.')[0] not in forbidden, path
                assert node.module != 'olav_skills.core.llm', path
    assert not (ROOT / 'src/olav_skills/core/embedder.py').exists()
    assert not (ROOT / 'src/olav_skills/core/memory').exists()
    assert not (ROOT / 'src/olav_skills/netops/core/memory_primer.py').exists()
    from olav_skills.core import utils
    assert not hasattr(utils, 'TextProcessor')
