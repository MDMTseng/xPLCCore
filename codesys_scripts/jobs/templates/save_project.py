# -*- coding: ascii -*-
# Persist whatever's currently in the warm session to the project file.
proj = projects.primary
proj.save()
print("project saved:", proj.path)
