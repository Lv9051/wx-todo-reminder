"""在 Windows 构建干净的免安装发行包，不包含开发者数据。"""
import hashlib
import importlib.metadata
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from runtime_paths import VERSION


def build(output=None):
    output=Path(output).resolve() if output else ROOT/'dist'
    subprocess.check_call([sys.executable,'-m','PyInstaller','--noconfirm','--clean',
                           '--distpath',str(output),str(ROOT/'WXTodoReminder.spec')],cwd=ROOT)
    package=output/f'QiangxinTodo-v{VERSION}-Windows-x64.zip'
    licenses={}
    for name in ('pystray','Pillow','uiautomation','comtypes','pywin32','six','pyinstaller','pywebview','pythonnet','clr_loader','cffi','bottle','proxy_tools','pycparser'):
        distribution=importlib.metadata.distribution(name)
        for file in distribution.files or []:
            if any(word in file.name.lower() for word in ('license','licence','copying','notice')):
                path=distribution.locate_file(file)
                if path.is_file() and path.suffix.lower() not in ('.py','.pyc','.pyd','.dll'):
                    licenses[f'强芯待办/LICENSES/{name}/{file.name}']=path
    for target,prefix,program in ((package,'WXTodoReminder','WXTodoReminder.exe'),):
        with zipfile.ZipFile(target,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as z:
            z.write(output/'WXTodoReminder.exe',prefix+'/'+program)
            for name in ('README.md','LICENSE','THIRD_PARTY_NOTICES.md'):
                z.write(ROOT/name,prefix+'/'+name)
            z.write(ROOT/'vendor/wechatauto-replica/LICENSE',prefix+'/LICENSES/wechatauto-replica/LICENSE')
            python_license=Path(sys.base_prefix)/'LICENSE.txt'
            if python_license.exists():
                z.write(python_license,prefix+'/LICENSES/Python/LICENSE.txt')
            for name,path in licenses.items():z.write(path,name.replace('强芯待办/',prefix+'/',1))
            contents=z.namelist()
            assert not any('/data/' in n or '/docs/' in n or n.endswith(('.db','.log')) or 'AGENTS' in n for n in contents)
    sums=[]
    for file in (package,output/'WXTodoReminder.exe'):
        sums.append(hashlib.sha256(file.read_bytes()).hexdigest()+'  '+file.name)
    (output/'SHA256SUMS.txt').write_text('\n'.join(sums)+'\n',encoding='utf8')
    print(f'Release ready: {package} ({package.stat().st_size//1024//1024} MB)')


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',type=Path)
    build(parser.parse_args().output)
