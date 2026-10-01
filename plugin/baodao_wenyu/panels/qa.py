# -*- coding: utf-8 -*-
"""地志智询面板：自然语言问答 → 防幻觉答案 → 地图定位高亮。
后端：rag_server.py @127.0.0.1:8600（结构化检索优先，LLM 仅组织语言）。"""
import json
import urllib.request

from qgis.PyQt.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLineEdit, QPushButton, QTextBrowser, QLabel
from qgis.PyQt.QtCore import Qt, QThread, pyqtSignal
from qgis.core import QgsProject, QgsFeatureRequest, QgsRectangle

API = "http://127.0.0.1:8600/api/ask"


class AskThread(QThread):
    done = pyqtSignal(dict)
    fail = pyqtSignal(str)

    def __init__(self, q):
        super().__init__()
        self.q = q

    def run(self):
        try:
            req = urllib.request.Request(
                API, data=json.dumps({"question": self.q}).encode("utf-8"),
                headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=120) as r:
                self.done.emit(json.loads(r.read().decode("utf-8")))
        except Exception as e:
            self.fail.emit(str(e)[:200])


class QaDialog(QDialog):
    def __init__(self, iface):
        super().__init__(iface.mainWindow())
        self.iface = iface
        self.setWindowTitle("宝岛文舆 · 地志智询（答案仅来自库内真实查询）")
        self.resize(520, 420)
        lay = QVBoxLayout(self)
        row = QHBoxLayout()
        self.edit = QLineEdit(); self.edit.setPlaceholderText("例：南投县人口多少？斗六地名溯源？")
        self.btn = QPushButton("提问"); self.btn.clicked.connect(self.ask)
        row.addWidget(self.edit); row.addWidget(self.btn)
        lay.addLayout(row)
        self.out = QTextBrowser()
        lay.addWidget(self.out)
        self.locate = QPushButton("在地图上定位答案实体"); self.locate.setEnabled(False)
        self.locate.clicked.connect(self.locate_entities)
        lay.addWidget(self.locate)
        self.last = {}

    def ask(self):
        q = self.edit.text().strip()
        if not q:
            return
        self.btn.setEnabled(False); self.out.setPlainText("检索与生成中（首次含模型加载，最长约 60s）…")
        self.th = AskThread(q)
        self.th.done.connect(self.on_answer)
        self.th.fail.connect(self.on_fail)
        self.th.start()

    def on_answer(self, resp):
        self.last = resp
        self.btn.setEnabled(True)
        ans = resp.get("answer", "")
        srcs = "；".join(resp.get("sources", []) or [])
        el = resp.get("elapsed", "")
        self.out.setHtml(f"<p>{ans}</p><hr><p style='color:#666'>来源：{srcs}　耗时：{el}</p>")
        self.locate.setEnabled(bool(resp.get("entities")))

    def on_fail(self, err):
        self.btn.setEnabled(True)
        self.out.setPlainText("服务不可用：" + err + "\n请先启动 rag_server.py（:8600）；插件其余功能不受影响。")

    def locate_entities(self):
        """答案实体 → 已加载的 gaz_tw 图层过滤定位。"""
        ents = self.last.get("entities", []) or []
        names = [(e if isinstance(e, str) else (e.get("name") or "")) for e in ents]
        names = [n for n in names if n]
        if not names:
            return
        for lyr in QgsProject.instance().mapLayersByName("gaz_tw 地名点"):
            ors = " OR ".join(f"\"name\" = '{n.replace(chr(39), chr(39)*2)}'" for n in names[:20])
            lyr.setSubsetString(f"({ors})")
            feats = lyr.getFeatures(QgsFeatureRequest())
            bbox = QgsRectangle()
            bbox.setMinimal(True)
            for f in feats:
                bbox.combineExtentWith(f.geometry().boundingBox())
            if not bbox.isEmpty():
                bbox.scale(1.6)
                self.iface.mapCanvas().setExtent(bbox)
                self.iface.mapCanvas().refresh()
            lyr.removeSelection()
            for f in lyr.getFeatures():
                lyr.selectById(f.id())
            break
