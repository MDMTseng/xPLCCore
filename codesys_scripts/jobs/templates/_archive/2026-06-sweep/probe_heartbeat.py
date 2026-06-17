# -*- coding: ascii -*-
# Probe UI heartbeat health: read GVL.RuntimeMs and GVL.LastUiPingMs
# repeatedly; report the gap (RuntimeMs - LastUiPingMs). If the UI's
# Web Worker heartbeat is alive, gap should stay well under 1500ms
# (1s interval + small jitter). Anything > 2000ms means PINGs are
# missing.
import time

proj = projects.primary
app = proj.find('Application', recursive=True)[0]
oapp = online.create_online_application(app)
try:
    oapp.login(OnlineChangeOption.Try, False)
except Exception as ex:
    print('login note:', str(ex)[:160])

def to_int(v):
    s = str(v)
    # CODESYS returns e.g. "LINT#12345" — strip type prefix
    if '#' in s:
        s = s.split('#', 1)[1]
    s = s.split()[0].replace(',', '')
    return int(s)

samples = []
N = 12
for _ in range(N):
    rt = oapp.read_value('AxisGroupSM.RuntimeMs')
    lp = oapp.read_value('GVL.LastUiPingMs')
    gap = to_int(rt) - to_int(lp)
    samples.append(gap)
    time.sleep(0.5)

print('runtime_ms - last_ui_ping_ms gaps over %d samples (0.5s spacing):' % N)
for i, g in enumerate(samples):
    print('  t=%4.1fs  gap=%6d ms' % (i*0.5, g))
mn, mx, av = min(samples), max(samples), sum(samples)//len(samples)
print('min=%d  max=%d  avg=%d' % (mn, mx, av))
if mx < 1500:
    print('OK: heartbeat healthy (max gap < 1500ms)')
elif mx < 2500:
    print('WARN: occasional miss (max gap %d ms)' % mx)
else:
    print('FAIL: heartbeat unhealthy (max gap %d ms)' % mx)

try:
    oapp.logout()
except Exception:
    pass
