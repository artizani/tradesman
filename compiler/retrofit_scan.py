#!/usr/bin/env python3
from pathlib import Path
import argparse,json
IGNORE={'.git','node_modules','vendor','dist','build','.venv','target'}
def main():
 p=argparse.ArgumentParser(); p.add_argument('--repo',required=True); p.add_argument('--output',required=True); a=p.parse_args(); r=Path(a.repo); fs=[]; ts=[]; ds=[]
 for x in r.rglob('*'):
  if any(q in IGNORE for q in x.parts) or not x.is_file(): continue
  rel=str(x.relative_to(r)); fs.append(rel)
  if 'test' in rel.lower() or 'spec' in rel.lower(): ts.append(rel)
  if x.suffix.lower() in {'.md','.adoc','.rst'}: ds.append(rel)
 data={'repository':str(r.resolve()),'file_count':len(fs),'test_file_count':len(ts),'documentation_count':len(ds),'candidate_test_files':ts[:200],'candidate_docs':ds[:200],'actions':['record current architecture','identify invariants','catalogue critical journeys','map tests to journeys','create gap tasks','record risks and deployment state']}
 o=Path(a.output); o.parent.mkdir(parents=True,exist_ok=True); o.write_text(json.dumps(data,indent=2)); print(o)
if __name__=='__main__': main()
