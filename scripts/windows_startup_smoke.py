"""Native read-only diagnostics and controlled Internet-zone regression.

Only the isolated smoke copy receives a synthetic ADS; no customer file or
real security configuration is modified. Always restore that one test stream.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

from qa_parser.startup_diagnostics import run, customer_command


def main():
    p=argparse.ArgumentParser();p.add_argument('--exe',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    a=p.parse_args();out=a.out.resolve();out.mkdir(parents=True,exist_ok=False)
    exe=a.exe.resolve();root=exe.parent;runtime=root/'_internal/pythonnet/runtime/Python.Runtime.dll'
    env=dict(os.environ, PYTHONNET_RUNTIME='netfx')
    def resolve():
        code='from clr_loader import get_netfx; import sys; get_netfx().get_assembly(sys.argv[1]).get_function("Python.Runtime.Loader.Initialize"); print("RESOLVED")'
        return subprocess.run([sys.executable,'-c',code,str(runtime)],env=env,capture_output=True,text=True,timeout=30)
    baseline=run(root,out/'baseline.json')
    assert baseline['assembly_probe']['status']=='loaded' and baseline['assembly_probe']['initialize_found'],baseline
    assert all(row['matches_v031'] for row in baseline['files']),baseline
    assert resolve().returncode==0
    stream=Path(str(runtime)+':Zone.Identifier')
    assert not stream.exists(),'Do not modify pre-existing provenance'
    try:
        stream.write_text('[ZoneTransfer]\nZoneId=3\nHostUrl=https://PRIVATE_INVALID.invalid/SECRET\n',encoding='ascii')
        marked=run(root,out/'marked.json')
        assert marked['assembly_probe']['status']=='failed',marked
        assert any(e['hresult']=='0x80131515' for e in marked['assembly_probe']['errors']),marked
        failed=resolve()
        assert failed.returncode!=0 and 'Failed to resolve Python.Runtime.Loader.Initialize' in failed.stderr
        assert 'SECRET' not in (out/'marked.json').read_text(encoding='utf-8-sig')
        # Frozen diagnostic must work even while the GUI bridge cannot load.
        target=out/'frozen-marked.json'
        result=subprocess.run([str(exe),'--diagnose-startup',str(target)],timeout=40)
        assert result.returncode==0 and json.loads(target.read_text(encoding='utf-8-sig'))['assembly_probe']['status']=='failed'
        # The normal frozen startup must repair only verified package provenance
        # and open its real native window without any customer intervention.
        target=out/'automatic-window.json'
        result=subprocess.run([str(exe),'--desktop-smoke',str(target)],timeout=90)
        assert result.returncode==0 and target.is_file(), 'Automatic recovery/window failed'
        recovery_path=Path(os.environ['LOCALAPPDATA'])/'QA Parser/diagnostics/startup-recovery.json'
        recovery=json.loads(recovery_path.read_text(encoding='utf-8'))
        assert recovery['status']=='recovered_verified_internet_mark',recovery
        assert recovery['changed']==['_internal/pythonnet/runtime/Python.Runtime.dll'],recovery
        assert not stream.exists()
        assert all(row['matches_v031'] for row in recovery['after']['files'])
    finally:
        stream.unlink(missing_ok=True)
    restored=run(root,out/'restored.json')
    assert restored['assembly_probe']['status']=='loaded' and resolve().returncode==0
    # A self-contained .cmd works with Windows PowerShell alone, without app launch.
    tool=root/'windows-startup-diagnostic.cmd';tool.write_bytes(customer_command().encode('ascii'))
    result=subprocess.run(['cmd.exe','/d','/c',str(tool)],stdin=subprocess.DEVNULL,capture_output=True,timeout=40)
    assert result.returncode==0
    standalone=Path(os.environ['LOCALAPPDATA'])/'QA Parser/diagnostics/startup-diagnostic.json'
    assert json.loads(standalone.read_text(encoding='utf-8-sig'))['assembly_probe']['status']=='loaded'
    report={'status':'passed','baseline':baseline,'marked':marked,'restored':restored,
            'outer_error_reproduced':True,'standalone_command':'passed','frozen_diagnostic':'passed',
            'automatic_recovery':recovery,
            'customer_root_cause':'unconfirmed_without_customer_report'}
    (out/'summary.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print('STARTUP_DIAGNOSTIC_REPORT '+json.dumps(report,ensure_ascii=False),flush=True)

if __name__=='__main__':main()
