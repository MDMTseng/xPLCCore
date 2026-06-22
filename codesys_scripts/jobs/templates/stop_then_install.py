# -*- coding: ascii -*-
# Canonical layout-safe install path.
#
# CODESYS pops a dialog (and hangs scripting) in two cases that bite us
# during dev:
#   1. OnlineChangeOption.Try meets a layout-changing edit (e.g. new
#      retained VAR_GLOBAL, new TYPE) -- IDE prompts "switch to
#      download?", scripting daemon hangs waiting.
#   2. Force download against a running application -- IDE prompts
#      "application is running, continue?", same hang.
#
# Stopping the application BEFORE the login call dodges both. This script
# is the safe default; routine non-layout edits can still use the lighter
# online_change.py.
#
# Sequence:
#   1. log in with OnlineChangeOption.Force (already-stopped-friendly)
#   2. if state is run, oapp.stop() and wait until state is stop
#   3. login with OnlineChangeOption.Force again -- now the download
#      proceeds without dialogs because nothing is running
#   4. oapp.start() (treats "already run" as success, per memory
#      daemon_plc_lifecycle.md)
#
# Note: a Force download takes the PLC TCP server down briefly. Any
# external client (UI / scripted harness) must reconnect afterwards.
#
# Run as:
#   python codesys_scripts/rpc.py exec --file codesys_scripts/jobs/templates/stop_then_install.py

import time

proj = projects.primary
app = proj.active_application
oapp = online.create_online_application(app)
print("project:", proj.path)
print("pre-state:", oapp.application_state, "logged_in:", oapp.is_logged_in)

# Step 1 -- ensure logged in. Force is safe pre-stop (doesn't actually
# download until we explicitly do).
try:
    oapp.login(OnlineChangeOption.Force, False)
    print("login(Force) ok:", oapp.application_state)
except Exception as ex:
    print("login(Force) err:", ex)

# Step 2 -- stop if running. Poll until state is .stop.
state = str(oapp.application_state).lower()
if "run" in state:
    print("application is running; stopping...")
    try:
        oapp.stop()
    except Exception as ex:
        print("stop err (continuing):", ex)
    # Wait for stop to settle. Bounded poll.
    deadline = time.time() + 5.0
    while time.time() < deadline:
        s = str(oapp.application_state).lower()
        if "stop" in s:
            print("stopped:", oapp.application_state); break
        time.sleep(0.2)
    else:
        print("WARN: stop did not settle within 5s; current state:",
              oapp.application_state)
else:
    print("application already stopped:", oapp.application_state)

# Step 3 -- re-login to force the actual download now that we're
# stopped. No "running, continue?" prompt.
try:
    oapp.login(OnlineChangeOption.Force, False)
    print("post-download state:", oapp.application_state)
except Exception as ex:
    print("login(Force) post-stop err:", ex)

# Step 4 -- start. Memory: if the Force-login auto-started the app, this
# raises "Cannot start if application state is run" -- treat as success.
try:
    oapp.start()
    print("post-start state:", oapp.application_state)
except Exception as ex:
    msg = str(ex)
    if "is run" in msg or "already" in msg.lower():
        print("start() reported already-running (expected after Force):", msg)
    else:
        print("start() err:", msg)

try:
    oapp.logout()
except Exception as ex:
    print("logout err (ignored):", ex)

print("[done]")
