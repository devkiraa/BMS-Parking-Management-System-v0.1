import os
import io
import uuid
import re
import csv
import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext, filedialog, simpledialog
from PIL import Image, ImageTk, UnidentifiedImageError
import cv2
from datetime import datetime, timedelta, time # Import time class for combining date and time
import math
import time as standard_time # Import the standard time module with an alias
from pymongo import MongoClient
from bson import ObjectId
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
# Make sure google.cloud.vision is installed and authenticated
try:
    from google.cloud import vision
    from google.cloud.vision_v1 import AnnotateImageResponse # For error checking
    from google.api_core import exceptions as google_exceptions # For API errors
except ImportError:
    # Use a basic Tk window for the error if the main root isn't available yet
    root_check = tk.Tk(); root_check.withdraw()
    messagebox.showerror("Missing Library", "Google Cloud Vision library not found.\nPlease install it: pip install google-cloud-vision", parent=None)
    root_check.destroy()
    sys.exit(1)
except Exception as e:
     root_check = tk.Tk(); root_check.withdraw()
     messagebox.showerror("Vision Client Error", f"Could not initialize Google Cloud Vision client.\nEnsure credentials are set correctly.\nError: {e}", parent=None)
     root_check.destroy()
     # Optionally exit, or allow running without OCR
     # sys.exit(1)

# Make sure bcrypt is installed
try:
    import bcrypt
except ImportError:
    root_check = tk.Tk(); root_check.withdraw()
    messagebox.showerror("Missing Library", "bcrypt library not found.\nPlease install it: pip install bcrypt", parent=None)
    root_check.destroy()
    sys.exit(1)

import sys
import pymongo
import traceback
import configparser

# ---- CONFIGURATION ----
CONFIG_FILE = "config.ini"
config = configparser.ConfigParser()

# --- Read Configuration ---
## ANALYSIS: Handles config file reading, creation of a default if missing, and basic validation.
if not os.path.exists(CONFIG_FILE):
    print(f"Configuration file '{CONFIG_FILE}' not found. Creating a default one.")
    config['Database'] = {
        'mongodb_uri': 'mongodb+srv://apms4bb:memoriesbringback@caspianbms.erpwt.mongodb.net/caspiandb?retryWrites=true&w=majority&appName=Caspianbms', # User MUST replace this
        'database_name': 'caspiandb',
        'parking_collection': 'parking',
        'property_collection': 'property',
        'user_collection': 'user',          # Added user collection
        'employee_collection': 'employee'   # Added employee collection
    }
    config['Paths'] = {
        'assets_dir': 'assets',
        'service_account_json': 'service_account.json'
    }
    try:
        with open(CONFIG_FILE, 'w') as configfile: config.write(configfile)
        # Use a basic Tk window for the error if the main root isn't available yet
        root_check = tk.Tk(); root_check.withdraw()
        messagebox.showerror("Configuration Needed", f"'{CONFIG_FILE}' created.\nPlease edit it with your actual MongoDB URI and Service Account path.", parent=None)
        root_check.destroy()
        sys.exit(1)
    except IOError as e:
        root_check = tk.Tk(); root_check.withdraw()
        messagebox.showerror("Config Error", f"Could not create config file '{CONFIG_FILE}': {e}", parent=None)
        root_check.destroy(); sys.exit(1)
else:
    try:
        config.read(CONFIG_FILE)
        if not config.has_section('Database') or not config.has_section('Paths'): raise ValueError("Missing sections [Database] or [Paths].")
        # Check for essential options
        required_db_options = ['mongodb_uri', 'database_name', 'parking_collection', 'property_collection', 'user_collection', 'employee_collection']
        required_path_options = ['service_account_json', 'assets_dir']
        for option in required_db_options:
            if not config.has_option('Database', option): raise ValueError(f"Missing database option: {option}")
        for option in required_path_options:
             if not config.has_option('Paths', option): raise ValueError(f"Missing path option: {option}")

        # Check if the placeholder URI is still present
        if config.get('Database', 'mongodb_uri') == 'YOUR_MONGODB_SRV_URI_HERE': # Check against the placeholder
            root_check = tk.Tk(); root_check.withdraw()
            messagebox.showerror("Configuration Needed", f"Please edit '{CONFIG_FILE}' and replace 'YOUR_MONGODB_SRV_URI_HERE' with your actual MongoDB connection string.", parent=None)
            root_check.destroy(); sys.exit(1)
    except Exception as e:
        root_check = tk.Tk(); root_check.withdraw()
        messagebox.showerror("Config Error", f"Error reading configuration file '{CONFIG_FILE}':\n{e}", parent=None)
        root_check.destroy(); sys.exit(1)

try:
    SERVICE_ACCOUNT_PATH = config.get('Paths', 'service_account_json')
    ASSETS_DIR = config.get('Paths', 'assets_dir', fallback='assets')
    MONGODB_URI = config.get('Database', 'mongodb_uri')
    DB_NAME = config.get('Database', 'database_name', fallback='caspiandb')
    PARKING_COL_NAME = config.get('Database', 'parking_collection', fallback='parking')
    PROPERTY_COL_NAME = config.get('Database', 'property_collection', fallback='property')
    USER_COL_NAME = config.get('Database', 'user_collection', fallback='user')             # Read user collection name
    EMPLOYEE_COL_NAME = config.get('Database', 'employee_collection', fallback='employee') # Read employee collection name
except configparser.NoOptionError as e:
    root_check = tk.Tk(); root_check.withdraw(); messagebox.showerror("Config Error", f"Missing required option in '{CONFIG_FILE}': {e}", parent=None); root_check.destroy(); sys.exit(1)
except Exception as e:
    root_check = tk.Tk(); root_check.withdraw(); messagebox.showerror("Config Error", f"Unexpected error reading config: {e}", parent=None); root_check.destroy(); sys.exit(1)


if not os.path.exists(SERVICE_ACCOUNT_PATH):
    root_check = tk.Tk(); root_check.withdraw(); messagebox.showerror("Configuration Error", f"Service account JSON not found:\n{os.path.abspath(SERVICE_ACCOUNT_PATH)}\nCheck path in '{CONFIG_FILE}'.", parent=None); root_check.destroy(); sys.exit(1)
# Set environment variable for Google Cloud library authentication
os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = SERVICE_ACCOUNT_PATH

# MongoDB Connection
client = None; db = None; parking_col = None; property_col = None; user_col = None; employee_col = None
try:
    print(f"Connecting to MongoDB...");
    # Added timeout and ping for better connection validation
    client = MongoClient(MONGODB_URI, serverSelectionTimeoutMS=5000)
    client.admin.command('ping'); # Check connection
    db = client[DB_NAME];
    parking_col = db[PARKING_COL_NAME];
    property_col = db[PROPERTY_COL_NAME]
    user_col = db[USER_COL_NAME]             # Initialize user collection
    employee_col = db[EMPLOYEE_COL_NAME]     # Initialize employee collection
    print(f"MongoDB connection successful to database '{DB_NAME}'. Collections initialized.")
except pymongo.errors.ConfigurationError as e:
    root_check = tk.Tk(); root_check.withdraw(); messagebox.showerror("Database Config Error", f"MongoDB Configuration Error (check URI in config.ini):\n{e}", parent=None); root_check.destroy(); sys.exit(1)
except pymongo.errors.ConnectionFailure as e:
    root_check = tk.Tk(); root_check.withdraw(); messagebox.showerror("Database Connection Error", f"Could not connect to MongoDB:\n{e}", parent=None); root_check.destroy(); sys.exit(1)
except Exception as e: # Catch other potential errors during connection
    root_check = tk.Tk(); root_check.withdraw(); messagebox.showerror("Database Error", f"An unexpected error occurred connecting to MongoDB:\n{e}", parent=None); root_check.destroy(); sys.exit(1)


def find_cameras(max_index=5):
    """Finds available cameras, returns list of tuples (index, name)."""
    ## ANALYSIS: Attempts to find connected cameras. Suppresses stderr during detection to avoid clutter.
    cams = []
    print("Detecting cameras...")
    original_stderr = None
    devnull = None
    try: # Redirect stderr to avoid OpenCV backend error messages flooding console
        original_stderr = os.dup(sys.stderr.fileno())
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, sys.stderr.fileno())
    except OSError: # Handle cases where redirection might fail
        print("[WARN] Could not redirect stderr during camera detection.")
        original_stderr = None

    try:
        for i in range(max_index):
            # Use CAP_DSHOW on Windows for better compatibility sometimes
            cap_api = cv2.CAP_DSHOW if sys.platform == 'win32' else cv2.CAP_ANY
            cap = cv2.VideoCapture(i, cap_api)
            if cap is not None and cap.isOpened():
                try:
                    # Try reading a frame to confirm it works
                    ret, frame = cap.read()
                    if ret and frame is not None:
                        cam_name = f"Camera {i+1}" # Simple naming
                        cams.append((i, cam_name))
                        print(f"  [OK] Found: {cam_name} (Index {i})")
                    else:
                        print(f"  [WARN] Could not read frame from index {i}")
                except Exception as e_read:
                    print(f"  [ERROR] reading index {i}: {e_read}")
                finally:
                    cap.release() # Always release the capture device
            # else: print(f"  [INFO] Index {i} not opened.") # Optional: log unopened indices
    finally:
        # Restore stderr
        if original_stderr is not None:
            try:
                os.dup2(original_stderr, sys.stderr.fileno())
                os.close(original_stderr)
            except OSError:
                print("[WARN] Could not restore stderr after camera detection.")
        if devnull is not None:
            try: os.close(devnull)
            except OSError: pass

    print(f"Cameras found: {len(cams)}")
    return cams

AVAILABLE_CAMERAS = find_cameras(5) # Check first 5 indices

# --- Google Cloud Vision OCR ---
def detect_text(image_path):
    """Detects text (potential number plate) in an image, handling standard Indian and BH series formats."""
    ## ANALYSIS: Uses Google Vision API. Includes regex for Indian plates. Handles API errors.
    ## ANALYSIS: Could be improved with more sophisticated text block analysis or image pre-processing.
    try:
        v_client = vision.ImageAnnotatorClient()
    except Exception as e:
        print(f"[ERROR] Initializing Vision Client: {e}")
        return f"OCR Failed: Vision Client Init Error - {e}" # Return error string

    try:
        if not os.path.exists(image_path):
            raise FileNotFoundError(f"Image file not found: {image_path}")

        with io.open(image_path, 'rb') as f:
            content = f.read()
        image = vision.Image(content=content)

        # Add language hint for better accuracy with English characters/numerals
        image_context = vision.ImageContext(language_hints=["en"])

        response = v_client.text_detection(image=image, image_context=image_context)

        # Check for API errors in the response itself
        if isinstance(response, AnnotateImageResponse) and response.error.message:
            raise google_exceptions.GoogleAPICallError(response.error.message)
        elif not isinstance(response, AnnotateImageResponse):
            # Handle cases where the response might not be the expected type (unlikely but safe)
            print(f"[WARN] Unexpected response type from Vision API: {type(response)}")
            # Attempt to get annotations if possible, otherwise return error
            texts = getattr(response, 'text_annotations', None)
            if texts is None:
                raise Exception("Vision API returned an unexpected response format.")
        else:
            texts = response.text_annotations


        if not texts:
            print("[INFO] Vision API found no text.")
            return "" # Return empty string if no text found

        print(f"[INFO] Vision API returned {len(texts)} text blocks.")
        possible_plates = []

        # Iterate through detected text blocks (skip the first, which is the full text)
        for i, text in enumerate(texts):
            if i == 0: continue # Skip the full text block initially

            block_text = text.description.upper() # Convert to uppercase
            compact_raw = re.sub(r'[^A-Z0-9]', '', block_text) # Remove non-alphanumeric
            if not compact_raw: continue # Skip empty blocks

            # Regex for BH series: YY BH NNNN LL(L)
            bh_match = re.search(r'^(\d{2})(BH)(\d{4})([A-Z]{1,2})$', compact_raw)
            if bh_match:
                year, bh_marker, nums, letters = bh_match.groups()
                formatted_plate = f"{year}-{bh_marker}-{nums}-{letters}"
                print(f"[INFO] Found BH plate block {i}: {formatted_plate}")
                possible_plates.append(formatted_plate)
                continue # Found a match, move to next block

            # Regex for Standard series: LL NN L(L) NNNN
            # Made RTO digits (NN) {1,2} and optional letters (L(L)) {1,2}
            # Made final numbers (NNNN) {3,4}
            standard_match = re.search(r'^([A-Z]{2})(\d{1,2})([A-Z]{1,2})?(\d{3,4})$', compact_raw)
            if standard_match:
                state, rto, letters, nums = standard_match.groups()
                rto_padded = rto.rjust(2, '0') # Pad RTO code if single digit
                nums_padded = nums.rjust(4, '0') # Pad final numbers
                letters_formatted = letters if letters else 'XX' # Use XX if letters part is missing
                formatted_plate = f"{state}-{rto_padded}-{letters_formatted}-{nums_padded}"
                print(f"[INFO] Found Standard plate block {i}: {formatted_plate}")
                possible_plates.append(formatted_plate)
                continue # Found a match

            # Fallback: If block looks somewhat like a plate (length, mix of letters/numbers)
            if 6 <= len(compact_raw) <= 10 and re.search(r'\d', compact_raw) and re.search(r'[A-Z]', compact_raw):
                print(f"[INFO] Found fallback plate block {i}: {compact_raw}")
                possible_plates.append(compact_raw) # Add the raw compact version

        # Select the best candidate (prefer formatted ones)
        if possible_plates:
            # Prefer plates that were formatted (matched regex with hyphens)
            formatted = [p for p in possible_plates if '-' in p]
            best_plate = formatted[0] if formatted else possible_plates[0] # Take first formatted, else first found
            print(f"[INFO] Selecting best plate: {possible_plates} -> {best_plate}")
            return best_plate
        else:
            # If no blocks matched, check the full text (texts[0]) as a last resort
            print("[WARN] No specific blocks matched plate format. Checking full text block.");
            if texts: # Ensure texts[0] exists
                full_text_raw = texts[0].description.upper()
                full_compact_raw = re.sub(r'[^A-Z0-9]', '', full_text_raw)

                # Try matching BH/Standard within the full compact text
                bh_match = re.search(r'(\d{2})(BH)(\d{4})([A-Z]{1,2})', full_compact_raw) # Search within
                if bh_match:
                    year, bh_marker, nums, letters = bh_match.groups()
                    formatted_plate = f"{year}-{bh_marker}-{nums}-{letters}"
                    print(f"[INFO] Found BH in full text: {formatted_plate}")
                    return formatted_plate

                standard_match = re.search(r'([A-Z]{2})(\d{1,2})([A-Z]{1,2})?(\d{3,4})', full_compact_raw) # Search within
                if standard_match:
                    state, rto, letters, nums = standard_match.groups()
                    rto_padded = rto.rjust(2, '0'); nums_padded = nums.rjust(4, '0')
                    letters_formatted = letters if letters else 'XX'
                    formatted_plate = f"{state}-{rto_padded}-{letters_formatted}-{nums_padded}"
                    print(f"[INFO] Found Standard in full text: {formatted_plate}")
                    return formatted_plate

            print("[WARN] No plate found even in full text.");
            return "" # Return empty if nothing found

    except google_exceptions.GoogleAPICallError as e:
        print(f"[ERROR] Vision API Call Error: {e}")
        return f"OCR Failed: API Error - {e}" # Return specific error
    except FileNotFoundError as e:
        print(f"[ERROR] {e}")
        return f"OCR Failed: File not found" # Return specific error
    except Exception as e:
        print(f"[ERROR] Error during text detection: {e}")
        traceback.print_exc() # Print stack trace for debugging
        return f"OCR Failed: {e}" # Return generic error

# --- Editable Dialog for Plate Correction ---
class EditableDialog(tk.Toplevel):
    """Dialog for confirming or correcting the detected license plate."""
    ## ANALYSIS: Modal dialog to show captured image and allow plate correction. Handles confirm/retake/close actions.
    ## ANALYSIS: Includes basic validation for the entered plate format.
    ## ANALYSIS: Attempts to delete the temporary image file on close.
    def __init__(self, master, img_path, plate, on_confirm, on_retake):
        super().__init__(master)
        self.title("Confirm/Edit Number Plate")
        self.on_confirm, self.on_retake = on_confirm, on_retake
        self.transient(master) # Keep dialog on top of master
        self.grab_set() # Make dialog modal
        self.img_path = img_path
        self.result_plate = None # Store confirmed plate here

        # Display the captured image (if path provided and valid)
        img_loaded = False
        try:
            if img_path and os.path.exists(img_path):
                img = Image.open(img_path)
                img.thumbnail((400,300), Image.Resampling.LANCZOS) # Resize for display
                self.photo = ImageTk.PhotoImage(img)
                tk.Label(self, image=self.photo).pack(padx=10,pady=10)
                img_loaded = True
            elif img_path:
                # Show message if image path given but not found
                tk.Label(self, text=f"Image not found:\n{img_path}", fg="orange").pack(padx=10,pady=10)
        except UnidentifiedImageError:
            tk.Label(self, text=f"Error: Cannot identify image file\n{img_path}", fg="red").pack(padx=10,pady=10)
        except Exception as e:
            tk.Label(self, text=f"Unexpected error loading image: {e}", fg="red").pack(padx=10,pady=10)
            print(f"[ERROR] Loading image in dialog: {e}")
        if not img_loaded:
            self.photo = None # Ensure self.photo exists even if loading fails

        # Label and Entry for the plate number
        tk.Label(self, text="Detected/Enter Plate:", font=('Segoe UI',12)).pack(pady=(5,5))
        self.plate_var = tk.StringVar()
        # Pre-fill entry if OCR didn't fail and returned something
        initial_plate = plate if not (plate.startswith("OCR Failed") or not plate) else ""
        self.plate_var.set(initial_plate)
        self.entry = ttk.Entry(self, textvariable=self.plate_var, font=('Segoe UI',14,'bold'), justify='center', width=20)
        self.entry.pack(pady=(0,10), padx=10)
        self.entry.focus_set() # Set focus to entry
        self.entry.selection_range(0, tk.END) # Select current text

        # Buttons Frame
        btn_frame = ttk.Frame(self)
        btn_frame.pack(pady=10, padx=10, fill='x', expand=True)
        btn_frame.columnconfigure(0, weight=1) # Make buttons expand
        btn_frame.columnconfigure(1, weight=1)

        confirm_btn = ttk.Button(btn_frame, text="✅ Confirm", command=self._confirm, style="Accent.TButton")
        confirm_btn.grid(row=0, column=0, padx=5, sticky='ew')

        # Change Retake button text if no image was provided (manual entry case)
        retake_btn_text = "🔄 Retake" if img_path else "❌ Cancel"
        retake_btn = ttk.Button(btn_frame, text=retake_btn_text,  command=self._retake)
        retake_btn.grid(row=0, column=1, padx=5, sticky='ew')

        # Bindings for Enter, Escape, and Window Close
        self.protocol("WM_DELETE_WINDOW", self._retake) # Closing window acts like retake/cancel
        self.bind("<Return>", self._confirm) # Enter key confirms
        self.bind("<Escape>", self._retake) # Escape key cancels/retakes
        # Bind destroy to cleanup function
        self.bind("<Destroy>", self._handle_destroy)

        # Center the dialog relative to the master window
        self.update_idletasks() # Ensure window dimensions are calculated
        master_x=master.winfo_rootx(); master_y=master.winfo_rooty()
        master_w=master.winfo_width(); master_h=master.winfo_height()
        dialog_w=self.winfo_width(); dialog_h=self.winfo_height()
        x=master_x+(master_w-dialog_w)//2
        y=master_y+(master_h-dialog_h)//2
        self.geometry(f"+{x}+{y}")

    def _handle_destroy(self, event):
        """Cleanup: Delete temp image and call appropriate callback."""
        # Ensure this runs only when the dialog itself is destroyed
        if event and event.widget == self:
            # Delete the temporary image file if it exists
            if self.img_path and os.path.exists(self.img_path):
                try:
                    os.remove(self.img_path)
                    print(f"[INFO] Deleted temp image: {self.img_path}")
                except Exception as e:
                    print(f"[ERROR] Deleting temp image {self.img_path}: {e}")

            # Call the appropriate callback based on whether confirm was clicked
            # This logic was moved from _confirm and _retake to ensure it runs *after* cleanup
            if self.result_plate: # If _confirm set a result
                if callable(self.on_confirm):
                    self.on_confirm(self.result_plate)
            else: # Otherwise, assume retake/cancel
                if callable(self.on_retake):
                    self.on_retake()

    def _validate_plate(self, plate_str):
        """Basic validation for number plate format."""
        if not plate_str:
            messagebox.showwarning("Input Required", "Please enter a number plate.", parent=self)
            return False
        # Simple regex: 6-13 chars, alphanumeric and hyphen allowed. Adjust if needed.
        if not re.fullmatch(r'[A-Z0-9\-]{6,13}', plate_str):
            messagebox.showwarning("Invalid Format", "Plate format seems incorrect.\nExpected: 6-13 Alphanumeric characters or Hyphens.\nExample: MH-01-XX-1234 or 24BH1234AA", parent=self)
            return False
        return True

    def _confirm(self, event=None):
        """Handle confirm action: validate and store result."""
        plate = self.plate_var.get().strip().upper() # Get, clean, uppercase
        if self._validate_plate(plate):
            self.result_plate = plate # Store the valid plate
            self.destroy() # Close the dialog (will trigger _handle_destroy)

    def _retake(self, event=None):
        """Handle retake/cancel action."""
        self.result_plate = None # Ensure no result is stored
        self.destroy() # Close the dialog (will trigger _handle_destroy)


# --- Main Application Class ---
class ParkingApp:
    def __init__(self, root):
        self.root = root
        root.title("🚗 Parking Management System - Login") # Initial title
        root.geometry("400x300") # Start with smaller login window size
        root.configure(bg="#f0f0f0") # Base background color

        self._make_styles() # Apply custom ttk styles

        # Store camera mapping: Name -> Index for easy lookup
        self.camera_name_to_index = {name: index for index, name in AVAILABLE_CAMERAS}

        # --- App State ---
        self.logged_in_user_role = None
        self.assigned_property_id_str = None # Store the string property_id (e.g., "p2")
        self.assigned_property_doc = None    # Store the full property document

        # --- Frames ---
        # Frame for the login widgets
        self.login_frame = ttk.Frame(root, padding="30 30 30 30")
        self.login_frame.pack(expand=True) # Pack initially

        # Frame for the main application (will be packed after login)
        self.main_app_frame = ttk.Frame(root)
        # Don't pack main_app_frame yet

        # --- Login UI Elements ---
        self._build_login_ui()

        # Bind Enter key press in password field to attempt login
        self.password_entry.bind('<Return>', self._attempt_login)


    def _build_login_ui(self):
        """Creates the widgets for the login screen."""
        ttk.Label(self.login_frame, text="Login", font=('Segoe UI', 16, 'bold')).grid(row=0, column=0, columnspan=2, pady=10)

        ttk.Label(self.login_frame, text="Username:").grid(row=1, column=0, sticky="w", padx=5, pady=5)
        self.username_var = tk.StringVar()
        self.username_entry = ttk.Entry(self.login_frame, textvariable=self.username_var, width=25)
        self.username_entry.grid(row=1, column=1, padx=5, pady=5)

        ttk.Label(self.login_frame, text="Password:").grid(row=2, column=0, sticky="w", padx=5, pady=5)
        self.password_var = tk.StringVar()
        self.password_entry = ttk.Entry(self.login_frame, textvariable=self.password_var, show="*", width=25)
        self.password_entry.grid(row=2, column=1, padx=5, pady=5)

        login_button = ttk.Button(self.login_frame, text="Login", command=self._attempt_login, style="Accent.TButton")
        login_button.grid(row=3, column=0, columnspan=2, pady=20)

        # Set focus to username entry initially
        self.username_entry.focus_set()

    def _attempt_login(self, event=None): # Added event=None for binding
        """Checks credentials against DB and proceeds if correct."""
        username = self.username_var.get().strip()
        password = self.password_var.get() # Don't strip password

        if not username or not password:
            messagebox.showerror("Login Failed", "Username and Password cannot be empty.", parent=self.root)
            return

        try:
            # 1. Find user by user_id
            user_doc = user_col.find_one({"user_id": username})
            if not user_doc:
                messagebox.showerror("Login Failed", "Invalid username or password.", parent=self.root)
                print(f"[WARN] Login failed: User '{username}' not found.")
                self.password_var.set("")
                self.password_entry.focus_set()
                return

            # 2. Check user role
            user_role = user_doc.get("role")
            allowed_roles = ["manager", "security"]
            if user_role not in allowed_roles:
                messagebox.showerror("Login Failed", "Access denied. Insufficient privileges.", parent=self.root)
                print(f"[WARN] Login failed: User '{username}' role '{user_role}' not allowed.")
                self.password_var.set("")
                self.password_entry.focus_set()
                return

            # 3. Verify password using bcrypt
            stored_hash = user_doc.get("password")
            if not stored_hash:
                 messagebox.showerror("Login Error", "User account configuration issue (missing password hash).", parent=self.root)
                 print(f"[ERROR] Login failed: User '{username}' has no password hash in DB.")
                 return

            # Ensure password and hash are bytes for bcrypt
            if bcrypt.checkpw(password.encode('utf-8'), stored_hash.encode('utf-8')):
                # Password matches
                print(f"[INFO] User '{username}' password verified.")

                # 4. Find associated active employee and property
                employee_doc = employee_col.find_one({"userid": username, "status": "active"})
                if not employee_doc:
                    messagebox.showerror("Login Failed", "No active employee record found for this user.", parent=self.root)
                    print(f"[WARN] Login failed: No active employee record for user '{username}'.")
                    self.password_var.set("")
                    self.password_entry.focus_set()
                    return

                assigned_p_id_str = employee_doc.get("p_id")
                if not assigned_p_id_str:
                     messagebox.showerror("Login Error", "Employee account configuration issue (missing property assignment).", parent=self.root)
                     print(f"[ERROR] Login failed: Employee record for '{username}' has no p_id.")
                     return

                # 5. Fetch the assigned property details
                property_document = property_col.find_one({"property_id": assigned_p_id_str})
                if not property_document:
                     messagebox.showerror("Login Error", f"Assigned property '{assigned_p_id_str}' not found in database.", parent=self.root)
                     print(f"[ERROR] Login failed: Property '{assigned_p_id_str}' for user '{username}' not found.")
                     return

                # 6. Store user info and proceed
                self.logged_in_user_role = user_role
                self.assigned_property_id_str = assigned_p_id_str
                self.assigned_property_doc = property_document # Store the whole doc
                print(f"[INFO] Login successful for user '{username}' (Role: {user_role}, Property: {self.assigned_property_doc.get('name', assigned_p_id_str)}).")

                # --- Proceed to main application ---
                self.login_frame.destroy() # Remove login widgets
                self._build_main_ui()      # Build the main application UI
                self.main_app_frame.pack(fill="both", expand=True) # Show the main app frame
                # Resize window for the main application
                self.root.geometry("1200x750")
                self.root.minsize(1100, 700)
                self.root.title(f"🚗 Parking Management System - Property: {self.assigned_property_doc.get('name', assigned_p_id_str)}") # Update title
                # Start camera for the initially selected tab after UI is built
                self.root.after(150, self._trigger_initial_camera_start)

            else:
                # Password does not match
                messagebox.showerror("Login Failed", "Invalid username or password.", parent=self.root)
                print(f"[WARN] Login failed: Incorrect password for user '{username}'.")
                self.password_var.set("")
                self.password_entry.focus_set()
                return

        except pymongo.errors.ConnectionFailure as e:
             messagebox.showerror("Database Error", f"Could not connect to database during login:\n{e}", parent=self.root)
             print(f"[ERROR] DB Connection Failure during login: {e}")
        except Exception as e:
            messagebox.showerror("Login Error", f"An unexpected error occurred during login:\n{e}", parent=self.root)
            print(f"[ERROR] Unexpected login error: {e}")
            traceback.print_exc()
            self.password_var.set("")
            self.password_entry.focus_set()


    def _build_main_ui(self):
        """Builds the main application UI after successful login."""
        # --- Top Bar for Date/Time ---
        self.top_bar = ttk.Frame(self.main_app_frame, padding=(10, 5))
        self.top_bar.pack(side="top", fill="x")

        self.datetime_label = ttk.Label(self.top_bar, text="Loading date/time...", font=('Segoe UI', 10))
        self.datetime_label.pack(side="left")
        self.datetime_after_id = None # To store the 'after' job ID
        self._update_datetime() # Start the clock

        # --- Main Structure: Notebook with Tabs ---
        self.nav = ttk.Notebook(self.main_app_frame)
        self.entry_tab = ttk.Frame(self.nav, padding=10)
        self.exit_tab  = ttk.Frame(self.nav, padding=10)
        self.dashboard_tab = ttk.Frame(self.nav, padding=10)
        self.settings_tab = ttk.Frame(self.nav, padding=10)

        self.nav.add(self.entry_tab, text="🚙 Entry")
        self.nav.add(self.exit_tab, text="🏁 Exit")
        # Only show settings tab for managers (optional)
        if self.logged_in_user_role == 'manager':
            self.nav.add(self.dashboard_tab, text="📊 Dashboard")
            self.nav.add(self.settings_tab, text="⚙️ Settings")

        self.nav.pack(fill="both", expand=True, padx=5, pady=(0, 5)) # Reduced pady top

        # Build UI elements for each tab
        self._build_tab(self.entry_tab, is_entry=True)
        self._build_tab(self.exit_tab, is_entry=False)
        if self.logged_in_user_role == 'manager':
            self._build_dashboard_tab(self.dashboard_tab)
            self._build_settings_tab(self.settings_tab)

        # Bind events
        self.nav.bind("<<NotebookTabChanged>>", self._on_tab_change) # Handle tab switching
        # Unbind Return from root, it's now bound to password entry for login
        # self.root.unbind('<Return>') # Or just don't bind it globally initially
        # Re-bind Enter key press globally for capture (if desired after login)
        self.root.bind('<Return>', self._on_enter_press_main)


    def _update_datetime(self):
        """Updates the date and time label."""
        # Check if the label exists before trying to configure it
        if not hasattr(self, 'datetime_label') or not self.datetime_label.winfo_exists():
            # print("[INFO] Datetime label does not exist, stopping update.") # Can be noisy
            self.datetime_after_id = None
            return

        now = datetime.now()
        # Format: Day, DD Mon YYYY HH:MM:SS AM/PM
        dt_string = now.strftime("%a, %d %b %Y %I:%M:%S %p")
        try:
            self.datetime_label.config(text=dt_string)
            # Schedule the next update after 1000ms (1 second)
            self.datetime_after_id = self.root.after(1000, self._update_datetime)
        except tk.TclError:
             # Handle error if the widget is destroyed before the update runs (e.g., during closing)
             # print("[INFO] Datetime label update skipped, widget likely destroyed.") # Can be noisy
             pass


    def _on_enter_press_main(self, event):
        """Handles the Enter key press AFTER LOGIN to trigger capture."""
        ## ANALYSIS: Convenience feature to trigger capture with Enter key. Checks focus to avoid interfering with text entry.
        # Check if main UI components exist before proceeding
        if not hasattr(self, 'nav') or not self.nav.winfo_exists():
            return # Main UI not built yet

        focused_widget = self.root.focus_get()
        # Don't trigger if focus is on an input field or the log text area
        if isinstance(focused_widget, (tk.Entry, ttk.Entry, scrolledtext.ScrolledText, ttk.Combobox)):
            return

        try:
            current_tab_name = self.nav.select() # Get the ID of the selected tab
            if not current_tab_name: return # Should not happen
            current_tab_widget = self.nav.nametowidget(current_tab_name) # Get the widget itself

            # Check if the current tab is Entry or Exit and has the capture trigger function
            if current_tab_widget in (self.entry_tab, self.exit_tab):
                if hasattr(current_tab_widget, 'trigger_capture') and callable(getattr(current_tab_widget, 'trigger_capture')):
                    # Also check if the capture button is currently enabled
                    if hasattr(current_tab_widget, '_btn_capture') and current_tab_widget._btn_capture['state'] == tk.NORMAL:
                        current_tab_widget.trigger_capture() # Call the tab's capture function
        except tk.TclError:
            print("[WARN] Error getting current tab widget on Enter (main).")
        except Exception as e:
            print(f"[ERROR] During Enter press handling (main): {e}")

    def _on_tab_change(self, event):
        """Handles tab changes: stops camera on old tab, starts on new (if applicable)."""
        ## ANALYSIS: Manages camera resources efficiently by only running the camera for the active Entry/Exit tab.
        # Check if main UI components exist before proceeding
        if not hasattr(self, 'nav') or not self.nav.winfo_exists():
            return # Main UI not built yet

        newly_selected_tab_widget = None
        try:
            newly_selected_tab_name = self.nav.select()
            if newly_selected_tab_name:
                newly_selected_tab_widget = self.nav.nametowidget(newly_selected_tab_name)
        except tk.TclError:
            print("[WARN] Error getting newly selected tab widget."); return

        # Determine which tabs exist based on role
        active_tabs = [self.entry_tab, self.exit_tab]
        if self.logged_in_user_role == 'manager':
             if hasattr(self, 'dashboard_tab'):
                 active_tabs.append(self.dashboard_tab)
             if hasattr(self, 'settings_tab'):
                 active_tabs.append(self.settings_tab)

        # Stop camera on any tab that is *not* the newly selected one
        for tab in active_tabs:
            if tab and tab.winfo_exists() and tab != newly_selected_tab_widget: # Check if tab exists
                # Check if the tab has a 'stop_camera' method
                if hasattr(tab, 'stop_camera') and callable(getattr(tab, 'stop_camera')):
                    try:
                        tab.stop_camera()
                    except Exception as e:
                        print(f"[ERROR] Stopping camera on non-active tab change: {e}")

        # Start camera if the new tab is Entry or Exit
        if newly_selected_tab_widget in (self.entry_tab, self.exit_tab):
            if hasattr(newly_selected_tab_widget,'start_camera') and callable(getattr(newly_selected_tab_widget, 'start_camera')):
                try:
                    newly_selected_tab_widget.start_camera()
                    # Also refresh logs for the current date when switching to Entry/Exit tab
                    if hasattr(newly_selected_tab_widget, '_log_date_var'):
                        self._load_logs(
                            newly_selected_tab_widget._log_widget,
                            is_entry=(newly_selected_tab_widget == self.entry_tab),
                            selected_date_str=newly_selected_tab_widget._log_date_var.get()
                        )
                except Exception as e:
                    print(f"[ERROR] Starting camera or loading logs on tab change: {e}")
        # Refresh dashboard if that tab is selected
        elif newly_selected_tab_widget == self.dashboard_tab:
            if hasattr(self, '_refresh_dashboard_data'):
                self._refresh_dashboard_data()
        # Refresh property list (now just details) if Settings tab is selected
        elif newly_selected_tab_widget == self.settings_tab:
             if hasattr(self.settings_tab, '_load_assigned_property_details'):
                 self.settings_tab._load_assigned_property_details() # Load assigned property

    def _trigger_initial_camera_start(self):
        """Trigger the start_camera for the initially selected tab."""
        ## ANALYSIS: Ensures the camera starts when the app launches for the default tab.
        # Check if main UI components exist before proceeding
        if not hasattr(self, 'nav') or not self.nav.winfo_exists():
            print("[INFO] Main UI not ready for initial camera start.")
            return # Main UI not built yet

        try:
            current_tab_name = self.nav.select() # Get the ID of the initially selected tab
            if not current_tab_name: return
            current_tab_widget = self.nav.nametowidget(current_tab_name)

            if current_tab_widget in (self.entry_tab, self.exit_tab):
                if hasattr(current_tab_widget, 'start_camera') and callable(getattr(current_tab_widget, 'start_camera')):
                    current_tab_widget.start_camera()
                    # Load initial logs for today's date
                    if hasattr(current_tab_widget, '_log_date_var'):
                        self._load_logs(
                            current_tab_widget._log_widget,
                            is_entry=(current_tab_widget == self.entry_tab),
                            selected_date_str=current_tab_widget._log_date_var.get() # Should be today's date initially
                        )
        except tk.TclError:
            print("[WARN] Error getting initial tab widget.")
        except Exception as e:
            print(f"[ERROR] Starting camera or loading initial logs: {e}")
            # Update canvas text if camera start fails immediately
            if hasattr(current_tab_widget, '_canvas'):
                current_tab_widget._canvas.config(text=f"Cam Start Error", image='')
                current_tab_widget._canvas.imgtk = None # Clear any previous image reference

    def _make_styles(self):
        """Configures ttk styles for a more modern look."""
        ## ANALYSIS: Centralized styling using ttk.Style for consistent appearance.
        s = ttk.Style()
        s.theme_use('clam') # A theme that allows more customization

        # General widget styling
        s.configure(".", font=('Segoe UI', 10), background="#f0f0f0")
        s.configure("TLabel", background="#f0f0f0", foreground="#333")
        s.configure("Header.TLabel", font=('Segoe UI', 12, 'bold'), background="#f0f0f0")
        s.configure("TEntry", fieldbackground="white", foreground="#333")
        s.configure("TCombobox", fieldbackground="white", foreground="#333")
        s.map("TCombobox", fieldbackground=[('readonly','white'), ('disabled', '#e0e0e0')]) # Ensure disabled bg is greyish
        s.map("TCombobox", foreground=[('disabled', '#555')]) # Darker grey text when disabled
        s.configure("TRadiobutton", background="#f0f0f0", font=('Segoe UI', 10))

        # Notebook styling
        s.configure("TNotebook", background="#e1e1e1", borderwidth=0)
        s.configure("TNotebook.Tab", padding=[12, 8], font=('Segoe UI', 11, 'bold'), background="#d0d0d0", foreground="#444")
        s.map("TNotebook.Tab",
              background=[("selected", "#f0f0f0"), ("active", "#e8e8e8")], # Selected tab matches frame bg
              foreground=[("selected", "#0078d4"), ("active", "#333")]) # Highlight selected tab text

        # Button styling
        s.configure("TButton", font=('Segoe UI', 10, 'bold'), padding=(10, 6), background="#e1e1e1", foreground="#333", borderwidth=1, relief="raised")
        s.map("TButton",
              background=[('active', '#c0c0c0'), ('disabled', '#d9d9d9')],
              foreground=[('disabled', '#a3a3a3')])

        # Accent button style (for primary actions)
        s.configure("Accent.TButton", font=('Segoe UI', 11, 'bold'), background="#0078d4", foreground="white")
        s.map("Accent.TButton",
              background=[('active', '#005a9e'), ('disabled', '#b0b0b0')],
              foreground=[('disabled', '#f0f0f0')]) # Make disabled text lighter

        # Manual action button style
        s.configure("Manual.TButton", font=('Segoe UI', 10), padding=(8, 5), background="#f0ad4e", foreground="white") # Orange-ish
        s.map("Manual.TButton",
              background=[('active', '#ec971f'), ('disabled', '#d9d9d9')],
              foreground=[('disabled', '#a3a3a3')])

        # Refresh button style (smaller, less prominent)
        s.configure("Refresh.TButton", font=('Segoe UI', 9), padding=(5, 3))
        s.map("Refresh.TButton",
              background=[('active', '#c0c0c0')])


        # Video canvas style
        s.configure("VideoCanvas.TLabel", background="black", foreground="white", font=('Segoe UI', 14), anchor="center")

        # Treeview styling
        s.configure("Treeview", rowheight=25, fieldbackground="white")
        s.configure("Treeview.Heading", font=('Segoe UI', 10,'bold'))
        s.map("Treeview",
              background=[('selected', '#0078d4')],
              foreground=[('selected', 'white')])


    def _build_tab(self, frame, is_entry):
        """Builds the UI elements for Entry or Exit tabs, using the assigned property."""
        ## ANALYSIS: Constructs the common layout for Entry/Exit tabs (camera feed, controls, log).
        ## ANALYSIS: Property selection is now disabled and pre-filled.
        frame.columnconfigure(0, weight=2) # Video side takes more space
        frame.columnconfigure(1, weight=1) # Log side
        frame.rowconfigure(0, weight=1) # Allow row to expand vertically
        section = "Entry" if is_entry else "Exit"

        # --- Left Side (Camera and Controls) ---
        left_frame = ttk.Frame(frame, padding=5)
        left_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 5))
        left_frame.columnconfigure(0, weight=1)
        # Define row weights for vertical expansion
        left_frame.rowconfigure(0, weight=0) # Property
        left_frame.rowconfigure(1, weight=0) # Slots
        left_frame.rowconfigure(2, weight=0) # Type
        left_frame.rowconfigure(3, weight=0) # Camera
        left_frame.rowconfigure(4, weight=1) # Video Canvas (expands most)
        left_frame.rowconfigure(5, weight=0) # Buttons

        # --- Property Display (Read-only) ---
        prop_frame = ttk.Frame(left_frame)
        prop_frame.grid(row=0, column=0, sticky="ew", pady=(0, 5))
        ttk.Label(prop_frame, text="Property:", width=8).pack(side="left", padx=(0, 5))
        prop_var = tk.StringVar()
        assigned_prop_name = self.assigned_property_doc.get('name', 'N/A') if self.assigned_property_doc else "Error"
        prop_var.set(assigned_prop_name)

        # Use a disabled Entry or Label to display the property name
        # A disabled Combobox looks slightly different, Entry might be clearer
        prop_display = ttk.Entry(prop_frame, textvariable=prop_var, state="readonly", width=30)
        # Or use a Label:
        # prop_display = ttk.Label(prop_frame, textvariable=prop_var, width=30, relief="sunken", anchor="w")
        prop_display.pack(side="left", fill="x", expand=True)

        # --- Available Slots Display ---
        slots_lbl = ttk.Label(left_frame, text="Slots: N/A", font=('Segoe UI', 10))
        slots_lbl.grid(row=1, column=0, sticky="w", pady=2, padx=5)

        # --- Vehicle Type Selection ---
        type_frame = ttk.Frame(left_frame)
        type_frame.grid(row=2, column=0, sticky="ew", pady=2)
        ttk.Label(type_frame, text="Type:", width=8).pack(side="left", padx=(0, 5))
        vehicle_type_var = tk.StringVar(value="Car") # Default to Car
        ttk.Radiobutton(type_frame, text="Car", variable=vehicle_type_var, value="Car").pack(side="left", padx=2)
        ttk.Radiobutton(type_frame, text="Bike", variable=vehicle_type_var, value="Bike").pack(side="left", padx=2)

        # --- Camera Selection (Using Names) ---
        cam_frame = ttk.Frame(left_frame)
        cam_frame.grid(row=3, column=0, sticky="ew", pady=2)
        ttk.Label(cam_frame, text="Camera:", width=8).pack(side="left", padx=(0, 5))
        cam_name_var = tk.StringVar()
        cam_names = [name for index, name in AVAILABLE_CAMERAS] if AVAILABLE_CAMERAS else ["N/A"]
        cam_state = "readonly" if AVAILABLE_CAMERAS else "disabled"
        cbcam = ttk.Combobox(cam_frame, textvariable=cam_name_var, values=cam_names, state=cam_state, width=15)
        if AVAILABLE_CAMERAS:
            cam_name_var.set(cam_names[0]) # Default to first found camera
        cbcam.pack(side="left")

        # --- Video Canvas ---
        # Using a Label to display video frames
        canvas = ttk.Label(left_frame, text="Initializing camera...", style="VideoCanvas.TLabel")
        canvas.grid(row=4, column=0, sticky="nsew", pady=5, padx=5)

        # --- Button Row ---
        button_row_frame = ttk.Frame(left_frame)
        button_row_frame.grid(row=5, column=0, pady=10) # Centered by default grid behavior

        btn_capture = ttk.Button(button_row_frame, text="📸 Capture & Process", style="Accent.TButton")
        btn_capture.pack(side="left", padx=(0, 10))

        btn_manual = ttk.Button(button_row_frame, text="⌨️ Manual " + section, style="Manual.TButton")
        btn_manual.pack(side="left")

        # Initial button states based on property and camera availability
        property_available = bool(self.assigned_property_doc) # Check if property doc loaded
        if not property_available or not AVAILABLE_CAMERAS:
            btn_capture.config(state="disabled")
        if not property_available:
             btn_manual.config(state="disabled")

        # More specific disabled text
        if not property_available and not AVAILABLE_CAMERAS:
             btn_capture.config(text="🚫 Property/Cam Error")
        elif not property_available:
             btn_capture.config(text="🚫 Property Error")
        elif not AVAILABLE_CAMERAS:
             btn_capture.config(text="🚫 No Camera Found")

        # Store button references on the frame itself for easy access later
        frame._btn_capture = btn_capture
        frame._btn_manual = btn_manual

        # --- Right Side Frame (Logs) ---
        right_frame = ttk.Frame(frame, padding=5)
        right_frame.grid(row=0, column=1, sticky="nsew", padx=(5, 0))
        right_frame.columnconfigure(0, weight=1)
        right_frame.rowconfigure(0, weight=0) # Header row
        right_frame.rowconfigure(1, weight=1) # Log display expands

        # --- Log Header ---
        log_header_frame = ttk.Frame(right_frame)
        log_header_frame.grid(row=0, column=0, sticky="ew", pady=(0, 2))
        log_header_frame.columnconfigure(1, weight=1) # Allow date entry to expand

        ttk.Label(log_header_frame, text=f"📄 {section} Log", style="Header.TLabel").pack(side="left", padx=(0,10)) # Removed "History"

        # --- Log Date Selection ---
        ttk.Label(log_header_frame, text="Date (YYYY-MM-DD):").pack(side="left", padx=(0, 5))
        log_date_var = tk.StringVar(value=datetime.now().strftime("%Y-%m-%d")) # Default to today
        log_date_entry = ttk.Entry(log_header_frame, textvariable=log_date_var, width=12)
        log_date_entry.pack(side="left", padx=(0, 5))

        # Refresh Log Button
        btn_refresh_log = ttk.Button(log_header_frame, text="🔄", style="Refresh.TButton", width=3)
        btn_refresh_log.pack(side="left")

        # --- Log Display ---
        log = scrolledtext.ScrolledText(right_frame, width=50, height=15, font=("Consolas", 10), wrap=tk.WORD, bg="#ffffff", fg="#333333", relief="solid", borderwidth=1, state=tk.DISABLED)
        log.grid(row=1, column=0, sticky="nsew")

        # --- Log Utility Function (Prints to Console) ---
        ## ANALYSIS: This function only prints to console, doesn't update the GUI log. GUI log updated via _load_logs.
        def append_log(message, level="INFO"):
            """Appends a message to the CONSOLE log with timestamp and level."""
            timestamp = datetime.now().strftime("%H:%M:%S")
            prefix_map = {"INFO": "[INFO]", "WARN": "[WARN]", "ERROR": "[ERROR]", "SAVE": "[SAVE]", "OCR": "[OCR]"}
            prefix = prefix_map.get(level.upper(), "[INFO]")
            full_message = f"{timestamp} {prefix} {message}\n"
            print(full_message.strip()) # Print status/debug to console

        # --- Refresh Slots Function (Uses assigned property) ---
        def refresh_slots_typed(*args):
            """Updates the available slots label based on assigned property and vehicle type."""
            if not self.assigned_property_doc:
                slots_lbl.config(text="Slots: Property Error")
                return

            v_type = vehicle_type_var.get().lower() # Use lowercase for keys
            try:
                # Get counts directly from the stored property document
                avail = self.assigned_property_doc.get(f'available_parking_spaces_{v_type}', 'N/A')
                total = self.assigned_property_doc.get(f'parking_spaces_{v_type}', 'N/A')
                slots_lbl.config(text=f"Slots ({v_type.capitalize()}): {avail} / {total}")
            except Exception as e:
                slots_lbl.config(text="Slots: Error")
                print(f"[ERROR] Error refreshing slots from assigned property doc: {e}")


        # Trace changes vehicle type to update slots (property is fixed)
        vehicle_type_var.trace_add('write', refresh_slots_typed)
        # Initial call to set slots based on default selection
        if property_available:
            refresh_slots_typed()

        # --- Store references and state on the frame widget itself ---
        frame._state = {'cap': None, 'frame': None, 'after_id': None} # Camera state
        frame._log_widget = log # Reference to the log display widget
        frame._append_log = append_log # Reference to the console log function
        frame._canvas = canvas # Reference to the video display label
        # frame._prop_var = prop_var # No longer needed as property is fixed
        frame._refresh_slots = refresh_slots_typed # Reference to slot refresh function
        frame._vehicle_type_var = vehicle_type_var # Reference to vehicle type variable
        frame._cam_name_var = cam_name_var # Reference to camera selection variable
        frame._log_date_var = log_date_var # Reference to log date variable

        # --- Camera Handling Functions (Specific to this tab) ---
        def update_feed():
            """Reads frame from camera and updates the canvas label."""
            cap = frame._state.get('cap')
            # Stop if capture device is gone or closed
            if cap is None or not cap.isOpened():
                if frame._state.get('after_id') is not None:
                    try:
                        frame._canvas.after_cancel(frame._state['after_id'])
                    except tk.TclError: pass # Ignore if already cancelled/destroyed
                    frame._state['after_id'] = None
                return

            try:
                ok, frm = cap.read()
                if ok and frm is not None:
                    frame._state['frame'] = frm # Store the latest frame
                    img_rgb = cv2.cvtColor(frm, cv2.COLOR_BGR2RGB) # Convert for PIL/Tkinter
                    img_pil = Image.fromarray(img_rgb)

                    # Resize image to fit canvas dimensions
                    canvas_w = frame._canvas.winfo_width()
                    canvas_h = frame._canvas.winfo_height()

                    # Avoid division by zero or tiny canvas size before window is fully drawn
                    if canvas_w <= 1 or canvas_h <= 1:
                        frame._state['after_id'] = frame._canvas.after(100, update_feed) # Retry later
                        return

                    img_pil.thumbnail((canvas_w, canvas_h), Image.Resampling.LANCZOS) # Resize smoothly
                    photo = ImageTk.PhotoImage(img_pil)

                    # Update the canvas label
                    frame._canvas.imgtk = photo # Keep a reference! Important.
                    frame._canvas.config(image=photo, text="") # Display image, clear text

            except Exception as e:
                print(f"[ERROR] in update_feed cam {cam_name_var.get()}: {e}")
                stop_camera() # Stop feed on error
                try:
                    frame._canvas.config(image='', text=f"Feed Error")
                    frame._canvas.imgtk = None
                except tk.TclError: pass # Ignore if widget destroyed
                return # Stop the loop

            # Schedule the next update
            try:
                frame._state['after_id'] = frame._canvas.after(40, update_feed) # Aim for ~25 FPS
            except tk.TclError: pass # Ignore if widget destroyed

        def start_camera(event=None):
            """Initializes and starts the selected camera feed."""
            stop_camera() # Ensure any previous camera is stopped first
            selected_cam_name = cam_name_var.get()
            selected_cam_index = self.camera_name_to_index.get(selected_cam_name)

            if selected_cam_index is None or selected_cam_name == "N/A":
                try:
                    frame._canvas.config(text="No Camera Selected", image='')
                    frame._canvas.imgtk = None
                    btn_capture.config(state="disabled", text="🚫 Select Camera")
                except tk.TclError: pass # Ignore if widget destroyed
                return

            append_log(f"Initializing {selected_cam_name}...")
            try:
                frame._canvas.config(text=f"Starting {selected_cam_name}...", image='')
                frame._canvas.imgtk = None
                self.root.update_idletasks() # Force UI update to show "Starting..."
            except tk.TclError: pass # Ignore if widget destroyed

            cap_api = cv2.CAP_DSHOW if sys.platform == 'win32' else cv2.CAP_ANY
            cap = cv2.VideoCapture(selected_cam_index, cap_api)
            # Use the standard time module's sleep (aliased)
            standard_time.sleep(0.5) # Give camera time to initialize (may need adjustment)

            if not cap.isOpened():
                messagebox.showerror("Camera Error", f"Cannot open {selected_cam_name}", parent=self.root)
                append_log(f"Failed to open {selected_cam_name}", "ERROR")
                frame._state['cap'] = None
                try:
                    frame._canvas.config(text="Failed to Open", image='')
                    frame._canvas.imgtk = None
                    btn_capture.config(state="disabled", text="🚫 Camera Error")
                except tk.TclError: pass # Ignore if widget destroyed
                return

            # Try a few initial reads to ensure camera is responsive
            read_success = False
            try:
                for _ in range(5): # Try up to 5 times
                    ok, test_frame = cap.read()
                    # Use the standard time module's sleep (aliased)
                    standard_time.sleep(0.05) # Small delay between reads
                    if ok and test_frame is not None:
                        read_success = True
                        break
                if not read_success:
                    raise IOError("Failed initial reads after opening.")
            except Exception as e:
                cap.release() # Release the failed capture device
                frame._state['cap'] = None
                messagebox.showerror("Camera Error", f"Error reading from {selected_cam_name}: {e}", parent=self.root)
                append_log(f"Failed initial read {selected_cam_name}: {e}", "ERROR")
                try:
                    frame._canvas.config(text="Read Error", image='')
                    frame._canvas.imgtk = None
                    btn_capture.config(state="disabled", text="🚫 Read Error")
                except tk.TclError: pass # Ignore if widget destroyed
                return

            # Store the capture device and start the feed
            frame._state['cap'] = cap
            append_log(f"{section} {selected_cam_name} started.", "INFO")
            try:
                frame._canvas.config(text="") # Clear "Starting..." text
            except tk.TclError: pass # Ignore if widget destroyed

            # Enable buttons if property doc loaded successfully
            try:
                if self.assigned_property_doc:
                    btn_capture.config(state="normal", text="📸 Capture & Process")
                    btn_manual.config(state="normal")
                else:
                    btn_capture.config(state="disabled", text="🚫 Property Error")
                    btn_manual.config(state="disabled")
            except tk.TclError: pass # Ignore if buttons destroyed

            update_feed() # Start the update loop

        def stop_camera():
            """Stops the camera feed and releases resources."""
            # Cancel any pending frame update
            if frame._state.get('after_id') is not None:
                try:
                    frame._canvas.after_cancel(frame._state['after_id'])
                except tk.TclError: pass # Ignore if already cancelled/destroyed
                frame._state['after_id'] = None

            # Release the capture device
            cap = frame._state.get('cap')
            if cap and cap.isOpened():
                cap.release()
                frame._state['cap'] = None
                append_log(f"Camera {cam_name_var.get()} stopped.", "INFO") # Log stop

            # Update UI
            try:
                frame._canvas.config(image='', text="Camera Stopped")
                frame._canvas.imgtk = None # Clear image reference
            except tk.TclError: pass # Ignore if widget destroyed

            # Disable capture button, re-enable manual button if property loaded
            try:
                if btn_capture['state'] == tk.NORMAL:
                     btn_capture.config(state="disabled", text="🚫 Camera Stopped")
                # Check property status before enabling manual button
                if self.assigned_property_doc:
                     if btn_manual['state'] == tk.DISABLED:
                          btn_manual.config(state="normal")
                else:
                     # Ensure manual button is disabled if no property loaded
                     if btn_manual['state'] == tk.NORMAL:
                          btn_manual.config(state="disabled")
            except tk.TclError: pass # Ignore if buttons destroyed

        # --- Define and Attach Button Commands ---
        # Use lambda or functools.partial if needed, but direct assignment works here
        def trigger_capture_local():
            # Pass the assigned property name
            prop_name = self.assigned_property_doc.get('name', 'Error') if self.assigned_property_doc else 'Error'
            self._capture_and_edit(frame, is_entry, append_log, prop_name, vehicle_type_var.get(), refresh_slots_typed, btn_capture, btn_manual)
        frame.trigger_capture = trigger_capture_local # Store function ref on frame for Enter key access
        btn_capture.config(command=trigger_capture_local)

        def trigger_manual_local():
             # Pass the assigned property name
            prop_name = self.assigned_property_doc.get('name', 'Error') if self.assigned_property_doc else 'Error'
            self._manual_entry_exit(frame, is_entry, append_log, prop_name, vehicle_type_var.get(), refresh_slots_typed, btn_capture, btn_manual)
        btn_manual.config(command=trigger_manual_local)

        # Attach command to the Refresh Log button
        def refresh_log_display():
             self._load_logs(frame._log_widget, is_entry, frame._log_date_var.get())
        btn_refresh_log.config(command=refresh_log_display)

        # --- Attach camera control functions to the frame ---
        frame.start_camera = start_camera
        frame.stop_camera = stop_camera

        # Bind camera selection change to restart the camera
        cbcam.bind("<<ComboboxSelected>>", start_camera)

        # Load initial logs for this tab (will be done by _trigger_initial_camera_start or _on_tab_change)

    # --- Build Dashboard Tab ---
    def _build_dashboard_tab(self, frame):
        """Builds the UI for the Dashboard tab (Manager role only)."""
        frame.columnconfigure(0, weight=1)
        frame.columnconfigure(1, weight=1)
        frame.columnconfigure(2, weight=1)

        # --- Metrics Frame ---
        metrics_frame = ttk.Frame(frame, padding=10)
        metrics_frame.grid(row=0, column=0, columnspan=3, sticky="ew")
        metrics_frame.columnconfigure(0, weight=1)
        metrics_frame.columnconfigure(1, weight=1)
        metrics_frame.columnconfigure(2, weight=1)
        metrics_frame.columnconfigure(3, weight=1)


        # Today's Revenue
        revenue_frame = ttk.LabelFrame(metrics_frame, text="Today's Revenue", padding=10)
        revenue_frame.grid(row=0, column=0, padx=5, pady=5, sticky="nsew")
        self.revenue_label = ttk.Label(revenue_frame, text="₹ 0.00", font=("Segoe UI", 20, "bold"))
        self.revenue_label.pack()

        # Today's Entries
        entries_frame = ttk.LabelFrame(metrics_frame, text="Today's Entries", padding=10)
        entries_frame.grid(row=0, column=1, padx=5, pady=5, sticky="nsew")
        self.entries_label = ttk.Label(entries_frame, text="0", font=("Segoe UI", 20, "bold"))
        self.entries_label.pack()

        # Today's Exits
        exits_frame = ttk.LabelFrame(metrics_frame, text="Today's Exits", padding=10)
        exits_frame.grid(row=0, column=2, padx=5, pady=5, sticky="nsew")
        self.exits_label = ttk.Label(exits_frame, text="0", font=("Segoe UI", 20, "bold"))
        self.exits_label.pack()

        # Refresh Button
        refresh_button = ttk.Button(metrics_frame, text="🔄 Refresh", command=self._refresh_dashboard_data)
        refresh_button.grid(row=0, column=3, padx=10, pady=10, sticky="e")


        # --- Occupancy Frame ---
        occupancy_frame = ttk.LabelFrame(frame, text="Current Occupancy", padding=10)
        occupancy_frame.grid(row=1, column=0, columnspan=2, padx=5, pady=5, sticky="nsew")
        occupancy_frame.columnconfigure(0, weight=1)
        occupancy_frame.columnconfigure(1, weight=1)

        ttk.Label(occupancy_frame, text="Cars:", font=("Segoe UI", 12)).grid(row=0, column=0, sticky="w")
        self.occupancy_car_label = ttk.Label(occupancy_frame, text="0 / 0", font=("Segoe UI", 12, "bold"))
        self.occupancy_car_label.grid(row=0, column=1, sticky="e")

        ttk.Label(occupancy_frame, text="Bikes:", font=("Segoe UI", 12)).grid(row=1, column=0, sticky="w")
        self.occupancy_bike_label = ttk.Label(occupancy_frame, text="0 / 0", font=("Segoe UI", 12, "bold"))
        self.occupancy_bike_label.grid(row=1, column=1, sticky="e")


        # --- Chart Frame ---
        chart_frame = ttk.LabelFrame(frame, text="7-Day Revenue Trend", padding=10)
        chart_frame.grid(row=2, column=0, columnspan=3, padx=5, pady=5, sticky="nsew")

        # Placeholder for the chart
        self.chart_canvas_placeholder = ttk.Label(chart_frame, text="Chart will be displayed here.", anchor="center")
        self.chart_canvas_placeholder.pack(fill="both", expand=True)

        # Initial data load
        self.root.after(100, self._refresh_dashboard_data)

    def _refresh_dashboard_data(self):
        """Fetches and displays the latest dashboard metrics."""
        print("Refreshing dashboard data...")

        if not self.assigned_property_doc:
            messagebox.showwarning("Dashboard Error", "Assigned property not loaded.", parent=self.root)
            return

        prop_id = self.assigned_property_doc.get('_id')
        now = datetime.now()
        start_of_day = datetime.combine(now.date(), time.min)
        end_of_day = datetime.combine(now.date(), time.max)

        try:
            # --- Today's Revenue ---
            revenue_pipeline = [
                {"$match": {"property_id": prop_id, "exit_time": {"$gte": start_of_day, "$lte": end_of_day}}},
                {"$group": {"_id": None, "total_revenue": {"$sum": "$fee"}}}
            ]
            revenue_result = list(parking_col.aggregate(revenue_pipeline))
            today_revenue = revenue_result[0]['total_revenue'] if revenue_result else 0
            self.revenue_label.config(text=f"₹ {today_revenue:,.2f}")

            # --- Today's Entries & Exits ---
            entries_count = parking_col.count_documents({"property_id": prop_id, "entry_time": {"$gte": start_of_day, "$lte": end_of_day}})
            exits_count = parking_col.count_documents({"property_id": prop_id, "exit_time": {"$gte": start_of_day, "$lte": end_of_day}})
            self.entries_label.config(text=str(entries_count))
            self.exits_label.config(text=str(exits_count))

            # --- Current Occupancy ---
            total_car_spaces = self.assigned_property_doc.get("parking_spaces_car", 0)
            avail_car_spaces = self.assigned_property_doc.get("available_parking_spaces_car", 0)
            occupied_car = total_car_spaces - avail_car_spaces
            self.occupancy_car_label.config(text=f"{occupied_car} / {total_car_spaces}")

            total_bike_spaces = self.assigned_property_doc.get("parking_spaces_bike", 0)
            avail_bike_spaces = self.assigned_property_doc.get("available_parking_spaces_bike", 0)
            occupied_bike = total_bike_spaces - avail_bike_spaces
            self.occupancy_bike_label.config(text=f"{occupied_bike} / {total_bike_spaces}")

            # --- 7-Day Revenue Chart ---
            self._update_revenue_chart()

        except pymongo.errors.PyMongoError as e:
            messagebox.showerror("Database Error", f"Failed to fetch dashboard data:\n{e}", parent=self.root)
            print(f"[ERROR] Dashboard data fetch error: {e}")

    def _update_revenue_chart(self):
        """Fetches 7-day revenue data and updates the chart."""
        prop_id = self.assigned_property_doc.get('_id')
        today = datetime.now()
        dates = [(today - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(6, -1, -1)]
        revenue_by_day = {date: 0 for date in dates}

        start_date = today - timedelta(days=6)
        start_of_period = datetime.combine(start_date.date(), time.min)

        pipeline = [
            {"$match": {"property_id": prop_id, "exit_time": {"$gte": start_of_period}}},
            {"$group": {
                "_id": {"$dateToString": {"format": "%Y-%m-%d", "date": "$exit_time"}},
                "daily_revenue": {"$sum": "$fee"}
            }}
        ]
        result = list(parking_col.aggregate(pipeline))
        for item in result:
            if item['_id'] in revenue_by_day:
                revenue_by_day[item['_id']] = item['daily_revenue']

        # --- Create Chart ---
        fig = Figure(figsize=(5, 2.5), dpi=100)
        ax = fig.add_subplot(111)

        days = [datetime.strptime(d, "%Y-%m-%d").strftime("%a") for d in revenue_by_day.keys()]
        revenues = list(revenue_by_day.values())

        ax.bar(days, revenues, color='#0078d4')
        ax.set_ylabel("Revenue (₹)")
        ax.set_title("Revenue Last 7 Days")
        fig.tight_layout()

        # --- Embed in Tkinter ---
        if hasattr(self, 'chart_canvas'):
            self.chart_canvas.get_tk_widget().destroy()

        self.chart_canvas = FigureCanvasTkAgg(fig, master=self.chart_canvas_placeholder)
        self.chart_canvas.draw()
        self.chart_canvas.get_tk_widget().pack(fill="both", expand=True)


    # --- Build Settings Tab ---
    def _build_settings_tab(self, frame):
        """Builds the UI for the Settings tab (Manager role only). Shows assigned property."""
        ## ANALYSIS: Provides interface for viewing/editing assigned property details.
        ## ANALYSIS: No property list needed.
        frame.columnconfigure(0, weight=1) # Details frame takes full width now
        frame.columnconfigure(1, weight=1) # Export frame takes full width now
        frame.rowconfigure(0, weight=0)
        frame.rowconfigure(1, weight=0)


        # --- Details & Fees Section (Assigned Property) ---
        details_frame = ttk.LabelFrame(frame, text="Assigned Property Details & Fees", padding=10)
        details_frame.grid(row=0, column=0, columnspan=2, sticky="nsew", pady=(0, 5)) # Span both columns
        details_frame.columnconfigure(1, weight=1) # Allow entry fields to expand slightly

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

        # Save Button
        ttk.Button(details_frame, text="💾 Save Changes", command=self._save_property_details, style="Accent.TButton").grid(row=6, column=0, columnspan=2, pady=(15, 5))

        # --- Export Records Section ---
        export_frame = ttk.LabelFrame(frame, text="Export Parking Records", padding=10)
        export_frame.grid(row=1, column=0, columnspan=2, sticky="nsew", pady=(5, 0)) # Span both columns
        export_frame.columnconfigure(1, weight=1)

        # Start Date Entry
        ttk.Label(export_frame, text="Start Date (YYYY-MM-DD):").grid(row=0, column=0, sticky="w", padx=5, pady=5)
        frame._export_start_date_var = tk.StringVar(value=datetime.now().strftime("%Y-%m-%d")) # Default to today
        ttk.Entry(export_frame, textvariable=frame._export_start_date_var, width=12).grid(row=0, column=1, sticky="w", padx=5, pady=5)

        # End Date Entry
        ttk.Label(export_frame, text="End Date (YYYY-MM-DD):").grid(row=1, column=0, sticky="w", padx=5, pady=5)
        frame._export_end_date_var = tk.StringVar(value=datetime.now().strftime("%Y-%m-%d")) # Default to today
        ttk.Entry(export_frame, textvariable=frame._export_end_date_var, width=12).grid(row=1, column=1, sticky="w", padx=5, pady=5)

        # Export Button
        ttk.Button(export_frame, text="⬇️ Export to CSV", command=self._export_records_date_range).grid(row=2, column=0, columnspan=2, pady=10)

        # Store function ref for easy calling on tab change
        frame._load_assigned_property_details = lambda: self._load_assigned_property_details(frame)

        # Initial load of assigned property details
        frame._load_assigned_property_details()

    # --- Settings Tab Helper Functions ---
    def _load_assigned_property_details(self, settings_tab_frame):
        """Loads the details of the assigned property into the settings tab fields."""
        if not self.assigned_property_doc:
            print("[WARN] Assigned property document not available for settings tab.")
            self._clear_property_details(settings_tab_frame) # Clear fields if no doc
            messagebox.showerror("Error", "Could not load assigned property details.", parent=self.root)
            return

        try:
            prop_data = self.assigned_property_doc
            # Populate the entry fields
            settings_tab_frame._prop_name_var.set(prop_data.get("name", ""))
            settings_tab_frame._prop_spaces_car_var.set(str(prop_data.get("parking_spaces_car", 0)))
            settings_tab_frame._prop_spaces_bike_var.set(str(prop_data.get("parking_spaces_bike", 0)))
            settings_tab_frame._prop_fee_car_var.set(str(prop_data.get("fee_per_hour_car", 0.0)))
            settings_tab_frame._prop_fee_bike_var.set(str(prop_data.get("fee_per_hour_bike", 0.0)))
            # Store the assigned property's MongoDB ObjectId for the save function
            settings_tab_frame._selected_prop_id = prop_data.get("_id") # Get the actual ObjectId
        except Exception as e:
            print(f"[ERROR] Populating settings tab with assigned property details: {e}")
            messagebox.showerror("UI Error", f"Failed load assigned property details: {e}", parent=self.root)
            self._clear_property_details(settings_tab_frame)

    def _clear_property_details(self, settings_tab_frame):
        """Clears the property detail fields in the settings tab."""
        try:
            settings_tab_frame._prop_name_var.set("")
            settings_tab_frame._prop_spaces_car_var.set("")
            settings_tab_frame._prop_spaces_bike_var.set("")
            settings_tab_frame._prop_fee_car_var.set("")
            settings_tab_frame._prop_fee_bike_var.set("")
            settings_tab_frame._selected_prop_id = None # Clear selected ID
        except AttributeError:
            # This might happen if called before widgets are fully created
            print("[WARN] Could not clear property details, widgets might not exist yet.")


    def _add_edit_property(self, prop_id=None):
        """Placeholder for Add/Edit Property Dialog. (Likely not needed if users are assigned)."""
        action = "Edit" if prop_id else "Add"
        messagebox.showinfo("Not Implemented", f"{action} Property functionality is currently disabled or not implemented.", parent=self.root)


    def _save_property_details(self):
        """Saves the edited details of the assigned property."""
        ## ANALYSIS: Updates the assigned property's details in the DB.
        if not hasattr(self.settings_tab, '_selected_prop_id') or not self.settings_tab._selected_prop_id:
            messagebox.showwarning("No Property", "Cannot save changes, assigned property ID not found.", parent=self.root)
            return

        prop_mongo_id = self.settings_tab._selected_prop_id # Get the MongoDB _id
        try:
            # Read and validate inputs
            spaces_car_str = self.settings_tab._prop_spaces_car_var.get()
            spaces_bike_str = self.settings_tab._prop_spaces_bike_var.get()
            fee_car_str = self.settings_tab._prop_fee_car_var.get()
            fee_bike_str = self.settings_tab._prop_fee_bike_var.get()

            # Check if fields are empty
            if not all([spaces_car_str, spaces_bike_str, fee_car_str, fee_bike_str]):
                 raise ValueError("All fields (Spaces, Fees) are required.")

            spaces_car = int(spaces_car_str)
            spaces_bike = int(spaces_bike_str)
            fee_car = float(fee_car_str)
            fee_bike = float(fee_bike_str)

            # Basic validation
            if spaces_car < 0 or spaces_bike < 0:
                raise ValueError("Number of parking spaces cannot be negative.")
            if fee_car < 0 or fee_bike < 0:
                raise ValueError("Fees cannot be negative.")

        except ValueError as e:
            messagebox.showerror("Invalid Input", f"Please check the input values:\n{e}", parent=self.root)
            return
        except Exception as e: # Catch any other unexpected errors during input processing
            messagebox.showerror("Input Error", f"Error reading input fields: {e}", parent=self.root)
            return

        try:
            # Fetch the current available spaces before updating totals
            current_prop_doc = property_col.find_one({"_id": prop_mongo_id})
            if not current_prop_doc:
                messagebox.showerror("Save Error", "Could not find the assigned property in the database to update.", parent=self.root)
                return

            current_available_car = current_prop_doc.get("available_parking_spaces_car", 0)
            current_available_bike = current_prop_doc.get("available_parking_spaces_bike", 0)
            current_total_car = current_prop_doc.get("parking_spaces_car", 0)
            current_total_bike = current_prop_doc.get("parking_spaces_bike", 0)

            # Calculate the difference in total spaces
            diff_car = spaces_car - current_total_car
            diff_bike = spaces_bike - current_total_bike

            # Adjust available spaces based on the change in total spaces
            # Ensure available doesn't exceed new total or go below zero
            new_available_car = max(0, min(spaces_car, current_available_car + diff_car))
            new_available_bike = max(0, min(spaces_bike, current_available_bike + diff_bike))


            # Prepare update data - update totals, fees, and adjusted available counts
            update_data = {
                "$set": {
                    "parking_spaces_car": spaces_car,
                    "parking_spaces_bike": spaces_bike,
                    "available_parking_spaces_car": new_available_car, # Update available based on change
                    "available_parking_spaces_bike": new_available_bike, # Update available based on change
                    "fee_per_hour_car": fee_car,
                    "fee_per_hour_bike": fee_bike
                    # Add other fee fields if they become editable (e.g., one_hour_rate)
                }
            }
            result = property_col.update_one({"_id": prop_mongo_id}, update_data)

            if result.modified_count > 0:
                messagebox.showinfo("Success", f"Property details updated successfully.", parent=self.root)
                # Refresh the stored property document
                self.assigned_property_doc = property_col.find_one({"_id": prop_mongo_id})
                # Reload details in settings tab
                self._load_assigned_property_details(self.settings_tab)
                # Refresh slots display on Entry/Exit tabs
                if hasattr(self, 'entry_tab') and self.entry_tab.winfo_exists():
                    self.entry_tab._refresh_slots()
                if hasattr(self, 'exit_tab') and self.exit_tab.winfo_exists():
                    self.exit_tab._refresh_slots()

            elif result.matched_count > 0:
                messagebox.showinfo("No Changes", "No changes were detected in the provided details.", parent=self.root)
            else:
                 # This shouldn't happen if _selected_prop_id is valid
                 messagebox.showerror("Save Error", "Could not find the assigned property in the database to update.", parent=self.root)
        except pymongo.errors.PyMongoError as e:
             messagebox.showerror("Database Error", f"Failed to save property details to database:\n{e}", parent=self.root)
             print(f"[ERROR] Saving property details: {e}")
        except Exception as e:
            messagebox.showerror("Unexpected Error", f"An unexpected error occurred while saving: {e}", parent=self.root)
            print(f"[ERROR] Unexpected error saving property: {e}")
            traceback.print_exc()


    def _refresh_property_comboboxes(self):
        """Refreshes the property selection comboboxes on Entry and Exit tabs. (Now just sets the assigned property)."""
        print("[INFO] Refreshing property display...")
        # Check if main UI components exist before proceeding
        if not hasattr(self, 'entry_tab') or not self.entry_tab.winfo_exists() or not self.assigned_property_doc:
            print("[WARN] Main UI or assigned property not ready for property display refresh.")
            return # Main UI not built yet or property not assigned

        assigned_prop_name = self.assigned_property_doc.get('name', 'Error')

        try:
            for tab in (self.entry_tab, self.exit_tab):
                 # Find the specific display widget (Entry or Label)
                 prop_display_widget = None
                 try:
                     left_frame_widget = tab.winfo_children()[0]
                     prop_frame_widget = left_frame_widget.winfo_children()[0]
                     prop_display_widget = prop_frame_widget.winfo_children()[1] # Assuming Entry/Label is second
                 except (IndexError, AttributeError, tk.TclError) as find_e:
                     print(f"[WARN] Could not find property display widget for tab {tab} via hierarchy: {find_e}")
                     continue # Skip to next tab if widget not found

                 if hasattr(tab, '_prop_var') and prop_display_widget:
                     tab._prop_var.set(assigned_prop_name) # Set the variable
                     # Ensure widget state is readonly/disabled if it's an Entry
                     if isinstance(prop_display_widget, ttk.Entry):
                         prop_display_widget.config(state="readonly")
                     # Refresh slots based on the (potentially updated) assigned property doc
                     if hasattr(tab, '_refresh_slots') and callable(tab._refresh_slots):
                         tab._refresh_slots()
                 else:
                      print(f"[WARN] Property variable or display widget missing for tab {tab}")

        except Exception as e:
            print(f"[ERROR] Failed to refresh property display: {e}")
            messagebox.showerror("UI Error", f"Failed to update property display: {e}", parent=self.root)


    # --- Capture/Save Logic ---
    def _capture_and_edit(self, tab_frame, is_entry, append_log_func, prop_name, vehicle_type, refresh_slots, btn_capture, btn_manual):
        """Captures frame, runs OCR, shows edit dialog, and calls save on confirm."""
        ## ANALYSIS: Orchestrates the capture->OCR->confirm->save workflow.
        ## ANALYSIS: Uses assigned property name.
        if not self.assigned_property_doc: # Check if property is loaded
            messagebox.showwarning("Property Error", "Assigned property details not loaded.", parent=self.root)
            return

        # Use the name from the loaded document for consistency
        assigned_prop_name = self.assigned_property_doc.get('name', 'Error')

        original_capture_text = btn_capture['text'] # Store original text
        btn_capture.config(state="disabled", text="⏳ Capturing...")
        btn_manual.config(state="disabled")
        self.root.update_idletasks() # Force UI update

        cap = tab_frame._state.get('cap')
        # Check again if camera is running
        if cap is None or not cap.isOpened():
            messagebox.showwarning("No Camera", "Camera is not running or not selected.", parent=self.root)
            # Restore button states
            btn_capture.config(state="normal" if AVAILABLE_CAMERAS else "disabled", text=original_capture_text) # Re-enable only if cameras exist
            btn_manual.config(state="normal" if self.assigned_property_doc else "disabled")
            # Attempt to restart camera if it stopped unexpectedly
            if hasattr(tab_frame, 'start_camera'):
                 print("[INFO] Attempting to restart camera...")
                 tab_frame.start_camera()
            return

        append_log_func("Capturing frame...", "INFO")
        captured_frame = tab_frame._state.get('frame') # Get the latest frame stored by update_feed

        # Fallback: Try one more read if frame wasn't stored (shouldn't happen often)
        if captured_frame is None:
            append_log_func("No frame in state, attempting final read...", "WARN")
            try:
                ok, captured_frame = cap.read()
                if not ok or captured_frame is None:
                    raise IOError("Final frame read failed.")
            except cv2.error as e: # Catch OpenCV specific errors
                messagebox.showerror("Capture Error", f"Failed to capture frame (OpenCV error):\n{e}", parent=self.root)
                append_log_func(f"OpenCV capture error: {e}", "ERROR")
                btn_capture.config(state="normal", text=original_capture_text) # Restore state
                btn_manual.config(state="normal" if self.assigned_property_doc else "disabled")
                return
            except Exception as e:
                messagebox.showerror("Capture Error", f"Failed to capture frame:\n{e}", parent=self.root)
                append_log_func(f"Capture error: {e}", "ERROR")
                btn_capture.config(state="normal", text=original_capture_text) # Restore state
                btn_manual.config(state="normal" if self.assigned_property_doc else "disabled")
                return

        # Save the captured frame to a temporary file for OCR
        path = None
        try:
            os.makedirs(ASSETS_DIR, exist_ok=True) # Ensure assets directory exists
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            filename = f"capture_{timestamp}_{uuid.uuid4().hex[:6]}.jpg" # Unique filename
            path = os.path.join(ASSETS_DIR, filename)

            success = cv2.imwrite(path, captured_frame, [cv2.IMWRITE_JPEG_QUALITY, 95]) # Save with decent quality
            if not success:
                raise IOError(f"Failed to save image file: {path}")
            append_log_func(f"Frame saved: {filename}", "INFO")
            btn_capture.config(text="⏳ Detecting...") # Update button text
            self.root.update_idletasks()

        except Exception as e:
            messagebox.showerror("File Save Error", f"Failed to save captured image:\n{e}", parent=self.root)
            append_log_func(f"Image save error: {e}", "ERROR")
            btn_capture.config(state="normal", text=original_capture_text) # Restore state
            btn_manual.config(state="normal" if self.assigned_property_doc else "disabled")
            if path and os.path.exists(path): # Clean up failed save attempt
                 try: os.remove(path)
                 except Exception as del_e: print(f"[ERROR] Cleanup failed save {path}: {del_e}")
            return

        # --- Perform OCR ---
        plate = detect_text(path)
        append_log_func(f"OCR Result: '{plate}'" if plate and not plate.startswith("OCR Failed") else f"OCR Result: {plate if plate else 'No plate detected'}", "OCR")

        # --- Show Confirmation Dialog ---
        def on_confirm_callback(edited_plate):
            append_log_func(f"Plate Confirmed/Edited: {edited_plate}", "INFO")
            try:
                btn_capture.config(text="⏳ Saving...") # Update button state before save
                self.root.update_idletasks()
            except tk.TclError: pass # Ignore if button destroyed

            log_date_to_refresh = tab_frame._log_date_var.get() if hasattr(tab_frame, '_log_date_var') else None
            # Use assigned_prop_name here
            self._save_record(edited_plate, is_entry, append_log_func, assigned_prop_name, vehicle_type, refresh_slots, log_date_to_refresh)

        def on_retake_callback():
            append_log_func("Retake/Cancel requested.", "INFO")


        try:
            dialog = EditableDialog(self.root, path, plate, on_confirm_callback, on_retake_callback)
            dialog.bind("<Destroy>", lambda e, b_cap=btn_capture, b_man=btn_manual, txt=original_capture_text: (
                b_cap.config(state="normal" if tab_frame._state.get('cap') and tab_frame._state['cap'].isOpened() else "disabled", text=txt if tab_frame._state.get('cap') and tab_frame._state['cap'].isOpened() else "🚫 Camera Stopped"),
                b_man.config(state="normal" if self.assigned_property_doc else "disabled") # Check assigned_property_doc
            ), add="+")

        except Exception as e:
            messagebox.showerror("Dialog Error", f"Failed to open confirmation dialog:\n{e}", parent=self.root)
            append_log_func(f"Dialog creation error: {e}", "ERROR")
            try:
                btn_capture.config(state="normal", text=original_capture_text)
                btn_manual.config(state="normal" if self.assigned_property_doc else "disabled")
            except tk.TclError: pass
            if path and os.path.exists(path):
                try: os.remove(path)
                except Exception as del_e: print(f"[ERROR] Cleanup dialog fail {path}: {del_e}")


    def _manual_entry_exit(self, tab_frame, is_entry, append_log_func, prop_name, vehicle_type, refresh_slots, btn_capture, btn_manual):
        """Handles manual entry/exit via a simple dialog, then calls save."""
        ## ANALYSIS: Uses assigned property name.
        if not self.assigned_property_doc: # Check if property is loaded
            messagebox.showwarning("Property Error", "Assigned property details not loaded.", parent=self.root)
            return

        # Use the name from the loaded document for consistency
        assigned_prop_name = self.assigned_property_doc.get('name', 'Error')

        section = "Entry" if is_entry else "Exit"
        plate = simpledialog.askstring("Manual Input", f"Enter Number Plate for Manual {section} ({vehicle_type}):", parent=self.root)

        if not plate: # User cancelled or entered nothing
            append_log_func("Manual input cancelled.", "INFO")
            return

        plate = plate.strip().upper() # Clean and standardize input

        if not re.fullmatch(r'[A-Z0-9\-]{6,13}', plate):
            messagebox.showwarning("Invalid Format", "Plate format seems incorrect.\nExpected: 6-13 Alphanumeric characters or Hyphens.", parent=self.root)
            append_log_func(f"Manual input validation failed: '{plate}'", "WARN")
            return

        append_log_func(f"Manual Plate Entered: {plate} ({vehicle_type}) for {section}", "INFO")

        original_manual_text = btn_manual['text']
        try:
            btn_manual.config(state="disabled", text="⏳ Saving...")
            btn_capture.config(state="disabled") # Disable capture during manual save
            self.root.update_idletasks()
        except tk.TclError: pass # Ignore if buttons destroyed

        try:
            log_date_to_refresh = tab_frame._log_date_var.get() if hasattr(tab_frame, '_log_date_var') else None
            # Use assigned_prop_name here
            self._save_record(plate, is_entry, append_log_func, assigned_prop_name, vehicle_type, refresh_slots, log_date_to_refresh)
        finally:
            # Restore button states after save attempt (success or failure)
            try:
                btn_manual.config(state="normal", text=original_manual_text)
                is_cam_running = tab_frame._state.get('cap') and tab_frame._state['cap'].isOpened()
                capture_button_state = "normal" if is_cam_running else "disabled"
                capture_button_text = "📸 Capture & Process" if is_cam_running else "🚫 Camera Stopped"
                if not self.assigned_property_doc: # Check assigned property again
                     capture_button_state = "disabled"
                     capture_button_text = "🚫 Property Error" if is_cam_running else capture_button_text

                btn_capture.config(state=capture_button_state, text=capture_button_text)
            except tk.TclError: pass # Ignore if buttons destroyed


    def _save_record(self, plate, is_entry, append_log_func, prop_name, vehicle_type, refresh_slots_func, log_date_to_refresh=None):
        """Saves entry/exit record to DB, updates slots, calculates fee on exit, and refreshes log for the specified date."""
        ## ANALYSIS: Uses the assigned property document fetched during login.
        now = datetime.now()
        v_type_lower = vehicle_type.lower() # Use lowercase for consistency in DB keys
        action = "entry" if is_entry else "exit"
        append_log_func(f"Attempting to save {action} for {plate} ({vehicle_type})...", "SAVE")

        if not self.assigned_property_doc:
             messagebox.showerror("Save Error", "Assigned property data is missing. Cannot save record.", parent=self.root)
             append_log_func("Save failed: Missing assigned property document.", "ERROR")
             return

        # Use the stored property document
        prop = self.assigned_property_doc
        pid = prop.get('_id') # Get the MongoDB ObjectId
        if not pid:
            messagebox.showerror("Save Error", "Assigned property ID is missing. Cannot save record.", parent=self.root)
            append_log_func("Save failed: Missing assigned property _id.", "ERROR")
            return

        # Final validation check on the plate format before DB operation
        if not re.fullmatch(r'[A-Z0-9\-]+', plate): # Simplified check, main validation done earlier
            messagebox.showerror("Save Error", f"Invalid plate format '{plate}' detected before saving.", parent=self.root)
            append_log_func(f"Save aborted: Invalid format '{plate}'.", "ERROR")
            return

        try:
            avail_space_key = f"available_parking_spaces_{v_type_lower}"
            total_space_key = f"parking_spaces_{v_type_lower}"
            fee_key = f"fee_per_hour_{v_type_lower}"

            if is_entry:
                # --- Handle Vehicle Entry ---
                existing_entry = parking_col.find_one({"vehicle_no": plate, "property_id": pid, "exit_time": None})
                if existing_entry:
                    messagebox.showwarning("Duplicate Entry", f"Vehicle {plate} already has an active parking session at {prop_name}.", parent=self.root)
                    append_log_func(f"Duplicate entry prevented: {plate} at {prop_name}.", "WARN")
                    return

                # Re-fetch latest property details just before update for accurate counts
                latest_prop = property_col.find_one({"_id": pid}, {avail_space_key: 1})
                if not latest_prop or latest_prop.get(avail_space_key, 0) <= 0:
                    messagebox.showwarning("Parking Full", f"No {vehicle_type} slots currently available at {prop_name}.", parent=self.root)
                    append_log_func(f"Entry failed: Parking full ({vehicle_type}) for {plate} at {prop_name}.", "WARN")
                    return

                # Create new parking record
                new_record = {
                    "parking_id": str(uuid.uuid4()),
                    "property_id": pid,
                    "vehicle_no": plate,
                    "vehicle_type": vehicle_type,
                    "entry_time": now,
                    "exit_time": None,
                    "fee": 0,
                    "mode_of_payment": None
                }
                insert_result = parking_col.insert_one(new_record)

                # Decrement available space count for the property
                update_result = property_col.update_one(
                    {"_id": pid, avail_space_key: {"$gt": 0}},
                    {"$inc": {avail_space_key: -1}}
                )

                if insert_result.inserted_id and update_result.modified_count > 0:
                    append_log_func(f"Entry Saved: {plate} ({vehicle_type}) @ {now:%Y-%m-%d %H:%M:%S}", "SAVE")
                    messagebox.showinfo("Entry Success", f"{vehicle_type} {plate} entry recorded successfully at {prop_name}.", parent=self.root)
                    # Refresh the stored property doc after update
                    self.assigned_property_doc = property_col.find_one({"_id": pid})
                    self._load_logs(self.entry_tab._log_widget, True, log_date_to_refresh)
                elif insert_result.inserted_id:
                     append_log_func(f"Entry saved for {plate}, but slot count update failed (maybe already 0?).", "WARN")
                     messagebox.showwarning("DB Warning", "Entry recorded, but failed to update slot count. Please check property details.", parent=self.root)
                     self.assigned_property_doc = property_col.find_one({"_id": pid}) # Refresh anyway
                     self._load_logs(self.entry_tab._log_widget, True, log_date_to_refresh)
                else:
                     append_log_func(f"Entry DB insert issue for {plate}.", "ERROR")
                     messagebox.showerror("DB Error", "Failed to save entry record to database.", parent=self.root)

            else:
                # --- Handle Vehicle Exit ---
                updated_doc = parking_col.find_one_and_update(
                    {"vehicle_no": plate, "exit_time": None, "property_id": pid},
                    {"$set": {"exit_time": now}},
                    sort=[('entry_time', -1)],
                    return_document=pymongo.ReturnDocument.AFTER
                )

                if updated_doc:
                    entry_time = updated_doc.get('entry_time')
                    calculated_fee = 0.0
                    exiting_vehicle_type = updated_doc.get('vehicle_type', 'Unknown')
                    exiting_v_type_lower = exiting_vehicle_type.lower()
                    exit_fee_key = f"fee_per_hour_{exiting_v_type_lower}"
                    exit_avail_space_key = f"available_parking_spaces_{exiting_v_type_lower}"

                    if entry_time and isinstance(entry_time, datetime):
                        duration = now - entry_time
                        total_hours = duration.total_seconds() / 3600
                        # Use fee from the stored property doc
                        fee_per_hour = prop.get(exit_fee_key, 10.0)

                        if not isinstance(fee_per_hour, (int, float)) or fee_per_hour < 0:
                            print(f"[WARN] Invalid {exit_fee_key} ({fee_per_hour}) in DB for {prop_name}. Using default 10.0.")
                            fee_per_hour = 10.0

                        if total_hours <= 1.0:
                            calculated_fee = 0.0
                        else:
                            chargeable_hours = math.ceil(total_hours) - 1
                            calculated_fee = chargeable_hours * fee_per_hour

                        calculated_fee = round(max(0.0, calculated_fee), 2)
                        parking_col.update_one({"_id": updated_doc["_id"]}, {"$set": {"fee": calculated_fee}})
                        append_log_func(f"Fee Calculated: ₹{calculated_fee:.2f} ({total_hours:.2f} hrs).", "INFO")
                    else:
                        append_log_func(f"Could not calculate fee for {plate}: Invalid or missing entry time in record.", "WARN")
                        messagebox.showwarning("Fee Warning", "Could not calculate parking fee. Entry time missing or invalid.", parent=self.root)

                    # Increment available space count for the correct vehicle type
                    property_col.update_one({"_id": pid}, {"$inc": {exit_avail_space_key: 1}})

                    log_msg = f"Exit Saved: {plate} ({exiting_vehicle_type}) Fee: ₹{calculated_fee:.2f} @ {now:%Y-%m-%d %H:%M:%S}"
                    append_log_func(log_msg, "SAVE")
                    messagebox.showinfo("Exit Success", f"Exit recorded for {plate} from {prop_name}.\nCalculated Fee: ₹{calculated_fee:.2f}", parent=self.root)
                    # Refresh the stored property doc after update
                    self.assigned_property_doc = property_col.find_one({"_id": pid})
                    self._load_logs(self.exit_tab._log_widget, False, log_date_to_refresh)
                else:
                    messagebox.showwarning("No Entry Found", f"No active parking session found for {plate} at {prop_name}.", parent=self.root)
                    append_log_func(f"Exit failed: No open entry found for {plate} at {prop_name}.", "WARN")

            # Refresh the slot count display on the current tab after entry or exit
            if callable(refresh_slots_func):
                refresh_slots_func()
            else:
                print("[WARN] refresh_slots_func not callable during save record.")

        except pymongo.errors.ConnectionFailure as e:
            messagebox.showerror("Database Error", f"Database connection lost during save:\n{e}", parent=self.root)
            append_log_func(f"DB Connection Failure during save: {e}", "ERROR")
        except pymongo.errors.PyMongoError as e: # Catch specific pymongo errors
            messagebox.showerror("Database Error", f"A database error occurred during save:\n{e}", parent=self.root)
            append_log_func(f"DB Error during save: {e}", "ERROR")
        except Exception as e:
            messagebox.showerror("Unexpected Error", f"An unexpected error occurred while saving record:\n{e}", parent=self.root)
            append_log_func(f"Unexpected Save Error: {e}", "ERROR")
            traceback.print_exc() # Print stack trace for debugging unexpected errors
        finally:
             # Ensure slots refresh even on error, if possible and function exists
             if callable(refresh_slots_func):
                  try:
                      refresh_slots_func()
                  except Exception as refresh_e:
                      print(f"[ERROR] Error during final slot refresh in _save_record: {refresh_e}")


    def _load_logs(self, log_widget, is_entry, selected_date_str=None):
        """Loads parking records for a specific date and the assigned property into the specified log display."""
        ## ANALYSIS: Fetches records from DB for a given date/property and populates the ScrolledText widget.
        section = "Entry" if is_entry else "Exit"

        if not self.assigned_property_doc:
             print("[WARN] Cannot load logs, assigned property not set.")
             log_widget.config(state=tk.NORMAL)
             log_widget.delete('1.0', tk.END)
             log_widget.insert("end", "Error: Property not assigned.\n")
             log_widget.config(state=tk.DISABLED)
             return

        assigned_prop_mongo_id = self.assigned_property_doc.get('_id') # Get MongoDB _id

        # Parse the selected date
        if selected_date_str is None:
            selected_date_str = datetime.now().strftime("%Y-%m-%d") # Default to today if none provided

        try:
            selected_date = datetime.strptime(selected_date_str, "%Y-%m-%d")
            start_of_day = datetime.combine(selected_date.date(), time.min)
            end_of_day = datetime.combine(selected_date.date(), time.max)
            date_header = selected_date.strftime("%Y-%m-%d") # For the log title
        except ValueError:
            messagebox.showerror("Invalid Date", f"Invalid date format: '{selected_date_str}'. Please use YYYY-MM-DD.", parent=self.root)
            log_widget.config(state=tk.NORMAL)
            log_widget.delete('1.0', tk.END)
            log_widget.insert("end", f"Invalid date format entered: {selected_date_str}\n")
            log_widget.config(state=tk.DISABLED)
            return

        # Enable widget, clear, add header, disable again
        log_widget.config(state=tk.NORMAL)
        log_widget.delete('1.0', tk.END)
        log_widget.insert("end", f"📄 {section} Log for: {date_header}\n" + "="*40 + "\n") # Header with date
        log_widget.config(state=tk.DISABLED)

        print(f"[INFO] Loading {section.lower()} logs for property '{self.assigned_property_doc.get('name')}' on date: {date_header}...") # Console log

        try:
            # Define query based on entry/exit, date range, AND property_id
            if is_entry:
                query = {
                    "property_id": assigned_prop_mongo_id, # Filter by assigned property
                    "entry_time": {"$gte": start_of_day, "$lte": end_of_day},
                    "exit_time": None
                }
                sort_key = "entry_time"
            else:
                query = {
                    "property_id": assigned_prop_mongo_id, # Filter by assigned property
                    "exit_time": {"$gte": start_of_day, "$lte": end_of_day}
                }
                sort_key = "exit_time"

            records_cursor = parking_col.find(query).sort(sort_key, pymongo.DESCENDING)
            records_list = list(records_cursor)

            if not records_list:
                log_widget.config(state=tk.NORMAL)
                log_widget.insert(tk.END, f"No {section.lower()} records found for {date_header}.\n")
                log_widget.config(state=tk.DISABLED)
                print(f"[INFO] No {section.lower()} records found in DB for {date_header}.")
            else:
                log_lines = []
                for record in records_list:
                    ts_key = "entry_time" if is_entry else "exit_time"
                    ts = record.get(ts_key)

                    if ts and isinstance(ts, datetime):
                        icon = "🟢" if is_entry else "🔴"
                        plate = record.get('vehicle_no', 'N/A')
                        v_type = record.get('vehicle_type', '')
                        time_str = ts.strftime('%H:%M:%S')
                        type_str = f" ({v_type})" if v_type else ""
                        log_line = f"{time_str} {icon} {plate:<14}{type_str:<7}"

                        if not is_entry:
                            fee = record.get('fee', None)
                            fee_str = f"₹{fee:.2f}" if isinstance(fee, (int, float)) else "N/A"
                            log_line += f" (Fee: {fee_str})"
                        log_lines.append(log_line + "\n")
                    else:
                        log_lines.append(f"Invalid record timestamp: ID {record.get('_id')}\n")

                if log_lines:
                    log_widget.config(state=tk.NORMAL)
                    log_widget.insert(tk.END, "".join(log_lines))
                    log_widget.config(state=tk.DISABLED)
                print(f"[INFO] Displayed {len(log_lines)} {section.lower()} log entries for {date_header}.")

        except pymongo.errors.ConnectionFailure as e:
            print(f"[ERROR] DB Connection Error loading logs: {e}")
            messagebox.showerror("DB Error", f"Database Connection Error while loading logs:\n{e}", parent=self.root)
            log_widget.config(state=tk.NORMAL); log_widget.insert(tk.END, "DB Connection Error\n"); log_widget.config(state=tk.DISABLED)
        except Exception as e:
            print(f"[ERROR] Error loading logs: {e}")
            traceback.print_exc()
            messagebox.showerror("Log Error", f"Failed to load logs:\n{e}", parent=self.root)
            log_widget.config(state=tk.NORMAL); log_widget.insert(tk.END, "Error loading logs\n"); log_widget.config(state=tk.DISABLED)

    def _export_records_date_range(self):
        """Exports parking records for the assigned property within a selected date range to CSV."""
        ## ANALYSIS: Exports data to CSV based on assigned property and date range.
        if not self.assigned_property_doc:
             messagebox.showerror("Error", "Cannot export records, assigned property not loaded.", parent=self.root)
             return

        assigned_prop_mongo_id = self.assigned_property_doc.get('_id') # Get MongoDB _id
        assigned_prop_name = self.assigned_property_doc.get('name', 'UnknownProperty')

        try:
            start_date_str = self.settings_tab._export_start_date_var.get()
            end_date_str = self.settings_tab._export_end_date_var.get()
            start_date = datetime.strptime(start_date_str, "%Y-%m-%d")
            end_date_dt = datetime.strptime(end_date_str, "%Y-%m-%d")
            end_date_exclusive = datetime.combine(end_date_dt, time.max)
        except ValueError:
            messagebox.showerror("Invalid Date", "Please enter dates in YYYY-MM-DD format.", parent=self.root)
            return

        if start_date > end_date_dt:
            messagebox.showerror("Invalid Date Range", "Start date must be before or the same as the end date.", parent=self.root)
            return

        # Suggest a filename including property name
        default_filename = f"parking_report_{assigned_prop_name.replace(' ','_')}_{start_date_str}_to_{end_date_str}.csv"
        path = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
            title="Save Parking Report As",
            initialfile=default_filename,
            parent=self.root
        )
        if not path: return

        print(f"[INFO] Exporting records for property '{assigned_prop_name}' from {start_date_str} to {end_date_str} to {path}")
        try:
            # Query MongoDB for records for the assigned property within the date range
            query = {
                "property_id": assigned_prop_mongo_id, # Filter by assigned property
                "entry_time": {
                    "$gte": start_date,
                    "$lte": end_date_exclusive
                }
            }
            records_cursor = parking_col.find(query).sort("entry_time", pymongo.ASCENDING)

            count = 0
            with open(path, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                header = ["Plate", "Vehicle Type", "Entry Time", "Exit Time", "Fee (₹)", "Property Name", "Parking ID"] # Changed Property ID header
                writer.writerow(header)

                for record in records_cursor:
                    entry_ts = record.get('entry_time')
                    exit_ts = record.get('exit_time')
                    fee = record.get('fee', None)

                    writer.writerow([
                        record.get('vehicle_no', 'N/A'),
                        record.get('vehicle_type', ''),
                        entry_ts.strftime('%Y-%m-%d %H:%M:%S') if entry_ts else '',
                        exit_ts.strftime('%Y-%m-%d %H:%M:%S') if exit_ts else 'PARKED',
                        f"{fee:.2f}" if isinstance(fee, (int, float)) else '',
                        assigned_prop_name, # Write property name instead of ID
                        record.get('parking_id', '')
                    ])
                    count += 1

            if count > 0:
                messagebox.showinfo("Export Success", f"Successfully exported {count} records to:\n{path}", parent=self.root)
                print(f"[INFO] Exported {count} records.")
            else:
                messagebox.showinfo("Export Info", f"No parking records found for property '{assigned_prop_name}' between {start_date_str} and {end_date_str}.", parent=self.root)
                print(f"[INFO] No records found for export in the specified date range/property.")

        except pymongo.errors.PyMongoError as e:
            messagebox.showerror("Database Error", f"Failed to query records for export:\n{e}", parent=self.root)
            print(f"[ERROR] DB export query error: {e}")
        except IOError as e:
            messagebox.showerror("File Error", f"Failed to write CSV file:\n{e}", parent=self.root)
            print(f"[ERROR] File write export error: {e}")
        except Exception as e:
            messagebox.showerror("Export Error", f"An unexpected error occurred during export:\n{e}", parent=self.root)
            print(f"[ERROR] Export error: {e}")
            traceback.print_exc()

# --- Main Execution ---
if __name__ == "__main__":
    # Crucial check: Ensure DB connection was successful before starting GUI
    if db is None or client is None or parking_col is None or property_col is None or user_col is None or employee_col is None:
         print("[FATAL] Exiting: Database connection or collection initialization failed.")
         try:
             root_err = tk.Tk(); root_err.withdraw()
             messagebox.showerror("Startup Error", "Database connection or collection initialization failed. Cannot start application.\nPlease check config.ini and network connection.")
             root_err.destroy()
         except Exception as tk_err:
             print(f"[FATAL] Could not even display Tkinter error message: {tk_err}")
         sys.exit(1) # Exit if DB is not ready

    root = tk.Tk()
    app = ParkingApp(root)

    def on_closing():
        """Handles window close event, stops cameras, cancels timers, closes DB connection."""
        print("[INFO] Closing application requested...")
        if messagebox.askokcancel("Quit", "Are you sure you want to quit the Parking Management System?", parent=root):
            print("[INFO] Quitting application...")

            # Cancel the datetime update loop only if the main app was built
            if hasattr(app, 'datetime_after_id') and app.datetime_after_id:
                try:
                    root.after_cancel(app.datetime_after_id)
                    print("[INFO] Date/time update loop cancelled.")
                except tk.TclError:
                    print("[WARN] Could not cancel date/time update loop (already cancelled or window destroyed).")
                app.datetime_after_id = None

            # Stop cameras only if the main app was built and tabs exist
            if hasattr(app, 'entry_tab') and hasattr(app, 'exit_tab'):
                tabs_to_check = [app.entry_tab, app.exit_tab] # Add settings tab if it might have camera later
                # if hasattr(app, 'settings_tab'): tabs_to_check.append(app.settings_tab)

                for tab in tabs_to_check:
                    if tab and tab.winfo_exists() and hasattr(tab, 'stop_camera') and callable(getattr(tab, 'stop_camera')):
                        try:
                            print(f"[INFO] Stopping camera for tab: {tab.winfo_class()}...")
                            tab.stop_camera()
                        except Exception as e:
                            print(f"[ERROR] Error stopping camera during shutdown: {e}")

            # Close MongoDB connection
            global client
            if client:
                try:
                    client.close()
                    print("[INFO] MongoDB connection closed.")
                except Exception as e:
                    print(f"[ERROR] Error closing MongoDB connection: {e}")
                client = None # Clear the reference

            root.destroy() # Close the Tkinter window
            print("[INFO] Application closed.")
        else:
            print("[INFO] Quit cancelled.")

    # Bind the close window event (clicking the 'X') to our cleanup function
    root.protocol("WM_DELETE_WINDOW", on_closing)

    # Start the Tkinter main loop
    root.mainloop()
