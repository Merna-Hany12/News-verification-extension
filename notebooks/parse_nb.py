import json
import sys

sys.stdout.reconfigure(encoding='utf-8')

with open('notebooks/media_pipeline_eval_yunet.ipynb', 'r', encoding='utf-8') as f:
    nb = json.load(f)

for i, cell in enumerate(nb['cells']):
    src = ''.join(cell['source'])
    if src.strip():
        ctype = cell['cell_type']
        print(f"=== CELL {i} ({ctype}) ===")
        print(src[:3000])
        print()
