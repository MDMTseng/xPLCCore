# -*- coding: ascii -*-
# Post-deploy verification: confirm the cyclic latch is running and
# GVL.LastAxesErrorIDs is being populated each scan.
proj = projects.primary
app = proj.active_application
oapp = online.create_online_application(app)
oapp.login(OnlineChangeOption.Try, False)
if not oapp.is_logged_in:
    print("NOT LOGGED IN -- aborting"); raise SystemExit(1)

for i in range(4):
    expr = "GVL.LastAxesErrorIDs[%d]" % i
    print("%-30s = %s" % (expr, oapp.read_value(expr)))
print("GVL.AxisGroupSMScans         =", oapp.read_value("GVL.AxisGroupSMScans"))
