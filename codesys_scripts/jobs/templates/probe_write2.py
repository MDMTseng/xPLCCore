# -*- coding: ascii -*-
import time
proj = projects.primary
app_obj = None
for app in list(proj.find("Application", True) or []):
    app_obj = app; break
oapp = online.create_online_application(app_obj)
oapp.login(OnlineChangeOption.Try, False)
try:
    all_attrs = sorted([a for a in dir(oapp) if not a.startswith("_")])
    print("ALL oapp methods:")
    for a in all_attrs:
        print("  ", a)
finally:
    oapp.logout()
