# -*- coding: ascii -*-
# Login with ForceDownload to push the layout-changing GVL update
# (reMP_info_buf went 256->1024). Online change won't accept that.
proj = projects.primary
app = proj.active_application
oapp = online.create_online_application(app)
print("pre-login state:", oapp.application_state)
print("OnlineChangeOption attrs:", [a for a in dir(OnlineChangeOption) if not a.startswith("_")])
# Stop first; can't download while running.
try: oapp.stop()
except Exception as ex: print("stop err (ok if already stopped):", ex)
oapp.login(OnlineChangeOption.Force, False)
print("post-login state:", oapp.application_state, "logged_in:", oapp.is_logged_in)
oapp.start()
print("post-start state:", oapp.application_state)
oapp.logout()
print("[done]")
