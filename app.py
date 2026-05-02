"""
Desire — Venture Funding Platform
Flask Backend  |  SQLAlchemy  |  JWT Auth
All bugs fixed:
  - JWT secret from env
  - Slug collision guard + name-change slug sync
  - Null-body guard on all POST routes
  - Null-check user after JWT lookup
  - Input validation on register (names, password length)
  - Role-based meeting status transitions + value validation
  - db.create_all/seed runs at import time (works with gunicorn)
  - timezone import used (UTC-aware timestamps)
  - Pitch upload: real file storage route
  - Plan field + /api/user/plan route for billing
  - get_jwt import removed (was unused)
  - DATABASE_URL env var support for PostgreSQL on Render
    (falls back to sqlite:////tmp/desire.db if not set)
  - postgres:// → postgresql:// URL rewrite for SQLAlchemy compat
"""
 
from flask import Flask, request, jsonify, send_from_directory
from flask_sqlalchemy import SQLAlchemy
from flask_bcrypt import Bcrypt
from flask_jwt_extended import (
    JWTManager, create_access_token, jwt_required, get_jwt_identity
)
from flask_cors import CORS
from datetime import datetime, timedelta, timezone
import os, json, re
 
BASE = os.path.dirname(os.path.abspath(__file__))
 
app = Flask(__name__, static_folder=os.path.join(BASE, "static"))
_db_url = os.environ.get("DATABASE_URL", f"sqlite:////tmp/desire.db")
# Render gives postgres:// but SQLAlchemy needs postgresql://
if _db_url.startswith("postgres://"):
    _db_url = _db_url.replace("postgres://", "postgresql://", 1)
app.config["SQLALCHEMY_DATABASE_URI"] = _db_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
# FIX: read secret from env; hard-coded fallback only for local dev
app.config["JWT_SECRET_KEY"]             = os.environ.get("JWT_SECRET_KEY", "change-me-in-production")
app.config["JWT_ACCESS_TOKEN_EXPIRES"]   = timedelta(days=7)
 
db     = SQLAlchemy(app)
bcrypt = Bcrypt(app)
jwt    = JWTManager(app)
CORS(app, resources={r"/api/*": {"origins": "*"}})
 
 
# ─── Models ────────────────────────────────────────────────────────────────
 
class User(db.Model):
    id         = db.Column(db.Integer, primary_key=True)
    first_name = db.Column(db.String(80),  nullable=False)
    last_name  = db.Column(db.String(80),  nullable=False)
    email      = db.Column(db.String(120), unique=True, nullable=False)
    password   = db.Column(db.String(200), nullable=False)
    role       = db.Column(db.String(20),  nullable=False)   # startup | investor
    company    = db.Column(db.String(120))
    bio        = db.Column(db.Text)
    location   = db.Column(db.String(100))
    avatar_url = db.Column(db.String(200))
    plan       = db.Column(db.String(30),  default="free")   # FIX: billing plan field
    # FIX: use timezone-aware UTC timestamps everywhere
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
 
    meetings_sent     = db.relationship("Meeting",      foreign_keys="Meeting.sender_id",    back_populates="sender",   lazy="dynamic")
    meetings_received = db.relationship("Meeting",      foreign_keys="Meeting.receiver_id",  back_populates="receiver", lazy="dynamic")
    messages_sent     = db.relationship("Message",      foreign_keys="Message.sender_id",    back_populates="sender",   lazy="dynamic")
    messages_received = db.relationship("Message",      foreign_keys="Message.receiver_id",  back_populates="receiver", lazy="dynamic")
    notifications     = db.relationship("Notification", back_populates="user",               lazy="dynamic")
    saved             = db.relationship("SavedStartup", back_populates="user",               lazy="dynamic")
 
    def to_dict(self):
        return {
            "id": self.id, "first_name": self.first_name, "last_name": self.last_name,
            "email": self.email, "role": self.role,
            "company": self.company or "", "bio": self.bio or "",
            "location": self.location or "", "avatar_url": self.avatar_url or "",
            "plan": self.plan or "free",
            "created_at": self.created_at.isoformat()
        }
 
 
class Startup(db.Model):
    id          = db.Column(db.Integer, primary_key=True)
    owner_id    = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    name        = db.Column(db.String(120), nullable=False)
    slug        = db.Column(db.String(80),  unique=True, nullable=False)
    emoji       = db.Column(db.String(10),  default="🚀")
    tagline     = db.Column(db.String(200))
    description = db.Column(db.Text)
    stage       = db.Column(db.String(30))
    sector      = db.Column(db.String(80))
    ask         = db.Column(db.String(30))
    arr         = db.Column(db.String(30))
    growth      = db.Column(db.String(20))
    location    = db.Column(db.String(100))
    founded     = db.Column(db.String(10))
    website     = db.Column(db.String(200))
    team_json   = db.Column(db.Text, default="[]")
    pitch_json  = db.Column(db.Text, default="[]")   # FIX: persisted pitch files
    created_at  = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
 
    saves = db.relationship("SavedStartup", back_populates="startup", lazy="dynamic")
 
    @property
    def team(self):
        try:   return json.loads(self.team_json)
        except: return []
 
    @property
    def pitches(self):
        try:   return json.loads(self.pitch_json)
        except: return []
 
    def to_dict(self):
        return {
            "id": self.id, "owner_id": self.owner_id,
            "name": self.name, "slug": self.slug, "emoji": self.emoji,
            "tagline": self.tagline or "", "description": self.description or "",
            "stage": self.stage or "", "sector": self.sector or "",
            "ask": self.ask or "", "arr": self.arr or "", "growth": self.growth or "",
            "location": self.location or "", "founded": self.founded or "",
            "website": self.website or "", "team": self.team,
            "pitches": self.pitches,
            "save_count": self.saves.count(),
            "created_at": self.created_at.isoformat()
        }
 
 
class Meeting(db.Model):
    id          = db.Column(db.Integer, primary_key=True)
    sender_id   = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    receiver_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    company     = db.Column(db.String(120))
    meet_type   = db.Column(db.String(50))
    date        = db.Column(db.String(20))
    time        = db.Column(db.String(10))
    duration    = db.Column(db.String(20))
    message     = db.Column(db.Text)
    status      = db.Column(db.String(20), default="pending")
    created_at  = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
 
    sender   = db.relationship("User", foreign_keys=[sender_id],   back_populates="meetings_sent")
    receiver = db.relationship("User", foreign_keys=[receiver_id], back_populates="meetings_received")
 
    def to_dict(self):
        return {
            "id": self.id, "sender_id": self.sender_id, "receiver_id": self.receiver_id,
            "sender_name": f"{self.sender.first_name} {self.sender.last_name}" if self.sender else "",
            "company": self.company or "", "meet_type": self.meet_type or "",
            "date": self.date or "", "time": self.time or "",
            "duration": self.duration or "", "message": self.message or "",
            "status": self.status, "created_at": self.created_at.isoformat()
        }
 
 
class Message(db.Model):
    id          = db.Column(db.Integer, primary_key=True)
    sender_id   = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    receiver_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    body        = db.Column(db.Text, nullable=False)
    read        = db.Column(db.Boolean, default=False)
    created_at  = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
 
    sender   = db.relationship("User", foreign_keys=[sender_id],   back_populates="messages_sent")
    receiver = db.relationship("User", foreign_keys=[receiver_id], back_populates="messages_received")
 
    def to_dict(self):
        return {
            "id": self.id, "sender_id": self.sender_id, "receiver_id": self.receiver_id,
            "sender_name":   f"{self.sender.first_name} {self.sender.last_name}"   if self.sender   else "",
            "receiver_name": f"{self.receiver.first_name} {self.receiver.last_name}" if self.receiver else "",
            "body": self.body, "read": self.read,
            "created_at": self.created_at.isoformat()
        }
 
 
class Notification(db.Model):
    id         = db.Column(db.Integer, primary_key=True)
    user_id    = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    icon       = db.Column(db.String(10))
    title      = db.Column(db.String(200))
    body       = db.Column(db.Text)
    read       = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
 
    user = db.relationship("User", back_populates="notifications")
 
    def to_dict(self):
        return {
            "id": self.id, "icon": self.icon or "🔔",
            "title": self.title or "", "body": self.body or "",
            "read": self.read, "created_at": self.created_at.isoformat()
        }
 
 
class SavedStartup(db.Model):
    id         = db.Column(db.Integer, primary_key=True)
    user_id    = db.Column(db.Integer, db.ForeignKey("user.id"),    nullable=False)
    startup_id = db.Column(db.Integer, db.ForeignKey("startup.id"), nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
 
    user    = db.relationship("User",    back_populates="saved")
    startup = db.relationship("Startup", back_populates="saves")
    __table_args__ = (db.UniqueConstraint("user_id", "startup_id"),)
 
 
class PipelineDeal(db.Model):
    id         = db.Column(db.Integer, primary_key=True)
    user_id    = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    name       = db.Column(db.String(120))
    sector     = db.Column(db.String(80))
    ask        = db.Column(db.String(30))
    stage      = db.Column(db.String(40), default="Prospect")
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
 
    def to_dict(self):
        return {"id": self.id, "user_id": self.user_id,
                "name": self.name, "sector": self.sector or "",
                "ask": self.ask or "", "stage": self.stage}
 
 
# ─── Helpers ───────────────────────────────────────────────────────────────
 
def ok(data=None, **kw):
    p = {"ok": True}
    if data is not None: p["data"] = data
    p.update(kw)
    return jsonify(p), 200
 
def err(msg, code=400):
    return jsonify({"ok": False, "error": msg}), code
 
def push_notif(user_id, icon, title, body=""):
    db.session.add(Notification(user_id=user_id, icon=icon, title=title, body=body))
    db.session.commit()
 
# FIX: proper slug generator + collision resolver
def _make_slug(name):
    slug = name.lower().strip()
    slug = re.sub(r'[^a-z0-9\s-]', '', slug)
    slug = re.sub(r'\s+', '-', slug)
    slug = re.sub(r'-+', '-', slug).strip('-')
    return slug or "startup"
 
def _unique_slug(base, exclude_id=None):
    candidate, n = base, 2
    while True:
        q = Startup.query.filter_by(slug=candidate)
        if exclude_id:
            q = q.filter(Startup.id != exclude_id)
        if not q.first():
            return candidate
        candidate = f"{base}-{n}"; n += 1
 
 
# ─── Auth ──────────────────────────────────────────────────────────────────
 
@app.route("/api/auth/register", methods=["POST"])
def register():
    # FIX: guard against missing body
    d = request.get_json() or {}
    first    = d.get("first_name", "").strip()
    last     = d.get("last_name",  "").strip()
    email    = d.get("email",      "").lower().strip()
    password = d.get("password",   "")
    role     = d.get("role", "startup")
 
    # FIX: full input validation
    if not first or not last:    return err("First and last name are required")
    if not email:                return err("Email is required")
    if not password or len(password) < 6:
        return err("Password must be at least 6 characters")
    if role not in ("startup", "investor"):
        return err("Invalid role")
    if User.query.filter_by(email=email).first():
        return err("Email already registered")
 
    u = User(first_name=first, last_name=last, email=email,
             password=bcrypt.generate_password_hash(password).decode(),
             role=role, company=d.get("company", "").strip())
    db.session.add(u); db.session.commit()
    push_notif(u.id, "🎉", "Welcome to Desire!", "Your account is ready.")
    return ok({"user": u.to_dict(), "token": create_access_token(identity=str(u.id))})
 
 
@app.route("/api/auth/login", methods=["POST"])
def login():
    d = request.get_json() or {}   # FIX: guard against missing body
    u = User.query.filter_by(email=d.get("email", "").lower()).first()
    if not u or not bcrypt.check_password_hash(u.password, d.get("password", "")):
        return err("Invalid email or password", 401)
    return ok({"user": u.to_dict(), "token": create_access_token(identity=str(u.id))})
 
 
@app.route("/api/auth/demo", methods=["POST"])
def demo_login():
    d    = request.get_json() or {}   # FIX: guard against missing body
    role = d.get("role", "startup")
    if role not in ("startup", "investor"): role = "startup"
    email = f"demo_{role}@desire.vc"
    u = User.query.filter_by(email=email).first()
    if not u:
        u = User(
            first_name="Alex" if role == "startup" else "Jordan",
            last_name ="Johnson" if role == "startup" else "Pierce",
            email=email, password=bcrypt.generate_password_hash("demo").decode(),
            role=role,
            company="NeuralNest AI" if role == "startup" else "Sequoia Capital"
        )
        db.session.add(u); db.session.commit()
        push_notif(u.id, "🎉", "Welcome, demo user!", "Explore the platform.")
    return ok({"user": u.to_dict(), "token": create_access_token(identity=str(u.id))})
 
 
@app.route("/api/auth/me")
@jwt_required()
def me():
    u = User.query.get(int(get_jwt_identity()))
    if not u: return err("User not found", 404)
    return ok(u.to_dict())
 
 
# ─── User / Profile / Plan ─────────────────────────────────────────────────
 
@app.route("/api/user/profile", methods=["PUT"])
@jwt_required()
def update_profile():
    u = User.query.get(int(get_jwt_identity()))
    if not u: return err("User not found", 404)   # FIX: null-check
    d = request.get_json() or {}
    for f in ("first_name", "last_name", "company", "bio", "location"):
        if f in d: setattr(u, f, d[f])
    db.session.commit()
    return ok(u.to_dict())
 
 
@app.route("/api/user/plan", methods=["PUT"])
@jwt_required()
def update_plan():
    """FIX: actual billing plan endpoint (was missing entirely)."""
    u = User.query.get(int(get_jwt_identity()))
    if not u: return err("User not found", 404)
    d = request.get_json() or {}
    plan = d.get("plan", "free")
    if plan not in {"free", "partner", "apex", "growth", "scale"}:
        return err("Invalid plan")
    u.plan = plan
    db.session.commit()
    push_notif(u.id, "💳", f"Upgraded to {plan.capitalize()}!", "Subscription is now active.")
    return ok(u.to_dict())
 
 
# ─── Startups ──────────────────────────────────────────────────────────────
 
@app.route("/api/startups")
def list_startups():
    q    = request.args.get("q", "").lower()
    sect = request.args.get("sector", "")
    query = Startup.query
    if q:
        query = query.filter(db.or_(
            Startup.name.ilike(f"%{q}%"), Startup.tagline.ilike(f"%{q}%"),
            Startup.sector.ilike(f"%{q}%"), Startup.description.ilike(f"%{q}%")))
    if sect:
        query = query.filter(Startup.sector.ilike(f"%{sect}%"))
    return ok([s.to_dict() for s in query.order_by(Startup.created_at.desc()).all()])
 
 
@app.route("/api/startups/<slug>")
def get_startup(slug):
    s = Startup.query.filter_by(slug=slug).first()
    if not s: return err("Not found", 404)
    return ok(s.to_dict())
 
 
@app.route("/api/startups", methods=["POST"])
@jwt_required()
def create_startup():
    u = User.query.get(int(get_jwt_identity()))
    if not u: return err("User not found", 404)
    d    = request.get_json() or {}
    name = d.get("name", "").strip()
    if not name: return err("Startup name is required")
 
    # FIX: deduplicate slug before INSERT — no more IntegrityError crashes
    slug = _unique_slug(_make_slug(name))
 
    s = Startup(
        owner_id=u.id, name=name, slug=slug,
        emoji=d.get("emoji","🚀"), tagline=d.get("tagline",""),
        description=d.get("description",""), stage=d.get("stage","SEED"),
        sector=d.get("sector",""), ask=d.get("ask",""), arr=d.get("arr",""),
        growth=d.get("growth",""), location=d.get("location",""),
        founded=d.get("founded",""), website=d.get("website",""),
        team_json=json.dumps(d.get("team",[]))
    )
    db.session.add(s); db.session.commit()
    return ok(s.to_dict())
 
 
@app.route("/api/startups/<int:sid>", methods=["PUT"])
@jwt_required()
def update_startup(sid):
    u = User.query.get(int(get_jwt_identity()))
    if not u: return err("User not found", 404)
    s = Startup.query.get(sid)
    if not s: return err("Not found", 404)
    if s.owner_id != u.id: return err("Forbidden", 403)
 
    d = request.get_json() or {}
    for f in ("emoji","tagline","description","stage","sector",
              "ask","arr","growth","location","founded","website"):
        if f in d: setattr(s, f, d[f])
 
    # FIX: when name changes regenerate + deduplicate slug so they stay in sync
    if "name" in d and d["name"].strip():
        s.name = d["name"].strip()
        s.slug = _unique_slug(_make_slug(s.name), exclude_id=sid)
 
    if "team" in d: s.team_json = json.dumps(d["team"])
    db.session.commit()
    return ok(s.to_dict())
 
 
# ─── Pitch Upload (real file storage) ──────────────────────────────────────
 
@app.route("/api/startups/<int:sid>/pitch", methods=["POST"])
@jwt_required()
def upload_pitch(sid):
    """FIX: actual file-upload route (was completely missing — frontend had a fake progress bar)."""
    u = User.query.get(int(get_jwt_identity()))
    if not u: return err("User not found", 404)
    s = Startup.query.get(sid)
    if not s: return err("Startup not found", 404)
    if s.owner_id != u.id: return err("Forbidden", 403)
 
    if "pitch" not in request.files:
        return err("No file provided")
    file = request.files["pitch"]
    if not file.filename:
        return err("No file selected")
 
    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in {".pdf", ".ppt", ".pptx"}:
        return err("Only PDF, PPT, PPTX files are allowed")
 
    upload_dir = os.path.join(BASE, "static", "pitches", str(sid))
    os.makedirs(upload_dir, exist_ok=True)
    safe_name = re.sub(r'[^a-zA-Z0-9._-]', '_', file.filename)
    filepath  = os.path.join(upload_dir, safe_name)
    file.save(filepath)
 
    file_url  = f"/static/pitches/{sid}/{safe_name}"
    file_size = os.path.getsize(filepath)
 
    pitches = [p for p in s.pitches if p["name"] != safe_name]   # overwrite same name
    pitches.append({
        "name": safe_name, "url": file_url, "size": file_size,
        "uploaded_at": datetime.now(timezone.utc).isoformat()
    })
    s.pitch_json = json.dumps(pitches)
    db.session.commit()
    return ok({"pitches": s.pitches})
 
 
@app.route("/api/startups/<int:sid>/pitch/<filename>", methods=["DELETE"])
@jwt_required()
def delete_pitch(sid, filename):
    u = User.query.get(int(get_jwt_identity()))
    if not u: return err("User not found", 404)
    s = Startup.query.get(sid)
    if not s: return err("Not found", 404)
    if s.owner_id != u.id: return err("Forbidden", 403)
 
    safe_name = re.sub(r'[^a-zA-Z0-9._-]', '_', filename)
    fp = os.path.join(BASE, "static", "pitches", str(sid), safe_name)
    if os.path.exists(fp): os.remove(fp)
 
    s.pitch_json = json.dumps([p for p in s.pitches if p["name"] != safe_name])
    db.session.commit()
    return ok({"pitches": s.pitches})
 
 
# ─── Saved / Watchlist ─────────────────────────────────────────────────────
 
@app.route("/api/startups/<int:sid>/save", methods=["POST"])
@jwt_required()
def toggle_save(sid):
    uid = int(get_jwt_identity())
    ex  = SavedStartup.query.filter_by(user_id=uid, startup_id=sid).first()
    if ex:
        db.session.delete(ex); db.session.commit()
        return ok({"saved": False})
    db.session.add(SavedStartup(user_id=uid, startup_id=sid)); db.session.commit()
    return ok({"saved": True})
 
 
@app.route("/api/user/saved")
@jwt_required()
def get_saved():
    uid  = int(get_jwt_identity())
    rows = SavedStartup.query.filter_by(user_id=uid).all()
    return ok([r.startup.to_dict() for r in rows])
 
 
# ─── Meetings ──────────────────────────────────────────────────────────────
 
@app.route("/api/meetings")
@jwt_required()
def list_meetings():
    uid  = int(get_jwt_identity())
    sent = Meeting.query.filter_by(sender_id=uid).all()
    recv = Meeting.query.filter_by(receiver_id=uid).all()
    all_m = {m.id: m for m in sent + recv}
    return ok([m.to_dict() for m in sorted(all_m.values(), key=lambda x: x.created_at, reverse=True)])
 
 
@app.route("/api/meetings", methods=["POST"])
@jwt_required()
def create_meeting():
    uid = int(get_jwt_identity())
    d   = request.get_json() or {}
    receiver_id = d.get("receiver_id", uid)
    m = Meeting(
        sender_id=uid, receiver_id=receiver_id,
        company=d.get("company",""), meet_type=d.get("meet_type","Video Call"),
        date=d.get("date",""), time=d.get("time",""),
        duration=d.get("duration","45 min"),
        message=d.get("message",""), status="pending"
    )
    db.session.add(m); db.session.commit()
    sender = User.query.get(uid)
    push_notif(receiver_id, "📅",
               f"Meeting request from {sender.first_name} {sender.last_name}",
               f"{m.meet_type} on {m.date} at {m.time}")
    return ok(m.to_dict())
 
 
VALID_STATUSES = {"pending", "confirmed", "cancelled", "done"}
 
@app.route("/api/meetings/<int:mid>/status", methods=["PUT"])
@jwt_required()
def update_meeting_status(mid):
    uid = int(get_jwt_identity())
    m   = Meeting.query.get(mid)
    if not m: return err("Not found", 404)
    if m.sender_id != uid and m.receiver_id != uid:
        return err("Forbidden", 403)
 
    d          = request.get_json() or {}
    new_status = d.get("status", "")
 
    # FIX: validate status value
    if new_status not in VALID_STATUSES:
        return err(f"Invalid status. Allowed: {', '.join(VALID_STATUSES)}")
 
    # FIX: role-based transition rules
    is_receiver = (m.receiver_id == uid)
 
    if new_status == "confirmed" and not is_receiver:
        return err("Only the meeting recipient can confirm")
    if new_status == "pending":
        return err("Cannot revert meeting to pending")
 
    m.status = new_status
    db.session.commit()
    return ok(m.to_dict())
 
 
# ─── Messages ──────────────────────────────────────────────────────────────
 
@app.route("/api/messages/threads")
@jwt_required()
def message_threads():
    uid      = int(get_jwt_identity())
    sent_ids = db.session.query(Message.receiver_id).filter_by(sender_id=uid).distinct()
    recv_ids = db.session.query(Message.sender_id).filter_by(receiver_id=uid).distinct()
    pids     = set([r[0] for r in sent_ids] + [r[0] for r in recv_ids])
    threads  = []
    for pid in pids:
        partner = User.query.get(pid)
        if not partner: continue
        last = Message.query.filter(db.or_(
            db.and_(Message.sender_id==uid,  Message.receiver_id==pid),
            db.and_(Message.sender_id==pid,  Message.receiver_id==uid)
        )).order_by(Message.created_at.desc()).first()
        unread = Message.query.filter_by(sender_id=pid, receiver_id=uid, read=False).count()
        threads.append({
            "partner_id": pid,
            "partner_name": f"{partner.first_name} {partner.last_name}",
            "partner_company": partner.company or "",
            "last_message": last.body if last else "",
            "last_time": last.created_at.isoformat() if last else "",
            "unread": unread
        })
    return ok(sorted(threads, key=lambda x: x["last_time"], reverse=True))
 
 
@app.route("/api/messages/<int:pid>")
@jwt_required()
def get_messages(pid):
    uid  = int(get_jwt_identity())
    msgs = Message.query.filter(db.or_(
        db.and_(Message.sender_id==uid, Message.receiver_id==pid),
        db.and_(Message.sender_id==pid, Message.receiver_id==uid)
    )).order_by(Message.created_at).all()
    for msg in msgs:
        if msg.receiver_id == uid and not msg.read:
            msg.read = True
    db.session.commit()
    return ok([msg.to_dict() for msg in msgs])
 
 
@app.route("/api/messages/<int:pid>", methods=["POST"])
@jwt_required()
def send_message(pid):
    uid  = int(get_jwt_identity())
    d    = request.get_json() or {}
    body = d.get("body","").strip()
    if not body: return err("Message cannot be empty")
    msg = Message(sender_id=uid, receiver_id=pid, body=body)
    db.session.add(msg); db.session.commit()
    sender = User.query.get(uid)
    push_notif(pid, "💬", f"New message from {sender.first_name} {sender.last_name}", body[:80])
    return ok(msg.to_dict())
 
 
# ─── Notifications ─────────────────────────────────────────────────────────
 
@app.route("/api/notifications")
@jwt_required()
def list_notifications():
    uid    = int(get_jwt_identity())
    notifs = Notification.query.filter_by(user_id=uid)\
             .order_by(Notification.created_at.desc()).limit(50).all()
    return ok([n.to_dict() for n in notifs])
 
 
@app.route("/api/notifications/read-all", methods=["PUT"])
@jwt_required()
def mark_all_read():
    uid = int(get_jwt_identity())
    Notification.query.filter_by(user_id=uid, read=False).update({"read": True})
    db.session.commit()
    return ok()
 
 
@app.route("/api/notifications/<int:nid>/read", methods=["PUT"])
@jwt_required()
def mark_read(nid):
    uid = int(get_jwt_identity())
    n   = Notification.query.filter_by(id=nid, user_id=uid).first()
    if n: n.read = True; db.session.commit()
    return ok()
 
 
# ─── Pipeline ──────────────────────────────────────────────────────────────
 
@app.route("/api/pipeline")
@jwt_required()
def get_pipeline():
    uid   = int(get_jwt_identity())
    deals = PipelineDeal.query.filter_by(user_id=uid).order_by(PipelineDeal.created_at).all()
    return ok([d.to_dict() for d in deals])
 
 
@app.route("/api/pipeline", methods=["POST"])
@jwt_required()
def add_pipeline():
    uid = int(get_jwt_identity())
    d   = request.get_json() or {}
    deal = PipelineDeal(user_id=uid, name=d.get("name",""),
                        sector=d.get("sector",""), ask=d.get("ask",""),
                        stage=d.get("stage","Prospect"))
    db.session.add(deal); db.session.commit()
    return ok(deal.to_dict())
 
 
@app.route("/api/pipeline/<int:did>", methods=["PUT"])
@jwt_required()
def update_pipeline(did):
    uid  = int(get_jwt_identity())
    deal = PipelineDeal.query.filter_by(id=did, user_id=uid).first()
    if not deal: return err("Not found", 404)
    d = request.get_json() or {}
    if "stage" in d: deal.stage = d["stage"]
    db.session.commit()
    return ok(deal.to_dict())
 
 
@app.route("/api/pipeline/<int:did>", methods=["DELETE"])
@jwt_required()
def delete_pipeline(did):
    uid  = int(get_jwt_identity())
    deal = PipelineDeal.query.filter_by(id=did, user_id=uid).first()
    if deal: db.session.delete(deal); db.session.commit()
    return ok()
 
 
# ─── Serve Static Frontend ─────────────────────────────────────────────────
 
@app.route("/", defaults={"path": ""})
@app.route("/<path:path>")
def serve(path):
    fp = os.path.join(app.static_folder, path)
    if path and os.path.exists(fp):
        return send_from_directory(app.static_folder, path)
    return send_from_directory(app.static_folder, "index.html")
 
 
# ─── Seed Demo Startups ────────────────────────────────────────────────────
 
def seed():
    if Startup.query.count() > 0:
        return
    sys_user = User.query.filter_by(email="system@desire.vc").first()
    if not sys_user:
        sys_user = User(first_name="Desire", last_name="Platform",
                        email="system@desire.vc",
                        password=bcrypt.generate_password_hash("system").decode(),
                        role="startup", company="Desire")
        db.session.add(sys_user); db.session.commit()
 
    demo = [
        {"name":"NeuralNest AI","slug":"neuralnest-ai","emoji":"🤖",
         "tagline":"Autonomous AI agents for enterprise workflow automation",
         "description":"NeuralNest AI deploys intelligent AI agents that handle complex knowledge-intensive workflows with human-level reasoning. Founded by ex-Google and ex-OpenAI researchers, our orchestration layer integrates with 200+ enterprise tools and has delivered 60% cost reductions for clients in finance and healthcare.",
         "stage":"SERIES A","sector":"AI/ML · B2B SaaS","ask":"$8M","arr":"$2.4M","growth":"3.2×",
         "location":"Bangalore, IN","founded":"2022",
         "team":[{"av":"AJ","name":"Alex Johnson","role":"CEO · ex-Google AI"},{"av":"PR","name":"Priya Rao","role":"CTO · ex-OpenAI"},{"av":"SK","name":"Sameer Khan","role":"CPO · ex-Stripe"},{"av":"LM","name":"Lisa Mueller","role":"CFO · ex-Goldman"}]},
        {"name":"VerdaCure Health","slug":"verdacure-health","emoji":"🧬",
         "tagline":"Personalised oncology drug delivery via nanoparticle biosensors",
         "description":"VerdaCure combines real-time genomic profiling with nanoparticle-based targeted drug delivery. Our biosensor platform dynamically adjusts drug dosage based on tumour biomarkers. 4 patents granted, Phase 2 clinical trials underway.",
         "stage":"SEED","sector":"HealthTech · BioTech","ask":"$5M","arr":"Phase 2","growth":"N/A",
         "location":"Hyderabad, IN","founded":"2021",
         "team":[{"av":"DK","name":"Dr. Devika Kumar","role":"CEO · ex-AIIMS"},{"av":"RN","name":"Raj Nair","role":"CTO · ex-Novartis"}]},
        {"name":"FluxPay","slug":"fluxpay","emoji":"💳",
         "tagline":"Instant cross-border B2B payments with embedded FX hedging",
         "description":"FluxPay eliminates international B2B payment friction. Integrates with accounting software, provides real-time FX hedging, and uses AI for multi-jurisdiction compliance. Processes $800K in daily transactions.",
         "stage":"PRE-SEED","sector":"FinTech · Payments","ask":"$2M","arr":"$180K MRR","growth":"N/A",
         "location":"Mumbai, IN","founded":"2023",
         "team":[{"av":"AS","name":"Arjun Shah","role":"CEO · ex-PayU"},{"av":"MM","name":"Maya Mehta","role":"CTO · ex-Razorpay"}]},
        {"name":"Solaro Energy","slug":"solaro-energy","emoji":"🌱",
         "tagline":"Next-gen perovskite solar panels with 41% efficiency",
         "description":"Solaro pioneers perovskite-silicon tandem cells achieving 41% efficiency — more than double the industry average — at 30% lower manufacturing cost than conventional silicon panels.",
         "stage":"SERIES B","sector":"CleanTech · Hardware","ask":"$30M","arr":"$12M","growth":"5.6×",
         "location":"Chennai, IN","founded":"2020",
         "team":[{"av":"VS","name":"Vikram Suresh","role":"CEO · ex-Tesla Energy"},{"av":"AN","name":"Anika Nair","role":"CTO · ex-SunPower"}]},
        {"name":"ShieldNet Security","slug":"shieldnet-security","emoji":"🔒",
         "tagline":"Zero-trust network security powered by behavioural AI",
         "description":"ShieldNet detects zero-day threats in under 200ms using behavioural AI trained on 10B+ threat signatures. Zero-hardware deployment, live in under one hour.",
         "stage":"SEED","sector":"Cybersecurity · AI","ask":"$6M","arr":"$900K","growth":"8×",
         "location":"Pune, IN","founded":"2022",
         "team":[{"av":"KP","name":"Kiran Patil","role":"CEO · ex-Palo Alto Networks"},{"av":"SB","name":"Shruti Bhat","role":"CTO · ex-CrowdStrike"}]},
        {"name":"CartAI Commerce","slug":"cartai-commerce","emoji":"🛒",
         "tagline":"Hyper-personalised D2C commerce engine with AI merchandising",
         "description":"CartAI powers next-gen D2C brands with AI-native merchandising, predictive inventory, and a 1-click checkout that boosts conversion by 34%. Processes over $2M GMV daily across 180+ brands.",
         "stage":"SERIES A","sector":"E-Commerce · AI","ask":"$12M","arr":"$4.8M","growth":"2.9×",
         "location":"Delhi, IN","founded":"2021",
         "team":[{"av":"PK","name":"Pooja Kapoor","role":"CEO · ex-Flipkart"},{"av":"AB","name":"Aryan Bose","role":"CTO · ex-Amazon"}]},
    ]
    for data in demo:
        team = data.pop("team", [])
        db.session.add(Startup(owner_id=sys_user.id, team_json=json.dumps(team), **data))
    db.session.commit()
    print("✅  Demo data seeded")
 
 
# ─── Init (works with both `python app.py` and gunicorn) ──────────────────
# FIX: moved out of __main__ block so tables are created under any WSGI server
with app.app_context():
    db.create_all()
    seed()
 
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=False, host="0.0.0.0", port=port
