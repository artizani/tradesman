#!/usr/bin/env python3
from pathlib import Path
import argparse
CORE=['LLM_README.aol','core/SYSTEM.aol','core/PROCESS.aol','core/ROLES.aol','core/QUALITY.aol','core/PRECEDENCE.aol']
def main():
 p=argparse.ArgumentParser(); p.add_argument('--root',required=True); p.add_argument('--project',required=True); p.add_argument('--task',required=True); p.add_argument('--role',required=True); p.add_argument('--output'); a=p.parse_args()
 r=Path(a.root); d=r/'projects'/a.project
 if not d.exists(): d=r/'examples'/a.project
 files=[r/x for x in CORE]+[d/x for x in ['project.aol','domain.aol','product.aol','architecture.aol','invariants.aol','journeys.aol','state.aol']]+[d/'tasks'/f'{a.task}.aol']
 out=[f'AOL_CONTEXT/1\nPROJECT={a.project}\nTASK={a.task}\nROLE={a.role.upper()}\n']
 for f in files: out += [f'\n### {f.relative_to(r)}\n', f.read_text() if f.exists() else f'MISSING={f}\n']
 h=d/'memory'/'handoffs.ndjson'
 if h.exists(): out += ['\n### LATEST_HANDOFFS\n','\n'.join(h.read_text().splitlines()[-10:])]
 o=Path(a.output) if a.output else r/'context-pack.aol'; o.write_text(''.join(out)); print(o)
if __name__=='__main__': main()
