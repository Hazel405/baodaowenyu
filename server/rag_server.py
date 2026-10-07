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


CFG = _load_cfg()
PGCTL, PGDATA, OLLAMA = CFG["PGCTL"], CFG["PGDATA"], CFG["OLLAMA"]
CN_REG = re.compile(r"[\u4e00-\u9fff]{2,6}")

app = FastAPI(title="地志智询 · 台湾人文地理 RAG")
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


def ollama_gen(model, prompt, timeout=900):
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


def classify(q):
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
    # 全岛总量口径
    if not ents or any(k in q for k in ("全台", "全岛", "总量", "总计", "共")):
        cur.execute("SELECT sum(pop) FROM tw_demo_countypop")
        tot = cur.fetchone()[0]
        if tot:
            out.append({"scope": "全台", "population_worldpop": int(tot), "source_year": 2020})
            sources.append("tw_demo_countypop 合计（WorldPop 2020）")
    for e in ents:
        cur.execute("""SELECT g.name, g.fcode, g.population, cb.name, ST_Y(g.geom), ST_X(g.geom)
            FROM tw.gaz_alias a JOIN tw.gaz_tw g USING (geonameid)
            LEFT JOIN LATERAL (SELECT name FROM tw.boundary b
                               WHERE b.level=2 AND ST_Within(g.geom, b.geom) LIMIT 1) cb ON true
            WHERE a.alt = ANY(%s) AND g.population > 0
            ORDER BY g.population DESC LIMIT 4""", ([e, s2t(e)],))
        rows = cur.fetchall()
        for name, fcode, pop, adm, la, lo in rows:
            out.append({"place": e, "name": name, "fcode": fcode, "population": pop,
                        "所在县市": county_cn(adm), "lat": round(la, 3), "lon": round(lo, 3)})
            sources.append(f"tw.gaz_tw/geonames geonameid 检索（{e}）")
    # 县市级：查演示人口表（WorldPop 分区）；实体或前缀双匹配（防简繁/后缀差异）
    for e in ents:
        cur.execute("SELECT n, pop FROM tw_demo_countypop WHERE n ILIKE %s OR cn ILIKE %s OR cn ILIKE %s OR cn ILIKE %s LIMIT 2",
                    (f"%{e}%", f"%{e}%", f"%{e[:2]}%", f"%{s2t(e)[:2]}%"))
        for n, pop in cur.fetchall():
            out.append({"place": e, "county": n, "population_worldpop": pop})
            sources.append("tw_demo_countypop（GADM 边界 × WorldPop 2020 分区）")
    out = out[:6]
    if not out:
        for e in ents:
            cur.execute("SELECT name, pop FROM tw_demo_townpop WHERE name ILIKE %s OR name ILIKE %s LIMIT 3",
                        (f"%{e}%", f"%{e[:2]}%"))
            for name, pop in cur.fetchall():
                out.append({"place": e, "name": name, "township_pop": pop})
                sources.append(f"WorldPop 2020 乡镇分区统计（{name}）")
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
                        "所在乡镇": r[5], "population": r[6], "elevation": r[7],
                        "lat": round(r[8], 3), "lon": round(r[9], 3), "zh": r[10]})
        sources.append(f"tw.gaz_tw（GeoNames 45,572 条，中文别名约 3.2 万条）")
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
# 白名单机制：只有下列民用要素表可经接口对外提供，osm_restricted 等非民用要素表一律不出现。
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


@app.post("/api/ask")
def ask(body: Ask):
    t0 = time.time()
    q = body.question.strip()
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
        answer = f"生成失败: {str(e)[:100]}"
    srcs.append("qwen3:8b 本地生成（Ollama）")
    return {"intent": intent, "entities": ents, "answer": answer,
            "sources": srcs, "elapsed": round(time.time() - t0, 1)}


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
