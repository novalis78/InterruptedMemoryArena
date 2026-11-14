"""What each chain left behind: files in its working directory and its memory/note files. Usage: files.py RUN [RUN...]"""
import glob
import re
import sys
import tarfile

NOTE = re.compile(r'(memor|note|handoff|plan|agents\.md|soul|user\.md|ledger|state)', re.I)
for run in sys.argv[1:]:
    for f in sorted(glob.glob(f'/var/lib/ima/runs/{run}/*/home.tar.gz')):
        chain = f.split('/')[-2]
        work, notes = [], []
        with tarfile.open(f) as tf:
            for m in tf.getmembers():
                if not m.isfile():
                    continue
                n = m.name.removeprefix('home/')
                if n.startswith('work/') and '/sessions/' not in n:
                    work.append(f'{n[5:]} ({m.size})')
                elif NOTE.search(n) and not re.search(r'skills/|/sessions?/|\.jsonl$|state\.db|logs/', n):
                    notes.append(f'{n} ({m.size})')
        print(chain)
        print('  work: ' + (', '.join(work[:25]) or '-') + (' …' if len(work) > 25 else ''))
        print('  notes: ' + (', '.join(notes[:15]) or '-'))
