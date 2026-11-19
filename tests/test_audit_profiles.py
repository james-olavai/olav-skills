"""Profile policies have independent count truth; subprocess path has native tests."""
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import pytest
from olav_skills import audit_profiles as m


@pytest.fixture
def project(tmp_path,monkeypatch):
    monkeypatch.setenv('OLAV_HOME',str(tmp_path))
    return tmp_path


def response(args,*,version='a'*64,missing=False,observed=3,concerns=0,unknown=0,uncatalogued=0):
    if args['recipe']=='inventory':
        step={'operation':'snapshot_summary','status':'success','counts':{'observed_device_count':observed,'inventory_device_count':4,'record_count':observed},'observed_without_catalog_count':uncatalogued}
    else:
        step={'operation':args['recipe']+'_records','status':'unknown' if missing else 'success','counts':None if missing else {'record_count':observed,'healthy_record_count':observed-concerns-unknown,'nonhealthy_record_count':concerns,'unknown_record_count':unknown}}
    return {'schema':'olav.investigation/v1','status':'success','source_version':{'database_sha256':version},'snapshot_id':args['snapshot_id'],'effective_request':args,'steps':[step],'temporal':{'collected_at':{'status':'unknown','value':None}}}


def req(**kwargs):return {'snapshot_id':'capture-a',**kwargs}


def test_list_does_not_read_data_or_require_project(monkeypatch):
    monkeypatch.delenv('OLAV_HOME',raising=False)
    out=m.run_audit({'operation':'list'})
    assert out['status']=='success' and not out['data_read'] and len(out['profiles'])==5


def test_fixed_rules_with_known_concern_and_unknown(project,monkeypatch):
    calls=[]
    def invoke(args,root,remaining):
        calls.append(args['recipe'])
        return response(args,uncatalogued=1) if args['recipe']=='inventory' else response(args,unknown=1) if args['recipe']=='bgp' else response(args)
    monkeypatch.setattr(m,'_invoke',invoke)
    out=m.run_audit(req())
    assert out['status']=='success' and out['completed'] and out['overall']=='concerns'
    assert [r['state'] for r in out['rules']]==['concern','pass','unknown']
    assert calls==['inventory','interfaces','bgp']
    assert out['collection_coverage']=='unknown' and out['live_state']=='unknown'


@pytest.mark.parametrize('recipe,kwargs',[('interfaces',{'missing':True}),('bgp',{'unknown':1}),('interfaces',{'observed':0})])
def test_missing_zero_or_unknown_not_pass(project,monkeypatch,recipe,kwargs):
    monkeypatch.setattr(m,'_invoke',lambda a,*_:response(a,**kwargs))
    out=m.run_audit(req(profile=recipe))
    assert out['overall']=='unknown' and out['rules'][0]['state']=='unknown'
    assert out['rules'][0]['metrics'] is None if kwargs.get('missing') else out['rules'][0]['metrics'] is not None


def test_source_versions_cannot_be_joined(project,monkeypatch):
    calls=[]
    def invoke(a,*_):
        calls.append(a)
        return response(a,version=('a' if len(calls)==1 else 'b')*64)
    monkeypatch.setattr(m,'_invoke',invoke)
    out=m.run_audit(req())
    assert out['status']=='error' and out['error_type']=='source_version_changed' and out['rules']==[]


def test_failed_capability_and_unexecuted_deadline(project,monkeypatch):
    monkeypatch.setattr(m,'_invoke',lambda *_: (_ for _ in ()).throw(ValueError('failure')))
    out=m.run_audit(req(profile='bgp'))
    assert out['status']=='partial' and not out['completed'] and out['overall']=='unknown'
    assert out['rules'][0]['metrics'] is None
    monkeypatch.setattr(m,'_invoke',lambda *_: (_ for _ in ()).throw(TimeoutError()))
    assert m.run_audit(req(profile='bgp'))['rules'][0]['state']=='not_executed'


def write_profile(project,profile):
    p=project/'.olav/audit_profiles/team.json';p.parent.mkdir(parents=True,exist_ok=True)
    p.write_text(json.dumps(profile))
    return p


def test_custom_threshold_records_the_explicit_policy(project,monkeypatch):
    definition=copy.deepcopy(m.PROFILES['interfaces']);definition['id']='team';definition['rules'][0]['max_concerns']=2
    file=write_profile(project,definition);before=file.read_bytes()
    monkeypatch.setattr(m,'_invoke',lambda a,*_:response(a,concerns=2))
    out=m.run_audit(req(profile_path='.olav/audit_profiles/team.json'))
    assert out['rules'][0]['state']=='pass' and out['overall']=='clear_for_observed_checks'
    assert out['profile_source']['file_sha256']==hashlib.sha256(before).hexdigest()
    assert file.read_bytes()==before
    monkeypatch.setattr(m,'_invoke',lambda a,*_:response(a,concerns=3))
    assert m.run_audit(req(profile_path='.olav/audit_profiles/team.json'))['rules'][0]['state']=='concern'


@pytest.mark.parametrize('changes',[{'sql':'select 1'},{'snapshot_id':'latest'},{'limit':True},{'limit':6},{'timeout_ms':0},{'timeout_ms':True},{'devices':'r1'},{'profile':'missing'},{'profile':'bgp','profile_path':'.olav/audit_profiles/a.json'},{'profile_path':'../../.env'},{'operation':'schedule'}])
def test_invalid_requests_do_not_call_capability(project,monkeypatch,changes):
    def forbidden(*_):pytest.fail('must not call capability')
    monkeypatch.setattr(m,'_invoke',forbidden)
    assert m.run_audit(req(**changes))['status']=='error'


@pytest.mark.parametrize('mutate',[lambda p:p['rules'].append(p['rules'][0]),lambda p:p['rules'][0].update(sql='SELECT 1'),lambda p:p['rules'][0].update(check='shell'),lambda p:p['rules'][0].update(max_concerns=True),lambda p:p.update(schema='other')])
def test_unreviewed_profile_shapes_rejected(project,monkeypatch,mutate):
    profile=copy.deepcopy(m.PROFILES['interfaces']);mutate(profile);write_profile(project,profile)
    monkeypatch.setattr(m,'_invoke',lambda *_:pytest.fail('must reject before invoke'))
    assert m.run_audit(req(profile_path='.olav/audit_profiles/team.json'))['status']=='error'


def test_profile_path_file_limits_and_links(project,monkeypatch,tmp_path):
    p=write_profile(project,m.PROFILES['bgp'])
    other=project/'other.json';other.write_bytes(p.read_bytes());p.unlink();p.symlink_to(other)
    assert m.run_audit(req(profile_path='.olav/audit_profiles/team.json'))['status']=='error'
    p.unlink();os.link(other,p)
    assert m.run_audit(req(profile_path='.olav/audit_profiles/team.json'))['status']=='error'
    p.unlink();p.write_bytes(b'x'*(m.MAX_PROFILE+1))
    assert m.run_audit(req(profile_path='.olav/audit_profiles/team.json'))['status']=='error'


def test_output_budget_withholds_overflow(project,monkeypatch):
    monkeypatch.setattr(m,'_invoke',lambda a,*_:response(a))
    monkeypatch.setattr(m,'MAX_OUTPUT',64)
    out=m.run_audit(req())
    assert out['status']=='error' and out['error_type']=='output_budget_exceeded' and out['rules']==[]


def test_bad_counter_contract_cannot_pass(project,monkeypatch):
    monkeypatch.setattr(m,'_invoke',lambda a,*_:response(a,observed=1,concerns=3))
    out=m.run_audit(req(profile='interfaces'))
    assert out['rules'][0]['state']=='unknown' and out['rules'][0]['metrics'] is None


def test_real_native_import_and_fixed_child_capabilities(tmp_path,monkeypatch):
    root=tmp_path/'native';root.mkdir();monkeypatch.setenv('OLAV_HOME',str(root))
    env={**os.environ,'OLAV_HOME':str(root),'PYTHONDONTWRITEBYTECODE':'1'}
    fixture=Path(__file__).parent/'fixtures/synthetic_2host_bundle'
    imported=subprocess.run([sys.executable,'-B','-m','olav_skills','run','importer','ingest_snapshot'],input=json.dumps({'path':str(fixture)}),text=True,capture_output=True,env=env)
    assert imported.returncode==0,(imported.stdout,imported.stderr)
    sid=json.loads(imported.stdout)['snapshot_id'];db=root/'.olav/databases/main.duckdb';before=hashlib.sha256(db.read_bytes()).hexdigest()
    out=m.run_audit({'snapshot_id':sid,'devices':['R2']})
    assert out['status']=='success',out
    assert [r['state'] for r in out['rules']]==['pass','concern','concern']
    assert out['source_version']==before==hashlib.sha256(db.read_bytes()).hexdigest()
    assert all(r['evidence']['effective_request']['devices']==['R2'] for r in out['rules'])
    child=subprocess.run([sys.executable,'-B','-m','olav_skills','run','reporter','run_audit'],input=json.dumps({'snapshot_id':sid,'profile':'interfaces','devices':['R2']}),text=True,capture_output=True,env=env)
    assert child.returncode==0,(child.stdout,child.stderr)
    assert json.loads(child.stdout)['rules'][0]['metrics']['nonhealthy_record_count']==1
    assert hashlib.sha256(db.read_bytes()).hexdigest()==before


def test_real_timeout_is_bounded_and_does_not_mutate(project):
    start=time.monotonic();out=m.run_audit(req(timeout_ms=1))
    assert out['status']=='partial' and not out['completed'] and time.monotonic()-start<3
    assert any(r['state']=='not_executed' for r in out['rules'])
    assert not (project/'.olav').exists()



@pytest.mark.parametrize('failure',[ValueError('failed'),TimeoutError('deadline')])
def test_partial_after_known_concern_keeps_overall_unknown(project,monkeypatch,failure):
    def invoke(a,*_):
        if a['recipe']=='inventory':return response(a,uncatalogued=1)
        raise failure
    monkeypatch.setattr(m,'_invoke',invoke)
    out=m.run_audit(req())
    assert out['status']=='partial' and not out['completed'] and out['overall']=='unknown'
    assert out['rules'][0]['state']=='concern' and out['rules'][0]['metrics']['uncatalogued_device_count']==1


def ospf_response(args, *, full=1, two_way=0, attention=0, unknown=0, reasons=None):
    return {'schema':'olav.ospf-analysis/v1','status':'success',
            'analysis_status':'unknown' if reasons else 'observed','reasons':reasons or [],
            'source_version':{'sha256':'a'*64},
            'summary':{'record_count':full+two_way+attention+unknown,'full_observed':full,
                       'two_way_unknown':two_way,'transitional_observed':attention,'unknown':unknown},
            'health':'unknown'}


@pytest.mark.parametrize('values,state',[
    ({},'pass'),({'two_way':1},'unknown'),({'attention':1},'concern'),
    ({'unknown':1},'unknown'),({'full':0},'unknown'),
    ({'reasons':['requested_device_source_missing']},'unknown')])
def test_ospf_profile_observations_do_not_certify_missing_scope(project,monkeypatch,values,state):
    monkeypatch.setattr(m,'_invoke',lambda args,*_:ospf_response(args,**values))
    out=m.run_audit(req(profile='ospf'))
    assert out['status']=='success' and out['rules'][0]['state']==state,out
    assert out['rules'][0]['evidence']['health']=='unknown'
    assert out['collection_coverage']=='unknown'
    assert len(m.PROFILES['baseline']['rules'])==3


def policy():
    return {'requirements':[{'device':'R1','command':'show ip ospf neighbor'}],
            'as_of':'2026-11-18T12:00:00Z','max_age_seconds':60}


def precheck(expected, *, readiness='ready', version='a'*64):
    n=len(expected['requirements'])
    return {'schema':'olav.evidence-readiness/v1','status':'success','effective_request':expected,
            'source_version':{'sha256':version},'readiness':readiness,
            'summary':{'requirement_count':n,'ready':n if readiness=='ready' else 0,
                       'not_ready':n if readiness=='not_ready' else 0,'unknown':n if readiness=='unknown' else 0,
                       'raw_verified':n if readiness=='ready' else 0,
                       'parsed_nonempty':n if readiness=='ready' else 0,'fresh':n if readiness=='ready' else 0}}


@pytest.mark.parametrize('readiness',['unknown','not_ready'])
def test_explicit_evidence_gate_blocks_rules_without_guessing(project,monkeypatch,readiness):
    monkeypatch.setattr(m,'_transport',lambda args,*_:precheck(args,readiness=readiness))
    monkeypatch.setattr(m,'_invoke',lambda *_:pytest.fail('rule must not execute'))
    out=m.run_audit(req(profile='ospf',devices=['R1'],evidence_check=policy()))
    assert out['status']=='partial' and not out['completed'] and out['overall']=='unknown',out
    assert out['rules'][0]['state']=='not_executed' and out['rules'][0]['reason']=='evidence_not_ready'
    assert out['evidence_check']['readiness']==readiness


def test_ready_scope_is_pinned_to_same_database_as_following_rules(project,monkeypatch):
    monkeypatch.setattr(m,'_transport',lambda args,*_:precheck(args))
    monkeypatch.setattr(m,'_invoke',lambda args,*_:ospf_response(args))
    out=m.run_audit(req(profile='ospf',devices=['R1'],evidence_check=policy()))
    assert out['overall']=='clear_for_observed_checks',out
    assert out['evidence_requirements']['snapshot_id']=='capture-a'
    def changed(args,*_):
        result=ospf_response(args);result['source_version']['sha256']='b'*64;return result
    monkeypatch.setattr(m,'_invoke',changed)
    assert m.run_audit(req(profile='ospf',evidence_check=policy()))['error_type']=='source_version_changed'


@pytest.mark.parametrize('change',[
    {'vendors':['cisco_ios']},{'devices':['R2']},
    {'evidence_check':{'requirements':[],'as_of':'2026-11-18','max_age_seconds':60}},
    {'evidence_check':{**policy(),'snapshot_id':'other'}},
    {'evidence_check':{**policy(),'max_age_seconds':True}}])
def test_evidence_policy_invalid_or_outside_scope_never_runs(project,monkeypatch,change):
    monkeypatch.setattr(m,'_transport',lambda *_:pytest.fail('no child expected'))
    monkeypatch.setattr(m,'_invoke',lambda *_:pytest.fail('no rule expected'))
    request=req(profile='ospf',evidence_check=policy());request.update(change)
    out=m.run_audit(request)
    assert out['status']=='error' and out['error_type']=='validation_error',out


@pytest.mark.parametrize('field,value',[
    ('ready',True),('ready',0),('fresh',0),('requirement_count',2),('unknown',1)])
def test_inconsistent_precheck_counts_cannot_admit_audit(project,monkeypatch,field,value):
    def transport(args,*_):
        result=precheck(args);result['summary'][field]=value;return result
    monkeypatch.setattr(m,'_transport',transport)
    monkeypatch.setattr(m,'_invoke',lambda *_:pytest.fail('rule must not execute'))
    out=m.run_audit(req(profile='ospf',evidence_check=policy()))
    assert out['status']=='partial' and out['rules'][0]['reason']=='evidence_capability_failed',out


@pytest.mark.parametrize('fault',['none','scope','schema','hash'])
def test_actual_transport_rejects_unbound_success_payload(project,monkeypatch,fault):
    expected={'snapshot_id':'capture-a',**policy()}
    result=precheck(expected)
    if fault=='scope':result['effective_request']={**expected,'snapshot_id':'other'}
    if fault=='schema':result['schema']='unexpected/v1'
    if fault=='hash':result['source_version']={'sha256':True}
    real_popen=subprocess.Popen
    def fixed_fixture_child(argv,**options):
        code='import json,sys;sys.stdin.buffer.read();print('+repr(json.dumps(result))+')'
        return real_popen([sys.executable,'-B','-c',code],**options)
    monkeypatch.setattr(m.subprocess,'Popen',fixed_fixture_child)
    if fault=='none':assert m._transport(expected,project,3,'check_evidence','olav.evidence-readiness/v1')==result
    else:
        with pytest.raises(ValueError):m._transport(expected,project,3,'check_evidence','olav.evidence-readiness/v1')
