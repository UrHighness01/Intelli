import yaml
import sys

path = 'agent-gateway/openapi.yaml'
try:
    with open(path, 'r', encoding='utf-8') as f:
        yaml.safe_load(f)
    print('OK')
except Exception as e:
    print('ERROR')
    print(e)
    sys.exit(2)
