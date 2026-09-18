"""Read connector metadata without returning secret environment values."""
import json
from pathlib import Path
import tomllib

def configurations():
    home=Path.home();result={'codex':{},'claude':{}}
    try:result['codex']=tomllib.loads((home/'.codex/config.toml').read_text()).get('mcp_servers',{})
    except (OSError,ValueError):pass
    try:result['claude']=json.loads((home/'.claude.json').read_text()).get('mcpServers',{})
    except (OSError,ValueError):pass
    return result

def inventory():
    configs=configurations();result={p:[] for p in configs}
    for provider,servers in configs.items():
        for name,spec in servers.items():
            result[provider].append({'id':'mcp:'+name,'name':name,'kind':'mcp','transport':'http' if spec.get('url') else 'stdio','status':'configured'})
    home=Path.home()
    try:
        plugins=tomllib.loads((home/'.codex/config.toml').read_text()).get('plugins',{})
        result['codex'] += [{'id':'plugin:'+name,'name':name,'kind':'plugin','status':'installed'} for name,spec in plugins.items() if isinstance(spec,dict)]
    except (OSError,ValueError):pass
    try:
        plugins=json.loads((home/'.claude/settings.json').read_text()).get('enabledPlugins',{})
        result['claude'] += [{'id':'plugin:'+name,'name':name,'kind':'plugin','status':'installed'} for name in plugins]
    except (OSError,ValueError):pass
    result['local']=result['codex']
    return result
