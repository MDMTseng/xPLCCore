# -*- coding: ascii -*-
#   rpc.py exec --file jobs/templates/sim_esp_gvl.py   (XPLC_CONFIG = the sim config)
# SIM PROJECT ONLY: stand-ins for the EasyCAT IO channels (the sim has no
# EasyCAT slave in its EtherCAT tree). Idempotent.
from System import Guid
NAME = "GVL_SimEsp"
DECL = 'VAR_GLOBAL\n\tecat_esp_in0 : USINT;\n\tecat_esp_in1 : USINT;\n\tecat_esp_in2 : USINT;\n\tecat_esp_in3 : USINT;\n\tecat_esp_in4 : USINT;\n\tecat_esp_in5 : USINT;\n\tecat_esp_in6 : USINT;\n\tecat_esp_in7 : USINT;\n\tecat_esp_in8 : USINT;\n\tecat_esp_in9 : USINT;\n\tecat_esp_in10 : USINT;\n\tecat_esp_in11 : USINT;\n\tecat_esp_in12 : USINT;\n\tecat_esp_in13 : USINT;\n\tecat_esp_in14 : USINT;\n\tecat_esp_in15 : USINT;\n\tecat_esp_in16 : USINT;\n\tecat_esp_in17 : USINT;\n\tecat_esp_in18 : USINT;\n\tecat_esp_in19 : USINT;\n\tecat_esp_in20 : USINT;\n\tecat_esp_in21 : USINT;\n\tecat_esp_in22 : USINT;\n\tecat_esp_in23 : USINT;\n\tecat_esp_in24 : USINT;\n\tecat_esp_in25 : USINT;\n\tecat_esp_in26 : USINT;\n\tecat_esp_in27 : USINT;\n\tecat_esp_in28 : USINT;\n\tecat_esp_in29 : USINT;\n\tecat_esp_in30 : USINT;\n\tecat_esp_in31 : USINT;\n\tecat_esp_out0 : USINT;\n\tecat_esp_out1 : USINT;\n\tecat_esp_out2 : USINT;\n\tecat_esp_out3 : USINT;\n\tecat_esp_out4 : USINT;\n\tecat_esp_out5 : USINT;\n\tecat_esp_out6 : USINT;\n\tecat_esp_out7 : USINT;\n\tecat_esp_out8 : USINT;\n\tecat_esp_out9 : USINT;\n\tecat_esp_out10 : USINT;\n\tecat_esp_out11 : USINT;\n\tecat_esp_out12 : USINT;\n\tecat_esp_out13 : USINT;\n\tecat_esp_out14 : USINT;\n\tecat_esp_out15 : USINT;\nEND_VAR'
app = projects.primary.active_application
g = None
for o in app.get_children():
    try:
        if str(o.get_name()) == NAME:
            g = o
    except Exception:
        pass
if g is None:
    g = app.create_gvl(NAME)
    print("created " + NAME)
g.textual_declaration.replace(DECL)
print("ok")
