import time
proj = projects.primary
app = next(iter(proj.find("Application", True)))
oapp = online.create_online_application(app)
oapp.login(OnlineChangeOption.Try, False)
oapp.set_prepared_value("GVL.bVirtualMotorsMode_Request","FALSE")
oapp.force_prepared_values()
time.sleep(0.4)
oapp.unforce_all_values()
time.sleep(0.2)
oapp.set_prepared_value("GVL.bVirtualMotorsMode_Request","TRUE")
oapp.set_prepared_value("GVL.ConveyorPulseSyntheticEnable","TRUE")
oapp.set_prepared_value("GVL.ConveyorPulseSyntheticStep","2")
oapp.force_prepared_values()
for _ in range(20):
    v = str(oapp.read_value("GVL.bVirtualMotorsMode"))
    if v.endswith("TRUE"): break
    time.sleep(0.1)
print("OK")
