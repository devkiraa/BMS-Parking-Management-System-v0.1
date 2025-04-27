import os
import io
import uuid
import re
import csv
import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext, filedialog
from PIL import Image, ImageTk, UnidentifiedImageError
import cv2
from datetime import datetime
from pymongo import MongoClient
from bson import ObjectId
from google.cloud import vision
import sys
import pymongo
import time

# ---- CONFIG ----
SERVICE_ACCOUNT_PATH = "service_account.json"
ASSETS_DIR = "assets"

# Check service account file before proceeding
if not os.path.exists(SERVICE_ACCOUNT_PATH):
    messagebox.showerror("Configuration Error", f"Service account JSON not found at: {SERVICE_ACCOUNT_PATH}")
    sys.exit()

os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = SERVICE_ACCOUNT_PATH

# MongoDB Connection
MONGODB_URI = (
    "mongodb+srv://apms4bb:memoriesbringback"
    "@caspianbms.erpwt.mongodb.net/caspiandb"
    "?retryWrites=true&w=majority&appName=Caspianbms"
)

client = None # Initialize client to None
try:
    client = MongoClient(MONGODB_URI)
    client.admin.command('ismaster') # Check connection
    db = client['caspiandb']
    parking_col  = db['parking']
    property_col = db['property']
    print("MongoDB connection successful.")
except Exception as e:
    messagebox.showerror("Database Error", f"Could not connect to MongoDB:\n{e}")
    sys.exit()

def find_cameras(max_index=5):
    """Finds available camera indexes by attempting to open and read a frame."""
    cams = []
    original_stderr = os.dup(sys.stderr.fileno())
    devnull = os.open(os.devnull, os.O_WRONLY)
    os.dup2(devnull, sys.stderr.fileno())
    os.close(devnull)

    try:
        for i in range(max_index):
            cap = cv2.VideoCapture(i)
            if cap.isOpened():
                 try:
                     ret, frame = cap.read() # Attempt to read a frame
                     if ret and frame is not None:
                         cams.append(i)
                         print(f"Found working camera index: {i}") # Debugging
                     else:
                         print(f"Camera index {i} opened but failed initial read.")
                 except Exception as e:
                      print(f"Error reading from camera index {i} during detection: {e}")
                 finally:
                     cap.release()
            else:
                pass # Camera not opened is expected for unavailable indexes

    finally:
        os.dup2(original_stderr, sys.stderr.fileno())
        os.close(original_stderr)

    return cams

CAMERA_INDEXES = find_cameras(5)

# --- Google Cloud Vision OCR ---
def detect_text(image_path):
    """Detects text (potential number plate) in an image, handling standard Indian and BH series formats."""
    v_client = vision.ImageAnnotatorClient()
    try:
        with io.open(image_path, 'rb') as f:
            content = f.read()
        image = vision.Image(content=content)
        resp = v_client.text_detection(image=image)
        texts = resp.text_annotations

        if not texts:
            print("Vision API found no text.")
            return ""

        # --- Text Processing ---
        raw = texts[0].description.upper() # Convert to uppercase immediately
        # Keep only A-Z, 0-9. Remove spaces, hyphens, and other symbols early.
        compact_raw = re.sub(r'[^A-Z0-9]', '', raw)

        print(f"Raw detected text: '{raw}'")
        print(f"Cleaned compact text: '{compact_raw}'")

        # --- Regex Matching ---

        # 1. Check for BH Series Format (e.g., 25BH4567AB)
        # Format: YY BH NNNN LL (Year, BH marker, 4 digits, 1 or 2 letters)
        bh_match = re.search(r'(\d{2})(BH)(\d{4})([A-Z]{1,2})', compact_raw)
        if bh_match:
            year, bh_marker, nums, letters = bh_match.groups()
            # Optional: Add hyphens for readability, although BH plates don't typically have them spaced this way
            formatted_plate = f"{year}-{bh_marker}-{nums}-{letters}"
            print(f"Formatted plate (BH series regex match): {formatted_plate}")
            # Return the compact version found if you prefer the raw format, or the hyphenated one
            # return compact_raw[bh_match.start():bh_match.end()] # Return exact matched part
            return formatted_plate # Return hyphenated version

        # 2. Check for Standard Indian Format (e.g., KA01AB1234 or MH05X9876)
        # Format: SS RR LL NNNN (State Code, RTO Code, Optional Letters, Numbers)
        # Adjusted regex slightly to be more robust after cleaning
        standard_match = re.search(r'([A-Z]{2})(\d{1,2})([A-Z]{1,2})?(\d{3,4})', compact_raw)
                                    #  State    RTO       Letters?    Numbers
        if standard_match:
            state, rto, letters, nums = standard_match.groups()

            # Perform formatting and padding
            rto_padded = rto.rjust(2, '0')
            nums_padded = nums.rjust(4, '0')
            # Handle cases where letters might be missing or optional in the standard format
            letters_formatted = letters if letters else 'X' # Use 'X' or 'XX' if letters are expected but missing, or adjust as needed

            formatted_plate = f"{state}-{rto_padded}-{letters_formatted}-{nums_padded}"
            print(f"Formatted plate (Standard regex match): {formatted_plate}")
            return formatted_plate

        # 3. Fallback
        print("No structured regex (BH or Standard) matched. Returning cleaned compact text.")
        return compact_raw

    except Exception as e:
        print(f"Error during text detection API call or processing: {e}")
        return f"OCR Failed: {e}"

# --- Editable Dialog for Plate Correction ---
class EditableDialog(tk.Toplevel):
    def __init__(self, master, img_path, plate, on_confirm, on_retake):
        super().__init__(master)
        self.title("Edit Number Plate")
        self.on_confirm, self.on_retake = on_confirm, on_retake
        self.transient(master)
        self.grab_set()
        self.img_path = img_path

        img_loaded = False
        try:
            img = Image.open(img_path);
            img.thumbnail((400,300), Image.Resampling.LANCZOS)
            self.photo = ImageTk.PhotoImage(img)
            tk.Label(self, image=self.photo).pack(padx=10,pady=10)
            img_loaded = True
        except (FileNotFoundError, UnidentifiedImageError) as e:
             tk.Label(self, text=f"Error loading image: {e}", fg="red").pack(padx=10,pady=10)
        except Exception as e:
             tk.Label(self, text=f"Unexpected error loading image: {e}", fg="red").pack(padx=10,pady=10)
             print(f"Error loading image in dialog: {e}")

        if not img_loaded:
             self.photo = None

        tk.Label(self, text="Plate:", font=('Segoe UI',12)).pack(pady=(0,5))
        self.entry = tk.Entry(self, font=('Segoe UI',14), justify='center')
        self.entry.insert(0, plate)
        self.entry.pack(pady=(0,10))
        self.entry.focus_set()

        btnf = ttk.Frame(self)
        btnf.pack(pady=10)
        ttk.Button(btnf, text="✅ Confirm", command=self._confirm).pack(side="left", padx=5)
        ttk.Button(btnf, text="🔄 Retake",  command=self._retake).pack(side="left", padx=5)

        self.protocol("WM_DELETE_WINDOW", self._retake)
        self.bind("<Destroy>", self._delete_image_file)

    def _delete_image_file(self, event):
        """Deletes the temporary image file when the dialog is closed."""
        if event.widget == self:
            if self.img_path and os.path.exists(self.img_path):
                try:
                    os.remove(self.img_path)
                    print(f"Deleted temporary image file: {self.img_path}")
                except Exception as e:
                    print(f"Error deleting temporary image file {self.img_path}: {e}")

    def _confirm(self):
        plate = self.entry.get().strip().upper()
        if not plate:
            messagebox.showwarning("Empty","Please enter a plate.", parent=self)
            return
        if not re.search(r'[A-Z0-9]', plate):
            messagebox.showwarning("Invalid Plate", "Plate should contain letters and numbers.", parent=self)
            return
        self.on_confirm(plate)
        self.destroy()

    def _retake(self):
        self.on_retake()
        self.destroy()

# --- Main Application Class ---
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
        self.root.after(100, self._trigger_initial_camera_start)

        # --- Bind Enter key to trigger capture ---
        self.root.bind('<Return>', self._on_enter_press)


    def _on_enter_press(self, event):
        """Handles the Enter key press to trigger capture on the current tab."""
        # Find the currently selected tab widget
        current_tab_name = self.nav.select()
        current_tab_widget = self.nav.nametowidget(current_tab_name)

        # If the tab widget has a method to trigger capture, call it
        if hasattr(current_tab_widget, 'trigger_capture'):
            current_tab_widget.trigger_capture()


    def _on_tab_change(self, _):
        """Handles tab changes by stopping the old camera and starting the new one."""
        for tab in (self.entry_tab, self.exit_tab):
            if tab and hasattr(tab, 'stop_camera'):
                 try:
                    tab.stop_camera()
                 except Exception as e:
                     print(f"Error stopping camera on tab change: {e}")

        cur = self.nav.select()
        widget = self.nav.nametowidget(cur)
        if widget and hasattr(widget,'start_camera'):
            try:
                widget.start_camera()
            except Exception as e:
                 print(f"Error starting camera on tab change: {e}")

    def _trigger_initial_camera_start(self):
        """Trigger the start_camera for the initially selected tab."""
        current_tab_name = self.nav.select()
        current_tab_widget = self.nav.nametowidget(current_tab_name)
        if current_tab_widget and hasattr(current_tab_widget, 'start_camera'):
            try:
                current_tab_widget.start_camera()
            except Exception as e:
                 print(f"Error starting camera for initial tab: {e}")
                 if hasattr(current_tab_widget, '_canvas'):
                      current_tab_widget._canvas.config(text=f"Start Error: {e}", image='')

    def _make_styles(self):
        """Configures ttk styles."""
        s = ttk.Style()
        s.theme_use('clam')
        s.configure("TNotebook", background="#e1e1e1", padding=10)
        s.configure("TNotebook.Tab", padding=[10,6], font=('Segoe UI',12,'bold'))
        s.configure("TButton", font=('Segoe UI',10,'bold'), padding=6)
        s.configure("TLabel", background="#f0f0f0", font=('Segoe UI',10))
        s.configure("VideoCanvas.TLabel", background="black", foreground="white", font=('Segoe UI', 14))

    def _build_tab(self, frame, is_entry):
        """Builds the UI elements for a single tab (Entry or Exit)."""
        frame.columnconfigure(0, weight=3)
        frame.columnconfigure(1, weight=1)

        props = list(property_col.find())
        section = "Entry" if is_entry else "Exit"

        # Left side (controls + video) frame
        left = ttk.Frame(frame)
        left.grid(row=0,column=0,sticky="nsew",padx=5,pady=5)

        # Configure columns and rows within the 'left' frame
        left.columnconfigure(0, weight=1) # Labels column allows expansion
        left.columnconfigure(1, weight=0) # Comboboxes column fixed width

        left.rowconfigure(0, weight=0) # Property row
        left.rowconfigure(1, weight=0) # Slots row
        left.rowconfigure(2, weight=0) # Camera row
        left.rowconfigure(3, weight=1) # Canvas row - Allows vertical expansion
        left.rowconfigure(4, weight=0) # Button row


        ttk.Label(left,text="Property:").grid(row=0,column=0,sticky="w", pady=2, padx=5)
        prop_var = tk.StringVar()
        names=[p['name'] for p in props]
        cbp = ttk.Combobox(left,textvariable=prop_var,values=names,state="readonly")

        property_available = bool(names)
        if property_available:
            cbp.current(0)
        else:
             cbp.set("No Properties Found")
             cbp.config(state="disabled")

        cbp.grid(row=0,column=1,sticky="ew",pady=2,padx=5)

        slots_lbl = ttk.Label(left,text="")
        slots_lbl.grid(row=1,column=0,columnspan=2,sticky="w",pady=2,padx=5)
        def refresh_slots(*_):
            selected_prop_name = prop_var.get()
            if selected_prop_name and selected_prop_name != "No Properties Found":
                try:
                    doc = property_col.find_one({"name":selected_prop_name})
                    if doc:
                        slots_lbl.config(text=f"Slots: {doc.get('available_parking_spaces', 0)}/{doc.get('parking_spaces', 0)}")
                    else:
                        slots_lbl.config(text="Slots: Error")
                except Exception as e:
                     slots_lbl.config(text="Slots: DB Error")
                     print(f"Database error during slots refresh: {e}")
            else:
                 slots_lbl.config(text="Slots: N/A")

        prop_var.trace_add('write', refresh_slots)
        if property_available: refresh_slots()

        ttk.Label(left,text="Camera:").grid(row=2,column=0,sticky="w", pady=2, padx=5)
        cam_var = tk.IntVar()

        if CAMERA_INDEXES:
            cam_var.set(CAMERA_INDEXES[0])
            cam_values = CAMERA_INDEXES
            cam_state = "readonly"
        else:
             cam_var.set(-1)
             cam_values = []
             cam_state = "disabled"

        cbcam = ttk.Combobox(left,textvariable=cam_var,values=cam_values,state=cam_state,width=3)
        if CAMERA_INDEXES:
             cbcam.current(0)

        cbcam.grid(row=2,column=1,sticky="w",pady=2,padx=5)

        # Video canvas label - positioned to expand within row 3
        canvas = ttk.Label(left, text="Initializing camera...", anchor="center", style="VideoCanvas.TLabel")
        # sticky="nsew" makes it fill its cell in the grid
        # It will expand, but the image inside won't stretch thanks to removing resize logic
        canvas.grid(row=3,column=0,columnspan=2,sticky="nsew",pady=5,padx=5)


        # --- Capture Button ---
        # Define the trigger_capture function inside _build_tab scope
        def trigger_capture():
            self._capture_and_edit(
                frame, # Pass the tab frame widget
                is_entry,
                frame._log,
                prop_var.get(),
                refresh_slots,
                btn # Pass the button reference
            )
        # Attach trigger_capture to the frame widget for access from ParkingApp
        frame.trigger_capture = trigger_capture

        btn = ttk.Button(left,text="📸 Capture & Edit",
             command=trigger_capture) # Button calls the local trigger_capture function

        if not property_available or not CAMERA_INDEXES:
             btn.config(state="disabled")
             if not property_available and not CAMERA_INDEXES:
                  btn.config(text="🚫 Setup (Prop/Cam)")
             elif not property_available:
                  btn.config(text="🚫 Add Property")
             else:
                   btn.config(text="🚫 No Camera")

        btn.grid(row=4,column=0,columnspan=2,pady=10)

        # Right side (logs + clear/export) frame
        right = ttk.Frame(frame)
        right.grid(row=0,column=1,sticky="nsew",padx=5,pady=5)
        right.rowconfigure(1, weight=1)

        ctrl = ttk.Frame(right)
        ctrl.grid(row=0,column=0,sticky="ew",pady=2,padx=5)
        clear = ttk.Button(ctrl, text="🗑 Clear Logs", command=lambda: frame._log.delete('1.0','end'))
        clear.pack(side="left",padx=2)
        export= ttk.Button(ctrl, text="⬇️ Export CSV", command=lambda:self._export(frame._log, section))
        export.pack(side="left",padx=2)

        log = scrolledtext.ScrolledText(right,width=30,font=("Consolas",10))
        log.grid(row=1,column=0,sticky="nsew",pady=5,padx=5)
        log.insert("end",f"📄 {section} Log:\n")
        self._load_logs(log, is_entry)

        state = {'cap':None,'frame':None, 'after_id':None}
        frame._state = state
        frame._log   = log
        frame._canvas = canvas
        frame._prop_var = prop_var


        def update_feed():
            """Updates the video feed on the canvas."""
            cap = frame._state['cap']
            if cap is None or not cap.isOpened():
                 if frame._state['after_id'] is not None:
                      frame._canvas.after_cancel(frame._state['after_id'])
                      frame._state['after_id'] = None
                 return

            try:
                ok, frm = cap.read()
                if ok and frm is not None:
                    frame._state['frame'] = frm

                    # Convert to RGB for PIL
                    img_rgb = cv2.cvtColor(frm, cv2.COLOR_BGR2RGB)
                    img_pil = Image.fromarray(img_rgb)

                    # --- FIX: Display PhotoImage without resizing to canvas size ---
                    # The Label will still expand to fill its grid cell (due to sticky="nsew" and weight),
                    # but the image itself will maintain its aspect ratio within that space.
                    # The black background of the canvas label will appear around the image if it doesn't fill the Label.
                    photo = ImageTk.PhotoImage(img_pil)

                    frame._canvas.imgtk = photo # Keep reference
                    frame._canvas.config(image=photo, text="") # Update image, clear text

            except Exception as e:
                 print(f"Error in update_feed: {e}")

            frame._state['after_id'] = frame._canvas.after(40, update_feed)

        def start_camera(evt=None):
            """Starts the selected camera."""
            stop_camera()

            selected_cam_index = cam_var.get()

            if not CAMERA_INDEXES:
                 frame._canvas.config(text="No Camera Found", image='')
                 frame._canvas.imgtk = None
                 return

            cap = cv2.VideoCapture(selected_cam_index)

            if not cap.isOpened():
                messagebox.showerror("Camera Error", f"Cannot open camera index {selected_cam_index}")
                log.insert("end", f"❌ Failed to open camera {selected_cam_index}\n")
                log.see("end")
                frame._state['cap'] = None
                frame._canvas.config(text="Failed to Open Camera", image='')
                frame._canvas.imgtk = None
                btn.config(state="disabled", text="🚫 Camera Error")
                return

            test_frame = None
            try:
                for _ in range(10):
                    ok, test_frame = cap.read()
                    if ok and test_frame is not None:
                        break
                    time.sleep(0.05)

                if test_frame is None:
                     cap.release()
                     frame._state['cap'] = None
                     messagebox.showerror("Camera Error", f"Camera index {selected_cam_index} opened, but failed to read initial frames.")
                     log.insert("end", f"❌ Failed to read initial frames from camera {selected_cam_index}\n")
                     log.see("end")
                     frame._canvas.config(text="No Frame Stream", image='')
                     frame._canvas.imgtk = None
                     btn.config(state="disabled", text="🚫 No Frame")
                     return

            except Exception as e:
                 cap.release()
                 frame._state['cap'] = None
                 messagebox.showerror("Camera Error", f"Error during initial frame read from camera {selected_cam_index}: {e}")
                 log.insert("end", f"❌ Error during initial read: {e}\n")
                 log.see("end")
                 frame._canvas.config(text=f"Read Error: {e}", image='')
                 frame._canvas.imgtk = None
                 btn.config(state="disabled", text="🚫 Camera Error")
                 return

            frame._state['cap'] = cap
            log.insert("end",f"✅ {section} Camera {selected_cam_index} started\n")
            log.see("end")
            frame._canvas.config(text="") # Clear text

            if property_available:
                 btn.config(state="normal", text="📸 Capture & Edit")
            else:
                 btn.config(state="disabled", text="🚫 Add Property")

            update_feed()

        def stop_camera():
            """Stops the current camera and cancels the update loop."""
            cap = frame._state['cap']
            if cap and cap.isOpened():
                cap.release()
                frame._state['cap'] = None
                log.insert("end", f"🛑 {section} Camera stopped\n")
                log.see("end")
            if frame._state['after_id'] is not None:
                 frame._canvas.after_cancel(frame._state['after_id'])
                 frame._state['after_id'] = None
            frame._canvas.config(image='', text="Camera Stopped")
            frame._canvas.imgtk = None

        frame.start_camera = start_camera
        frame.stop_camera  = stop_camera
        cbcam.bind("<<ComboboxSelected>>",start_camera)


    def _capture_and_edit(self, tab_frame, is_entry, log, prop_name, refresh_slots, btn):
        """Captures frame directly, detects text, and opens edit dialog."""
        if not prop_name or prop_name == "No Properties Found":
            messagebox.showwarning("Property Required", "Please select a property before capturing.")
            return

        btn.config(state="disabled")

        cap = tab_frame._state['cap']

        if cap is None or not cap.isOpened():
             messagebox.showwarning("No Camera", "Camera is not available or not open.")
             btn.config(state="normal")
             return

        log.insert("end", "📸 Attempting to capture frame...\n")
        log.see("end")
        captured_frame = None
        try:
            for _ in range(5): # Read several frames to get the latest one
                 ok, frame = cap.read()
                 if ok and frame is not None:
                      captured_frame = frame
                 else:
                     if not ok:
                         print("Capture read failed mid-attempt.")
                         break

            if captured_frame is None:
                messagebox.showwarning("Capture Failed", "Failed to capture valid frame from camera.")
                log.insert("end", "❌ Frame capture failed after multiple reads.\n")
                log.see("end")
                btn.config(state="normal")
                return

        except Exception as e:
            messagebox.showerror("Capture Error", f"An error occurred during frame capture: {e}")
            log.insert("end", f"❌ Capture error: {e}\n")
            log.see("end")
            btn.config(state="normal")
            return

        if captured_frame is not None:
            try:
                os.makedirs(ASSETS_DIR,exist_ok=True)
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                path = os.path.join(ASSETS_DIR,f"capture_{timestamp}_{uuid.uuid4().hex[:6]}.jpg")
                success = cv2.imwrite(path, captured_frame)
                if not success:
                     raise IOError(f"Failed to write image to {path}")

                log.insert("end", f"✅ Captured frame saved to {os.path.basename(path)}\n")
                log.see("end")

                log.insert("end", "🔍 Detecting text...\n")
                log.see("end")
                plate = detect_text(path)
                log.insert("end", f"✅ Detected: {plate or 'None found'}\n")
                log.see("end")

                def on_confirm(edited_plate):
                    if not edited_plate or edited_plate.startswith("OCR Failed"):
                         messagebox.showwarning("Invalid Plate", "Plate cannot be empty or an error message. Record not saved.")
                         log.insert("end", f"⚠️ Save aborted: Invalid plate '{edited_plate}'\n")
                         log.see("end")
                    else:
                        self._save_record(edited_plate, is_entry, log, prop_name, refresh_slots)
                    # Button re-enabled on dialog destruction via binding

                def on_retake():
                    log.insert("end", "🔄 Retake requested.\n")
                    log.see("end")
                    # Button re-enabled on dialog destruction via binding

                # Open dialog
                dialog = EditableDialog(self.root, path, plate, on_confirm, on_retake)
                # --- Re-enable button when dialog is destroyed ---
                dialog.bind("<Destroy>", lambda event: btn.config(state="normal"), add="+")


            except Exception as e:
                messagebox.showerror("Process Error", f"An error occurred during processing: {e}")
                log.insert("end", f"❌ Process error: {e}\n")
                log.see("end")
                btn.config(state="normal")
                if 'path' in locals() and path and os.path.exists(path):
                     try:
                         os.remove(path)
                         print(f"Cleaned up temp file {path} after error.")
                     except Exception as cleanup_e:
                         print(f"Error during temp file cleanup: {cleanup_e}")

    def _save_record(self, plate, is_entry, log, prop_name, refresh_slots):
        """Saves or updates parking record in the database."""
        now = datetime.now()

        try:
            prop = property_col.find_one({"name":prop_name})
            if not prop:
                messagebox.showwarning("Property Error", f"Property '{prop_name}' not found in the database.")
                log.insert("end", f"❌ Property '{prop_name}' not found in DB during save.\n")
                log.see("end")
                return

            pid = prop['_id']

            if is_entry:
                existing_entry = parking_col.find_one({
                    "vehicle_no": plate,
                    "property_id": str(pid),
                    "exit_time": None
                })
                if existing_entry:
                    messagebox.showwarning("Duplicate Entry", f"An open entry already exists for vehicle {plate} at this property.")
                    log.insert("end", f"⚠️ Duplicate entry attempt for {plate}.\n")
                    log.see("end")
                    return

                latest_prop = property_col.find_one({"_id": pid})
                if not latest_prop or latest_prop.get('available_parking_spaces', 0) <= 0:
                    messagebox.showwarning("Parking Full","No slots available at this property.")
                    log.insert("end", "❌ Parking full.\n")
                    log.see("end")
                    return

                rec = {
                    "parking_id": str(uuid.uuid4()),
                    "property_id": str(pid),
                    "vehicle_no": plate,
                    "entry_time": now,
                    "exit_time": None,
                    "fee": 0,
                    "mode_of_payment":"cash"
                }
                parking_col.insert_one(rec)
                property_col.update_one({"_id":pid},{"$inc":{"available_parking_spaces":-1}})
                log.insert("end",f"🟢 Entry {plate} @ {now:%Y-%m-%d %H:%M:%S}\n")
                messagebox.showinfo("Success",f"Entry recorded for {plate}")

            else: # is_exit
                upd = parking_col.find_one_and_update(
                    {"vehicle_no":plate,"exit_time":None,"property_id":str(pid)},
                    {"$set":{"exit_time":now}},
                    sort=[('entry_time', -1)],
                    return_document=pymongo.ReturnDocument.AFTER
                )
                if upd:
                    entry_time = upd.get('entry_time')
                    calculated_fee = 0
                    if entry_time:
                        duration_seconds = (now - entry_time).total_seconds()
                        fee_per_minute = prop.get('fee_per_minute', 0.1)
                        calculated_fee = round((duration_seconds / 60) * fee_per_minute, 2)

                    parking_col.update_one(
                         {"_id": upd["_id"]},
                         {"$set": {"fee": calculated_fee}}
                    )

                    property_col.update_one({"_id":pid},{"$inc":{"available_parking_spaces":1}})
                    log.insert("end",f"🔴 Exit {plate} @ {now:%Y-%m-%d %H:%M:%S} (Fee: ${calculated_fee:.2f})\n")
                    messagebox.showinfo("Success",f"Exit recorded for {plate}. Fee: ${calculated_fee:.2f}")
                else:
                    messagebox.showwarning("No Entry Found", f"No open entry found for vehicle {plate} at this property.")
                    log.insert("end",f"❌ No open entry found for {plate}\n")

            log.see("end")
            refresh_slots()

        except Exception as e:
            messagebox.showerror("Database Error", f"An error occurred while saving record: {e}")
            log.insert("end", f"❌ DB Error during save: {e}\n")
            log.see("end")
            refresh_slots()

    def _load_logs(self, log, is_entry):
        """Loads recent parking records into the log display."""
        try:
            qry = {"exit_time":None} if is_entry else {"exit_time":{"$ne":None}}

            recent_records = parking_col.find(qry).sort(
                "entry_time" if is_entry else "exit_time", -1
            ).limit(10)

            log.delete('1.0', 'end')
            section = "Entry" if is_entry else "Exit"
            log.insert("end",f"📄 {section} Log:\n")

            count = 0
            for d in recent_records:
                ts_key = "entry_time" if is_entry else "exit_time"
                ts = d.get(ts_key)
                if ts:
                    icon = "🟢 Entry" if is_entry else "🔴 Exit"
                    plate = d.get('vehicle_no', 'N/A')
                    time_str = ts.strftime('%Y-%m-%d %H:%M:%S')
                    log_line = f"{icon} {plate} @ {time_str}"
                    if not is_entry and 'fee' in d:
                         log_line += f" (Fee: ₹{d.get('fee', 0.0):.2f})"
                    log.insert("end", f"{log_line}\n")
                    count += 1

            if count == 0:
                 log.insert("end", f"No recent { 'entries' if is_entry else 'exits' } found.\n")

            log.see("end")

        except Exception as e:
            log.insert("end", f"❌ Error loading logs: {e}\n")
            log.see("end")

    def _export(self, log, section):
        """Exports log content to a CSV file."""
        lines = log.get("1.0","end").strip().splitlines()[1:]
        if not lines or (len(lines) == 1 and "No recent" in lines[0]) or lines[0].startswith("❌ Error loading logs"):
             return messagebox.showinfo("Export","No log entries to export or error present.")

        path = filedialog.asksaveasfilename(defaultextension=".csv",
             filetypes=[("CSV files","*.csv"), ("All files","*.*")],
             title=f"Save {section} Logs")
        if not path:
            return

        try:
            with open(path,'w',newline='', encoding='utf-8') as f:
                w = csv.writer(f)
                header = ["Action", "Plate", "Time"]
                if section == "Exit":
                     header.append("Fee")
                w.writerow(header)

                for line in lines:
                    match = re.match(r'(.*)\s+@\s+(.*?)(\s+\(Fee:\s*\$(.*?)\))?$', line)
                    if match:
                        action_plate_part, time_part, _, fee_part = match.groups()
                        action_plate_split = action_plate_part.split(None, 1)

                        action = action_plate_split[0] if len(action_plate_split) > 0 else ""
                        plate = action_plate_split[1] if len(action_plate_split) > 1 else action_plate_part
                        row = [action.strip(), plate.strip(), time_part.strip()]
                        if section == "Exit":
                            row.append(fee_part.strip() if fee_part else "")
                        w.writerow(row)
                    else:
                        print(f"Skipping malformed log line during export: {line}")

            messagebox.showinfo("Export","Logs exported successfully!")

        except Exception as e:
            messagebox.showerror("Export Error", f"An error occurred during export: {e}")
            print(f"Export error: {e}")

if __name__=="__main__":
    root = tk.Tk()
    app = ParkingApp(root)

    def on_closing():
        print("Closing application...")
        for tab in (app.entry_tab, app.exit_tab):
             if tab and hasattr(tab, 'stop_camera'):
                 try:
                    tab.stop_camera()
                 except Exception as e:
                     print(f"Error stopping camera during shutdown: {e}")

        global client
        if client:
            try:
                client.close()
                print("MongoDB connection closed.")
            except Exception as e:
                 print(f"Error closing MongoDB connection: {e}")
            client = None

        root.destroy()
        print("Application closed.")

    root.protocol("WM_DELETE_WINDOW", on_closing)
    root.mainloop()