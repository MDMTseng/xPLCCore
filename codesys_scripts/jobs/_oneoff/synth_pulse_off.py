proj = projects.primary
app = next(iter(proj.find("Application", True)))
oapp = online.create_online_application(app)
oapp.login(OnlineChangeOption.Try, False)
oapp.set_prepared_value("GVL.ConveyorPulseSyntheticEnable","FALSE")
oapp.force_prepared_values()
import time; time.sleep(0.1)
oapp.unforce_all_values()
print("synthetic off, all forces released")
