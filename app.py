import os, re, math, sqlite3
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from urllib.parse import urlparse
import requests
from flask import Flask, jsonify, request, send_from_directory

ROOT=os.path.dirname(os.path.abspath(__file__))
DB=os.path.join(ROOT,"data","nicheradar.db")
KEY=os.getenv("YOUTUBE_API_KEY","").strip()
app=Flask(__name__,static_folder="static",static_url_path="/static")
STOP={"de","la","el","los","las","un","una","y","o","en","para","por","con","sin","que","como","cómo","del","al","es","son","the","a","an","and","or","in","on","for","to","of","with","is","are","how","why","what","your","you","from","más","menos"}

def db():
    os.makedirs(os.path.dirname(DB),exist_ok=True)
    c=sqlite3.connect(DB); c.row_factory=sqlite3.Row; return c

def init_db():
    c=db()
    c.execute("""CREATE TABLE IF NOT EXISTS channels(youtube_id TEXT PRIMARY KEY,handle TEXT,title TEXT,thumbnail TEXT,subscribers INTEGER DEFAULT 0,views INTEGER DEFAULT 0,videos INTEGER DEFAULT 0,uploads_playlist_id TEXT,published_at TEXT,category_hint TEXT,created_at TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS videos(youtube_id TEXT PRIMARY KEY,channel_id TEXT,title TEXT,published_at TEXT,duration_seconds INTEGER,views INTEGER,likes INTEGER,comments INTEGER,thumbnail TEXT,fetched_at TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS channel_snapshots(id INTEGER PRIMARY KEY AUTOINCREMENT,channel_id TEXT,captured_at TEXT,subscribers INTEGER,views INTEGER,videos INTEGER)""")
    c.execute("""CREATE TABLE IF NOT EXISTS radar_runs(id INTEGER PRIMARY KEY AUTOINCREMENT,started_at TEXT,finished_at TEXT,region TEXT,category_limit INTEGER,channels_limit INTEGER,discovery_mode TEXT,channels_scanned INTEGER DEFAULT 0,niches_found INTEGER DEFAULT 0,status TEXT DEFAULT 'running')""")
    c.execute("""CREATE TABLE IF NOT EXISTS radar_run_channels(run_id INTEGER,channel_id TEXT,position INTEGER,channel_score REAL,momentum REAL,outliers REAL,audience_efficiency REAL,freshness REAL,consistency REAL,observed_growth_per_day REAL,confidence_score REAL,confidence_label TEXT,created_at TEXT,PRIMARY KEY(run_id,channel_id))""")
    cols={r["name"] for r in c.execute("PRAGMA table_info(radar_run_channels)")}
    for n,t in [("confidence_score","REAL"),("confidence_label","TEXT")]:
        if n not in cols:c.execute(f"ALTER TABLE radar_run_channels ADD COLUMN {n} {t}")
    c.execute("CREATE INDEX IF NOT EXISTS idx_snapshots_channel_time ON channel_snapshots(channel_id,captured_at)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_radar_runs_time ON radar_runs(started_at)")
    c.commit(); c.close()

def yt(endpoint,params):
    if not KEY: raise RuntimeError("Configura YOUTUBE_API_KEY")
    p=dict(params); p["key"]=KEY
    r=requests.get("https://www.googleapis.com/youtube/v3/"+endpoint,params=p,timeout=30); r.raise_for_status(); return r.json()

def batched(values,size=50):
    for i in range(0,len(values),size): yield values[i:i+size]

def iso_dt(v):
    if not v:return None
    try:
        d=datetime.fromisoformat(v.replace("Z","+00:00")); return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except ValueError:return None

def parse_target(v):
    v=v.strip()
    if v.startswith("@"):return "handle",v[1:]
    if "youtube.com" not in v:return ("id",v) if v.startswith("UC") else ("handle",v.lstrip("@"))
    p=urlparse(v).path.strip("/")
    if p.startswith("@"):return "handle",p[1:].split("/")[0]
    parts=p.split("/")
    if len(parts)>1 and parts[0]=="channel":return "id",parts[1]
    return "query",parts[-1]

def resolve_channel(v):
    kind,target=parse_target(v)
    if kind=="id": d=yt("channels",{"part":"snippet,statistics,contentDetails","id":target})
    elif kind=="handle": d=yt("channels",{"part":"snippet,statistics,contentDetails","forHandle":target})
    else:
        s=yt("search",{"part":"snippet","type":"channel","q":target,"maxResults":1})
        if not s.get("items"):return None
        d=yt("channels",{"part":"snippet,statistics,contentDetails","id":s["items"][0]["snippet"]["channelId"]})
    return d["items"][0] if d.get("items") else None

def normalize_channel(i,hint=""):
    s=i["snippet"]; st=i.get("statistics",{}); cd=i.get("contentDetails",{}); th=s.get("thumbnails",{})
    return {"youtube_id":i["id"],"handle":s.get("customUrl",""),"title":s.get("title",""),"thumbnail":(th.get("high") or th.get("medium") or th.get("default") or {}).get("url",""),"subscribers":int(st.get("subscriberCount",0) or 0),"views":int(st.get("viewCount",0) or 0),"videos":int(st.get("videoCount",0) or 0),"uploads_playlist_id":(cd.get("relatedPlaylists") or {}).get("uploads",""),"published_at":s.get("publishedAt",""),"category_hint":hint}

def save_channel(ch):
    c=db(); c.execute("""INSERT INTO channels(youtube_id,handle,title,thumbnail,subscribers,views,videos,uploads_playlist_id,published_at,category_hint,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(youtube_id) DO UPDATE SET handle=excluded.handle,title=excluded.title,thumbnail=excluded.thumbnail,subscribers=excluded.subscribers,views=excluded.views,videos=excluded.videos,uploads_playlist_id=excluded.uploads_playlist_id,published_at=excluded.published_at,category_hint=CASE WHEN excluded.category_hint!='' THEN excluded.category_hint ELSE channels.category_hint END""",(ch["youtube_id"],ch["handle"],ch["title"],ch["thumbnail"],ch["subscribers"],ch["views"],ch["videos"],ch["uploads_playlist_id"],ch["published_at"],ch["category_hint"],datetime.now(timezone.utc).isoformat())); c.commit(); c.close()

def snapshot_channel(ch):
    c=db(); prev=c.execute("SELECT * FROM channel_snapshots WHERE channel_id=? ORDER BY captured_at DESC LIMIT 1",(ch["youtube_id"],)).fetchone(); c.execute("INSERT INTO channel_snapshots(channel_id,captured_at,subscribers,views,videos) VALUES(?,?,?,?,?)",(ch["youtube_id"],datetime.now(timezone.utc).isoformat(),ch["subscribers"],ch["views"],ch["videos"])); c.commit(); c.close(); return dict(prev) if prev else None

def snapshot_history(cid,limit=200):
    c=db(); rows=[dict(r) for r in c.execute("SELECT captured_at,subscribers,views,videos FROM channel_snapshots WHERE channel_id=? ORDER BY captured_at DESC LIMIT ?",(cid,limit))]; c.close(); return rows

def closest_snapshot_before(rows,target):
    e=[(iso_dt(r["captured_at"]),r) for r in rows if iso_dt(r["captured_at"]) and iso_dt(r["captured_at"])<=target]
    if not e:return None
    e.sort(key=lambda x:x[0],reverse=True); return e[0][1]

def window_delta(cur,base,now):
    if not base:return {"available":False}
    d=iso_dt(base["captured_at"])
    if not d:return {"available":False}
    days=max((now-d).total_seconds()/86400,.01); dv=max(0,cur["views"]-base["views"]); ds=max(0,cur["subscribers"]-base["subscribers"])
    return {"available":True,"baseline_at":base["captured_at"],"coverage_days":round(days,2),"views_delta":dv,"subs_delta":ds,"views_per_day":round(dv/days,1),"subs_per_day":round(ds/days,2),"views_growth_pct":round(dv/max(base["views"],1)*100,3),"subs_growth_pct":round(ds/max(base["subscribers"],1)*100,3)}

def classify_timeline(w,score=None):
    a=sum(1 for x in w.values() if x.get("available"))
    if not a:return {"status":"Recolectando datos","direction":"unknown","reason":"Hace falta historial."}
    v24=w["24h"].get("views_per_day") if w["24h"].get("available") else None; v7=w["7d"].get("views_per_day") if w["7d"].get("available") else None; v30=w["30d"].get("views_per_day") if w["30d"].get("available") else None
    if v24 is not None and v7 is not None and v24>max(v7*1.25,1):return {"status":"Acelerando","direction":"up","reason":"24h supera claramente 7d."}
    if v7 is not None and v30 is not None and v7>max(v30*1.2,1):return {"status":"Acelerando","direction":"up","reason":"7d supera 30d."}
    if v7 is not None and v30 is not None and v7<v30*.7:return {"status":"Desacelerando","direction":"down","reason":"La velocidad reciente cayó."}
    if score is not None and score>=80:return {"status":"Fuerte","direction":"steady","reason":"Growth Score en zona fuerte."}
    return {"status":"Emergente","direction":"up","reason":"Hay crecimiento observado."}

def opportunity_timeline(cid,score=None):
    s=snapshot_history(cid,200); empty={"24h":{"available":False},"7d":{"available":False},"30d":{"available":False}}
    if not s:return {"channel_id":cid,"windows":empty,"trend":classify_timeline(empty,score),"snapshot_count":0}
    cur=s[0]; now=iso_dt(cur["captured_at"]) or datetime.now(timezone.utc); w={}
    for label,days in (("24h",1),("7d",7),("30d",30)):w[label]=window_delta(cur,closest_snapshot_before(s[1:],now-timedelta(days=days)),now)
    return {"channel_id":cid,"current_at":cur["captured_at"],"snapshot_count":len(s),"windows":w,"trend":classify_timeline(w,score)}

def clamp(v):return round(max(0,min(100,v)),1)
def median(xs):
    xs=sorted(xs); n=len(xs)
    if not n:return 0
    return xs[n//2] if n%2 else (xs[n//2-1]+xs[n//2])/2

def parse_duration(v):
    m=re.fullmatch(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?",v or "")
    if not m:return 0
    h,mi,s=[int(x or 0) for x in m.groups()]; return h*3600+mi*60+s

def playlist_video_ids(pid,max_results=30):
    if not pid:return []
    d=yt("playlistItems",{"part":"contentDetails","playlistId":pid,"maxResults":min(max_results,50)}); return [x["contentDetails"]["videoId"] for x in d.get("items",[])]

def save_video_details(cid,ids):
    if not ids:return 0
    c=db(); now=datetime.now(timezone.utc).isoformat(); count=0
    for batch in batched(ids):
        d=yt("videos",{"part":"snippet,statistics,contentDetails","id":",".join(batch)})
        for i in d.get("items",[]):
            s=i["snippet"]; st=i.get("statistics",{}); cd=i.get("contentDetails",{}); th=s.get("thumbnails",{}); thumb=(th.get("high") or th.get("medium") or th.get("default") or {}).get("url","")
            c.execute("""INSERT INTO videos(youtube_id,channel_id,title,published_at,duration_seconds,views,likes,comments,thumbnail,fetched_at) VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(youtube_id) DO UPDATE SET title=excluded.title,published_at=excluded.published_at,duration_seconds=excluded.duration_seconds,views=excluded.views,likes=excluded.likes,comments=excluded.comments,thumbnail=excluded.thumbnail,fetched_at=excluded.fetched_at""",(i["id"],cid,s.get("title",""),s.get("publishedAt",""),parse_duration(cd.get("duration","")),int(st.get("viewCount",0) or 0),int(st.get("likeCount",0) or 0),int(st.get("commentCount",0) or 0),thumb,now)); count+=1
    c.commit(); c.close(); return count

def video_analysis(cid):
    c=db(); rows=[dict(r) for r in c.execute("SELECT * FROM videos WHERE channel_id=? ORDER BY published_at DESC LIMIT 30",(cid,))]; c.close(); now=datetime.now(timezone.utc)
    for v in rows:
        p=iso_dt(v["published_at"]); age=max((now-p).total_seconds()/86400,.25) if p else 1; v["age_days"]=round(age,1); v["views_per_day"]=round(v["views"]/age,1)
    base=median([v["views_per_day"] for v in rows if v["views_per_day"]>0]) or 1
    for v in rows:v["outlier_score"]=round(v["views_per_day"]/max(base,1),2)
    return rows,base

def months_old(v):
    p=iso_dt(v)
    return 999 if not p else max(.1,(datetime.now(timezone.utc)-p).days/30.44)

def observed_growth(ch,prev):
    if not prev:return None
    p=iso_dt(prev["captured_at"])
    if not p:return None
    days=max((datetime.now(timezone.utc)-p).total_seconds()/86400,.01)
    if days<.02:return None
    return round(max(0,ch["views"]-prev["views"])/days,1)

def confidence_label(score):
    return "Alta" if score>=75 else "Media" if score>=50 else "Baja"

def channel_confidence(video_count,strong_count,timeline):
    snapshot_count=timeline.get("snapshot_count",0); available=sum(1 for w in timeline.get("windows",{}).values() if w.get("available")); coverage=min(1,snapshot_count/8); history=available/3; sample=min(1,video_count/20); repeat=min(1,strong_count/4)
    score=clamp(sample*30+repeat*30+coverage*20+history*20)
    return {"score":score,"label":confidence_label(score),"factors":{"video_sample":round(sample*100,1),"outlier_repeatability":round(repeat*100,1),"snapshot_depth":round(coverage*100,1),"timeline_coverage":round(history*100,1)}}

def growth_score_v2(ch,prev=None):
    videos,base=video_analysis(ch["youtube_id"]); recent=[v for v in videos if v["age_days"]<=30]; older=[v for v in videos if 30<v["age_days"]<=90]; strong=[v for v in videos if v["outlier_score"]>=2]; signal=[v for v in recent if v["outlier_score"]>=1.2]
    rv=median([v["views_per_day"] for v in recent]) or base; ov=median([v["views_per_day"] for v in older]); ratio=rv/max(ov,1) if ov else 1; proxy=clamp(35+math.log10(max(rv,1))*12+max(0,math.log2(max(ratio,.25)))*18); gpd=observed_growth(ch,prev); momentum=clamp(proxy*.55+clamp(math.log10(max(gpd,1))*24)*.45) if gpd is not None else proxy
    density=len(strong)/max(len(videos),1); avg=sum(min(v["outlier_score"],8) for v in strong)/len(strong) if strong else 0; outliers=clamp(density*180+avg*10); recent_views=sum(v["views"] for v in recent); eff=clamp(math.log10(1+recent_views/max(ch["subscribers"],1))*55)
    newest=min((v["age_days"] for v in strong),default=999); sf=100 if newest<=7 else 85 if newest<=30 else 65 if newest<=60 else 40; age=months_old(ch.get("published_at","")); cf=100 if age<=6 else 85 if age<=12 else 65 if age<=24 else 40 if age<=48 else 20; freshness=clamp(sf*.7+cf*.3); consistency=clamp(len(signal)/max(len(recent),1)*55+min(1,len(strong)/3)*30+min(1,len(recent)/8)*15); score=clamp(momentum*.30+outliers*.25+eff*.20+freshness*.15+consistency*.10)
    top=sorted(videos,key=lambda x:(x["outlier_score"],x["views_per_day"]),reverse=True)[:5]; timeline=opportunity_timeline(ch["youtube_id"],score); confidence=channel_confidence(len(videos),len(strong),timeline)
    return {"youtube_id":ch["youtube_id"],"title":ch["title"],"handle":ch.get("handle",""),"thumbnail":ch.get("thumbnail",""),"subscribers":ch["subscribers"],"views":ch["views"],"video_count":ch["videos"],"age_months":round(age,1),"category_hint":ch.get("category_hint",""),"median_views_per_day":round(base,1),"uploads_30d":len(recent),"outliers_2x":len(strong),"observed_views_growth_per_day":gpd,"channel_score":score,"score_version":2,"components":{"momentum":momentum,"outliers":outliers,"audience_efficiency":eff,"freshness":freshness,"consistency":consistency},"top_videos":top,"timeline":timeline,"confidence":confidence}

def tokens(text):return set(w for w in re.findall(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ0-9]+",text.lower()) if len(w)>2 and w not in STOP and not w.isdigit())
def jaccard(a,b):return len(a&b)/max(1,len(a|b))

def niche_confidence(channel_count,video_count,avg_outlier,fresh_count):
    independent=min(1,channel_count/5); sample=min(1,video_count/12); strength=min(1,max(avg_outlier-1,0)/3); freshness=min(1,fresh_count/5); score=clamp(independent*40+sample*25+strength*20+freshness*15)
    return {"score":score,"label":confidence_label(score),"factors":{"independent_channels":round(independent*100,1),"signal_sample":round(sample*100,1),"signal_strength":round(strength*100,1),"freshness":round(freshness*100,1)}}

def cluster_signal_videos(channels):
    sig=[]
    for ch in channels:
        for v in ch["top_videos"]:
            if v["outlier_score"]>=1.5:
                x=dict(v); x["channel_title"]=ch["title"]; x["channel_id"]=ch["youtube_id"]; x["channel_score"]=ch["channel_score"]; sig.append(x)
    clusters=[]
    for v in sorted(sig,key=lambda x:(x["outlier_score"],x["views_per_day"]),reverse=True):
        t=tokens(v["title"]); bi=None; bs=0
        for i,c in enumerate(clusters):
            s=jaccard(t,c["tokens"])
            if s>bs:bi,bs=i,s
        if bi is not None and bs>=.20:clusters[bi]["videos"].append(v); clusters[bi]["tokens"].update(t)
        else:clusters.append({"tokens":set(t),"videos":[v]})
    out=[]
    for c in clusters:
        vs=c["videos"]
        if not vs:continue
        ids={v["channel_id"] for v in vs}; weighted=defaultdict(float)
        for v in vs:
            for t in tokens(v["title"]):weighted[t]+=min(v["outlier_score"],8)
        terms=[t for t,_ in sorted(weighted.items(),key=lambda x:x[1],reverse=True)[:3]]; label=" · ".join(terms).title() if terms else "Tema emergente"; ao=sum(v["outlier_score"] for v in vs)/len(vs); av=sum(v["views_per_day"] for v in vs)/len(vs); ac=sum(v["channel_score"] for v in vs)/len(vs); fresh=sum(1 for v in vs if v["age_days"]<=30); validation=clamp(len(ids)*24+len(vs)*6); demand=clamp(ao*15+math.log10(max(av,1))*18); fr=clamp(fresh*22); opp=clamp(demand*.35+validation*.30+fr*.20+ac*.15); conf=niche_confidence(len(ids),len(vs),ao,fresh)
        out.append({"name":label,"opportunity_score":opp,"confidence":conf,"channel_count":len(ids),"signal_video_count":len(vs),"avg_outlier":round(ao,2),"avg_views_per_day":round(av,1),"evidence":[{"channel":v["channel_title"],"title":v["title"],"outlier_score":v["outlier_score"],"views_per_day":v["views_per_day"]} for v in sorted(vs,key=lambda x:x["outlier_score"],reverse=True)[:5]]})
    out.sort(key=lambda x:(x["opportunity_score"],x["confidence"]["score"]),reverse=True); return out[:12]

def begin_radar_run(region,category_limit,channels_limit,mode):
    c=db(); cur=c.execute("INSERT INTO radar_runs(started_at,region,category_limit,channels_limit,discovery_mode,status) VALUES(?,?,?,?,?,'running')",(datetime.now(timezone.utc).isoformat(),region,category_limit,channels_limit,mode)); rid=cur.lastrowid; c.commit(); c.close(); return rid

def finish_radar_run(rid,scored,niches):
    c=db(); c.execute("UPDATE radar_runs SET finished_at=?,channels_scanned=?,niches_found=?,status='completed' WHERE id=?",(datetime.now(timezone.utc).isoformat(),len(scored),len(niches),rid))
    for pos,ch in enumerate(scored,1):
        cp=ch["components"]; cf=ch["confidence"]; c.execute("""INSERT OR REPLACE INTO radar_run_channels(run_id,channel_id,position,channel_score,momentum,outliers,audience_efficiency,freshness,consistency,observed_growth_per_day,confidence_score,confidence_label,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",(rid,ch["youtube_id"],pos,ch["channel_score"],cp["momentum"],cp["outliers"],cp["audience_efficiency"],cp["freshness"],cp["consistency"],ch["observed_views_growth_per_day"],cf["score"],cf["label"],datetime.now(timezone.utc).isoformat()))
    c.commit(); c.close()
def fail_radar_run(rid):
    c=db(); c.execute("UPDATE radar_runs SET finished_at=?,status='failed' WHERE id=?",(datetime.now(timezone.utc).isoformat(),rid)); c.commit(); c.close()

def discover_channel_hints(region="US",category_limit=8,channels_limit=20,discovery_mode="balanced"):
    d=yt("videoCategories",{"part":"snippet","regionCode":region}); cats=[(i["id"],i["snippet"]["title"]) for i in d.get("items",[]) if i.get("snippet",{}).get("assignable",False)][:category_limit]; hints={}; per=max(4,math.ceil(channels_limit/max(len(cats),1))); after=(datetime.now(timezone.utc)-timedelta(days=30)).isoformat().replace("+00:00","Z")
    for cid,name in cats:
        pop=yt("videos",{"part":"snippet","chart":"mostPopular","regionCode":region,"videoCategoryId":cid,"maxResults":min(8,per+2)})
        for i in pop.get("items",[]):
            x=i.get("snippet",{}).get("channelId")
            if x:hints.setdefault(x,name)
        if discovery_mode in {"balanced","deep"}:
            rec=yt("search",{"part":"snippet","type":"video","order":"viewCount","regionCode":region,"videoCategoryId":cid,"publishedAfter":after,"maxResults":min(8,per+2)})
            for i in rec.get("items",[]):
                x=i.get("snippet",{}).get("channelId")
                if x:hints.setdefault(x,name)
        if len(hints)>=channels_limit*2:break
    return dict(list(hints.items())[:channels_limit*2])

def discover_candidates(region="US",category_limit=8,channels_limit=20,discovery_mode="balanced"):
    rid=begin_radar_run(region,category_limit,channels_limit,discovery_mode)
    try:
        hints=discover_channel_hints(region,category_limit,channels_limit,discovery_mode); items=[]
        for batch in batched(list(hints.keys())):items.extend(yt("channels",{"part":"snippet,statistics,contentDetails","id":",".join(batch)}).get("items",[]))
        candidates=[normalize_channel(i,hints.get(i["id"],"")) for i in items]; candidates.sort(key=lambda c:(c["subscribers"]>500000,c["subscribers"])); candidates=candidates[:channels_limit]; scored=[]
        for ch in candidates:
            prev=snapshot_channel(ch); save_channel(ch); save_video_details(ch["youtube_id"],playlist_video_ids(ch["uploads_playlist_id"],30)); scored.append(growth_score_v2(ch,prev))
        scored.sort(key=lambda x:(x["channel_score"],x["confidence"]["score"]),reverse=True); niches=cluster_signal_videos(scored); finish_radar_run(rid,scored,niches)
        return {"run_id":rid,"region":region,"discovery_mode":discovery_mode,"channels_scanned":len(scored),"channels":scored,"niches":niches,"note":"Growth Score v2 + Timeline + Confidence Score. Confidence mide la solidez de la evidencia, no el potencial."}
    except Exception:fail_radar_run(rid); raise

@app.route("/")
def home():return send_from_directory("static","index.html")
@app.get("/api/status")
def status():return jsonify({"mvp":"global-discovery","score_version":2,"timeline_version":1,"confidence_version":1,"youtube_api_configured":bool(KEY)})
@app.post("/api/discovery/run")
def run_discovery():
    b=request.get_json(silent=True) or {}; region=str(b.get("region","US")).upper()[:2]; cl=max(1,min(int(b.get("category_limit",8)),15)); lim=max(5,min(int(b.get("channels_limit",20)),30)); mode=str(b.get("discovery_mode","balanced")); mode=mode if mode in {"light","balanced","deep"} else "balanced"
    if not KEY:return jsonify({"error":"Configura YOUTUBE_API_KEY para ejecutar el radar global."}),503
    try:return jsonify(discover_candidates(region,cl,lim,mode))
    except requests.HTTPError as e:return jsonify({"error":f"YouTube API respondió con error {e.response.status_code if e.response is not None else 502}."}),502
    except Exception as e:return jsonify({"error":str(e)}),500
@app.get("/api/discovery/history")
def history():
    limit=max(1,min(int(request.args.get("limit",20)),100)); c=db(); rows=[dict(r) for r in c.execute("SELECT * FROM radar_runs ORDER BY started_at DESC LIMIT ?",(limit,))]; c.close(); return jsonify(rows)
@app.get("/api/discovery/history/<int:rid>")
def history_detail(rid):
    c=db(); run=c.execute("SELECT * FROM radar_runs WHERE id=?",(rid,)).fetchone()
    if not run:c.close(); return jsonify({"error":"Ejecución no encontrada"}),404
    rows=[dict(r) for r in c.execute("SELECT r.*,c.title,c.handle,c.thumbnail,c.subscribers FROM radar_run_channels r LEFT JOIN channels c ON c.youtube_id=r.channel_id WHERE r.run_id=? ORDER BY r.position",(rid,))]; c.close(); return jsonify({"run":dict(run),"channels":rows})
@app.get("/api/channels/<cid>/history")
def channel_history(cid):return jsonify(snapshot_history(cid,100))
@app.get("/api/channels/<cid>/timeline")
def channel_timeline(cid):return jsonify(opportunity_timeline(cid))
@app.get("/api/channels")
def channels():
    c=db(); rows=[dict(r) for r in c.execute("SELECT * FROM channels ORDER BY created_at DESC")]; c.close(); return jsonify(rows)
@app.post("/api/channels")
def add_channel():
    b=request.get_json(force=True) or {}; v=str(b.get("url","")).strip()
    if not v:return jsonify({"error":"Falta el canal"}),400
    if not KEY:return jsonify({"error":"Configura YOUTUBE_API_KEY"}),503
    try:
        i=resolve_channel(v)
        if not i:return jsonify({"error":"Canal no encontrado"}),404
        ch=normalize_channel(i); prev=snapshot_channel(ch); save_channel(ch); save_video_details(ch["youtube_id"],playlist_video_ids(ch["uploads_playlist_id"],30)); return jsonify(growth_score_v2(ch,prev))
    except Exception as e:return jsonify({"error":str(e)}),500

init_db()
if __name__=="__main__":app.run(host="0.0.0.0",port=int(os.getenv("PORT","8000")),debug=True)
