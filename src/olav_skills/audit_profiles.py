"""Run a small reviewed audit profile through fixed installed capabilities."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import sys
import tempfile
import time

from . import __version__

SCHEMA = 'olav.audit/v1'
PROFILE_SCHEMA = 'olav.audit-profile/v1'
CHECKS = {'inventory_membership':'inventory','interface_states':'interfaces','bgp_states':'bgp','ospf_states':'ospf'}
MAX_INPUT = 8192
MAX_PROFILE = 16384
MAX_OUTPUT = 65536
MAX_CHILD = 40000
MAX_TIMEOUT_MS = 18000
LABEL = re.compile(r'[A-Za-z][A-Za-z0-9_-]{0,63}\Z')
HASH = re.compile(r'[a-f0-9]{64}\Z')


def _rule(check):
    return {'id':check,'check':check,'severity':'info' if check=='inventory_membership' else 'warning','max_concerns':0}


PROFILES = {name:{'schema':PROFILE_SCHEMA,'id':name,'version':'1','rules':[_rule(c) for c in checks]}
            for name,checks in {'baseline':('inventory_membership','interface_states','bgp_states'),'inventory':('inventory_membership',),'interfaces':('interface_states',),'bgp':('bgp_states',),'ospf':('ospf_states',)}.items()}


def _wire(value):
    return json.dumps(value,sort_keys=True,ensure_ascii=False,allow_nan=False,separators=(',',':')).encode()


def _hash(value):
    return hashlib.sha256(value).hexdigest()


def _pairs(pairs):
    out={}
    for k,v in pairs:
        if k in out:raise ValueError('duplicate JSON key')
        out[k]=v
    return out


def _load(raw):
    def invalid(_):raise ValueError('nonfinite number')
    try:return json.loads(raw,object_pairs_hook=_pairs,parse_constant=invalid)
    except RecursionError as exc:raise ValueError('JSON nesting exceeds supported depth') from exc


def _error(kind):
    return {'schema':SCHEMA,'status':'error','completed':False,'overall':'unknown','error_type':kind,'rules':[],
            'collection_coverage':'unknown','live_state':'unknown'}


def _project():
    value=os.environ.get('OLAV_HOME')
    if not value or not Path(value).is_absolute():raise ValueError('absolute OLAV_HOME required')
    root=Path(value)
    if root.resolve()!=root or not root.is_dir() or any(p.is_symlink() for p in (root,*root.parents)):
        raise ValueError('canonical project directory required')
    return root


def _read_profile(root,relative):
    if not isinstance(relative,str):raise ValueError('profile_path must be a relative JSON file')
    parts=relative.split('/')
    if len(parts)!=3 or parts[:2]!=['.olav','audit_profiles'] or not parts[2].endswith('.json') or not LABEL.fullmatch(parts[2][:-5]):
        raise ValueError('profile_path must be .olav/audit_profiles/NAME.json')
    if not hasattr(os,'O_NOFOLLOW'):raise ValueError('nofollow paths unavailable')
    flags=os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW
    directory=os.open('/',flags)
    try:
        for part in (*root.parts[1:],*parts[:2]):
            child=os.open(part,flags,dir_fd=directory);os.close(directory);directory=child
        fd=os.open(parts[2],os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=directory)
        with os.fdopen(fd,'rb') as stream:
            info=os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or info.st_size>MAX_PROFILE:raise ValueError('profile must be bounded regular single-link JSON')
            data=stream.read(MAX_PROFILE+1)
        if len(data)>MAX_PROFILE:raise ValueError('profile byte budget exceeded')
        return _load(data),_hash(data)
    finally:os.close(directory)


def _validate_profile(profile):
    if not isinstance(profile,dict) or set(profile)!={'schema','id','version','rules'} or profile['schema']!=PROFILE_SCHEMA:
        raise ValueError('unsupported profile schema/keys')
    if not isinstance(profile['id'],str) or not LABEL.fullmatch(profile['id']):raise ValueError('invalid profile id')
    if not isinstance(profile['version'],str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,31}',profile['version']):raise ValueError('invalid profile version')
    rules=profile['rules']
    if not isinstance(rules,list) or not 1<=len(rules)<=3:raise ValueError('profile requires 1..3 fixed checks')
    ids=set();checks=set()
    for rule in rules:
        if not isinstance(rule,dict) or set(rule)!={'id','check','severity','max_concerns'}:raise ValueError('unsupported rule keys')
        if not isinstance(rule['id'],str) or not LABEL.fullmatch(rule['id']) or rule['id'] in ids:raise ValueError('invalid/duplicate rule id')
        check=rule['check']
        if not isinstance(check,str) or check not in CHECKS or check in checks:raise ValueError('unsupported/duplicate check')
        if rule['severity'] not in ('info','warning','critical'):raise ValueError('invalid severity')
        n=rule['max_concerns']
        if type(n) is not int or not 0<=n<=1000000:raise ValueError('max_concerns must be an integer 0..1000000')
        ids.add(rule['id']);checks.add(check)
    return profile


def _arguments(request):
    allowed={'operation','profile','profile_path','snapshot_id','devices','vendors','limit','timeout_ms','evidence_check'}
    if not isinstance(request,dict) or set(request)-allowed:raise ValueError('unsupported request keys')
    operation=request.get('operation','run')
    if operation=='list':
        if set(request)!={'operation'}:raise ValueError('list accepts operation only')
        return None
    if operation!='run' or ('profile' in request and 'profile_path' in request):raise ValueError('invalid operation/profile selector')
    sid=request.get('snapshot_id')
    if not isinstance(sid,str) or not sid or sid!=sid.strip() or len(sid.encode('utf-8'))>256 or not sid.isprintable() or sid.lower() in ('latest','current','newest','all'):raise ValueError('exact snapshot_id required')
    args={'snapshot_id':sid,'limit':request.get('limit',5)}
    if type(args['limit']) is not int or not 1<=args['limit']<=5:raise ValueError('limit must be 1..5')
    for key in ('devices','vendors'):
        values=request.get(key,[])
        if not isinstance(values,list) or len(values)>20 or any(not isinstance(v,str) or not v or len(v)>256 for v in values):raise ValueError('filters must contain at most 20 exact strings')
        args[key]=list(dict.fromkeys(values))
    timeout=request.get('timeout_ms',MAX_TIMEOUT_MS)
    if type(timeout) is not int or not 1<=timeout<=MAX_TIMEOUT_MS:raise ValueError('timeout_ms must be 1..18000')
    return args,timeout


def _transport(args,root,remaining,script,schema):
    if remaining<=0:raise TimeoutError('audit deadline')
    env={'PATH':str(Path(sys.executable).parent)+':/usr/bin:/bin','LANG':'C.UTF-8','OLAV_HOME':str(root),'PYTHONDONTWRITEBYTECODE':'1','PYTHONNOUSERSITE':'1'}
    argv=[sys.executable,'-I','-B','-m','olav_skills','run','analyzer',script]
    with tempfile.TemporaryDirectory(prefix='olav-audit-child-') as scratch:
        env['TMPDIR']=scratch
        with tempfile.TemporaryFile() as output,tempfile.TemporaryFile() as errors:
            process=subprocess.Popen(argv,stdin=subprocess.PIPE,stdout=output,stderr=errors,env=env,cwd=root,start_new_session=True)
            try:
                process.communicate(_wire(args),timeout=remaining)
            except subprocess.TimeoutExpired:
                try:os.killpg(process.pid,signal.SIGKILL)
                except ProcessLookupError:pass
                process.communicate()
                raise TimeoutError('audit deadline') from None
            if os.fstat(output.fileno()).st_size>MAX_CHILD or os.fstat(errors.fileno()).st_size>MAX_CHILD:
                raise ValueError('child output budget')
            output.seek(0);raw=output.read(MAX_CHILD+1)
    result=_load(raw)
    if process.returncode!=0 or not isinstance(result,dict) or result.get('status')!='success' or result.get('schema')!=schema:
        raise ValueError('fixed capability failed')
    if result.get('effective_request')!=args:raise ValueError('capability scope mismatch')
    _source_hash(result)
    return result


def _source_hash(result):
    schema=result.get('schema')
    if schema not in ('olav.investigation/v1','olav.ospf-analysis/v1','olav.evidence-readiness/v1'):
        raise ValueError('unsupported evidence schema')
    block=result.get('source_version')
    if not isinstance(block,dict):raise ValueError('missing source version')
    value=block.get('database_sha256' if schema=='olav.investigation/v1' else 'sha256')
    if not isinstance(value,str) or not HASH.fullmatch(value):raise ValueError('missing source version')
    return value


def _invoke(args,root,remaining):
    if args['recipe']=='ospf':
        from .ospf_analysis import OSPFError, _request
        try:
            request=_request(_wire({k:v for k,v in args.items() if k!='recipe'}|{
                'mode':'inspect','timeout_ms':min(15000,max(1,int(remaining*1000)))}))
        except OSPFError:
            raise ValueError('invalid OSPF request') from None
        return _transport(request,root,remaining,'analyze_ospf','olav.ospf-analysis/v1')
    result=_transport(args,root,remaining,'run_investigation','olav.investigation/v1')
    if result.get('snapshot_id')!=args['snapshot_id']:raise ValueError('capability scope mismatch')
    return result


def _precheck_request(request,args):
    from .evidence_readiness import EvidenceError, _request
    block=request['evidence_check']
    if not isinstance(block,dict) or set(block)!={'requirements','as_of','max_age_seconds'}:
        raise ValueError('explicit evidence policy required')
    if args['vendors']:raise ValueError('evidence precheck does not infer vendor membership')
    try:
        expected=_request(_wire({'snapshot_id':args['snapshot_id'],**block}))
    except EvidenceError:
        raise ValueError('invalid evidence policy') from None
    if args['devices'] and any(item['device'] not in args['devices'] for item in expected['requirements']):
        raise ValueError('evidence requirement outside audit device scope')
    return expected


def _invoke_evidence(expected,root,remaining):
    result=_transport(expected,root,remaining,'check_evidence','olav.evidence-readiness/v1')
    if result.get('readiness') not in ('ready','not_ready','unknown'):raise ValueError('invalid readiness')
    summary=result.get('summary');count=len(expected['requirements'])
    if not isinstance(summary,dict) or type(summary.get('requirement_count')) is not int or summary['requirement_count']!=count:
        raise ValueError('evidence denominator mismatch')
    states=[summary.get(k) for k in ('ready','not_ready','unknown')]
    if not all(type(n) is int and n>=0 for n in states) or sum(states)!=count:
        raise ValueError('evidence outcome counts mismatch')
    derived='not_ready' if summary['not_ready'] else 'unknown' if summary['unknown'] else 'ready'
    if result['readiness']!=derived:raise ValueError('evidence outcome mismatch')
    if derived=='ready' and any(type(summary.get(k)) is not int or summary[k]!=count for k in ('raw_verified','parsed_nonempty','fresh')):
        raise ValueError('incomplete evidence cannot admit audit')
    return result



def _evaluate(rule,result):
    if rule['check']=='ospf_states':
        counts=result.get('summary');state='unknown';metrics=None
        keys=('full_observed','two_way_unknown','transitional_observed','unknown')
        if isinstance(counts,dict) and all(type(counts.get(k)) is int and counts[k]>=0 for k in ('record_count',*keys)) and sum(counts[k] for k in keys)==counts['record_count']:
            metrics=counts
            if result.get('analysis_status')=='observed' and result.get('reasons')==[]:
                state='concern' if counts['transitional_observed']>rule['max_concerns'] else 'unknown' if counts['two_way_unknown'] or counts['unknown'] or not counts['record_count'] else 'pass'
        return {**rule,'state':state,'metrics':metrics,'evidence':result,
                'meaning':'Selected recorded OSPF states only; transitional observations need review, 2-Way lacks context, and pass is not network health.'}
    steps=result.get('steps')
    if not isinstance(steps,list) or not steps or not isinstance(steps[0],dict):raise ValueError('invalid evidence steps')
    step=steps[0];counts=step.get('counts')
    metrics=None;state='unknown'
    if step.get('status')=='success' and isinstance(counts,dict):
        if rule['check']=='inventory_membership':
            observed=counts.get('observed_device_count');concerns=step.get('observed_without_catalog_count');unknown=0
            if all(type(n) is int and n>=0 for n in (observed,concerns)) and concerns<=observed:
                metrics={'observed_device_count':observed,'uncatalogued_device_count':concerns,'unknown_record_count':None}
                state='concern' if concerns>rule['max_concerns'] else 'pass' if observed else 'unknown'
        else:
            observed=counts.get('record_count');concerns=counts.get('nonhealthy_record_count');unknown=counts.get('unknown_record_count');healthy=counts.get('healthy_record_count')
            if all(type(n) is int and n>=0 for n in (observed,concerns,unknown,healthy)) and concerns+unknown+healthy==observed:
                metrics=counts
                state='concern' if concerns>rule['max_concerns'] else 'unknown' if unknown or not observed else 'pass'
    return {**rule,'state':state,'metrics':metrics,'evidence':result,
            'meaning':'Rule applied to selected recorded observations only; pass is not network health or collection completeness.'}


def run_audit(request):
    try:
        if len(_wire(request))>MAX_INPUT:raise ValueError('input budget exceeded')
        validated=_arguments(request)
        if validated is None:return {'schema':SCHEMA,'status':'success','profiles':list(PROFILES.values()),'data_read':False}
        args,timeout=validated;root=_project()
        if 'profile_path' in request:
            profile,file_hash=_read_profile(root,request['profile_path']);origin={'kind':'project_file','path':request['profile_path'],'file_sha256':file_hash}
        else:
            name=request.get('profile','baseline')
            if not isinstance(name,str) or name not in PROFILES:raise ValueError('unsupported builtin profile')
            profile=_load(_wire(PROFILES[name]));origin={'kind':'builtin','id':name}
        _validate_profile(profile)
        expected=_precheck_request(request,args) if 'evidence_check' in request else None
        deadline=time.monotonic()+timeout/1000;rows=[];version=None;partial=False;precheck=None;skip_reason=None
        if expected is not None:
            try:
                precheck=_invoke_evidence(expected,root,deadline-time.monotonic())
                version=_source_hash(precheck)
                if precheck['readiness']!='ready':skip_reason='evidence_not_ready'
            except TimeoutError:skip_reason='evidence_timeout'
            except (ValueError,OSError):skip_reason='evidence_capability_failed'
            if skip_reason:partial=True
        for rule in profile['rules']:
            if skip_reason:
                rows.append({**rule,'state':'not_executed','reason':skip_reason,'metrics':None});continue
            if time.monotonic()>=deadline:
                rows.append({**rule,'state':'not_executed','reason':'deadline','metrics':None});partial=True;continue
            effective={**args,'recipe':CHECKS[rule['check']]}
            try:
                evidence=_invoke(effective,root,deadline-time.monotonic())
                current=_source_hash(evidence)
                if version is not None and version!=current:return _error('source_version_changed')
                version=current
                rows.append(_evaluate(rule,evidence))
            except TimeoutError:
                rows.append({**rule,'state':'not_executed','reason':'timeout','metrics':None});partial=True
            except (ValueError,OSError):
                rows.append({**rule,'state':'unknown','reason':'capability_failed','metrics':None});partial=True
        overall='unknown' if partial else 'concerns' if any(r['state']=='concern' for r in rows) else 'unknown' if any(r['state']!='pass' for r in rows) else 'clear_for_observed_checks'
        result={'schema':SCHEMA,'status':'partial' if partial else 'success','completed':not partial,'overall':overall,
                'profile':profile,'profile_source':origin,'profile_sha256':_hash(_wire(profile)),
                'package_version':__version__,'executor_sha256':_hash(Path(__file__).read_bytes()),
                'scope':args,'source_version':version,'rules':rows,'collection_coverage':'unknown','live_state':'unknown',
                'limits':{'rules':3,'rows_per_check':args['limit'],'timeout_ms':timeout,'output_bytes':MAX_OUTPUT}}
        if expected is not None:result.update(evidence_check=precheck,evidence_requirements=expected)
        if len(_wire(result))>MAX_OUTPUT:return _error('output_budget_exceeded')
        return result
    except (ValueError,TypeError,OSError,RecursionError):return _error('validation_error')


def main():
    try:
        raw=sys.stdin.buffer.read(MAX_INPUT+1)
        if len(raw)>MAX_INPUT:raise ValueError('input budget exceeded')
        result=run_audit(_load(raw))
    except (ValueError,TypeError,RecursionError):result=_error('validation_error')
    print(_wire(result).decode())
    return 0 if result['status']=='success' else 1
