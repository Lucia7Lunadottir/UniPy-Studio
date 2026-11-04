"""Read-only solution snapshot; measure Roslyn descendants independently of the UI."""
import os, sys, time, tempfile, shutil, argparse
from collections import deque
from pathlib import Path
import xml.etree.ElementTree as ET
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PyQt6.QtCore import QCoreApplication
from studio.lsp import RoslynServer
ET.register_namespace('', 'http://schemas.microsoft.com/developer/msbuild/2003')
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("project", type=Path)
parser.add_argument("--seconds", type=int, default=180)
parser.add_argument("--baseline", action="store_true")
args = parser.parse_args()
source = args.project.resolve()
if not source.is_dir() or not list(source.glob("*.sln")):
    parser.error("project must contain a generated .sln")
app=QCoreApplication([])
def tree_memory(pid):
    ids = {int(pid)}
    infos = []
    for p in Path('/proc').iterdir():
        if not p.name.isdigit(): continue
        try:
            fields = (p/'stat').read_text().rsplit(')',1)[1].split()
            infos.append((int(p.name), int(fields[1]), (p/'comm').read_text().strip()))
        except OSError: pass
    for _ in range(8):
        ids.update(i for i,parent,name in infos if parent in ids)
    return [(i,name,round(memory(i),1)) for i,parent,name in infos if i in ids]

def memory(pid):
    try:return int(Path(f'/proc/{pid}/statm').read_text().split()[1])*os.sysconf('SC_PAGE_SIZE')/1048576
    except (OSError,IndexError):return 0
with tempfile.TemporaryDirectory(prefix='unipy-roslyn-probe-') as tmp:
    root=Path(tmp)
    os.environ['DOTNET_CLI_HOME']=str(root/'dotnet-home')
    os.environ['DOTNET_SKIP_FIRST_TIME_EXPERIENCE']='1'
    os.environ['DOTNET_CLI_TELEMETRY_OPTOUT']='1'
    os.environ['XDG_CACHE_HOME']=str(root/'cache')
    for p in source.glob('*.sln'):shutil.copy2(p,root/p.name)
    for p in source.glob('*.csproj'):
        tree=ET.parse(p)
        for el in tree.iter():
            tag=el.tag.rsplit('}',1)[-1]
            if tag in ('Compile','Analyzer','AdditionalFiles') and el.get('Include'):
                value=el.get('Include').replace('\\','/')
                if not os.path.isabs(value):el.set('Include',str(source/value))
            if tag in ('HintPath','AnalyzerConfigPath') and el.text:
                value=el.text.replace('\\','/')
                if not os.path.isabs(value):el.text=str(source/value)
        tree.write(root/p.name,encoding='utf-8',xml_declaration=True)
    server=RoslynServer(str(root),shutil.which('roslyn-language-server') or str(Path.home()/'.dotnet/tools/roslyn-language-server'))
    if args.baseline:
        server.environment = lambda: {}
        server.configuration = lambda section: None
    logs=deque(maxlen=128);server.log.connect(lambda text:logs.append(text[-2000:]))
    try:
        server.start()
        path=str(source/'Assets/TutorialInfo/Readme.cs')
        text=Path(path).read_text()
        server.sync(path,text)
        start=time.monotonic();last=-1
        while time.monotonic()-start<args.seconds:
            app.processEvents();time.sleep(.02)
            elapsed=int(time.monotonic()-start)
            if elapsed//10!=last:
                last=elapsed//10
                pid=server.conn.proc.processId()
                print('seconds',elapsed,'state',server.state,'client MiB',round(memory(os.getpid()),1),'Roslyn tree',tree_memory(pid),'callbacks',len(server.conn._callbacks),'buffer',len(server.conn._buf),'log messages',len(logs),flush=True)
                if sum(item[2] for item in tree_memory(pid))>2000 or server.state=='failed':break
        print('Recent logs:',list(logs)[-3:],flush=True)
    finally:
        server.stop()
        print('Roslyn stopped, PID',server.conn.proc.processId(),flush=True)
