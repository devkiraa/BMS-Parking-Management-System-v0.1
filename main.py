import os
import io
import uuid
import re
import csv
import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext, filedialog
from PIL import Image, ImageTk, UnidentifiedImageError
import cv2
from datetime import datetime, timedelta # Ensure timedelta is imported
import math # Import math for ceil
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
db = None
parking_col = None
property_col = None

try:
    client = MongoClient(MONGODB_URI, serverSelectionTimeoutMS=5000) # Add timeout
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
    # Suppress stderr temporarily to avoid OpenCV error messages for non-existent devices
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
        # Restore stderr
        os.dup2(original_stderr, sys.stderr.fileno())
        os.close(original_stderr)

    return cams

CAMERA_INDEXES = find_cameras(5)

# --- Google Cloud Vision OCR ---
def detect_text(image_path):
    """Detects text (potential number plate) in an image, handling standard Indian and BH series formats."""
    # Ensure client is initialized (it should be if GOOGLE_APPLICATION_CREDENTIALS is set)
    try:
        v_client = vision.ImageAnnotatorClient()
    except Exception as e:
        print(f"Error initializing Vision Client: {e}")
        return f"OCR Failed: Vision Client Init Error - {e}"

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
        # Using ^ and $ for stricter matching on the cleaned string

        # 1. Check for BH Series Format (e.g., 25BH4567AB)
        bh_match = re.search(r'^(\d{2})(BH)(\d{4})([A-Z]{1,2})$', compact_raw)
        if bh_match:
            year, bh_marker, nums, letters = bh_match.groups()
            formatted_plate = f"{year}-{bh_marker}-{nums}-{letters}" # Consistent format
            print(f"Formatted plate (BH series regex match): {formatted_plate}")
            return formatted_plate

        # 2. Check for Standard Indian Format (e.g., KA01AB1234 or MH05X9876)
        standard_match = re.search(r'^([A-Z]{2})(\d{1,2})([A-Z]{1,2})?(\d{3,4})$', compact_raw)
        if standard_match:
            state, rto, letters, nums = standard_match.groups()
            rto_padded = rto.rjust(2, '0')
            nums_padded = nums.rjust(4, '0')
            # Use 'XX' as placeholder if letters part is missing/not matched
            letters_formatted = letters if letters else 'XX'
            formatted_plate = f"{state}-{rto_padded}-{letters_formatted}-{nums_padded}"
            print(f"Formatted plate (Standard regex match): {formatted_plate}")
            return formatted_plate

        # 3. Fallback - Return the cleaned compact text if it seems plausible
        print("No structured regex (BH or Standard) matched.")
        if 4 <= len(compact_raw) <= 10: # Basic length check
            print("Returning cleaned compact text as potential plate.")
            return compact_raw
        else:
            print("Cleaned text doesn't resemble a typical plate length. Returning empty.")
            return "" # Return empty if it's unlikely to be a plate

    except vision.exceptions.GoogleCloudError as e:
        print(f"Vision API Error: {e}")
        return f"OCR Failed: API Error - {e}"
    except FileNotFoundError:
        print(f"Error: Image file not found at {image_path}")
        return f"OCR Failed: File not found"
    except Exception as e:
        print(f"Error during text detection or processing: {e}")
        # Log the full traceback for debugging if needed
        # import traceback
        # traceback.print_exc()
        return f"OCR Failed: {e}"


# --- Editable Dialog for Plate Correction ---
class EditableDialog(tk.Toplevel):
    def __init__(self, master, img_path, plate, on_confirm, on_retake):
        super().__init__(master)
        self.title("Confirm/Edit Number Plate")
        self.on_confirm, self.on_retake = on_confirm, on_retake
        self.transient(master) # Keep on top of master
        self.grab_set() # Modal behavior
        self.img_path = img_path # Store path for potential deletion

        # --- Image Display ---
        img_loaded = False
        try:
            # Open image using PIL
            img = Image.open(img_path);
            # Resize for display (using LANCZOS for better quality)
            img.thumbnail((400,300), Image.Resampling.LANCZOS)
            # Convert to PhotoImage for Tkinter
            self.photo = ImageTk.PhotoImage(img)
            # Display in a Label
            tk.Label(self, image=self.photo).pack(padx=10,pady=10)
            img_loaded = True
        except FileNotFoundError:
             tk.Label(self, text=f"Error: Image file not found\n{img_path}", fg="red").pack(padx=10,pady=10)
        except UnidentifiedImageError:
             tk.Label(self, text=f"Error: Cannot identify image file\n{img_path}", fg="red").pack(padx=10,pady=10)
        except Exception as e:
             tk.Label(self, text=f"Unexpected error loading image: {e}", fg="red").pack(padx=10,pady=10)
             print(f"Error loading image in dialog: {e}")

        if not img_loaded:
            self.photo = None # Ensure photo attribute exists even if loading fails

        # --- Plate Entry ---
        tk.Label(self, text="Detected/Enter Plate:", font=('Segoe UI',12)).pack(pady=(0,5))
        self.entry = tk.Entry(self, font=('Segoe UI',14), justify='center', width=20)
        self.entry.insert(0, plate if not plate.startswith("OCR Failed") else "") # Pre-fill if valid detection
        self.entry.pack(pady=(0,10), padx=10)
        self.entry.focus_set() # Set focus to the entry field
        self.entry.selection_range(0, tk.END) # Select existing text

        # --- Buttons ---
        btn_frame = ttk.Frame(self)
        btn_frame.pack(pady=10)
        ttk.Button(btn_frame, text="✅ Confirm", command=self._confirm, style="Accent.TButton").pack(side="left", padx=10)
        ttk.Button(btn_frame, text="🔄 Retake",  command=self._retake).pack(side="left", padx=10)

        # --- Bindings ---
        self.protocol("WM_DELETE_WINDOW", self._retake) # Treat closing window as retake
        self.bind("<Return>", self._confirm) # Bind Enter key to confirm
        self.bind("<Escape>", self._retake) # Bind Escape key to retake
        # Bind destroy event AFTER the mainloop starts processing it
        self.bind("<Destroy>", self._delete_image_file_on_destroy)

    def _delete_image_file_on_destroy(self, event):
        """Deletes the temporary image file when the dialog widget is destroyed."""
        # Ensure this runs only when the dialog itself is destroyed
        if event.widget == self:
            if self.img_path and os.path.exists(self.img_path):
                try:
                    os.remove(self.img_path)
                    print(f"Deleted temporary image file: {self.img_path}")
                except Exception as e:
                    print(f"Error deleting temporary image file {self.img_path}: {e}")
            else:
                print(f"Temporary image file not found or already deleted: {self.img_path}")


    def _confirm(self, event=None): # Add event=None to handle binding
        """Handles confirmation action."""
        plate = self.entry.get().strip().upper() # Get, clean, and uppercase
        # Basic validation
        if not plate:
            messagebox.showwarning("Input Required","Please enter a number plate.", parent=self)
            return
       
        # Allow only alphanumeric characters (can be adjusted)
        # if not re.fullmatch(r'[A-Z0-9]+', plate):
        #      messagebox.showwarning("Invalid Format", "Plate should contain only letters (A-Z) and numbers (0-9).", parent=self)
        #      return
        # Optional: Add length check if desired
        # if not (4 <= len(plate) <= 10):
        #     messagebox.showwarning("Invalid Length", "Plate length seems incorrect.", parent=self)
        #     return

        self.on_confirm(plate) # Call the confirmation callback
        self.destroy() # Close the dialog

    def _retake(self, event=None): # Add event=None to handle binding
        """Handles retake action."""
        self.on_retake() # Call the retake callback
        self.destroy() # Close the dialog


# --- Main Application Class ---
class ParkingApp:
    def __init__(self, root):
        self.root = root
        root.title("🚗 Parking Management System")
        # Set a minimum size and allow resizing
        root.minsize(1000, 650)
        root.geometry("1100x700") # Start slightly larger
        root.configure(bg="#f0f0f0")
        self._make_styles()

        # --- Main Structure ---
        self.nav = ttk.Notebook(root)
        self.entry_tab = ttk.Frame(self.nav, padding=10) # Add padding to frames
        self.exit_tab  = ttk.Frame(self.nav, padding=10)
        self.nav.add(self.entry_tab, text="🚙 Entry")
        self.nav.add(self.exit_tab, text="🏁 Exit")
        self.nav.pack(fill="both", expand=True, padx=5, pady=5)

        # Build UI for each tab
        self._build_tab(self.entry_tab, True)
        self._build_tab(self.exit_tab, False)

        # Bind events
        self.nav.bind("<<NotebookTabChanged>>", self._on_tab_change)
        self.root.bind('<Return>', self._on_enter_press) # Bind Enter globally

        # Start camera for the initially selected tab after a short delay
        self.root.after(150, self._trigger_initial_camera_start)

    def _on_enter_press(self, event):
        """Handles the Enter key press to trigger capture on the current tab."""
        # Check if focus is on an Entry widget, if so, do nothing (allow typing)
        focused_widget = self.root.focus_get()
        if isinstance(focused_widget, (tk.Entry, ttk.Entry, scrolledtext.ScrolledText)):
            return # Don't trigger capture if typing in an entry/text box

        # Find the currently selected tab widget
        try:
            current_tab_name = self.nav.select()
            if not current_tab_name: return # No tab selected
            current_tab_widget = self.nav.nametowidget(current_tab_name)

            # If the tab widget has a method to trigger capture, call it
            if hasattr(current_tab_widget, 'trigger_capture') and callable(getattr(current_tab_widget, 'trigger_capture')):
                current_tab_widget.trigger_capture()
            else:
                print("Current tab widget does not have 'trigger_capture' method.")
        except tk.TclError:
            print("Error getting current tab widget (might be during setup/teardown).")
        except Exception as e:
            print(f"Error during Enter press handling: {e}")


    def _on_tab_change(self, event):
        """Handles tab changes by stopping the old camera and starting the new one."""
        # Stop camera on the previously active tab (if any)
        # Note: This logic might be complex if tabs are added/removed dynamically.
        # It assumes only entry_tab and exit_tab exist.
        # A more robust way might involve tracking the previously selected tab index.
        for tab in (self.entry_tab, self.exit_tab):
             if tab and hasattr(tab, 'stop_camera') and callable(getattr(tab, 'stop_camera')):
                 # Check if the tab is *not* the newly selected one
                 try:
                     if self.nav.select() != str(tab): # Compare string representations
                         tab.stop_camera()
                 except Exception as e:
                     print(f"Error stopping camera on non-active tab change: {e}")


        # Start camera on the newly selected tab
        try:
            current_tab_name = self.nav.select()
            if not current_tab_name: return
            widget = self.nav.nametowidget(current_tab_name)
            if widget and hasattr(widget,'start_camera') and callable(getattr(widget, 'start_camera')):
                widget.start_camera()
        except tk.TclError:
             print("Error getting current tab widget for starting camera (might be during setup/teardown).")
        except Exception as e:
            print(f"Error starting camera on tab change: {e}")

    def _trigger_initial_camera_start(self):
        """Trigger the start_camera for the initially selected tab."""
        try:
            current_tab_name = self.nav.select()
            if not current_tab_name: return
            current_tab_widget = self.nav.nametowidget(current_tab_name)
            if current_tab_widget and hasattr(current_tab_widget, 'start_camera') and callable(getattr(current_tab_widget, 'start_camera')):
                current_tab_widget.start_camera()
        except tk.TclError:
             print("Error getting initial tab widget (might be during setup).")
        except Exception as e:
            print(f"Error starting camera for initial tab: {e}")
            # Try to display error on canvas if it exists
            if hasattr(current_tab_widget, '_canvas'):
                current_tab_widget._canvas.config(text=f"Cam Start Error: {e}", image='')
                current_tab_widget._canvas.imgtk = None # Clear image reference

    def _make_styles(self):
        """Configures ttk styles for a more modern look."""
        s = ttk.Style()
        s.theme_use('clam') # 'clam', 'alt', 'default', 'classic'

        # General widget styling
        s.configure(".", font=('Segoe UI', 10), background="#f0f0f0") # Default font and background
        s.configure("TLabel", background="#f0f0f0", foreground="#333") # Standard labels
        s.configure("TEntry", fieldbackground="white", foreground="#333")
        s.configure("TCombobox", fieldbackground="white", foreground="#333")
        s.map("TCombobox", fieldbackground=[('readonly','white')]) # Ensure readonly bg is white

        # Notebook styling
        s.configure("TNotebook", background="#e1e1e1", borderwidth=0)
        s.configure("TNotebook.Tab", padding=[12, 8], font=('Segoe UI', 11, 'bold'), background="#d0d0d0", foreground="#444")
        s.map("TNotebook.Tab",
              background=[("selected", "#f0f0f0"), ("active", "#e8e8e8")], # Selected tab matches frame bg
              foreground=[("selected", "#0078d4"), ("active", "#333")])

        # Button styling
        s.configure("TButton", font=('Segoe UI', 10, 'bold'), padding=(10, 6),
                    background="#e1e1e1", foreground="#333", borderwidth=1, relief="raised")
        s.map("TButton",
              background=[('active', '#c0c0c0'), ('disabled', '#d9d9d9')],
              foreground=[('disabled', '#a3a3a3')])

        # Special Accent Button (for Confirm/Capture)
        s.configure("Accent.TButton", font=('Segoe UI', 11, 'bold'), background="#0078d4", foreground="white")
        s.map("Accent.TButton",
              background=[('active', '#005a9e'), ('disabled', '#b0b0b0')],
              foreground=[('disabled', '#f0f0f0')])

        # Video Canvas Label Style
        s.configure("VideoCanvas.TLabel", background="black", foreground="white", font=('Segoe UI', 14), anchor="center")

        # Log Text Style
        # ScrolledText is a Tk widget, not Ttk, styling is done directly
        # See _build_tab where log widget is created

    def _build_tab(self, frame, is_entry):
        """Builds the UI elements for a single tab (Entry or Exit)."""
        # Configure grid weights for resizing behavior
        frame.columnconfigure(0, weight=2) # Left side (video + controls) takes more space
        frame.columnconfigure(1, weight=1) # Right side (logs) takes less space
        frame.rowconfigure(0, weight=1)    # Allow vertical expansion

        section = "Entry" if is_entry else "Exit"

        # --- Left Side Frame ---
        left_frame = ttk.Frame(frame, padding=5)
        left_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 5))
        left_frame.columnconfigure(0, weight=1) # Make column 0 expandable
        # Configure rows within the left frame
        left_frame.rowconfigure(0, weight=0) # Property row
        left_frame.rowconfigure(1, weight=0) # Slots row
        left_frame.rowconfigure(2, weight=0) # Camera row
        left_frame.rowconfigure(3, weight=1) # Canvas row - Allow vertical expansion
        left_frame.rowconfigure(4, weight=0) # Button row

        # --- Property Selection ---
        prop_frame = ttk.Frame(left_frame)
        prop_frame.grid(row=0, column=0, sticky="ew", pady=(0, 5))
        ttk.Label(prop_frame, text="Property:", width=8).pack(side="left", padx=(0, 5))
        prop_var = tk.StringVar()
        try:
            props = list(property_col.find({}, {"name": 1})) # Fetch only names initially
            names = [p['name'] for p in props if 'name' in p]
        except Exception as e:
            print(f"Error fetching properties: {e}")
            names = []
            messagebox.showerror("DB Error", f"Could not fetch properties: {e}")

        cbp = ttk.Combobox(prop_frame, textvariable=prop_var, values=names, state="readonly", width=30)
        property_available = bool(names)
        if property_available:
            cbp.current(0)
        else:
            cbp.set("No Properties Found")
            cbp.config(state="disabled")
        cbp.pack(side="left", fill="x", expand=True)

        # --- Available Slots Display ---
        slots_lbl = ttk.Label(left_frame, text="Slots: N/A", font=('Segoe UI', 10))
        slots_lbl.grid(row=1, column=0, sticky="w", pady=2, padx=5)

        def refresh_slots(*args):
            selected_prop_name = prop_var.get()
            if selected_prop_name and selected_prop_name != "No Properties Found":
                try:
                    # Fetch the specific document for slot info
                    doc = property_col.find_one({"name": selected_prop_name}, {"available_parking_spaces": 1, "parking_spaces": 1})
                    if doc:
                        avail = doc.get('available_parking_spaces', 'N/A')
                        total = doc.get('parking_spaces', 'N/A')
                        slots_lbl.config(text=f"Slots Available: {avail} / {total}")
                    else:
                        slots_lbl.config(text="Slots: Error (Not Found)")
                except Exception as e:
                    slots_lbl.config(text="Slots: DB Error")
                    print(f"Database error during slots refresh: {e}")
            else:
                slots_lbl.config(text="Slots: N/A")

        prop_var.trace_add('write', refresh_slots)
        if property_available: refresh_slots() # Initial call

        # --- Camera Selection ---
        cam_frame = ttk.Frame(left_frame)
        cam_frame.grid(row=2, column=0, sticky="ew", pady=2)
        ttk.Label(cam_frame, text="Camera:", width=8).pack(side="left", padx=(0, 5))
        cam_var = tk.IntVar()
        cam_values = CAMERA_INDEXES if CAMERA_INDEXES else ["N/A"]
        cam_state = "readonly" if CAMERA_INDEXES else "disabled"

        cbcam = ttk.Combobox(cam_frame, textvariable=cam_var, values=cam_values, state=cam_state, width=5)
        if CAMERA_INDEXES:
            cam_var.set(CAMERA_INDEXES[0]) # Set default if cameras exist
        else:
            cam_var.set(-1) # Or some indicator for no camera
        cbcam.pack(side="left")

        # --- Video Canvas ---
        canvas = ttk.Label(left_frame, text="Initializing camera...", style="VideoCanvas.TLabel")
        canvas.grid(row=3, column=0, sticky="nsew", pady=5, padx=5)

        # --- Capture Button ---
        btn_capture = ttk.Button(left_frame, text="📸 Capture & Process", style="Accent.TButton")
        # Command is set later after trigger_capture is defined

        if not property_available or not CAMERA_INDEXES:
            btn_capture.config(state="disabled")
            if not property_available and not CAMERA_INDEXES:
                 btn_capture.config(text="🚫 Setup Required")
            elif not property_available:
                 btn_capture.config(text="🚫 Add Property First")
            else: # No cameras
                 btn_capture.config(text="🚫 No Camera Found")

        btn_capture.grid(row=4, column=0, pady=10)

        # --- Right Side Frame (Logs) ---
        right_frame = ttk.Frame(frame, padding=5)
        right_frame.grid(row=0, column=1, sticky="nsew", padx=(5, 0))
        right_frame.columnconfigure(0, weight=1)
        right_frame.rowconfigure(1, weight=1) # Log area expands

        # --- Log Controls (Clear/Export) ---
        log_ctrl_frame = ttk.Frame(right_frame)
        log_ctrl_frame.grid(row=0, column=0, sticky="ew", pady=(0, 5))
        clear_btn = ttk.Button(log_ctrl_frame, text="🗑 Clear Logs", width=12)
        clear_btn.pack(side="left", padx=(0, 5))
        export_btn = ttk.Button(log_ctrl_frame, text="⬇️ Export CSV", width=12)
        export_btn.pack(side="left")

        # --- Log Display ---
        log = scrolledtext.ScrolledText(right_frame, width=40, height=15,
                                        font=("Consolas", 10), wrap=tk.WORD,
                                        bg="#ffffff", fg="#333333", relief="solid", borderwidth=1)
        log.grid(row=1, column=0, sticky="nsew")
        log.insert("end", f"📄 {section} Log History:\n" + "="*25 + "\n")

        # Set commands for log control buttons
        clear_btn.config(command=lambda: log.delete('1.0', 'end')) # Simple clear
        export_btn.config(command=lambda: self._export(log, section))

        # --- Store references and state on the frame widget itself ---
        frame._state = {'cap': None, 'frame': None, 'after_id': None}
        frame._log = log
        frame._canvas = canvas
        frame._prop_var = prop_var # Store prop_var for refresh_slots access
        frame._refresh_slots = refresh_slots # Store function reference

        # --- Camera Handling Functions (defined within _build_tab) ---
        def update_feed():
            """Updates the video feed on the canvas."""
            cap = frame._state.get('cap') # Use get for safety
            if cap is None or not cap.isOpened():
                # Ensure loop stops if camera becomes unavailable
                if frame._state.get('after_id') is not None:
                    frame._canvas.after_cancel(frame._state['after_id'])
                    frame._state['after_id'] = None
                return

            try:
                ok, frm = cap.read()
                if ok and frm is not None:
                    frame._state['frame'] = frm # Store the latest raw frame

                    # Convert color space for PIL
                    img_rgb = cv2.cvtColor(frm, cv2.COLOR_BGR2RGB)
                    img_pil = Image.fromarray(img_rgb)

                    # --- Display PhotoImage without resizing to fill canvas ---
                    # Calculate aspect ratio to fit within the canvas widget dimensions
                    canvas_w = frame._canvas.winfo_width()
                    canvas_h = frame._canvas.winfo_height()

                    # Prevent division by zero if canvas hasn't been drawn yet
                    if canvas_w <= 1 or canvas_h <= 1:
                         # If canvas size is not determined, use a default or skip update
                         frame._state['after_id'] = frame._canvas.after(100, update_feed) # Try again later
                         return

                    img_pil.thumbnail((canvas_w, canvas_h), Image.Resampling.LANCZOS)
                    photo = ImageTk.PhotoImage(img_pil)

                    # Update the label's image
                    frame._canvas.imgtk = photo # Keep reference to prevent garbage collection
                    frame._canvas.config(image=photo, text="") # Set image, clear any text
                else:
                    # Handle case where read fails but camera might still be open
                    print(f"Warning: Frame read failed for camera {cam_var.get()}")
                    # Optionally display a message on the canvas
                    # frame._canvas.config(image='', text="Frame Read Error")
                    # frame._canvas.imgtk = None

            except Exception as e:
                print(f"Error in update_feed for camera {cam_var.get()}: {e}")
                # Optionally stop camera or display error
                stop_camera()
                frame._canvas.config(image='', text=f"Feed Error: {e}")
                frame._canvas.imgtk = None
                return # Stop the loop on error

            # Schedule the next update
            frame._state['after_id'] = frame._canvas.after(40, update_feed) # Approx 25 FPS

        def start_camera(event=None):
            """Starts or restarts the selected camera."""
            stop_camera() # Ensure previous camera is stopped

            selected_cam_index = cam_var.get()
            if selected_cam_index == -1 or not isinstance(selected_cam_index, int): # Check if a valid index is selected
                 frame._canvas.config(text="No Camera Selected/Available", image='')
                 frame._canvas.imgtk = None
                 btn_capture.config(state="disabled", text="🚫 Select Camera")
                 return

            log.insert("end", f"⏳ Initializing camera {selected_cam_index}...\n")
            log.see("end")
            frame._canvas.config(text=f"Starting Camera {selected_cam_index}...", image='')
            frame._canvas.imgtk = None
            self.root.update_idletasks() # Update UI to show message

            cap = cv2.VideoCapture(selected_cam_index)
            time.sleep(0.5) # Give camera time to initialize

            if not cap.isOpened():
                messagebox.showerror("Camera Error", f"Cannot open camera index {selected_cam_index}", parent=self.root)
                log.insert("end", f"❌ Failed to open camera {selected_cam_index}\n")
                log.see("end")
                frame._state['cap'] = None
                frame._canvas.config(text="Failed to Open Camera", image='')
                frame._canvas.imgtk = None
                btn_capture.config(state="disabled", text="🚫 Camera Error")
                return

            # --- Test Read ---
            test_frame = None
            read_success = False
            try:
                # Try reading a few frames to ensure it's working
                for _ in range(5):
                    ok, test_frame = cap.read()
                    if ok and test_frame is not None:
                        read_success = True
                        break
                    time.sleep(0.1) # Short delay between attempts

                if not read_success:
                    raise IOError("Failed to read initial frames after opening.")

            except Exception as e:
                cap.release()
                frame._state['cap'] = None
                messagebox.showerror("Camera Error", f"Error reading initial frames from camera {selected_cam_index}: {e}", parent=self.root)
                log.insert("end", f"❌ Failed initial read: {e}\n")
                log.see("end")
                frame._canvas.config(text="Camera Read Error", image='')
                frame._canvas.imgtk = None
                btn_capture.config(state="disabled", text="🚫 Read Error")
                return
            # --- End Test Read ---

            frame._state['cap'] = cap
            log.insert("end", f"✅ {section} Camera {selected_cam_index} started successfully.\n")
            log.see("end")
            frame._canvas.config(text="") # Clear status text

            # Enable capture button only if property is also selected
            if prop_var.get() and prop_var.get() != "No Properties Found":
                btn_capture.config(state="normal", text="📸 Capture & Process")
            else:
                 btn_capture.config(state="disabled", text="🚫 Select Property")

            update_feed() # Start the video feed loop

        def stop_camera():
            """Stops the current camera and cancels the update loop."""
            if frame._state.get('after_id') is not None:
                frame._canvas.after_cancel(frame._state['after_id'])
                frame._state['after_id'] = None

            cap = frame._state.get('cap')
            if cap and cap.isOpened():
                cap.release()
                frame._state['cap'] = None
                log.insert("end", f"🛑 {section} Camera stopped.\n")
                log.see("end")

            # Clear canvas and disable button
            frame._canvas.config(image='', text="Camera Stopped")
            frame._canvas.imgtk = None # Clear reference
            # Keep button disabled unless explicitly started again
            if btn_capture['state'] != 'disabled':
                 btn_capture.config(state="disabled", text="🚫 Camera Stopped")

        # --- Define and Attach Capture Trigger ---
        def trigger_capture_local():
             self._capture_and_edit(
                 frame, # Pass the specific tab frame
                 is_entry,
                 log,
                 prop_var.get(),
                 refresh_slots, # Pass the refresh function
                 btn_capture # Pass the button itself
             )
        frame.trigger_capture = trigger_capture_local # Attach to frame for global access
        btn_capture.config(command=trigger_capture_local) # Set button command

        # Attach start/stop methods to the frame for external control (e.g., tab change)
        frame.start_camera = start_camera
        frame.stop_camera = stop_camera

        # Bind camera selection change to restart the camera
        cbcam.bind("<<ComboboxSelected>>", start_camera)

        # Load initial logs for this tab
        self._load_logs(log, is_entry)


    def _capture_and_edit(self, tab_frame, is_entry, log, prop_name, refresh_slots, btn):
        """Captures frame, detects text, shows edit dialog, and saves on confirm."""
        if not prop_name or prop_name == "No Properties Found":
            messagebox.showwarning("Property Required", "Please select a property before capturing.", parent=self.root)
            return

        # Disable button during processing
        original_btn_text = btn['text']
        btn.config(state="disabled", text="⏳ Capturing...")
        self.root.update_idletasks()

        cap = tab_frame._state.get('cap')
        if cap is None or not cap.isOpened():
            messagebox.showwarning("No Camera", "Camera is not available or not running.", parent=self.root)
            btn.config(state="normal", text=original_btn_text) # Re-enable with original text
            # Try restarting camera if possible
            if hasattr(tab_frame, 'start_camera'): tab_frame.start_camera()
            return

        log.insert("end", "📸 Capturing current frame...\n")
        log.see("end")

        # --- Capture Frame ---
        # Use the frame already stored in the state by update_feed for less delay
        captured_frame = tab_frame._state.get('frame')

        # Fallback: If no frame in state, try one last read
        if captured_frame is None:
            print("No frame in state, attempting final read...")
            try:
                ok, captured_frame = cap.read()
                if not ok or captured_frame is None:
                     raise IOError("Final frame read failed.")
            except Exception as e:
                 messagebox.showerror("Capture Error", f"Failed to capture frame: {e}", parent=self.root)
                 log.insert("end", f"❌ Capture error: {e}\n")
                 log.see("end")
                 btn.config(state="normal", text=original_btn_text)
                 return
        # --- End Capture Frame ---

        # --- Save Frame Temporarily ---
        path = None # Initialize path
        try:
            os.makedirs(ASSETS_DIR, exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f") # Add microseconds
            filename = f"capture_{timestamp}_{uuid.uuid4().hex[:6]}.jpg"
            path = os.path.join(ASSETS_DIR, filename)

            # Use high quality JPEG saving
            success = cv2.imwrite(path, captured_frame, [cv2.IMWRITE_JPEG_QUALITY, 95])
            if not success:
                raise IOError(f"cv2.imwrite failed to save to {path}")

            log.insert("end", f"💾 Frame saved: {filename}\n")
            log.see("end")
            btn.config(text="⏳ Detecting...")
            self.root.update_idletasks()

        except Exception as e:
            messagebox.showerror("File Save Error", f"Could not save captured image: {e}", parent=self.root)
            log.insert("end", f"❌ Image save error: {e}\n")
            log.see("end")
            btn.config(state="normal", text=original_btn_text)
            return
        # --- End Save Frame ---

        # --- Detect Text ---
        plate = detect_text(path) # Call OCR function
        log.insert("end", f"🔍 OCR Result: '{plate}'\n" if plate else "🔍 OCR Result: No plate detected\n")
        log.see("end")
        # --- End Detect Text ---

        # --- Define Callbacks for Dialog ---
        def on_confirm_dialog(edited_plate):
            """Callback when user confirms in the dialog."""
            log.insert("end", f"✅ Confirmed Plate: {edited_plate}\n")
            log.see("end")
            # Proceed to save the record
            self._save_record(edited_plate, is_entry, log, prop_name, refresh_slots)
            # Button is re-enabled by the dialog's <Destroy> binding

        def on_retake_dialog():
            """Callback when user requests retake from the dialog."""
            log.insert("end", "🔄 Retake requested by user.\n")
            log.see("end")
            # Button is re-enabled by the dialog's <Destroy> binding

        # --- Show Editable Dialog ---
        try:
            dialog = EditableDialog(self.root, path, plate, on_confirm_dialog, on_retake_dialog)
            # --- IMPORTANT: Re-enable button when dialog is destroyed ---
            # Use add='+' to ensure this binding doesn't overwrite the dialog's internal one
            dialog.bind("<Destroy>", lambda e: btn.config(state="normal", text=original_btn_text), add="+")
            self.root.wait_window(dialog) # Wait for the dialog to close

        except Exception as e:
             messagebox.showerror("Dialog Error", f"Failed to open edit dialog: {e}", parent=self.root)
             log.insert("end", f"❌ Dialog error: {e}\n")
             log.see("end")
             btn.config(state="normal", text=original_btn_text) # Re-enable button on error
             # Clean up temp file if dialog failed to open
             if path and os.path.exists(path):
                 try: os.remove(path)
                 except Exception as del_e: print(f"Error cleaning up {path}: {del_e}")


    # <<< --- THIS IS THE UPDATED _save_record METHOD --- >>>
    def _save_record(self, plate, is_entry, log, prop_name, refresh_slots_func):
        """Saves or updates parking record in the database with HOURLY fee logic."""
        now = datetime.now()
        log.insert("end", f"💾 Attempting to save {'entry' if is_entry else 'exit'} for {plate}...\n")
        log.see("end")

        # Validate plate format again before saving (optional but good practice)
        if not re.fullmatch(r'[A-Z0-9\-]+', plate): # Allow hyphens from formatted plates
             messagebox.showerror("Save Error", f"Invalid plate format '{plate}' provided for saving.", parent=self.root)
             log.insert("end", f"❌ Save aborted: Invalid plate format '{plate}'.\n")
             log.see("end")
             return

        try:
            # Find the property details using the provided name
            prop = property_col.find_one({"name": prop_name})
            if not prop:
                messagebox.showerror("Property Error", f"Property '{prop_name}' not found in the database. Cannot save record.", parent=self.root)
                log.insert("end", f"❌ Property '{prop_name}' not found in DB during save.\n")
                log.see("end")
                return

            pid = prop['_id'] # Get the property's MongoDB ObjectId

            if is_entry:
                # --- Handle Vehicle Entry ---

                # 1. Check for existing open entry for this vehicle at this property
                existing_entry = parking_col.find_one({
                    "vehicle_no": plate,
                    "property_id": str(pid), # Store property_id as string for consistency? Or keep as ObjectId? Let's use str(pid) for now.
                    "exit_time": None        # Crucial condition: only find records without an exit time
                })
                if existing_entry:
                    messagebox.showwarning("Duplicate Entry", f"Vehicle {plate} already has an active parking session at {prop_name}.", parent=self.root)
                    log.insert("end", f"⚠️ Duplicate entry attempt for {plate}. Already parked.\n")
                    log.see("end")
                    return

                # 2. Check for available parking spaces (re-fetch latest count)
                latest_prop = property_col.find_one({"_id": pid}, {"available_parking_spaces": 1})
                if not latest_prop or latest_prop.get('available_parking_spaces', 0) <= 0:
                    messagebox.showwarning("Parking Full", f"No parking slots currently available at {prop_name}.", parent=self.root)
                    log.insert("end", f"❌ Parking full at {prop_name}. Entry denied for {plate}.\n")
                    log.see("end")
                    return

                # 3. Create and insert the new parking record
                new_record = {
                    "parking_id": str(uuid.uuid4()), # Unique ID for this parking event
                    "property_id": str(pid),         # Link to the property
                    "vehicle_no": plate,
                    "entry_time": now,               # Record current time as entry time
                    "exit_time": None,               # Null exit time signifies active session
                    "fee": 0,                        # Initial fee is zero
                    "mode_of_payment": None          # Payment mode set on exit/payment
                    # Add other relevant fields if needed (e.g., entry_gate_id)
                }
                insert_result = parking_col.insert_one(new_record)

                # 4. Decrement available parking spaces for the property
                update_result = property_col.update_one(
                    {"_id": pid},
                    {"$inc": {"available_parking_spaces": -1}}
                )

                # 5. Log success and show confirmation
                if insert_result.inserted_id and update_result.modified_count > 0:
                    log.insert("end", f"🟢 Entry recorded: {plate} @ {now:%Y-%m-%d %H:%M:%S}\n")
                    messagebox.showinfo("Entry Success", f"Vehicle {plate} entry recorded successfully at {prop_name}.", parent=self.root)
                else:
                     log.insert("end", f"⚠️ Entry DB update issue for {plate}. Check DB consistency.\n")
                     messagebox.showwarning("DB Warning", "Entry recorded, but slot count update might have failed.", parent=self.root)


            else: # is_exit
                # --- Handle Vehicle Exit ---

                # 1. Find the latest open entry for this vehicle at this property
                #    Update its exit_time and return the updated document
                updated_doc = parking_col.find_one_and_update(
                    {
                        "vehicle_no": plate,
                        "exit_time": None,       # Find the active session
                        "property_id": str(pid)
                    },
                    {
                        "$set": {"exit_time": now} # Set the current time as exit time
                    },
                    sort=[('entry_time', -1)], # Get the latest entry if duplicates somehow exist
                    return_document=pymongo.ReturnDocument.AFTER # Return the document *after* the update
                )

                if updated_doc:
                    entry_time = updated_doc.get('entry_time')
                    calculated_fee = 0 # Initialize fee

                    # 2. Calculate Fee based on duration (if entry_time is valid)
                    if entry_time and isinstance(entry_time, datetime):
                        duration = now - entry_time
                        total_hours = duration.total_seconds() / 3600 # Duration in hours

                        # Get the hourly fee from the property document
                        # IMPORTANT: Ensure 'fee_per_hour' field exists in your property collection!
                        fee_per_hour = prop.get('fee_per_hour', 10.0) # Default to 10.0 if not found
                        if not isinstance(fee_per_hour, (int, float)) or fee_per_hour < 0:
                             print(f"Warning: Invalid fee_per_hour ({fee_per_hour}) for property {prop_name}. Using default 10.0.")
                             fee_per_hour = 10.0 # Fallback to default if invalid type/value

                        if total_hours <= 1.0:
                            # First hour is free
                            calculated_fee = 0.0
                        else:
                            # Calculate chargeable hours: round total hours UP, then subtract the 1 free hour
                            chargeable_hours = math.ceil(total_hours) - 1
                            calculated_fee = chargeable_hours * fee_per_hour

                        # Ensure fee is non-negative and format as float
                        calculated_fee = round(max(0.0, calculated_fee), 2)

                        # 3. Update the fee in the parking record
                        parking_col.update_one(
                            {"_id": updated_doc["_id"]},
                            {"$set": {"fee": calculated_fee}}
                            # Optionally update payment mode here if known:
                            # "$set": {"fee": calculated_fee, "mode_of_payment": "Cash"}
                        )
                        log.insert("end", f"💲 Fee calculated: ₹{calculated_fee:.2f} for {total_hours:.2f} hours.\n")
                    else:
                         log.insert("end", f"⚠️ Could not calculate fee for {plate}: Invalid entry time found.\n")
                         messagebox.showwarning("Fee Warning", "Could not calculate fee due to missing entry time.", parent=self.root)


                    # 4. Increment available parking spaces
                    property_col.update_one(
                        {"_id": pid},
                        {"$inc": {"available_parking_spaces": 1}}
                    )

                    # 5. Log success and show confirmation with fee
                    log.insert("end", f"🔴 Exit recorded: {plate} @ {now:%Y-%m-%d %H:%M:%S} (Fee: ₹{calculated_fee:.2f})\n")
                    messagebox.showinfo("Exit Success", f"Exit recorded for {plate}.\nCalculated Fee: ₹{calculated_fee:.2f}", parent=self.root)

                else:
                    # No open entry was found for this vehicle at this property
                    messagebox.showwarning("No Entry Found", f"No active parking session found for vehicle {plate} at {prop_name}.", parent=self.root)
                    log.insert("end", f"❌ Exit attempt failed: No open entry found for {plate} at {prop_name}.\n")

            # Always refresh slots and scroll log after entry or exit attempt
            log.see("end")
            if callable(refresh_slots_func):
                refresh_slots_func()
            else:
                 print("Warning: refresh_slots_func is not callable.")


        except pymongo.errors.ConnectionFailure as e:
             messagebox.showerror("Database Error", f"Database connection lost: {e}", parent=self.root)
             log.insert("end", f"❌ DB Connection Failure: {e}\n")
        except pymongo.errors.PyMongoError as e:
             messagebox.showerror("Database Error", f"A database error occurred: {e}", parent=self.root)
             log.insert("end", f"❌ DB Error during save: {e}\n")
        except Exception as e:
            # Catch any other unexpected errors
            messagebox.showerror("Unexpected Error", f"An unexpected error occurred while saving: {e}", parent=self.root)
            log.insert("end", f"❌ Unexpected Save Error: {e}\n")
            # import traceback # Optional detailed logging for debugging
            # traceback.print_exc()
        finally:
            log.see("end")
            # Ensure slots are refreshed even if there was an error during DB interaction
            if callable(refresh_slots_func):
                try:
                    refresh_slots_func()
                except Exception as refresh_e:
                    print(f"Error during final slot refresh: {refresh_e}")

    # <<< --- END OF UPDATED _save_record METHOD --- >>>


    def _load_logs(self, log_widget, is_entry):
        """Loads recent parking records into the specified log display."""
        log_widget.config(state=tk.NORMAL) # Enable writing
        log_widget.delete('1.0', 'end') # Clear previous content
        section = "Entry" if is_entry else "Exit"
        log_widget.insert("end", f"📄 {section} Log History:\n" + "="*25 + "\n")

        try:
            # Define query based on entry/exit tab
            query = {"exit_time": None} if is_entry else {"exit_time": {"$ne": None}}
            # Define sort key based on entry/exit tab
            sort_key = "entry_time" if is_entry else "exit_time"

            # Fetch recent records from MongoDB, limit to e.g., 20
            recent_records = parking_col.find(query).sort(sort_key, pymongo.DESCENDING).limit(20)

            count = 0
            records_list = list(recent_records) # Convert cursor to list to check count easily
            if not records_list:
                 log_widget.insert("end", f"No recent {section.lower()} records found.\n")
            else:
                for record in records_list:
                    ts_key = "entry_time" if is_entry else "exit_time"
                    ts = record.get(ts_key)
                    if ts and isinstance(ts, datetime):
                        icon = "🟢" if is_entry else "🔴"
                        plate = record.get('vehicle_no', 'N/A')
                        time_str = ts.strftime('%Y-%m-%d %H:%M:%S') # Consistent time format
                        log_line = f"{icon} {plate:<15} @ {time_str}" # Pad plate for alignment

                        # Add fee info for exit logs
                        if not is_entry:
                            fee = record.get('fee', None)
                            fee_str = f"₹{fee:.2f}" if isinstance(fee, (int, float)) else "N/A"
                            log_line += f" (Fee: {fee_str})"

                        log_widget.insert("end", f"{log_line}\n")
                        count += 1
                    else:
                         print(f"Skipping record due to missing/invalid timestamp: {record.get('_id')}")

            log_widget.see("end") # Scroll to the end

        except pymongo.errors.ConnectionFailure as e:
             log_widget.insert("end", f"\n❌ DB Connection Error loading logs: {e}\n")
        except Exception as e:
            log_widget.insert("end", f"\n❌ Error loading logs: {e}\n")
            # import traceback; traceback.print_exc() # For debugging
        finally:
             log_widget.config(state=tk.DISABLED) # Disable writing after loading


    def _export(self, log_widget, section):
        """Exports visible log content to a CSV file."""
        log_widget.config(state=tk.NORMAL) # Enable reading
        log_content = log_widget.get("1.0", "end").strip()
        log_widget.config(state=tk.DISABLED) # Disable again

        # Extract relevant lines (skip header)
        lines = [line for line in log_content.splitlines() if line.strip() and (line.startswith("🟢") or line.startswith("🔴"))]

        if not lines:
            messagebox.showinfo("Export Info", "No log entries found to export.", parent=self.root)
            return

        # Ask for save file path
        default_filename = f"{section.lower()}_logs_{datetime.now():%Y%m%d_%H%M}.csv"
        path = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
            title=f"Save {section} Logs As",
            initialfile=default_filename,
            parent=self.root
        )
        if not path: # User cancelled
            return

        try:
            with open(path, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                # Write header
                header = ["Action", "Plate", "Timestamp"]
                if section == "Exit":
                    header.append("Fee (₹)")
                writer.writerow(header)

                # Process each log line
                for line in lines:
                    # Regex to parse the structured log line
                    # Example: 🟢 MH12AB1234      @ 2023-10-27 10:30:00
                    # Example: 🔴 KA05XY9876      @ 2023-10-27 11:45:15 (Fee: ₹20.00)
                    pattern = r'([🟢🔴])\s+([A-Z0-9\-]+)\s+@\s+([\d\-]+\s+[\d:]+)(?:\s+\(Fee:\s*₹?([\d\.]+)\))?'
                    match = re.match(pattern, line.strip())

                    if match:
                        icon, plate, timestamp, fee = match.groups()
                        action = "Entry" if icon == "🟢" else "Exit"
                        row_data = [action, plate.strip(), timestamp.strip()]
                        if section == "Exit":
                            row_data.append(fee.strip() if fee else "") # Add fee if present
                        writer.writerow(row_data)
                    else:
                        print(f"Skipping malformed log line during export: {line}") # Log skipped lines

            messagebox.showinfo("Export Success", f"Logs successfully exported to:\n{path}", parent=self.root)

        except Exception as e:
            messagebox.showerror("Export Error", f"An error occurred during CSV export: {e}", parent=self.root)
            print(f"Export error: {e}")


# --- Main Execution ---
if __name__ == "__main__":
    if db is None or client is None:
         print("Exiting: Database connection not established.")
         sys.exit(1) # Exit if DB connection failed earlier

    root = tk.Tk()
    app = ParkingApp(root)

    def on_closing():
        """Gracefully handle application closing."""
        print("Closing application...")
        # Stop cameras on both tabs
        for tab in (app.entry_tab, app.exit_tab):
            if tab and hasattr(tab, 'stop_camera') and callable(getattr(tab, 'stop_camera')):
                try:
                    print(f"Stopping camera for tab: {tab}")
                    tab.stop_camera()
                except Exception as e:
                    print(f"Error stopping camera during shutdown for {tab}: {e}")

        # Close MongoDB connection
        global client
        if client:
            try:
                client.close()
                print("MongoDB connection closed.")
            except Exception as e:
                print(f"Error closing MongoDB connection: {e}")
            client = None # Ensure client is None after closing

        # Destroy the Tkinter window
        root.destroy()
        print("Application closed.")

    # Set the close protocol
    root.protocol("WM_DELETE_WINDOW", on_closing)
    # Start the Tkinter event loop
    root.mainloop()
