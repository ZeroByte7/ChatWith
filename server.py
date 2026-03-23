"""
ChatWith - Python/Flask Backend
Ready to host - all credentials configured
"""

import os
import random
import string
from datetime import datetime, timezone
from functools import wraps

from dotenv import load_dotenv
from flask import Flask, request, jsonify, send_from_directory
from flask_socketio import SocketIO, emit, join_room, leave_room
from flask_cors import CORS
from flask_pymongo import PyMongo
from werkzeug.security import generate_password_hash, check_password_hash
import jwt as pyjwt
import cloudinary
import cloudinary.uploader

load_dotenv()

app = Flask(__name__, static_folder=".")
app.config["SECRET_KEY"] = os.getenv("JWT_SECRET", "chatwith_super_secret_key_12345")
app.config["MONGO_URI"] = os.getenv("MONGO_URI")

CORS(app)
mongo = PyMongo(app)
socketio = SocketIO(app, cors_allowed_origins="*")

cloudinary.config(
    cloud_name=os.getenv("CLOUDINARY_CLOUD_NAME"),
    api_key=os.getenv("CLOUDINARY_API_KEY"),
    api_secret=os.getenv("CLOUDINARY_API_SECRET"),
)

online_users: dict = {}
uid_to_sid: dict = {}


def gen_uid(prefix="CW"):
    chars = random.choices(string.ascii_uppercase + string.digits, k=6)
    return f"{prefix}-{''.join(chars)}"


def now_time():
    return datetime.now().strftime("%H:%M")


def make_token(user):
    payload = {
        "user": {
            "id":       str(user["_id"]),
            "username": user["username"],
            "uid":      user["uid"],
            "display":  user["display"],
            "bio":      user.get("bio", ""),
        }
    }
    return pyjwt.encode(payload, app.config["SECRET_KEY"], algorithm="HS256")


def decode_token(token):
    try:
        token = token.replace("Bearer ", "")
        data = pyjwt.decode(token, app.config["SECRET_KEY"], algorithms=["HS256"])
        return data.get("user")
    except Exception:
        return None


def auth_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = request.headers.get("Authorization", "")
        user = decode_token(token)
        if not user:
            return jsonify({"msg": "Token is not valid"}), 401
        request.current_user = user
        return f(*args, **kwargs)
    return decorated


@app.route("/")
def index():
    return send_from_directory(".", "index.html")


@app.route("/uploads/<path:filename>")
def serve_upload(filename):
    return send_from_directory("uploads", filename)


@app.route("/api/register", methods=["POST"])
def register():
    data     = request.get_json()
    username = data.get("username", "").strip()
    display  = data.get("display", "").strip()
    password = data.get("pass", "")
    bio      = data.get("bio", "")
    uid      = data.get("uid", gen_uid())

    if not username or not display or not password:
        return jsonify({"msg": "Username, display name and password are required"}), 400
    if mongo.db.users.find_one({"username": username}):
        return jsonify({"msg": "Username already exists"}), 400

    mongo.db.users.insert_one({
        "username":  username,
        "display":   display,
        "pass":      generate_password_hash(password),
        "bio":       bio,
        "uid":       uid,
        "createdAt": datetime.now(timezone.utc),
    })
    return jsonify({"msg": "User registered successfully"}), 201


@app.route("/api/login", methods=["POST"])
def login():
    data  = request.get_json()
    login = data.get("login", "")
    pwd   = data.get("pass", "")

    user = mongo.db.users.find_one({"$or": [{"username": login}, {"uid": login}]})
    if not user or not check_password_hash(user["pass"], pwd):
        return jsonify({"msg": "Invalid Credentials"}), 400

    token = make_token(user)
    return jsonify({
        "token": token,
        "user": {
            "id":       str(user["_id"]),
            "username": user["username"],
            "uid":      user["uid"],
            "display":  user["display"],
            "bio":      user.get("bio", ""),
        }
    })


@app.route("/api/profile", methods=["GET"])
@auth_required
def get_profile():
    from bson import ObjectId
    user = mongo.db.users.find_one({"_id": ObjectId(request.current_user["id"])})
    if not user:
        return jsonify({"msg": "User not found"}), 404
    return jsonify({
        "id":       str(user["_id"]),
        "username": user["username"],
        "uid":      user["uid"],
        "display":  user["display"],
        "bio":      user.get("bio", ""),
    })


@app.route("/api/profile", methods=["PUT"])
@auth_required
def update_profile():
    from bson import ObjectId
    data = request.get_json()
    mongo.db.users.update_one(
        {"_id": ObjectId(request.current_user["id"])},
        {"$set": {
            "display":  data.get("display", ""),
            "username": data.get("username", ""),
            "bio":      data.get("bio", ""),
        }}
    )
    return jsonify({"msg": "Profile updated"})


@app.route("/api/profile/password", methods=["POST"])
@auth_required
def change_password():
    from bson import ObjectId
    data    = request.get_json()
    current = data.get("current", "")
    new_pwd = data.get("npass", "")

    user = mongo.db.users.find_one({"_id": ObjectId(request.current_user["id"])})
    if not check_password_hash(user["pass"], current):
        return jsonify({"msg": "Incorrect current password"}), 400

    mongo.db.users.update_one(
        {"_id": ObjectId(request.current_user["id"])},
        {"$set": {"pass": generate_password_hash(new_pwd)}}
    )
    return jsonify({"msg": "Password updated"})


@app.route("/api/profile", methods=["DELETE"])
@auth_required
def delete_account():
    from bson import ObjectId
    data     = request.get_json()
    username = data.get("username", "")
    pwd      = data.get("pass", "")

    user = mongo.db.users.find_one({"_id": ObjectId(request.current_user["id"])})
    if user["username"] != username:
        return jsonify({"msg": "Incorrect username"}), 400
    if not check_password_hash(user["pass"], pwd):
        return jsonify({"msg": "Incorrect password"}), 400

    mongo.db.users.delete_one({"_id": ObjectId(request.current_user["id"])})
    return jsonify({"msg": "Account deleted"})


@app.route("/api/rooms", methods=["POST"])
@auth_required
def create_room():
    data = request.get_json()
    name = data.get("name", "")
    bio  = data.get("bio", "")
    pwd  = data.get("pass", "")
    uid  = data.get("uid", gen_uid("RM"))

    if not name or not pwd:
        return jsonify({"msg": "Room name and password are required"}), 400

    mongo.db.rooms.insert_one({
        "name":      name,
        "uid":       uid,
        "bio":       bio,
        "pass":      pwd,
        "createdAt": datetime.now(timezone.utc),
    })
    return jsonify({"msg": "Room created", "uid": uid}), 201


@app.route("/api/contacts/request", methods=["POST"])
@auth_required
def send_contact_request():
    data       = request.get_json()
    target_uid = data.get("target_uid", "")
    sender     = request.current_user

    if target_uid == sender["uid"]:
        return jsonify({"msg": "Cannot add yourself"}), 400

    # Check not already contacts
    if mongo.db.contacts.find_one({"owner_uid": sender["uid"], "contact_uid": target_uid}):
        return jsonify({"msg": "Already a contact"}), 400

    # Check no duplicate pending request
    if mongo.db.requests.find_one({"from_uid": sender["uid"], "to_uid": target_uid}):
        return jsonify({"msg": "Request already sent"}), 400

    mongo.db.requests.insert_one({
        "from_uid":   sender["uid"],
        "from_name":  sender["display"],
        "to_uid":     target_uid,
        "created_at": datetime.now(timezone.utc),
    })

    # Notify target if online via socket
    target_sid = uid_to_sid.get(target_uid)
    if target_sid:
        socketio.emit("receive_request", {"name": sender["display"], "uid": sender["uid"]}, room=target_uid)

    return jsonify({"msg": "Request sent"})


@app.route("/api/contacts/requests", methods=["GET"])
@auth_required
def get_contact_requests():
    uid      = request.current_user["uid"]
    reqs     = list(mongo.db.requests.find({"to_uid": uid}, {"_id": 0}))
    return jsonify(reqs)


@app.route("/api/contacts/accept", methods=["POST"])
@auth_required
def accept_contact_request():
    data      = request.get_json()
    from_uid  = data.get("from_uid", "")
    from_name = data.get("from_name", "")
    me        = request.current_user

    # Remove the pending request
    mongo.db.requests.delete_one({"from_uid": from_uid, "to_uid": me["uid"]})

    # Add contact for both sides (if not already)
    if not mongo.db.contacts.find_one({"owner_uid": me["uid"], "contact_uid": from_uid}):
        mongo.db.contacts.insert_one({"owner_uid": me["uid"], "contact_uid": from_uid, "contact_name": from_name})
    if not mongo.db.contacts.find_one({"owner_uid": from_uid, "contact_uid": me["uid"]}):
        mongo.db.contacts.insert_one({"owner_uid": from_uid, "contact_uid": me["uid"], "contact_name": me["display"]})

    # Notify sender if online
    socketio.emit("request_accepted", {"name": me["display"], "uid": me["uid"]}, room=from_uid)

    return jsonify({"msg": "Contact accepted"})


@app.route("/api/contacts", methods=["GET"])
@auth_required
def get_contacts():
    uid      = request.current_user["uid"]
    contacts = list(mongo.db.contacts.find({"owner_uid": uid}, {"_id": 0, "contact_uid": 1, "contact_name": 1}))
    return jsonify(contacts)


@app.route("/api/contacts/remove", methods=["POST"])
@auth_required
def remove_contact():
    data       = request.get_json()
    target_uid = data.get("target_uid", "")
    me         = request.current_user["uid"]
    mongo.db.contacts.delete_one({"owner_uid": me, "contact_uid": target_uid})
    return jsonify({"msg": "Removed"})


@app.route("/api/contacts/requests/decline", methods=["POST"])
@auth_required
def decline_contact_request():
    data     = request.get_json()
    from_uid = data.get("from_uid", "")
    me       = request.current_user["uid"]
    mongo.db.requests.delete_one({"from_uid": from_uid, "to_uid": me})
    return jsonify({"msg": "Request declined"})


@app.route("/api/rooms/join", methods=["POST"])
@auth_required
def join_room_api():
    data = request.get_json()
    uid  = data.get("uid", "")
    pwd  = data.get("pass", "")

    room = mongo.db.rooms.find_one({"uid": uid})
    if not room or room["pass"] != pwd:
        return jsonify({"msg": "Invalid Room ID or password"}), 400

    return jsonify({"msg": "Joined", "room": {"name": room["name"], "uid": room["uid"]}})


@app.route("/api/upload", methods=["POST"])
@auth_required
def upload_file():
    if "file" not in request.files:
        return jsonify({"msg": "No file uploaded"}), 400

    file          = request.files["file"]
    original_name = file.filename

    result = cloudinary.uploader.upload(file, folder="chatwith_uploads", resource_type="auto")
    url    = result.get("secure_url", "")

    return jsonify({"url": url, "name": original_name})


@socketio.on("connect")
def on_connect():
    print(f"Client connected: {request.sid}")


@socketio.on("authenticate")
def on_authenticate(token):
    user = decode_token(token)
    if not user:
        return
    sid = request.sid
    online_users[sid] = user
    uid_to_sid[user["uid"]] = sid
    join_room(user["uid"])
    print(f"Authenticated: {user['username']}")


@socketio.on("search_users")
def on_search_users(query):
    import re
    pattern = re.compile(query, re.IGNORECASE)
    results = list(mongo.db.users.find(
        {"$or": [
            {"username": {"$regex": pattern}},
            {"uid":      {"$regex": pattern}},
            {"display":  {"$regex": pattern}},
        ]},
        {"_id": 0, "uid": 1, "display": 1}
    ).limit(10))
    return [{"name": u["display"], "uid": u["uid"]} for u in results]


@socketio.on("send_request")
def on_send_request(target_uid):
    sender = online_users.get(request.sid)
    if not sender:
        return
    socketio.emit("receive_request", {"name": sender["display"], "uid": sender["uid"]}, room=target_uid)


@socketio.on("accept_request")
def on_accept_request(target_uid):
    acceptor = online_users.get(request.sid)
    if not acceptor:
        return
    socketio.emit("request_accepted", {"name": acceptor["display"], "uid": acceptor["uid"]}, room=target_uid)


@socketio.on("send_pm")
def on_send_pm(data):
    sender = online_users.get(request.sid)
    if not sender:
        return
    socketio.emit("receive_pm", {
        "senderUid":  sender["uid"],
        "senderName": sender["display"],
        "text":       data.get("text", ""),
        "type":       data.get("type", "text"),
        "label":      data.get("label", ""),
        "timestamp":  now_time(),
    }, room=data.get("targetUid", ""))


@socketio.on("join_room_socket")
def on_join_room(room_uid):
    join_room(room_uid)


@socketio.on("leave_room_socket")
def on_leave_room(room_uid):
    leave_room(room_uid)


@socketio.on("send_room_msg")
def on_send_room_msg(data):
    sender = online_users.get(request.sid)
    if not sender:
        return
    room_uid = data.get("roomUid", "")
    socketio.emit("receive_room_msg", {
        "roomUid":    room_uid,
        "senderName": sender["display"],
        "senderUid":  sender["uid"],
        "text":       data.get("text", ""),
        "type":       data.get("type", "text"),
        "label":      data.get("label", ""),
        "timestamp":  now_time(),
    }, room=room_uid, include_self=False)


@socketio.on("disconnect")
def on_disconnect():
    sid  = request.sid
    user = online_users.pop(sid, None)
    if user:
        uid_to_sid.pop(user["uid"], None)
    print(f"Client disconnected: {sid}")


if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    print(f"Server running on port {port}")
    socketio.run(app, host="0.0.0.0", port=port, debug=False)
