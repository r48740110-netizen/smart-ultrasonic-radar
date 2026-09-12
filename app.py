from flask import Flask, request, redirect, url_for, session, jsonify, render_template_string, send_file
import sqlite3, os, csv, io, json, math, random, time
from functools import wraps
from werkzeug.security import generate_password_hash, check_password_hash

try:
    import psycopg
    from psycopg.rows import dict_row
except ImportError:
    psycopg = None
    dict_row = None

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_FILE = os.path.join(BASE_DIR, 'radar.db')
DATABASE_URL = os.environ.get('DATABASE_URL', '').strip()
USE_POSTGRES = DATABASE_URL.startswith(('postgres://', 'postgresql://'))

app = Flask(__name__)
app.secret_key = os.environ.get('RADAR_SECRET_KEY', 'dev-change-this-secret')
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_SECURE'] = os.environ.get('COOKIE_SECURE', '0') == '1'

def db():
    if USE_POSTGRES:
        if psycopg is None:
            raise RuntimeError('psycopg is required when DATABASE_URL is set')
        return psycopg.connect(DATABASE_URL, row_factory=dict_row)
    c = sqlite3.connect(DB_FILE)
    c.row_factory = sqlite3.Row
    return c

def execute(c, sql, params=()):
    if USE_POSTGRES:
        sql = sql.replace('?', '%s')
    return c.execute(sql, params)

def init_db():
    c = db()
    if USE_POSTGRES:
        c.execute('CREATE TABLE IF NOT EXISTS users (id BIGSERIAL PRIMARY KEY, username TEXT UNIQUE NOT NULL, password TEXT NOT NULL)')
        c.execute('CREATE TABLE IF NOT EXISTS readings (id BIGSERIAL PRIMARY KEY, user_id BIGINT NOT NULL, ts DOUBLE PRECISION NOT NULL, angle DOUBLE PRECISION NOT NULL, distance DOUBLE PRECISION NOT NULL, source TEXT NOT NULL)')
    else:
        c.execute('CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE NOT NULL, password TEXT NOT NULL)')
        c.execute('CREATE TABLE IF NOT EXISTS readings (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, ts REAL NOT NULL, angle REAL NOT NULL, distance REAL NOT NULL, source TEXT NOT NULL)')
    c.commit(); c.close()

init_db()

def current_user():
    uid = session.get('user_id')
    if not uid: return None
    c=db(); u=execute(c, 'SELECT * FROM users WHERE id=?', (uid,)).fetchone(); c.close(); return u

def login_required(fn):
    @wraps(fn)
    def w(*a, **kw):
        if not current_user(): return redirect(url_for('login'))
        return fn(*a, **kw)
    return w

def add_reading(angle, distance, source='demo'):
    u=current_user()
    if not u: return
    c=db(); execute(c, 'INSERT INTO readings(user_id,ts,angle,distance,source) VALUES(?,?,?,?,?)', (u['id'],time.time(),angle,distance,source)); c.commit(); c.close()

def get_readings(limit=5000):
    u=current_user()
    if not u: return []
    c=db(); rows=execute(c, 'SELECT ts,angle,distance,source FROM readings WHERE user_id=? ORDER BY id DESC LIMIT ?', (u['id'],limit)).fetchall(); c.close()
    return [dict(r) for r in reversed(rows)]

LOGIN='''<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"><title>Smart Ultrasonic Radar</title><style>body{margin:0;background:#05080b;color:#e9f2f5;font-family:Segoe UI,Arial;display:grid;place-items:center;min-height:100vh}.box{width:min(410px,90vw);padding:32px;background:#0b1117;border:1px solid #1c2b34;border-radius:16px;box-shadow:0 18px 60px #0008}h1{color:#39ff88;font-size:21px;letter-spacing:2px;margin:0 0 7px;text-align:center}p{text-align:center;color:#7f919b;font-size:12px;margin:0 0 25px}label{display:block;font-size:12px;color:#aebbc3;margin:13px 0 6px}input{width:100%;box-sizing:border-box;padding:12px;background:#070c10;color:white;border:1px solid #263640;border-radius:8px}button{width:100%;margin-top:18px;padding:12px;border-radius:8px;border:1px solid #1caa68;background:#0b1d15;color:#39ff88;font-weight:700;cursor:pointer}.switch{text-align:center;margin-top:16px;font-size:12px}.switch a{color:#39ff88}.err{color:#ff6c7b;background:#250f14;border:1px solid #6b2934;padding:9px;border-radius:7px;margin-top:12px;font-size:12px;text-align:center}.hint{text-align:left!important;margin:12px 0 0!important;color:#71828b!important;font-size:11px!important}</style></head><body><div class="box"><h1>SMART ULTRASONIC RADAR</h1><p>{{ 'CREATE STUDENT ACCOUNT' if mode=='signup' else 'STUDENT LOGIN' }}</p><form method="post">{% if mode=='signup' %}<label>Username</label><input name="username" required minlength="3" autocomplete="username" placeholder="Create username"><label>Password</label><input type="password" name="password" required minlength="4" autocomplete="new-password" placeholder="Create password"><label>Confirm Password</label><input type="password" name="confirm_password" required minlength="4" autocomplete="new-password" placeholder="Re-enter password"><button>CREATE ACCOUNT</button>{% else %}<label>Username</label><input name="username" required autofocus autocomplete="username" placeholder="Enter username"><label>Password</label><input type="password" name="password" required autocomplete="current-password" placeholder="Enter password"><button>LOGIN</button>{% endif %}{% if error %}<div class="err">{{error}}</div>{% endif %}</form>{% if mode=='signup' %}<p class="hint">Create your own account. Your project readings will stay linked to your account.</p><div class="switch">Already have an account? <a href="{{url_for('login')}}">Login</a></div>{% else %}<div class="switch">New student? <a href="{{url_for('signup')}}">Create account</a></div>{% endif %}</div></body></html>'''

@app.route('/login',methods=['GET','POST'])
def login():
    if request.method=='POST':
        username=request.form.get('username','').strip(); password=request.form.get('password','')
        c=db(); u=c.execute('SELECT * FROM users WHERE username=?',(username,)).fetchone(); c.close()
        if u:
            stored=u['password']; valid=False
            try: valid=check_password_hash(stored,password)
            except Exception: valid=(stored==password)
            if valid:
                # Upgrade old plaintext accounts on first successful login.
                if not stored.startswith(('scrypt:','pbkdf2:')):
                    c=db(); c.execute('UPDATE users SET password=? WHERE id=?',(generate_password_hash(password),u['id'])); c.commit(); c.close()
                session.clear(); session['user_id']=u['id']; return redirect(url_for('home'))
        return render_template_string(LOGIN,error='Invalid username or password',mode='login')
    return render_template_string(LOGIN,error=None,mode='login')

@app.route('/signup',methods=['GET','POST'])
def signup():
    if request.method=='POST':
        username=request.form.get('username','').strip()
        password=request.form.get('password','')
        confirm=request.form.get('confirm_password','')
        if len(username)<3:
            return render_template_string(LOGIN,error='Username must be at least 3 characters',mode='signup')
        if len(password)<4:
            return render_template_string(LOGIN,error='Password must be at least 4 characters',mode='signup')
        if password != confirm:
            return render_template_string(LOGIN,error='Passwords do not match',mode='signup')
        try:
            password_hash=generate_password_hash(password)
            c=db()
            if USE_POSTGRES:
                cur=execute(c, 'INSERT INTO users(username,password) VALUES(?,?) RETURNING id', (username,password_hash))
                uid=cur.fetchone()['id']
            else:
                cur=execute(c, 'INSERT INTO users(username,password) VALUES(?,?)', (username,password_hash))
                uid=cur.lastrowid
            c.commit(); c.close()
            session.clear(); session['user_id']=uid
            return redirect(url_for('home'))
        except Exception as e:
            try: c.close()
            except Exception: pass
            if USE_POSTGRES and psycopg is not None and isinstance(e, psycopg.errors.UniqueViolation):
                return render_template_string(LOGIN,error='Username already exists. Choose another.',mode='signup')
            if isinstance(e, sqlite3.IntegrityError):
                return render_template_string(LOGIN,error='Username already exists. Choose another.',mode='signup')
            raise
    return render_template_string(LOGIN,error=None,mode='signup')

@app.route('/logout')
def logout(): session.clear(); return redirect(url_for('login'))

HTML=r'''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Smart Ultrasonic Radar</title><style>
:root{--bg:#040709;--panel:#0a1015;--line:#1b2932;--text:#e8f1f4;--muted:#7e919c;--green:#39ff88;--yellow:#ffc857;--red:#ff4057;--cyan:#25d9ff}*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 65% 15%,#102019,#05080b 42%,#030506);color:var(--text);font-family:Segoe UI,Arial,sans-serif}.app{min-height:100vh}.sidebar{position:fixed;left:0;top:0;bottom:0;width:225px;background:#060b0f;border-right:1px solid var(--line);padding:22px 14px;z-index:20}.brand{padding:5px 10px 22px;border-bottom:1px solid var(--line);margin-bottom:18px}.brand h1{font-size:16px;letter-spacing:2px;line-height:1.25;color:var(--green);margin:0}.brand p{font-size:10px;letter-spacing:1.3px;color:var(--muted);margin:8px 0 0}.nav{display:grid;gap:7px}.nav button{appearance:none;width:100%;border:1px solid transparent;background:transparent;color:#9aaab4;padding:12px 13px;border-radius:9px;text-align:left;font-size:14px;font-weight:650;cursor:pointer}.nav button:hover,.nav button.active{background:#101a20;border-color:#23333d;color:#fff}.nav button.active{box-shadow:inset 3px 0 var(--green);color:var(--green)}.main{margin-left:225px;min-height:100vh}.top{height:72px;position:sticky;top:0;z-index:10;display:flex;align-items:center;justify-content:space-between;padding:0 28px;background:rgba(4,8,11,.94);border-bottom:1px solid var(--line)}.title{font-size:24px;font-weight:800;letter-spacing:2px}.online{font-size:13px;font-weight:700}.dot{display:inline-block;width:10px;height:10px;border-radius:50%;background:var(--green);box-shadow:0 0 14px var(--green);margin-right:8px}.content{padding:25px;max-width:1500px;margin:auto}.page{display:none}.page.active{display:block}.hero{display:flex;justify-content:space-between;align-items:end;margin-bottom:18px}.hero h2{font-size:30px;margin:0 0 6px}.hero p{color:var(--muted);margin:0}.grid{display:grid;grid-template-columns:minmax(0,1fr) 360px;gap:18px}.card{background:rgba(9,16,21,.93);border:1px solid var(--line);border-radius:15px;padding:16px;box-shadow:0 18px 45px rgba(0,0,0,.18)}.head{display:flex;justify-content:space-between;color:var(--muted);font-size:12px;letter-spacing:1.4px;margin-bottom:12px}.state{color:var(--green);font-weight:700}.radarwrap{height:510px;background:#020607;border:1px solid #17242b;border-radius:10px;overflow:hidden}.radarwrap canvas{display:block;width:100%;height:100%}.legend{display:flex;gap:18px;flex-wrap:wrap;margin-top:10px;font-size:11px;color:var(--muted)}.targetmain{height:190px;border:1px solid var(--line);border-radius:12px;display:grid;place-items:center;align-content:center;gap:12px}.distance{font-size:43px;font-weight:800}.threat{padding:8px 18px;border-radius:30px;border:1px solid}.low{color:var(--green);border-color:#1e744b;background:#081b12}.medium{color:var(--yellow);border-color:#80611e;background:#1c1607}.high{color:var(--red);border-color:#81303c;background:#210b10}.metrics{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:12px}.metric{padding:14px;border:1px solid var(--line);border-radius:10px}.metric span{display:block;color:#78909d;font-size:11px;text-transform:uppercase;letter-spacing:1px}.metric strong{display:block;font-size:19px;margin-top:6px}.actions{display:flex;gap:10px;margin-top:12px}.btn{padding:12px 16px;border-radius:9px;border:1px solid #29404b;background:#0b1318;color:#dfe9ed;font-weight:700;cursor:pointer}.btn.primary{border-color:#1ca965;background:#081b12;color:var(--green)}.btn.danger{border-color:#74303b;background:#19090d;color:#ff7080}.stats{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-top:18px}.stat{padding:18px;border:1px solid var(--line);border-radius:12px;background:#080e13}.stat span{font-size:11px;color:#718793;text-transform:uppercase;letter-spacing:1px}.stat strong{font-size:25px;display:block;margin-top:7px}.connect{display:grid;grid-template-columns:1fr 1fr;gap:18px}.option{padding:24px;border:1px solid var(--line);border-radius:13px;background:#080f14}.option h3{margin:0 0 8px}.option p{color:var(--muted);line-height:1.6;font-size:13px}.status{margin-top:15px;padding:12px;border:1px solid var(--line);border-radius:9px;color:var(--muted)}.tables{width:100%;border-collapse:collapse}.tables th,.tables td{padding:11px;border-bottom:1px solid var(--line);text-align:left;font-size:12px}.tables th{color:#7e929e}.settings{display:grid;grid-template-columns:1fr 1fr;gap:18px}.row{display:flex;justify-content:space-between;align-items:center;padding:15px 0;border-bottom:1px solid var(--line)}.row:last-child{border:0}.row input{width:100px;background:#070d12;color:white;border:1px solid #293b45;border-radius:7px;padding:9px}.msg{padding:12px;border-radius:9px;margin-bottom:15px;background:#081b12;border:1px solid #1d7048;color:var(--green)}@media(max-width:900px){.sidebar{width:78px;padding:15px 8px}.brand h1{font-size:10px}.brand p{display:none}.nav button{font-size:0;text-align:center}.nav button:first-letter{font-size:20px}.main{margin-left:78px}.top{padding:0 15px}.title{font-size:17px}.content{padding:15px}.grid,.connect,.settings{grid-template-columns:1fr}.radarwrap{height:420px}.stats{grid-template-columns:1fr 1fr}}@media(max-width:560px){.hero h2{font-size:23px}.stats{grid-template-columns:1fr}.radarwrap{height:340px}}
</style></head><body><div class="app"><aside class="sidebar"><div class="brand"><h1>SMART ULTRASONIC RADAR</h1><p>MONITORING SYSTEM</p></div><nav class="nav"><button data-page="home">🏠 HOME</button><button data-page="connect">🔌 CONNECT</button><button data-page="radar">📡 RADAR</button><button data-page="safety">🛡 SAFETY</button><button data-page="alerts">🔔 ALERTS</button><button data-page="analytics">📊 ANALYTICS</button><button data-page="settings">⚙ SETTINGS</button></nav></aside><main class="main"><header class="top"><div class="title">SMART ULTRASONIC RADAR</div><div class="online"><span class="dot"></span><span id="sys">SYSTEM ONLINE</span> · <a href="/logout" style="color:#8fa3ad">Logout</a></div></header><div class="content">
<section id="home" class="page"><div class="hero"><div><h2>Monitoring Overview</h2><p>Real-time ultrasonic object monitoring and risk status.</p></div></div><div class="grid"><div class="card"><div class="head"><span>LIVE RADAR</span><span class="state" id="radarState">● STANDBY</span></div><div class="radarwrap"><canvas id="radar"></canvas></div><div class="legend"><span style="color:#39ff88">● SAFE</span><span style="color:#ffc857">● WARNING</span><span style="color:#ff4057">● DANGER</span><span>● TARGET TRACKING</span></div></div><div class="card"><div class="head">CURRENT TARGET</div><div class="targetmain"><div class="distance" id="distance">-- <small style="font-size:16px;color:#8da0aa">cm</small></div><div id="threat" class="threat low">NO ACTIVE SCAN</div></div><div class="metrics"><div class="metric"><span>Angle</span><strong id="angle">--°</strong></div><div class="metric"><span>Object</span><strong id="object">CLEAR</strong></div><div class="metric"><span>Status</span><strong id="status">READY</strong></div><div class="metric"><span>Range</span><strong id="range">400 cm</strong></div></div><div class="actions"><button class="btn primary" id="start">▶ Start Radar</button><button class="btn danger" id="stop">■ Stop</button></div></div></div><div class="stats"><div class="stat"><span>Total Readings</span><strong id="total">0</strong></div><div class="stat"><span>Detected</span><strong id="detected">0</strong></div><div class="stat"><span>Detection Rate</span><strong id="rate">0%</strong></div><div class="stat"><span>Average Distance</span><strong id="avg">0 cm</strong></div></div></section>
<section id="connect" class="page"><div class="hero"><div><h2>Connect Your Project</h2><p>Connect your Arduino or use Demo Mode — no technical setup is needed after your board is ready.</p></div></div><div class="card" style="margin-bottom:18px"><h3 style="margin-top:0">🎓 Quick start for students</h3><div style="color:#9aaeb8;line-height:1.8;font-size:13px"><b>1.</b> Connect Arduino by USB &nbsp; <b>2.</b> Upload the Arduino example &nbsp; <b>3.</b> Click Connect Arduino &nbsp; <b>4.</b> Select the serial port &nbsp; <b>5.</b> Start monitoring<br><span style="color:#718793">Supported data: <b>angle,distance</b> (example: <b>75,42</b>) at <b>115200 baud</b>. For a presentation without hardware, use Demo Mode.</span></div></div><div class="connect"><div class="option"><h3>🧪 Demo Mode</h3><p>Use simulated ultrasonic readings when your Arduino is not available. This is the easiest mode for project demonstration.</p><button class="btn primary" id="demo">Start Demo Mode</button><div class="status" id="demoStatus">Demo is stopped.</div></div><div class="option"><h3>🔌 Connect Arduino</h3><p>Connect a compatible Arduino through your browser. Chrome or Edge is required. Your board should send <b>angle,distance</b>, for example <b>75,42</b>.</p><button class="btn primary" id="usb">Connect Arduino</button><div class="status" id="usbStatus">Arduino not connected.</div></div></div></section>
<section id="radar" class="page"><div class="hero"><div><h2>Live Radar</h2><p>Detailed live scan view.</p></div></div><div class="card"><div class="radarwrap" style="height:620px"><canvas id="radar2"></canvas></div></div></section>
<section id="safety" class="page"><div class="hero"><div><h2>Safety Zone</h2><p>Distance-based risk classification.</p></div></div><div class="stats"><div class="stat"><span>Danger Zone</span><strong id="dangerVal">&lt; 60 cm</strong></div><div class="stat"><span>Warning Zone</span><strong id="warningVal">60–120 cm</strong></div><div class="stat"><span>Safe Zone</span><strong id="safeVal">&gt;= 120 cm</strong></div><div class="stat"><span>Current Status</span><strong id="safeStatus">READY</strong></div></div></section>
<section id="alerts" class="page"><div class="hero"><div><h2>Alerts</h2><p>Recent detected objects and risk events.</p></div></div><div class="card"><table class="tables"><thead><tr><th>Time</th><th>Angle</th><th>Distance</th><th>Level</th></tr></thead><tbody id="alertRows"></tbody></table></div></section>
<section id="analytics" class="page"><div class="hero"><div><h2>Analytics</h2><p>Your radar session statistics.</p></div></div><div class="stats"><div class="stat"><span>Total</span><strong id="aTotal">0</strong></div><div class="stat"><span>Detected</span><strong id="aDetected">0</strong></div><div class="stat"><span>Rate</span><strong id="aRate">0%</strong></div><div class="stat"><span>Average</span><strong id="aAvg">0 cm</strong></div></div><div class="card" style="margin-top:18px"><h3>Threat Mix</h3><p id="mix" style="color:#8fa3ad">Danger 0 · Warning 0 · Safe 0</p></div></section>
<section id="settings" class="page"><div class="hero"><div><h2>Settings</h2><p>Adjust the monitoring thresholds.</p></div></div><div class="settings"><div class="card"><div class="row"><div>Range</div><input id="setRange" type="number" min="50" max="1000" value="400"></div><div class="row"><div>Danger threshold</div><input id="setDanger" type="number" min="1" max="999" value="60"></div><div class="row"><div>Warning threshold</div><input id="setWarning" type="number" min="2" max="1000" value="120"></div><button class="btn primary" id="saveSettings">Save Settings</button></div><div class="card"><h3>Account</h3><p style="color:#8fa3ad">Logged in as <b>{{ username }}</b>. Your readings are kept separate from other accounts.</p><p style="color:#8fa3ad">Use Demo Mode for presentation or Connect Arduino for live hardware.</p></div></div></section>
</div></main></div><script>
(function(){'use strict';
const $=id=>document.getElementById(id); let running=false, demo=false, frozen=false, serialPort=null, reader=null, step=0, last=null, targets=new Map(); let timer=null;
let settings=JSON.parse(localStorage.getItem('radarSettings')||'null')||{range:400,danger:60,warning:120};
function save(){localStorage.setItem('radarSettings',JSON.stringify(settings));$('range').textContent=settings.range+' cm';$('dangerVal').textContent='< '+settings.danger+' cm';$('warningVal').textContent=settings.danger+'–'+settings.warning+' cm';$('safeVal').textContent='>= '+settings.warning+' cm';}
function level(d){return d<settings.danger?'HIGH':d<settings.warning?'MEDIUM':'LOW'}
function nav(name){document.querySelectorAll('.page').forEach(p=>p.classList.toggle('active',p.id===name));document.querySelectorAll('.nav button').forEach(b=>b.classList.toggle('active',b.dataset.page===name));location.hash=name;if(name==='analytics')stats();if(name==='alerts')renderAlerts();requestAnimationFrame(()=>{resize();draw('radar');draw('radar2');});}
document.querySelectorAll('.nav button').forEach(b=>b.addEventListener('click',()=>nav(b.dataset.page)));
function resize(){['radar','radar2'].forEach(id=>{const c=$(id);if(c){c.width=c.clientWidth*devicePixelRatio;c.height=c.clientHeight*devicePixelRatio}})}
window.addEventListener('resize',()=>{resize();requestAnimationFrame(()=>{draw('radar');draw('radar2');});});
function draw(id){const c=$(id);if(!c)return;const ctx=c.getContext('2d'),w=c.clientWidth,h=c.clientHeight,dpr=devicePixelRatio||1;c.width=w*dpr;c.height=h*dpr;ctx.setTransform(dpr,0,0,dpr,0,0);ctx.clearRect(0,0,w,h);ctx.fillStyle='#020607';ctx.fillRect(0,0,w,h);const cx=w/2,cy=h*.9,R=Math.min(w*.46,h*.82);ctx.strokeStyle='rgba(65,120,115,.32)';ctx.lineWidth=1;for(let i=1;i<=5;i++){ctx.beginPath();ctx.arc(cx,cy,R*i/5,Math.PI,2*Math.PI);ctx.stroke()}for(let a=0;a<=180;a+=30){let r=(a-180)*Math.PI/180;ctx.beginPath();ctx.moveTo(cx,cy);ctx.lineTo(cx+Math.cos(r)*R,cy+Math.sin(r)*R);ctx.stroke()}let sweep=(step*5)%181,r=(sweep-180)*Math.PI/180;ctx.strokeStyle='rgba(57,255,136,.9)';ctx.lineWidth=2.5;ctx.beginPath();ctx.moveTo(cx,cy);ctx.lineTo(cx+Math.cos(r)*R,cy+Math.sin(r)*R);ctx.stroke();for(const t of targets.values()){if(!frozen && Date.now()-t.time>6000)continue;let rr=Math.min(R,Math.max(5,t.distance/settings.range*R)),tr=(t.angle-180)*Math.PI/180,x=cx+Math.cos(tr)*rr,y=cy+Math.sin(tr)*rr,col=t.threat==='HIGH'?'#ff4057':t.threat==='MEDIUM'?'#ffc857':'#39ff88';ctx.fillStyle=col;ctx.shadowBlur=15;ctx.shadowColor=col;ctx.beginPath();ctx.arc(x,y,7,0,Math.PI*2);ctx.fill();ctx.shadowBlur=0;ctx.strokeStyle=col;ctx.beginPath();ctx.arc(x,y,13,0,Math.PI*2);ctx.stroke();ctx.fillStyle='#e8f1f4';ctx.font='bold 11px Arial';ctx.fillText(Math.round(t.distance)+' cm',x+16,y-5)}ctx.fillStyle='#39ff88';ctx.beginPath();ctx.arc(cx,cy,4,0,Math.PI*2);ctx.fill()}
function process(d,source){let a=Number(d.angle),dist=Number(d.distance);if(!Number.isFinite(a)||!Number.isFinite(dist)||a<0||a>180||dist<=0||dist>1000)return;last={angle:a,distance:dist,threat:level(dist),source,time:Date.now()};targets.set(a,last);$('angle').textContent=Math.round(a)+'°';$('distance').innerHTML=Math.round(dist)+' <small style="font-size:16px;color:#8da0aa">cm</small>';$('threat').textContent=last.threat;$('threat').className='threat '+last.threat.toLowerCase();$('object').textContent=dist<settings.warning?'OBJECT DETECTED':'CLEAR';$('status').textContent='SCANNING';$('safeStatus').textContent=last.threat;$('radarState').textContent='● LIVE';addServer(a,dist,source);step++;draw('radar');draw('radar2')}
let pending=Promise.resolve();function addServer(a,d,s){pending=pending.then(()=>fetch('/api/reading',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({angle:a,distance:d,source:s})}).catch(()=>{}))}
function loop(){if(!running)return;const [a,d]=demo_reading();process({angle:a,distance:d},'demo');timer=setTimeout(loop,260)}
function demo_reading(){let a=(step*5)%181, d=180+Math.random()*150;const t={30:55,75:90,120:45,155:70};for(const k in t)if(Math.abs(a-Number(k))<=5)d=t[k]+(Math.random()*6-3);return [a,d]}
function resetTarget(){last=null;targets.clear();$('angle').textContent='--°';$('distance').innerHTML='-- <small style="font-size:16px;color:#8da0aa">cm</small>';$('threat').textContent='NO ACTIVE SCAN';$('threat').className='threat low';$('object').textContent='NO ACTIVE SCAN';$('status').textContent='READY';$('safeStatus').textContent='READY';draw('radar');draw('radar2')}
function start(){if(running)return;resetTarget();frozen=false;running=true;demo=true;$('sys').textContent='DEMO RUNNING';$('demoStatus').textContent='Demo mode is running.';$('demo').textContent='Stop Demo Mode';$('radarState').textContent='● LIVE';$('status').textContent='SCANNING';loop()}
async function stop(){running=false;demo=false;if(timer)clearTimeout(timer);timer=null;if(reader){try{await reader.cancel()}catch(e){}try{reader.releaseLock()}catch(e){}reader=null}if(serialPort){try{await serialPort.close()}catch(e){}serialPort=null}$('sys').textContent='SYSTEM ONLINE';$('radarState').textContent='● STOPPED';$('status').textContent='STOPPED';$('usbStatus').textContent='Arduino not connected.';$('demo').textContent='Start Demo Mode';$('demoStatus').textContent='Demo is stopped.';frozen=true;draw('radar');draw('radar2');}
$('start').addEventListener('click',start);$('stop').addEventListener('click',stop);$('demo').addEventListener('click',()=>running?stop():start());
$('usb').addEventListener('click',async()=>{if(!('serial' in navigator)){ $('usbStatus').textContent='Web Serial needs Chrome or Edge over HTTPS or localhost.';return}if(running)await stop();try{serialPort=await navigator.serial.requestPort();await serialPort.open({baudRate:115200});$('usbStatus').textContent='Arduino connected — receiving data.';running=true;demo=false;$('demo').textContent='Start Demo Mode';$('demoStatus').textContent='Demo is stopped.';$('sys').textContent='ARDUINO CONNECTED';$('radarState').textContent='● LIVE';$('status').textContent='SCANNING';readSerial();}catch(e){serialPort=null;$('usbStatus').textContent='Connection cancelled or failed.';}});
if('serial' in navigator){navigator.serial.addEventListener('disconnect',e=>{if(serialPort===e.port){running=false;demo=false;serialPort=null;reader=null;$('usbStatus').textContent='Arduino disconnected.';$('sys').textContent='SYSTEM ONLINE';$('radarState').textContent='● STANDBY';}})}
async function readSerial(){if(!serialPort||!serialPort.readable)return;reader=serialPort.readable.getReader();let buf='';try{while(serialPort&&running){const {value,done}=await reader.read();if(done)break;buf+=new TextDecoder().decode(value);let lines=buf.split(/\\r?\\n/);buf=lines.pop();for(const line of lines){let x=line.trim();if(!x)continue;try{let j=JSON.parse(x);process(j,'arduino')}catch(e){let p=x.split(',');if(p.length>=2)process({angle:p[0].trim(),distance:p[1].trim()},'arduino')}}}}catch(e){$('usbStatus').textContent='Arduino connection lost.'}finally{try{reader.releaseLock()}catch(e){}}}
async function stats(){const r=await fetch(`/api/stats?danger=${settings.danger}&warning=${settings.warning}`);const d=await r.json();$('total').textContent=d.total;$('detected').textContent=d.detected;$('rate').textContent=d.rate+'%';$('avg').textContent=d.average+' cm';$('aTotal').textContent=d.total;$('aDetected').textContent=d.detected;$('aRate').textContent=d.rate+'%';$('aAvg').textContent=d.average+' cm';$('mix').textContent='Danger '+d.high+' · Warning '+d.warning+' · Safe '+d.safe}
async function renderAlerts(){const r=await fetch(`/api/alerts?danger=${settings.danger}&warning=${settings.warning}`);const rows=await r.json();$('alertRows').innerHTML=rows.map(x=>'<tr><td>'+new Date(x.ts*1000).toLocaleTimeString()+'</td><td>'+Math.round(x.angle)+'°</td><td>'+Math.round(x.distance)+' cm</td><td style="font-weight:700;color:'+(x.level==='HIGH'?'#ff4057':x.level==='MEDIUM'?'#ffc857':'#39ff88')+'">'+x.level+'</td></tr>').join('')||'<tr><td colspan="4">No readings yet.</td></tr>'}
$('saveSettings').addEventListener('click',()=>{let range=Number($('setRange').value),danger=Number($('setDanger').value),warning=Number($('setWarning').value);if(range>=50&&range<=1000&&danger>0&&danger<warning&&warning<range){settings={range,danger,warning};save();draw('radar');draw('radar2')}});
save(); requestAnimationFrame(()=>{if(location.hash)nav(location.hash.slice(1)); else nav('home');}); setInterval(()=>{if(running)stats()},1200); stats();
})();
</script></body></html>'''

@app.after_request
def security_headers(response):
    response.headers.setdefault('X-Content-Type-Options', 'nosniff')
    response.headers.setdefault('X-Frame-Options', 'DENY')
    response.headers.setdefault('Referrer-Policy', 'same-origin')
    response.headers.setdefault('Permissions-Policy', 'serial=(self)')
    return response

@app.route('/')
@login_required
def home(): return render_template_string(HTML, username=current_user()['username'])

@app.route('/api/reading',methods=['POST'])
@login_required
def reading():
    p=request.get_json(silent=True) or {}
    try:a=float(p['angle']);d=float(p['distance'])
    except: return jsonify(ok=False,error='Invalid reading'),400
    if not (0<=a<=180 and 0<d<=1000): return jsonify(ok=False,error='Out of range'),400
    add_reading(a,d,str(p.get('source') or 'api')); return jsonify(ok=True)

def thresholds_from_request():
    try: danger=float(request.args.get('danger',60)); warning=float(request.args.get('warning',120))
    except (TypeError,ValueError): danger,warning=60,120
    if not (0<danger<warning<1000): danger,warning=60,120
    return danger,warning

@app.route('/api/stats')
@login_required
def stats_api():
    danger,warning=thresholds_from_request(); rows=get_readings(); ds=[r['distance'] for r in rows]
    high=sum(d<danger for d in ds); warn=sum(danger<=d<warning for d in ds); safe=sum(d>=warning for d in ds); det=high+warn
    return jsonify(total=len(ds),detected=det,rate=round(det/len(ds)*100,2) if ds else 0,average=round(sum(ds)/len(ds),2) if ds else 0,high=high,warning=warn,safe=safe)

@app.route('/api/alerts')
@login_required
def alerts():
    danger,warning=thresholds_from_request(); rows=get_readings(50); out=[]
    for r in reversed(rows):
        d=r['distance']; out.append({'ts':r['ts'],'angle':r['angle'],'distance':d,'level':'HIGH' if d<danger else 'MEDIUM' if d<warning else 'LOW'})
    return jsonify(out)

@app.route('/api/health')
def health(): return jsonify(ok=True,service='Smart Ultrasonic Radar',version='4.0',database='postgres' if USE_POSTGRES else 'sqlite')

@app.route('/api/export')
@login_required
def export():
    out=io.StringIO(); w=csv.writer(out); w.writerow(['timestamp','angle','distance','source']);
    for r in get_readings(): w.writerow([r['ts'],r['angle'],r['distance'],r['source']])
    return send_file(io.BytesIO(out.getvalue().encode()),mimetype='text/csv',as_attachment=True,download_name='radar_readings.csv')

if __name__=='__main__':
    print('Smart Ultrasonic Radar Started')
    print('Open: http://127.0.0.1:5001')
    app.run(host='127.0.0.1',port=int(os.environ.get('PORT','5001')),debug=False,use_reloader=False)
