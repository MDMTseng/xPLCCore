# -*- coding: ascii -*-
proj = projects.primary
app_obj = None
for app in list(proj.find("Application", True) or []):
    app_obj = app; break
oapp = online.create_online_application(app_obj)
oapp.login(OnlineChangeOption.Try, False)
try:
    write_attrs = [a for a in dir(oapp) if "write" in a.lower() or "set" in a.lower()]
    read_attrs  = [a for a in dir(oapp) if "read"  in a.lower() or "get"  in a.lower()]
    print("write-ish:", write_attrs)
    print("read-ish :", read_attrs)
finally:
    oapp.logout()
