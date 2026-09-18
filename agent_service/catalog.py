"""Public catalog metadata from the selected project only; never execute source text."""
from pathlib import Path
import tomllib

def catalog(config, project):
    root = Path(project['root']).resolve() if project.get('root') else None
    result = {'agents': [], 'skills': [], 'warnings': [],
              'scope': 'Projeto selecionado e motores configurados neste serviço; não é um inventário global da máquina.'}
    for provider,service in config.get('services',{}).items():
        if service.get('enabled'):result['agents'].append({'name':provider,'description':', '.join(service.get('models',[])),'source':'Configuração do serviço','status':'Configurado'})
    if root is None:return result
    def safe(path):
        try:
            relative = path.relative_to(root)
            return not any(root.joinpath(*relative.parts[:i]).is_symlink() for i in range(1, len(relative.parts)+1)) and path.resolve().is_relative_to(root)
        except (ValueError, OSError):
            return False
    def read(path):
        if not safe(path) or not path.is_file(): return None
        with path.open('rb') as stream: data = stream.read(65537)
        if len(data)>65536: raise ValueError('metadata_too_large')
        return data.decode('utf-8')
    def frontmatter(text):
        lines=text.splitlines()
        if not lines or lines[0].strip()!='---': return {}
        fields={}; key=None
        for line in lines[1:]:
            if line.strip()=='---': break
            if line.startswith((' ', '\t')) and key:
                fields[key]+=' '+line.strip()
            elif ':' in line:
                key,value=line.split(':',1); key=key.strip()
                if key not in ('name','description'): key=None; continue
                fields[key]=value.strip().strip('\"\'')
                if fields[key] in ('>','|','>-','|-'):fields[key]=''
            else: key=None
        return fields
    for kind, folders in (('skills', ('skills','.agents/skills','.codex/skills')), ('agents',('agents','.codex/agents','.agents/agents'))):
        for folder in folders:
            base=root/folder
            if not safe(base) or not base.is_dir(): continue
            try:
                for index,path in enumerate(base.iterdir()):
                    if index>=200:
                        result['warnings'].append('Catálogo limitado a 200 entradas por pasta.');break
                    if not safe(path): continue
                    if kind=='skills': path=path/'SKILL.md'
                    elif path.suffix not in ('.toml','.md'):continue
                    try:
                        text=read(path)
                        if text is None:continue
                        meta=tomllib.loads(text) if path.suffix=='.toml' else frontmatter(text)
                        name=meta.get('name') or (path.parent.name if kind=='skills' else path.stem)
                        description=meta.get('description') or 'Descrição não informada no arquivo.'
                        result[kind].append({'name':str(name)[:160], 'description':str(description)[:2000],
                            'source':str(path.relative_to(root)), 'status':'Disponível no projeto'})
                    except (OSError,ValueError):
                        result['warnings'].append('Não foi possível ler metadados de '+str(path.relative_to(root)))
            except OSError: result['warnings'].append('Não foi possível consultar '+folder)
    return result
