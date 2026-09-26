"""Bounded, fresh native resource metadata. Discovery never executes templates."""
import hashlib
from itertools import islice
import json
import os
from pathlib import Path
import re
import shlex
import tomllib

MAX_BYTES = 65536
MAX_FILES = 500
ENGINES = {'codex':'codex','local':'codex','deepseek':'codex','claude':'claude','gemini':'gemini'}
NAME = re.compile(r'^[\w.:-]{1,160}$')


class ResourceError(ValueError):
    pass


def read(path):
    with path.open('rb') as stream:
        value=stream.read(MAX_BYTES+1)
    if len(value)>MAX_BYTES:raise ValueError('resource_too_large')
    return value.decode('utf-8')


def markdown(text):
    lines=text.splitlines()
    if not lines or lines[0].strip()!='---':return {},text
    meta={};key=None
    for index,line in enumerate(lines[1:],1):
        if line.strip()=='---':return meta,'\n'.join(lines[index+1:])
        if line.startswith((' ','\t')) and key:
            meta[key]+=' '+line.strip()
        elif ':' in line:
            key,value=line.split(':',1);key=key.strip();meta[key]=value.strip().strip('\"\'')
            if meta[key] in ('|','>','|-','>-'):meta[key]=''
        else:key=None
    raise ValueError('invalid_frontmatter')


def roots(engine):
    home=Path.home()
    if engine=='codex':return Path(os.environ.get('CODEX_HOME',home/'.codex')),[home/'.agents/skills']
    if engine=='claude':return Path(os.environ.get('CLAUDE_CONFIG_DIR',home/'.claude')),[]
    if engine=='gemini':return Path(os.environ.get('GEMINI_CLI_HOME',home))/'.gemini',[home/'.agents/skills']
    raise ValueError('unsupported_resource_engine')


def files(base, boundary, global_roots, kind):
    """Follow skill aliases only inside explicitly known resource roots; cap cycles."""
    pending=[base];seen=set();count=0
    while pending and count<MAX_FILES:
        path=pending.pop();count+=1
        resolved=path.resolve()
        if resolved in seen:continue
        if not any(resolved.is_relative_to(root.resolve()) for root in ([boundary] if boundary else global_roots)):continue
        seen.add(resolved)
        if path.is_dir():
            if kind=='skill' and (path/'SKILL.md').is_file():
                pending.append(path/'SKILL.md')
            else:
                children=list(islice(path.iterdir(),MAX_FILES+1))
                if len(children)>MAX_FILES:raise ResourceError('resource_scan_limit')
                pending.extend(sorted(children,reverse=True))
        elif path.is_file():yield path
    if pending:raise ResourceError('resource_scan_limit')


def discover(config, project_id, backend, model=None, *, private=False, execution_mode=None):
    engine=ENGINES.get(backend)
    result={'engine':engine,'items':[],'warnings':[]}
    if engine is None:
        result['warnings'].append('Choose a concrete engine to query its resources.');return result
    if (execution_mode or config.get('services',{}).get(backend,{}).get('mode'))!='native':
        result['warnings'].append('Native resources require a native-mode execution.');return result
    project=config['projects'][project_id];root=Path(project['root']).resolve() if project.get('root') else None
    global_base,shared=roots(engine)
    sources=[]
    def add(base,scope,origin,boundary):
        sources.extend((base/kind,scope,origin,boundary,kind[:-1]) for kind in ('agents','skills','commands') if not (engine=='codex' and kind=='commands'))
    if root:
        # Nearest project config first, bounded by the Git root when nested.
        ancestors=[root];pending=[]
        for parent in root.parents:
            if (ancestors[-1]/'.git').exists():break
            if parent==Path.home() or parent==Path('/'):break
            pending.append(parent)
            if (parent/'.git').exists():ancestors.extend(pending);break
        for folder in ancestors:
            add(folder/('.'+engine),'project',engine,folder)
            if engine in ('codex','gemini'):
                sources.append((folder/'.agents/skills','project','agents',folder,'skill'))
    add(global_base,'global',engine,None)
    if engine=='codex':sources.append((global_base/'prompts','global','codex',None,'command'))
    sources.extend((p,'global','agents' if p.parent.name=='.agents' else engine,None,'skill') for p in shared)
    global_roots=[p for p,s,_,_,_ in sources if s=='global']
    disabled=set()
    if engine=='codex':
        for path in [global_base/'config.toml',*([root/'.codex/config.toml'] if root else [])]:
            try:
                settings=tomllib.loads(read(path))
                for item in settings.get('skills',{}).get('config',[]):
                    if isinstance(item,dict) and item.get('enabled') is False and item.get('path'):
                        disabled.add(str(Path(item['path']).expanduser().resolve()))
            except FileNotFoundError:pass
            except (ValueError,OSError,TypeError,AttributeError):result['warnings'].append('Could not check the Codex skills configuration.')
    seen=set();agent_names=set();skill_names=set()
    for base,scope,origin,boundary,kind in sources:
        try:
            for path in files(base,boundary,global_roots,kind):
                if kind=='skill' and path.name!='SKILL.md':continue
                if kind!='skill' and path.suffix!=('.toml' if engine=='codex' and kind=='agent' or engine=='gemini' and kind=='command' else '.md'):continue
                canonical=str(path.resolve());key=(kind,canonical)
                if key in seen:continue
                try:
                    text=read(path)
                    meta=tomllib.loads(text) if path.suffix=='.toml' else markdown(text)[0]
                    body=meta.get('prompt','') if kind=='command' and path.suffix=='.toml' else markdown(text)[1] if path.suffix=='.md' else text
                    if not isinstance(body,str):raise ValueError('invalid_resource_body')
                    name=meta.get('name') or (path.parent.name if kind=='skill' else path.stem)
                    if kind=='command':name=str(path.relative_to(base).with_suffix('')).replace(os.sep,':')
                    if not isinstance(name,str) or not NAME.fullmatch(name):raise ValueError('invalid_name')
                    if kind=='agent' and engine=='codex' and not isinstance(meta.get('developer_instructions'),str):raise ValueError('invalid_agent')
                    reason=''
                    if canonical in disabled:reason='Skill disabled in the engine configuration.'
                    if engine=='gemini' and kind in ('agent','skill'):reason='The Gemini adapter still disables agents and skills in this execution.'
                    if engine=='claude' and kind=='agent':reason='The Claude adapter does not yet offer native agent delegation.'
                    if backend in ('local','deepseek') and (scope=='global' or kind=='agent'):
                        reason='This resource is not available in the isolated environment of this executor.'
                    if kind=='skill' and engine=='claude' and name in skill_names:reason='Another skill with this name takes precedence in the engine; rename it to select it.'
                    if kind=='agent' and name in agent_names:reason='Another agent with this name takes precedence in the engine; rename it to select it.'
                    if kind=='command' and (re.search(r'!\{|!`|@\{|\$\{|\$[A-Za-z_]+',body.replace('$ARGUMENTS','')) or (engine=='claude' and re.search(r'^\s*(context|agent|hooks|allowed-tools):',text,re.M))):
                        reason='This command requires native expansion features that are not yet supported.'
                    if kind=='skill' and str(meta.get('user-invocable','true')).lower()=='false':reason='Skill not available for invocation by the user.'
                    identity=hashlib.sha256((engine+'\0'+kind+'\0'+canonical).encode()).hexdigest()
                    item={'id':identity,'revision':hashlib.sha256(text.encode()).hexdigest(),'kind':kind,'name':name,
                          'description':str(meta.get('description',''))[:1000],'scope':scope,'origin':origin,
                          'source':str(path),'selectable':not bool(reason),'unavailable_reason':reason}
                    if private:item.update(_text=text,_body=body,_meta=meta)
                    result['items'].append(item);seen.add(key)
                    if kind=='agent':agent_names.add(name)
                    if kind=='skill':skill_names.add(name)
                except (ValueError,OSError,TypeError):result['warnings'].append('Could not read the resource '+str(path))
                if len(result['items'])>=MAX_FILES:
                    result['warnings'].append('Catalog limited to 500 resources.');return result
        except ResourceError:result['warnings'].append('Read limited to 500 entries in '+str(base))
        except (OSError,RuntimeError):result['warnings'].append('Could not access '+str(base))
    result['items'].sort(key=lambda i:(i['scope']!='project',i['origin'],i['name'].casefold(),i['kind']))
    return result


def resolve(config,data):
    prompt=data.get('prompt','')
    if re.search(r'(?<!\S)(@@|//)[\w:-]*',prompt):raise ResourceError('tail_resources_unavailable')
    selections=data.get('resource_selections',[])
    if not isinstance(selections,list) or len(selections)>20:raise ResourceError('invalid_resource_selections')
    if not selections:return []
    if data.get('workspace_id'):raise ResourceError('resources_unavailable_in_workspace')
    found={i['id']:i for i in discover(config,data['project_id'],data['backend'],data.get('model'),private=True,execution_mode=data.get('execution_mode'))['items']}
    result=[];tokens={}
    for selection in selections:
        if not isinstance(selection,dict) or not all(isinstance(selection.get(key),str) and len(selection[key])<=200 for key in ('id','revision','token')):raise ResourceError('invalid_resource_selections')
        item=found.get(selection.get('id'))
        if item is None or not item['selectable']:raise ResourceError('resource_unavailable')
        if item['revision']!=selection.get('revision'):raise ResourceError('resource_changed')
        token=('@' if item['kind']=='agent' else '/')+item['name']
        if selection.get('token')!=token or not re.search(r'(?<!\S)'+re.escape(token)+r'(?=\s|$)',prompt):raise ResourceError('resource_selection_missing')
        if token in tokens and tokens[token]!=item['id']:raise ResourceError('resource_name_ambiguous')
        tokens[token]=item['id']
        if item not in result:result.append(item)
    return result


def prepare_prompt(prompt,items):
    notes=[];commands={};skills={}
    for item in items:
        name=item['name'];token=('@' if item['kind']=='agent' else '/')+name
        if item['kind']=='agent':
            notes.append('Delegate this task using the native agent '+json.dumps(name)+' defined at '+json.dumps(item['source'])+'. Use actual native delegation, not role-play. If unavailable, report that limitation without claiming delegation.')
        elif item['kind']=='skill':
            if item['origin'] in ('codex','agents'):skills[token]='$'+name
            notes.append('Explicitly invoke the selected skill '+json.dumps(name)+' at '+json.dumps(item['source'])+'. Preserve its native instructions and dependencies; report unavailable tools instead of substituting silently.')
        else:commands[token]=item
    patterns=[]
    if commands:patterns.append(r'(?P<command>'+ '|'.join(map(re.escape,commands))+r')(?=\s|$)(?P<args>[^\n]*)')
    if skills:patterns.append(r'(?P<skill>'+ '|'.join(map(re.escape,skills))+r')(?=\s|$)')
    if patterns:
        def expand(match):
            groups=match.groupdict()
            if groups.get('skill') is not None:return skills[groups['skill']]
            args=groups['args'].strip()
            try:positional=shlex.split(args)
            except ValueError:raise ResourceError('invalid_command_arguments') from None
            def substitute(placeholder):
                if placeholder.group(1) is None:return args
                index=int(placeholder.group(1))-1
                return positional[index] if index<len(positional) else ''
            return re.sub(r'\{\{args\}\}|\$ARGUMENTS|\$([1-9])(?!\d)',substitute,commands[groups['command']]['_body'])
        # Match the original text once; arguments and inserted bodies remain data.
        prompt=re.sub(r'(?<!\S)(?:'+'|'.join(patterns)+')',expand,prompt)
    prompt+=('\n\nEXPLICIT RESOURCE SELECTIONS:\n'+'\n'.join(notes) if notes else '')
    if len(prompt)>150000:raise ResourceError('resource_prompt_limit')
    return prompt
