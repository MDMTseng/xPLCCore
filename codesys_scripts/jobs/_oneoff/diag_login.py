"""Deeper login diagnostic — check if PLC needs credentials, check
runtime version + project consistency."""

proj = projects.primary
app = next(iter(proj.find("Application", True)))
oapp = online.create_online_application(app)
dev = oapp.get_online_device()

if not dev.connected:
    dev.connect()
print("connected:", dev.connected)
print("current_logged_on_username:", dev.current_logged_on_username)

# Check device password / credentials API
try:
    # Maybe device user mgmt is enforced
    print("\nTesting set_credentials_for_initial_user (no-op if no UM)...")
    # device may require login as Administrator or empty
except Exception as e:
    print("creds check fail:", e)

# Try login with explicit Stop first
print("\noperation_state before:", oapp.operation_state)
try:
    print("calling oapp.stop()...")
    oapp.stop()
    print("stop done")
except Exception as e:
    print("stop fail (ok if not logged in):", e)

# Try login with reset = True (might bypass dialogs)
import sys
print("\nattempting oapp.login(Never, False) with stdout flush...")
sys.stdout.flush()
try:
    oapp.login(OnlineChangeOption.Never, False)
    print("LOGIN OK")
    print("app_state =", oapp.application_state)
except Exception as e:
    print("LOGIN FAIL:", e)
    # Get more detail
    import traceback
    traceback.print_exc()
