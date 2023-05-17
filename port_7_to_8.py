# Usage: ./port_7_to_8.py api_7_plugin.py > api_8_plugin.py
import re
import sys

with open(sys.argv[1], "rb") as fin:
    content = fin.read().decode("utf-8")

content = content.replace("# ba_meta require api 7", "# ba_meta require api 8")
content = content.replace("# ba_meta export plugin", "# ba_meta export babase.Plugin")

content = content.replace("_ba.", "_babase.")
content = content.replace("ba.", "babase.")
content = content.replace("import _ba", "import _babase")
content = content.replace("import ba", "import babase\nimport bauiv1 as bui")
content = content.replace("babase.app.ui", "bui.app.classic.ui")
content = content.replace("babase.app.accounts_v1", "bui.app.classic.accounts")
content = content.replace("babase.getsound", "bui.getsound")


# This needs to be done manually, depending whether the screenmessage
# will stay local only to the host or if it'll also be needed to
# transmitted to the clients.

# For local:
content = content.replace("_babase.screenmessage", "bui.screenmessage")
content = content.replace("babase.screenmessage", "bui.screenmessage")
# For transmission:
content = content.replace("_babase.screenmessage", "bascenev1.screenmessage")
content = content.replace("babase.screenmessage", "bascenev1.screenmessage")


content = content.replace("babase.Window", "bui.Window")
content = content.replace("babase.Widget", "bui.Widget")
content = content.replace("babase.widget", "bui.widget")
content = content.replace("babase.containerwidget", "bui.containerwidget")
content = content.replace("babase.scrollwidget", "bui.scrollwidget")
content = content.replace("babase.buttonwidget", "bui.buttonwidget")
content = content.replace("babase.textwidget", "bui.textwidget")
content = content.replace("babase.checkboxwidget", "bui.checkboxwidget")
content = content.replace("babase.imagewidget", "bui.imagewidget")
content = re.sub(r"babase\.playsound\((.*)\)", r"\1.play()", content)

content = content.replace("babase.internal.add_transaction", "bui.app.plus.add_v1_account_transaction")
content = content.replace("babase.internal.run_transaction", "bui.app.plus.run_v1_account_transaction")
content = content.replace("_babase.add_transaction", "bui.app.plus.add_v1_account_transaction")
content = content.replace("_babase.run_transactions", "bui.app.plus.run_v1_account_transactions")

print(content)