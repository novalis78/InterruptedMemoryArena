"""How each agent is set up in a chain's home and how one turn is run. Identical prompts for all; only the harness differs."""
import json
import os
import shutil

PROXY = 'http://127.0.0.1:8402/api/v1'
BASE_PATH = '/usr/local/bin:/usr/bin:/bin'


def write(path, text, mode=0o644):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w') as f:
        f.write(text)
    os.chmod(path, mode)


class Arm:
    name = 'arm'
    resume_ok = True

    def __init__(self, model, key, home, settings=None):
        self.model, self.key, self.home, self.settings = model, key, home, settings
        self.work = f'{home}/work'

    def env(self):
        return {'HOME': self.home, 'PATH': BASE_PATH, 'LANG': 'C.UTF-8', 'TERM': 'dumb', 'NO_COLOR': '1'}

    def setup(self):
        pass

    def command(self, prompt, session, resume):
        raise NotImplementedError


class Hermes(Arm):
    name = 'hermes'

    def env(self):
        e = super().env()
        e.update({'PATH': f'{self.home}/.local/bin:{BASE_PATH}', 'HERMES_HOME': f'{self.home}/.hermes',
                  'OPENROUTER_API_KEY': self.key})
        return e

    def setup(self):
        import yaml
        cfg = {
            'model': {'default': self.model, 'provider': 'openrouter', 'base_url': PROXY},
            'terminal': {'backend': 'local', 'timeout': 180},
            'checkpoints': {'enabled': False},
            'updates': {'check_on_startup': False},
        }
        write(f'{self.home}/.hermes/config.yaml', yaml.safe_dump(cfg, default_flow_style=False))
        write(f'{self.home}/.hermes/.env', f'OPENROUTER_API_KEY={self.key}\n', 0o600)

    def command(self, prompt, session, resume):
        return ['hermes', '--yolo', 'chat', *(['--resume', 'latest'] if resume else []), '-q', prompt, '-Q', '--model', self.model]


class PicoClaw(Arm):
    name = 'picoclaw'

    def env(self):
        e = super().env()
        e.update({'PICOCLAW_HOME': f'{self.home}/.picoclaw', 'PICOCLAW_CONFIG': f'{self.home}/.picoclaw/config.json',
                  'PICOCLAW_AGENTS_DEFAULTS_WORKSPACE': self.work,
                  'PICOCLAW_AGENTS_DEFAULTS_RESTRICT_TO_WORKSPACE': 'false',
                  'PICOCLAW_AGENTS_DEFAULTS_MAX_TOOL_ITERATIONS': '100000',
                  'PICOCLAW_TOOLS_EXEC_ENABLE_DENY_PATTERNS': 'false'})
        return e

    def setup(self):
        cfg = {'model_list': [{'model_name': 'bench', 'provider': 'openrouter', 'model': self.model, 'api_keys': [self.key],
                               'api_base': PROXY, 'request_timeout': 900}],
               'agents': {'defaults': {'model_name': 'bench'}}}
        write(f'{self.home}/.picoclaw/config.json', json.dumps(cfg, indent=1), 0o600)

    def command(self, prompt, session, resume):
        return ['/opt/picoclaw/picoclaw', 'agent', '--no-color', '-m', prompt, '--model', 'bench', '-s', f'cli:session-{session}']


# The private run also has two arms that are not part of this release: Hermes with bootkey's coding hands, and
# Mannie, our proprietary bootkey.ai agent.
ARMS = {a.name: a for a in (Hermes, PicoClaw)}
