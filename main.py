import os
import io
import uuid
import re
import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext
from PIL import Image, ImageTk
import cv2
from datetime import datetime
from pymongo import MongoClient
from bson import ObjectId
from google.cloud import vision

# ---- CONFIG ----
SERVICE_ACCOUNT_PATH = "service_account.json"
ASSETS_DIR = "assets"

if not os.path.exists(SERVICE_ACCOUNT_PATH):
    raise FileNotFoundError("Service account JSON not found.")
os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = SERVICE_ACCOUNT_PATH

MONGODB_URI = (
    "mongodb+srv://apms4bb:memoriesbringback"
    "@caspianbms.erpwt.mongodb.net/caspiandb"
    "?retryWrites=true&w=majority&appName=Caspianbms"
)
client = MongoClient(MONGODB_URI)
db = client['caspiandb']
parking_col  = db['parking']
property_col = db['property']

def find_cameras(max_index=5):
    cams = []
    for i in range(max_index):
        cap = cv2.VideoCapture(i)
        if cap.isOpened():
            cams.append(i)
            cap.release()
    return cams

CAMERA_INDEXES = find_cameras(5)

def detect_text(image_path):
    v_client = vision.ImageAnnotatorClient()
    with io.open(image_path, 'rb') as f:
        content = f.read()
    image = vision.Image(content=content)
    resp = v_client.text_detection(image=image)
    texts = resp.text_annotations
    if not texts:
        return ""
    raw = texts[0].description.upper().replace(" ", "").replace("-", "")
    matches = re.findall(r'[A-Z]{2}\d{1,2}[A-Z]{0,2}\d{3,4}', raw)
    if not matches:
        return ""
    p = matches[0]
    state = p[:2]
    if len(p) >= 4 and p[3].isdigit():
        rto = p[2:4]; rest = p[4:]
    else:
        rto = "0" + p[2]; rest = p[3:]
    letters = ''.join(filter(str.isalpha, rest))
    nums    = ''.join(filter(str.isdigit, rest)).rjust(4, '0')
    return f"{state}-{rto}-{letters or 'X'}-{nums}"

class EditableDialog(tk.Toplevel):
    def __init__(self, master, img_path, plate, on_confirm, on_retake):
        super().__init__(master)
        self.title("Edit Number Plate")
        self.on_confirm = on_confirm
        self.on_retake  = on_retake

        img = Image.open(img_path)
        img.thumbnail((400, 300))
        self.photo = ImageTk.PhotoImage(img)
        tk.Label(self, image=self.photo).pack(padx=10, pady=10)

        tk.Label(self, text="Plate:", font=('Segoe UI',12)).pack(pady=(0,5))
        self.entry = tk.Entry(self, font=('Segoe UI',14), justify='center')
        self.entry.insert(0, plate)
        self.entry.pack(pady=(0,10))

        btnf = ttk.Frame(self); btnf.pack(pady=10)
        ttk.Button(btnf, text="✅ Confirm", command=self._confirm).pack(side="left", padx=5)
        ttk.Button(btnf, text="🔄 Retake",  command=self._retake).pack(side="left", padx=5)

    def _confirm(self):
        plate = self.entry.get().strip().upper()
        if not plate:
            messagebox.showwarning("Empty", "Please enter a plate.")
            return
        self.on_confirm(plate)
        self.destroy()

    def _retake(self):
        self.on_retake()
        self.destroy()

class ParkingApp:
    def __init__(self, root):
        self.root = root
        root.title("🚗 Parking Management System")
        root.geometry("1000x600")
        root.configure(bg="#f0f0f0")

        self._make_styles()
        self.navbar = ttk.Notebook(root)
        self.entry_frame = ttk.Frame(self.navbar)
        self.exit_frame  = ttk.Frame(self.navbar)
        self.navbar.add(self.entry_frame, text="🚙 Entry")
        self.navbar.add(self.exit_frame,  text="🏁 Exit")
        self.navbar.pack(expand=1, fill="both")

        self._build_tab(self.entry_frame, is_entry=True)
        self._build_tab(self.exit_frame,  is_entry=False)

        self.navbar.bind("<<NotebookTabChanged>>", self._on_tab_change)
        self.entry_frame.start_camera()

    def _make_styles(self):
        s = ttk.Style()
        s.theme_use('clam')
        s.configure("TNotebook",   background="#e1e1e1", padding=10)
        s.configure("TNotebook.Tab", padding=[10,6], font=('Segoe UI',12,'bold'))
        s.configure("TButton",     font=('Segoe UI',10,'bold'), padding=6)
        s.configure("TLabel",      background="#f0f0f0", font=('Segoe UI',10))

    def _build_tab(self, frame, *, is_entry):
        section = "Entry" if is_entry else "Exit"
        props = list(property_col.find())

        container = ttk.Frame(frame)
        container.pack(fill="both", expand=True, padx=10, pady=10)
        left  = ttk.Frame(container); left.pack(side="left",  fill="both", expand=True)
        right = ttk.Frame(container, width=300); right.pack(side="right", fill="y")

        # Property selector
        ttk.Label(left, text="Select Property:").pack(pady=5)
        property_var = tk.StringVar()
        names = [p['name'] for p in props]
        prop_cb = ttk.Combobox(left, textvariable=property_var,
                               values=names, state="readonly")
        prop_cb.current(0)
        prop_cb.pack()

        # Slots display
        slots_lbl = ttk.Label(left, text="")
        slots_lbl.pack(pady=(2,10))
        def refresh_slots(*_):
            sel = property_var.get()
            doc = property_col.find_one({"name": sel})
            if doc:
                total = doc.get('parking_spaces',0)
                avail = doc.get('available_parking_spaces',0)
                slots_lbl.config(text=f"Slots: {avail}/{total}")
        property_var.trace_add('write', refresh_slots)
        refresh_slots()

        # Camera selector
        ttk.Label(left, text="Select Camera:").pack(pady=5)
        camera_var = tk.IntVar()
        cam_cb = ttk.Combobox(left, textvariable=camera_var,
                              values=CAMERA_INDEXES, state="readonly")
        cam_cb.current(0)
        cam_cb.pack()

        # Video canvas
        canvas = tk.Label(left, bg="black", width=800, height=400)
        canvas.pack(fill="both", expand=True, padx=10, pady=10)

        # Log box
        log_box = scrolledtext.ScrolledText(right, wrap=tk.WORD,
                                            width=40, height=25,
                                            font=("Consolas",10))
        log_box.pack(padx=5, pady=5)
        log_box.insert(tk.END, f"📄 {section} Log:\n")
        self._load_logs(log_box, is_entry)

        state = {'cap': None, 'frame': None}

        def update_feed():
            cap = state['cap']
            if cap and cap.isOpened():
                ok, frm = cap.read()
                if ok:
                    state['frame'] = frm
                    img = cv2.cvtColor(frm, cv2.COLOR_BGR2RGBA)
                    photo = ImageTk.PhotoImage(Image.fromarray(img))
                    canvas.imgtk = photo
                    canvas.config(image=photo)
            canvas.after(15, update_feed)

        def start_camera(evt=None):
            if state['cap']:
                state['cap'].release()
            idx = camera_var.get()
            cap = cv2.VideoCapture(idx)
            if not cap.isOpened():
                messagebox.showerror("Camera Error", f"Cannot open camera {idx}")
                return
            state['cap'] = cap
            log_box.insert(tk.END, f"✅ {section} Camera {idx} started\n")
            update_feed()

        btn = ttk.Button(left,
                         text="📸 Capture & Edit Plate",
                         command=lambda: self._capture_and_edit(
                             state['frame'], is_entry, log_box,
                             property_var.get(), refresh_slots))
        btn.pack(pady=10)

        cam_cb.bind("<<ComboboxSelected>>", start_camera)
        frame.start_camera = start_camera
        frame.stop_camera  = lambda: state['cap'].release() if state['cap'] else None

    def _on_tab_change(self, ev):
        # stop all tabs' cameras
        for frm in (self.entry_frame, self.exit_frame):
            if hasattr(frm, 'stop_camera'):
                frm.stop_camera()
        # start newly selected
        cur    = self.navbar.select()
        widget = self.navbar.nametowidget(cur)
        if hasattr(widget, 'start_camera'):
            widget.start_camera()

    def _capture_and_edit(self, frame, is_entry, log_box, prop_name, refresh_slots):
        if frame is None:
            return messagebox.showwarning("No Frame", "No camera frame available.")
        os.makedirs(ASSETS_DIR, exist_ok=True)
        path = os.path.join(ASSETS_DIR, f"{uuid.uuid4()}.jpg")
        cv2.imwrite(path, frame)
        plate = detect_text(path)

        def on_confirm(edited):
            self._save_record(edited, is_entry, log_box, prop_name, refresh_slots)

        def on_retake():
            self._capture_and_edit(frame, is_entry, log_box, prop_name, refresh_slots)

        EditableDialog(self.root, path, plate, on_confirm, on_retake)

    def _save_record(self, plate, is_entry, log_box, prop_name, refresh_slots):
        now = datetime.now()
        doc = property_col.find_one({"name": prop_name})
        if not doc:
            return messagebox.showerror("Error","Property not found")
        pid = doc['_id']

        if is_entry:
            if doc['available_parking_spaces'] <= 0:
                return messagebox.showwarning("Full","No slots available")
            rec = {
                "parking_id":    str(uuid.uuid4())[:8],
                "property_id":   str(pid),
                "vehicle_no":    plate,
                "entry_time":    now,
                "fee":           0,
                "mode_of_payment":"cash"
            }
            parking_col.insert_one(rec)
            property_col.update_one(
                {"_id": pid},
                {"$inc": {"available_parking_spaces": -1}}
            )
            log_box.insert(tk.END,
                f"📥 Entry {plate} @ {now:%H:%M:%S} (Prop: {prop_name})\n"
            )
        else:
            upd = parking_col.find_one_and_update(
                {"vehicle_no": plate, "exit_time": None, "property_id": str(pid)},
                {"$set": {"exit_time": now}}
            )
            if upd:
                property_col.update_one(
                    {"_id": pid},
                    {"$inc": {"available_parking_spaces": 1}}
                )
                log_box.insert(tk.END,
                    f"📤 Exit  {plate} @ {now:%H:%M:%S} (Prop: {prop_name})\n"
                )
            else:
                log_box.insert(tk.END,
                    f"⚠️ No entry for {plate} @ {prop_name}\n"
                )

        log_box.see(tk.END)
        refresh_slots()
        messagebox.showinfo("Success", f"Plate saved: {plate}")

    def _load_logs(self, log_box, is_entry):
        qry  = {"exit_time": None} if is_entry else {"exit_time": {"$ne": None}}
        for d in parking_col.find(qry).sort("entry_time", -1).limit(10):
            vehicle   = d.get("vehicle_no","UNKNOWN")
            ts_field  = "entry_time" if is_entry else "exit_time"
            timestamp = d.get(ts_field)
            # property_id is stored as string of _id
            try:
                prop_obj = property_col.find_one({"_id": ObjectId(d["property_id"])})
                prop_name = prop_obj["name"]
            except:
                prop_name = "Unknown"
            icon   = "📥" if is_entry else "📤"
            action = "Entry" if is_entry else "Exit"
            if timestamp:
                log_box.insert(tk.END,
                    f"{icon} {action} {vehicle} @ {timestamp:%H:%M:%S} (Prop: {prop_name})\n"
                )
        log_box.see(tk.END)

if __name__ == "__main__":
    root = tk.Tk()
    ParkingApp(root)
    root.mainloop()
