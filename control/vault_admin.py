"""Owner-only vault provisioning through the existing local administration guard."""
import copy
import re
import uuid

from agent_service.errors import APIError, UserMessageError
from agent_service.integrations import integration_contract, validate_integration
from agent_service.secret_vault import SecretVault


def validate_settings(data, projects, catalogs):
    result = {}
    revision = data.get('secret_vault_revision')
    if revision is not None:
        if not isinstance(revision, str) or not re.fullmatch(r'[a-f0-9]{32}', revision):
            raise UserMessageError('Invalid vault revision.')
        result['secret_vault_revision'] = revision
    contracts = data.get('integrations', [])
    bindings = data.get('integration_bindings', [])
    effects = data.get('effect_integrations', [])
    if any(not isinstance(value, list) or len(value) > 100 for value in (contracts, bindings, effects)):
        raise UserMessageError('Invalid integration configuration.')
    try:
        for contract in contracts:
            validate_integration(contract)
        for effect in effects:
            if not isinstance(effect, dict) or set(effect) - {'integration', 'operation', 'endpoint', 'destination_allowlist', 'mediated', 'credential_binding'}:
                raise UserMessageError('Only nonsecret integration configuration is allowed.')
            integration_contract({'effect_integrations': effects}, effect.get('integration'))
    except APIError as exc:
        raise UserMessageError(exc.code) from None
    names = [item['integration'] for item in contracts]
    if len(names) != len(set(names)):
        raise UserMessageError('Duplicate integration contract.')
    scopes = set()
    for binding in bindings:
        if (not isinstance(binding, dict) or set(binding) - {'integration', 'project_id', 'catalog_id', 'credential_binding'}
            or binding.get('project_id') not in projects
            or not isinstance(binding.get('integration'), str)
            or not isinstance(binding.get('credential_binding'), str)
            or not re.fullmatch(r'[A-Za-z0-9_.-]{1,128}', binding['credential_binding'])
            or (binding.get('catalog_id') and binding['catalog_id'] not in catalogs)):
            raise UserMessageError('Invalid integration binding scope.')
        scope = (binding['integration'], binding['project_id'], binding.get('catalog_id'))
        if scope in scopes:
            raise UserMessageError('Duplicate integration binding scope.')
        scopes.add(scope)
    for key, values in (('integrations', contracts), ('integration_bindings', bindings), ('effect_integrations', effects)):
        if values or key in data:
            result[key] = copy.deepcopy(values)
    return result


def vault(manager):
    return SecretVault(manager.state / 'harness.secrets.json')


async def read_vault(request, manager):
    return {'credentials': vault(manager).status(),
            'bindings': manager.settings.get('integration_bindings', []),
            'contracts': manager.settings.get('integrations', [])}


async def change_vault(request, manager, data):
    action = data.get('action')
    binding = data.get('binding')
    if not isinstance(binding, str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,128}', binding):
        raise UserMessageError('Choose a valid credential binding name.')
    draft = copy.deepcopy(manager.settings)
    draft['secret_vault_revision'] = uuid.uuid4().hex
    if action == 'delete':
        draft['integration_bindings'] = [item for item in draft.get('integration_bindings', []) if item['credential_binding'] != binding]
        await manager.apply_settings(draft)
        vault(manager).delete(binding)
    elif action == 'set':
        project_id, catalog_id = data.get('project_id'), data.get('catalog_id') or None
        projects = {item['id']: item for item in draft['projects']}
        if project_id not in projects and project_id != 'sem-projeto':
            raise UserMessageError('Choose a registered project.')
        if catalog_id and catalog_id not in projects.get(project_id, {}).get('catalogs', []):
            raise UserMessageError('Choose a catalog assigned to this project.')
        integration = data.get('integration')
        from agent_service.catalog_manifest import load_manifest
        from agent_service.catalog_pin import effective_catalogs

        from .catalog_admin import catalog_config

        declared = [item for catalog in effective_catalogs(catalog_config(manager), projects.get(project_id, {}))
                    if not catalog_id or catalog['id'] == catalog_id
                    for item in (load_manifest(catalog['root']) or {}).get('integrations', [])
                    if item['integration'] == integration]
        contract = data.get('contract')
        if contract is not None:
            validate_integration(contract)
            if contract['integration'] != integration:
                raise UserMessageError('Integration and contract must match.')
            if any(item != contract for item in declared):
                raise UserMessageError('Use the integration contract declared by this catalog.')
            draft['integrations'] = [item for item in draft.get('integrations', []) if item['integration'] != integration] + [contract]
        available = [item for item in draft.get('integrations', []) if item['integration'] == integration] + declared
        if not available or any(item != available[0] for item in available):
            raise UserMessageError('Configure one unambiguous integration contract before saving credentials.')
        if data.get('effect_contract') is not None and not available[0]['mediated']:
            raise UserMessageError('This integration uses advisory credentials, not a mediated executor.')
        values = data.get('values')
        SecretVault._validate(binding, values)
        if not set(available[0]['environment'].values()) <= set(values):
            raise UserMessageError('Provide all credential fields required by the contract.')
        if available[0]['mediated']:
            effect = data.get('effect_contract')
            if not isinstance(effect, dict) or effect.get('integration') != integration or effect.get('credential_binding') != binding:
                raise UserMessageError('Mediated credentials require the matching executor contract.')
            draft['effect_integrations'] = [item for item in draft.get('effect_integrations', []) if item['integration'] != integration] + [effect]
        item = {'integration': integration, 'project_id': project_id, 'credential_binding': binding}
        if catalog_id:
            item['catalog_id'] = catalog_id
        draft['integration_bindings'] = [existing for existing in draft.get('integration_bindings', []) if (existing['integration'], existing['project_id'], existing.get('catalog_id')) != (integration, project_id, catalog_id)] + [item]
        validated = manager.validate(draft)
        # Credentials never enter settings or the returned status. Roll back a replaced
        # binding if applying the nonsecret configuration fails.
        store = vault(manager)
        try:
            previous = store.get(binding)
        except APIError as exc:
            if exc.code != 'effect_credentials_unavailable':
                raise
            previous = None
        store.set(binding, values)
        try:
            await manager.apply_settings(validated)
        except BaseException:
            store.delete(binding) if previous is None else store.set(binding, previous)
            raise
    else:
        raise UserMessageError('Choose set or delete.')
    manager.audit('vault_' + action)
    return await read_vault(request, manager)
