import os
import io
import uuid
import re
import csv
import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext, filedialog, simpledialog
from PIL import Image, ImageTk, UnidentifiedImageError
import cv2
from datetime import datetime, timedelta
import math
from pymongo import MongoClient
from bson import ObjectId
from google.cloud import vision
import sys
import pymongo
import time
import traceback
import configparser # For reading config file

# ---- CONFIGURATION ----
CONFIG_FILE = "config.ini"
config = configparser.ConfigParser()

# --- Read Configuration ---
if not os.path.exists(CONFIG_FILE):
    # Create a default config file if it doesn't exist
    print(f"Configuration file '{CONFIG_FILE}' not found. Creating a default one.")
    config['Database'] = {
        'mongodb_uri': 'YOUR_MONGODB_SRV_URI_HERE',
        'database_name': 'caspiandb',
        'parking_collection': 'parking',
        'property_collection': 'property'
    }
    config['Paths'] = {
        'assets_dir': 'assets',
        'service_account_json': 'service_account.json'
    }
    try:
        with open(CONFIG_FILE, 'w') as configfile:
            config.write(configfile)
        messagebox.showerror("Configuration Needed", f"Configuration file '{CONFIG_FILE}' created.\nPlease edit it with your actual MongoDB URI and Service Account path.")
        sys.exit(1)
    except IOError as e:
         messagebox.showerror("Config Error", f"Could not create config file '{CONFIG_FILE}': {e}")
         sys.exit(1)
else:
    try:
        config.read(CONFIG_FILE)
        # Validate essential sections/keys
        if not config.has_section('Database') or not config.has_section('Paths'):
             raise ValueError("Config file missing required sections ([Database], [Paths]).")
        if not config.has_option('Database', 'mongodb_uri') or not config.has_option('Paths', 'service_account_json'):
             raise ValueError("Config file missing required options (mongodb_uri, service_account_json).")
    except Exception as e:
        messagebox.showerror("Config Error", f"Error reading configuration file '{CONFIG_FILE}':\n{e}")
        sys.exit(1)


# --- Get values from config ---
try:
    SERVICE_ACCOUNT_PATH = config.get('Paths', 'service_account_json')
    ASSETS_DIR = config.get('Paths', 'assets_dir', fallback='assets') # Fallback if not specified
    MONGODB_URI = config.get('Database', 'mongodb_uri')
    DB_NAME = config.get('Database', 'database_name', fallback='caspiandb')
    PARKING_COL_NAME = config.get('Database', 'parking_collection', fallback='parking')
    PROPERTY_COL_NAME = config.get('Database', 'property_collection', fallback='property')
except configparser.NoOptionError as e:
     messagebox.showerror("Config Error", f"Missing required option in '{CONFIG_FILE}': {e}")
     sys.exit(1)
except Exception as e:
     messagebox.showerror("Config Error", f"Unexpected error reading config: {e}")
     sys.exit(1)


# Check service account file before proceeding
if not os.path.exists(SERVICE_ACCOUNT_PATH):
    root_check = tk.Tk(); root_check.withdraw()
    messagebox.showerror("Configuration Error", f"Service account JSON not found at:\n{os.path.abspath(SERVICE_ACCOUNT_PATH)}\n\nPlease check the path in '{CONFIG_FILE}'.", parent=None)
    root_check.destroy(); sys.exit(1)

os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = SERVICE_ACCOUNT_PATH

# MongoDB Connection
client = None; db = None; parking_col = None; property_col = None
try:
    print(f"Connecting to MongoDB...")
    client = MongoClient(MONGODB_URI, serverSelectionTimeoutMS=5000)
    client.admin.command('ismaster') # Check connection
    db = client[DB_NAME]
    parking_col  = db[PARKING_COL_NAME]
    property_col = db[PROPERTY_COL_NAME]
    print(f"MongoDB connection successful to database '{DB_NAME}'.")
    # Check if collections exist (optional)
    # print(f"Collections found: {db.list_collection_names()}")
except Exception as e:
    root_check = tk.Tk(); root_check.withdraw()
    messagebox.showerror("Database Error", f"Could not connect to MongoDB (check URI in config.ini):\n{e}", parent=None)
    root_check.destroy(); sys.exit(1)

def find_cameras(max_index=5):
    """Finds available camera indexes by attempting to open and read a frame."""
    cams = []
    print("Detecting cameras...")
    original_stderr = os.dup(sys.stderr.fileno())
    devnull = os.open(os.devnull, os.O_WRONLY)
    os.dup2(devnull, sys.stderr.fileno())
    os.close(devnull)
    try:
        for i in range(max_index):
            cap_api = cv2.CAP_DSHOW if sys.platform == 'win32' else cv2.CAP_ANY
            cap = cv2.VideoCapture(i, cap_api)
            if cap.isOpened():
                try:
                    ret, frame = cap.read()
                    if ret and frame is not None: cams.append(i); print(f"  [OK] Found: {i}")
                    # else: print(f"  [WARN] Index {i} opened but failed read.")
                except Exception as e_read: print(f"  [ERROR] reading index {i}: {e_read}")
                finally: cap.release()
    finally:
        os.dup2(original_stderr, sys.stderr.fileno()); os.close(original_stderr)
    print(f"Cameras found: {cams}")
    return cams

CAMERA_INDEXES = find_cameras(5)

# --- Google Cloud Vision OCR ---
def detect_text(image_path):
    """Detects text (potential number plate) in an image, handling standard Indian and BH series formats."""
    try:
        v_client = vision.ImageAnnotatorClient()
    except Exception as e:
        print(f"[ERROR] Initializing Vision Client: {e}")
        return f"OCR Failed: Vision Client Init Error - {e}"

    try:
        if not os.path.exists(image_path):
             raise FileNotFoundError(f"Image file not found at path: {image_path}")

        with io.open(image_path, 'rb') as f: content = f.read()
        image = vision.Image(content=content)
        image_context = vision.ImageContext(language_hints=["en"])
        response = v_client.text_detection(image=image, image_context=image_context)
        texts = response.text_annotations

        if response.error.message: raise Exception(f"Vision API Error: {response.error.message}")
        if not texts: print("[INFO] Vision API found no text."); return ""

        print(f"[INFO] Vision API returned {len(texts)} text blocks.")
        possible_plates = []
        # Check individual blocks first (more reliable)
        for i, text in enumerate(texts):
             if i == 0: continue # Skip full text block initially
             block_text = text.description.upper()
             compact_raw = re.sub(r'[^A-Z0-9]', '', block_text)
             if not compact_raw: continue

             # --- Regex Matching on the compact block text ---
             bh_match = re.search(r'^(\d{2})(BH)(\d{4})([A-Z]{1,2})$', compact_raw)
             if bh_match:
                 year, bh_marker, nums, letters = bh_match.groups()
                 formatted_plate = f"{year}-{bh_marker}-{nums}-{letters}"
                 print(f"[INFO] Found BH plate in block {i}: {formatted_plate}")
                 possible_plates.append(formatted_plate); continue

             standard_match = re.search(r'^([A-Z]{2})(\d{1,2})([A-Z]{1,2})?(\d{3,4})$', compact_raw)
             if standard_match:
                 state, rto, letters, nums = standard_match.groups()
                 rto_padded = rto.rjust(2, '0'); nums_padded = nums.rjust(4, '0')
                 letters_formatted = letters if letters else 'XX'
                 formatted_plate = f"{state}-{rto_padded}-{letters_formatted}-{nums_padded}"
                 print(f"[INFO] Found Standard plate in block {i}: {formatted_plate}")
                 possible_plates.append(formatted_plate); continue

             if 6 <= len(compact_raw) <= 10 and re.search(r'\d', compact_raw) and re.search(r'[A-Z]', compact_raw):
                 print(f"[INFO] Found potential fallback plate in block {i}: {compact_raw}")
                 possible_plates.append(compact_raw)

        # --- Select the best candidate ---
        if possible_plates:
             # Simple: Return the first formatted plate, else first fallback
             formatted = [p for p in possible_plates if '-' in p]
             best_plate = formatted[0] if formatted else possible_plates[0]
             print(f"[INFO] Selecting best plate from candidates: {possible_plates} -> {best_plate}")
             return best_plate
        else:
             print("[WARN] No blocks matched expected formats. Checking full text as last resort.")
             # Last resort: Check the full text block (texts[0])
             full_text_raw = texts[0].description.upper()
             full_compact_raw = re.sub(r'[^A-Z0-9]', '', full_text_raw)
             bh_match = re.search(r'(\d{2})(BH)(\d{4})([A-Z]{1,2})', full_compact_raw)
             if bh_match:
                 year, bh_marker, nums, letters = bh_match.groups()
                 formatted_plate = f"{year}-{bh_marker}-{nums}-{letters}"
                 print(f"[INFO] Found BH plate in full text (last resort): {formatted_plate}")
                 return formatted_plate
             standard_match = re.search(r'([A-Z]{2})(\d{1,2})([A-Z]{1,2})?(\d{3,4})', full_compact_raw)
             if standard_match:
                 state, rto, letters, nums = standard_match.groups()
                 rto_padded = rto.rjust(2, '0'); nums_padded = nums.rjust(4, '0')
                 letters_formatted = letters if letters else 'XX'
                 formatted_plate = f"{state}-{rto_padded}-{letters_formatted}-{nums_padded}"
                 print(f"[INFO] Found Standard plate in full text (last resort): {formatted_plate}")
                 return formatted_plate
             print("[WARN] No plate format found even in full text.")
             return "" # Truly nothing found

    except vision.exceptions.GoogleCloudError as e: print(f"[ERROR] Vision API Error: {e}"); return f"OCR Failed: API Error - {e}"
    except FileNotFoundError as e: print(f"[ERROR] {e}"); return f"OCR Failed: File not found"
    except Exception as e: print(f"[ERROR] Error during text detection: {e}"); traceback.print_exc(); return f"OCR Failed: {e}"


# --- Editable Dialog for Plate Correction ---
class EditableDialog(tk.Toplevel):
    """Dialog for confirming or correcting the detected license plate."""
    def __init__(self, master, img_path, plate, on_confirm, on_retake):
        super().__init__(master)
        self.title("Confirm/Edit Number Plate")
        self.on_confirm, self.on_retake = on_confirm, on_retake
        self.transient(master); self.grab_set()
        self.img_path = img_path; self.result_plate = None

        img_loaded = False
        try:
            if img_path and os.path.exists(img_path):
                img = Image.open(img_path); img.thumbnail((400,300), Image.Resampling.LANCZOS)
                self.photo = ImageTk.PhotoImage(img); tk.Label(self, image=self.photo).pack(padx=10,pady=10)
                img_loaded = True
            elif img_path: tk.Label(self, text=f"Image not found:\n{img_path}", fg="orange").pack(padx=10,pady=10)
        except UnidentifiedImageError: tk.Label(self, text=f"Error: Cannot identify image file\n{img_path}", fg="red").pack(padx=10,pady=10)
        except Exception as e: tk.Label(self, text=f"Unexpected error loading image: {e}", fg="red").pack(padx=10,pady=10); print(f"[ERROR] Loading image in dialog: {e}")
        if not img_loaded: self.photo = None

        tk.Label(self, text="Detected/Enter Plate:", font=('Segoe UI',12)).pack(pady=(5,5))
        self.plate_var = tk.StringVar()
        initial_plate = plate if not (plate.startswith("OCR Failed") or not plate) else ""
        self.plate_var.set(initial_plate)
        self.entry = ttk.Entry(self, textvariable=self.plate_var, font=('Segoe UI',14,'bold'), justify='center', width=20)
        self.entry.pack(pady=(0,10), padx=10); self.entry.focus_set(); self.entry.selection_range(0, tk.END)

        btn_frame = ttk.Frame(self); btn_frame.pack(pady=10, padx=10, fill='x', expand=True)
        btn_frame.columnconfigure(0, weight=1); btn_frame.columnconfigure(1, weight=1)
        confirm_btn = ttk.Button(btn_frame, text="✅ Confirm", command=self._confirm, style="Accent.TButton"); confirm_btn.grid(row=0, column=0, padx=5, sticky='ew')
        retake_btn_text = "🔄 Retake" if img_path else "❌ Cancel"
        retake_btn = ttk.Button(btn_frame, text=retake_btn_text,  command=self._retake); retake_btn.grid(row=0, column=1, padx=5, sticky='ew')

        self.protocol("WM_DELETE_WINDOW", self._retake); self.bind("<Return>", self._confirm); self.bind("<Escape>", self._retake)
        self.bind("<Destroy>", self._handle_destroy)
        self.update_idletasks()
        master_x=master.winfo_rootx(); master_y=master.winfo_rooty(); master_w=master.winfo_width(); master_h=master.winfo_height()
        dialog_w=self.winfo_width(); dialog_h=self.winfo_height(); x=master_x+(master_w-dialog_w)//2; y=master_y+(master_h-dialog_h)//2
        self.geometry(f"+{x}+{y}")

    def _handle_destroy(self, event):
        if event.widget == self:
            if self.img_path and os.path.exists(self.img_path):
                try: os.remove(self.img_path); print(f"[INFO] Deleted temp image: {self.img_path}")
                except Exception as e: print(f"[ERROR] Deleting temp image {self.img_path}: {e}")
            if self.result_plate:
                 if callable(self.on_confirm): self.on_confirm(self.result_plate)
            else:
                 if callable(self.on_retake): self.on_retake()

    def _validate_plate(self, plate_str):
        if not plate_str: messagebox.showwarning("Input Required", "Please enter a number plate.", parent=self); return False
        if not re.fullmatch(r'[A-Z0-9\-]{6,13}', plate_str):
             messagebox.showwarning("Invalid Format", "Plate should contain 6-13 letters (A-Z), numbers (0-9), and hyphens (-).\nExample: MH-01-XX-1234 or 24-BH-1234-AA", parent=self); return False
        return True

    def _confirm(self, event=None):
        plate = self.plate_var.get().strip().upper()
        if self._validate_plate(plate): self.result_plate = plate; self.destroy()

    def _retake(self, event=None): self.result_plate = None; self.destroy()


# --- Main Application Class ---
class ParkingApp:
    def __init__(self, root):
        self.root = root
        root.title("🚗 Parking Management System")
        root.minsize(1100, 700) # Increased min size
        root.geometry("1200x750")
        root.configure(bg="#f0f0f0")
        self._make_styles()

        # --- Main Structure ---
        self.nav = ttk.Notebook(root)
        self.entry_tab = ttk.Frame(self.nav, padding=10)
        self.exit_tab  = ttk.Frame(self.nav, padding=10)
        self.settings_tab = ttk.Frame(self.nav, padding=10) # New Settings Tab

        self.nav.add(self.entry_tab, text="🚙 Entry")
        self.nav.add(self.exit_tab, text="🏁 Exit")
        self.nav.add(self.settings_tab, text="⚙️ Settings") # Add Settings Tab

        self.nav.pack(fill="both", expand=True, padx=5, pady=5)

        # Build UI for each tab
        self._build_tab(self.entry_tab, True)
        self._build_tab(self.exit_tab, False)
        self._build_settings_tab(self.settings_tab) # Build Settings Tab UI

        # Bind events
        self.nav.bind("<<NotebookTabChanged>>", self._on_tab_change)
        self.root.bind('<Return>', self._on_enter_press)

        # Start camera for the initially selected tab
        self.root.after(150, self._trigger_initial_camera_start)

    def _on_enter_press(self, event):
        """Handles the Enter key press to trigger capture on the current tab if button enabled."""
        focused_widget = self.root.focus_get()
        if isinstance(focused_widget, (tk.Entry, ttk.Entry, scrolledtext.ScrolledText, ttk.Combobox)): return

        try:
            current_tab_name = self.nav.select()
            if not current_tab_name: return
            current_tab_widget = self.nav.nametowidget(current_tab_name)
            # Only trigger capture if on Entry or Exit tab
            if current_tab_widget in (self.entry_tab, self.exit_tab):
                if hasattr(current_tab_widget, 'trigger_capture') and callable(getattr(current_tab_widget, 'trigger_capture')):
                    if hasattr(current_tab_widget, '_btn_capture') and current_tab_widget._btn_capture['state'] == tk.NORMAL:
                        current_tab_widget.trigger_capture()
        except tk.TclError: print("[WARN] Error getting current tab widget on Enter.")
        except Exception as e: print(f"[ERROR] During Enter press handling: {e}")

    def _on_tab_change(self, event):
        """Handles tab changes by stopping/starting cameras and refreshing settings."""
        newly_selected_tab_widget = None
        try:
             newly_selected_tab_name = self.nav.select()
             if newly_selected_tab_name:
                  newly_selected_tab_widget = self.nav.nametowidget(newly_selected_tab_name)
        except tk.TclError:
             print("[WARN] Error getting newly selected tab widget.")
             return # Cannot proceed without the widget

        # Stop camera on all *other* tabs
        for tab in (self.entry_tab, self.exit_tab, self.settings_tab):
             # Ensure tab exists and is not the newly selected one
             if tab and tab != newly_selected_tab_widget:
                  if hasattr(tab, 'stop_camera') and callable(getattr(tab, 'stop_camera')):
                      try: tab.stop_camera()
                      except Exception as e: print(f"[ERROR] Stopping camera on non-active tab change: {e}")

        # Start camera only if Entry or Exit tab is selected
        if newly_selected_tab_widget in (self.entry_tab, self.exit_tab):
            if hasattr(newly_selected_tab_widget,'start_camera') and callable(getattr(newly_selected_tab_widget, 'start_camera')):
                try: newly_selected_tab_widget.start_camera()
                except Exception as e: print(f"[ERROR] Starting camera on tab change: {e}")
        # Refresh settings tab if selected
        elif newly_selected_tab_widget == self.settings_tab:
             if hasattr(self.settings_tab, '_load_properties_into_list'):
                  self.settings_tab._load_properties_into_list()


    def _trigger_initial_camera_start(self):
        """Trigger the start_camera for the initially selected tab."""
        try:
            current_tab_name = self.nav.select()
            if not current_tab_name: return
            current_tab_widget = self.nav.nametowidget(current_tab_name)
            # Only start if it's an entry/exit tab
            if current_tab_widget in (self.entry_tab, self.exit_tab):
                if hasattr(current_tab_widget, 'start_camera') and callable(getattr(current_tab_widget, 'start_camera')):
                    current_tab_widget.start_camera()
        except tk.TclError: print("[WARN] Error getting initial tab widget.")
        except Exception as e:
            print(f"[ERROR] Starting camera for initial tab: {e}")
            if hasattr(current_tab_widget, '_canvas'):
                current_tab_widget._canvas.config(text=f"Cam Start Error", image=''); current_tab_widget._canvas.imgtk = None

    def _make_styles(self):
        """Configures ttk styles for a more modern look."""
        s = ttk.Style()
        s.theme_use('clam')
        s.configure(".", font=('Segoe UI', 10), background="#f0f0f0")
        s.configure("TLabel", background="#f0f0f0", foreground="#333")
        s.configure("Header.TLabel", font=('Segoe UI', 12, 'bold'), background="#f0f0f0")
        s.configure("TEntry", fieldbackground="white", foreground="#333")
        s.configure("TCombobox", fieldbackground="white", foreground="#333")
        s.map("TCombobox", fieldbackground=[('readonly','white')])
        s.configure("TRadiobutton", background="#f0f0f0", font=('Segoe UI', 10))
        s.configure("TNotebook", background="#e1e1e1", borderwidth=0)
        s.configure("TNotebook.Tab", padding=[12, 8], font=('Segoe UI', 11, 'bold'), background="#d0d0d0", foreground="#444")
        s.map("TNotebook.Tab", background=[("selected", "#f0f0f0"), ("active", "#e8e8e8")], foreground=[("selected", "#0078d4"), ("active", "#333")])
        s.configure("TButton", font=('Segoe UI', 10, 'bold'), padding=(10, 6), background="#e1e1e1", foreground="#333", borderwidth=1, relief="raised")
        s.map("TButton", background=[('active', '#c0c0c0'), ('disabled', '#d9d9d9')], foreground=[('disabled', '#a3a3a3')])
        s.configure("Accent.TButton", font=('Segoe UI', 11, 'bold'), background="#0078d4", foreground="white")
        s.map("Accent.TButton", background=[('active', '#005a9e'), ('disabled', '#b0b0b0')], foreground=[('disabled', '#f0f0f0')])
        s.configure("Manual.TButton", font=('Segoe UI', 10), padding=(8, 5), background="#f0ad4e", foreground="white")
        s.map("Manual.TButton", background=[('active', '#ec971f'), ('disabled', '#d9d9d9')], foreground=[('disabled', '#a3a3a3')])
        s.configure("VideoCanvas.TLabel", background="black", foreground="white", font=('Segoe UI', 14), anchor="center")
        # Style for Treeview (used in Settings)
        s.configure("Treeview", rowheight=25, fieldbackground="white")
        s.configure("Treeview.Heading", font=('Segoe UI', 10,'bold'))
        s.map("Treeview", background=[('selected', '#0078d4')], foreground=[('selected', 'white')])


    def _build_tab(self, frame, is_entry):
        """Builds the UI elements for Entry or Exit tabs."""
        frame.columnconfigure(0, weight=2); frame.columnconfigure(1, weight=1)
        frame.rowconfigure(0, weight=1)
        section = "Entry" if is_entry else "Exit"

        # --- Left Side ---
        left_frame = ttk.Frame(frame, padding=5); left_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 5))
        left_frame.columnconfigure(0, weight=1)
        left_frame.rowconfigure(0, weight=0); left_frame.rowconfigure(1, weight=0); left_frame.rowconfigure(2, weight=0);
        left_frame.rowconfigure(3, weight=0) # Vehicle Type Row
        left_frame.rowconfigure(4, weight=1) # Canvas row - Expands
        left_frame.rowconfigure(5, weight=0) # Button row

        # --- Property Selection ---
        prop_frame = ttk.Frame(left_frame); prop_frame.grid(row=0, column=0, sticky="ew", pady=(0, 5))
        ttk.Label(prop_frame, text="Property:", width=8).pack(side="left", padx=(0, 5))
        prop_var = tk.StringVar()
        try:
            props = list(property_col.find({}, {"name": 1}).sort("name", 1)); names = [p['name'] for p in props if 'name' in p]
        except Exception as e: print(f"[ERROR] Fetching properties: {e}"); names = []; messagebox.showerror("DB Error", f"Could not fetch properties: {e}")
        cbp = ttk.Combobox(prop_frame, textvariable=prop_var, values=names, state="readonly", width=30)
        property_available = bool(names)
        if property_available: cbp.current(0)
        else: cbp.set("No Properties Found"); cbp.config(state="disabled")
        cbp.pack(side="left", fill="x", expand=True)

        # --- Available Slots Display ---
        slots_lbl = ttk.Label(left_frame, text="Slots: N/A", font=('Segoe UI', 10)); slots_lbl.grid(row=1, column=0, sticky="w", pady=2, padx=5)

        # --- Vehicle Type Selection ---
        type_frame = ttk.Frame(left_frame); type_frame.grid(row=2, column=0, sticky="ew", pady=2)
        ttk.Label(type_frame, text="Type:", width=8).pack(side="left", padx=(0, 5))
        vehicle_type_var = tk.StringVar(value="Car") # Default to Car
        ttk.Radiobutton(type_frame, text="Car", variable=vehicle_type_var, value="Car").pack(side="left", padx=2)
        ttk.Radiobutton(type_frame, text="Bike", variable=vehicle_type_var, value="Bike").pack(side="left", padx=2)
        # Add more types (Truck, etc.) if needed

        # --- Camera Selection ---
        cam_frame = ttk.Frame(left_frame); cam_frame.grid(row=3, column=0, sticky="ew", pady=2)
        ttk.Label(cam_frame, text="Camera:", width=8).pack(side="left", padx=(0, 5))
        cam_var = tk.IntVar()
        cam_values = CAMERA_INDEXES if CAMERA_INDEXES else ["N/A"]; cam_state = "readonly" if CAMERA_INDEXES else "disabled"
        cbcam = ttk.Combobox(cam_frame, textvariable=cam_var, values=cam_values, state=cam_state, width=5)
        if CAMERA_INDEXES: cam_var.set(CAMERA_INDEXES[0])
        else: cam_var.set(-1)
        cbcam.pack(side="left")

        # --- Video Canvas ---
        canvas = ttk.Label(left_frame, text="Initializing camera...", style="VideoCanvas.TLabel"); canvas.grid(row=4, column=0, sticky="nsew", pady=5, padx=5)

        # --- Button Row ---
        button_row_frame = ttk.Frame(left_frame); button_row_frame.grid(row=5, column=0, pady=10)
        btn_capture = ttk.Button(button_row_frame, text="📸 Capture & Process", style="Accent.TButton"); btn_capture.pack(side="left", padx=(0, 10))
        btn_manual = ttk.Button(button_row_frame, text="⌨️ Manual " + section, style="Manual.TButton"); btn_manual.pack(side="left")

        # Initial button states
        if not property_available or not CAMERA_INDEXES: btn_capture.config(state="disabled")
        if not property_available: btn_manual.config(state="disabled")
        if not property_available and not CAMERA_INDEXES: btn_capture.config(text="🚫 Setup Required")
        elif not property_available: btn_capture.config(text="🚫 Add Property First")
        elif not CAMERA_INDEXES: btn_capture.config(text="🚫 No Camera Found")

        frame._btn_capture = btn_capture; frame._btn_manual = btn_manual

        # --- Right Side Frame (Logs) ---
        right_frame = ttk.Frame(frame, padding=5); right_frame.grid(row=0, column=1, sticky="nsew", padx=(5, 0))
        right_frame.columnconfigure(0, weight=1); right_frame.rowconfigure(1, weight=1)
        log_ctrl_frame = ttk.Frame(right_frame); log_ctrl_frame.grid(row=0, column=0, sticky="ew", pady=(0, 5))
        clear_btn = ttk.Button(log_ctrl_frame, text="🗑 Clear Logs", width=12); clear_btn.pack(side="left", padx=(0, 5))
        export_btn = ttk.Button(log_ctrl_frame, text="⬇️ Export CSV", width=12); export_btn.pack(side="left")
        log = scrolledtext.ScrolledText(right_frame, width=50, height=15, font=("Consolas", 10), wrap=tk.WORD, bg="#ffffff", fg="#333333", relief="solid", borderwidth=1, state=tk.DISABLED)
        log.grid(row=1, column=0, sticky="nsew")

        # --- Log Utility Function ---
        def append_log(message, level="INFO"):
            timestamp = datetime.now().strftime("%H:%M:%S"); prefix_map = {"INFO": "[INFO]", "WARN": "[WARN]", "ERROR": "[ERROR]", "SAVE": "[SAVE]", "OCR": "[OCR]"}
            prefix = prefix_map.get(level.upper(), "[INFO]"); full_message = f"{timestamp} {prefix} {message}\n"
            try: log.config(state=tk.NORMAL); log.insert(tk.END, full_message); log.see(tk.END); log.config(state=tk.DISABLED)
            except tk.TclError as e: print(f"[WARN] Error appending to log: {e}")
            except Exception as e: print(f"[ERROR] Unexpected error appending log: {e}")

        # --- Log Control Commands ---
        def clear_log_action():
            if messagebox.askyesno("Confirm Clear", "Clear the log display?", parent=self.root):
                log.config(state=tk.NORMAL); log.delete('1.0', tk.END); log.insert("end", f"📄 {section} Log History (Cleared):\n" + "="*25 + "\n"); log.config(state=tk.DISABLED)
        clear_btn.config(command=clear_log_action)
        export_btn.config(command=lambda: self._export(log, section))

        # --- Refresh Slots Function (Updated for Vehicle Types) ---
        def refresh_slots_typed(*args):
            selected_prop_name = prop_var.get()
            v_type = vehicle_type_var.get().lower() # e.g., 'car', 'bike'
            if selected_prop_name and selected_prop_name != "No Properties Found":
                try:
                    # Fetch fields specific to vehicle types
                    projection = {f"available_parking_spaces_{v_type}": 1, f"parking_spaces_{v_type}": 1}
                    doc = property_col.find_one({"name": selected_prop_name}, projection)
                    if doc:
                        avail = doc.get(f'available_parking_spaces_{v_type}', 'N/A')
                        total = doc.get(f'parking_spaces_{v_type}', 'N/A')
                        slots_lbl.config(text=f"Slots ({v_type.capitalize()}): {avail} / {total}")
                    else: slots_lbl.config(text=f"Slots ({v_type.capitalize()}): Error")
                except pymongo.errors.ConnectionFailure: slots_lbl.config(text="Slots: DB Error"); print(f"[ERROR] DB connection error.")
                except Exception as e: slots_lbl.config(text="Slots: Error"); print(f"[ERROR] DB error refreshing slots: {e}")
            else: slots_lbl.config(text="Slots: N/A")

        prop_var.trace_add('write', refresh_slots_typed)
        vehicle_type_var.trace_add('write', refresh_slots_typed) # Refresh when type changes too
        if property_available: refresh_slots_typed() # Initial call

        # --- Store references and state ---
        frame._state = {'cap': None, 'frame': None, 'after_id': None}
        frame._log_widget = log; frame._append_log = append_log; frame._canvas = canvas
        frame._prop_var = prop_var; frame._refresh_slots = refresh_slots_typed # Use typed refresh
        frame._vehicle_type_var = vehicle_type_var # Store vehicle type var

        # --- Camera Handling Functions (mostly unchanged, check button enabling) ---
        def update_feed():
            cap = frame._state.get('cap')
            if cap is None or not cap.isOpened():
                if frame._state.get('after_id') is not None: frame._canvas.after_cancel(frame._state['after_id']); frame._state['after_id'] = None
                return
            try:
                ok, frm = cap.read()
                if ok and frm is not None:
                    frame._state['frame'] = frm; img_rgb = cv2.cvtColor(frm, cv2.COLOR_BGR2RGB); img_pil = Image.fromarray(img_rgb)
                    canvas_w = frame._canvas.winfo_width(); canvas_h = frame._canvas.winfo_height()
                    if canvas_w <= 1 or canvas_h <= 1: frame._state['after_id'] = frame._canvas.after(100, update_feed); return
                    img_pil.thumbnail((canvas_w, canvas_h), Image.Resampling.LANCZOS); photo = ImageTk.PhotoImage(img_pil)
                    frame._canvas.imgtk = photo; frame._canvas.config(image=photo, text="")
            except Exception as e:
                print(f"[ERROR] in update_feed cam {cam_var.get()}: {e}"); stop_camera(); frame._canvas.config(image='', text=f"Feed Error"); frame._canvas.imgtk = None; return
            frame._state['after_id'] = frame._canvas.after(40, update_feed)

        def start_camera(event=None):
            stop_camera()
            selected_cam_index = cam_var.get()
            if selected_cam_index == -1 or not isinstance(selected_cam_index, int):
                 frame._canvas.config(text="No Camera Selected", image=''); frame._canvas.imgtk = None
                 btn_capture.config(state="disabled", text="🚫 Select Camera"); return

            append_log(f"Initializing camera {selected_cam_index}...")
            frame._canvas.config(text=f"Starting Cam {selected_cam_index}...", image=''); frame._canvas.imgtk = None
            self.root.update_idletasks()
            cap_api = cv2.CAP_DSHOW if sys.platform == 'win32' else cv2.CAP_ANY
            cap = cv2.VideoCapture(selected_cam_index, cap_api); time.sleep(0.5)

            if not cap.isOpened():
                messagebox.showerror("Camera Error", f"Cannot open camera {selected_cam_index}", parent=self.root)
                append_log(f"Failed to open camera {selected_cam_index}", "ERROR")
                frame._state['cap'] = None; frame._canvas.config(text="Failed to Open", image=''); frame._canvas.imgtk = None
                btn_capture.config(state="disabled", text="🚫 Camera Error"); return

            read_success = False
            try:
                for _ in range(5):
                    ok, test_frame = cap.read(); time.sleep(0.05)
                    if ok and test_frame is not None:
                        read_success = True
                        break
                if not read_success: raise IOError("Failed initial reads.")
            except Exception as e:
                cap.release(); frame._state['cap'] = None
                messagebox.showerror("Camera Error", f"Error reading initial frames from camera {selected_cam_index}: {e}", parent=self.root)
                append_log(f"Failed initial read cam {selected_cam_index}: {e}", "ERROR")
                frame._canvas.config(text="Read Error", image=''); frame._canvas.imgtk = None
                btn_capture.config(state="disabled", text="🚫 Read Error"); return

            frame._state['cap'] = cap
            append_log(f"{section} Camera {selected_cam_index} started.", "INFO")
            frame._canvas.config(text="")
            # Enable buttons only if property is also selected
            if prop_var.get() and prop_var.get() != "No Properties Found":
                btn_capture.config(state="normal", text="📸 Capture & Process")
                btn_manual.config(state="normal")
            else:
                 btn_capture.config(state="disabled", text="🚫 Select Property")
                 btn_manual.config(state="disabled")
            update_feed()

        def stop_camera():
            if frame._state.get('after_id') is not None: frame._canvas.after_cancel(frame._state['after_id']); frame._state['after_id'] = None
            cap = frame._state.get('cap')
            if cap and cap.isOpened(): cap.release(); frame._state['cap'] = None
            frame._canvas.config(image='', text="Camera Stopped"); frame._canvas.imgtk = None
            if btn_capture['state'] == tk.NORMAL: btn_capture.config(state="disabled", text="🚫 Camera Stopped")
            # Keep manual button enabled if property is selected
            if prop_var.get() and prop_var.get() != "No Properties Found":
                 if btn_manual['state'] == tk.DISABLED: btn_manual.config(state="normal")
            else:
                 if btn_manual['state'] == tk.NORMAL: btn_manual.config(state="disabled")

        # --- Define and Attach Button Commands ---
        def trigger_capture_local():
             self._capture_and_edit(frame, is_entry, append_log, prop_var.get(), vehicle_type_var.get(), refresh_slots_typed, btn_capture, btn_manual)
        frame.trigger_capture = trigger_capture_local
        btn_capture.config(command=trigger_capture_local)

        def trigger_manual_local():
             self._manual_entry_exit(frame, is_entry, append_log, prop_var.get(), vehicle_type_var.get(), refresh_slots_typed, btn_capture, btn_manual)
        btn_manual.config(command=trigger_manual_local)

        frame.start_camera = start_camera; frame.stop_camera = stop_camera
        cbcam.bind("<<ComboboxSelected>>", start_camera)
        self._load_logs(frame._log_widget, frame._append_log, is_entry) # Initial log load

    # --- Build Settings Tab ---
    def _build_settings_tab(self, frame):
        """Builds the UI for the Settings tab."""
        frame.columnconfigure(0, weight=1)
        frame.columnconfigure(1, weight=2)
        frame.rowconfigure(1, weight=1)

        ttk.Label(frame, text="Property Management", style="Header.TLabel").grid(row=0, column=0, columnspan=2, pady=(0, 10), sticky="w")

        # --- Left Pane: Property List ---
        list_frame = ttk.LabelFrame(frame, text="Properties", padding=10)
        list_frame.grid(row=1, column=0, sticky="nsew", padx=(0, 10))
        list_frame.rowconfigure(0, weight=1)
        list_frame.columnconfigure(0, weight=1)

        # Use Treeview for a better list display
        cols = ("name", "cars", "bikes")
        frame._settings_tree = ttk.Treeview(list_frame, columns=cols, show='headings', selectmode='browse')
        frame._settings_tree.heading("name", text="Name")
        frame._settings_tree.heading("cars", text="Car Slots")
        frame._settings_tree.heading("bikes", text="Bike Slots")
        frame._settings_tree.column("name", width=150)
        frame._settings_tree.column("cars", width=80, anchor='center')
        frame._settings_tree.column("bikes", width=80, anchor='center')
        frame._settings_tree.grid(row=0, column=0, sticky="nsew")

        # Scrollbar for Treeview
        scrollbar = ttk.Scrollbar(list_frame, orient="vertical", command=frame._settings_tree.yview)
        frame._settings_tree.configure(yscrollcommand=scrollbar.set)
        scrollbar.grid(row=0, column=1, sticky="ns")

        # Add/Delete Buttons for Property List
        prop_button_frame = ttk.Frame(list_frame)
        prop_button_frame.grid(row=2, column=0, columnspan=2, pady=(10, 0), sticky='ew')
        ttk.Button(prop_button_frame, text="➕ Add New", command=lambda: self._add_edit_property(None)).pack(side="left", padx=5)
        # ttk.Button(prop_button_frame, text="🗑️ Delete Selected", command=self._delete_property).pack(side="left", padx=5) # Add later

        # --- Right Pane: Details & Fees ---
        details_frame = ttk.LabelFrame(frame, text="Details & Fees", padding=10)
        details_frame.grid(row=1, column=1, sticky="nsew")
        details_frame.columnconfigure(1, weight=1)

        # Labels and Entry fields for selected property details
        ttk.Label(details_frame, text="Property Name:").grid(row=0, column=0, sticky="w", padx=5, pady=3)
        frame._prop_name_var = tk.StringVar()
        ttk.Entry(details_frame, textvariable=frame._prop_name_var, state="readonly", width=30).grid(row=0, column=1, sticky="ew", padx=5, pady=3)

        ttk.Label(details_frame, text="Total Car Spaces:").grid(row=1, column=0, sticky="w", padx=5, pady=3)
        frame._prop_spaces_car_var = tk.StringVar()
        ttk.Entry(details_frame, textvariable=frame._prop_spaces_car_var, width=10).grid(row=1, column=1, sticky="w", padx=5, pady=3)

        ttk.Label(details_frame, text="Total Bike Spaces:").grid(row=2, column=0, sticky="w", padx=5, pady=3)
        frame._prop_spaces_bike_var = tk.StringVar()
        ttk.Entry(details_frame, textvariable=frame._prop_spaces_bike_var, width=10).grid(row=2, column=1, sticky="w", padx=5, pady=3)

        ttk.Separator(details_frame, orient='horizontal').grid(row=3, column=0, columnspan=2, sticky='ew', pady=10)

        ttk.Label(details_frame, text="Fee per Hour (Car): ₹").grid(row=4, column=0, sticky="w", padx=5, pady=3)
        frame._prop_fee_car_var = tk.StringVar()
        ttk.Entry(details_frame, textvariable=frame._prop_fee_car_var, width=10).grid(row=4, column=1, sticky="w", padx=5, pady=3)

        ttk.Label(details_frame, text="Fee per Hour (Bike): ₹").grid(row=5, column=0, sticky="w", padx=5, pady=3)
        frame._prop_fee_bike_var = tk.StringVar()
        ttk.Entry(details_frame, textvariable=frame._prop_fee_bike_var, width=10).grid(row=5, column=1, sticky="w", padx=5, pady=3)

        # Save Button for Details
        ttk.Button(details_frame, text="💾 Save Changes", command=self._save_property_details, style="Accent.TButton").grid(row=6, column=0, columnspan=2, pady=(15, 5))

        # --- Bind selection event ---
        frame._settings_tree.bind("<<TreeviewSelect>>", self._on_property_select)

        # Store references on the settings tab frame itself
        frame._load_properties_into_list = lambda: self._load_properties_into_list(frame._settings_tree) # Pass treeview

        # Initial load
        frame._load_properties_into_list()

    # --- Settings Tab Helper Functions ---
    def _load_properties_into_list(self, tree):
        """Clears and reloads the property list in the settings tab."""
        # Clear existing items
        for item in tree.get_children():
            tree.delete(item)
        # Fetch properties from DB
        try:
            properties = list(property_col.find({}, {"name": 1, "parking_spaces_car": 1, "parking_spaces_bike": 1, "_id": 1}).sort("name", 1))
            for prop in properties:
                # Insert into treeview: iid is the MongoDB ObjectId string
                tree.insert("", "end", iid=str(prop['_id']), values=(
                    prop.get("name", "N/A"),
                    prop.get("parking_spaces_car", 0),
                    prop.get("parking_spaces_bike", 0)
                ))
        except Exception as e:
            print(f"[ERROR] Loading properties into settings list: {e}")
            messagebox.showerror("DB Error", f"Failed to load properties: {e}", parent=self.root)

    def _on_property_select(self, event):
        """Handles selection change in the settings property list."""
        tree = event.widget
        selected_items = tree.selection() # Get selected item IDs (should be only one)
        if not selected_items: return # Nothing selected

        selected_iid = selected_items[0] # Get the first (and only) selected item's IID (which is the ObjectId string)

        try:
            prop_id = ObjectId(selected_iid) # Convert IID back to ObjectId
            prop_data = property_col.find_one({"_id": prop_id})

            if prop_data:
                # Populate the detail fields
                self.settings_tab._prop_name_var.set(prop_data.get("name", ""))
                self.settings_tab._prop_spaces_car_var.set(str(prop_data.get("parking_spaces_car", 0)))
                self.settings_tab._prop_spaces_bike_var.set(str(prop_data.get("parking_spaces_bike", 0)))
                self.settings_tab._prop_fee_car_var.set(str(prop_data.get("fee_per_hour_car", 0.0)))
                self.settings_tab._prop_fee_bike_var.set(str(prop_data.get("fee_per_hour_bike", 0.0)))
                # Store the current ObjectId for saving
                self.settings_tab._selected_prop_id = prop_id
            else:
                 print(f"[WARN] Property with ID {selected_iid} not found in DB.")
                 self._clear_property_details() # Clear fields if not found

        except Exception as e:
            print(f"[ERROR] Fetching property details for {selected_iid}: {e}")
            messagebox.showerror("DB Error", f"Failed to load property details: {e}", parent=self.root)
            self._clear_property_details()

    def _clear_property_details(self):
         """Clears the property detail fields in the settings tab."""
         self.settings_tab._prop_name_var.set("")
         self.settings_tab._prop_spaces_car_var.set("")
         self.settings_tab._prop_spaces_bike_var.set("")
         self.settings_tab._prop_fee_car_var.set("")
         self.settings_tab._prop_fee_bike_var.set("")
         self.settings_tab._selected_prop_id = None # Clear selected ID

    def _add_edit_property(self, prop_id=None):
        """Placeholder for Add/Edit Property Dialog (To be implemented)."""
        # This would typically open a new Toplevel window/dialog
        # For adding: prop_id is None
        # For editing: prop_id is the ObjectId of the property to edit
        action = "Edit" if prop_id else "Add"
        messagebox.showinfo("Not Implemented", f"{action} Property functionality is not yet implemented.", parent=self.root)
        # TODO: Implement a dialog window to get/edit property details
        # On successful save in the dialog, call:
        # self.settings_tab._load_properties_into_list()
        # self._clear_property_details()

    def _save_property_details(self):
        """Saves the edited details of the currently selected property."""
        if not hasattr(self.settings_tab, '_selected_prop_id') or not self.settings_tab._selected_prop_id:
             messagebox.showwarning("No Selection", "Please select a property from the list to save.", parent=self.root)
             return

        prop_id = self.settings_tab._selected_prop_id
        # --- Validate Inputs ---
        try:
            name = self.settings_tab._prop_name_var.get().strip()
            spaces_car = int(self.settings_tab._prop_spaces_car_var.get())
            spaces_bike = int(self.settings_tab._prop_spaces_bike_var.get())
            fee_car = float(self.settings_tab._prop_fee_car_var.get())
            fee_bike = float(self.settings_tab._prop_fee_bike_var.get())

            if not name: raise ValueError("Property name cannot be empty.")
            if spaces_car < 0 or spaces_bike < 0: raise ValueError("Parking spaces cannot be negative.")
            if fee_car < 0 or fee_bike < 0: raise ValueError("Fees cannot be negative.")

        except ValueError as e:
             messagebox.showerror("Invalid Input", f"Please check your inputs:\n{e}", parent=self.root)
             return
        except Exception as e:
             messagebox.showerror("Input Error", f"Unexpected error reading inputs: {e}", parent=self.root)
             return

        # --- Update Database ---
        try:
            update_data = {
                "$set": {
                    "name": name,
                    "parking_spaces_car": spaces_car,
                    "parking_spaces_bike": spaces_bike,
                    "fee_per_hour_car": fee_car,
                    "fee_per_hour_bike": fee_bike
                    # IMPORTANT: We are NOT updating available spaces here.
                    # Available spaces should only be changed by entry/exit events.
                }
            }
            result = property_col.update_one({"_id": prop_id}, update_data)

            if result.modified_count > 0:
                messagebox.showinfo("Success", f"Property '{name}' updated successfully.", parent=self.root)
                # Refresh the list to show updated values
                self.settings_tab._load_properties_into_list()
                # Keep the item selected and refresh details (optional, but good UX)
                # self.settings_tab._settings_tree.selection_set(str(prop_id)) # Re-select
                # self._on_property_select(MagicMock(widget=self.settings_tab._settings_tree)) # Simulate event
            elif result.matched_count > 0:
                 messagebox.showinfo("No Changes", "No changes were detected to save.", parent=self.root)
            else:
                 messagebox.showerror("Save Error", "Could not find the property to update.", parent=self.root)

        except Exception as e:
            messagebox.showerror("Database Error", f"Failed to save property details: {e}", parent=self.root)
            print(f"[ERROR] Saving property details: {e}")


    # --- Capture/Save Logic (Updated Signatures) ---
    def _capture_and_edit(self, tab_frame, is_entry, append_log_func, prop_name, vehicle_type, refresh_slots, btn_capture, btn_manual):
        """Captures frame, detects text, shows edit dialog, and saves on confirm."""
        if not prop_name or prop_name == "No Properties Found": messagebox.showwarning("Property Required", "Select property.", parent=self.root); return

        original_capture_text = btn_capture['text']
        btn_capture.config(state="disabled", text="⏳ Capturing..."); btn_manual.config(state="disabled")
        self.root.update_idletasks()

        cap = tab_frame._state.get('cap')
        if cap is None or not cap.isOpened():
            messagebox.showwarning("No Camera", "Camera not running.", parent=self.root)
            btn_capture.config(state="normal", text=original_capture_text); btn_manual.config(state="normal")
            if hasattr(tab_frame, 'start_camera'): tab_frame.start_camera(); return

        append_log_func("Capturing frame...", "INFO")
        captured_frame = tab_frame._state.get('frame')
        if captured_frame is None:
            append_log_func("No frame in state, reading...", "WARN")
            try:
                ok, captured_frame = cap.read()
                if not ok or captured_frame is None:
                    raise IOError("Final read failed.")
            except Exception as e:
                messagebox.showerror("Capture Error", f"Failed: {e}", parent=self.root)
                append_log_func(f"Capture error: {e}", "ERROR")
                btn_capture.config(state="normal", text=original_capture_text)
                btn_manual.config(state="normal")
                return

        path = None
        try:
            os.makedirs(ASSETS_DIR, exist_ok=True); timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            filename = f"capture_{timestamp}_{uuid.uuid4().hex[:6]}.jpg"; path = os.path.join(ASSETS_DIR, filename)
            success = cv2.imwrite(path, captured_frame, [cv2.IMWRITE_JPEG_QUALITY, 95])
            if not success: raise IOError(f"Save failed: {path}")
            append_log_func(f"Frame saved: {filename}", "INFO"); btn_capture.config(text="⏳ Detecting..."); self.root.update_idletasks()
        except Exception as e:
            messagebox.showerror("File Save Error", f"Save failed: {e}", parent=self.root); append_log_func(f"Image save error: {e}", "ERROR")
            btn_capture.config(state="normal", text=original_capture_text); btn_manual.config(state="normal"); return

        plate = detect_text(path)
        append_log_func(f"Result: '{plate}'" if plate else "Result: No plate detected", "OCR")

        confirm_callback = lambda edited_plate: (
            append_log_func(f"Confirmed Plate: {edited_plate}", "INFO"),
            btn_capture.config(text="⏳ Saving..."), self.root.update_idletasks(),
            self._save_record(edited_plate, is_entry, append_log_func, prop_name, vehicle_type, refresh_slots) # Pass vehicle_type
        )
        retake_callback = lambda: append_log_func("Retake requested.", "INFO")

        try:
            dialog = EditableDialog(self.root, path, plate, confirm_callback, retake_callback)
            dialog.bind("<Destroy>", lambda e, b_cap=btn_capture, b_man=btn_manual, txt=original_capture_text: (
                b_cap.config(state="normal", text=txt),
                b_man.config(state="normal" if tab_frame._prop_var.get() and tab_frame._prop_var.get() != "No Properties Found" else "disabled")
                ), add="+")
        except Exception as e:
             messagebox.showerror("Dialog Error", f"Failed: {e}", parent=self.root); append_log_func(f"Dialog error: {e}", "ERROR")
             btn_capture.config(state="normal", text=original_capture_text); btn_manual.config(state="normal")
             if path and os.path.exists(path):
                 try:
                     os.remove(path)
                 except Exception as del_e:
                     print(f"[ERROR] Cleanup {path}: {del_e}")

    def _manual_entry_exit(self, tab_frame, is_entry, append_log_func, prop_name, vehicle_type, refresh_slots, btn_capture, btn_manual):
        """Handles manual entry/exit without camera capture."""
        if not prop_name or prop_name == "No Properties Found": messagebox.showwarning("Property Required", "Select property.", parent=self.root); return

        section = "Entry" if is_entry else "Exit"
        plate = simpledialog.askstring("Manual Input", f"Enter Plate for Manual {section} ({vehicle_type}):", parent=self.root)
        if not plate: append_log_func("Manual input cancelled.", "INFO"); return
        plate = plate.strip().upper()

        if not re.fullmatch(r'[A-Z0-9\-]{6,13}', plate):
             messagebox.showwarning("Invalid Format", "Plate: 6-13 Alphanumeric/Hyphens.", parent=self.root); append_log_func(f"Manual validation failed: '{plate}'", "WARN"); return

        append_log_func(f"Manual Plate Entered: {plate} ({vehicle_type})", "INFO")
        original_manual_text = btn_manual['text']
        btn_manual.config(state="disabled", text="⏳ Saving..."); btn_capture.config(state="disabled"); self.root.update_idletasks()

        try: self._save_record(plate, is_entry, append_log_func, prop_name, vehicle_type, refresh_slots) # Pass vehicle_type
        finally:
             btn_manual.config(state="normal", text=original_manual_text)
             is_cam_running = tab_frame._state.get('cap') and tab_frame._state['cap'].isOpened()
             btn_capture.config(state="normal" if is_cam_running else "disabled", text="📸 Capture & Process" if is_cam_running else "🚫 Camera Stopped")

    def _save_record(self, plate, is_entry, append_log_func, prop_name, vehicle_type, refresh_slots_func):
        """Saves or updates parking record, handles vehicle types and fees."""
        now = datetime.now()
        v_type_lower = vehicle_type.lower() # e.g., 'car', 'bike'
        append_log_func(f"Saving {'entry' if is_entry else 'exit'} for {plate} ({vehicle_type})...", "SAVE")

        if not re.fullmatch(r'[A-Z0-9\-]+', plate):
             messagebox.showerror("Save Error", f"Invalid plate format '{plate}'.", parent=self.root); append_log_func(f"Save aborted: Invalid format '{plate}'.", "ERROR"); return

        try:
            prop = property_col.find_one({"name": prop_name})
            if not prop:
                messagebox.showerror("Property Error", f"Property '{prop_name}' not found.", parent=self.root); append_log_func(f"Property '{prop_name}' not found.", "ERROR"); return

            pid = prop['_id'] # Use ObjectId
            # Define keys based on vehicle type
            avail_space_key = f"available_parking_spaces_{v_type_lower}"
            fee_key = f"fee_per_hour_{v_type_lower}"

            if is_entry:
                existing_entry = parking_col.find_one({"vehicle_no": plate, "property_id": pid, "exit_time": None}) # Use ObjectId for query
                if existing_entry:
                    messagebox.showwarning("Duplicate Entry", f"{plate} already has active session.", parent=self.root); append_log_func(f"Duplicate entry: {plate}.", "WARN"); return

                # Check type-specific availability
                latest_prop = property_col.find_one({"_id": pid}, {avail_space_key: 1})
                if not latest_prop or latest_prop.get(avail_space_key, 0) <= 0:
                    messagebox.showwarning("Parking Full", f"No {vehicle_type} slots available.", parent=self.root); append_log_func(f"Parking full ({vehicle_type}) for {plate}.", "WARN"); return

                new_record = {
                    "parking_id": str(uuid.uuid4()), "property_id": pid, "vehicle_no": plate, # Store ObjectId
                    "vehicle_type": vehicle_type, # Store selected type
                    "entry_time": now, "exit_time": None, "fee": 0, "mode_of_payment": None
                }
                insert_result = parking_col.insert_one(new_record)
                # Decrement type-specific count
                update_result = property_col.update_one({"_id": pid}, {"$inc": {avail_space_key: -1}})

                if insert_result.inserted_id and update_result.modified_count > 0:
                    append_log_func(f"Entry: {plate} ({vehicle_type})", "SAVE"); messagebox.showinfo("Entry Success", f"{vehicle_type} {plate} entry recorded.", parent=self.root)
                else: append_log_func(f"Entry DB update issue: {plate}.", "WARN"); messagebox.showwarning("DB Warning", "Entry recorded, slot count update failed.", parent=self.root)

            else: # is_exit
                updated_doc = parking_col.find_one_and_update(
                    {"vehicle_no": plate, "exit_time": None, "property_id": pid}, # Use ObjectId
                    {"$set": {"exit_time": now}},
                    sort=[('entry_time', -1)], return_document=pymongo.ReturnDocument.AFTER
                )

                if updated_doc:
                    entry_time = updated_doc.get('entry_time'); calculated_fee = 0.0
                    # Get the vehicle type from the record being exited
                    exiting_vehicle_type = updated_doc.get('vehicle_type', 'Unknown') # Get from DB
                    exiting_v_type_lower = exiting_vehicle_type.lower()
                    exit_fee_key = f"fee_per_hour_{exiting_v_type_lower}" # Use fee key based on *exiting* vehicle type
                    exit_avail_space_key = f"available_parking_spaces_{exiting_v_type_lower}" # Use space key based on *exiting* vehicle type

                    if entry_time and isinstance(entry_time, datetime):
                        duration = now - entry_time; total_hours = duration.total_seconds() / 3600
                        # Use the correct fee key based on the record's vehicle type
                        fee_per_hour = prop.get(exit_fee_key, 10.0) # Default if key missing for that type
                        if not isinstance(fee_per_hour, (int, float)) or fee_per_hour < 0:
                             print(f"[WARN] Invalid {exit_fee_key} ({fee_per_hour}) for {prop_name}. Using 10.0.")
                             fee_per_hour = 10.0
                        if total_hours <= 1.0: calculated_fee = 0.0
                        else: chargeable_hours = math.ceil(total_hours) - 1; calculated_fee = chargeable_hours * fee_per_hour
                        calculated_fee = round(max(0.0, calculated_fee), 2)
                        parking_col.update_one({"_id": updated_doc["_id"]}, {"$set": {"fee": calculated_fee}})
                        append_log_func(f"Fee: ₹{calculated_fee:.2f} ({total_hours:.2f} hrs).", "INFO")
                    else: append_log_func(f"Could not calculate fee for {plate}: Invalid entry time.", "WARN"); messagebox.showwarning("Fee Warning", "Could not calculate fee.", parent=self.root)

                    # Increment the correct type-specific count
                    property_col.update_one({"_id": pid}, {"$inc": {exit_avail_space_key: 1}})
                    log_msg = f"Exit: {plate} ({exiting_vehicle_type}) Fee: ₹{calculated_fee:.2f}"
                    append_log_func(log_msg, "SAVE"); messagebox.showinfo("Exit Success", f"Exit recorded for {plate}.\nFee: ₹{calculated_fee:.2f}", parent=self.root)
                else:
                    messagebox.showwarning("No Entry Found", f"No active session found for {plate}.", parent=self.root); append_log_func(f"Exit failed: No open entry for {plate}.", "WARN")

            if callable(refresh_slots_func): refresh_slots_func()
            else: print("[WARN] refresh_slots_func not callable.")

        except pymongo.errors.ConnectionFailure as e: messagebox.showerror("Database Error", f"DB connection lost: {e}", parent=self.root); append_log_func(f"DB Connection Failure: {e}", "ERROR")
        except pymongo.errors.PyMongoError as e: messagebox.showerror("Database Error", f"DB error: {e}", parent=self.root); append_log_func(f"DB Error during save: {e}", "ERROR")
        except Exception as e: messagebox.showerror("Unexpected Error", f"Error saving: {e}", parent=self.root); append_log_func(f"Unexpected Save Error: {e}", "ERROR"); traceback.print_exc()
        finally:
            if callable(refresh_slots_func):
                try: refresh_slots_func()
                except Exception as refresh_e: print(f"[ERROR] final slot refresh: {refresh_e}")


    def _load_logs(self, log_widget, append_log_func, is_entry):
        """Loads recent parking records into the specified log display."""
        section = "Entry" if is_entry else "Exit"
        log_widget.config(state=tk.NORMAL); log_widget.delete('1.0', tk.END)
        log_widget.insert("end", f"📄 {section} Log History:\n" + "="*25 + "\n"); log_widget.config(state=tk.DISABLED)
        append_log_func(f"Loading recent {section.lower()} logs...", "INFO")
        try:
            query = {"exit_time": None} if is_entry else {"exit_time": {"$ne": None}}
            sort_key = "entry_time" if is_entry else "exit_time"
            recent_records = parking_col.find(query).sort(sort_key, pymongo.DESCENDING).limit(30)
            records_list = list(recent_records)
            if not records_list: append_log_func(f"No recent {section.lower()} records found.", "INFO")
            else:
                log_lines = []
                for record in records_list:
                    ts_key = "entry_time" if is_entry else "exit_time"; ts = record.get(ts_key)
                    if ts and isinstance(ts, datetime):
                        icon = "🟢" if is_entry else "🔴"; plate = record.get('vehicle_no', 'N/A'); v_type = record.get('vehicle_type', '')
                        time_str = ts.strftime('%Y-%m-%d %H:%M:%S'); type_str = f" ({v_type})" if v_type else ""
                        log_line = f"{icon} {plate:<15}{type_str:<7} @ {time_str}" # Add type, adjust padding
                        if not is_entry:
                            fee = record.get('fee', None); fee_str = f"₹{fee:.2f}" if isinstance(fee, (int, float)) else "N/A"
                            log_line += f" (Fee: {fee_str})"
                        log_lines.append(log_line + "\n")
                if log_lines:
                     log_widget.config(state=tk.NORMAL); log_widget.insert(tk.END, "".join(log_lines)); log_widget.config(state=tk.DISABLED)
                append_log_func(f"Loaded {len(log_lines)} log entries.", "INFO")
        except pymongo.errors.ConnectionFailure as e: append_log_func(f"DB Connection Error loading logs: {e}", "ERROR")
        except Exception as e: append_log_func(f"Error loading logs: {e}", "ERROR"); traceback.print_exc()


    def _export(self, log_widget, section):
        """Exports visible log content to a CSV file."""
        log_widget.config(state=tk.NORMAL); log_content = log_widget.get("1.0", "end").strip(); log_widget.config(state=tk.DISABLED)
        lines = [line for line in log_content.splitlines() if line.strip() and (line.strip().endswith(")") or "@" in line) and (line.strip().startswith("🟢") or line.strip().startswith("🔴"))]
        if not lines: messagebox.showinfo("Export Info", "No log entries to export.", parent=self.root); return

        default_filename = f"{section.lower()}_logs_{datetime.now():%Y%m%d_%H%M}.csv"
        path = filedialog.asksaveasfilename(defaultextension=".csv", filetypes=[("CSV files", "*.csv"), ("All files", "*.*")], title=f"Save {section} Logs As", initialfile=default_filename, parent=self.root)
        if not path: return
        try:
            with open(path, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                header = ["Action", "Plate", "Vehicle Type", "Timestamp"] # Added Vehicle Type
                if section == "Exit": header.append("Fee (₹)")
                writer.writerow(header)
                for line in lines:
                    icon_part = line[:1]; action = "Entry" if icon_part == "🟢" else "Exit"
                    # Extract Plate, Type, Timestamp, Fee using more robust regex
                    pattern = r'([🟢🔴])\s+([A-Z0-9\-]+)\s*(?:\((.*?)\))?\s*@\s+([\d\-]+\s+[\d:]+)(?:\s+\(Fee:\s*₹([\d\.]+)\))?'
                    match = re.match(pattern, line.strip())
                    if match:
                         icon, plate, v_type, timestamp, fee = match.groups()
                         row_data = [action, plate.strip(), v_type.strip() if v_type else "", timestamp.strip()]
                         if section == "Exit": row_data.append(fee.strip() if fee else "")
                         writer.writerow(row_data)
                    else: print(f"[WARN] Skipping export line: {line}")
            messagebox.showinfo("Export Success", f"Logs exported to:\n{path}", parent=self.root)
        except Exception as e: messagebox.showerror("Export Error", f"Export failed: {e}", parent=self.root); print(f"[ERROR] Export error: {e}"); traceback.print_exc()


# --- Main Execution ---
if __name__ == "__main__":
    if db is None or client is None:
         print("[FATAL] Exiting: Database connection not established.")
         try: root_err = tk.Tk(); root_err.withdraw(); messagebox.showerror("Startup Error", "Database connection failed."); root_err.destroy()
         except Exception: pass
         sys.exit(1)

    root = tk.Tk()
    app = ParkingApp(root)

    def on_closing():
        print("[INFO] Closing application requested...")
        if messagebox.askokcancel("Quit", "Quit Parking Management System?", parent=root):
            print("[INFO] Quitting...")
            for tab in (app.entry_tab, app.exit_tab, app.settings_tab): # Include settings tab if needed
                if tab and hasattr(tab, 'stop_camera') and callable(getattr(tab, 'stop_camera')):
                    try: tab.stop_camera()
                    except Exception as e: print(f"[ERROR] Stopping camera shutdown: {e}")
            global client
            if client:
                try: client.close(); print("[INFO] MongoDB connection closed.")
                except Exception as e: print(f"[ERROR] Closing MongoDB connection: {e}")
                client = None
            root.destroy(); print("[INFO] Application closed.")
        else: print("[INFO] Quit cancelled.")

    root.protocol("WM_DELETE_WINDOW", on_closing)
    root.mainloop()

