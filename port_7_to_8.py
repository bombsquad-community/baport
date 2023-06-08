# Usage: ./port_7_to_8.py api_7_plugin.py > api_8_plugin.py
import re
import sys

with open(sys.argv[1], "rb") as fin:
    content = fin.read().decode("utf-8")

content = content.replace("# ba_meta require api 7", "# ba_meta require api 8")
content = content.replace("# ba_meta export game", "# ba_meta export bascenev1.GameActivity")

content = content.replace("_ba.", "_babase.")
content = content.replace("ba.", "babase.")
content = content.replace("import _ba", "import _babase")
content = content.replace("import ba", "import babase\nimport bauiv1 as bui\nimport bascenev1 as bs")
content = content.replace("babase.app.ui", "bui.app.classic.ui")
content = content.replace("babase.app.accounts_v1", "bui.app.classic.accounts")


# Comment out one of these as per your requirements, depending whether to
# stay local or if it'll also be needed to transmitted to the clients.

## For local:
# content = content.replace("_babase.screenmessage", "bui.screenmessage")
# content = content.replace("babase.screenmessage", "bui.screenmessage")
# content = content.replace("babase.getsound", "bui.getsound")
## For transmission:
content = content.replace("babase.screenmessage", "bs.screenmessage")
content = content.replace("babase.getsound", "bs.getsound")


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
content = content.replace("babase.gettexture", "bs.gettexture")
content = content.replace("babase.getactivity", "bs.getactivity")
content = content.replace("babase.getactivity", "bs.getactivity")
content = content.replace("babase.CelebrateMessage", "bs.CelebrateMessage")
content = content.replace("babase.ScoreConfig", "bs.ScoreConfig")
content = content.replace("babase.ScoreType", "bs.ScoreType")
content = content.replace("babase.GameResults", "bs.GameResults")
content = content.replace("babase.getmaps", "bs.app.classic.getmaps")
content = content.replace("babase.cameraflash", "bs.cameraflash")
content = content.replace("babase.getmodel", "bs.getmesh")
content = content.replace("model", "mesh")

content = content.replace("babase.Window", "bui.Window")
content = content.replace("babase.Widget", "bui.Widget")
content = content.replace("babase.widget", "bui.widget")
content = content.replace("babase.containerwidget", "bui.containerwidget")
content = content.replace("babase.scrollwidget", "bui.scrollwidget")
content = content.replace("babase.buttonwidget", "bui.buttonwidget")
content = content.replace("babase.textwidget", "bui.textwidget")
content = content.replace("babase.checkboxwidget", "bui.checkboxwidget")
content = content.replace("babase.imagewidget", "bui.imagewidget")
content = re.sub(
    r'babase\.playsound\(\s*([^,\n]+),\s*([^,\n]+),\s*position=([^,\n]+)\)',
    r'\1.play(\2, position=\3)',
    content,
    flags=re.MULTILINE
)
content = re.sub("babase\.playsound\((.+?), (.+?), (.+?)\)", "\\1.play(\\2, \\3)", content)
content = re.sub("babase\.playsound\((.*)\)", "\\1.play()", content)

content = content.replace("babase.internal.add_transaction", "bui.app.plus.add_v1_account_transaction")
content = content.replace("babase.internal.run_transaction", "bui.app.plus.run_v1_account_transaction")
content = content.replace("_babase.add_transaction", "bui.app.plus.add_v1_account_transaction")
content = content.replace("_babase.run_transactions", "bui.app.plus.run_v1_account_transactions")

content = content.replace("bastd.ui", "bauiv1lib")
content = content.replace("bastd", "bascenev1lib")

print(content)