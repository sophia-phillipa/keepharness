"""Resolve omitted execution fields without overriding an explicit client choice."""
from . import maestro

AUTO = (None, '', 'auto')

def resolve(config, data):
    defaults = config.get('mcp_defaults') or {}
    backend, model, effort = (data.get(k) for k in ('backend', 'model', 'effort'))
    if all(v not in AUTO for v in (backend, model, effort)):
        return None  # Existing assessment validates explicit requests.
    if not defaults and all(v in AUTO for v in (backend, model, effort)):
        return None  # Keep existing automatic/Maestro selection.
    if backend == 'maestro':
        return None
    choices = maestro.candidates(config, data.get('project_id'), bool(data.get('file_ids') or data.get('workspace_id')))
    if data.get('workspace_id'):
        choices = [m for m in choices if m['permissions'].get('read')]
    if backend not in AUTO:
        choices = [m for m in choices if m['backend'] == backend]
    if model not in AUTO:
        choices = [m for m in choices if m['model'] == model]
    elif defaults and backend in (*AUTO, defaults.get('backend')):
        choices = [m for m in choices if m['backend'] == defaults['backend'] and m['model'] == defaults['model']]
    if not choices:
        raise ValueError('default_or_requested_executor_not_available_for_task')
    choice = choices[0]
    if effort in AUTO:
        same = all(choice[k] == defaults.get(k) for k in ('backend', 'model'))
        effort = defaults.get('effort') if same else None
        if effort in AUTO:
            effort = 'low' if 'low' in choice['efforts'] else choice['efforts'][0]
    if effort not in choice['efforts']:
        raise ValueError('default_or_requested_effort_not_available')
    return {**data, 'backend': choice['backend'], 'model': choice['model'], 'effort': effort}
