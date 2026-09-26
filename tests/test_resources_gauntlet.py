"""Ten contextual gauntlet rounds, synthetic files and no inference."""
import pytest
from agent_service import resources
from test_resources import put, cfg


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv('HOME', str(tmp_path/'home'))
    for key in ('CODEX_HOME', 'CLAUDE_CONFIG_DIR', 'GEMINI_CLI_HOME'):
        monkeypatch.delenv(key, raising=False)
    root = tmp_path/'project'
    root.mkdir()
    return root


def command(root, body):
    import json
    put(root, '.gemini/commands/review.toml', 'prompt='+json.dumps(body))
    return resources.discover(cfg(root, 'gemini'), 'p', 'gemini', private=True)['items'][0]


@pytest.mark.parametrize('arg', ['$1', '$ARGUMENTS', '{{args}}', '$2 literal'])
def test_round01_literal_arguments(project, arg):
    item = command(project, 'Review {{args}}')
    assert resources.prepare_prompt('/review '+arg, [item]) == 'Review '+arg


@pytest.mark.parametrize('value', ['123', 'true', '[]', '{}'])
def test_round02_malformed_command_catalog(project, value):
    put(project, '.gemini/commands/broken.toml', 'prompt='+value)
    put(project, '.gemini/commands/good.toml', 'prompt="Good {{args}}"')
    catalog = resources.discover(cfg(project, 'gemini'), 'p', 'gemini')
    assert [i['name'] for i in catalog['items']] == ['good']
    assert catalog['warnings']


def test_round03_final_prompt_size(project):
    put(project, '.agents/skills/check/SKILL.md', '---\nname: check\n---\nCheck')
    item = resources.discover(cfg(project), 'p', 'codex', private=True)['items'][0]
    with pytest.raises(resources.ResourceError, match='resource_prompt_limit'):
        resources.prepare_prompt('/check '+'x'*149990, [item])


def selection(item):
    return {'id': item['id'], 'revision': item['revision'],
            'token': ('@' if item['kind']=='agent' else '/')+item['name']}


def test_round04_project_switch_and_return(project):
    put(project, '.agents/skills/check/SKILL.md', '---\nname: check\n---\nFirst')
    other = project.parent/'other'
    put(other, '.agents/skills/check/SKILL.md', '---\nname: check\n---\nSecond')
    config = cfg(project)
    config['projects']['other'] = {'root': str(other)}
    item = resources.discover(config, 'p', 'codex')['items'][0]
    data = {'project_id': 'p', 'backend': 'codex', 'prompt': '/check', 'resource_selections': [selection(item)]}
    assert resources.resolve(config, data)
    with pytest.raises(resources.ResourceError, match='resource_unavailable'):
        resources.resolve(config, dict(data, project_id='other'))
    assert resources.resolve(config, data)


@pytest.mark.parametrize('backend', ['local', 'deepseek', 'gemini', 'claude'])
def test_round05_executor_switch_revalidates_agents(project, backend):
    put(project, '.codex/agents/review.toml', 'name="review"\ndeveloper_instructions="Review"')
    config = cfg(project)
    config['services'][backend] = {'mode': 'native'}
    item = resources.discover(config, 'p', 'codex')['items'][0]
    data = {'project_id': 'p', 'backend': backend, 'prompt': '@review', 'resource_selections': [selection(item)]}
    with pytest.raises(resources.ResourceError, match='resource_unavailable'):
        resources.resolve(config, data)


def test_round06_model_switch_and_repeated_selection(project):
    put(project, '.agents/skills/check/SKILL.md', '---\nname: check\n---\nCheck')
    config = cfg(project)
    item = resources.discover(config, 'p', 'codex', 'first')['items'][0]
    data = {'project_id': 'p', 'backend': 'codex', 'model': 'second', 'prompt': '/check now',
            'resource_selections': [selection(item), selection(item)]}
    resolved = resources.resolve(config, data)
    assert len(resolved) == 1
    assert resources.prepare_prompt(data['prompt'], resolved).count('Explicitly invoke') == 1
    with pytest.raises(resources.ResourceError, match='resources_unavailable_in_workspace'):
        resources.resolve(config, dict(data, workspace_id='isolated'))


def test_round07_mixed_resources_and_fresh_revision(project):
    path = put(project, '.agents/skills/check/SKILL.md', '---\nname: check\n---\nCheck')
    put(project, '.codex/agents/review.toml', 'name="review"\ndeveloper_instructions="Review"')
    config = cfg(project)
    items = resources.discover(config, 'p', 'codex')['items']
    data = {'project_id': 'p', 'backend': 'codex', 'prompt': '@review /check',
            'resource_selections': [selection(i) for i in items]}
    prepared = resources.prepare_prompt(data['prompt'], resources.resolve(config, data))
    assert '@review $check' in prepared
    assert 'actual native delegation' in prepared
    path.write_text('---\nname: check\n---\nChanged')
    with pytest.raises(resources.ResourceError, match='resource_changed'):
        resources.resolve(config, data)


def test_round08_escape_and_malformed_neighbor(project):
    secret = put(project.parent, 'outside/SKILL.md', '---\nname: outside\n---\nSynthetic')
    alias = project/'.agents/skills/escape'
    alias.parent.mkdir(parents=True)
    alias.symlink_to(secret.parent)
    put(project, '.agents/skills/broken/SKILL.md', '---\nname: broken\nmissing delimiter')
    put(project, '.agents/skills/good/SKILL.md', '---\nname: good\n---\nGood')
    catalog = resources.discover(cfg(project), 'p', 'codex')
    assert [i['name'] for i in catalog['items']] == ['good']
    assert catalog['warnings']
    assert all('_text' not in i and '_body' not in i for i in catalog['items'])


def test_round09_unicode_and_argument_recovery(project):
    item = command(project, 'Review {{args}}')
    with pytest.raises(resources.ResourceError, match='invalid_command_arguments'):
        resources.prepare_prompt('/review "unfinished', [item])
    assert resources.prepare_prompt('/review "café façade"\nDraft intact', [item]) == 'Review "café façade"\nDraft intact'
    assert resources.prepare_prompt('/review-other literal', [item]) == '/review-other literal'


def test_round10_cross_retest_all_placeholder_forms(project):
    item = command(project, '{{args}} | $ARGUMENTS | $1 | $2')
    assert resources.prepare_prompt('/review "$2" literal', [item]) == '"$2" literal | "$2" literal | $2 | literal'
    # The engineer repeats the catalog hunter's malformed-neighbor case.
    put(project, '.gemini/commands/broken.toml', 'prompt=false')
    catalog = resources.discover(cfg(project, 'gemini'), 'p', 'gemini')
    assert [i['name'] for i in catalog['items']] == ['review']
    assert catalog['warnings']


def test_round10_handoff_rejection_preserves_request_and_queue(project, tmp_path):
    import copy
    from agent_service.app import Service, APIError
    from test_workspaces import config as service_config
    conf = service_config(tmp_path)
    conf['projects']['p']['root'] = str(project)
    conf['services']['codex']['mode'] = 'native'
    put(project, '.agents/skills/check/SKILL.md', '---\nname: check\n---\nCheck')
    service = Service(conf)
    identity = ('a', conf['clients']['a'])
    try:
        item = service.resource_catalog(identity, 'p', 'codex', 'gpt-6-astra')['items'][0]
        data = {'project_id': 'p', 'backend': 'codex', 'model': 'gpt-6-astra', 'effort': 'low',
                'prompt': '/check draft survives', 'resource_selections': [selection(item)]}
        original = copy.deepcopy(data)
        # An executor/mode change invalidates native selection before enqueue.
        with pytest.raises(APIError, match='resource_unavailable'):
            service.submit(identity, dict(data, execution_mode='scoped'))
        assert service.db.execute('SELECT count(*) FROM jobs').fetchone()[0] == 0
        assert data == original
        assert service.submit(identity, data)['job_id']
        assert data == original
    finally:
        service.db.close()


@pytest.mark.parametrize('reverse', [False, True])
@pytest.mark.parametrize('template', ['First {{args}}', 'First /other {{args}}'])
def test_round10_commands_do_not_expand_inserted_text(project, reverse, template):
    first = command(project, template)
    put(project, '.gemini/commands/other.toml', 'prompt="SECOND {{args}}"')
    other = next(i for i in resources.discover(cfg(project, 'gemini'), 'p', 'gemini', private=True)['items'] if i['name']=='other')
    items = [first, other]
    if reverse:
        items.reverse()
    result = resources.prepare_prompt('/review /other literal\n/other actual', items)
    assert result == template.replace('{{args}}', '/other literal')+'\nSECOND actual'


@pytest.mark.parametrize('reverse', [False, True])
def test_round10_skill_tokens_in_command_data_stay_literal(project, reverse):
    put(project, '.codex/skills/check/SKILL.md', '---\nname: check\n---\nCheck')
    put(project.parent/'home', '.codex/prompts/review.md', 'Template /check $ARGUMENTS')
    items = resources.discover(cfg(project), 'p', 'codex', private=True)['items']
    items.sort(key=lambda item: item['kind']=='skill', reverse=reverse)
    result = resources.prepare_prompt('/review /check literal\n/check actual', items)
    assert result.startswith('Template /check /check literal\n$check actual\n')
