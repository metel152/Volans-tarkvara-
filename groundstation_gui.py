import tkinter as tk
from tkinter import ttk, messagebox
import serial
import serial.tools.list_ports
import threading
import queue
import csv
import os
import base64
import io
import math
from datetime import datetime
from PIL import Image, ImageTk


class AttitudeIndicator(tk.Canvas):
    def __init__(self, parent, app, width=420, height=420, **kwargs):
        super().__init__(
            parent,
            width=width,
            height=height,
            bg="#111111",
            highlightthickness=0,
            **kwargs
        )
        self.app = app
        self.w = width
        self.h = height
        self.cx = width / 2
        self.cy = height / 2
        self.radius = min(width, height) / 2 - 12

        self.sky_color = "#3A6DB4"
        self.ground_color = "#8B5A2B"
        self.hud_color = "#F0F0F0"
        self.accent_color = "#F8E45C"
        self.vv_color = "#7CFF7C"

    def _rot(self, x, y, deg):
        a = math.radians(deg)
        xr = x * math.cos(a) - y * math.sin(a)
        yr = x * math.sin(a) + y * math.cos(a)
        return xr, yr

    def _draw_bank_marks(self, cx, cy, r):
        bank_marks = [-60, -45, -30, -20, -10, 10, 20, 30, 45, 60]
        for ang in bank_marks:
            inner = r - 12
            outer = r - (26 if abs(ang) in (30, 60) else 19)
            x1 = cx + inner * math.sin(math.radians(ang))
            y1 = cy - inner * math.cos(math.radians(ang))
            x2 = cx + outer * math.sin(math.radians(ang))
            y2 = cy - outer * math.cos(math.radians(ang))
            self.create_line(x1, y1, x2, y2, fill=self.hud_color, width=2)

        pointer = [
            (cx, cy - r + 10),
            (cx - 8, cy - r + 24),
            (cx + 8, cy - r + 24),
        ]
        self.create_polygon(pointer, fill=self.accent_color, outline="")

    def _draw_fixed_aircraft_symbol(self, cx, cy):
        self.create_line(cx - 50, cy, cx - 14, cy, fill=self.hud_color, width=3)
        self.create_line(cx + 14, cy, cx + 50, cy, fill=self.hud_color, width=3)
        self.create_line(cx, cy - 10, cx, cy + 10, fill=self.hud_color, width=2)
        self.create_rectangle(cx - 4, cy - 4, cx + 4, cy + 4, outline=self.hud_color)

    def _draw_velocity_vector(self, cx, cy):
        vx = cx + self.app.vx
        vy = cy + self.app.vy
        self.create_oval(vx - 8, vy - 8, vx + 8, vy + 8, outline=self.vv_color, width=2)
        self.create_line(vx - 16, vy, vx - 8, vy, fill=self.vv_color, width=2)
        self.create_line(vx + 8, vy, vx + 16, vy, fill=self.vv_color, width=2)
        self.create_line(vx, vy - 16, vx, vy - 8, fill=self.vv_color, width=2)

    def draw_indicator(self, roll_deg, pitch_deg):
        self.delete("all")

        cx, cy, r = self.cx, self.cy, self.radius
        pitch_scale = 3.0
        pitch_px = pitch_deg * pitch_scale
        scale = 6 * r

        sky_rect = [
            (-scale, -scale - pitch_px),
            (scale, -scale - pitch_px),
            (scale, -pitch_px),
            (-scale, -pitch_px),
        ]

        ground_rect = [
            (-scale, -pitch_px),
            (scale, -pitch_px),
            (scale, scale - pitch_px),
            (-scale, scale - pitch_px),
        ]

        def transform(points):
            out = []
            for x, y in points:
                xr, yr = self._rot(x, y, -roll_deg)
                out.extend([cx + xr, cy + yr])
            return out

        self.create_polygon(transform(sky_rect), fill=self.sky_color, outline="", smooth=False)
        self.create_polygon(transform(ground_rect), fill=self.ground_color, outline="", smooth=False)

        x1, y1 = self._rot(-140, -pitch_px, -roll_deg)
        x2, y2 = self._rot(140, -pitch_px, -roll_deg)
        self.create_line(cx + x1, cy + y1, cx + x2, cy + y2, fill=self.accent_color, width=3)

        for mark in range(-30, 31, 5):
            if mark == 0:
                continue

            y = -mark * pitch_scale - pitch_px
            half = 38 if mark % 10 == 0 else 22

            lx1, ly1 = self._rot(-half, y, -roll_deg)
            lx2, ly2 = self._rot(half, y, -roll_deg)
            self.create_line(cx + lx1, cy + ly1, cx + lx2, cy + ly2, fill=self.hud_color, width=2)

            if mark % 10 == 0:
                txl, tyl = self._rot(-half - 18, y, -roll_deg)
                txr, tyr = self._rot(half + 18, y, -roll_deg)
                label = str(abs(mark))
                self.create_text(cx + txl, cy + tyl, text=label, fill=self.hud_color, font=("Segoe UI", 10))
                self.create_text(cx + txr, cy + tyr, text=label, fill=self.hud_color, font=("Segoe UI", 10))

        self._draw_bank_marks(cx, cy, r)
        self._draw_fixed_aircraft_symbol(cx, cy)
        self._draw_velocity_vector(cx, cy)

        self.create_oval(cx - r, cy - r, cx + r, cy + r, outline="#DDDDDD", width=3)

        self.create_text(
            cx,
            cy + r - 18,
            text=f"ROLL {roll_deg:+05.1f}°   PITCH {pitch_deg:+05.1f}°",
            fill="#DDDDDD",
            font=("Segoe UI", 10, "bold")
        )


class GroundStationApp:
    def __init__(self, root):
        self.root = root
        self.root.title("CanSat Ground Station")
        self.root.geometry("1460x900")
        self.root.configure(bg="#0F1115")

        base_dir = os.path.dirname(os.path.abspath(__file__))
        self.log_dir = os.path.join(base_dir, "groundstation_logs")
        self.preview_dir = os.path.join(self.log_dir, "previews")
        os.makedirs(self.preview_dir, exist_ok=True)

        self.telemetry_csv = os.path.join(self.log_dir, "telemetry.csv")
        self.raw_log = os.path.join(self.log_dir, "raw_packets.log")

        self.serial_port = None
        self.serial_thread = None
        self.running = False
        self.rx_queue = queue.Queue()

        self.packet_count = 0
        self.last_packet_time = None
        self.current_preview_photo = None
        self.image_buffers = {}

        self.roll = 0.0
        self.pitch = 0.0
        self.vx = 0.0
        self.vy = 0.0

        self.last_seq = None

        self._build_styles()
        self._build_ui()
        self.refresh_ports()
        self.root.after(100, self.process_queue)
        self.root.after(500, self.update_link_status)

    def _build_styles(self):
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except Exception:
            pass

        style.configure("TFrame", background="#0F1115")
        style.configure("TLabelframe", background="#151922", foreground="#E6EAF2")
        style.configure("TLabelframe.Label", background="#151922", foreground="#E6EAF2", font=("Segoe UI", 11, "bold"))
        style.configure("TLabel", background="#0F1115", foreground="#E6EAF2", font=("Segoe UI", 10))
        style.configure("Header.TLabel", background="#0F1115", foreground="#7FDBFF", font=("Segoe UI", 11, "bold"))
        style.configure("Value.TLabel", background="#151922", foreground="#FFFFFF", font=("Consolas", 12, "bold"))
        style.configure("TButton", font=("Segoe UI", 10))

    def _build_ui(self):
        top = ttk.Frame(self.root, padding=10)
        top.pack(fill="x")

        ttk.Label(top, text="COM Port:", style="Header.TLabel").pack(side="left")
        self.port_var = tk.StringVar()
        self.port_combo = ttk.Combobox(top, textvariable=self.port_var, width=18, state="readonly")
        self.port_combo.pack(side="left", padx=6)

        ttk.Button(top, text="Refresh", command=self.refresh_ports).pack(side="left", padx=4)

        ttk.Label(top, text="Baud:", style="Header.TLabel").pack(side="left", padx=(14, 0))
        self.baud_var = tk.StringVar(value="57600")
        ttk.Entry(top, textvariable=self.baud_var, width=10).pack(side="left", padx=6)

        self.connect_btn = ttk.Button(top, text="Connect", command=self.toggle_connection)
        self.connect_btn.pack(side="left", padx=10)

        self.deploy_btn = tk.Button(
            top,
            text="LANGEVARI",
            bg="#C62828",
            fg="white",
            activebackground="#E53935",
            activeforeground="white",
            font=("Segoe UI", 11, "bold"),
            command=self.manual_deploy
        )
        self.deploy_btn.pack(side="left", padx=12)

        self.status_var = tk.StringVar(value="Disconnected")
        self.packet_var = tk.StringVar(value="Packets: 0")
        self.last_rx_var = tk.StringVar(value="Last RX: -")
        self.link_var = tk.StringVar(value="Link: idle")

        ttk.Label(top, textvariable=self.status_var, style="Header.TLabel").pack(side="left", padx=(20, 10))
        ttk.Label(top, textvariable=self.packet_var).pack(side="left", padx=10)
        ttk.Label(top, textvariable=self.last_rx_var).pack(side="left", padx=10)
        ttk.Label(top, textvariable=self.link_var, style="Header.TLabel").pack(side="left", padx=10)

        main = ttk.Frame(self.root, padding=(10, 0, 10, 10))
        main.pack(fill="both", expand=True)

        left = ttk.Frame(main)
        left.pack(side="left", fill="both", expand=True)

        right = ttk.Frame(main)
        right.pack(side="right", fill="y", padx=(10, 0))

        cards = ttk.Frame(left)
        cards.pack(fill="x")

        self.card_vars = {}
        card_items = [
            ("seq", "SEQ"),
            ("temp", "TEMP °C"),
            ("pressure", "PRESS hPa"),
            ("alt", "ALT m"),
            ("peak_alt", "PEAK ALT"),
            ("deploy", "DEPLOY"),
            ("ax", "ACC X"),
            ("ay", "ACC Y"),
            ("az", "ACC Z"),
            ("gx", "GYRO X"),
            ("gy", "GYRO Y"),
            ("gz", "GYRO Z"),
        ]

        for i, (key, label) in enumerate(card_items):
            frame = ttk.LabelFrame(cards, text=label, padding=10)
            frame.grid(row=i // 6, column=i % 6, padx=5, pady=5, sticky="nsew")
            var = tk.StringVar(value="-")
            ttk.Label(frame, textvariable=var, style="Value.TLabel", width=12).pack()
            self.card_vars[key] = var

        for i in range(6):
            cards.columnconfigure(i, weight=1)

        ts_frame = ttk.LabelFrame(left, text="Timestamp", padding=10)
        ts_frame.pack(fill="x", pady=(8, 8))
        self.timestamp_var = tk.StringVar(value="-")
        ttk.Label(ts_frame, textvariable=self.timestamp_var, style="Value.TLabel").pack(anchor="w")

        lower = ttk.Frame(left)
        lower.pack(fill="both", expand=True)

        att_frame = ttk.LabelFrame(lower, text="Attitude Indicator", padding=10)
        att_frame.pack(side="left", fill="both", expand=True, padx=(0, 8))

        self.attitude = AttitudeIndicator(att_frame, self, width=420, height=420)
        self.attitude.pack(expand=True, fill="both")
        self.attitude.draw_indicator(0.0, 0.0)

        preview_frame = ttk.LabelFrame(lower, text="Latest Preview", padding=10)
        preview_frame.pack(side="right", fill="both", expand=True)

        self.preview_label = ttk.Label(preview_frame, text="No preview yet", anchor="center")
        self.preview_label.pack(fill="both", expand=True)

        console_frame = ttk.LabelFrame(right, text="Console", padding=10)
        console_frame.pack(fill="both", expand=True)

        self.console = tk.Text(
            console_frame,
            width=42,
            wrap="word",
            bg="#11151C",
            fg="#E6EAF2",
            insertbackground="white",
            relief="flat",
            font=("Consolas", 10)
        )
        self.console.pack(side="left", fill="both", expand=True)

        yscroll = ttk.Scrollbar(console_frame, orient="vertical", command=self.console.yview)
        yscroll.pack(side="right", fill="y")
        self.console.configure(yscrollcommand=yscroll.set)

    def log(self, text):
        ts = datetime.now().strftime("%H:%M:%S")
        self.console.insert("end", f"[{ts}] {text}\n")
        self.console.see("end")

    def refresh_ports(self):
        ports = [p.device for p in serial.tools.list_ports.comports()]
        self.port_combo["values"] = ports
        if ports and not self.port_var.get():
            self.port_var.set(ports[0])

    def toggle_connection(self):
        if self.running:
            self.disconnect()
        else:
            self.connect()

    def connect(self):
        port = self.port_var.get().strip()
        if not port:
            messagebox.showerror("Error", "Select a COM port.")
            return

        try:
            baud = int(self.baud_var.get().strip())
        except ValueError:
            messagebox.showerror("Error", "Invalid baud rate.")
            return

        try:
            self.serial_port = serial.Serial(port, baud, timeout=1)
        except Exception as e:
            messagebox.showerror("Connection Error", str(e))
            return

        self.running = True
        self.serial_thread = threading.Thread(target=self.serial_reader, daemon=True)
        self.serial_thread.start()

        self.status_var.set(f"Connected: {port} @ {baud}")
        self.connect_btn.config(text="Disconnect")
        self.link_var.set("Link: active")
        self.log(f"Connected to {port} @ {baud}")

    def disconnect(self):
        self.running = False
        try:
            if self.serial_port and self.serial_port.is_open:
                self.serial_port.close()
        except Exception:
            pass

        self.status_var.set("Disconnected")
        self.connect_btn.config(text="Connect")
        self.link_var.set("Link: idle")
        self.log("Disconnected")

    def manual_deploy(self):
        if not self.running or not self.serial_port or not self.serial_port.is_open:
            messagebox.showerror("Error", "Not connected to radio.")
            return

        ok = messagebox.askyesno("Confirm parachute deploy", "Send MANUAL DEPLOY command?")
        if not ok:
            return

        try:
            for _ in range(3):
                self.serial_port.write(b"CMD,DEPLOY\n")
            self.log("MANUAL DEPLOY sent")
        except Exception as e:
            messagebox.showerror("Error", str(e))

    def serial_reader(self):
        while self.running:
            try:
                line = self.serial_port.readline().decode(errors="ignore").strip()
                if line:
                    self.rx_queue.put(line)
            except Exception as e:
                self.rx_queue.put(("ERROR", str(e)))
                break

    def process_queue(self):
        try:
            while True:
                item = self.rx_queue.get_nowait()
                if isinstance(item, tuple) and item[0] == "ERROR":
                    self.log(f"Serial error: {item[1]}")
                    self.disconnect()
                    break
                self.handle_line(item)
        except queue.Empty:
            pass

        self.root.after(100, self.process_queue)

    def update_link_status(self):
        if self.last_packet_time is None:
            self.link_var.set("Link: idle")
        else:
            age = (datetime.now() - self.last_packet_time).total_seconds()
            if age < 2:
                self.link_var.set("Link: good")
            elif age < 5:
                self.link_var.set("Link: stale")
            else:
                self.link_var.set("Link: lost")
        self.root.after(500, self.update_link_status)

    def handle_line(self, line):
        with open(self.raw_log, "a", encoding="utf-8") as f:
            f.write(line + "\n")

        if line.startswith("TEL,"):
            self.handle_telemetry(line)
        elif line.startswith("IMGMETA,"):
            self.handle_imgmeta(line)
        elif line.startswith("IMG,"):
            self.handle_imgchunk(line)
        else:
            self.log(f"RAW {line}")

    def _safe_float(self, value, default=0.0):
        try:
            if str(value).lower() == "nan":
                return default
            return float(value)
        except Exception:
            return default

    def accel_to_attitude(self, ax, ay, az):
        roll = math.degrees(math.atan2(ay, az if abs(az) > 1e-6 else 1e-6))
        pitch = math.degrees(math.atan2(-ax, math.sqrt(ay * ay + az * az) + 1e-6))
        return roll, pitch

    def handle_telemetry(self, line):
        parts = line.split(",")
        if len(parts) < 16:
            self.log(f"Bad TEL packet: {line}")
            return

        try:
            _, seq, timestamp, temp, pressure, alt, ax, ay, az, gx, gy, gz, peak_alt, descent_count, deployed, deploy_reason = parts[:16]
        except ValueError:
            self.log(f"Parse TEL failed: {line}")
            return

        # ACK back to CanSat
        try:
            if self.serial_port and self.serial_port.is_open:
                self.serial_port.write(f"ACK,{seq}\n".encode("utf-8"))
        except Exception as e:
            self.log(f"ACK send error: {e}")

        # Packet counting / loss
        self.packet_count += 1
        self.last_packet_time = datetime.now()
        self.packet_var.set(f"Packets: {self.packet_count}")
        self.last_rx_var.set(f"Last RX: {self.last_packet_time.strftime('%H:%M:%S')}")

        try:
            seq_int = int(seq)
            if self.last_seq is not None and seq_int != self.last_seq + 1:
                self.log(f"PACKET LOSS: expected {self.last_seq + 1}, got {seq_int}")
            self.last_seq = seq_int
        except Exception:
            pass

        self.card_vars["seq"].set(seq)
        self.card_vars["temp"].set(temp)
        self.card_vars["pressure"].set(pressure)
        self.card_vars["alt"].set(alt)
        self.card_vars["peak_alt"].set(peak_alt)
        self.card_vars["deploy"].set(f"{deployed} {deploy_reason}")
        self.card_vars["ax"].set(ax)
        self.card_vars["ay"].set(ay)
        self.card_vars["az"].set(az)
        self.card_vars["gx"].set(gx)
        self.card_vars["gy"].set(gy)
        self.card_vars["gz"].set(gz)
        self.timestamp_var.set(timestamp)

        axf = self._safe_float(ax)
        ayf = self._safe_float(ay)
        azf = self._safe_float(az, default=1.0)
        gxf = self._safe_float(gx)
        gyf = self._safe_float(gy)

        roll_new, pitch_new = self.accel_to_attitude(axf, ayf, azf)

        alpha_roll = 0.12
        alpha_pitch = 0.12
        self.roll = (1 - alpha_roll) * self.roll + alpha_roll * roll_new
        self.pitch = (1 - alpha_pitch) * self.pitch + alpha_pitch * pitch_new

        self.roll = max(-85, min(85, self.roll))
        self.pitch = max(-40, min(40, self.pitch))

        self.vx = max(-60, min(60, gyf * 0.08))
        self.vy = max(-60, min(60, -gxf * 0.08))

        self.attitude.draw_indicator(self.roll, self.pitch)

        new_file = not os.path.exists(self.telemetry_csv)
        with open(self.telemetry_csv, "a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if new_file:
                w.writerow([
                    "seq", "timestamp", "temp_C", "pressure_hPa", "alt_m",
                    "ax", "ay", "az", "gx", "gy", "gz",
                    "peak_alt_m", "descent_count", "deployed", "deploy_reason"
                ])
            w.writerow([
                seq, timestamp, temp, pressure, alt,
                ax, ay, az, gx, gy, gz,
                peak_alt, descent_count, deployed, deploy_reason
            ])

    def handle_imgmeta(self, line):
        parts = line.split(",", 4)
        if len(parts) != 5:
            self.log(f"Bad IMGMETA packet: {line}")
            return

        _, image_seq, timestamp, filename, total = parts
        try:
            total = int(total)
        except ValueError:
            self.log(f"Bad IMGMETA total: {line}")
            return

        self.image_buffers[image_seq] = {
            "timestamp": timestamp,
            "filename": filename,
            "total": total,
            "chunks": {}
        }
        self.log(f"IMGMETA image_seq={image_seq} chunks={total}")

    def handle_imgchunk(self, line):
        parts = line.split(",", 4)
        if len(parts) != 5:
            self.log(f"Bad IMG packet: {line}")
            return

        _, image_seq, chunk_index, total, chunk_data = parts

        try:
            chunk_index = int(chunk_index)
            total = int(total)
        except ValueError:
            self.log(f"Bad IMG numbering: {line}")
            return

        if image_seq not in self.image_buffers:
            self.image_buffers[image_seq] = {
                "timestamp": "",
                "filename": f"preview_{image_seq}.jpg",
                "total": total,
                "chunks": {}
            }

        buf = self.image_buffers[image_seq]
        buf["total"] = total
        buf["chunks"][chunk_index] = chunk_data

        if len(buf["chunks"]) == total:
            self.reassemble_image(image_seq)

    def reassemble_image(self, image_seq):
        buf = self.image_buffers.get(image_seq)
        if not buf:
            return

        total = buf["total"]
        chunks = buf["chunks"]

        if any(i not in chunks for i in range(total)):
            self.log(f"IMG image_seq={image_seq} incomplete")
            return

        try:
            b64_data = "".join(chunks[i] for i in range(total))
            img_bytes = base64.b64decode(b64_data)

            safe_name = os.path.splitext(buf["filename"])[0] + f"_preview_{image_seq}.jpg"
            out_path = os.path.join(self.preview_dir, safe_name)

            with open(out_path, "wb") as f:
                f.write(img_bytes)

            self.show_preview(img_bytes)
            self.log(f"Preview saved: {out_path}")

            del self.image_buffers[image_seq]

        except Exception as e:
            self.log(f"Preview decode failed: {e}")

    def show_preview(self, img_bytes):
        try:
            image = Image.open(io.BytesIO(img_bytes))
            image.thumbnail((420, 320))
            photo = ImageTk.PhotoImage(image)
            self.current_preview_photo = photo
            self.preview_label.configure(image=photo, text="")
        except Exception as e:
            self.log(f"Preview display failed: {e}")

    def shutdown(self):
        self.disconnect()
        self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    app = GroundStationApp(root)
    root.protocol("WM_DELETE_WINDOW", app.shutdown)
    root.mainloop()