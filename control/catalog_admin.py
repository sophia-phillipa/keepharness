"""Local owner catalog provisioning, immutable pins and cross-project drift."""
import asyncio
import copy
import re
import secrets
import time
from pathlib import Path

from agent_service.catalog_drift import compare_resources
from agent_service.catalog_manifest import hooks_digest, load_manifest, materialize_runtime, preflight
from agent_service.catalog_pin import effective_catalogs, pin_catalog, preview_update
from agent_service.resources import discover


def catalog_config(manager):
    return {**manager.settings, 'state_dir': str(manager.state / 'runs'),
            'control_state_dir': str(manager.state),
            'projects': {'sem-projeto': {}, **{item['id']: item for item in manager.settings['projects']}}}


def _current_digest(root):
    try:
        return hooks_digest(root)
    except (OSError, ValueError):
        return None


def hooks_trust_record(incoming, saved, root):
    """The stored hook digest to keep for a catalog being saved.

    A plain re-save keeps the stored digest and never recomputes it. A digest is computed only
    for a new catalog or a changed root; a posted digest counts only if it matches the files now.
    A catalog stored without one (saved before hook trust) stays without it until re-trusted.
    """
    current = _current_digest(root)
    if saved is None or str(Path(str(saved.get('root', ''))).expanduser().resolve()) != root:
        return {'hooks_sha256': current} if current else {}
    posted = incoming.get('hooks_sha256')
    value = posted if current is not None and posted == current else saved.get('hooks_sha256')
    return {'hooks_sha256': value} if isinstance(value, str) and re.fullmatch(r'[0-9a-f]{64}', value) else {}


def hooks_trust(catalog):
    """Owner-facing state of the stored hook digest for one catalog root."""
    current = _current_digest(catalog['root'])
    if current is None:
        return {'trusted': False, 'message': 'The catalog hooks cannot be read; hooks are skipped until re-trusted.'}
    if catalog.get('hooks_sha256') == current:
        return {'trusted': True, 'message': 'Catalog hooks match the trusted digest.'}
    return {'trusted': False, 'message': 'Catalog hooks changed or are not confirmed yet. They are skipped until you review and re-trust them.'}


def validate_pins(project, catalogs, state):
    pins = project.get('catalog_pins', {})
    if not isinstance(pins, dict) or set(pins) - set(project.get('catalogs', [])):
        raise ValueError('Invalid project catalog pins.')
    if pins:
        if any(not isinstance(pin, dict) or set(pin) != {'root', 'commit'} for pin in pins.values()):
            raise ValueError('Invalid project catalog pins.')
        effective_catalogs({'catalogs': catalogs, 'control_state_dir': str(state)}, project)
    return copy.deepcopy(pins)


def _status(manager):
    config = catalog_config(manager)
    snapshots, checks = {}, []
    for project_id, project in config['projects'].items():
        if not project.get('root'):
            continue
        for catalog in effective_catalogs(config, project):
            manifest = load_manifest(catalog['root'])
            checks.append({'project_id': project_id, 'catalog_id': catalog['id'],
                           'manifest': manifest is not None,
                           'integrations': (manifest or {}).get('integrations', []),
                           'hooks_trust': hooks_trust(catalog) if (manifest or {}).get('allowed_hooks') and not catalog.get('pin') else None,
                           'preflight': preflight(catalog['root'], manifest, manager.state, catalog['id']) if manifest else []})
        items = {}
        for backend in ('codex', 'claude', 'gemini'):
            for item in discover(config, project_id, backend, execution_mode='native')['items']:
                items[item['resource_id']] = item
        snapshots[project_id] = list(items.values())
    return {'catalogs': manager.settings.get('catalogs', []), 'projects': manager.settings['projects'],
            'preflight': checks, 'drift': [item for item in compare_resources(snapshots) if item['drift']]}


async def read_catalogs(request, manager):
    return await asyncio.to_thread(_status, manager)


async def change_pin(request, manager, data):
    project_id, catalog_id = data.get('project_id'), data.get('catalog_id')
    draft = copy.deepcopy(manager.settings)
    project = next((item for item in draft['projects'] if item['id'] == project_id), None)
    catalog = next((item for item in draft.get('catalogs', []) if item['id'] == catalog_id), None)
    if project is None or catalog is None or catalog_id not in project.get('catalogs', []):
        raise ValueError('Choose a catalog assigned to this project.')
    action = data.get('action')
    pins = project.setdefault('catalog_pins', {})
    if action == 'preview':
        if catalog_id not in pins:
            raise ValueError('Pin this catalog before checking for an update.')
        result = await asyncio.to_thread(preview_update, catalog, pins[catalog_id], manager.state, data.get('ref'), owner=True)
        previews = getattr(manager, 'catalog_previews', {})
        previews = {key: value for key, value in previews.items() if value['expires'] > time.monotonic()}
        if len(previews) >= 50:
            raise ValueError('Too many pending catalog previews; wait for expiry.')
        token = secrets.token_urlsafe(24)
        previews[token] = {'project_id': project_id, 'catalog_id': catalog_id, 'before': copy.deepcopy(pins[catalog_id]),
                           'root': catalog['root'], 'pin': result['pin'], 'expires': time.monotonic() + 900}
        manager.catalog_previews = previews
        return {**result, 'preview_token': token}
    if manager.busy():
        raise ValueError('Wait for running and queued work before changing catalog provisioning.')
    if action == 'pin':
        if catalog_id in pins:
            raise ValueError('Use Preview update and Move pin for an existing pin.')
        pins[catalog_id] = await asyncio.to_thread(pin_catalog, catalog, manager.state, data.get('ref'), owner=True)
    elif action == 'update':
        token = data.get('preview_token')
        preview = getattr(manager, 'catalog_previews', {}).get(token) if isinstance(token, str) else None
        if (not preview or preview['expires'] <= time.monotonic() or preview['project_id'] != project_id
            or preview['catalog_id'] != catalog_id or preview['before'] != pins.get(catalog_id) or preview['root'] != catalog['root']):
            raise ValueError('Preview is missing or stale. Preview the update again.')
        pins[catalog_id] = await asyncio.to_thread(pin_catalog, catalog, manager.state, preview['pin']['commit'], owner=True)
    elif action == 'retrust':
        digest = await asyncio.to_thread(hooks_digest, catalog['root'])
        catalog['hooks_sha256'] = digest
    elif action == 'provision':
        if manager.running():
            raise ValueError('Stop the harness before provisioning a shared catalog runtime.')
        effective = next(item for item in effective_catalogs(catalog_config(manager), project) if item['id'] == catalog_id)
        manifest = load_manifest(effective['root'])
        if manifest is None:
            raise ValueError('This catalog has no provisioning manifest.')
        await asyncio.to_thread(materialize_runtime, effective['root'], manifest, manager.state, catalog_id)
        manager.audit('catalog_provision')
        return await read_catalogs(request, manager)
    else:
        raise ValueError('Choose pin, preview, update, retrust or provision.')
    await manager.apply_settings(draft)
    if action == 'update':
        manager.catalog_previews.pop(token, None)
    manager.audit('catalog_' + action)
    return await read_catalogs(request, manager)
