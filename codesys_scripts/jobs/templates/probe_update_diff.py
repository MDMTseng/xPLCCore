# -*- coding: ascii -*-
# Diagnose why AxisGroupManager/Update keeps re-applying. Read the on-disk
# .st and the in-project text; print first diff position + context.
import os, difflib

SRC = r'C:\Users\X1\Desktop\X2.5\TCP_UI\TCP_UI\codesys_code\Application\Robot_FBs\AxisGroupManager\Update.st'
IMPL_MARKER = '(* =========== IMPLEMENTATION =========== *)'

proj = projects.primary

def find_child(o, name):
    for c in o.get_children():
        try:
            if c.get_name() == name: return c
        except Exception: pass
    return None

root = None
for top in proj.get_children():
    if top.get_name() == 'Device':
        root = top; break
cur = find_child(root, 'Plc Logic')
for part in ['Application', 'Robot_FBs', 'AxisGroupManager', 'Update']:
    cur = find_child(cur, part)

td = getattr(cur, 'textual_declaration', None)
ti = getattr(cur, 'textual_implementation', None)
proj_decl = td.text if td else ''
proj_impl = ti.text if ti else ''

with open(SRC, 'rb') as f:
    raw = f.read()
disk = raw.decode('utf-8', 'replace')

if IMPL_MARKER in disk:
    idx = disk.index(IMPL_MARKER)
    disk_decl = disk[:idx]
    disk_impl = disk[idx + len(IMPL_MARKER):]
    if disk_decl.endswith('\n'): disk_decl = disk_decl[:-1]
    if disk_impl.startswith('\n'): disk_impl = disk_impl[1:]
else:
    disk_decl = disk
    disk_impl = None

def report(label, a, b):
    if a == b:
        print('%s: identical (%d bytes)' % (label, len(a)))
        return
    print('%s: DIFFERENT (disk=%d  proj=%d bytes)' % (label, len(a), len(b)))
    # find first diff offset
    lim = min(len(a), len(b))
    diff_at = lim
    for i in range(lim):
        if a[i] != b[i]:
            diff_at = i; break
    s = max(0, diff_at - 20)
    e = min(lim, diff_at + 20)
    def show(x, off):
        return repr(x[off:off+40])
    print('  first diff at offset %d' % diff_at)
    print('  disk  ...%s...' % show(a, s))
    print('  proj  ...%s...' % show(b, s))
    # Also show full unified diff line-by-line, first 30 lines
    diffs = list(difflib.unified_diff(
        b.splitlines(True), a.splitlines(True),
        fromfile='project', tofile='disk', n=2))
    for line in diffs[:30]:
        # show invisible chars
        print('    ' + line.replace('\t', '\\t').replace('\r', '\\r').rstrip('\n'))

print('--- declaration ---')
report('decl', disk_decl, proj_decl)
print('--- implementation ---')
if disk_impl is None:
    print('disk has no IMPL_MARKER (decl-only file)')
else:
    report('impl', disk_impl, proj_impl)
