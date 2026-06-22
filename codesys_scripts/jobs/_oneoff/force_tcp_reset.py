# Force TCP_MSGPAK_Server reset to kick a stuck client.
proj = projects.primary
app = next(iter(proj.find("Application", True)))
oapp = online.create_online_application(app)
oapp.login(OnlineChangeOption.Try, False)
oapp.set_prepared_value("TCP_MSGPAK_Server.fbMyServer.xResetConnection", "TRUE")
oapp.force_prepared_values()
import time; time.sleep(0.5)
oapp.set_prepared_value("TCP_MSGPAK_Server.fbMyServer.xResetConnection", "FALSE")
oapp.force_prepared_values()
time.sleep(0.3)
oapp.unforce_all_values()
print("reset cycled")
