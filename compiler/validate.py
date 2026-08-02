#!/usr/bin/env python3
from pathlib import Path
import argparse,sys
REQ=['LLM_README.aol','core/SYSTEM.aol','core/PROCESS.aol','core/ROLES.aol','core/QUALITY.aol','core/PRECEDENCE.aol']
PREQ=['project.aol','domain.aol','product.aol','architecture.aol','invariants.aol','journeys.aol','state.aol']
def main():
 p=argparse.ArgumentParser(); p.add_argument('--root',required=True); a=p.parse_args(); r=Path(a.root); e=[]
 for x in REQ:
  if not (r/x).exists(): e.append('missing '+x)
 for parent in [r/'projects',r/'examples']:
  if parent.exists():
   for d in [x for x in parent.iterdir() if x.is_dir()]:
    for x in PREQ:
     if not (d/x).exists(): e.append(f'{d.name}: missing {x}')
 if e:
  print('VALIDATION=FAIL'); [print(' - '+x) for x in e]; sys.exit(1)
 print('VALIDATION=PASS')
if __name__=='__main__': main()
