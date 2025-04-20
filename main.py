import os
import io
import uuid
import re
import csv
import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext, filedialog
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
        rto, rest = p[2:4], p[4:]
    else:
        rto, rest = "0"+p[2], p[3:]
    letters = ''.join(filter(str.isalpha, rest))
    nums    = ''.join(filter(str.isdigit, rest)).rjust(4,'0')
    return f"{state}-{rto}-{letters or 'X'}-{nums}"

class EditableDialog(tk.Toplevel):
    def __init__(self, master, img_path, plate, on_confirm, on_retake):
        super().__init__(master)
        self.title("Edit Number Plate")
        self.on_confirm, self.on_retake = on_confirm, on_retake

        img = Image.open(img_path); img.thumbnail((400,300))
        self.photo = ImageTk.PhotoImage(img)
        tk.Label(self, image=self.photo).pack(padx=10,pady=10)

        tk.Label(self, text="Plate:", font=('Segoe UI',12)).pack(pady=(0,5))
        self.entry = tk.Entry(self, font=('Segoe UI',14), justify='center')
        self.entry.insert(0, plate); self.entry.pack(pady=(0,10))

        btnf = ttk.Frame(self); btnf.pack(pady=10)
        ttk.Button(btnf, text="✅ Confirm", command=self._confirm).pack(side="left", padx=5)
        ttk.Button(btnf, text="🔄 Retake",  command=self._retake).pack(side="left", padx=5)

    def _confirm(self):
        plate = self.entry.get().strip().upper()
        if not plate:
            messagebox.showwarning("Empty","Please enter a plate."); return
        self.on_confirm(plate); self.destroy()

    def _retake(self):
        self.on_retake(); self.destroy()

class ParkingApp:
    def __init__(self, root):
        self.root = root
        root.title("🚗 Parking Management System")
        root.geometry("1000x600")
        root.configure(bg="#f0f0f0")
        self._make_styles()

        self.nav = ttk.Notebook(root)
        self.entry_tab = ttk.Frame(self.nav)
        self.exit_tab  = ttk.Frame(self.nav)
        self.nav.add(self.entry_tab, text="🚙 Entry")
        self.nav.add(self.exit_tab, text="🏁 Exit")
        self.nav.pack(fill="both",expand=True)

        self._build_tab(self.entry_tab, True)
        self._build_tab(self.exit_tab, False)

        self.nav.bind("<<NotebookTabChanged>>", self._on_tab_change)
        self.entry_tab.start_camera()

    def _make_styles(self):
        s = ttk.Style(); s.theme_use('clam')
        s.configure("TNotebook", background="#e1e1e1", padding=10)
        s.configure("TNotebook.Tab", padding=[10,6], font=('Segoe UI',12,'bold'))
        s.configure("TButton", font=('Segoe UI',10,'bold'), padding=6)
        s.configure("TLabel", background="#f0f0f0", font=('Segoe UI',10))

    def _build_tab(self, frame, is_entry):
        frame.columnconfigure(0, weight=3)
        frame.columnconfigure(1, weight=1)
        props = list(property_col.find())
        section = "Entry" if is_entry else "Exit"

        # Left side (controls + video)
        left = ttk.Frame(frame); left.grid(row=0,column=0,sticky="nsew",padx=5,pady=5)
        left.rowconfigure(3, weight=1)

        ttk.Label(left,text="Property:").grid(row=0,column=0,sticky="w")
        prop_var = tk.StringVar(); names=[p['name'] for p in props]
        cbp = ttk.Combobox(left,textvariable=prop_var,values=names,state="readonly")
        cbp.current(0); cbp.grid(row=0,column=1,sticky="ew",padx=5)

        slots_lbl = ttk.Label(left,text=""); slots_lbl.grid(row=1,column=0,columnspan=2,sticky="w")
        def refresh_slots(*_):
            doc = property_col.find_one({"name":prop_var.get()})
            if doc:
                slots_lbl.config(text=f"Slots: {doc['available_parking_spaces']}/{doc['parking_spaces']}")
        prop_var.trace_add('write', refresh_slots); refresh_slots()

        ttk.Label(left,text="Camera:").grid(row=2,column=0,sticky="w")
        cam_var = tk.IntVar(); cbcam = ttk.Combobox(left,textvariable=cam_var,values=CAMERA_INDEXES,state="readonly",width=3)
        cbcam.current(0); cbcam.grid(row=2,column=1,sticky="w",padx=5)

        canvas = tk.Label(left,bg="black"); canvas.grid(row=3,column=0,columnspan=2,sticky="nsew",pady=5)

        btn = ttk.Button(left,text="📸 Capture & Edit",
            command=lambda:self._capture_and_edit(
                frame._state['frame'], is_entry, frame._log,
                prop_var.get(), refresh_slots, btn))
        btn.grid(row=4,column=0,columnspan=2,pady=10)

        # Right side (logs + clear/export)
        right = ttk.Frame(frame); right.grid(row=0,column=1,sticky="nsew",padx=5,pady=5)
        right.rowconfigure(1, weight=1)

        ctrl = ttk.Frame(right); ctrl.grid(row=0,column=0,sticky="ew")
        clear = ttk.Button(ctrl, text="🗑 Clear Logs", command=lambda: frame._log.delete('1.0','end'))
        clear.pack(side="left",padx=2)
        export= ttk.Button(ctrl, text="⬇️ Export CSV", command=lambda:self._export(frame._log, section))
        export.pack(side="left",padx=2)

        log = scrolledtext.ScrolledText(right,width=30,font=("Consolas",10))
        log.grid(row=1,column=0,sticky="nsew",pady=5)
        log.insert("end",f"📄 {section} Log:\n")
        self._load_logs(log, is_entry)

        # Camera state & functions
        state = {'cap':None,'frame':None}
        frame._state = state
        frame._log   = log

        def update_feed():
            cap=state['cap']
            if cap and cap.isOpened():
                ok,frm=cap.read()
                if ok:
                    state['frame']=frm
                    photo=ImageTk.PhotoImage(Image.fromarray(cv2.cvtColor(frm,cv2.COLOR_BGR2RGBA)))
                    canvas.imgtk=photo; canvas.config(image=photo)
            canvas.after(30,update_feed)

        def start_camera(evt=None):
            if state['cap']: state['cap'].release()
            cap=cv2.VideoCapture(cam_var.get())
            if not cap.isOpened():
                messagebox.showerror("Camera","Cannot open camera"); return
            state['cap']=cap
            log.insert("end",f"✅ {section} Camera {cam_var.get()} started\n")
            update_feed()

        cbcam.bind("<<ComboboxSelected>>",start_camera)
        frame.start_camera = start_camera
        frame.stop_camera  = lambda: state['cap'].release() if state['cap'] else None
        start_camera()

    def _on_tab_change(self,_):
        for tab in (self.entry_tab, self.exit_tab):
            if hasattr(tab,'stop_camera'):
                tab.stop_camera()
        cur = self.nav.select(); widget = self.nav.nametowidget(cur)
        if hasattr(widget,'start_camera'): widget.start_camera()

    def _capture_and_edit(self, frame, is_entry, log, prop_name, refresh_slots, btn):
        if frame is None:
            return messagebox.showwarning("No Frame","No camera frame available")
        btn.config(state="disabled")
        os.makedirs(ASSETS_DIR,exist_ok=True)
        path = os.path.join(ASSETS_DIR,f"{uuid.uuid4()}.jpg")
        cv2.imwrite(path,frame)
        plate = detect_text(path)

        def on_confirm(edited):
            self._save_record(edited, is_entry, log, prop_name, refresh_slots)
            btn.config(state="normal")
        def on_retake():
            btn.config(state="normal")
            self._capture_and_edit(frame,is_entry,log,prop_name,refresh_slots,btn)

        EditableDialog(self.root,path,plate,on_confirm,on_retake)

    def _save_record(self, plate, is_entry, log, prop_name, refresh_slots):
        now=datetime.now()
        prop=property_col.find_one({"name":prop_name})
        pid=prop['_id']
        if is_entry:
            if prop['available_parking_spaces']<=0:
                return messagebox.showwarning("Full","No slots available")
            rec={
                "parking_id": str(uuid.uuid4())[:8],
                "property_id": str(pid),
                "vehicle_no": plate,
                "entry_time": now,
                "fee": 0,
                "mode_of_payment":"cash"
            }
            parking_col.insert_one(rec)
            property_col.update_one({"_id":pid},{"$inc":{"available_parking_spaces":-1}})
            log.insert("end",f"Entry {plate} @ {now:%H:%M:%S}\n")
        else:
            upd=parking_col.find_one_and_update(
                {"vehicle_no":plate,"exit_time":None,"property_id":str(pid)},
                {"$set":{"exit_time":now}}
            )
            if upd:
                property_col.update_one({"_id":pid},{"$inc":{"available_parking_spaces":1}})
                log.insert("end",f"Exit {plate} @ {now:%H:%M:%S}\n")
            else:
                log.insert("end",f"No entry found for {plate}\n")
        log.see("end"); refresh_slots()
        messagebox.showinfo("Success",f"{'Entry' if is_entry else 'Exit'} recorded")

    def _load_logs(self, log, is_entry):
        qry = {"exit_time":None} if is_entry else {"exit_time":{"$ne":None}}
        for d in parking_col.find(qry).sort("entry_time",-1).limit(10):
            ts = d["entry_time"] if is_entry else d["exit_time"]
            icon = "Entry" if is_entry else "Exit"
            log.insert("end",f"{icon} {d['vehicle_no']} @ {ts:%H:%M:%S}\n")
        log.see("end")

    def _export(self, log, section):
        lines = log.get("1.0","end").strip().splitlines()[1:]
        if not lines:
            return messagebox.showinfo("Export","No log entries")
        path = filedialog.asksaveasfilename(defaultextension=".csv",
            filetypes=[("CSV","*.csv")], title="Save Logs")
        if not path: return
        with open(path,'w',newline='') as f:
            w=csv.writer(f); w.writerow(["Action","Plate","Time"])
            for line in lines:
                parts=line.split()
                w.writerow([parts[0], parts[1], parts[3]])
        messagebox.showinfo("Export","Logs exported")

if __name__=="__main__":
    root = tk.Tk()
    ParkingApp(root)
    root.mainloop()
