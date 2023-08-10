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
content = re.sub(r'\bimport ba(\b|\.(\w+))', "import babase\nimport bauiv1 as bui\nimport bascenev1\nimport bascenev1 as bs", content)
content = content.replace("babase.app.ui", "bui.app.ui_v1")
content = content.replace("babase.app.accounts_v1", "bui.app.classic.accounts")

###################################################################################
# Comment out one of these as per your requirements, depending whether to
# stay local or if it'll also be needed to transmitted to the clients.

## For local:
if sys.argv[1] == "client":
    content = content.replace("_babase.screenmessage", "bui.screenmessage")
    content = content.replace("babase.screenmessage", "bui.screenmessage")
    content = content.replace("babase.getsound", "bui.getsound")
    content = content.replace("_babase.gettexture", "bui.gettexture")
    content = content.replace("babase.gettexture", "bui.gettexture")
    content = content.replace("babase.getmesh", "bui.getmesh")
    content = content.replace("babase.getcollisionmesh", "bui.getcollisionmesh")
else:
## For transmission:
    content = content.replace("_babase.screenmessage", "bs.broadcastmessage")
    content = content.replace("babase.screenmessage", "bs.broadcastmessage")
    content = content.replace("babase.getsound", "bs.getsound")
    content = content.replace("_babase.gettexture", "bs.gettexture")
    content = content.replace("babase.gettexture", "bs.gettexture")
    content = content.replace("babase.getmesh", "bs.getmesh")
    content = content.replace("babase.getcollisionmesh", "bs.getcollisionmesh")
###################################################################################
content = content.replace("babase.open_url", "bui.open_url")
content = content.replace("babase.IntSetting", "bs.IntSetting")
content = content.replace("babase.IntChoiceSetting", "bs.IntChoiceSetting")
content = content.replace("babase.FloatChoiceSetting", "bs.FloatChoiceSetting")
content = content.replace("babase.BoolSetting", "bs.BoolSetting")
content = content.replace("babase.Actor", "bs.Actor")
content = content.replace("babase.Player", "bs.Player")
content = content.replace("babase.PlayerDiedMessage", "bs.PlayerDiedMessage")
content = content.replace("babase.time", "bs.time")
content = content.replace("babase.Timer", "bs.Timer")
content = content.replace("babase.newnode", "bs.newnode")
content = content.replace("babase.Node", "bs.Node")
content = content.replace("babase.emitfx", "bs.emitfx")
content = content.replace("babase.animate", "bs.animate")
content = content.replace("babase.FreeForAllSession", "bs.FreeForAllSession")
content = content.replace("babase.DualTeamSession", "bs.DualTeamSession")
content = content.replace("babase.MultiTeamSession", "bs.MultiTeamSession")
content = content.replace("babase.TeamGameActivity", "bs.TeamGameActivity")
content = content.replace("babase.Team", "bs.Team")
content = content.replace("babase.Session", "bs.Session")
content = content.replace("babase.Material", "bs.Material")
content = content.replace("babase.WeakCall", "bs.WeakCall")
content = content.replace("babase.DieMessage", "bs.DieMessage")
content = content.replace("babase.OutOfBoundsMessage", "bs.OutOfBoundsMessage")
content = content.replace("babase.DroppedMessage", "bs.DroppedMessage")
content = content.replace("babase.HitMessage", "bs.HitMessage")
content = content.replace("babase.NotFoundError", "bs.NotFoundError")
content = content.replace("babase.getcollision", "bs.getcollision")
content = content.replace("babase.app.lang", "bs.app.lang")
content = content.replace("babase.MusicType", "bs.MusicType")
content = content.replace("babase.getactivity", "bs.getactivity")
content = content.replace("babase.getactivity", "bs.getactivity")
content = content.replace("babase.CelebrateMessage", "bs.CelebrateMessage")
content = content.replace("babase.ScoreConfig", "bs.ScoreConfig")
content = content.replace("babase.ScoreType", "bs.ScoreType")
content = content.replace("babase.GameResults", "bs.GameResults")
content = content.replace("babase.getmaps", "bs.app.classic.getmaps")
content = content.replace("babase.cameraflash", "bs.cameraflash")
content = content.replace("babase.getmodel", "bs.getmesh")
content = content.replace("babase.Map", "bascenev1.Map")
content = content.replace("babase.DeathType", "bs.DeathType")
content = content.replace("babase.GameActivity", "bascenev1.GameActivity")
content = content.replace("_babase.app.stress_test_reset_timer", "_babase.app.classic.stress_test_reset_timer")
content = content.replace("babase.app.stress_test_reset_timer", "_babase.app.classic.stress_test_reset_timer")
content = content.replace("babase._map", "bascenev1._map")
content = content.replace("babase._session.", "bascenev1._session.")
content = content.replace("babase._activity", "bascenev1._activity")
content = content.replace("_babase.get_client_public_device_uuid", "_bascenev1.get_client_public_device_uuid")
content = content.replace("babase.PickedUpMessage", "bs.PickedUpMessage")
content = content.replace("babase.PowerupMessage", "bs.PowerupMessage")
content = content.replace("babase.FreezeMessage", "bs.FreezeMessage")
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
content = content.replace("_babase.get_game_roster", "bs.get_game_roster")
content = content.replace("_babase.get_public_party_max_size", "bs.get_public_party_max_size")
content = content.replace("babase._playlist", "bascenev1._playlist")
content = content.replace("model", "mesh")
content = content.replace("TimeType.REAL", "use `bs.apptimer` in `activity.context` instead")

content = content.replace("babase.Window", "bui.Window")
content = content.replace("babase.Widget", "bui.Widget")
content = content.replace("babase.widget", "bui.widget")
content = content.replace("babase.containerwidget", "bui.containerwidget")
content = content.replace("babase.scrollwidget", "bui.scrollwidget")
content = content.replace("babase.buttonwidget", "bui.buttonwidget")
content = content.replace("babase.textwidget", "bui.textwidget")
content = content.replace("babase.checkboxwidget", "bui.checkboxwidget")
content = content.replace("babase.imagewidget", "bui.imagewidget")
content = content.replace("_bui", "bui")
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

content = content.replace("babase.internal.add_transaction", "bui.app.plus.add_v1_account_transaction")
content = content.replace("babase.internal.run_transaction", "bui.app.plus.run_v1_account_transaction")
content = content.replace("_babase.add_transaction", "bui.app.plus.add_v1_account_transaction")
content = content.replace("_babase.run_transactions", "bui.app.plus.run_v1_account_transactions")
content = content.replace("babase._store.get_store_layout", "bui.app.classic.store.get_store_layout")
content = content.replace("babase.internal.get_store_layout", "bui.app.classic.store.get_store_layout")

content = content.replace("babase._store", "")
content = content.replace("babase.internal", "")
content = content.replace("bastd.ui", "bauiv1lib")
content = content.replace("bastd", "bascenev1lib")
content = content.replace("timetype=","")
content = content.replace("babase.columnwidget", "bui.columnwidget")
content = content.replace("_babase.get_game_port", "bs.get_game_port")
content = content.replace("_babase.get_chat_messages", "bs.get_chat_messages")
content = content.replace("_babase.get_foreground_host_session","bs.get_foreground_host_session")
content = content.replace("_babase.get_foreground_host_activity","bs.get_foreground_host_activity")
content = re.sub(r'bs\.Timer\(([^)]*)\bTimeType\.REAL\b([^)]*)\)', r'babase.AppTimer(\1\2)', content)

with open(sys.argv[2], "w") as f:
    f.write(content)
