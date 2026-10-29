# -*- coding: utf-8 -*-
"""台湾人文地理 RAG 服务（结构化检索优先 + qwen3:8b 生成 + 来源回链）。
架构：用户问题 → qwen3:0.6b 意图分类 → PostGIS 结构化查询（真实数据毫秒级）
     → 上下文组装（含来源标注）→ qwen3:8b 生成 → 带来源回答。
启动：python rag_server.py（起 tw 库 + FastAPI :8600）"""
import io
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time

sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
import urllib.request

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn

# ── 环境配置：默认沿用本机 H 盘的 PG 实例；换机时改 config_local.py（.gitignore 已排除）或环境变量
def _load_cfg():
    cfg = {"PGCTL": r"H:\tools\conda_env_pg\Library\bin\pg_ctl.exe",
           "PGDATA": r"H:\tools\pgdata_htm2026",
           "HOST": "127.0.0.1", "PORT": 5433, "DB": "tw", "USER": "postgres", "PWD": "",
           "OLLAMA": "http://127.0.0.1:11434", "PG_LOG": r"H:\tools\pg_start.log"}
    env = {"PGCTL": "BAODAO_PGCTL", "PGDATA": "BAODAO_PGDATA", "HOST": "BAODAO_PG_HOST",
           "PORT": "BAODAO_PG_PORT", "DB": "BAODAO_PG_DB", "USER": "BAODAO_PG_USER",
           "PWD": "BAODAO_PG_PASSWORD", "OLLAMA": "BAODAO_OLLAMA"}
    for k, var in env.items():
        v = os.environ.get(var)
        if v:
            cfg[k] = int(v) if k == "PORT" else v
    for d in (os.path.dirname(os.path.abspath(__file__)), os.getcwd()):
        if os.path.exists(os.path.join(d, "config_local.py")):
            if d not in sys.path:
                sys.path.insert(0, d)
            try:
                from config_local import PG as local
                cfg.update({str(k).upper(): v for k, v in dict(local).items()})
                print("已载入 config_local.py")
            except Exception as e:
                print("config_local 读取失败：", str(e)[:100])
            break
    return cfg


# ── 区域配置（区域模板化）：库名与模式来自 config/region_<code>.json（BW_REGION 环境变量选择，默认 tw）
def _load_region():
    import json
    code = (os.environ.get("BW_REGION") or "tw").lower()
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "config", f"region_{code}.json")
    with open(path, encoding="utf-8") as f:
        r = json.load(f)
    r["code"] = code
    return r


class _RegionCur:
    """execute 时把 SQL 中的 "tw." 模式前缀重定向到区域配置的模式——同构库零 SQL 改动平移；
    来源标注串（如 "tw.gaz_tw"）也会跟着改写，正好与真实库名一致。其余方法/属性透传。"""
    def __init__(self, cur, schema):
        self._c, self._s = cur, schema
    def execute(self, q, *a, **k):
        return self._c.execute(q.replace("tw.", f"{self._s}."), *a, **k)
    def __getattr__(self, k):
        return getattr(self._c, k)


class _RegionConn:
    def __init__(self, conn, schema):
        self._c, self._s = conn, schema
    def cursor(self, *a, **k):
        return _RegionCur(self._c.cursor(*a, **k), self._s)
    @property
    def autocommit(self):
        return self._c.autocommit
    @autocommit.setter
    def autocommit(self, v):
        self._c.autocommit = v
    def __getattr__(self, k):
        return getattr(self._c, k)


CFG = _load_cfg()
REGION = _load_region()
CFG["DB"] = REGION["pg"]["dbname"]      # 区域配置决定目标库；连接参数仍可由 config_local / 环境变量覆盖
S_SCHEMA = REGION["pg"]["schema"]
PGCTL, PGDATA, OLLAMA = CFG["PGCTL"], CFG["PGDATA"], CFG["OLLAMA"]
CN_REG = re.compile(r"[\u4e00-\u9fff]{2,6}")

app = FastAPI(title=f"地志智询 · {REGION['name_zh']}人文地理 RAG")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)

# 自定义请求验证异常处理：
# Windows 默认 GBK 终端下 curl 会把中文转成 GBK 字节流，FastAPI 解析失败后
# 把 raw bytes 塞进 error dict，pydantic error message 含这些字节 → jsonable_encoder
# 用 utf-8 decode 崩。此 handler 兜底返回安全字符串。
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse as _JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


@app.exception_handler(RequestValidationError)
async def safe_validation_exception_handler(request: Request, exc: RequestValidationError):
    try:
        return _JSONResponse(
            status_code=400,
            content={"detail": str(exc.errors())},
        )
    except Exception:
        return _JSONResponse(
            status_code=400,
            content={"detail": "Request body could not be parsed. Windows 用户请用 chcp 65001 切换终端到 UTF-8，或用 python -c 'import urllib.request; ...' 发请求。"},
        )

_conn = [None]


def get_conn():
    import psycopg2
    if _conn[0] is not None:
        try:
            _conn[0].cursor().execute("SELECT 1")
        except Exception:
            _conn[0] = None
    if _conn[0] is None:
        for attempt in range(3):
            ensure_pg()
            try:
                _conn[0] = psycopg2.connect(host=CFG["HOST"], port=CFG["PORT"], dbname=CFG["DB"],
                                            user=CFG["USER"], password=CFG["PWD"])
                _conn[0] = _RegionConn(_conn[0], S_SCHEMA)
                _conn[0].autocommit = True
                break
            except Exception:
                time.sleep(5)
        else:
            raise RuntimeError("PG 无法启动/连接（重试 3 次）")
    return _conn[0]


def ensure_pg():
    """PG 已由服务/手动/容器管着就直接放行；只有本机存在 pg_ctl 与数据目录才尝试自启。"""
    try:
        socket.create_connection((CFG["HOST"], CFG["PORT"]), 1).close()
        return
    except OSError:
        pass
    if not (os.path.exists(PGCTL) and os.path.isdir(PGDATA)):
        return        # 换机时路径对不上也不在此处炸，交给 get_conn 的连接异常统一报
    with open("NUL", "w") as nul:
        subprocess.run([PGCTL, "-D", PGDATA, "-o", f"-p {CFG['PORT']}", "-l", CFG["PG_LOG"], "start"],
                       stdout=nul, stderr=nul, timeout=60)
    for _ in range(30):
        try:
            s = socket.create_connection((CFG["HOST"], CFG["PORT"]), 1)
            s.close()
            break
        except OSError:
            time.sleep(1)


def ollama_gen(model, prompt, timeout=120):
    req = urllib.request.Request(OLLAMA + "/api/generate",
        data=json.dumps({"model": model, "prompt": prompt, "stream": False,
                         "think": False, "keep_alive": "30m",
                         "options": {"temperature": 0.2, "num_predict": 320}}).encode())
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read()).get("response", "")


def ollama_embed(text):
    req = urllib.request.Request(OLLAMA + "/api/embed",
        data=json.dumps({"model": "bge-m3", "input": text}).encode())
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read()).get("embeddings", [[]])[0]


# ── 意图分类（关键词规则；place 放最后兜底，避免"在哪/哪天"误劫持设施与关注度问题） ──
INTENTS = [
    ("population", ["人口", "多少人", "人口数", "多少人住", "人口规模"]),
    ("event", ["事件", "冲突", "抗议", "发生什么", "态势", "活动", "报道", "GDELT"]),
    ("facility", ["机场", "港口", "码头", "电站", "变电站", "铁路", "车站", "设施", "电厂", "乡镇", "行政区"]),
    ("attention", ["关注度", "舆情", "热度", "趋势", "新闻", "峰值"]),
    ("place", ["哪里", "在哪", "位置", "坐标", "地名", "什么叫", "是什么地方"]),
]
# 释名类问句（因何而来/由来/得名/旧称…）：方志招牌场景，优先走 place_etymology 释名库
ETYM_KW = ["因何而来", "由来", "得名", "名字由来", "旧称", "原名", "为什么叫", "叫什么", "寓意", "典故", "名称由来"]


def classify(q):
    if any(k in q for k in ETYM_KW):
        return "etymology"
    if "属于" in q or "哪个乡镇" in q or "何乡镇" in q:
        return "place"
    for name, kws in INTENTS:
        if any(k in q for k in kws):
            return name
    return "place"


# ---- 简繁归一（改进 A · 2026-10-06 队长）----
# 基线：原仅 19 字硬编码表，对库内 31,998 条中文地名的简体还原率 30.52%
# 改进：OpenCC "s2tw" 词级转换为主（可保护"后里/万里"等地名本字不被逐字误转），
#       内置扩展表（709 字对，由库内用字频次统计生成）在缺 OpenCC 时兜底 → 实测还原率 84.55%
try:
    from opencc import OpenCC as _OpenCC
    _CC_S2T = _OpenCC("s2tw")   # 简体 -> 台湾正体
    _CC_T2S = _OpenCC("tw2s")   # 台湾正体 -> 简体
except Exception:               # 未安装 OpenCC 时回落内置表，不阻断服务启动
    _CC_S2T = _CC_T2S = None

_S2T_FB = str.maketrans('万与专业丛丝两严丧个丰临为丽举乌乐乔习书乱云亚产亩亲亿仅仆从仑仓仪众优会伞伟传伦体侨俩储儿兰关兴养冈军农冯冻准凑几凤凯划刘则刚别剑劝办务动励劲劳勋医华协单卖卢卧卫厂厅压参双发变叙叠叶号叽吕启吴员呜咙哑唠啰啸噜团围图圆圣块坚坛坜坝坞坟坠垄垅垒垦垭堑墙壮声壶处复夹奋奥妇妈娱婶孙学宁宝实审宪宽宾对寻导寿将尔尧尽层属岁岗岘岚峦嵘巩币帅师帐帘帮并广庆庐库应庙废廪开张弥弯弹强归当录彦径忧怀总恶恼悦惊愿戏战扑执扩扪扫扬抚护报拦拨择挂挞挟揽摆斋斩断无旧时昙显晓晖暂术杀权杠条杨极枢枧枪枫柜标栈栉栋栏树样栾桦桧桩梦检棱榄榉槠樱橱欢欧残毕气汇汉汤沟沤沥沦沪泄泷泽泾浅浆浊测济浓涂涌涛润涧涨涩淀渊渔湿滚满滥滨滩潜澜濑灯灵灿炉炖点炼烂烛烧焕爷牵犹独狭狮狱猎献玛环珑琏琼瑶瓮电画畅疗疮癞盐监盖盘眬矶矿码砖砾硕硗硚确礼祯禄禅种积稣稳窝竖笃笔笕笼筿箪篮篱籁籴粜粤粪红纤约级纪纯纱纲纳纵纸纹纺练组细织绍经绒结给绞继绮维绵绷绸综绿缆缉缎缘缝缪网翚耸耻职联聪胀胁胆胜胶脐脑脱腊腾舰艳艺节芜芦苇苋苍苎苏范茑荆荡荣荪药莱莳莹莺萝萤营萧萨蒋蓝虚虾蚁蚂蛎蛮补装裤见观规视览觉触计让训议讯记讲许论诊诏译诒试诗诚话诞语诰诲请诺读调谅谈谊谛谢谦贝贞负贡财贤货贮贯贰贵贸贺贼资赌赐赖赛赞赢赵赶践踯轨轩转轮软轻载辅辇辉辒辕辘辞边辽达迁过运还进远连迹逊遗遥邓邮邹邻郁郑酱采鉴銮针钓钖钗钟钢钦钩钰钱钿铁铃铅铔铜铝铭铳银铸铺链锁锄锅锋锡锣锥锦锭锯锻锽镒镜镬镰长闩闪闲间闷闸闹闻阁阄阉阔阙队阳阴阵阶陆陇陈陕险随隐难雾韦韩韬须顿颂预领颈颖颜额颧风飘飞饬饭饲饴饶饷饼馒驮驱驳驹驻驼驾驿骄骆验骑骡骧鱼鲈鲍鲤鲥鲫鲲鲸鳄鳌鳍鳕鳖鳗鸟鸠鸡鸢鸣鸦鸭鸯鸳鸶鸽鸾鸿鹄鹅鹊鹚鹤鹫鹭鹰麦齐齿龄龙龚龟', '萬與專業叢絲兩嚴喪個豐臨為麗舉烏樂喬習書亂雲亞產畝親億僅僕從侖倉儀眾優會傘偉傳倫體僑倆儲兒蘭關興養岡軍農馮凍準湊幾鳳凱劃劉則剛別劍勸辦務動勵勁勞勳醫華協單賣盧臥衛廠廳壓參雙發變敘疊葉號嘰呂啟吳員嗚嚨啞嘮囉嘯嚕團圍圖圓聖塊堅壇壢壩塢墳墜壟壠壘墾埡塹牆壯聲壺處復夾奮奧婦媽娛嬸孫學寧寶實審憲寬賓對尋導壽將爾堯盡層屬歲崗峴嵐巒嶸鞏幣帥師帳簾幫並廣慶廬庫應廟廢廩開張彌彎彈強歸當錄彥徑憂懷總惡惱悅驚願戲戰撲執擴捫掃揚撫護報攔撥擇掛撻挾攬擺齋斬斷無舊時曇顯曉暉暫術殺權槓條楊極樞梘槍楓櫃標棧櫛棟欄樹樣欒樺檜樁夢檢稜欖櫸櫧櫻櫥歡歐殘畢氣匯漢湯溝漚瀝淪滬洩瀧澤涇淺漿濁測濟濃塗湧濤潤澗漲澀澱淵漁溼滾滿濫濱灘潛瀾瀨燈靈燦爐燉點煉爛燭燒煥爺牽猶獨狹獅獄獵獻瑪環瓏璉瓊瑤甕電畫暢療瘡癩鹽監蓋盤矓磯礦碼磚礫碩磽礄確禮禎祿禪種積穌穩窩豎篤筆筧籠篠簞籃籬籟糴糶粵糞紅纖約級紀純紗綱納縱紙紋紡練組細織紹經絨結給絞繼綺維綿繃綢綜綠纜緝緞緣縫繆網翬聳恥職聯聰脹脅膽勝膠臍腦脫臘騰艦豔藝節蕪蘆葦莧蒼苧蘇範蔦荊蕩榮蓀藥萊蒔瑩鶯蘿螢營蕭薩蔣藍虛蝦蟻螞蠣蠻補裝褲見觀規視覽覺觸計讓訓議訊記講許論診詔譯詒試詩誠話誕語誥誨請諾讀調諒談誼諦謝謙貝貞負貢財賢貨貯貫貳貴貿賀賊資賭賜賴賽贊贏趙趕踐躑軌軒轉輪軟輕載輔輦輝轀轅轆辭邊遼達遷過運還進遠連跡遜遺遙鄧郵鄒鄰鬱鄭醬採鑑鑾針釣鍚釵鍾鋼欽鉤鈺錢鈿鐵鈴鉛錏銅鋁銘銃銀鑄鋪鏈鎖鋤鍋鋒錫鑼錐錦錠鋸鍛鍠鎰鏡鑊鐮長閂閃閒間悶閘鬧聞閣鬮閹闊闕隊陽陰陣階陸隴陳陝險隨隱難霧韋韓韜須頓頌預領頸穎顏額顴風飄飛飭飯飼飴饒餉餅饅馱驅駁駒駐駝駕驛驕駱驗騎騾驤魚鱸鮑鯉鰣鯽鯤鯨鱷鰲鰭鱈鱉鰻鳥鳩雞鳶鳴鴉鴨鴦鴛鷥鴿鸞鴻鵠鵝鵲鷀鶴鷲鷺鷹麥齊齒齡龍龔龜')
_T2S_FB = str.maketrans('萬與專業叢絲兩嚴喪個豐臨為麗舉烏樂喬習書亂雲亞產畝親億僅僕從侖倉儀眾優會傘偉傳倫體僑倆儲兒蘭關興養岡軍農馮凍準湊幾鳳凱劃劉則剛別劍勸辦務動勵勁勞勳醫華協單賣盧臥衛廠廳壓參雙發變敘疊葉號嘰呂啟吳員嗚嚨啞嘮囉嘯嚕團圍圖圓聖塊堅壇壢壩塢墳墜壟壠壘墾埡塹牆壯聲壺處復夾奮奧婦媽娛嬸孫學寧寶實審憲寬賓對尋導壽將爾堯盡層屬歲崗峴嵐巒嶸鞏幣帥師帳簾幫並廣慶廬庫應廟廢廩開張彌彎彈強歸當錄彥徑憂懷總惡惱悅驚願戲戰撲執擴捫掃揚撫護報攔撥擇掛撻挾攬擺齋斬斷無舊時曇顯曉暉暫術殺權槓條楊極樞梘槍楓櫃標棧櫛棟欄樹樣欒樺檜樁夢檢稜欖櫸櫧櫻櫥歡歐殘畢氣匯漢湯溝漚瀝淪滬洩瀧澤涇淺漿濁測濟濃塗湧濤潤澗漲澀澱淵漁溼滾滿濫濱灘潛瀾瀨燈靈燦爐燉點煉爛燭燒煥爺牽猶獨狹獅獄獵獻瑪環瓏璉瓊瑤甕電畫暢療瘡癩鹽監蓋盤矓磯礦碼磚礫碩磽礄確禮禎祿禪種積穌穩窩豎篤筆筧籠篠簞籃籬籟糴糶粵糞紅纖約級紀純紗綱納縱紙紋紡練組細織紹經絨結給絞繼綺維綿繃綢綜綠纜緝緞緣縫繆網翬聳恥職聯聰脹脅膽勝膠臍腦脫臘騰艦豔藝節蕪蘆葦莧蒼苧蘇範蔦荊蕩榮蓀藥萊蒔瑩鶯蘿螢營蕭薩蔣藍虛蝦蟻螞蠣蠻補裝褲見觀規視覽覺觸計讓訓議訊記講許論診詔譯詒試詩誠話誕語誥誨請諾讀調諒談誼諦謝謙貝貞負貢財賢貨貯貫貳貴貿賀賊資賭賜賴賽贊贏趙趕踐躑軌軒轉輪軟輕載輔輦輝轀轅轆辭邊遼達遷過運還進遠連跡遜遺遙鄧郵鄒鄰鬱鄭醬採鑑鑾針釣鍚釵鍾鋼欽鉤鈺錢鈿鐵鈴鉛錏銅鋁銘銃銀鑄鋪鏈鎖鋤鍋鋒錫鑼錐錦錠鋸鍛鍠鎰鏡鑊鐮長閂閃閒間悶閘鬧聞閣鬮閹闊闕隊陽陰陣階陸隴陳陝險隨隱難霧韋韓韜須頓頌預領頸穎顏額顴風飄飛飭飯飼飴饒餉餅饅馱驅駁駒駐駝駕驛驕駱驗騎騾驤魚鱸鮑鯉鰣鯽鯤鯨鱷鰲鰭鱈鱉鰻鳥鳩雞鳶鳴鴉鴨鴦鴛鷥鴿鸞鴻鵠鵝鵲鷀鶴鷲鷺鷹麥齊齒齡龍龔龜', '万与专业丛丝两严丧个丰临为丽举乌乐乔习书乱云亚产亩亲亿仅仆从仑仓仪众优会伞伟传伦体侨俩储儿兰关兴养冈军农冯冻准凑几凤凯划刘则刚别剑劝办务动励劲劳勋医华协单卖卢卧卫厂厅压参双发变叙叠叶号叽吕启吴员呜咙哑唠啰啸噜团围图圆圣块坚坛坜坝坞坟坠垄垅垒垦垭堑墙壮声壶处复夹奋奥妇妈娱婶孙学宁宝实审宪宽宾对寻导寿将尔尧尽层属岁岗岘岚峦嵘巩币帅师帐帘帮并广庆庐库应庙废廪开张弥弯弹强归当录彦径忧怀总恶恼悦惊愿戏战扑执扩扪扫扬抚护报拦拨择挂挞挟揽摆斋斩断无旧时昙显晓晖暂术杀权杠条杨极枢枧枪枫柜标栈栉栋栏树样栾桦桧桩梦检棱榄榉槠樱橱欢欧残毕气汇汉汤沟沤沥沦沪泄泷泽泾浅浆浊测济浓涂涌涛润涧涨涩淀渊渔湿滚满滥滨滩潜澜濑灯灵灿炉炖点炼烂烛烧焕爷牵犹独狭狮狱猎献玛环珑琏琼瑶瓮电画畅疗疮癞盐监盖盘眬矶矿码砖砾硕硗硚确礼祯禄禅种积稣稳窝竖笃笔笕笼筿箪篮篱籁籴粜粤粪红纤约级纪纯纱纲纳纵纸纹纺练组细织绍经绒结给绞继绮维绵绷绸综绿缆缉缎缘缝缪网翚耸耻职联聪胀胁胆胜胶脐脑脱腊腾舰艳艺节芜芦苇苋苍苎苏范茑荆荡荣荪药莱莳莹莺萝萤营萧萨蒋蓝虚虾蚁蚂蛎蛮补装裤见观规视览觉触计让训议讯记讲许论诊诏译诒试诗诚话诞语诰诲请诺读调谅谈谊谛谢谦贝贞负贡财贤货贮贯贰贵贸贺贼资赌赐赖赛赞赢赵赶践踯轨轩转轮软轻载辅辇辉辒辕辘辞边辽达迁过运还进远连迹逊遗遥邓邮邹邻郁郑酱采鉴銮针钓钖钗钟钢钦钩钰钱钿铁铃铅铔铜铝铭铳银铸铺链锁锄锅锋锡锣锥锦锭锯锻锽镒镜镬镰长闩闪闲间闷闸闹闻阁阄阉阔阙队阳阴阵阶陆陇陈陕险随隐难雾韦韩韬须顿颂预领颈颖颜额颧风飘飞饬饭饲饴饶饷饼馒驮驱驳驹驻驼驾驿骄骆验骑骡骧鱼鲈鲍鲤鲥鲫鲲鲸鳄鳌鳍鳕鳖鳗鸟鸠鸡鸢鸣鸦鸭鸯鸳鸶鸽鸾鸿鹄鹅鹊鹚鹤鹫鹭鹰麦齐齿龄龙龚龟')

def s2t(w):
    """简体 -> 繁体（台湾正体）：OpenCC 词级优先，内置扩展表兜底。"""
    return _CC_S2T.convert(w) if _CC_S2T else w.translate(_S2T_FB)

def t2s(w):
    """繁体 -> 简体：OpenCC 词级优先，内置扩展表兜底。"""
    return _CC_T2S.convert(w) if _CC_T2S else w.translate(_T2S_FB)

# boundary.name 英文→中文县市名（admin1/admin2 实为 GeoNames 代码，中文归属一律空间推导）
EN2CN = {"Taipei": "台北市", "NewTaipei": "新北市", "Taoyuan": "桃园市", "Taichung": "台中市",
         "Tainan": "台南市", "Kaohsiung": "高雄市", "Keelung": "基隆市", "HsinchuCity": "新竹市",
         "HsinchuCounty": "新竹县", "Changhua": "彰化县", "Miaoli": "苗栗县", "Nantou": "南投县",
         "Yulin": "云林县", "ChiayiCity": "嘉义市", "ChiayiCounty": "嘉义县", "Pingtung": "屏东县",
         "Yilan": "宜兰县", "Hualien": "花莲县", "Taitung": "台东县", "Penghu": "澎湖县",
         "Kinmen": "金门县", "Lienkiang": "连江县"}
def county_cn(en):
    return EN2CN.get((en or "").replace(" ", ""), en or "")


CN_STOP = {"多少", "人口", "哪里", "在哪", "介绍", "情况", "什么", "发生", "事件", "几个", "多少人口"}
SPLIT_RE = re.compile(r"[ 的在个哪些哪什么有多少和与及或比了是对从至约规模量类请帮介绍查属名型单元]")
# 县市名前缀（T2 修复）：用于「云林县三和在哪里？」这类"县名 + 地名"连写问句的剥离
CNTY_RE = re.compile(r"[\u4e00-\u9fff]{2,3}(?:县|市)")
def extract_entities(q):
    """从问题中提取候选实体：虚词切分 + 前缀扩展 + 停用词过滤 + 匹配库内已知名（优先）。"""
    cands = []
    # 【T2 修复·合入队员 B（2026-10-07）】先剥掉「X县/X市」前缀再取尾词候选。
    #   原前缀策略 q[:2]/q[:3] 在「云林县三和在哪里？」里前 3 字被"云林县"占满，
    #   真正的地名「三和」进不了候选 → 只剩县名实体，检索到 ADM2 行政记录而答非所问。
    #   此处仅**追加**候选（不删原有候选），因此不影响正常县名题（县域人口走 population
    #   意图、不经此分支；place 题原有候选也全部保留）。
    _stripped = CNTY_RE.sub("", q)   # 「云林县三和在哪里？」→「三和在哪里？」
    if _stripped and _stripped != q:
        for pre in (_stripped[:2], _stripped[:3]):
            if re.fullmatch(r"[\u4e00-\u9fff]{2,3}", pre) and pre not in cands:
                cands.append(pre)
    for pre in (q[:2], q[:3]):  # 问题前 2/3 字优先当实体（三和/和平/宜兰 等开头地名）
        if re.fullmatch(r"[\u4e00-\u9fff]{2,3}", pre) and pre not in cands:
            cands.append(pre)
    for p in SPLIT_RE.split(q):
        p = p.strip()
        for c in ([p] if len(p) >= 2 else []) + ([p[:2]] if len(p) >= 3 else []) + ([p[:3]] if len(p) > 3 else []):
            if c and len(c) >= 2 and c not in CN_STOP and c not in cands:
                cands.append(c)
    for w in CN_REG.findall(q):
        if 2 <= len(w) <= 4 and w not in CN_STOP and w not in cands:
            cands.append(w)
    # 剔除含阿拉伯数字的候选：'20' / '202' / '2024年台湾发生' 等不是地名，混入会污染实体过滤
    # （点年份的事件/关注度类问句尤甚——数字应交给时间抽取，而非当成实体名去查地名库）
    cands = [c for c in cands if not re.search(r"[0-9]", c)]
    cur = get_conn().cursor()
    out = []
    for w in cands[:10]:
        # 只接受库里真实存在的别名（高雄→Kaohsiung），拒绝噪声；简繁双查
        cur.execute("""SELECT DISTINCT alt FROM tw.gaz_alias WHERE alt = ANY(%s) LIMIT 2""",
                    ([w, s2t(w)],))
        for (alt,) in cur.fetchall():
            if alt and alt not in out and (alt in q or s2t(alt) in q or q.startswith(alt) or alt == w or alt == s2t(w)):
                out.append(alt)
    out = [x for x in out if not any(x != y and y.startswith(x) for y in out)]
    # 简繁归一后去重：'台中市' 与 '臺中市' 属同一实体的两种写法，
    # 若不去重会重复检索、挤占 q_population 的结果配额（改进 A 配套修复）
    _seen, _dedup = set(), []
    for x in out:
        k = t2s(x)
        if k not in _seen:
            _seen.add(k)
            _dedup.append(x)
    out = _dedup
    # 自由提问兜底：库内未命中时保留原文候选（让查询层自己做 ILIKE 模糊）
    if not out:
        out = cands[:2]
    return out[:3]


def extract_time(q):
    """从问句抽取时间限定（年份 + 可选月份），返回 (year, month) 或 (None, None)。

    事件 / 关注度类问句里的「2024年」是时间条件、不是地名实体——须与实体抽取分开处理：
    否则年份既被当作地名送进检索（查不到），真正的时间条件又缺失，导致"有数据却答不出"。
    """
    m = re.search(r"(20\d{2})\s*年(?:\s*(\d{1,2})\s*月)?", q)
    if not m:
        return None, None
    yr = int(m.group(1))
    mo = int(m.group(2)) if m.group(2) else None
    if mo is not None and not (1 <= mo <= 12):
        mo = None
    return yr, mo


def time_scope(table, alias, col, yr, mo=None):
    """生成"限定到某年 / 某月"的子查询片段（列名可指定），供事件、关注度的多段 SQL 复用。"""
    if mo:
        lo = f"{yr:04d}-{mo:02d}-01"
        nxt = f"{yr + 1:04d}-01-01" if mo == 12 else f"{yr:04d}-{mo + 1:02d}-01"
    else:
        lo, nxt = f"{yr:04d}-01-01", f"{yr + 1:04d}-01-01"
    return f"(SELECT * FROM {table} WHERE {col} >= '{lo}' AND {col} < '{nxt}') {alias}"


def q_population(q, ents):
    cur = get_conn().cursor()
    out, sources = [], []
    # 实体归一去重：简/繁同形实体（台中市/臺中市）会重复占用 out[:6] 名额，
    # 把另一实体（台北）整体挤出 → 对比题只答一半。与 q_place 同款修复。
    _dedup, _k = [], set()
    for _e in ents:
        _key = t2s(_e)
        if _key in _k:
            continue
        _k.add(_key)
        _dedup.append(_e)
    ents = _dedup
    # 全岛总量口径（含「台湾省」：省 1998 年已虚级化，现行无省级人口统计 → 以全台合计代表）
    # 实体级判断：**仅当用户自己写明了「台湾省」**（问句含「台湾省/臺灣省」，或实体确切是
    #   「台湾省」）才走「省级虚级化」口径说明；用户问「台湾人口」时并未提「省」，属正常
    #   全台口径提问，**不得**主动科普省级区划沿革。两种问法都取官方全台合计，只是说明行不同；
    #   并据此跳过该实体的 gaz 检索（避免 ADM1 历史台湾省 7,060,473 等污染口径）。
    _province_q = ("台湾省" in q or "臺灣省" in q) or any(t2s(e) == "台湾省" for e in ents)
    if not ents or any(k in q for k in ("全台", "全岛", "总量", "总计", "共")) or _province_q:
        cur.execute("SELECT sum(pop) FROM tw_demo_countypop")
        tot = cur.fetchone()[0]
        if tot:
            item = {"scope": "全台", "population_worldpop": int(tot), "source_year": 2020}
            if _province_q:
                item["说明"] = ("台湾省自1998年起已虚级化，现行无省级人口统计口径；上值为全台22县市"
                                "合计（WorldPop 2020）。库内名称含『臺灣省』的条目属历史区划或旧别名"
                                "（如臺北縣），不代表现行行政区人口，请勿采用其数字。")
            out.append(item)
            sources.append("tw_demo_countypop 合计（WorldPop 2020）")
    # ①-a 县辖市优先（tw_demo_townpop 精确名命中）：**仅当用户自己写明了『X市』后缀**时启用。
    #    根因：countypop（县市表）里只有「花蓮縣」，乡镇表里才有「花蓮市」。原逻辑用
    #    cn ILIKE '%花莲%' 模糊匹配 → 「花莲市」被抢答成花蓮縣的 292,578；正确值 199,037
    #    在乡镇表，而乡镇表原先只在 out 为空时才查 → 该分支永远走不到。
    #    安全性论证（已逐条核对库内数据，勿轻改）：
    #      · 乡镇表 tw_demo_townpop 里以「市」结尾的全部 14 条**都是县辖市**
    #        （彰化市/竹北市/員林市/南投市/斗六市/頭份市/花蓮市/屏東市/宜蘭市/臺東市/
    #          苗栗市/太保市/馬公市/朴子市），**没有**一条是直辖市/省辖市；
    #      · 乡镇表里没有任何「X县」「X区」形态条目。
    #    因此「X市」在乡镇表精确命中 ⟹ X 必为县辖市，采用乡镇口径即正确；而全部直辖市
    #    （台北/新北/桃园/台中/台南/高雄）与「X县」类问法在乡镇表无精确命中 → 零影响。
    #    只对**带「市」后缀的写法**生效：用户写「花莲人口」（无后缀）时不启用，不改既有语义。
    town_hit = {}
    for e in ents:
        if not (e and e.endswith("市") and CNTY_RE.fullmatch(e)):
            continue
        cur.execute("SELECT name, pop FROM tw_demo_townpop WHERE name = %s OR name = %s LIMIT 1",
                    (e, s2t(e)))
        row = cur.fetchone()
        if row:
            town_hit[t2s(e)] = row
    # ①-b 县市级官方口径（tw_demo_countypop，WorldPop 2020 行政区合计）：同一口径、可跨县市对比。
    #    gaz 里同一县市混着三种口径——都会区 PPLC（台北 7,871,900，含台北+新北）／
    #    市界 ADM2（台中 2,867,848）／城区点 PPLA2（台中 2,850,285）。若与官方口径混喂，
    #    模型会挑都会区那条当「台北市」，跨县市对比直接得出反结论。
    county_hit, county_geo = {}, {}
    for e in ents:
        # ①-a 已判定为县辖市的实体跳过县市表模糊匹配：否则「花莲市」仍会被
        #   countypop 的 cn ILIKE '%花莲%' 抢答成花蓮縣（本次修复的核心）。
        if t2s(e) in town_hit:
            continue
        cur.execute("SELECT n, pop FROM tw_demo_countypop WHERE n ILIKE %s OR cn ILIKE %s OR cn ILIKE %s OR cn ILIKE %s LIMIT 2",
                    (f"%{e}%", f"%{e}%", f"%{e[:2]}%", f"%{s2t(e)[:2]}%"))
        for n, pop in cur.fetchall():
            county_hit[t2s(e)] = (n, pop)
            out.append({"place": e, "county": n, "population_worldpop": pop,
                        "口径": "县市行政区合计(WorldPop 2020)"})
            sources.append("tw_demo_countypop（GADM 边界 × WorldPop 2020 分区）")
    # ①-c 县辖市行（乡镇表口径）：追加到县市行之后，口径行明确标注，供模型照抄。
    for e in ents:
        if t2s(e) not in town_hit:
            continue
        name, pop = town_hit[t2s(e)]
        out.append({"place": e, "name": name, "township_pop": pop,
                    "口径": "县辖市乡镇分区(WorldPop 2020)"})
        sources.append("tw_demo_townpop 乡镇分区（WorldPop 2020）")
    # ② gaz 明细：已是县市级的实体**只借坐标**（人口沿用官方口径，避免多口径串台）；
    #    非县市级实体照常给多形态明细（PPLC/PPL 等）。
    seen_pop = set()   # 同一 geonameid 经简/繁多别名 JOIN 会重复出行，去重保持证据干净
    for e in ents:
        # 「台湾省」类问法：省级历史/别名条目（ADM1 历史台湾省 7,060,473、ADM2 臺北縣旧别名
        #   『臺灣省』4,004,367）会污染人口口径 → 跳过其实体检索，省级人口一律用上面的官方全台合计。
        if _province_q and t2s(e) == "台湾省":
            continue
        cur.execute("""SELECT g.geonameid, g.name, g.fcode, g.population, cb.name, ST_Y(g.geom), ST_X(g.geom)
            FROM tw.gaz_alias a JOIN tw.gaz_tw g USING (geonameid)
            LEFT JOIN LATERAL (SELECT name FROM tw.boundary b
                               WHERE b.level=2 AND ST_Within(g.geom, b.geom) LIMIT 1) cb ON true
            WHERE a.alt = ANY(%s) AND g.population > 0
            ORDER BY g.population DESC LIMIT 4""", ([e, s2t(e)],))
        rows = cur.fetchall()
        if t2s(e) in county_hit:
            for gid, _n, _f, _p, _adm, la, lo in rows:
                seen_pop.add(gid)
                if t2s(e) not in county_geo:
                    county_geo[t2s(e)] = (round(la, 3), round(lo, 3))
            continue
        for gid, name, fcode, pop, adm, la, lo in rows:
            if gid in seen_pop:
                continue
            seen_pop.add(gid)
            out.append({"place": e, "name": name, "fcode": fcode, "population": pop,
                        "口径": {"PPLC": "都会区(含邻县)", "ADM1": "行政区(GeoNames)",
                                 "ADM2": "行政区(GeoNames)", "ADM2H": "历史区划(GeoNames)",
                                 "PPLA2": "城区点(GeoNames)"}.get(fcode, "地点(GeoNames)"),
                        "所在县市": county_cn(adm), "lat": round(la, 3), "lon": round(lo, 3)})
            sources.append(f"tw.gaz_tw/geonames geonameid 检索（{e}）")
    # 县市行补坐标（人口仍用官方合计，坐标来自 gaz 同实体首个记录）
    for it in out:
        # 注意：全台汇总条（scope=全台）没有 place 字段，必须跳过，否则 KeyError 会
        #        让 ask() 的兜底吞掉整批证据 → 答案退化成「查询结果为空」。
        if it.get("place") and "population_worldpop" in it and "lat" not in it:
            g = county_geo.get(t2s(it["place"]))
            if g:
                it["lat"], it["lon"] = g
    # 多实体对比题（如「台中市和台北谁人口多」）给足名额：单实体 6 条足够，
    #   多实体若仍砍到 6，第一个实体的 gaz 多形态行会占满，后面的实体与人口表行全被截掉
    #   → 对比题只答一半。故按实体数放宽上限。
    out = out[:6] if len(ents) <= 1 else out[:12]
    if not out:
        for e in ents:
            cur.execute("SELECT name, pop FROM tw_demo_townpop WHERE name ILIKE %s OR name ILIKE %s LIMIT 3",
                        (f"%{e}%", f"%{e[:2]}%"))
            for name, pop in cur.fetchall():
                out.append({"place": e, "name": name, "township_pop": pop})
                sources.append(f"WorldPop 2020 乡镇分区统计（{name}）")
    if not out:
        # 引导式空答（最后一级兜底）：县市/乡镇人口表都没有时，回查「同名地点有哪些」
        #   （不过滤人口）。目的：把「库里有什么」如实告诉用户，空答从「查询结果为空」
        #   升级为可引导的回答，同时绝不编造人口数。
        # 注意：用户问「X人口」时并未声称 X 是县/市，故**不得**主动提及「无 X县/X市」——
        #   只如实说明「X 在库内是村里/聚落级地名、无人口数」。县/市场前提的纠偏，仅当
        #   用户**自己写明了**「X县 / X市」时，才由 premise_notes 针对该写法给出。
        for e in ents:
            cur.execute("""SELECT count(DISTINCT g.geonameid) FROM tw.gaz_alias a
                JOIN tw.gaz_tw g USING (geonameid) WHERE a.alt = ANY(%s)""", ([e, s2t(e), t2s(e)],))
            total = cur.fetchone()[0]
            if not total:
                continue
            cur.execute("""SELECT DISTINCT g.geonameid, g.name, g.fcode, cb.name
                FROM tw.gaz_alias a JOIN tw.gaz_tw g USING (geonameid)
                LEFT JOIN LATERAL (SELECT name FROM tw.boundary b
                                   WHERE b.level=2 AND ST_Within(g.geom, b.geom) LIMIT 1) cb ON true
                WHERE a.alt = ANY(%s) ORDER BY 1 LIMIT 6""", ([e, s2t(e), t2s(e)],))
            spots = [f"{county_cn(r[3]) or '台湾'}·{r[1]}({r[2]})" for r in cur.fetchall()]
            out.append({
                "说明": f"库内没有『{e}』的人口统计数据；『{e}』是村里/聚落级地名，"
                        f"库内共{total}处、均无人口数，例如：{'、'.join(spots)}",
                "数据状态": "无人口数据·有同名地点"})
            sources.append("tw.gaz_tw（同名地点线索，人口字段未收录）")
    return out, sources


def q_events(q, ents):
    cur = get_conn().cursor()
    place_cond, params = "", []
    # 仅用"库内验证过的地点实体"过滤（events 表本身已是台湾域数据，非地名实体不得进过滤）
    en_ents = []
    zh_ents = []
    for e in ents:
        cur.execute("""SELECT DISTINCT g.name FROM tw.gaz_alias a JOIN tw.gaz_tw g USING (geonameid)
            WHERE a.alt = ANY(%s) LIMIT 2""", ([e, s2t(e)],))
        names = [r[0] for r in cur.fetchall()]
        if names:
            zh_ents.append(e)
            en_ents += [n for n in names if re.search(r"[a-zA-Z]", n)]
    pats = zh_ents + en_ents
    if pats:
        pat = "|".join(pats)
        place_cond = "AND (geo_name ~* %s OR actor1 ~* %s OR actor2 ~* %s)"
        params = [pat, pat, pat]
    # 时间范围：问句点了年份/月份就按该范围查，未点则沿用"近一年"默认口径。
    # 原实现把起点写死为 2025-06-01，问「2024年」时范围根本不含目标年份 → 库里有数据却答不出。
    yr, mo = extract_time(q)
    if yr:
        scope = time_scope("tw.events", "ev", "sqldate", yr, mo)
        tnote = f"{yr}年" + (f"{mo}月" if mo else "")
    else:
        scope = "(SELECT * FROM tw.events WHERE sqldate >= '2025-06-01') ev"
        tnote = "近一年"
    cur.execute(f"""SELECT root_name, rootcode, count(*), round(avg(goldstein)::numeric, 2),
        max(sqldate)::text, min(sqldate)::text
        FROM {scope} WHERE 1=1 {place_cond}
        GROUP BY root_name, rootcode ORDER BY 3 DESC LIMIT 8""", params)
    cats = cur.fetchall()
    CN_ROOT = {"01": "表态", "02": "呼吁", "03": "合作意向", "04": "磋商", "05": "外交合作", "06": "物质合作",
               "07": "提供援助", "08": "让步/屈服", "09": "调查", "10": "需求/主张", "11": "不赞成", "12": "抗议",
               "13": "威胁施压", "14": "制裁", "15": "武力部署", "16": "武装冲突", "17": "强制", "18": "袭击", "19": "战斗", "20": "大规模暴力"}
    cats = [[c[0], CN_ROOT.get(str(c[1])[:2], "") + "类", c[2], c[3], c[4], c[5]] for c in cats]
    cur.execute(f"""SELECT sqldate::text, root_name, geo_name, goldstein, mentions
        FROM {scope} WHERE 1=1 {place_cond}
        ORDER BY sqldate DESC, mentions DESC NULLS LAST LIMIT 8""", params)
    recent = cur.fetchall()
    cur.execute(f"""SELECT to_char(sqldate, 'YYYY-MM') m, count(*) FROM {scope}
        WHERE 1=1 {place_cond} GROUP BY to_char(sqldate, 'YYYY-MM') ORDER BY m""", params)
    monthly = cur.fetchall()
    cur.execute("SELECT min(sqldate)::text, max(sqldate)::text, count(*) FROM tw.events")
    span = cur.fetchone()
    # 问题点名的类别做定向计数（在时间范围内统计，不受 top8 截断影响）
    FOCUS = [("抗议", "12"), ("战斗", "19"), ("袭击", "18"), ("制裁", "14"), ("威胁", "13"), ("磋商", "04"), ("部署", "15")]
    focus = []
    for kw, code in FOCUS:
        if kw in q:
            cur.execute(f"""SELECT count(*) FROM {scope} WHERE rootcode LIKE %s {place_cond}""",
                        [code + "%"] + params)
            focus.append(f"{kw}类(CAMEO {code}) {tnote} {cur.fetchone()[0]} 条")
    cur.execute(f"SELECT count(*) FROM {scope} WHERE 1=1 {place_cond}", params)
    scope_n = cur.fetchone()[0]
    sources = ["tw.events（GDELT 1.0 双周采样，月分区表）"]
    return {"时间范围": tnote, "该范围事件数": scope_n,
            "收录时间范围": f"{span[0]} 至 {span[1]}，共 {span[2]} 条",
            "指定类别": focus, "categories": cats, "recent": recent, "monthly": monthly[-12:]}, sources


def q_facility(q, ents):
    # 县市简→繁/英对照（地名词典产物，用于县级空间过滤）
    COUNTY = {"台北": "Taipei", "新北": "New Taipei", "桃园": "Taoyuan", "台中": "Taichung",
              "台南": "Tainan", "高雄": "Kaohsiung", "基隆": "Keelung|Chilung", "新竹": "Hsinchu",
              "苗栗": "Miaoli", "彰化": "Changhua", "南投": "Nantou", "云林": "Yunlin",
              "嘉义": "Chiayi", "屏东": "Pingtung", "宜兰": "Yilan", "花莲": "Hualien",
              "台东": "Taitung", "澎湖": "Penghu", "金门": "Kinmen|Quemoy", "连江": "Lienchiang"}
    cur = get_conn().cursor()
    out, sources = [], []
    kind = None
    if "机场" in q or "機場" in q:
        kind, tbl = "机场", "tw.airports"
    elif "港口" in q or "碼頭" in q or "码头" in q or "漁港" in q or "渔港" in q:
        kind, tbl = "港口/码头", "tw.ports"
    elif "电力" in q or "電廠" in q or "电厂" in q or "变电站" in q or "變電" in q or "電站" in q:
        kind, tbl = "电力设施", "tw.power"
    elif "铁路" in q or "鐵路的" in q or "轨" in q:
        kind, tbl = "铁路", "tw.rail"
    else:
        tbl = None
    # 县市级空间过滤（宜兰的港口 → 县面内计数）：别名英文 → 简繁对照表兜底
    county_geo = None
    if ents and tbl:
        cur.execute("""SELECT DISTINCT g.name FROM tw.gaz_alias a JOIN tw.gaz_tw g USING (geonameid)
                       WHERE a.alt = ANY(%s) AND g.name ~* '[a-z]' LIMIT 3""", ([ents[0], s2t(ents[0])],))
        en_list = [r[0] for r in cur.fetchall()]
        if not en_list:
            for k, v in COUNTY.items():
                if k in (ents[0] or "") or k in q:
                    en_list = [v]
                    break
        if en_list:
            cur.execute("""SELECT ST_AsText(geom) FROM tw.boundary WHERE level=2 AND name ~* %s LIMIT 1""",
                        ("|".join(en_list),))
            r = cur.fetchone()
            if r:
                county_geo = r[0]
    if tbl:
        if county_geo:
            cur.execute(f"SELECT count(*) FROM {tbl} p WHERE ST_Within(p.geom, ST_GeomFromText(%s,4326))", (county_geo,))
            sub = cur.fetchone()[0]
            cur.execute(f"SELECT count(*) FROM {tbl}")
            total = cur.fetchone()[0]
            cur.execute(f"""SELECT COALESCE(name,'(未命名)'), ST_Y(ST_PointOnSurface(geom)), ST_X(ST_PointOnSurface(geom)) FROM {tbl} p
                            WHERE ST_Within(p.geom, ST_GeomFromText(%s,4326)) LIMIT 8""", (county_geo,))
            rows = cur.fetchall()
            out = {"统计": f"{ents[0]} 境内{kind} {sub} 处（全台 {kind} 共 {total} 条）",
                   "样例": [{"name": n, "lat": round(la, 3), "lon": round(lo, 3)} for n, la, lo in rows]}
            sources.append(f"{tbl} × tw.boundary 空间过滤（{ents[0]}）")
            return out, sources
        cur.execute(f"SELECT count(*) FROM {tbl}")
        total = cur.fetchone()[0]
        if "最大" in q and tbl == "tw.power":
            cur.execute("""SELECT COALESCE(name,'(未命名)'), ST_Y(ST_PointOnSurface(geom)), ST_X(ST_PointOnSurface(geom)) FROM tw.power
                           WHERE kind='plant' ORDER BY osm_id LIMIT 6""")
            rows = cur.fetchall()
            out = {"统计": f"电厂 {total} 处；OSM 未标注装机容量（已知缺口，见 power_capacity 记档），无法按容量排序，按名称列出部分电厂",
                   "样例": [{"name": n, "lat": round(la, 3), "lon": round(lo, 3)} for n, la, lo in rows]}
            sources.append("tw.power / tw.power_capacity（容量字段 OSM 缺失，诚实记档）")
            return out, sources
        cond, params = "", []
        if ents:
            cond = "WHERE name ILIKE %s"
            params = [f"%{ents[0]}%"]
            cur.execute(f"SELECT COALESCE(name,'(未命名)'), ST_Y(ST_PointOnSurface(geom)), ST_X(ST_PointOnSurface(geom)) FROM {tbl} {cond} LIMIT 12", params)
            rows = cur.fetchall()
            if not rows:  # 实体过滤无命中 → 回退全量样例
                cond, params = "", []
                cur.execute(f"SELECT COALESCE(name,'(未命名)'), ST_Y(ST_PointOnSurface(geom)), ST_X(ST_PointOnSurface(geom)) FROM {tbl} LIMIT 12")
                rows = cur.fetchall()
        else:
            cur.execute(f"SELECT COALESCE(name,'(未命名)'), ST_Y(ST_PointOnSurface(geom)), ST_X(ST_PointOnSurface(geom)) FROM {tbl} LIMIT 12")
            rows = cur.fetchall()
        out = [{"name": n, "lat": round(la, 3), "lon": round(lo, 3)} for n, la, lo in rows][:8]
        out = {"统计": f"{kind} 总数 {total} 条", "样例": out}
        sources.append(f"{tbl}（OSM Overpass，共 {total} 条）")
        return out, sources
    if "乡镇" in q or "行政" in q:
        cur.execute("SELECT count(*) FROM tw.admin_osm WHERE level=8")
        n8 = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM tw.boundary WHERE level=2")
        n2 = cur.fetchone()[0]
        sources.append("tw.admin_osm（OSM admin_level=8）/ tw.boundary（县市面）")
        return {"统计": f"乡镇级面 {n8} 个（覆盖 204/368），县市级面 {n2} 个"}, sources
    # 未指明设施类型 → 全类计数
    cur.execute("""SELECT '机场' k, count(*) FROM tw.airports UNION ALL
                   SELECT '港口/码头', count(*) FROM tw.ports UNION ALL
                   SELECT '电力设施', count(*) FROM tw.power UNION ALL
                   SELECT '铁路段', count(*) FROM tw.rail""")
    counts = {k: n for k, n in cur.fetchall()}
    sources.append("tw.airports / tw.ports / tw.power / tw.rail（OSM Overpass）")
    return {"统计": (f"机场 {counts.get('机场', 0)} · 港口/码头 {counts.get('港口/码头', 0)} · "
                    f"电力设施 {counts.get('电力设施', 0)} · 铁路 {counts.get('铁路段', 0)} 段")}, sources


def q_place(q, ents):
    cur = get_conn().cursor()
    out, sources = [], []
    SEL = """SELECT g.geonameid, g.name, g.fclass, g.fcode, cb.name, tb.name, g.population, g.elev_m,
            ST_Y(g.geom), ST_X(g.geom), a.alt
            FROM tw.gaz_alias a JOIN tw.gaz_tw g USING (geonameid)
            LEFT JOIN LATERAL (SELECT name FROM tw.boundary b
                               WHERE b.level=2 AND ST_Within(g.geom, b.geom) LIMIT 1) cb ON true
            LEFT JOIN LATERAL (SELECT name FROM tw.admin_osm o
                               WHERE o.level=8 AND ST_Within(g.geom, o.geom) LIMIT 1) tb ON true"""
    # 问题点名县市 → 只取该县境内条目（防 LIMIT 按人口截掉小村）；无命中回退全域
    cty = next((cn for cn in EN2CN.values() if cn in q), None)
    for e in ents[:2]:
        rows = []
        if cty:
            en = next((k for k, v in EN2CN.items() if v == cty), None)
            if en:
                cur.execute(SEL + """ WHERE (a.alt = ANY(%s) OR g.name ILIKE %s)
                        AND ST_Within(g.geom, (SELECT geom FROM tw.boundary b
                            WHERE b.level=2 AND b.name ~* %s LIMIT 1))
                        ORDER BY g.population DESC NULLS LAST LIMIT 4""",
                            ([e, s2t(e)], f"%{e}%", en))
                rows = cur.fetchall()
        if not rows:
            cur.execute(SEL + """ WHERE a.alt = ANY(%s) OR g.name ILIKE %s
                    ORDER BY g.population DESC NULLS LAST LIMIT 4""", ([e, s2t(e)], f"%{e}%"))
            rows = cur.fetchall()
        seen = set()
        for r in rows:
            if r[0] in seen:
                continue
            seen.add(r[0])
            out.append({"query": e, "name": r[1], "fcode": r[3], "所在县市": county_cn(r[4]),
                        "所在乡镇": r[5], "elevation": r[7],
                        "lat": round(r[8], 3), "lon": round(r[9], 3), "zh": r[10]})
        # 10-09 口径收口：地名分支不再携带 gaz_tw.population（同一县市混 PPLC 都会区/
        # ADM2 市界/PPLA2 城区点三种口径，如「臺北縣」答 4,004,367 会与人口图层
        # WorldPop 4,404,039 冲突）。人口问题一律由 q_population 按 WorldPop 口径回答，
        # 地名题只答位置与沿革，不报人口。
        sources.append(f"tw.gaz_tw（GeoNames 45,572 条，中文别名约 3.2 万条）")
    return out, sources


# ── 地名释名库（「地志智询」招牌场景：得名由来/旧称考据）──
# 结构化检索优先：事实全部来自这张 curated 小表（整理自公开地名文献/县市志），模型只组织语言。
# 数据规模刻意做小（30+ 条主要地名）：条数少、每条可核查、来源可标注，与防幻觉红线一致。
# 平移新区域：给对应 schema 灌一份同名表即可（区域模板化机制的一部分）。
_ETYM = [
    ("彰化", ["半线", "半線"], "清雍正元年（1723年）设县，取「彰显王化」之意定名彰化；此前为平埔族巴布萨社「半线」之地。", "彰化县志"),
    ("台北", ["臺北", "大加蚋"], "清光绪元年（1875年）置台北府，因在台湾府之北得名；旧称「大加蚋」，源自凯达格兰语。", "台北市文献"),
    ("高雄", ["打狗"], "旧称「打狗」（平埔族语 Takao），日治大正九年（1920年）取日语同音改设高雄州，沿用至今。", "高雄市志"),
    ("基隆", ["雞籠", "鸡笼"], "旧称「鸡笼」（源自凯达格兰族名/基隆河旧称），清同治年间取「基地昌隆」之意改名基隆。", "基隆市志"),
    ("新竹", ["竹塹", "竹堑"], "旧称「竹堑」（平埔族竹堑社），清光绪八年（1882年）改名新竹。", "新竹市志"),
    ("台南", ["臺南", "赤崁", "承天府"], "明郑时期置承天府；清改台湾府，光绪年间府治北移后俗称「台南」；旧称赤崁（平埔语 Sakkam）。", "台南市志"),
    ("台中", ["臺中", "大墩", "東大墩", "东大墩"], "清领中期称「东大墩」；1887年台湾建省后择为省城，取「台湾之中」之意设台中府。", "台中市志"),
    ("宜兰", ["宜蘭", "噶玛兰", "噶瑪蘭"], "旧为平埔族噶玛兰族居住地；清嘉庆年间设厅时取「兰」字定名宜兰。", "宜兰县志"),
    ("花莲", ["花蓮", "洄瀾", "洄澜"], "旧称「洄澜」（洄澜溪）；后因地方士绅嫌「洄澜」欠雅，取其台语近音改写为「花莲」。", "花莲县志"),
    ("台东", ["臺東", "寶桑", "宝桑"], "旧称「宝桑」（卑南族音译）；清设卑南厅，后因其在台湾东部改称台东。", "台东县志"),
    ("屏东", ["屏東", "阿緱", "阿猴"], "清设阿猴县丞；后因治所位于半屏山之东，改名屏东。", "屏东县志"),
    ("苗栗", ["貓裏", "猫里"], "旧称「猫里」（道卡斯族猫里社音译），清光绪年间改名苗栗。", "苗栗县志"),
    ("南投", [], "名称源自平埔族「南投社」；清置南投县丞，沿用社名。", "南投县志"),
    ("云林", ["雲林", "林圯埔"], "清嘉庆年间设县，因县治附近「云林坪」得名（今县治已移至斗六）。", "云林县志"),
    ("嘉义", ["嘉義", "諸羅", "诸罗"], "旧称「诸罗」（诸罗山社）；乾隆年间林爽文事件中乡民守城有功，清廷取「嘉勉忠义」之意改名嘉义。", "嘉义市志"),
    ("桃园", ["桃園", "桃仔園", "虎茅庄"], "旧称「虎茅庄」，后因移民遍植桃树改称「桃仔园」，日治时期定名桃园。", "桃园市志"),
    ("新北", [], "2010年台北县升格为直辖市，取「新台北」之意定名新北市。", "新北市志"),
    ("澎湖", ["彭湖"], "名称早见于元代文献；因港外波涛澎湃、港内水静如湖而得名。", "澎湖县志"),
    ("金门", ["金門", "浯洲", "仙洲"], "旧称浯洲；明洪武年间置守御千户所，取「固若金汤、雄镇海门」之意定名金门。", "金门县志"),
    ("马祖", ["馬祖", "妈祖", "媽祖"], "因南竿岛马祖村得名（传为妈祖林默娘灵穴所在）。", "连江县志（马祖）"),
    ("淡水", ["滬尾", "沪尾"], "「淡水」源自淡水河名（平埔族语译音）；清曾以「沪尾」称河口街市。", "淡水镇志"),
    ("鹿港", [], "清领时期因港口为鹿皮等土产集散要地，得名鹿港。", "鹿港镇志"),
    ("艋舺", [], "源自平埔语「独木舟」音译，因渡口舟楫云集得名；今属台北市万华区。", "台北市文献"),
    ("大稻埕", [], "因早期稻谷集散、晒谷大埕（广场）得名；今属台北市大同区。", "台北市文献"),
    ("三峡", ["三峽", "三角涌"], "旧称「三角涌」（因大汉溪、三峡河、横溪三水交汇），日治时期改以「三峡」称之。", "三峡区志"),
    ("九份", [], "清领后期村落仅九户人家，出丁九份，故名九份。", "瑞芳区志"),
    ("玉山", [], "因山巅积雪洁白如玉得名；布农族称 Patungkuonʲ（石英之山），日治时期曾称新高山。", "玉山国家公园文献"),
    ("日月潭", [], "因湖形轮廓一说如日轮、一说如月钩得名；邵族称「水沙连」。", "南投县志"),
    ("阿里山", [], "名称源自邹族社名之音译，沿用为山名与林区名。", "嘉义县志"),
    ("兰屿", ["蘭嶼", "紅頭嶼", "红头屿"], "旧称红头屿；因岛上盛产兰花（台湾蝴蝶兰原产地）改名兰屿。", "台东县志"),
    ("绿岛", ["綠島", "火燒島", "火烧岛"], "旧称「火烧岛」（夕照下山峦暗红似火），后以满岛绿意更名绿岛。", "台东县志"),
    ("垦丁", ["墾丁"], "清末官方募壮丁于此开垦，遂有「垦丁」之称。", "恒春镇志"),
    ("莺歌", ["鶯歌", "莺歌石"], "因境内莺歌石（形似鹦鹉的巨石）得名。", "莺歌区志"),
    ("士林", [], "旧称「八芝兰林」（平埔族语 Pattsiran 音译），后雅化为「士林」。", "台北市文献"),
    ("北投", [], "源自平埔族语 Patauw（意为女巫），因硫磺谷地热喷气如烟而得名。", "北投区志"),
    ("安平", [], "明郑时期郑成功以其故乡福建泉州「安平镇」命名此港。", "台南市志"),
]
_etym_ready = False


def ensure_etymology():
    """释名小表：启动问答前幂等建表+灌数据（空表才灌，不覆盖人工订正）。"""
    global _etym_ready
    if _etym_ready:
        return
    cur = get_conn().cursor()
    cur.execute("""CREATE TABLE IF NOT EXISTS tw.place_etymology(
        name text PRIMARY KEY, aliases text[] NOT NULL DEFAULT '{}',
        etymology text NOT NULL, source text NOT NULL)""")
    cur.execute("SELECT count(*) FROM tw.place_etymology")
    if cur.fetchone()[0] == 0:
        for name, al, et, src in _ETYM:
            cur.execute("INSERT INTO tw.place_etymology(name,aliases,etymology,source)"
                        " VALUES (%s,%s,%s,%s) ON CONFLICT (name) DO NOTHING",
                        (name, al, et, src))
    _etym_ready = True


def q_etymology(q, ents):
    """释名题：先查 curated 释名库；未命中则回退地名库已有信息（方案二兜底话术），
    而不是 flat 回答「未提供相关信息」。"""
    ensure_etymology()
    cur = get_conn().cursor()
    out, sources = [], []
    hits = []
    for e in ents[:2]:
        cur.execute("""SELECT name, array_to_string(aliases, '、'), etymology, source
                FROM tw.place_etymology
                WHERE name = ANY(%s) OR aliases && %s OR strpos(%s, name) > 0
                LIMIT 3""", ([e, s2t(e)], [e, s2t(e)], e))
        for r in cur.fetchall():
            if r[0] not in [h[0] for h in hits]:
                hits.append(r)
    if hits:
        for name, al, et, src in hits:
            out.append({"地名": name, "旧称": al or "—", "得名由来": et, "记载来源": src})
        sources.append(f"tw.place_etymology（地名释名库 {len(_ETYM)} 条，整理自县志/地名文献）")
        return out, sources
    # 未命中：把地名库已有的别名/隶属/坐标亮出来，由来缺口如实说明
    out, sources = q_place(q, ents)
    note = ("释名库暂未收录该地名的由来。请先如实说明这一缺口，"
            "再把下列结构化信息（旧称/所在县市乡镇/坐标）逐条列出，不要只重复缺口说明")
    if out:
        out.insert(0, {"说明": note})
    else:
        out = [{"说明": note + "；且地名库也未检索到该条目"}]
    return out, sources


def q_attention(q, ents):
    cur = get_conn().cursor()
    # 关注度同样支持问句的时间限定（原实现固定取全表最新值，问「2024年」答不出）
    yr, mo = extract_time(q)
    if yr:
        scope = time_scope("tw.ts_attention", "ta", "d", yr, mo)
        tnote = f"{yr}年" + (f"{mo}月" if mo else "")
    else:
        scope, tnote = "tw.ts_attention", "全时段"
    cur.execute(f"SELECT d::text, volume FROM {scope} ORDER BY d DESC LIMIT 2")
    latest = cur.fetchall()
    cur.execute(f"SELECT round(avg(volume)::numeric, 3), max(volume), min(volume) FROM {scope}")
    stat = cur.fetchone()
    cur.execute(f"SELECT d::text, volume FROM {scope} ORDER BY volume DESC LIMIT 1")
    peak = cur.fetchone()
    cur.execute(f"""SELECT d::text, volume FROM {scope}
                   WHERE d >= (SELECT max(d) FROM {scope}) - interval '30 days' ORDER BY d""")
    recent30 = cur.fetchall()
    cur.execute(f"""SELECT to_char(date_trunc('month', d), 'YYYY-MM') m, round(avg(volume)::numeric, 3)
                   FROM {scope} GROUP BY 1 ORDER BY 1""")
    monthly = cur.fetchall()
    cur.execute(f"SELECT d::text, volume FROM {scope} ORDER BY d DESC OFFSET 364 LIMIT 1")
    year_ago = cur.fetchone()
    sources = ["tw.ts_attention（GDELT DOC timeline，710 天每日）"]
    return {"时间范围": tnote, "latest": latest, "avg_max_min": stat, "peak": peak,
            "recent_30d": recent30[-10:], "monthly_avg": monthly[-13:], "year_ago": year_ago}, sources


class Ask(BaseModel):
    question: str


class Locate(BaseModel):
    names: list
    county: str = ""


# ───────────────────────── Web 查看端接口（矢量瓦片 / 图层元数据 / 实体定位）
# 白名单机制：只有下列民用要素表可经接口对外提供。《规范》禁止表示的受限要素入库即剔除（库内不设对应要素表），
# 核电站在导出层按燃料类型与名称双重排除（《公开地图内容表示规范》第二十条）——非民用要素不进任何接口、快照与页面。
def _tile_bounds(z, x, y):
    """瓦片号 → (4326 经纬度范围, 3857 米范围)，用于索引过滤与 MVT 裁剪。"""
    import math
    n = 2.0 ** z
    lon0, lon1 = x / n * 360.0 - 180.0, (x + 1) / n * 360.0 - 180.0
    lat0 = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * (y + 1) / n))))
    lat1 = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / n))))
    R = 6378137.0
    return ((lon0, lat0, lon1, lat1),
            (20037504.27 * (x / n * 2 - 1), 20037504.27 * (1 - (y + 1) / n * 2),
             20037504.27 * ((x + 1) / n * 2 - 1), 20037504.27 * (1 - y / n * 2)))


# props 为 MVT 携带的属性列；simplify=True 时按层级做 ST_Simplify；order 用于要素上限截断时保大头。
# 说明：人口图层没有独立的多边形表——人口是挂在边界表上的属性。
#   pop_county = tw.boundary(level=2) ⋈ public.tw_demo_countypop.n，22 个县市
#   town       = tw.admin_osm(level=8) ⋈ public.tw_demo_townpop.name，204 个乡镇
# 因此这两层用 `sql` 直接给完整子查询（其余字段仅作元数据），不依赖手工建的 tw.pop_county/tw.pop_town。
_POP_COUNTY_SQL = (
    "SELECT ST_Transform(b.geom,3857) AS g, c.cn AS name, c.pop AS pop "
    "FROM tw.boundary b JOIN public.tw_demo_countypop c ON b.name = c.n "
    "WHERE b.level = 2 AND b.geom IS NOT NULL"
)
_POP_TOWN_SQL = (
    "SELECT ST_Transform(a.geom,3857) AS g, t.name AS name, t.pop AS pop "
    "FROM tw.admin_osm a JOIN public.tw_demo_townpop t ON a.name = t.name "
    "WHERE a.level = 8 AND a.geom IS NOT NULL"
)
MVT_LAYERS = {
    "pop_county": dict(sql=_POP_COUNTY_SQL, props=["name", "pop"], geom="g",
                       kind="polygon", label="县市人口", minzoom=3, simplify=True, order="pop DESC", limit=64),
    "town": dict(sql=_POP_TOWN_SQL, props=["name", "pop"], geom="g",
                 kind="polygon", label="乡镇人口", minzoom=7, simplify=True, order="pop DESC", limit=400),
    "county_line": dict(table="tw.boundary", props=["name"], geom="geom",
                        kind="polygon", label="县市界", minzoom=3, simplify=True, where="level=2", limit=64),
    "town_line": dict(table="tw.admin_osm", props=["name"], geom="geom",
                      kind="polygon", label="乡镇界", minzoom=8, simplify=True, where="level=8", limit=400),
    "roads": dict(table="tw.roads", props=["class", "name"], geom="geom",
                  kind="line", label="道路", minzoom=6, simplify=True, order="class", limit=3000,
                  where="class IN ('motorway','trunk','primary','secondary')"),
    "rail": dict(table="tw.rail", props=["kind", "name"], geom="geom",
                 kind="line", label="铁路", minzoom=7, simplify=True, limit=1500),
    "water_line": dict(table="tw.osm_water_line", props=["wclass", "name"], geom="geom",
                       kind="line", label="水系", minzoom=7, simplify=True, limit=3000,
                       where="wclass IN ('river','stream')"),
    "water_poly": dict(table="tw.osm_water_poly", props=["kind", "name"], geom="geom",
                       kind="polygon", label="湖库", minzoom=6, simplify=True, limit=1200,
                       where="kind IN ('reservoir','lake','water','riverbank')"),
    # 主要城市：原依赖手工表 tw.cities_major（不在 pipeline 生成范围内，换机恢复即缺）；
    # 改从 gaz_tw 取人口最多的 20 个城镇（与 web/data/cities.geojson 快照口径一致，20 名后数量级断层）。
    "cities": dict(table="tw.gaz_tw", props=["name", "population AS pop"], geom="geom",
                   kind="point", label="主要城市", minzoom=4,
                   order="population DESC NULLS LAST", limit=20,
                   where="fclass='P' AND population IS NOT NULL"),
    "places": dict(table="tw.gaz_tw", props=["name", "population AS pop", "fclass AS kind"], geom="geom",
                   kind="point", label="聚落点", minzoom=9, order="population DESC NULLS LAST", limit=1500,
                   where="fclass='P'"),
    "peaks": dict(table="tw.gaz_tw", props=["name", "elev_m AS elev", "fclass AS kind"], geom="geom",
                  kind="point", label="山峰会堂", minzoom=9, order="elev_m DESC NULLS LAST", limit=800,
                  where="fclass='T'"),
    "ports": dict(table="tw.ports", props=["name", "kind"], geom="geom",
                  kind="point", label="港口", minzoom=8, order="name", limit=800),
    "power": dict(table="tw.power_capacity", props=["name", "fuel", "cap_mw"], geom="geom",
                  kind="point", label="电厂", minzoom=9, order="cap_mw DESC NULLS LAST", limit=800),
    "events": dict(table="tw.events", props=["to_char(sqldate,'YYYY-MM-DD') AS date", "root_name AS theme",
                                             "mentions", "geo_name AS place"], geom="geom",
                   kind="point", label="事件点", minzoom=5, order="mentions DESC", limit=2500,
                   where="sqldate >= '2024-01-01'"),
}


@app.get("/api/layers")
def api_layers():
    """前端图层面板的元数据来源（含中文标签、几何类型、可见层级）。"""
    return [{"id": k, "label": v["label"], "kind": v["kind"], "minzoom": v["minzoom"],
             "fields": [p.split(" AS ")[-1].split()[0] for p in v["props"]]}
            for k, v in MVT_LAYERS.items()]


@app.get("/mvt/{name}/{z}/{x}/{y}.pbf")
def mvt(name: str, z: int, x: int, y: int):
    spec = MVT_LAYERS.get(name)
    if not spec or z < spec["minzoom"] or z > 14:
        return Response(status_code=404)
    (lon0, lat0, lon1, lat1), (minx, miny, maxx, maxy) = _tile_bounds(z, x, y)
    # 1 瓦片单位 ≈ 40075016/(2^z*4096) 米；低层级简化，高层级保原细节
    tol = 40075016.686 / (2 ** z * 4096) * (2 if z < 11 else 0)
    g = spec["geom"]
    sel = ", ".join(spec["props"])
    out_cols = ", ".join(p.split(" AS ")[-1].split()[0] for p in spec["props"])   # 内层已改名，外层只能引别名
    where = spec.get("where", "true")
    order = f'ORDER BY {spec["order"]}' if spec.get("order") else ""
    if spec.get("sql"):
        # 已提供完整子查询（含 join 与 3857 变换，几何列名见 spec["geom"]），此处只做瓦片索引过滤
        inner = (f'SELECT * FROM ({spec["sql"]}) src '
                 f'WHERE ST_Intersects(ST_Transform(g,4326), '
                 f'ST_MakeEnvelope({lon0},{lat0},{lon1},{lat1},4326)) {order} LIMIT {spec["limit"]}')
    else:
        inner = (f'SELECT ST_Transform({g},3857) AS g, {sel} FROM {spec["table"]} '
                 f'WHERE {where} AND ST_Intersects({g}, ST_MakeEnvelope({lon0},{lat0},{lon1},{lat1},4326)) '
                 f'{order} LIMIT {spec["limit"]}')
    simp = f'ST_Simplify(g,{tol})' if spec.get("simplify") and tol > 0 else 'g'
    # 外层只带属性列与裁剪后的 geom，避免中间几何列 g 混进 MVT 属性
    sql = (f"SELECT ST_AsMVT(m,'{name}',4096,'geom') FROM ("
           f"SELECT ST_AsMVTGeom({simp},ST_MakeEnvelope({minx},{miny},{maxx},{maxy},3857),4096,64,true) AS geom,{out_cols} "
           f"FROM ({inner}) s WHERE g IS NOT NULL) m")
    try:
        cur = get_conn().cursor()
        cur.execute(sql)
        row = cur.fetchone()
    except Exception as e:
        return Response(content=("MVT error: " + str(e)[:200]).encode(), status_code=500)
    buf = bytes(row[0]) if row and row[0] else b""
    if not buf:
        return Response(status_code=204)
    return Response(content=buf, media_type="application/x-protobuf",
                    headers={"Cache-Control": "public, max-age=3600"})


@app.post("/api/locate")
def api_locate(body: Locate):
    """实体名 → 点位（问答联动用）：别名简繁双向校验 + 空间反查县市/乡镇，附地名归属。"""
    names = [str(n).strip() for n in body.names if n and len(str(n).strip()) >= 2][:8]
    if not names:
        return {"points": [], "note": "无实体"}
    cands = sorted({w for n in names for w in (n, s2t(n), t2s(n))})
    county = (body.county or "").strip()
    cur = get_conn().cursor()
    sql = """SELECT COALESCE(a.alt, g.name), g.name, round(ST_Y(g.geom)::numeric,4), round(ST_X(g.geom)::numeric,4),
                    cb.name, tb.name, g.fclass, g.elev_m, COALESCE(g.population,0)
             FROM tw.gaz_tw g
             LEFT JOIN tw.gaz_alias a ON a.geonameid = g.geonameid AND a.alt = ANY(%s)
             LEFT JOIN tw.boundary cb ON cb.level=2 AND ST_Within(g.geom, cb.geom)
             LEFT JOIN tw.admin_osm tb ON tb.level=8 AND ST_Within(g.geom, tb.geom)
             WHERE (g.name = ANY(%s) OR a.alt IS NOT NULL)"""
    args = [cands, cands]
    if county:
        sql += " AND cb.name = ANY(%s)"
        en = [k for k, v in EN2CN.items() if county in (v, s2t(v), t2s(v))]
        args.append(en or [county])
    sql += " ORDER BY COALESCE(g.population,0) DESC NULLS LAST LIMIT 12"
    try:
        cur.execute(sql, args)
        rows = cur.fetchall()
    except Exception as e:
        return {"points": [], "note": str(e)[:120]}
    pts = [{"alias": r[0], "name": r[1], "lat": float(r[2]), "lon": float(r[3]),
            "county_en": r[4], "county": county_cn(r[4]) if r[4] else "", "town": r[5] or "",
            "fclass": r[6], "elev_m": r[7], "population": int(r[8] or 0)} for r in rows]
    if not pts:  # 退而求其次：把实体当县市名给质心
        en = [k for k, v in EN2CN.items() if any(c in (v, s2t(v), t2s(v)) for c in cands)]
        if en:
            cur.execute("SELECT name, round(ST_Y(ST_PointOnSurface(geom))::numeric,4),"
                        " round(ST_X(ST_PointOnSurface(geom))::numeric,4), 4 FROM tw.boundary"
                        " WHERE level=2 AND name = ANY(%s)", (en,))
            pts = [{"alias": r[0], "name": county_cn(r[0]), "lat": float(r[1]), "lon": float(r[2]),
                    "county_en": r[0], "county": county_cn(r[0]), "town": "", "fclass": "county",
                    "elev_m": None, "population": 0} for r in cur.fetchall()]
    return {"points": pts}


# ───────────────────────── 析 · 地形与灾害参考（网页端工作台，2026-10-09 新增）
# 与 QGIS 插件「析」面板同源同口径：tw.dem_zonal_stats（70m SRTM 空洞修补派生层）。
# 覆盖口径：21 个县市单元（連江縣无 SRTM 抽样）+ 其下挂 parent 的乡镇单元。
# 演示纪律：只统计 parent 非空的乡镇（即挂在本县市名下的单元），县市外单元不入结果；
#   覆盖缺口如实呈现，不作推测、不作填补——「如实标注缺口」本身是本工作台的展示点。
class Hazard(BaseModel):
    unit: str = ""


@app.get("/api/hazard/units")
def hazard_units():
    """可查询的县市单元（繁中名 + 边界质心，供前端联动定位）。"""
    cur = get_conn().cursor()
    cur.execute("SELECT DISTINCT unit_name FROM tw.dem_zonal_stats"
                " WHERE unit_type='county' ORDER BY 1")
    units = [r[0] for r in cur.fetchall()]
    name2en = {s2t(v): k for k, v in EN2CN.items()}
    cent = {}
    try:
        cur.execute("SELECT name, round(ST_Y(ST_PointOnSurface(geom))::numeric,4),"
                    " round(ST_X(ST_PointOnSurface(geom))::numeric,4)"
                    " FROM tw.boundary WHERE level=2")
        byen = {r[0]: (float(r[1]), float(r[2])) for r in cur.fetchall()}
        for u in units:
            p = byen.get(name2en.get(u, ""))
            if p:
                cent[u] = {"lat": p[0], "lon": p[1]}
    except Exception:
        pass
    return {"units": [{"name": u, "lat": cent.get(u, {}).get("lat"),
                       "lon": cent.get(u, {}).get("lon")} for u in units],
            "covered_counties": len(units), "total_counties": 22,
            "note": "高程分区统计覆盖 21 个县市单元；連江縣无 SRTM 抽样，如实记档，不作填补。"}


@app.post("/api/hazard")
def api_hazard(body: Hazard):
    """选定县市 → 高程分区统计 + 暴雨情景受影响乡镇参考清单（覆盖缺口如实标注）。"""
    raw = (body.unit or "").strip()
    if not raw:
        return {"error": "未指定县市"}
    cur = get_conn().cursor()
    cur.execute("SELECT DISTINCT unit_name FROM tw.dem_zonal_stats WHERE unit_type='county'")
    counties = [r[0] for r in cur.fetchall()]
    cands = {raw[:2], s2t(raw[:2]), t2s(raw[:2])}          # 简繁双向匹配，用户输入「台北」也要命中「臺北市」
    unit = next((c for c in counties if c[:2] in cands), None)
    if not unit:
        return {"error": f"未收录「{raw}」", "covered_counties": len(counties),
                "note": "高程分区统计覆盖 21 个县市单元（連江縣无 SRTM 抽样）"}
    pfx = unit[:2]
    cur.execute("""SELECT unit_type, count(*), round(avg(slope_mean)::numeric,1),
                          round(avg(mobility_mean)::numeric,1), round(max(elev_max)::numeric,0),
                          round(max(area_km2)::numeric,1)
                   FROM tw.dem_zonal_stats
                   WHERE (unit_type='county' AND unit_name LIKE %s)
                      OR (unit_type<>'county' AND parent LIKE %s)
                   GROUP BY unit_type ORDER BY unit_type""", (pfx + "%", pfx + "%"))
    zonal = [{"level": "县级" if r[0] == "county" else "乡镇级", "units": r[1],
              "slope_mean": float(r[2]) if r[2] is not None else None,
              "mobility": float(r[3]) if r[3] is not None else None,
              "elev_max": float(r[4]) if r[4] is not None else None,
              "area_km2": float(r[5]) if r[5] is not None else None} for r in cur.fetchall()]
    cur.execute("""SELECT z.unit_name, round(z.slope_mean::numeric,1),
                          round(z.steep30_frac::numeric,3), round(z.stream_l2_frac::numeric,3),
                          round(z.mobility_mean::numeric,1)
                   FROM tw.dem_zonal_stats AS z
                   WHERE unit_type<>'county' AND parent LIKE %s
                     AND (slope_mean > 25 OR stream_l2_frac > 0.05)
                   ORDER BY slope_mean DESC LIMIT 12""", (pfx + "%",))
    scenario = [{"name": r[0], "slope_mean": float(r[1]) if r[1] is not None else None,
                 "steep30_frac": float(r[2]) if r[2] is not None else None,
                 "stream_l2_frac": float(r[3]) if r[3] is not None else None,
                 "mobility": float(r[4]) if r[4] is not None else None} for r in cur.fetchall()]
    cur.execute("SELECT count(*) FROM tw.dem_zonal_stats"
                " WHERE unit_type<>'county' AND parent LIKE %s", (pfx + "%",))
    covered = cur.fetchone()[0]
    return {"unit": unit, "zonal": zonal, "scenario": scenario, "covered_towns": covered,
            "trigger": "平均坡度 > 25° 或 二级河道占比 > 5%",
            "note": "70m SRTM（空洞修补）派生层分区统计；清单为参考口径，非结论；"
                    "未覆盖单元不作推测、不作填补。"}


# 问答结果短缓存：相同问题（归一化去空白后）10 分钟内直接命中——评委重复提问、演示重开
# 不必重等 5–25 s 本地推理；容量到 200 条整体清空（比赛场景提问量小，无需真 LRU）
_ASK_CACHE = {}
_ASK_CACHE_TTL = 600
_ASK_CACHE_MAX = 200


@app.post("/api/ask")
def ask(body: Ask):
    t0 = time.time()
    q = body.question.strip()
    ckey = re.sub(r"\s+", "", q)
    hit = _ASK_CACHE.get(ckey)
    if hit and time.time() - hit[0] < _ASK_CACHE_TTL:
        return hit[1]
    ensure_pg()
    intent = classify(q)
    ents = extract_entities(q)
    try:
        if intent == "population":
            data, sources = q_population(q, ents)
        elif intent == "event":
            data, sources = q_events(q, ents)
        elif intent == "facility":
            data, sources = q_facility(q, ents)
        elif intent == "attention":
            data, sources = q_attention(q, ents)
        elif intent == "etymology":
            data, sources = q_etymology(q, ents)
        else:
            data, sources = q_place(q, ents)
    except Exception as e:
        data, sources = [], [f"查询失败: {str(e)[:80]}"]

    ctx = json.dumps(data, ensure_ascii=False, default=str)[:3500]
    srcs = list(dict.fromkeys(sources))[:4]
    # 本次实际来源表名写进提示词：原先示例把表名写死成 tw_demo_countypop，模型会照抄该示例，
    # 于是事件/关注度/地名等问题的来源全被标成人口表（演示界面「引用来源」区可见）。
    src_tables = "、".join(s.split("（")[0].strip() for s in srcs) or "本地数据库"
    prompt = f"""你是"地志智询"——台湾人文地理数据助手。只根据下面提供的查询结果回答问题，不要编造数据。
如果查询结果为空或无法回答，明确说明。回答末尾用【来源：…】标注数据表。

查询结果（JSON）：
{ctx}

用户问题：{q}

要求：用中文简洁回答（不超过150字），数字必须来自查询结果；回答中禁止出现JSON原文、字段名或花括号；来源只能写下列本次实际用到的数据表名之一（【来源：…】），不要写其他表名：{src_tables}；若查询结果含说明性文字（如数据缺口记档、口径说明），请如实转述其中的数字与结论，不要说"没有数据"；若查询结果有多个同名条目，逐条列出各自的县市/乡镇与坐标，不要只挑一个。"""
    try:
        answer = ollama_gen("qwen3:8b", "/no_think\n" + prompt)
        answer = re.sub(r"<think>.*?</think>", "", answer, flags=re.S).strip()
        if not answer:
            answer = "（模型未生成正文——请重试或减小问题复杂度）"
    except Exception as e:
        msg = str(e)
        if "拒绝" in msg or "refused" in msg.lower():
            answer = ("生成失败：本地模型服务 Ollama 未启动（127.0.0.1:11434）。"
                      "检索与地图定位不受影响；请启动 Ollama（ollama serve）并拉取 qwen3:8b 后重试。")
        else:
            answer = f"生成失败: {msg[:100]}"
    srcs.append("qwen3:8b 本地生成（Ollama）")
    result = {"intent": intent, "entities": ents, "answer": answer,
              "sources": srcs, "elapsed": round(time.time() - t0, 1)}
    # 失败响应不入缓存：Ollama 宕机等临时故障恢复后，用户不应在 TTL 内一直吃到旧的失败答案
    if "生成失败" not in answer:
        if len(_ASK_CACHE) >= _ASK_CACHE_MAX:
            _ASK_CACHE.clear()
        _ASK_CACHE[ckey] = (time.time(), result)
    return result


@app.get("/api/health")
def health():
    return {"status": "ok"}


if __name__ == "__main__":
    ensure_pg()
    # 预建乡镇人口小表（demo 前端用）
    c = get_conn()
    c.autocommit = True
    cur = c.cursor()
    # 修正（10-07）：① 限定 g.fclass='P'——原 JOIN 无此限定，会把 fclass='A'/fcode='PCLI' 的
    #   「Taiwan」国家级条目（population=23,451,837、坐标 121.0/24.0）计入埔里鎮，令该镇人口假高 2362 万；
    #   ② 取 max 而非 sum——GeoNames 单个乡镇内可含多个市镇点，sum 会重复累加；
    #   ③ DROP 后重建——原 IF NOT EXISTS 使修正口径在旧库上不生效。
    cur.execute("DROP TABLE IF EXISTS tw_demo_townpop")
    cur.execute("""CREATE TABLE tw_demo_townpop AS
        SELECT o.name, COALESCE(max(g.population), 0)::numeric AS pop
        FROM tw.admin_osm o LEFT JOIN tw.gaz_tw g
          ON ST_Contains(o.geom, g.geom) AND g.fclass = 'P'
        WHERE o.level = 8 GROUP BY o.name""")
    cur.execute("CREATE INDEX IF NOT EXISTS tdp_name_ix ON tw_demo_townpop (name)")
    # 同步修正 tw.pop_town 的 pop 值（QGIS 浏览面板与离线导出引用的历史手工表）：
    # 与重建后的 tw_demo_townpop 对齐，避免沿用手工时期把 fclass='A' 国家级点计入的旧值。
    cur.execute("""DO $$
BEGIN
  IF to_regclass('tw.pop_town') IS NOT NULL THEN
    UPDATE tw.pop_town p SET pop = t.pop
    FROM public.tw_demo_townpop t WHERE p.town = t.name;
  END IF;
END $$""")
    print("town pop table ready")
    def _warm():  # 启动即加载模型常驻，首问不再吃 7s 加载
        try:
            ollama_gen("qwen3:8b", "回复：ok", timeout=120)
            print("model warm (qwen3:8b keep_alive=30m)")
        except Exception as e:
            print("warm skip:", str(e)[:80])
    threading.Thread(target=_warm, daemon=True).start()
    uvicorn.run(app, host="127.0.0.1", port=8600, log_level="warning")
