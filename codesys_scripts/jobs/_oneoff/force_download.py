"""Full download. Ensure device is connected first, then login Never,
then start."""

proj = projects.primary
app = next(iter(proj.find("Application", True)))
oapp = online.create_online_application(app)
dev = oapp.get_online_device()

if not dev.connected:
    print("connecting device...")
    dev.connect()
    print("connected:", dev.connected)
else:
    print("already connected")

print("application_state =", oapp.application_state)
print("logging in (Never = full download)...")
oapp.login(OnlineChangeOption.Never, False)
print("login complete; application_state =", oapp.application_state)

try:
    oapp.start()
    print("started")
except Exception as e:
    print("start fail:", e)

print("final application_state =", oapp.application_state)
print("final operation_state   =", oapp.operation_state)
