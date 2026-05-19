from __future__ import annotations

import asyncio
from collections import deque
import platform
import queue
import threading
import time

try:
    import tkinter as tk
    from tkinter import messagebox

    TKINTER_AVAILABLE = True
    TKINTER_IMPORT_ERROR = ""
except ImportError as exc:
    tk = None  # type: ignore[assignment]
    messagebox = None  # type: ignore[assignment]
    TKINTER_AVAILABLE = False
    TKINTER_IMPORT_ERROR = str(exc)

try:
    from bleak import BleakClient, BleakScanner
    from bleak.exc import BleakBluetoothNotAvailableError, BleakError

    BLEAK_AVAILABLE = True
    BLEAK_IMPORT_ERROR = ""
except ImportError as exc:
    BleakClient = None  # type: ignore[assignment]
    BleakScanner = None  # type: ignore[assignment]
    BleakBluetoothNotAvailableError = RuntimeError  # type: ignore[assignment]
    BleakError = Exception  # type: ignore[assignment]
    BLEAK_AVAILABLE = False
    BLEAK_IMPORT_ERROR = str(exc)


APP_TITLE = "Trackify - Bluetooth Proximity Tracker"
TARGET_DEVICE_NAME = "Trackify_ESP32"
RSSI_NEAR_THRESHOLD = -60
RSSI_MEDIUM_THRESHOLD = -75
RSSI_FILTER_WINDOW_SIZE = 5
LOST_TIMEOUT_SECONDS = 3.0
NO_DEVICE_NOTICE_SECONDS = 5.0
UI_TICK_MS = 250
ALERT_BEEP_COOLDOWN_SECONDS = 2.0
REMOTE_ALERT_RETRY_SECONDS = 2.0
REMOTE_ALERT_HEARTBEAT_SECONDS = 1.0
TRACKIFY_ALERT_SERVICE_UUID = "9d3f0001-7b31-4f8e-9b6f-76d53d2a1001"
TRACKIFY_ALERT_CHAR_UUID = "9d3f0001-7b31-4f8e-9b6f-76d53d2a1002"
REMOTE_ALERT_CLEAR = 0
REMOTE_ALERT_FAR = 1
REMOTE_ALERT_LOST = 2

BG = "#07111f"
SURFACE = "#0f1b2d"
SURFACE_ALT = "#13233a"
BORDER = "#22324a"
TEXT = "#ecf3ff"
MUTED = "#8ca1be"
ACCENT = "#4ecbff"
ACCENT_SOFT = "#16384d"
SUCCESS = "#57d18b"
SUCCESS_BG = "#102d22"
WARNING = "#ffbe55"
WARNING_BG = "#332613"
DANGER = "#ff6b7f"
DANGER_BG = "#3a1822"
IDLE = "#90a1bb"
IDLE_BG = "#1a2638"


def estimate_proximity(rssi: int | None) -> str:
    """Convert RSSI readings into simple proximity buckets."""
    if rssi is None:
        return "Lost"
    if rssi >= RSSI_NEAR_THRESHOLD:
        return "Near"
    if rssi >= RSSI_MEDIUM_THRESHOLD:
        return "Medium"
    return "Far"


def signal_strength_fraction(rssi: int | None) -> float:
    """Map RSSI into a 0..1 range for a simple visual meter."""
    if rssi is None:
        return 0.0

    clamped = max(-90, min(-45, rssi))
    return (clamped + 90) / 45


def filtered_rssi_value(samples: deque[int]) -> int | None:
    """Smooth recent RSSI samples with a small moving average window."""
    if not samples:
        return None
    return round(sum(samples) / len(samples))


def normalized_name(value: object) -> str:
    return str(value or "").replace("\x00", "").strip()


def is_trackify_match(device, advertisement_data) -> tuple[bool, str]:
    candidate_names = [
        normalized_name(getattr(advertisement_data, "local_name", "")),
        normalized_name(getattr(device, "name", "")),
    ]
    target_lower = TARGET_DEVICE_NAME.lower()

    for candidate in candidate_names:
        if not candidate:
            continue
        candidate_lower = candidate.lower()
        if candidate_lower == target_lower or target_lower in candidate_lower:
            return True, candidate

    service_uuids = [str(uuid).lower() for uuid in (getattr(advertisement_data, "service_uuids", None) or [])]
    if TRACKIFY_ALERT_SERVICE_UUID.lower() in service_uuids:
        for candidate in candidate_names:
            if candidate:
                return True, candidate
        return True, TARGET_DEVICE_NAME

    return False, ""


class TrackifyApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title(APP_TITLE)
        self.root.geometry("1040x720")
        self.root.minsize(920, 640)
        self.root.configure(bg=BG)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        self.font_family = "Helvetica"
        self.mono_family = "Menlo" if platform.system() == "Darwin" else "Consolas"

        self.event_queue: queue.Queue[tuple[str, dict[str, object]]] = queue.Queue()
        self.stop_event = threading.Event()
        self.scanner_thread: threading.Thread | None = None

        self.scanning = False
        self.closing = False
        self.target_ble_device: object | None = None
        self.target_device_address: str | None = None
        self.last_seen_at: float | None = None
        self.last_rssi: int | None = None
        self.filtered_rssi: int | None = None
        self.rssi_samples: deque[int] = deque(maxlen=RSSI_FILTER_WINDOW_SIZE)
        self.scan_started_at: float | None = None
        self.has_seen_device = False
        self.last_beep_at = 0.0
        self.current_warning_active = False
        self.hardware_alert_link_state = "idle"
        self.desired_remote_alert_level = REMOTE_ALERT_CLEAR

        self.connection_status_var = tk.StringVar(value="Idle")
        self.device_name_var = tk.StringVar(value="Not detected")
        self.rssi_var = tk.StringVar(value="-- dBm")
        self.proximity_var = tk.StringVar(value="--")
        self.alert_title_var = tk.StringVar(value="Ready to Scan")
        self.warning_var = tk.StringVar(
            value="Press the scan button to start listening for BLE advertisements."
        )
        self.info_var = tk.StringVar(
            value="Trackify listens for BLE advertisements from Trackify_ESP32 and estimates proximity from filtered RSSI."
        )
        self.signal_hint_var = tk.StringVar(value="Waiting for first BLE signal")
        self.hardware_link_var = tk.StringVar(value="Hardware alert link idle")

        self.header_status_chip: tk.Label | None = None
        self.scan_button: tk.Button | None = None
        self.connection_value_label: tk.Label | None = None
        self.device_value_label: tk.Label | None = None
        self.rssi_value_label: tk.Label | None = None
        self.proximity_value_label: tk.Label | None = None
        self.signal_meter_fill: tk.Frame | None = None
        self.signal_meter_glow: tk.Frame | None = None
        self.alert_banner: tk.Frame | None = None
        self.alert_title_label: tk.Label | None = None
        self.alert_message_label: tk.Label | None = None
        self.hardware_link_label: tk.Label | None = None
        self.info_label: tk.Label | None = None

        self._build_ui()
        self._refresh_visual_state()
        self._ui_tick()

    def _build_ui(self) -> None:
        main_frame = tk.Frame(self.root, bg=BG, padx=24, pady=24)
        main_frame.pack(fill="both", expand=True)
        main_frame.grid_columnconfigure(0, weight=7)
        main_frame.grid_columnconfigure(1, weight=5)
        main_frame.grid_rowconfigure(3, weight=1)

        hero = self._create_card(main_frame, padding=0)
        hero.grid(row=0, column=0, columnspan=2, sticky="ew")
        hero.grid_columnconfigure(0, weight=1)

        accent_strip = tk.Frame(hero, bg=ACCENT, height=5)
        accent_strip.pack(fill="x", side="top")

        hero_body = tk.Frame(hero, bg=SURFACE, padx=24, pady=22)
        hero_body.pack(fill="both", expand=True)
        hero_body.grid_columnconfigure(0, weight=1)

        hero_left = tk.Frame(hero_body, bg=SURFACE)
        hero_left.grid(row=0, column=0, sticky="w")

        badge = tk.Label(
            hero_left,
            text=f"TARGET DEVICE  {TARGET_DEVICE_NAME}",
            font=(self.font_family, 10, "bold"),
            bg=ACCENT_SOFT,
            fg=ACCENT,
            padx=12,
            pady=6,
        )
        badge.pack(anchor="w")

        title_label = tk.Label(
            hero_left,
            text="Trackify",
            font=(self.font_family, 30, "bold"),
            bg=SURFACE,
            fg=TEXT,
        )
        title_label.pack(anchor="w", pady=(18, 2))

        subtitle_label = tk.Label(
            hero_left,
            text="Bluetooth proximity tracking dashboard for your ESP32 safety tag.",
            font=(self.font_family, 12),
            bg=SURFACE,
            fg=MUTED,
        )
        subtitle_label.pack(anchor="w")

        hero_right = tk.Frame(hero_body, bg=SURFACE)
        hero_right.grid(row=0, column=1, sticky="e")

        chip_caption = tk.Label(
            hero_right,
            text="CURRENT STATE",
            font=(self.font_family, 9, "bold"),
            bg=SURFACE,
            fg=MUTED,
        )
        chip_caption.pack(anchor="e")

        self.header_status_chip = tk.Label(
            hero_right,
            text="IDLE",
            font=(self.font_family, 10, "bold"),
            bg=IDLE_BG,
            fg=IDLE,
            padx=14,
            pady=8,
        )
        self.header_status_chip.pack(anchor="e", pady=(8, 16))

        self.scan_button = tk.Button(
            hero_right,
            text="Scan for Trackify",
            command=self.start_scan,
            font=(self.font_family, 12, "bold"),
            bg=ACCENT,
            fg="#03111d",
            activebackground="#86e4ff",
            activeforeground="#03111d",
            relief="flat",
            bd=0,
            cursor="hand2",
            padx=22,
            pady=12,
        )
        self.scan_button.pack(anchor="e")

        metrics_frame = tk.Frame(main_frame, bg=BG)
        metrics_frame.grid(row=1, column=0, sticky="nsew", padx=(0, 12), pady=(18, 0))
        metrics_frame.grid_columnconfigure(0, weight=1)
        metrics_frame.grid_columnconfigure(1, weight=1)
        metrics_frame.grid_rowconfigure(0, weight=1)
        metrics_frame.grid_rowconfigure(1, weight=1)

        self.connection_value_label = self._create_metric_card(
            metrics_frame,
            row=0,
            column=0,
            title="Connection",
            value_var=self.connection_status_var,
            footnote="BLE link state",
            accent=ACCENT,
        )
        self.device_value_label = self._create_metric_card(
            metrics_frame,
            row=0,
            column=1,
            title="Device Name",
            value_var=self.device_name_var,
            footnote="Expected advertiser",
            accent=SUCCESS,
            wraplength=270,
            value_font=(self.font_family, 18, "bold"),
        )
        self.rssi_value_label = self._create_metric_card(
            metrics_frame,
            row=1,
            column=0,
            title="Filtered RSSI",
            value_var=self.rssi_var,
            footnote=f"Moving average of {RSSI_FILTER_WINDOW_SIZE} BLE samples",
            accent=WARNING,
            value_font=(self.mono_family, 24, "bold"),
        )
        self.proximity_value_label = self._create_metric_card(
            metrics_frame,
            row=1,
            column=1,
            title="Proximity",
            value_var=self.proximity_var,
            footnote="Estimated from RSSI",
            accent=DANGER,
        )

        signal_card = self._create_card(main_frame)
        signal_card.grid(row=1, column=1, sticky="nsew", pady=(18, 0))

        signal_title = tk.Label(
            signal_card,
            text="Signal Guide",
            font=(self.font_family, 16, "bold"),
            bg=SURFACE,
            fg=TEXT,
        )
        signal_title.pack(anchor="w")

        signal_subtitle = tk.Label(
            signal_card,
            text="Trackify smooths recent BLE RSSI samples before converting them into simple range states.",
            font=(self.font_family, 10),
            bg=SURFACE,
            fg=MUTED,
            justify="left",
            wraplength=360,
        )
        signal_subtitle.pack(anchor="w", pady=(6, 18))

        meter_title = tk.Label(
            signal_card,
            text="Live signal strength",
            font=(self.font_family, 10, "bold"),
            bg=SURFACE,
            fg=MUTED,
        )
        meter_title.pack(anchor="w")

        meter_track = tk.Frame(signal_card, bg="#091320", height=20, highlightbackground=BORDER, highlightthickness=1)
        meter_track.pack(fill="x", pady=(10, 8))
        meter_track.pack_propagate(False)

        self.signal_meter_fill = tk.Frame(meter_track, bg=ACCENT)
        self.signal_meter_fill.place(relx=0.0, rely=0.0, relheight=1.0, relwidth=0.0)

        self.signal_meter_glow = tk.Frame(meter_track, bg="#9eefff", width=6)
        self.signal_meter_glow.place(relx=0.0, rely=0.0, relheight=1.0, anchor="ne")

        signal_hint = tk.Label(
            signal_card,
            textvariable=self.signal_hint_var,
            font=(self.font_family, 10),
            bg=SURFACE,
            fg=TEXT,
        )
        signal_hint.pack(anchor="w", pady=(0, 16))

        self._create_legend_row(signal_card, "Near", f"Filtered RSSI >= {RSSI_NEAR_THRESHOLD}", SUCCESS)
        self._create_legend_row(
            signal_card,
            "Medium",
            f"Filtered {RSSI_MEDIUM_THRESHOLD} to {RSSI_NEAR_THRESHOLD - 1}",
            ACCENT,
        )
        self._create_legend_row(signal_card, "Far", f"Filtered RSSI < {RSSI_MEDIUM_THRESHOLD}", WARNING)
        self._create_legend_row(
            signal_card,
            "Lost",
            f"No signal for about {int(LOST_TIMEOUT_SECONDS)} seconds",
            DANGER,
        )

        self.alert_banner = self._create_card(main_frame, padding=18)
        self.alert_banner.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(18, 0))

        self.alert_title_label = tk.Label(
            self.alert_banner,
            textvariable=self.alert_title_var,
            font=(self.font_family, 18, "bold"),
            bg=SURFACE,
            fg=TEXT,
        )
        self.alert_title_label.pack(anchor="w")

        self.alert_message_label = tk.Label(
            self.alert_banner,
            textvariable=self.warning_var,
            font=(self.font_family, 11),
            bg=SURFACE,
            fg=MUTED,
            justify="left",
            wraplength=900,
        )
        self.alert_message_label.pack(anchor="w", pady=(8, 0))

        notes_card = self._create_card(main_frame)
        notes_card.grid(row=3, column=0, columnspan=2, sticky="nsew", pady=(18, 0))
        notes_card.grid_columnconfigure(0, weight=1)

        notes_title = tk.Label(
            notes_card,
            text="Scanner Notes",
            font=(self.font_family, 16, "bold"),
            bg=SURFACE,
            fg=TEXT,
        )
        notes_title.pack(anchor="w")

        notes_subtitle = tk.Label(
            notes_card,
            text="This is a prototype, so even filtered RSSI can change based on walls, people, and interference.",
            font=(self.font_family, 10),
            bg=SURFACE,
            fg=MUTED,
        )
        notes_subtitle.pack(anchor="w", pady=(6, 12))

        hardware_link_caption = tk.Label(
            notes_card,
            text="Hardware Alert Link",
            font=(self.font_family, 10, "bold"),
            bg=SURFACE,
            fg=MUTED,
        )
        hardware_link_caption.pack(anchor="w")

        self.hardware_link_label = tk.Label(
            notes_card,
            textvariable=self.hardware_link_var,
            font=(self.font_family, 10),
            bg=SURFACE,
            fg=ACCENT,
            justify="left",
            wraplength=920,
        )
        self.hardware_link_label.pack(anchor="w", pady=(6, 14))

        self.info_label = tk.Label(
            notes_card,
            textvariable=self.info_var,
            font=(self.font_family, 11),
            bg=SURFACE_ALT,
            fg=TEXT,
            justify="left",
            anchor="nw",
            wraplength=920,
            padx=18,
            pady=16,
        )
        self.info_label.pack(fill="both", expand=True)

    def _create_card(self, parent: tk.Widget, padding: int = 18) -> tk.Frame:
        return tk.Frame(
            parent,
            bg=SURFACE,
            highlightbackground=BORDER,
            highlightthickness=1,
            padx=padding,
            pady=padding,
        )

    def _create_metric_card(
        self,
        parent: tk.Widget,
        row: int,
        column: int,
        title: str,
        value_var: tk.StringVar,
        footnote: str,
        accent: str,
        wraplength: int = 220,
        value_font: tuple[str, int, str] | None = None,
    ) -> tk.Label:
        card = self._create_card(parent)
        card.grid(row=row, column=column, sticky="nsew", padx=6, pady=6)

        accent_bar = tk.Frame(card, bg=accent, height=5)
        accent_bar.pack(fill="x")

        title_label = tk.Label(
            card,
            text=title.upper(),
            font=(self.font_family, 9, "bold"),
            bg=SURFACE,
            fg=MUTED,
        )
        title_label.pack(anchor="w", pady=(14, 10))

        if value_font is None:
            value_font = (self.font_family, 24, "bold")

        value_label = tk.Label(
            card,
            textvariable=value_var,
            font=value_font,
            bg=SURFACE,
            fg=TEXT,
            justify="left",
            anchor="w",
            wraplength=wraplength,
        )
        value_label.pack(anchor="w")

        footnote_label = tk.Label(
            card,
            text=footnote,
            font=(self.font_family, 10),
            bg=SURFACE,
            fg=MUTED,
        )
        footnote_label.pack(anchor="w", pady=(10, 0))

        return value_label

    def _create_legend_row(self, parent: tk.Widget, title: str, detail: str, color: str) -> None:
        row = tk.Frame(parent, bg=SURFACE)
        row.pack(fill="x", pady=6)

        marker = tk.Frame(row, bg=color, width=12, height=12)
        marker.pack(side="left", padx=(0, 10))
        marker.pack_propagate(False)

        title_label = tk.Label(
            row,
            text=title,
            font=(self.font_family, 10, "bold"),
            bg=SURFACE,
            fg=TEXT,
        )
        title_label.pack(side="left")

        detail_label = tk.Label(
            row,
            text=f"  {detail}",
            font=(self.font_family, 10),
            bg=SURFACE,
            fg=MUTED,
        )
        detail_label.pack(side="left")

    def start_scan(self) -> None:
        if self.scanning:
            self.info_var.set("Scanning is already running in the background.")
            self._refresh_visual_state()
            return

        if not BLEAK_AVAILABLE:
            message = (
                "The 'bleak' package is not installed, so Bluetooth scanning cannot start.\n\n"
                "Install it with: pip install bleak\n\n"
                f"Import error: {BLEAK_IMPORT_ERROR}"
            )
            self._show_error(message, popup=True)
            return

        self.scanning = True
        self.stop_event = threading.Event()
        self.target_ble_device = None
        self.target_device_address = None
        self.scan_started_at = time.monotonic()
        self.has_seen_device = False
        self.last_seen_at = None
        self.last_rssi = None
        self.filtered_rssi = None
        self.rssi_samples.clear()
        self.last_beep_at = 0.0
        self.current_warning_active = False
        self.hardware_alert_link_state = "waiting"
        self.desired_remote_alert_level = REMOTE_ALERT_CLEAR

        self.connection_status_var.set("Scanning...")
        self.device_name_var.set("Not detected")
        self.rssi_var.set("-- dBm")
        self.proximity_var.set("--")
        self.info_var.set(
            f"Scanning for BLE advertisements from '{TARGET_DEVICE_NAME}'. "
            "Keep the ESP32 powered on and broadcasting."
        )
        self.signal_hint_var.set("Searching for the first advertisement packet")
        self.hardware_link_var.set("Waiting for Trackify to appear before opening the hardware alert link.")

        self._set_scan_button_state(enabled=False, text="Scanning Live")
        self._refresh_visual_state()

        self.scanner_thread = threading.Thread(
            target=self._scanner_thread_main,
            name="TrackifyBLEScanner",
            daemon=True,
        )
        self.scanner_thread.start()

    def _set_scan_button_state(self, enabled: bool, text: str) -> None:
        if self.scan_button is None:
            return

        if enabled:
            self.scan_button.config(
                state="normal",
                text=text,
                bg=ACCENT,
                fg="#03111d",
                activebackground="#86e4ff",
                activeforeground="#03111d",
                cursor="hand2",
            )
        else:
            self.scan_button.config(
                state="disabled",
                text=text,
                bg="#31455f",
                fg="#96a8c3",
                disabledforeground="#96a8c3",
                activebackground="#31455f",
                activeforeground="#96a8c3",
                cursor="arrow",
            )

    def _scanner_thread_main(self) -> None:
        try:
            asyncio.run(self._ble_worker_loop())
        except Exception as exc:
            self.event_queue.put(
                (
                    "error",
                    {
                        "message": f"Unexpected scan error: {exc}",
                        "popup": True,
                    },
                )
            )
        finally:
            self.event_queue.put(("scanner_stopped", {}))

    async def _ble_worker_loop(self) -> None:
        def detection_callback(device, advertisement_data) -> None:
            matched, advertised_name = is_trackify_match(device, advertisement_data)
            if not matched:
                return

            device_address = getattr(device, "address", None) or ""
            self.event_queue.put(
                (
                    "device_seen",
                    {
                        "ble_device": device,
                        "name": advertised_name,
                        "address": str(device_address),
                        "rssi": advertisement_data.rssi,
                    },
                )
            )

        control_task: asyncio.Task[None] | None = None
        try:
            async with BleakScanner(detection_callback=detection_callback):
                control_task = asyncio.create_task(self._control_alert_link_loop())
                while not self.stop_event.is_set():
                    await asyncio.sleep(0.1)
        except BleakBluetoothNotAvailableError as exc:
            reason = getattr(exc, "reason", None)
            reason_text = ""
            if reason is not None and hasattr(reason, "name"):
                reason_text = f" ({reason.name.replace('_', ' ').title()})"

            self.event_queue.put(
                (
                    "error",
                    {
                        "message": (
                            "Bluetooth is unavailable or permission was denied"
                            f"{reason_text}. Check that Bluetooth is turned on."
                        ),
                        "popup": True,
                    },
                )
            )
        except BleakError as exc:
            self.event_queue.put(
                (
                    "error",
                    {
                        "message": f"BLE scan error: {exc}",
                        "popup": True,
                    },
                )
            )
        finally:
            if control_task is not None:
                control_task.cancel()
                try:
                    await control_task
                except asyncio.CancelledError:
                    pass

    async def _control_alert_link_loop(self) -> None:
        client: BleakClient | None = None
        connected_address: str | None = None
        last_write_at = 0.0
        last_sent_level: int | None = None
        next_retry_at = 0.0
        desktop_only_mode = False

        def on_disconnect(_: BleakClient) -> None:
            self.event_queue.put(("alert_link", {"state": "disconnected"}))

        try:
            while not self.stop_event.is_set():
                address = self.target_device_address
                target_ble_device = self.target_ble_device

                if desktop_only_mode:
                    await asyncio.sleep(0.25)
                    continue

                if not address:
                    await asyncio.sleep(0.25)
                    continue

                if client is not None and connected_address != address:
                    try:
                        await client.disconnect()
                    except Exception:
                        pass
                    client = None
                    connected_address = None
                    last_sent_level = None

                if client is None:
                    now = time.monotonic()
                    if now < next_retry_at:
                        await asyncio.sleep(0.25)
                        continue

                    self.event_queue.put(("alert_link", {"state": "connecting"}))
                    try:
                        client_target = target_ble_device if target_ble_device is not None else address
                        client = BleakClient(
                            client_target,
                            disconnected_callback=on_disconnect,
                            services=[TRACKIFY_ALERT_SERVICE_UUID],
                        )
                        await client.connect()
                        connected_address = address
                        services = client.services
                        if services.get_characteristic(TRACKIFY_ALERT_CHAR_UUID) is None:
                            desktop_only_mode = True
                            self.event_queue.put(
                                (
                                    "alert_link",
                                    {
                                        "state": "desktop_only",
                                        "message": (
                                            "Trackify was found, but the compatible firmware with the "
                                            "hardware alert characteristic is not available."
                                        ),
                                    },
                                )
                            )
                            try:
                                await client.disconnect()
                            except Exception:
                                pass
                            client = None
                            connected_address = None
                            last_sent_level = None
                            continue

                        self.event_queue.put(("alert_link", {"state": "connected"}))
                        last_write_at = 0.0
                        last_sent_level = None
                    except BleakBluetoothNotAvailableError as exc:
                        reason = getattr(exc, "reason", None)
                        reason_text = ""
                        if reason is not None and hasattr(reason, "name"):
                            reason_text = f" ({reason.name.replace('_', ' ').title()})"
                        self.event_queue.put(
                            (
                                "alert_link",
                                {
                                    "state": "retrying",
                                    "message": (
                                        "Bluetooth is unavailable for the hardware alert link"
                                        f"{reason_text}."
                                    ),
                                },
                            )
                        )
                        client = None
                        connected_address = None
                        next_retry_at = time.monotonic() + REMOTE_ALERT_RETRY_SECONDS
                        await asyncio.sleep(0.25)
                        continue
                    except BleakError as exc:
                        self.event_queue.put(
                            (
                                "alert_link",
                                {
                                    "state": "retrying",
                                    "message": f"Hardware alert link retry scheduled: {exc}",
                                },
                            )
                        )
                        client = None
                        connected_address = None
                        next_retry_at = time.monotonic() + REMOTE_ALERT_RETRY_SECONDS
                        await asyncio.sleep(0.25)
                        continue

                if client is None:
                    await asyncio.sleep(0.25)
                    continue

                if not client.is_connected:
                    try:
                        await client.disconnect()
                    except Exception:
                        pass
                    client = None
                    connected_address = None
                    last_sent_level = None
                    next_retry_at = time.monotonic() + REMOTE_ALERT_RETRY_SECONDS
                    await asyncio.sleep(0.25)
                    continue

                now = time.monotonic()
                desired_level = self.desired_remote_alert_level
                should_write = (
                    last_sent_level != desired_level
                    or (now - last_write_at) >= REMOTE_ALERT_HEARTBEAT_SECONDS
                )
                if should_write:
                    try:
                        await client.write_gatt_char(
                            TRACKIFY_ALERT_CHAR_UUID,
                            bytes([desired_level]),
                            response=True,
                        )
                        last_sent_level = desired_level
                        last_write_at = now
                    except BleakError as exc:
                        self.event_queue.put(
                            (
                                "alert_link",
                                {
                                    "state": "retrying",
                                    "message": f"Hardware alert command failed: {exc}",
                                },
                            )
                        )
                        try:
                            await client.disconnect()
                        except Exception:
                            pass
                        client = None
                        connected_address = None
                        last_sent_level = None
                        next_retry_at = time.monotonic() + REMOTE_ALERT_RETRY_SECONDS
                        await asyncio.sleep(0.25)
                        continue

                await asyncio.sleep(0.25)
        finally:
            if client is not None:
                try:
                    await client.disconnect()
                except Exception:
                    pass

    def _ui_tick(self) -> None:
        self._process_worker_events()
        self._refresh_tracking_state()
        if not self.closing:
            self.root.after(UI_TICK_MS, self._ui_tick)

    def _process_worker_events(self) -> None:
        while True:
            try:
                event_name, payload = self.event_queue.get_nowait()
            except queue.Empty:
                break

            if event_name == "device_seen":
                self._handle_device_seen(payload)
            elif event_name == "alert_link":
                self._handle_alert_link_event(payload)
            elif event_name == "error":
                message = str(payload.get("message", "Unknown scan error."))
                popup = bool(payload.get("popup", False))
                self._show_error(message, popup=popup)
            elif event_name == "scanner_stopped":
                self._handle_scanner_stopped()

    def _handle_device_seen(self, payload: dict[str, object]) -> None:
        self.has_seen_device = True
        self.last_seen_at = time.monotonic()
        self.last_rssi = int(payload.get("rssi")) if payload.get("rssi") is not None else None
        if self.last_rssi is not None:
            self.rssi_samples.append(self.last_rssi)
        self.filtered_rssi = filtered_rssi_value(self.rssi_samples)
        device_address = str(payload.get("address", "")).strip()
        ble_device = payload.get("ble_device")
        if device_address:
            self.target_device_address = device_address
            self.target_ble_device = ble_device
            if self.hardware_alert_link_state in {"waiting", "idle"}:
                self.hardware_alert_link_state = "connecting"
                self.hardware_link_var.set(
                    "Trackify detected. Preparing the hardware alert link for the buzzer and LED."
                )

        device_name = str(payload.get("name", TARGET_DEVICE_NAME))
        self.device_name_var.set(device_name)
        self.connection_status_var.set("Connected")
        self.info_var.set("Trackify device detected. Monitoring live BLE advertisements in real time with RSSI smoothing.")
        self.signal_hint_var.set("Receiving live BLE signal samples for moving-average filtering")
        self._refresh_visual_state()

    def _handle_alert_link_event(self, payload: dict[str, object]) -> None:
        state = str(payload.get("state", "idle"))
        message = str(payload.get("message", "")).strip()
        self.hardware_alert_link_state = state

        if state == "connecting":
            self.hardware_link_var.set("Attempting to open the ESP32 hardware alert link...")
        elif state == "connected":
            self.hardware_link_var.set(
                "Hardware alert link active. Desktop warnings and ESP32 buzzer/LED commands are available."
            )
        elif state == "disconnected":
            self.hardware_link_var.set("Hardware alert link disconnected. Retrying while scanning continues...")
        elif state == "retrying":
            retry_message = message or "Retrying the hardware alert link in the background."
            self.hardware_link_var.set(retry_message)
        elif state == "desktop_only":
            fallback_message = message or "Legacy firmware detected. Desktop alerts remain active, but hardware alerts are unavailable."
            self.hardware_link_var.set(fallback_message)
        else:
            self.hardware_link_var.set("Hardware alert link idle")

        self._refresh_visual_state()

    def _handle_scanner_stopped(self) -> None:
        if self.closing:
            return

        if not self.stop_event.is_set():
            self.scanning = False
            self.target_ble_device = None
            self.target_device_address = None
            self._set_remote_alert_level(REMOTE_ALERT_CLEAR)
            self.hardware_alert_link_state = "idle"
            self.hardware_link_var.set("Hardware alert link stopped.")
            self._set_scan_button_state(enabled=True, text="Scan for Trackify")
            self._refresh_visual_state()

    def _set_remote_alert_level(self, alert_level: int) -> None:
        self.desired_remote_alert_level = alert_level

    def _refresh_tracking_state(self) -> None:
        if not self.scanning:
            return

        now = time.monotonic()

        if not self.has_seen_device:
            if self.scan_started_at and now - self.scan_started_at >= NO_DEVICE_NOTICE_SECONDS:
                self.connection_status_var.set("Scanning...")
                self.device_name_var.set("Not detected")
                self.rssi_var.set("-- dBm")
                self.proximity_var.set("--")
                self._set_remote_alert_level(REMOTE_ALERT_CLEAR)
                self.info_var.set(
                    f"No '{TARGET_DEVICE_NAME}' device found yet. "
                    "Check that the ESP32 is powered on and advertising BLE."
                )
                self.signal_hint_var.set("Still listening for the first Trackify advertisement")
                self.current_warning_active = False
                self._refresh_visual_state()
            return

        if self.last_seen_at is None:
            return

        time_since_seen = now - self.last_seen_at

        if time_since_seen >= LOST_TIMEOUT_SECONDS:
            self.connection_status_var.set("Lost")
            self.last_rssi = None
            self.filtered_rssi = None
            self.rssi_samples.clear()
            self.rssi_var.set("-- dBm")
            self.proximity_var.set("Lost")
            self._set_remote_alert_level(REMOTE_ALERT_LOST)
            self.info_var.set(
                f"'{TARGET_DEVICE_NAME}' has not been detected for about "
                f"{int(LOST_TIMEOUT_SECONDS)} seconds."
            )
            self.signal_hint_var.set("Signal missing - waiting for the tracker to reappear")
            self._set_warning_state(True)
            self._refresh_visual_state()
            return

        self.rssi_var.set(f"{self.filtered_rssi} dBm" if self.filtered_rssi is not None else "-- dBm")
        proximity = estimate_proximity(self.filtered_rssi)
        self.proximity_var.set(proximity)
        self.connection_status_var.set("Connected")

        if proximity == "Far":
            self._set_remote_alert_level(REMOTE_ALERT_FAR)
            self.info_var.set("Filtered signal is weak. Move closer to the Trackify device.")
            self.signal_hint_var.set("Weak filtered signal detected - tracker may be far away")
            self._set_warning_state(True)
        elif proximity == "Medium":
            self._set_remote_alert_level(REMOTE_ALERT_CLEAR)
            self.info_var.set("Filtered signal is moderate. The Trackify device is still within range.")
            self.signal_hint_var.set("Moderate filtered signal - tracker is still nearby")
            self._set_warning_state(False)
        else:
            self._set_remote_alert_level(REMOTE_ALERT_CLEAR)
            self.info_var.set("Filtered signal looks stable. The Trackify device is close to this computer.")
            self.signal_hint_var.set("Strong filtered signal - tracker is nearby")
            self._set_warning_state(False)

        self._refresh_visual_state()

    def _refresh_visual_state(self) -> None:
        connection_status = self.connection_status_var.get()
        proximity = self.proximity_var.get()
        device_name = self.device_name_var.get()

        chip_text = connection_status.upper().replace("...", "")
        chip_fg, chip_bg = self._connection_palette(connection_status)
        proximity_fg, _ = self._proximity_palette(proximity)

        if self.header_status_chip is not None:
            self.header_status_chip.config(text=chip_text, fg=chip_fg, bg=chip_bg)

        if self.connection_value_label is not None:
            self.connection_value_label.config(fg=chip_fg)

        if self.device_value_label is not None:
            self.device_value_label.config(fg=TEXT if device_name != "Not detected" else MUTED)

        if self.rssi_value_label is not None:
            self.rssi_value_label.config(fg=proximity_fg if self.filtered_rssi is not None else MUTED)

        if self.proximity_value_label is not None:
            self.proximity_value_label.config(fg=proximity_fg if proximity != "--" else MUTED)

        if self.hardware_link_label is not None:
            hardware_fg = ACCENT
            if self.hardware_alert_link_state == "connected":
                hardware_fg = SUCCESS
            elif self.hardware_alert_link_state in {"retrying", "desktop_only"}:
                hardware_fg = WARNING
            elif self.hardware_alert_link_state in {"disconnected", "error"}:
                hardware_fg = DANGER
            self.hardware_link_label.config(fg=hardware_fg)

        self._update_signal_meter(proximity_fg)
        self._update_alert_banner(connection_status, proximity)

        if not self.scanning and connection_status != "Error":
            self._set_scan_button_state(enabled=True, text="Scan for Trackify")

    def _connection_palette(self, status: str) -> tuple[str, str]:
        if status == "Connected":
            return SUCCESS, SUCCESS_BG
        if status == "Scanning...":
            return ACCENT, ACCENT_SOFT
        if status in {"Lost", "Error"}:
            return DANGER, DANGER_BG
        return IDLE, IDLE_BG

    def _proximity_palette(self, proximity: str) -> tuple[str, str]:
        if proximity == "Near":
            return SUCCESS, SUCCESS_BG
        if proximity == "Medium":
            return ACCENT, ACCENT_SOFT
        if proximity == "Far":
            return WARNING, WARNING_BG
        if proximity == "Lost":
            return DANGER, DANGER_BG
        return MUTED, IDLE_BG

    def _update_signal_meter(self, fill_color: str) -> None:
        if self.signal_meter_fill is None or self.signal_meter_glow is None:
            return

        width_fraction = signal_strength_fraction(self.filtered_rssi)
        if self.filtered_rssi is not None:
            width_fraction = max(0.06, width_fraction)

        self.signal_meter_fill.config(bg=fill_color)
        self.signal_meter_glow.config(bg=fill_color)
        self.signal_meter_fill.place_configure(relwidth=width_fraction)
        self.signal_meter_glow.place_configure(relx=width_fraction)

    def _update_alert_banner(self, connection_status: str, proximity: str) -> None:
        if self.alert_banner is None or self.alert_title_label is None or self.alert_message_label is None:
            return

        if connection_status == "Error":
            title = "Scanner Error"
            message = self.info_var.get()
            title_fg = DANGER
            message_fg = "#ffd9df"
            bg = DANGER_BG
        elif connection_status == "Lost":
            title = "Tracker Lost"
            message = "Warning: Item may be left behind! No BLE signal has been seen for about 3 seconds."
            title_fg = DANGER
            message_fg = "#ffd9df"
            bg = DANGER_BG
        elif proximity == "Far":
            title = "Tracker Is Far"
            message = "Warning: Item may be left behind! The filtered BLE signal is weak, so the tracker may be moving away."
            title_fg = WARNING
            message_fg = "#ffe4bb"
            bg = WARNING_BG
        elif proximity == "Medium":
            title = "Within Range"
            message = "Filtered signal is moderate. The tracker is still nearby, but it is not right next to the computer."
            title_fg = ACCENT
            message_fg = "#d5f5ff"
            bg = ACCENT_SOFT
        elif proximity == "Near":
            title = "All Clear"
            message = "Strong filtered signal detected. Trackify appears to be close to this computer."
            title_fg = SUCCESS
            message_fg = "#dff9ea"
            bg = SUCCESS_BG
        elif self.scanning:
            title = "Searching for Trackify"
            message = "Listening for BLE advertisements from Trackify_ESP32 and smoothing recent RSSI samples."
            title_fg = ACCENT
            message_fg = "#d5f5ff"
            bg = ACCENT_SOFT
        else:
            title = "Ready to Scan"
            message = "Press the scan button to begin proximity monitoring."
            title_fg = IDLE
            message_fg = "#d7e1f0"
            bg = IDLE_BG

        self.alert_title_var.set(title)
        self.warning_var.set(message)
        self.alert_banner.config(bg=bg, highlightbackground=BORDER)
        self.alert_title_label.config(bg=bg, fg=title_fg)
        self.alert_message_label.config(bg=bg, fg=message_fg)

    def _set_warning_state(self, warning_active: bool) -> None:
        if warning_active:
            should_beep = (
                not self.current_warning_active
                or (time.monotonic() - self.last_beep_at) >= ALERT_BEEP_COOLDOWN_SECONDS
            )
            if should_beep:
                self._play_alert_beep()
                self.last_beep_at = time.monotonic()

        self.current_warning_active = warning_active

    def _play_alert_beep(self) -> None:
        try:
            if platform.system() == "Windows":
                import winsound

                winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
            else:
                self.root.bell()
        except Exception:
            pass

    def _show_error(self, message: str, popup: bool = False) -> None:
        self.scanning = False
        self.current_warning_active = False
        self.target_ble_device = None
        self.target_device_address = None
        self.last_rssi = None
        self.filtered_rssi = None
        self.rssi_samples.clear()
        self._set_remote_alert_level(REMOTE_ALERT_CLEAR)
        self.hardware_alert_link_state = "error"
        self.hardware_link_var.set("Hardware alert link unavailable because BLE scanning failed.")
        self.connection_status_var.set("Error")
        self.proximity_var.set("--")
        self.signal_hint_var.set("Scanner unavailable")
        self.info_var.set(message)
        self._set_scan_button_state(enabled=True, text="Scan for Trackify")
        self._refresh_visual_state()

        if popup and not self.closing:
            messagebox.showerror("Trackify Error", message)

    def on_close(self) -> None:
        self.closing = True
        self.stop_event.set()

        if self.scanner_thread and self.scanner_thread.is_alive():
            self.scanner_thread.join(timeout=1.0)

        self.root.destroy()


def main() -> None:
    if not TKINTER_AVAILABLE:
        raise SystemExit(
            "Tkinter is not available in this Python installation. "
            "Install a Python build that includes Tk support, then run the app again. "
            f"Import error: {TKINTER_IMPORT_ERROR}"
        )

    root = tk.Tk()
    app = TrackifyApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
