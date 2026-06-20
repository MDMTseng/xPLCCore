proj = projects.primary
app = next(iter(proj.find("Application", True)))
oapp = online.create_online_application(app)
all_methods = sorted([m for m in dir(oapp) if not m.startswith("_")])
print("== all oapp methods ==")
for m in all_methods:
    print(" ", m)
