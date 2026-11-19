"""Reject mixed-version and unsafe candidate archives without running their code."""
from pathlib import Path
import importlib.util
import io
import tarfile
import zipfile

import pytest
from olav_skills import __version__

SOURCE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('candidate_release_check', SOURCE/'tools/release_check.py')
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)


def contents(*, wheel=True, metadata_version=None, library_version=None):
    root = 'olav_skills/resources/skills/' if wheel else 'skills/'
    blobs = {root+skill+'/SKILL.md': b'name: synthetic\n' for skill in check.SKILLS}
    examples = 'olav_skills/examples/' if wheel else 'examples/'
    blobs[examples+'synthetic_walkthrough.py'] = b'# synthetic archive; never executed\n'
    blobs[examples+'synthetic_2host_bundle/manifest.yaml'] = b'schema_version: 1\n'
    init = 'olav_skills/__init__.py' if wheel else 'src/olav_skills/__init__.py'
    blobs[init] = f'__version__ = {library_version or __version__!r}\n'.encode()
    metadata = (f'Metadata-Version: 2.4\nName: olav-skills\nVersion: {metadata_version or __version__}\n'
                'Description-Content-Type: text/markdown\nLicense-File: LICENSE\n'
                'Project-URL: Repository, https://github.com/james-olavai/olav-skills\n'
                'Project-URL: Issues, https://github.com/james-olavai/olav-skills/issues\n'
                + ''.join('Requires-Dist: '+name+'\n' for name in
                          ['duckdb','pyyaml','pydantic','textfsm','ntc-templates','networkx','netutils','netconan'])+'\n')
    name = f'olav_skills-{__version__}.dist-info/METADATA' if wheel else 'PKG-INFO'
    blobs[name] = metadata.encode()
    return blobs


def archive(tmp_path, blobs, *, wheel=True, duplicate=False):
    path = tmp_path/(f'olav_skills-{__version__}-py3-none-any.whl' if wheel
                     else f'olav_skills-{__version__}.tar.gz')
    if wheel:
        with zipfile.ZipFile(path,'w') as output:
            for name,data in blobs.items(): output.writestr(name,data)
            if duplicate:
                with pytest.warns(UserWarning): output.writestr(next(iter(blobs)),b'duplicate')
    else:
        with tarfile.open(path,'w:gz') as output:
            for name,data in blobs.items():
                info=tarfile.TarInfo(f'olav_skills-{__version__}/'+name);info.size=len(data)
                output.addfile(info,io.BytesIO(data))
    return path


@pytest.mark.parametrize('wheel',[True,False])
def test_candidate_metadata_filename_namespace_and_library_agree(tmp_path,wheel):
    result=check.inspect(archive(tmp_path,contents(wheel=wheel),wheel=wheel))
    assert result['ok'] and result['version']==__version__


@pytest.mark.parametrize('wheel',[True,False])
@pytest.mark.parametrize('field',['metadata_version','library_version'])
def test_mixed_version_rejected(tmp_path,wheel,field):
    path=archive(tmp_path,contents(wheel=wheel,**{field:'0.0.0'}),wheel=wheel)
    with pytest.raises(AssertionError):check.inspect(path)


def test_mislabeled_archive_rejected(tmp_path):
    path=archive(tmp_path,contents());wrong=path.with_name('olav_skills-0.0.0-py3-none-any.whl');path.rename(wrong)
    with pytest.raises(AssertionError,match='filename/version'):check.inspect(wrong)


@pytest.mark.parametrize('bad',['olav_skills/.env','olav_skills/.olav/main.duckdb','other_pkg/payload.py'])
def test_private_or_foreign_members_rejected(tmp_path,bad):
    blobs=contents();blobs[bad]=b'synthetic'
    with pytest.raises(AssertionError):check.inspect(archive(tmp_path,blobs))


def test_duplicate_member_rejected(tmp_path):
    with pytest.raises(AssertionError,match='duplicate'):check.inspect(archive(tmp_path,contents(),duplicate=True))
