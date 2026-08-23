import os, re, sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from urllib.parse import urlparse
import requests
from flask import Flask, jsonify, request, send_from_directory

ROOT=os.path.dirname(os.path.abspath(__file__))
DB=os.path.join(ROOT,"data","nicheradar.db")
KEY=os.getenv("YOUTUBE_API_KEY","").strip()
app=Flask(__name__,static_folder="static",static_url_path="/static")
STOP={"de","la","el","los","las","un","una","y","o","en","para","por","con","sin","que","como","cómo","del","al","es","son","the","a","an","and","or","in","on","for","to","of","with","is","are","how","why","what","your","you","from","más","menos"}

def db():
    c=sqlite3.connect(DB); c.row_factory=sqlite3.Row; return c

def init_db():
    c=db()
    c.execute("""CREATE TABLE IF NOT EXISTS channels(
      youtube_id TEXT PRIMARY KEY,handle TEXT,title TEXT,thumbnail TEXT,
      subscribers INTEGER,views INTEGER,videos INTEGER,uploads_playlist_id TEXT,created_at TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS videos(
      youtube_id TEXT PRIMARY KEY,channel_id TEXT,title TEXT,published_at TEXT,
      duration_seconds INTEGER,views INTEGER,likes INTEGER,comments INTEGER,thumbnail TEXT,fetched_at TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS niches(id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT UNIQUE,created_at TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS niche_channels(niche_id INTEGER,channel_id TEXT,UNIQUE(niche_id,channel_id))""")
    c.commit(); c.close()

def yt(endpoint,params):
    if not KEY: raise RuntimeError("Configura YOUTUBE_API_KEY")
    p=dict(params); p["key"]=KEY
    r=requests.get("https://www.googleapis.com/youtube/v3/"+endpoint,params=p,timeout=25)
    r.raise_for_status(); return r.json()

def parse_target(v):
    v=v.strip()
    if v.startswith("@"): return ("handle",v[1:])
    if "youtube.com" not in v:
        return ("id",v) if v.startswith("UC") else ("handle",v.lstrip("@"))
    path=urlparse(v).path.strip("/")
    if path.startswith("@"): return ("handle",path[1:].split("/")[0])
    parts=path.split("/")
    if len(parts)>1 and parts[0]=="channel": return ("id",parts[1])
    return ("query",parts[-1])

def resolve(v):
    kind,val=parse_target(v)
    if kind=="id":
        d=yt("channels",{"part":"snippet,statistics,contentDetails","id":val})
    elif kind=="handle":
        d=yt("channels",{"part":"snippet,statistics,contentDetails","forHandle":val})
    else:
        s=yt("search",{"part":"snippet","type":"channel","q":val,"maxResults":1})
        if not s.get("items"): return None
        cid=s["items"][0]["snippet"]["channelId"]
        d=yt("channels",{"part":"snippet,statistics,contentDetails","id":cid})
    return d["items"][0] if d.get("items") else None

def save_channel(i):
    sn=i["snippet"]; st=i.get("statistics",{}); cd=i.get("contentDetails",{})
    th=(sn.get("thumbnails",{}).get("high") or sn.get("thumbnails",{}).get("default") or {}).get("url","")
    up=(cd.get("relatedPlaylists") or {}).get("uploads","")
    row=(i["id"],sn.get("customUrl",""),sn.get("title",""),th,int(st.get("subscriberCount",0) or 0),int(st.get("viewCount",0) or 0),int(st.get("videoCount",0) or 0),up,datetime.utcnow().isoformat())
    c=db()
    c.execute("""INSERT INTO channels VALUES(?,?,?,?,?,?,?,?,?)
    ON CONFLICT(youtube_id) DO UPDATE SET handle=excluded.handle,title=excluded.title,thumbnail=excluded.thumbnail,
    subscribers=excluded.subscribers,views=excluded.views,videos=excluded.videos,uploads_playlist_id=excluded.uploads_playlist_id""",row)
    c.commit(); c.close()
    return dict(zip(["youtube_id","handle","title","thumbnail","subscribers","views","videos","uploads_playlist_id","created_at"],row))

def ids_from_playlist(pid):
    d=yt("playlistItems",{"part":"contentDetails","playlistId":pid,"maxResults":50})
    return [x["contentDetails"]["videoId"] for x in d.get("items",[])]

def dur(s):
    m=re.fullmatch(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?",s or "")
    if not m:return 0
    h,mi,se=[int(x or 0) for x in m.groups()]; return h*3600+mi*60+se

def save_videos(cid,ids):
    if not ids:return 0
    d=yt("videos",{"part":"snippet,statistics,contentDetails","id":",".join(ids)})
    c=db(); now=datetime.utcnow().isoformat()
    for i in d.get("items",[]):
        sn=i["snippet"]; st=i.get("statistics",{}); cd=i.get("contentDetails",{})
        th=(sn.get("thumbnails",{}).get("high") or sn.get("thumbnails",{}).get("default") or {}).get("url","")
        row=(i["id"],cid,sn.get("title",""),sn.get("publishedAt",""),dur(cd.get("duration","")),int(st.get("viewCount",0) or 0),int(st.get("likeCount",0) or 0),int(st.get("commentCount",0) or 0),th,now)
        c.execute("""INSERT INTO videos VALUES(?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(youtube_id) DO UPDATE SET title=excluded.title,published_at=excluded.published_at,duration_seconds=excluded.duration_seconds,
        views=excluded.views,likes=excluded.likes,comments=excluded.comments,thumbnail=excluded.thumbnail,fetched_at=excluded.fetched_at""",row)
    c.commit(); c.close(); return len(d.get("items",[]))

def med(xs):
    xs=sorted(xs); n=len(xs)
    if not n:return 1
    return xs[n//2] if n%2 else (xs[n//2-1]+xs[n//2])/2

def analyzed(cid):
    c=db(); rows=[dict(r) for r in c.execute("SELECT * FROM videos WHERE channel_id=? ORDER BY published_at DESC LIMIT 50",(cid,))]; ch=c.execute("SELECT * FROM channels WHERE youtube_id=?",(cid,)).fetchone(); c.close()
    now=datetime.now(timezone.utc); tmp=[]
    for v in rows:
        try: p=datetime.fromisoformat(v["published_at"].replace("Z","+00:00")); age=max((now-p).total_seconds()/86400,.25)
        except: age=1
        v["age_days"]=round(age,1); v["views_per_day"]=round(v["views"]/age,1); tmp.append(v)
    baseline=med([v["views_per_day"] for v in tmp if v["views_per_day"]>0])
    for v in tmp: v["outlier_score"]=round(v["views_per_day"]/baseline,2)
    return (dict(ch) if ch else None,tmp)

def toks(t): return set(w for w in re.findall(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ0-9]+",t.lower()) if len(w)>2 and w not in STOP and not w.isdigit())
def jac(a,b): return len(a&b)/max(1,len(a|b))

def cluster(videos):
    cs=[]
    for v in sorted(videos,key=lambda x:x["outlier_score"],reverse=True):
        tv=toks(v["title"]); bi=None; bs=0
        for i,c in enumerate(cs):
            s=jac(tv,c["tokens"])
            if s>bs: bi,bs=i,s
        if bi is not None and bs>=.22: cs[bi]["videos"].append(v); cs[bi]["tokens"]|=tv
        else: cs.append({"tokens":set(tv),"videos":[v]})
    return cs

def label(c):
    w=defaultdict(float)
    for v in c["videos"]:
        for t in toks(v["title"]): w[t]+=min(v["outlier_score"],8)
    top=[x for x,_ in sorted(w.items(),key=lambda z:z[1],reverse=True)[:3]]
    return " · ".join(top).title() if top else "Tema emergente"

def opportunities(cids):
    outs=[]
    for cid in cids:
        ch,vs=analyzed(cid)
        if not ch:continue
        for v in vs:
            if v["outlier_score"]>=2:
                v=dict(v);v["channel_title"]=ch["title"];outs.append(v)
    cards=[]
    for c in cluster(outs):
        vs=c["videos"]; chans=set(v["channel_title"] for v in vs); spread=len(chans)
        ao=sum(v["outlier_score"] for v in vs)/len(vs); av=sum(v["views_per_day"] for v in vs)/len(vs)
        r30=sum(1 for v in vs if v["age_days"]<=30); r90=sum(1 for v in vs if v["age_days"]<=90)
        demand=min(100,30+ao*13+min(av,100000)/2500)
        validation=min(100,spread*24+len(vs)*7)
        fresh=min(100,r30*32+r90*8)
        comp=min(100,spread*17+max(0,len(vs)-spread)*8); white=max(0,100-comp)
        score=min(100,demand*.35+validation*.25+fresh*.20+white*.20)
        lab=label(c); core=lab.split(" · ")[0].lower()
        cards.append({"theme":lab,"opportunity_score":round(score,1),"demand_score":round(demand,1),"validation_score":round(validation,1),"freshness_score":round(fresh,1),"whitespace_score":round(white,1),"channel_spread":spread,"outlier_count":len(vs),"avg_outlier":round(ao,2),"avg_views_per_day":round(av,1),
        "evidence":[{"channel":v["channel_title"],"title":v["title"],"outlier_score":v["outlier_score"],"views_per_day":v["views_per_day"]} for v in sorted(vs,key=lambda x:x["outlier_score"],reverse=True)[:3]],
        "ideas":[f"Lo que nadie te explica sobre {core}",f"Probé 3 enfoques para {core}: cuál funciona mejor",f"7 errores que debes evitar al usar {core}"]})
    cards.sort(key=lambda x:x["opportunity_score"],reverse=True)
    ns=round(sum(x["opportunity_score"] for x in cards[:5])/max(1,min(5,len(cards))),1) if cards else 0
    return {"niche_score":ns,"cards":cards[:12]}

@app.route("/")
def home(): return send_from_directory("static","index.html")
@app.get("/api/status")
def status(): return jsonify({"phase":4,"youtube_api_configured":bool(KEY)})

@app.get("/api/channels")
def channels():
    c=db(); rows=[dict(r) for r in c.execute("SELECT * FROM channels ORDER BY created_at DESC")]; c.close(); return jsonify(rows)

@app.post("/api/channels")
def add():
    v=(request.get_json(force=True) or {}).get("url","").strip()
    if not v:return jsonify({"error":"Falta el canal"}),400
    if not KEY:return jsonify({"error":"Configura YOUTUBE_API_KEY"}),503
    try:
        i=resolve(v)
        if not i:return jsonify({"error":"Canal no encontrado"}),404
        return jsonify(save_channel(i))
    except Exception as e:return jsonify({"error":str(e)}),500

@app.post("/api/channels/<cid>/sync-videos")
def sync(cid):
    c=db(); ch=c.execute("SELECT * FROM channels WHERE youtube_id=?",(cid,)).fetchone(); c.close()
    if not ch:return jsonify({"error":"Canal no encontrado"}),404
    return jsonify({"synced":save_videos(cid,ids_from_playlist(ch["uploads_playlist_id"]))})

@app.post("/api/niches")
def niche():
    b=request.get_json(force=True) or {}; name=b.get("name","").strip(); ids=b.get("channel_ids",[])
    if not name:return jsonify({"error":"Falta nombre"}),400
    if len(ids)<2:return jsonify({"error":"Selecciona al menos 2 canales"}),400
    c=db(); c.execute("INSERT OR IGNORE INTO niches(name,created_at) VALUES(?,?)",(name,datetime.utcnow().isoformat())); n=c.execute("SELECT * FROM niches WHERE name=?",(name,)).fetchone(); c.execute("DELETE FROM niche_channels WHERE niche_id=?",(n["id"],))
    for cid in ids:c.execute("INSERT OR IGNORE INTO niche_channels VALUES(?,?)",(n["id"],cid))
    c.commit(); c.close(); return jsonify({"id":n["id"],"name":name})

@app.get("/api/niches/<int:nid>/opportunities")
def opp(nid):
    c=db(); ids=[r["channel_id"] for r in c.execute("SELECT channel_id FROM niche_channels WHERE niche_id=?",(nid,))]; c.close()
    return jsonify(opportunities(ids))

@app.delete("/api/channels/<cid>")
def delete(cid):
    c=db(); c.execute("DELETE FROM videos WHERE channel_id=?",(cid,));c.execute("DELETE FROM niche_channels WHERE channel_id=?",(cid,));c.execute("DELETE FROM channels WHERE youtube_id=?",(cid,));c.commit();c.close();return jsonify({"ok":True})

if __name__=="__main__":
    init_db(); app.run(host="0.0.0.0",port=int(os.getenv("PORT","8000")),debug=True)
