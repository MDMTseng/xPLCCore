# -*- coding: ascii -*-
import time
proj = projects.primary
app_obj = None
for app in list(proj.find("Application", True) or []):
    app_obj = app; break
oapp = online.create_online_application(app_obj)
oapp.login(OnlineChangeOption.Try, False)
try:
    print("before:", oapp.read_value("GVL.bRunMsgPackTests"))

    # Try pattern A: set_prepared_value with assignment syntax
    try:
        oapp.set_prepared_value("GVL.bRunMsgPackTests", "TRUE")
        print("set_prepared_value accepted")
    except Exception as ex:
        print("set_prepared_value raised:", ex)

    try:
        ret = oapp.write_prepared_values()
        print("write_prepared_values ret:", ret)
    except Exception as ex:
        print("write_prepared_values raised:", ex)

    time.sleep(0.1)
    print("after write_prepared:", oapp.read_value("GVL.bRunMsgPackTests"))

    # Inspect what get_prepared_expressions/get_prepared_value say
    try:
        exprs = oapp.get_prepared_expressions()
        print("prepared expressions:", list(exprs) if exprs else None)
    except Exception as ex:
        print("get_prepared_expressions:", ex)

    # Introspect signatures
    import inspect  # may not exist in IronPython 2.7 the same way
    for name in ["set_prepared_value", "write_prepared_values", "read_value"]:
        m = getattr(oapp, name)
        print(name, "->", m)
finally:
    oapp.logout()
