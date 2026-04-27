# -*- coding: ascii -*-
import time

proj = projects.primary
app = proj.find('Application', recursive=True)[0]
oapp = online.create_online_application(app)
try:
    oapp.login(OnlineChangeOption.Try, False)
except Exception as ex:
    print('login note:', str(ex)[:160])

def rd(expr):
    try:
        return str(oapp.read_value(expr))
    except Exception as ex:
        return 'ERR(%s)' % str(ex)[:80]

print('UiPingCount       :', rd('GVL.UiPingCount'))
print('LastUiPingMs      :', rd('GVL.LastUiPingMs'))
print('AxisGroupSM.RuntimeMs:', rd('AxisGroupSM.RuntimeMs'))
print('FSM state         :', rd('AxisGroupSM.AxisGroupManagerFb._eState'))
print('UiHeartbeatStaleCount:', rd('GVL.UiHeartbeatStaleCount'))
print('LastErrorSource   :', rd('GVL.LastErrorSource'))

# Sample UiPingCount twice 2s apart to see if it's increasing
c1 = rd('GVL.UiPingCount')
time.sleep(2.0)
c2 = rd('GVL.UiPingCount')
print('UiPingCount 2s delta: %s -> %s' % (c1, c2))

try:
    oapp.logout()
except Exception:
    pass
