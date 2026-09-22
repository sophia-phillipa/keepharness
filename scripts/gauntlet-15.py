"""Run one reproducible cross-profile round; stop between rounds to fix findings.

Profiles are simulated lenses, not people or separate LLM agents. API/browser
fixtures do not certify live inference. Each profile retains its own contract
and retests another profile's contract; round number rotates those assignments.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
PROFILES = [
    ('P01', 'Iniciante', 'composer-send-stop.spec.cjs'),
    ('P02', 'Apressado', 'test_composer_queue.py'),
    ('P03', 'Pesquisadora', 'harness-model-handoff.spec.cjs'),
    ('P04', 'Acessibilidade', 'model-provider-groups.spec.cjs'),
    ('P05', 'Mobile e rede instável', 'harness-reconnect.spec.cjs'),
    ('P06', 'Engenheira de software', 'test_model_handoff.py'),
    ('P07', 'Especialista UI/UX', 'project-edit-dialog.spec.cjs'),
    ('P08', 'Hacker web', 'test_api_security.py'),
    ('P09', 'Hacker de isolamento', 'test_project_grants.py'),
    ('P10', 'Caçador de bugs', 'gauntlet-live-model.spec.cjs'),
    ('P11', 'Engenheira de caos', 'harness-reload.spec.cjs'),
    ('P12', 'Especialista em harness', 'harness-resources.spec.cjs'),
    ('P13', 'Administradora de projetos', 'project-create-dialog.spec.cjs'),
    ('P14', 'Redatora', 'response-format.spec.cjs'),
    ('P15', 'Entradas adversariais', 'test_gauntlet_edges.py'),
]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--round', type=int, choices=range(1, 11), required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    output = args.output / f'round-{args.round:02}'
    output.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, GAUNTLET_ROUND=str(args.round))
    records = []
    # Execute each distinct contract once, share actual evidence with both lenses.
    for offset in range(len(PROFILES)):
        index = (offset + args.round - 1) % len(PROFILES)
        ident, role, file = PROFILES[index]
        reviewer = PROFILES[(index + args.round) % len(PROFILES)]
        command = ([sys.executable, '-m', 'pytest', '-q'] if file.endswith('.py') else ['node']) + [str(ROOT / 'tests' / file)]
        started = time.monotonic()
        try:
            result = subprocess.run(command, cwd=ROOT, env=env, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, text=True, timeout=120)
            code, log = result.returncode, result.stdout
        except subprocess.TimeoutExpired as error:
            code, log = 124, str(error.stdout or '') + '\nTIMEOUT 120s'
        (output / f'{ident}.log').write_text(log)
        record = dict(profile=ident, role=role, cross_reviewer=reviewer[0],
                      reviewer_role=reviewer[1], test=file, command=command,
                      status='pass' if code == 0 else 'fail', exit_code=code,
                      seconds=round(time.monotonic()-started, 2))
        records.append(record)
        (output / 'results.json').write_text(json.dumps(records, indent=2, ensure_ascii=False)+'\n')
        print(f'R{args.round:02} {ident}/{reviewer[0]} {record["status"]} {file}', flush=True)
    return int(any(record['status'] != 'pass' for record in records))


if __name__ == '__main__':
    raise SystemExit(main())
