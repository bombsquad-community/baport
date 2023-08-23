# Usage: port_7_to_8.py <client/server type of mod> <plugin-name>

# You'll have to manually update the following:
# with ba.Context(_ba.foreground_host_activity()):
# To:
# with _ba.foreground_host_activity().context:

import re
import sys

with open(sys.argv[2], "rb") as fin:
    print("Porting "+ sys.argv[2])
    content = fin.read().decode("utf-8")

content = content.replace("# ba_meta require api 7", "# ba_meta require api 8")
content = content.replace("# ba_meta export game", "# ba_meta export bascenev1.GameActivity")

content = content.replace("user_agent_string", "legacy_user_agent_string")
content = content.replace("_ba.", "_babase.")
content = content.replace("_ba.", "_babase.")
content = content.replace("ba.", "babase.")
content = content.replace("import _ba", "import _babase")
content = re.sub(r'\bimport _ba\b', "import _babase", content)
content = re.sub(r'\bimport ba(\b|\.(\w+))', "import babase\nimport bauiv1\nimport bascenev1", content)
content = content.replace("babase.app.ui", "bauiv1.app.ui_v1")
content = content.replace("babase.app.accounts_v1", "bauiv1.app.classic.accounts")

###################################################################################
# Comment out one of these as per your requirements, depending whether to
# stay local or if it'll also be needed to transmitted to the clients.

## For local:
if sys.argv[1] == "client":
    content = content.replace("_babase.screenmessage", "bauiv1.screenmessage")
    content = content.replace("babase.screenmessage", "bauiv1.screenmessage")
    content = content.replace("babase.getsound", "bauiv1.getsound")
    content = content.replace("_babase.gettexture", "bauiv1.gettexture")
    content = content.replace("babase.gettexture", "bauiv1.gettexture")
    content = content.replace("babase.getmesh", "bauiv1.getmesh")
    content = content.replace("babase.getcollisionmesh", "bauiv1.getcollisionmesh")
else:
## For transmission:
    content = content.replace("_babase.screenmessage", "bascenev1.broadcastmessage")
    content = content.replace("babase.screenmessage", "bascenev1.broadcastmessage")
    content = content.replace("babase.getsound", "bascenev1.getsound")
    content = content.replace("_babase.gettexture", "bascenev1.gettexture")
    content = content.replace("babase.gettexture", "bascenev1.gettexture")
    content = content.replace("babase.getmesh", "bascenev1.getmesh")
    content = content.replace("babase.getcollisionmesh", "bascenev1.getcollisionmesh")
###################################################################################
content = content.replace("babase.getcollidemesh", "bascenev1.getcollisionmesh")
content = content.replace("collide_mesh", "collision_mesh")
content = content.replace("babase.open_url", "bauiv1.open_url")
content = content.replace("babase.IntSetting", "bascenev1.IntSetting")
content = content.replace("babase.IntChoiceSetting", "bascenev1.IntChoiceSetting")
content = content.replace("babase.FloatChoiceSetting", "bascenev1.FloatChoiceSetting")
content = content.replace("babase.BoolSetting", "bascenev1.BoolSetting")
content = content.replace("babase.Actor", "bascenev1.Actor")
content = content.replace("babase.Player", "bascenev1.Player")
content = content.replace("babase.PlayerDiedMessage", "bascenev1.PlayerDiedMessage")
content = content.replace("babase.time", "bascenev1.time")
content = content.replace("babase.Timer", "bascenev1.Timer")
content = content.replace("babase.newnode", "bascenev1.newnode")
content = content.replace("babase.Node", "bascenev1.Node")
content = content.replace("babase.emitfx", "bascenev1.emitfx")
content = content.replace("babase.animate", "bascenev1.animate")
content = content.replace("babase.FreeForAllSession", "bascenev1.FreeForAllSession")
content = content.replace("babase.DualTeamSession", "bascenev1.DualTeamSession")
content = content.replace("babase.MultiTeamSession", "bascenev1.MultiTeamSession")
content = content.replace("babase.TeamGameActivity", "bascenev1.TeamGameActivity")
content = content.replace("babase.Team", "bascenev1.Team")
content = content.replace("babase.Session", "bascenev1.Session")
content = content.replace("babase.Material", "bascenev1.Material")
content = content.replace("babase.WeakCall", "bascenev1.WeakCall")
content = content.replace("babase.DieMessage", "bascenev1.DieMessage")
content = content.replace("babase.OutOfBoundsMessage", "bascenev1.OutOfBoundsMessage")
content = content.replace("babase.DroppedMessage", "bascenev1.DroppedMessage")
content = content.replace("babase.HitMessage", "bascenev1.HitMessage")
content = content.replace("babase.ThawMessage", "bascenev1.ThawMessage")
content = content.replace("babase.NotFoundError", "bascenev1.NotFoundError")
content = content.replace("babase.getcollision", "bascenev1.getcollision")
content = content.replace("babase.app.lang", "bascenev1.app.lang")
content = content.replace("babase.MusicType", "bascenev1.MusicType")
content = content.replace("babase.getactivity", "bascenev1.getactivity")
content = content.replace("babase.getactivity", "bascenev1.getactivity")
content = content.replace("babase.CelebrateMessage", "bascenev1.CelebrateMessage")
content = content.replace("babase.ScoreConfig", "bascenev1.ScoreConfig")
content = content.replace("babase.ScoreType", "bascenev1.ScoreType")
content = content.replace("babase.GameResults", "bascenev1.GameResults")
content = content.replace("babase.getmaps", "bascenev1.app.classic.getmaps")
content = content.replace("babase.cameraflash", "bascenev1.cameraflash")
content = content.replace("babase.getmodel", "bascenev1.getmesh")
content = content.replace("babase.Map", "bascenev1.Map")
content = content.replace("babase.DeathType", "bascenev1.DeathType")
content = content.replace("babase.GameActivity", "bascenev1.GameActivity")
content = content.replace("_babase.app.stress_test_reset_timer", "_babase.app.classic.stress_test_reset_timer")
content = content.replace("babase.app.stress_test_reset_timer", "_babase.app.classic.stress_test_reset_timer")
content = content.replace("babase._map", "bascenev1._map")
content = content.replace("babase._session.", "bascenev1._session.")
content = content.replace("babase._activity", "bascenev1._activity")
content = content.replace("_babase.get_client_public_device_uuid", "_bascenev1.get_client_public_device_uuid")
content = content.replace("babase.PickedUpMessage", "bascenev1.PickedUpMessage")
content = content.replace("babase.PowerupMessage", "bascenev1.PowerupMessage")
content = content.replace("babase.FreezeMessage", "bascenev1.FreezeMessage")
content = content.replace("with babase.ContextRef(activity):", "with activity.context:")
content = content.replace("babase.Context", "babase.ContextRef")
content = content.replace("babase._dualteamsession", "bascenev1._dualteamsession")
content = content.replace("babase._freeforallsession", "bascenev1._freeforallsession")
content = content.replace("babase._multiteamsession", "bascenev1._multiteamsession")
content = content.replace("babase._gameactivity", "bascenev1._gameactivity")
content = content.replace("babase._powerup", "bascenev1._powerup")
content = content.replace("babase.Chooser", "bascenev1.Chooser")
content = content.replace("babase._lobby", "bascenev1._lobby")
content = content.replace("babase._stats", "bascenev1._stats")
content = content.replace("babase._team", "bascenev1._team")
content = content.replace("PlayerType", "PlayerT")
content = content.replace("babase.app.spaz_appearances", "babase.app.classic.spaz_appearances")
content = content.replace("babase._coopsession", "bascenev1._coopsession")
content = content.replace("babase._servermode", "baclassic._servermode")
content = content.replace("_babase.app.server", "babase.app.classic.server")
content = content.replace("_babase.chatmessage", "bascenev1.chatmessage")
content = content.replace("_babase.disconnect_client", "_bascenev1.disconnect_client")
content = content.replace("_babase.get_game_roster", "bascenev1.get_game_roster")
content = content.replace("_babase.get_public_party_max_size", "bascenev1.get_public_party_max_size")
content = content.replace("_babase.new_host_session", "bascenev1.new_host_session")
content = content.replace("babase._playlist", "bascenev1._playlist")
content = content.replace("model", "mesh")
content = content.replace("TimeType.REAL", "use `bascenev1.apptimer` in `activity.context` instead")
content = content.replace("_babase.app.coop_session_args", "babase.app.classic.coop_session_args")
content = content.replace("_babase.app.campaigns", "babase.app.classic.campaigns")

content = content.replace("_babase.newactivity", "bascenev1.newactivity")
content = content.replace("babase.Window", "bauiv1.Window")
content = content.replace("babase.Widget", "bauiv1.Widget")
content = content.replace("babase.widget", "bauiv1.widget")
content = content.replace("babase.containerwidget", "bauiv1.containerwidget")
content = content.replace("babase.scrollwidget", "bauiv1.scrollwidget")
content = content.replace("babase.buttonwidget", "bauiv1.buttonwidget")
content = content.replace("babase.textwidget", "bauiv1.textwidget")
content = content.replace("babase.checkboxwidget", "bauiv1.checkboxwidget")
content = content.replace("babase.imagewidget", "bauiv1.imagewidget")
content = content.replace("_babase.set_public_party_max_size", "bascenev1.set_public_party_max_size")
content = content.replace("_bauiv1", "bauiv1")
# Converting `ba.playsound(abc)` to `abc.play()` is tricky.
# Do it manually in case regex substitution fails.
content = re.sub(
    r'babase\.playsound\(\s*([^,\n]+),\s*([^,\n]+),\s*position=([^,\n]+)\)',
    r'\1.play(\2, position=\3)',
    content,
    flags=re.MULTILINE
)
content = re.sub("babase\.playsound\((.+?), (.+?), (.+?)\)", "\\1.play(\\2, \\3)", content)
content = re.sub(
    r'babase\.playsound\(([^,\n]+),\s*position=([^,\n]+)\)',
    r'\1.play(position=\2)',
    content
)
content = re.sub("babase\.playsound\((.*)\)", "\\1.play()", content)

content = content.replace("babase.internal.add_transaction", "bauiv1.app.plus.add_v1_account_transaction")
content = content.replace("babase.internal.run_transaction", "bauiv1.app.plus.run_v1_account_transaction")
content = content.replace("_babase.add_transaction", "bauiv1.app.plus.add_v1_account_transaction")
content = content.replace("_babase.run_transactions", "bauiv1.app.plus.run_v1_account_transactions")
content = content.replace("babase._store.get_store_layout", "bauiv1.app.classic.store.get_store_layout")
content = content.replace("babase.internal.get_store_layout", "bauiv1.app.classic.store.get_store_layout")
content = content.replace("babase.internal.connect_to_party", "bascenev1.connect_to_party")

content = content.replace("babase._store", "")
content = content.replace("babase.internal", "")
content = content.replace("bastd.ui", "bauiv1lib")
content = content.replace("bastd", "bascenev1lib")
content = content.replace("timetype=","")
content = content.replace("babase.columnwidget", "bauiv1.columnwidget")
content = content.replace("_babase.get_game_port", "bascenev1.get_game_port")
content = content.replace("_babase.get_chat_messages", "bascenev1.get_chat_messages")
content = content.replace("_babase.get_foreground_host_session", "bascenev1.get_foreground_host_session")
content = content.replace("_babase.get_foreground_host_activity", "bascenev1.get_foreground_host_activity")
content = content.replace("bascenev1.SessionPlayerNotFoundError", "babase.SessionPlayerNotFoundError")
content = content.replace("bascenev1", "bs")
content = content.replace("bauiv1", "bui")
content = content.replace("import bs", "import bascenev1 as bs")
content = content.replace("import bui", "import bauiv1 as bui")
content = content.replace("bslib", "bascenev1lib")
content = content.replace("builib", "bauiv1lib")
content = content.replace("from bs.", "from bascenev1.")
content = content.replace("from bui.", "from bauiv1.")

content = re.sub(r'bs\.Timer\(([^)]*)\bTimeType\.REAL\b([^)]*)\)', r'babase.AppTimer(\1\2)', content)

with open(sys.argv[2], "w") as f:
    f.write(content)

