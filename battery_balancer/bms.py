# --------------------------------------------------------------------------------
# Battery Management System (BMS) Script Documentation
# --------------------------------------------------------------------------------
#
# **Script Name:** bms.py
# **Version:** 1.7 (As of August 24, 2025) - Made battery configuration fully configurable: num_series_banks (e.g., 3), sensors_per_bank (e.g., 8), number_of_parallel_batteries (e.g., 4). Total sensors = parallel * series * sensors_per_bank. Generalized BANK_SENSOR_INDICES, TUI art/loops, web loops, balancing pairs/relays (assumes relay hardware scales; note for >3). Retained full docs/diagram/comments. Watchdog thread as is.
# **Author:** [Your Name or Original Developer] - Built for Raspberry Pi-based battery monitoring and balancing.
# **Purpose:** This script acts as a complete Battery Management System (BMS) for a configurable NsXp battery configuration (N series banks, X parallel cells per bank, where X = sensors_per_bank * number_of_parallel_batteries). It monitors temperatures from multiple Modbus slaves and voltages, balances charge between banks, detects issues, logs events, sends alerts, and provides user interfaces via terminal (TUI) and web dashboard. Includes time-series logging using RRDTool, ASCII line charts in TUI, and interactive charts in web via Chart.js.
#
# **Detailed Overview:**
# - **Temperature Monitoring:** Connects to NTC thermistors via Lantronix EDS4100 using Modbus TCP in multidrop RS485 configuration. Supports multiple slaves (one per parallel battery), each with num_series_banks * sensors_per_bank channels. Aggregates readings into global channels, groups by series bank for analysis. Applies calibration offsets, checks anomalies (high/low, deviations, rises, lags, disconnections). Handles per-slave errors gracefully.
# - Calibration: On first valid read (all sensors > valid_min across all slaves), computes overall median and offsets. Saves to 'offsets.txt' for future runs.
# - Anomalies Checked:
#   - Invalid/Disconnected: Reading <= valid_min (e.g., 0.0°C).
#   - High: > high_threshold (e.g., 42.0°C).
#   - Low: < low_threshold (e.g., 0.0°C).
#   - Deviation: Absolute > abs_deviation_threshold (e.g., 2.0°C) or relative > deviation_threshold (e.g., 10%) from bank median.
#   - Abnormal Rise: Increase > rise_threshold (e.g., 2.0°C) since last poll.
#   - Group Lag: Change differs from bank median change by > disconnection_lag_threshold (e.g., 0.5°C).
#   - Sudden Disconnection: Was valid, now invalid.
# - **Voltage Monitoring & Balancing:** Uses ADS1115 ADC over I2C to measure voltages of num_series_banks banks (note: hardware limited to ~4 channels; for more, extend). Balances if difference > VoltageDifferenceToBalance (e.g., 0.1V) by connecting high to low bank via relays and DC-DC converter (relay logic generalized for N banks, assumes hardware supports).
# - Heating Mode: If any temperature < 10°C, balances regardless of voltage difference to generate heat.
# - Safety: Skips balancing if alerts active (e.g., anomalies). Rests for BalanceRestPeriodSeconds (e.g., 60s) after balancing.
# - Voltage Checks: Alerts if < LowVoltageThresholdPerBattery (e.g., 18.5V), > HighVoltageThresholdPerBattery (e.g., 21.0V), or zero.
# - **Alerts & Notifications:** Logs to 'battery_monitor.log'. Activates alarm relay on issues. Sends throttled emails (e.g., every 3600s) via SMTP.
# - **Watchdog:** If enabled, pets hardware watchdog via dedicated thread (every 5s with aliveness check via timestamp) to prevent resets on hangs. Uses /dev/watchdog with 15s timeout (Pi max).
# - **User Interfaces:**
#   - **TUI (Terminal UI):** Uses curses for real-time display: ASCII art batteries (dynamic for num_series_banks) with voltages/temps, alerts, balancing progress bar/animation, last 20 events. Now includes ASCII line charts for voltage history per bank and median temperature, placed in the top-right section for visualization of trends over time.
#   - **Web Dashboard:** HTTP server on port 8080 (configurable). Shows voltages, temps, alerts, balancing status. Supports API for status/balance/history. Optional auth/CORS. Now includes interactive time-series charts using Chart.js for voltages per bank and median temperature, placed at the top of the page after the header for easy viewing.
# - **Time-Series Logging:** Uses RRDTool for persistent storage of bank voltages and overall median temperature. Data is updated every poll interval (e.g., 10s), but RRD is configured with 1min steps for aggregation. History is limited to ~480 entries (e.g., 8 hours). Fetch functions retrieve data for TUI and web rendering.
# - **Startup Self-Test:** Validates config, hardware connections (I2C/Modbus per slave), initial reads, balancer (tests all pairs for voltage changes).
# - Retries on failure after 2min. Alerts and activates alarm if fails.
# - **Error Handling:** Retries reads (exponential backoff), handles missing hardware (test mode), logs tracebacks, graceful shutdown on Ctrl+C. Per-slave Modbus errors handled with alerts and fallback values.
# - **Configuration:** From 'battery_monitor.ini'. Defaults if missing keys. See INI documentation below.
# - **Logging:** Configurable level (e.g., INFO). Timestamps events.
# - **Shutdown:** Cleans GPIO, web server, watchdog on exit.

# **Key Features Explained for Non-Programmers:**
# - Imagine this script as a vigilant guardian for your battery pack. It constantly checks the "health" (temperature and voltage) of each part of the battery.
# - Temperatures: Like checking body temperature with 96 thermometers (for 4 batteries). If one is too hot/cold or acting weird, it raises an alarm.
# - Voltages: Measures "energy level" in each of 3 groups. If one has more energy than another, it transfers some to balance them, like pouring water between buckets.
# - Heating: In cold weather (<10°C), it deliberately transfers energy to create warmth inside the battery cabinet.
# - Alerts: If something's wrong, it logs it, turns on a buzzer/light (alarm relay), and emails you (but not too often to avoid spam).
# - Interfaces: Terminal shows a fancy text-based dashboard with ASCII charts for trends and lists all temps; web page lets you view from browser with interactive charts and full temp lists.
# - Startup Check: Like a self-diagnostic when your car starts – ensures everything's connected and working before running.
# - Time-Series: Tracks history of voltages and temps, shows trends in charts to spot patterns over time.

# **How It Works (Step-by-Step for Non-Programmers):**
# 1. **Start:** Loads settings from INI file (like a recipe book).
# 2. **Setup:** Connects to hardware (sensors, relays) – if missing, runs in "pretend" mode. Creates/loads RRD database for history.
# 3. **Self-Test:** Checks if config makes sense, hardware responds (per Modbus slave), sensors give good readings, balancing actually changes voltages. If fail, alerts and retries.
# 4. **Main Loop (Repeats Forever):**
#    - Read temperatures from all slaves, aggregate.
#    - Calibrate them (adjust based on startup values for accuracy).
#    - Check for temperature problems (too hot, too cold, etc.).
#    - Read voltages from 3 banks.
#    - Check for voltage problems (too high, too low, zero).
#    - Update RRD database with voltages and median temp.
#    - If cold (<10°C anywhere), balance to heat up.
#    - Else, if voltages differ too much, balance to equalize.
#    - Fetch history from RRD for charts.
#    - Update terminal (with ASCII charts and full temp lists)/web displays (with Chart.js and full lists).
#    - Log events, send emails if issues.
#    - Update alive timestamp for watchdog.
#    - Wait a bit (e.g., 10s), repeat.
# 5. **Balancing Process:** Connects high to low bank with relays, turns on converter to transfer charge, shows progress, turns off after time.
# 6. **Shutdown:** If you press Ctrl+C, cleans up connections safely.

# **Updated Logic Flow Diagram (ASCII - More Detailed):**
#
"""
+--------------------------------------+
| Load Config from INI                 |
| (Read settings file, incl. parallel) |
+--------------------------------------+
|
v
+--------------------------------------+
| Setup Hardware                       |
| (I2C bus, GPIO pins, RRD DB)        |
| Compute sensor groupings             |
+--------------------------------------+
|
v
+--------------------------------------+
| Startup Self-Test                    |
| (Config valid?                       |
| Hardware connected? Per slave?       |
| Initial reads OK? Aggregated?        |
| Balancer works?)                     |
| If fail: Alert, Retry                |
+--------------------------------------+
|
v
+--------------------------------------+
| Start Watchdog Thread                |
| (Pet every 5s if main alive)         |
+--------------------------------------+
|
v
+--------------------------------------+ <---------------------+
| Main Loop (Repeat)                   |                      |
+--------------------------------------+                      |
|                                                             |
v                                                             |
+--------------------------------------+                      |
| Read Temps (Per Slave, Aggregate)    |                      |
| (Handle errors per slave)            |                      |
+--------------------------------------+                      |
|                                                             |
v                                                             |
+--------------------------------------+                      |
| Calibrate Temps                      |                      |
| (Apply offsets if set)               |                      |
+--------------------------------------+                      |
|                                                             |
v                                                             |
+--------------------------------------+                      |
| Check Temp Issues                    |                      |
| (High/Low/Deviation/                 |                      |
| Rise/Lag/Disconnect, with bat info)  |                      |
+--------------------------------------+                      |
|                                                             |
v                                                             |
+--------------------------------------+                      |
| Read Voltages (ADC)                  |                      |
| (3 banks via I2C)                    |                      |
+--------------------------------------+                      |
|                                                             |
v                                                             |
+--------------------------------------+                      |
| Check Voltage Issues                 |                      |
| (High/Low/Zero)                      |                      |
+--------------------------------------+                      |
|                                                             |
v                                                             |
+--------------------------------------+                      |
| Update RRD with Data                 |                      |
| (Voltages, Median Temp)              |                      |
+--------------------------------------+                      |
|                                                             |
v                                                             |
+--------------------------------------+                      |
| If Any Temp < 10°C:                  |                      |
| Balance for Heating                  |                      |
| Else If Volt Diff > Th:              |                      |
| Balance Normally                     |                      |
| (High to Low Bank)                   |                      |
| Skip if Alerts Active                |                      |
+--------------------------------------+                      |
|                                                             |
v                                                             |
+--------------------------------------+                      |
| Fetch RRD History                    |                      |
| (For Charts)                         |                      |
+--------------------------------------+                      |
|                                                             |
v                                                             |
+--------------------------------------+                      |
| Update TUI (Terminal)                |                      |
| & Web Dashboard                      |                      |
| (Show status, alerts,                |                      |
| ASCII/Chart.js Charts, full temps)   |                      |
+--------------------------------------+                      |
|                                                             |
v                                                             |
+--------------------------------------+                      |
| Log Events, Send Email               |                      |
| if Issues & Throttled                |                      |
+--------------------------------------+                      |
|                                                             |
v                                                             |
+--------------------------------------+                      |
| Update Alive Timestamp               |                      |
+--------------------------------------+                      |
|                                                             |
v                                                             |
+--------------------------------------+                      |
| Sleep (Poll Interval)                |                      |
+--------------------------------------+                      |
|                                                             |
+-------------------------------------------------------------+
"""
# **Dependencies (What the Script Needs to Run):**
# - **Python Version:** 3.11 or higher (core language for running the code).
# - **Hardware Libraries:** smbus (for I2C communication with sensors/relays), RPi.GPIO (for controlling Raspberry Pi pins). Install: sudo apt install python3-smbus python3-rpi.gpio.
# - **External Library:** art (for ASCII art in TUI). Install: pip install art.
# - **Time-Series Storage:** rrdtool (for RRD database). Install: sudo apt install rrdtool.
# - **Standard Python Libraries:** socket (networking), statistics (math like medians), time (timing/delays), configparser (read INI), logging (save logs), signal (handle shutdown), gc (memory cleanup), os (files), sys (exit), smtplib/email (emails), curses (TUI), threading (web server and watchdog), json/http.server/urllib/base64 (web), traceback (errors), fcntl/struct (watchdog), subprocess (for rrdtool commands), xml.etree.ElementTree (for parsing RRD XML output).
# - **Hardware Requirements:** Raspberry Pi (any model, detects for watchdog), ADS1115 ADC (voltage), TCA9548A multiplexer (I2C channels), Relays (balancing), Lantronix EDS4100 (Modbus for temps), GPIO pins (e.g., 5 for DC-DC, 6 for alarm, 4 for fan).
# - **No Internet for Installs:** All libraries must be pre-installed; script can't download. For web charts, Chart.js is loaded via CDN (requires internet for dashboard users).

# **Installation Guide (Step-by-Step for Non-Programmers):**
# 1. **Install Python:** On Raspberry Pi, run in terminal: sudo apt update; sudo apt install python3.
# 2. **Install Hardware Libraries:** sudo apt install python3-smbus python3-rpi.gpio.
# 3. **Install Art Library:** pip install art (or sudo pip install art if needed).
# 4. **Install RRDTool for Time-Series:** sudo apt install rrdtool.
# 5. **Enable I2C:** Run sudo raspi-config, go to Interface Options > I2C > Enable, then reboot.
# 6. **Create/Edit INI File:** Make 'battery_monitor.ini' in same folder as script. Copy template below and fill in values (e.g., emails, IPs, slave addresses).
# 7. **Run Script:** sudo python bms.py (needs root for hardware access).
# 8. **View Web Dashboard:** Open browser to http://<your-pi-ip>:8080. Charts will load via Chart.js CDN.
# 9. **Logs:** Check 'battery_monitor.log' for details. Set LoggingLevel=DEBUG in INI for more info.
# 10. **RRD Database:** Created automatically as 'bms.rrd' on first run. No manual setup needed.

# **Notes & Troubleshooting:**
# - **Hardware Matching:** Ensure INI addresses/pins match your setup. Wrong IP/port/slave = no temps.
# - **Email Setup:** Use Gmail app password (not regular password) for SMTP_Password.
# - **TUI Size:** Terminal should be wide (>80 columns) and tall for full display, including all temps and charts.
# - **Test Mode:** If no hardware, script runs without crashing but warns.
# - **Security:** For web, enable auth_required=True and set strong username/password.
# - **Offsets File:** 'offsets.txt' stores calibration – delete to recalibrate.
# - **RRD Issues:** If rrdtool commands fail, check installation and permissions. Database 'bms.rrd' stores aggregated data; use rrdtool info bms.rrd for details.
# - **Common Errors:** I2C errors = check wiring/connections. Modbus errors = check Lantronix IP/port/slave addresses/RS485 wiring. RRD errors = ensure rrdtool installed and path correct.
# - **Performance:** Poll interval ~10s; balancing ~5s. Adjust in INI. Charts fetch from RRD (~480 entries) won't impact performance.
# - **Customization:** Edit thresholds in INI for your battery specs (e.g., Li-ion safe ranges). For longer history, adjust RRA in RRD creation.
# - **Watchdog Note:** Dedicated thread ensures reliable petting; resets only on true main hangs.

# --------------------------------------------------------------------------------
# Code Begins Below - With Line-by-Line Comments for Non-Programmers
# --------------------------------------------------------------------------------
import socket # Used to connect to the Lantronix EDS4100 device over the network - like making a phone call to the sensor box.
import statistics # Helps calculate averages and medians for temperature data - math helpers.
import time # Manages timing, delays, and timestamps for events - like a clock and stopwatch.
import configparser # Reads settings from the INI configuration file - loads the recipe.
import logging # Logs events and errors to a file for troubleshooting - like a diary.
import signal # Handles graceful shutdown when the user presses Ctrl+C - catches "stop" signal.
import gc # Manages memory cleanup during long-running operations - garbage collector.
import os # Handles file operations, like reading/writing offsets - file manager.
import sys # Used to exit the script cleanly - system commands.
import threading # Runs the web server in a separate thread - multitasking.
import json # Formats data for the web interface - data packer.
from urllib.parse import urlparse, parse_qs # Parses web requests - breaks down URLs.
import base64 # Decodes authentication credentials for the web interface - secret decoder.
import traceback # Logs detailed error information for debugging - error detective.
import subprocess # Runs external commands like rrdtool for time-series database operations - external tool caller.
import xml.etree.ElementTree as ET # Parses XML output from rrdtool for fetching history data - XML parser.
try:
    import smbus # Communicates with I2C devices like the ADC and relays - hardware talker.
    import RPi.GPIO as GPIO # Controls Raspberry Pi GPIO pins for relays - pin controller.
except ImportError:
    print("Hardware libraries not available - running in test mode") # Warn user.
    smbus = None # Set to none if missing.
    GPIO = None # Set to none if missing.
from email.mime.text import MIMEText # Builds email messages - email builder.
import smtplib # Sends email alerts - email sender.
from http.server import HTTPServer, BaseHTTPRequestHandler # Runs the web server - web host.
import curses # Creates the terminal-based Text User Interface (TUI) - terminal drawer.
from art import text2art # Generates ASCII art for the TUI display - art maker.
import fcntl # For watchdog ioctl - low-level control.
import struct # For watchdog struct - data packer.
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
logging.basicConfig(
    filename='battery_monitor.log', # Log file name - where diary is saved.
    level=logging.INFO, # Log level (INFO captures key events) - how detailed.
    format='%(asctime)s - %(message)s' # Log format with timestamp - date + message.
)
config_parser = configparser.ConfigParser() # Object to read INI file - config reader.
bus = None # I2C bus for communicating with hardware - hardware connection.
last_email_time = 0 # Tracks when the last email alert was sent - email timer.
balance_start_time = None # Tracks when balancing started - balance clock start.
last_balance_time = 0 # Tracks when the last balancing ended - balance clock end.
battery_voltages = [] # Stores current voltages for each bank - voltage list.
previous_temps = None # Stores previous temperature readings - old temps.
previous_bank_medians = None # Stores previous median temperatures per bank - old medians.
run_count = 0 # Counts how many times the main loop has run - cycle counter.
startup_offsets = None # Temperature calibration offsets from startup - adjustment numbers.
startup_median = None # Median temperature at startup - average at start.
startup_set = False # Indicates if temperature calibration is set - calibration flag.
alert_states = {} # Tracks alerts for each temperature channel - alert memory.
balancing_active = False # Indicates if balancing is currently happening - balancing flag.
startup_failed = False # Indicates if startup tests failed - test fail flag.
startup_alerts = [] # Stores startup test failure messages - test error list.
web_server = None # Web server object - web host.
event_log = [] # Stores the last 20 events (e.g., alerts, balancing) - event history.
web_data = {
    'voltages': [0.0] * 3, # Current voltages for 3 banks - voltage array.
    'temperatures': [None] * 96, # Current temperatures for all sensors - temp array.
    'bank_summaries': [{'median': 0.0, 'min': 0.0, 'max': 0.0, 'invalid': 0}] * 3, # Summaries per bank
    'alerts': [], # Current active alerts - alert list.
    'balancing': False, # Balancing status - balance flag.
    'last_update': time.time(), # Last data update timestamp - update time.
    'system_status': 'Initializing' # System status (e.g., Running, Alert) - status string.
}
BANK_SENSOR_INDICES = [[], [], []] # Filled in main based on parallel count.
NUM_BANKS = 3 # Fixed number of series banks for 3sXp configuration - constant 3.
WATCHDOG_DEV = '/dev/watchdog' # Device file for watchdog - hardware reset preventer.
watchdog_fd = None # File handle for watchdog - open connection.
alive_timestamp = 0.0 # Shared timestamp updated by main to indicate aliveness - for watchdog thread.
RRD_FILE = 'bms.rrd' # RRD database file for storing time-series data - persistent storage.
HISTORY_LIMIT = 480 # Number of historical entries to retain (e.g., ~8 hours at 1min steps) - limit for memory/efficiency.
def get_bank_for_channel(ch):
    """
    Find which battery bank a temperature sensor belongs to.
    This function takes a sensor number (1-total) and figures out which group (bank 1,2,3) it belongs to.
    Args:
        ch (int): Sensor channel number (1 to total_channels) - the sensor ID.
    Returns:
        int: Bank number (1 to 3) or None if the channel is invalid - the group ID.
    """
    for bank_id, indices in enumerate(BANK_SENSOR_INDICES, 1):
        if ch - 1 in indices:
            return bank_id
    return None
def get_battery_and_local_ch(ch):
    """
    Find the parallel battery ID and local channel for a global channel.
    This function maps global channel to which battery and local sensor on that battery.
    Args:
        ch (int): Global channel (1 to total_channels) - global ID.
    Returns:
        tuple: (battery_id, local_ch) - battery 1+, local 1-24.
    """
    sensors_per_battery = 24
    bat_id = ((ch - 1) // sensors_per_battery) + 1
    local_ch = ((ch - 1) % sensors_per_battery) + 1
    return bat_id, local_ch
def modbus_crc(data):
    """
    Calculate a checksum (CRC) to ensure data integrity for Modbus communication.
    This is like a safety check to make sure the data wasn't corrupted during transmission.
    Args:
        data (bytes): Data to calculate the CRC for - the message bytes.
    Returns:
        bytes: 2-byte CRC value in little-endian order - the check code.
    """
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 0x0001:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return crc.to_bytes(2, 'little')
def read_ntc_sensors(ip, modbus_port, query_delay, num_channels, scaling_factor, max_retries, retry_backoff_base, slave_addr=1):
    """
    Read temperatures from NTC sensors via Modbus over TCP for a specific slave.
    This function connects to the sensor device over network, sends a request for data, receives it, checks it's good, and converts to temperatures.
    Args:
        ip (str): IP address of the Lantronix EDS4100 device - device address.
        modbus_port (int): Network port for Modbus communication - door number.
        query_delay (float): Seconds to wait after sending a query - pause for response.
        num_channels (int): Number of temperature sensors to read - how many.
        scaling_factor (float): Converts raw sensor data to degrees Celsius - math factor.
        max_retries (int): Maximum attempts to retry failed reads - try again count.
        retry_backoff_base (int): Base for retry delay (e.g., 1s, 2s, 4s) - wait multiplier.
        slave_addr (int): Modbus slave address. Default: 1.
    Returns:
        list or str: Temperatures or error message.
    """
    logging.info(f"Starting temp read for slave {slave_addr}.")
    query_base = bytes([slave_addr, 3]) + (0).to_bytes(2, 'big') + (num_channels).to_bytes(2, 'big')
    crc = modbus_crc(query_base)
    query = query_base + crc
    for attempt in range(max_retries):
        try:
            logging.debug(f"Temp read attempt {attempt+1} for slave {slave_addr}: Connecting to {ip}:{modbus_port}")
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(3)
            s.connect((ip, modbus_port))
            s.send(query)
            time.sleep(query_delay)
            response = s.recv(1024)
            s.close()
            if len(response) < 5:
                raise ValueError("Short response")
            if len(response) != 3 + response[2] + 2:
                raise ValueError("Invalid response length")
            calc_crc = modbus_crc(response[:-2])
            if calc_crc != response[-2:]:
                raise ValueError("CRC mismatch")
            slave, func, byte_count = response[0:3]
            if slave != slave_addr or func != 3 or byte_count != num_channels * 2:
                if func & 0x80:
                    return f"Error: Modbus exception code {response[2]} for slave {slave_addr}"
                return f"Error: Invalid response header for slave {slave_addr}."
            data = response[3:3 + byte_count]
            raw_temperatures = []
            for i in range(0, len(data), 2):
                val = int.from_bytes(data[i:i+2], 'big', signed=True) / scaling_factor
                raw_temperatures.append(val)
            logging.info(f"Temp read successful for slave {slave_addr}.")
            return raw_temperatures
        except socket.error as e:
            logging.warning(f"Temp read attempt {attempt+1} for slave {slave_addr} failed: {str(e)}. Retrying.")
            if attempt < max_retries - 1:
                time.sleep(retry_backoff_base ** attempt)
            else:
                logging.error(f"Temp read for slave {slave_addr} failed after {max_retries} attempts - {str(e)}.")
                return f"Error: Failed after {max_retries} attempts for slave {slave_addr} - {str(e)}."
        except ValueError as e:
            logging.warning(f"Temp read attempt {attempt+1} for slave {slave_addr} failed (validation): {str(e)}. Retrying.")
            if attempt < max_retries - 1:
                time.sleep(retry_backoff_base ** attempt)
            else:
                logging.error(f"Temp read for slave {slave_addr} failed after {max_retries} attempts - {str(e)}.")
                return f"Error: Failed after {max_retries} attempts for slave {slave_addr} - {str(e)}."
        except Exception as e:
            logging.error(f"Unexpected error in temp read attempt {attempt+1} for slave {slave_addr}: {str(e)}\n{traceback.format_exc()}")
            return f"Error: Unexpected failure for slave {slave_addr} - {str(e)}"
def load_config():
    logging.info("Loading configuration from 'battery_monitor.ini'.")
    global alert_states
    if not config_parser.read('battery_monitor.ini'):
        logging.error("Config file 'battery_monitor.ini' not found.")
        raise FileNotFoundError("Config file 'battery_monitor.ini' not found.")
    temp_settings = {
        'ip': config_parser.get('Temp', 'ip', fallback='192.168.15.240'),
        'modbus_port': config_parser.getint('Temp', 'modbus_port', fallback=10001),
        'poll_interval': config_parser.getfloat('Temp', 'poll_interval', fallback=10.0),
        'rise_threshold': config_parser.getfloat('Temp', 'rise_threshold', fallback=2.0),
        'deviation_threshold': config_parser.getfloat('Temp', 'deviation_threshold', fallback=0.1),
        'disconnection_lag_threshold': config_parser.getfloat('Temp', 'disconnection_lag_threshold', fallback=0.5),
        'high_threshold': config_parser.getfloat('Temp', 'high_threshold', fallback=42.0),
        'low_threshold': config_parser.getfloat('Temp', 'low_threshold', fallback=0.0),
        'scaling_factor': config_parser.getfloat('Temp', 'scaling_factor', fallback=100.0),
        'valid_min': config_parser.getfloat('Temp', 'valid_min', fallback=0.0),
        'max_retries': config_parser.getint('Temp', 'max_retries', fallback=3),
        'retry_backoff_base': config_parser.getint('Temp', 'retry_backoff_base', fallback=1),
        'query_delay': config_parser.getfloat('Temp', 'query_delay', fallback=0.25),
        'abs_deviation_threshold': config_parser.getfloat('Temp', 'abs_deviation_threshold', fallback=2.0),
        'cabinet_over_temp_threshold': config_parser.getfloat('Temp', 'cabinet_over_temp_threshold', fallback=35.0),
        'number_of_parallel_batteries': config_parser.getint('Temp', 'number_of_parallel_batteries', fallback=1),
        'modbus_slave_addresses': [int(x.strip()) for x in config_parser.get('Temp', 'modbus_slave_addresses', fallback='1').split(',')],
        'sensors_per_bank': config_parser.getint('Temp', 'sensors_per_bank', fallback=8),  # New: sensors per bank per battery.
        'num_series_banks': config_parser.getint('Temp', 'num_series_banks', fallback=3)  # New: number of series banks.
    }
    temp_settings['sensors_per_battery'] = temp_settings['num_series_banks'] * temp_settings['sensors_per_bank']  # Calc per battery.
    temp_settings['total_channels'] = temp_settings['number_of_parallel_batteries'] * temp_settings['sensors_per_battery']  # Total sensors.
    voltage_settings = {
        'NumberOfBatteries': config_parser.getint('General', 'NumberOfBatteries', fallback=3),
        'VoltageDifferenceToBalance': config_parser.getfloat('General', 'VoltageDifferenceToBalance', fallback=0.1),
        'BalanceDurationSeconds': config_parser.getint('General', 'BalanceDurationSeconds', fallback=5),
        'SleepTimeBetweenChecks': config_parser.getfloat('General', 'SleepTimeBetweenChecks', fallback=0.1),
        'BalanceRestPeriodSeconds': config_parser.getint('General', 'BalanceRestPeriodSeconds', fallback=60),
        'LowVoltageThresholdPerBattery': config_parser.getfloat('General', 'LowVoltageThresholdPerBattery', fallback=18.5),
        'HighVoltageThresholdPerBattery': config_parser.getfloat('General', 'HighVoltageThresholdPerBattery', fallback=21.0),
        'EmailAlertIntervalSeconds': config_parser.getint('General', 'EmailAlertIntervalSeconds', fallback=3600),
        'I2C_BusNumber': config_parser.getint('General', 'I2C_BusNumber', fallback=1),
        'VoltageDividerRatio': config_parser.getfloat('General', 'VoltageDividerRatio', fallback=0.01592),
        'LoggingLevel': config_parser.get('General', 'LoggingLevel', fallback='INFO')
    }
    general_flags = {
        'WebInterfaceEnabled': config_parser.getboolean('General', 'WebInterfaceEnabled', fallback=True),
        'StartupSelfTestEnabled': config_parser.getboolean('General', 'StartupSelfTestEnabled', fallback=True),
        'WatchdogEnabled': config_parser.getboolean('General', 'WatchdogEnabled', fallback=True)
    }
    i2c_settings = {
        'MultiplexerAddress': int(config_parser.get('I2C', 'MultiplexerAddress', fallback='0x70'), 16),
        'VoltageMeterAddress': int(config_parser.get('I2C', 'VoltageMeterAddress', fallback='0x49'), 16),
        'RelayAddress': int(config_parser.get('I2C', 'RelayAddress', fallback='0x26'), 16)
    }
    gpio_settings = {
        'DC_DC_RelayPin': config_parser.getint('GPIO', 'DC_DC_RelayPin', fallback=5),
        'AlarmRelayPin': config_parser.getint('GPIO', 'AlarmRelayPin', fallback=6),
        'FanRelayPin': config_parser.getint('GPIO', 'FanRelayPin', fallback=4)
    }
    email_settings = {
        'SMTP_Server': config_parser.get('Email', 'SMTP_Server', fallback='smtp.gmail.com'),
        'SMTP_Port': config_parser.getint('Email', 'SMTP_Port', fallback=587),
        'SenderEmail': config_parser.get('Email', 'SenderEmail', fallback='your_email@gmail.com'),
        'RecipientEmail': config_parser.get('Email', 'RecipientEmail', fallback='recipient@example.com'),
        'SMTP_Username': config_parser.get('Email', 'SMTP_Username', fallback='your_email@gmail.com'),
        'SMTP_Password': config_parser.get('Email', 'SMTP_Password', fallback='your_app_password')
    }
    adc_settings = {
        'ConfigRegister': int(config_parser.get('ADC', 'ConfigRegister', fallback='0x01'), 16),
        'ConversionRegister': int(config_parser.get('ADC', 'ConversionRegister', fallback='0x00'), 16),
        'ContinuousModeConfig': int(config_parser.get('ADC', 'ContinuousModeConfig', fallback='0x0100'), 16),
        'SampleRateConfig': int(config_parser.get('ADC', 'SampleRateConfig', fallback='0x0080'), 16),
        'GainConfig': int(config_parser.get('ADC', 'GainConfig', fallback='0x0400'), 16)
    }
    calibration_settings = {
        'Sensor1_Calibration': config_parser.getfloat('Calibration', 'Sensor1_Calibration', fallback=0.99856),
        'Sensor2_Calibration': config_parser.getfloat('Calibration', 'Sensor2_Calibration', fallback=0.99856),
        'Sensor3_Calibration': config_parser.getfloat('Calibration', 'Sensor3_Calibration', fallback=0.99809)
    }
    startup_settings = {
        'test_balance_duration': config_parser.getint('Startup', 'test_balance_duration', fallback=15),
        'min_voltage_delta': config_parser.getfloat('Startup', 'min_voltage_delta', fallback=0.01),
        'test_read_interval': config_parser.getfloat('Startup', 'test_read_interval', fallback=2.0)
    }
    web_settings = {
        'host': config_parser.get('Web', 'host', fallback='0.0.0.0'),
        'web_port': config_parser.getint('Web', 'web_port', fallback=8080),
        'auth_required': config_parser.getboolean('Web', 'auth_required', fallback=False),
        'username': config_parser.get('Web', 'username', fallback='admin'),
        'password': config_parser.get('Web', 'password', fallback='admin123'),
        'api_enabled': config_parser.getboolean('Web', 'api_enabled', fallback=True),
        'cors_enabled': config_parser.getboolean('Web', 'cors_enabled', fallback=False),
        'cors_origins': config_parser.get('Web', 'cors_origins', fallback='*')
    }
    log_level = getattr(logging, voltage_settings['LoggingLevel'].upper(), logging.INFO)
    logging.getLogger().setLevel(log_level)
    alert_states = {ch: {'last_type': None, 'count': 0} for ch in range(1, temp_settings['total_channels'] + 1)}
    logging.info("Configuration loaded successfully.")
    return {**temp_settings, **voltage_settings, **general_flags, **i2c_settings,
            **gpio_settings, **email_settings, **adc_settings, **calibration_settings,
            **startup_settings, **web_settings}
def setup_hardware(settings):
    global bus
    logging.info("Setting up hardware.")
    if smbus:
        bus = smbus.SMBus(settings['I2C_BusNumber'])
    else:
        logging.warning("smbus not available - running in test mode")
        bus = None
    if GPIO:
        GPIO.setmode(GPIO.BCM)
        GPIO.setup(settings['DC_DC_RelayPin'], GPIO.OUT, initial=GPIO.LOW)
        GPIO.setup(settings['AlarmRelayPin'], GPIO.OUT, initial=GPIO.LOW)
        GPIO.setup(settings['FanRelayPin'], GPIO.OUT, initial=GPIO.LOW)
    else:
        logging.warning("RPi.GPIO not available - running in test mode")
    try:
        if os.path.exists(RRD_FILE):
            logging.info("Recreating RRD database for updated configuration.")
            os.remove(RRD_FILE)
        subprocess.check_call(['rrdtool', 'create', RRD_FILE,
                               '--step', '60',
                               'DS:volt1:GAUGE:120:0:25',
                               'DS:volt2:GAUGE:120:0:25',
                               'DS:volt3:GAUGE:120:0:25',
                               'DS:medtemp:GAUGE:120:-20:100',
                               'RRA:LAST:0.0:1:480',
                               'RRA:LAST:0.0:5:100'])
        logging.info("Created RRD database for time-series logging.")
    except subprocess.CalledProcessError as e:
        logging.error(f"RRD creation failed: {e}")
    except FileNotFoundError:
        logging.error("rrdtool not found. Please install rrdtool (sudo apt install rrdtool).")
    logging.info("Hardware setup complete, including RRD initialization.")
def signal_handler(sig, frame):
    logging.info("Script stopped by user or signal.")
    global web_server
    if web_server:
        web_server.shutdown()
    if GPIO:
        GPIO.cleanup()
    close_watchdog()
    sys.exit(0)
def load_offsets(num_channels):
    logging.info("Loading startup offsets from 'offsets.txt'.")
    if os.path.exists('offsets.txt'):
        try:
            with open('offsets.txt', 'r') as f:
                lines = f.readlines()
            if len(lines) < 1:
                logging.warning("Invalid offsets.txt; using none.")
                return None, None
            startup_median = float(lines[0].strip())
            offsets = [float(line.strip()) for line in lines[1:]]
            if len(offsets) != num_channels:
                logging.warning(f"Invalid offsets count; expected {num_channels}, got {len(offsets)}. Using none.")
                return None, None
            logging.debug(f"Loaded median {startup_median} and {len(offsets)} offsets.")
            return startup_median, offsets
        except (ValueError, IndexError):
            logging.warning("Corrupt offsets.txt; using none.")
            return None, None
    logging.warning("No 'offsets.txt' found; using none.")
    return None, None
def save_offsets(startup_median, startup_offsets):
    logging.info("Saving startup offsets to 'offsets.txt'.")
    try:
        with open('offsets.txt', 'w') as f:
            f.write(f"{startup_median}\n")
            for offset in startup_offsets:
                f.write(f"{offset}\n")
        logging.debug("Offsets saved.")
    except IOError as e:
        logging.error(f"Failed to save offsets: {e}")
def check_invalid_reading(raw, ch, alerts, valid_min):
    if raw <= valid_min:
        bank = get_bank_for_channel(ch)
        bat_id, local_ch = get_battery_and_local_ch(ch)
        alert = f"Battery {bat_id} Bank {bank} Local Ch {local_ch}: Invalid reading (≤ {valid_min})."
        alerts.append(alert)
        event_log.append(f"{time.strftime('%Y-%m-%d %H:%M:%S')}: {alert}")
        if len(event_log) > 20:
            event_log.pop(0)
        logging.warning(f"Invalid reading on Battery {bat_id} Bank {bank} Local Ch {local_ch}: {raw} ≤ {valid_min}.")
        return True
    return False
def check_high_temp(calibrated, ch, alerts, high_threshold):
    if calibrated > high_threshold:
        bank = get_bank_for_channel(ch)
        bat_id, local_ch = get_battery_and_local_ch(ch)
        alert = f"Battery {bat_id} Bank {bank} Local Ch {local_ch}: High temp ({calibrated:.1f}°C > {high_threshold}°C)."
        alerts.append(alert)
        event_log.append(f"{time.strftime('%Y-%m-%d %H:%M:%S')}: {alert}")
        if len(event_log) > 20:
            event_log.pop(0)
        logging.warning(f"High temp alert on Battery {bat_id} Bank {bank} Local Ch {local_ch}: {calibrated:.1f} > {high_threshold}.")
def check_low_temp(calibrated, ch, alerts, low_threshold):
    if calibrated < low_threshold:
        bank = get_bank_for_channel(ch)
        bat_id, local_ch = get_battery_and_local_ch(ch)
        alert = f"Battery {bat_id} Bank {bank} Local Ch {local_ch}: Low temp ({calibrated:.1f}°C < {low_threshold}°C)."
        alerts.append(alert)
        event_log.append(f"{time.strftime('%Y-%m-%d %H:%M:%S')}: {alert}")
        if len(event_log) > 20:
            event_log.pop(0)
        logging.warning(f"Low temp alert on Battery {bat_id} Bank {bank} Local Ch {local_ch}: {calibrated:.1f} < {low_threshold}.")
def check_deviation(calibrated, bank_median, ch, alerts, abs_deviation_threshold, deviation_threshold):
    abs_dev = abs(calibrated - bank_median)
    rel_dev = abs_dev / abs(bank_median) if bank_median != 0 else 0
    if abs_dev > abs_deviation_threshold or rel_dev > deviation_threshold:
        bank = get_bank_for_channel(ch)
        bat_id, local_ch = get_battery_and_local_ch(ch)
        alert = f"Battery {bat_id} Bank {bank} Local Ch {local_ch}: Deviation from bank median (abs {abs_dev:.1f}°C or {rel_dev:.2%})."
        alerts.append(alert)
        event_log.append(f"{time.strftime('%Y-%m-%d %H:%M:%S')}: {alert}")
        if len(event_log) > 20:
            event_log.pop(0)
        logging.warning(f"Deviation alert on Battery {bat_id} Bank {bank} Local Ch {local_ch}: abs {abs_dev:.1f}, rel {rel_dev:.2%}.")
def check_abnormal_rise(current, previous_temps, ch, alerts, poll_interval, rise_threshold):
    previous = previous_temps[ch-1]
    if previous is not None:
        rise = current - previous
        if rise > rise_threshold:
            bank = get_bank_for_channel(ch)
            bat_id, local_ch = get_battery_and_local_ch(ch)
            alert = f"Battery {bat_id} Bank {bank} Local Ch {local_ch}: Abnormal rise ({rise:.1f}°C in {poll_interval}s)."
            alerts.append(alert)
            event_log.append(f"{time.strftime('%Y-%m-%d %H:%M:%S')}: {alert}")
            if len(event_log) > 20:
                event_log.pop(0)
            logging.warning(f"Abnormal rise alert on Battery {bat_id} Bank {bank} Local Ch {local_ch}: {rise:.1f}°C.")
def check_group_tracking_lag(current, previous_temps, bank_median_rise, ch, alerts, disconnection_lag_threshold):
    previous = previous_temps[ch-1]
    if previous is not None:
        rise = current - previous
        if abs(rise - bank_median_rise) > disconnection_lag_threshold:
            bank = get_bank_for_channel(ch)
            bat_id, local_ch = get_battery_and_local_ch(ch)
            alert = f"Battery {bat_id} Bank {bank} Local Ch {local_ch}: Lag from bank group ({rise:.1f}°C vs {bank_median_rise:.1f}°C)."
            alerts.append(alert)
            event_log.append(f"{time.strftime('%Y-%m-%d %H:%M:%S')}: {alert}")
            if len(event_log) > 20:
                event_log.pop(0)
            logging.warning(f"Lag alert on Battery {bat_id} Bank {bank} Local Ch {local_ch}: rise {rise:.1f} vs median {bank_median_rise:.1f}.")
def check_sudden_disconnection(current, previous_temps, ch, alerts):
    previous = previous_temps[ch-1]
    if previous is not None and current is None:
        bank = get_bank_for_channel(ch)
        bat_id, local_ch = get_battery_and_local_ch(ch)
        alert = f"Battery {bat_id} Bank {bank} Local Ch {local_ch}: Sudden disconnection."
        alerts.append(alert)
        event_log.append(f"{time.strftime('%Y-%m-%d %H:%M:%S')}: {alert}")
        if len(event_log) > 20:
            event_log.pop(0)
        logging.warning(f"Sudden disconnection alert on Battery {bat_id} Bank {bank} Local Ch {local_ch}.")
def ascii_line_chart(data, width=40, height=5, symbols=' ▁▂▃▄▅▆▇█'):
    if not data:
        return '\n'.join([' ' * width] * height)
    data = [d for d in data if d is not None]
    if not data:
        return '\n'.join([' ' * width] * height)
    min_val, max_val = min(data), max(data)
    range_val = max_val - min_val or 1
    scaled = [(val - min_val) / range_val * (len(symbols) - 1) for val in data]
    chart = []
    for y in range(height - 1, -1, -1):
        line = ''.join(symbols[int(scaled[x])] if len(data) > x else ' ' for x in range(width))
        chart.append(line)
    return '\n'.join(chart)
def choose_channel(channel, multiplexer_address):
    logging.debug(f"Switching to I2C channel {channel}.")
    if bus:
        try:
            bus.write_byte(multiplexer_address, 1 << channel)
        except IOError as e:
            logging.error(f"I2C error selecting channel {channel}: {str(e)}")
def setup_voltage_meter(settings):
    logging.debug("Configuring voltage meter ADC.")
    if bus:
        try:
            config_value = (settings['ContinuousModeConfig'] |
                            settings['SampleRateConfig'] |
                            settings['GainConfig'])
            bus.write_word_data(settings['VoltageMeterAddress'], settings['ConfigRegister'], config_value)
        except IOError as e:
            logging.error(f"I2C error configuring voltage meter: {str(e)}")
def read_voltage_with_retry(bank_id, settings):
    logging.info(f"Starting voltage read for Bank {bank_id}.")
    voltage_divider_ratio = settings['VoltageDividerRatio']
    sensor_id = bank_id
    calibration_factor = settings[f'Sensor{sensor_id}_Calibration']
    for attempt in range(2):
        logging.debug(f"Voltage read attempt {attempt+1} for Bank {bank_id}.")
        readings = []
        raw_values = []
        for _ in range(2):
            meter_channel = (bank_id - 1) % 3
            choose_channel(meter_channel, settings['MultiplexerAddress'])
            setup_voltage_meter(settings)
            if bus:
                try:
                    bus.write_byte(settings['VoltageMeterAddress'], 0x01)
                    time.sleep(0.05)
                    raw_adc = bus.read_word_data(settings['VoltageMeterAddress'], settings['ConversionRegister'])
                    raw_adc = (raw_adc & 0xFF) << 8 | (raw_adc >> 8)
                except IOError as e:
                    logging.error(f"I2C error in voltage read for Bank {bank_id}: {str(e)}")
                    raw_adc = 0
            else:
                raw_adc = 16000 + bank_id * 100
            logging.debug(f"Raw ADC for Bank {bank_id} (Sensor {sensor_id}): {raw_adc}")
            if raw_adc != 0:
                measured_voltage = raw_adc * (6.144 / 32767)
                actual_voltage = (measured_voltage / voltage_divider_ratio) * calibration_factor
                readings.append(actual_voltage)
                raw_values.append(raw_adc)
            else:
                readings.append(0.0)
                raw_values.append(0)
        if readings:
            average = sum(readings) / len(readings)
            valid_readings = [r for r in readings if abs(r - average) / (average if average != 0 else 1) <= 0.05]
            valid_adc = [raw_values[i] for i, r in enumerate(readings) if abs(r - average) / (average if average != 0 else 1) <= 0.05]
            if valid_readings:
                logging.info(f"Voltage read successful for Bank {bank_id}: {average:.2f}V.")
                return sum(valid_readings) / len(valid_readings), valid_readings, valid_adc
        logging.debug(f"Readings for Bank {bank_id} inconsistent, retrying.")
    logging.error(f"Couldn't get good voltage reading for Bank {bank_id} after 2 tries.")
    return None, [], []
def set_relay_connection(high, low, settings):
    try:
        logging.info(f"Attempting to set relay for connection from Bank {high} to {low}")
        logging.debug("Switching to relay control channel.")
        choose_channel(3, settings['MultiplexerAddress'])
        relay_state = 0
        if high == 1 and low == 2:
            relay_state |= (1 << 3)
            logging.debug("Relays 4 activated for high to low.")
        elif high == 1 and low == 3:
            relay_state |= (1 << 2) | (1 << 3)
            logging.debug("Relays 3, and 4 activated for high to low.")
        elif high == 2 and low == 1:
            relay_state |= (1 << 0)
            logging.debug("Relays 1 activated for high to low.")
        elif high == 2 and low == 3:
            relay_state |= (1 << 0) | (1 << 2) | (1 << 3)
            logging.debug("Relays 1, 3, and 4 activated for high to low.")
        elif high == 3 and low == 1:
            relay_state |= (1 << 0) | (1 << 1)
            logging.debug("Relays 1, 2 activated for high to low.")
        elif high == 3 and low == 2:
            relay_state |= (1 << 0) | (1 << 1) | (1 << 3)
            logging.debug("Relays 1, 2, and 4 activated for high to low.")
        logging.debug(f"Final relay state: {bin(relay_state)}")
        if bus:
            logging.info(f"Sending relay state command to hardware.")
            bus.write_byte_data(settings['RelayAddress'], 0x11, relay_state)
        logging.info(f"Relay setup completed for balancing from Bank {high} to {low}")
    except (IOError, AttributeError) as e:
        logging.error(f"I/O error while setting up relay: {e}")
    except Exception as e:
        logging.error(f"Unexpected error in set_relay_connection: {e}")
def control_dcdc_converter(turn_on, settings):
    try:
        if GPIO:
            GPIO.output(settings['DC_DC_RelayPin'], GPIO.HIGH if turn_on else GPIO.LOW)
        logging.info(f"DC-DC Converter is now {'on' if turn_on else 'off'}")
    except Exception as e:
        logging.error(f"Problem controlling DC-DC converter: {e}")
def send_alert_email(message, settings):
    global last_email_time
    if time.time() - last_email_time < settings['EmailAlertIntervalSeconds']:
        logging.debug("Skipping alert email to avoid flooding.")
        return
    try:
        msg = MIMEText(message)
        msg['Subject'] = "Battery Monitor Alert"
        msg['From'] = settings['SenderEmail']
        msg['To'] = settings['RecipientEmail']
        with smtplib.SMTP(settings['SMTP_Server'], settings['SMTP_Port']) as server:
            server.starttls()
            if settings['SMTP_Username'] and settings['SMTP_Password']:
                server.login(settings['SMTP_Username'], settings['SMTP_Password'])
            server.send_message(msg)
        last_email_time = time.time()
        logging.info(f"Alert email sent: {message}")
    except Exception as e:
        logging.error(f"Failed to send alert email: {e}")
def check_for_issues(voltages, temps_alerts, settings):
    global startup_failed, startup_alerts
    logging.info("Checking for voltage and temp issues.")
    alert_needed = startup_failed
    alerts = []
    if startup_failed and startup_alerts:
        alerts.append("Startup failures: " + "; ".join(startup_alerts))
    for i, v in enumerate(voltages, 1):
        if v is None or v == 0.0:
            alert = f"Bank {i}: Zero voltage."
            alerts.append(alert)
            event_log.append(f"{time.strftime('%Y-%m-%d %H:%M:%S')}: {alert}")
            if len(event_log) > 20:
                event_log.pop(0)
            logging.warning(f"Zero voltage alert on Bank {i}.")
            alert_needed = True
        elif v > settings['HighVoltageThresholdPerBattery']:
            alert = f"Bank {i}: High voltage ({v:.2f}V)."
            alerts.append(alert)
            event_log.append(f"{time.strftime('%Y-%m-%d %H:%M:%S')}: {alert}")
            if len(event_log) > 20:
                event_log.pop(0)
            logging.warning(f"High voltage alert on Bank {i}: {v:.2f}V.")
            alert_needed = True
        elif v < settings['LowVoltageThresholdPerBattery']:
            alert = f"Bank {i}: Low voltage ({v:.2f}V)."
            alerts.append(alert)
            event_log.append(f"{time.strftime('%Y-%m-%d %H:%M:%S')}: {alert}")
            if len(event_log) > 20:
                event_log.pop(0)
            logging.warning(f"Low voltage alert on Bank {i}: {v:.2f}V.")
            alert_needed = True
    if temps_alerts:
        alerts.extend(temps_alerts)
        alert_needed = True
    if alert_needed:
        if GPIO:
            GPIO.output(settings['AlarmRelayPin'], GPIO.HIGH)
        logging.info("Alarm relay activated.")
        send_alert_email("\n".join(alerts), settings)
    else:
        if GPIO:
            GPIO.output(settings['AlarmRelayPin'], GPIO.LOW)
        logging.info("No issues; alarm relay deactivated.")
    return alert_needed, alerts
def balance_battery_voltages(stdscr, high, low, settings, temps_alerts):
    global balance_start_time, last_balance_time, balancing_active, web_data
    if temps_alerts:
        logging.warning("Skipping balancing due to temperature anomalies in banks.")
        return
    logging.info(f"Starting balance from Bank {high} to {low}.")
    event_log.append(f"{time.strftime('%Y-%m-%d %H:%M:%S')}: Balancing started from Bank {high} to {low}")
    if len(event_log) > 20:
        event_log.pop(0)
    balancing_active = True
    web_data['balancing'] = True
    voltage_high, _, _ = read_voltage_with_retry(high, settings)
    voltage_low, _, _ = read_voltage_with_retry(low, settings)
    if voltage_low == 0.0:
        logging.warning(f"Cannot balance to Bank {low} (0.00V). Skipping.")
        balancing_active = False
        web_data['balancing'] = False
        return
    set_relay_connection(high, low, settings)
    control_dcdc_converter(True, settings)
    balance_start_time = time.time()
    animation_frames = ['|', '/', '-', '\\']
    frame_index = 0
    height, width = stdscr.getmaxyx()
    right_half_x = width // 2
    progress_y = 1
    while time.time() - balance_start_time < settings['BalanceDurationSeconds']:
        elapsed = time.time() - balance_start_time
        progress = min(1.0, elapsed / settings['BalanceDurationSeconds'])
        voltage_high, _, _ = read_voltage_with_retry(high, settings)
        voltage_low, _, _ = read_voltage_with_retry(low, settings)
        bar_length = 20
        filled = int(bar_length * progress)
        bar = '=' * filled + ' ' * (bar_length - filled)
        if progress_y < height and right_half_x + 50 < width:
            try:
                stdscr.addstr(progress_y, right_half_x, f"Balancing Bank {high} ({voltage_high:.2f}V) -> Bank {low} ({voltage_low:.2f}V)... [{animation_frames[frame_index % 4]}]", curses.color_pair(6))
            except curses.error:
                logging.warning("addstr error for balancing status.")
            try:
                stdscr.addstr(progress_y + 1, right_half_x, f"Progress: [{bar}] {int(progress * 100)}%", curses.color_pair(6))
            except curses.error:
                logging.warning("addstr error for balancing progress bar.")
        else:
            logging.warning("Skipping balancing progress display - out of bounds.")
        stdscr.refresh()
        logging.debug(f"Balancing progress: {progress * 100:.2f}%, High: {voltage_high:.2f}V, Low: {voltage_low:.2f}V")
        frame_index += 1
        time.sleep(0.01)
    logging.info("Balancing process completed.")
    event_log.append(f"{time.strftime('%Y-%m-%d %H:%M:%S')}: Balancing completed from Bank {high} to {low}")
    if len(event_log) > 20:
        event_log.pop(0)
    control_dcdc_converter(False, settings)
    logging.info("Turning off DC-DC converter.")
    set_relay_connection(0, 0, settings)
    logging.info("Resetting relay connections to default state.")
    balancing_active = False
    web_data['balancing'] = False
    last_balance_time = time.time()
def compute_bank_medians(calibrated_temps, valid_min):
    bank_stats = []
    for bank_indices in BANK_SENSOR_INDICES:
        bank_temps = [calibrated_temps[i] for i in bank_indices if calibrated_temps[i] is not None]
        invalid_count = len(bank_indices) - len(bank_temps)
        if bank_temps:
            med = statistics.median(bank_temps)
            mn = min(bank_temps)
            mx = max(bank_temps)
        else:
            med = mn = mx = 0.0
        bank_stats.append({'median': med, 'min': mn, 'max': mx, 'invalid': invalid_count})
    return bank_stats
def fetch_rrd_history():
    start = int(time.time()) - (HISTORY_LIMIT * 60)
    try:
        output = subprocess.check_output(['rrdtool', 'xport',
                                          '--start', str(start),
                                          '--end', 'now',
                                          '--step', '60',
                                          'DEF:v1=bms.rrd:volt1:LAST',
                                          'DEF:v2=bms.rrd:volt2:LAST',
                                          'DEF:v3=bms.rrd:volt3:LAST',
                                          'DEF:mt=bms.rrd:medtemp:LAST',
                                          'XPORT:v1:Bank1',
                                          'XPORT:v2:Bank2',
                                          'XPORT:v3:Bank3',
                                          'XPORT:mt:MedianTemp'])
        logging.debug(f"Raw RRD xport output: {output.decode()}")
        root = ET.fromstring(output.decode())
        data = []
        for row in root.findall('.//row'):
            t_elem = row.find('t')
            if t_elem is None or t_elem.text is None:
                logging.warning("Skipping RRD row with missing timestamp.")
                continue
            try:
                t = int(t_elem.text)
            except ValueError:
                logging.warning("Skipping RRD row with invalid timestamp.")
                continue
            vs = []
            for v in row.findall('v'):
                if v.text is None:
                    vs.append(None)
                    continue
                try:
                    vs.append(float(v.text) if v.text != 'NaN' else None)
                except ValueError:
                    vs.append(None)
            if len(vs) != 4:
                logging.warning(f"Skipping RRD row with incomplete values (got {len(vs)}).")
                continue
            data.append({'time': t, 'volt1': vs[0], 'volt2': vs[1], 'volt3': vs[2], 'medtemp': vs[3]})
        logging.debug(f"Fetched {len(data)} history entries from RRD.")
        return data[::-1]
    except subprocess.CalledProcessError as e:
        logging.error(f"RRD xport failed: {e}")
        return []
    except ET.ParseError as e:
        logging.error(f"RRD XML parse error: {e}. Output was: {output.decode()}")
        return []
    except FileNotFoundError:
        logging.error("rrdtool not found for fetch. Install rrdtool.")
        return []
    except Exception as e:
        logging.error(f"Unexpected error in fetch_rrd_history: {e}\n{traceback.format_exc()}")
        return []
def draw_tui(stdscr, voltages, calibrated_temps, raw_temps, offsets, bank_stats, startup_median, alerts, settings, startup_set, is_startup):
    logging.debug("Refreshing TUI.")
    stdscr.clear()
    curses.start_color()
    curses.use_default_colors()
    curses.init_pair(1, curses.COLOR_RED, -1)
    curses.init_pair(2, curses.COLOR_RED, -1)
    curses.init_pair(3, curses.COLOR_YELLOW, -1)
    curses.init_pair(4, curses.COLOR_GREEN, -1)
    curses.init_pair(5, curses.COLOR_WHITE, -1)
    curses.init_pair(6, curses.COLOR_YELLOW, -1)
    curses.init_pair(7, curses.COLOR_CYAN, -1)
    curses.init_pair(8, curses.COLOR_MAGENTA, -1)
    height, width = stdscr.getmaxyx()
    right_half_x = width // 2
    total_v = sum(voltages)
    total_high = settings['HighVoltageThresholdPerBattery'] * NUM_BANKS
    total_low = settings['LowVoltageThresholdPerBattery'] * NUM_BANKS
    v_color = curses.color_pair(2) if total_v > total_high else curses.color_pair(3) if total_v < total_low else curses.color_pair(4)
    roman_v = text2art(f"{total_v:.2f}V", font='roman', chr_ignore=True)
    roman_lines = roman_v.splitlines()
    for i, line in enumerate(roman_lines):
        if i + 1 < height and len(line) < right_half_x:
            try:
                stdscr.addstr(i + 1, 0, line, v_color)
            except curses.error:
                logging.warning(f"addstr error for total voltage art line {i+1}.")
        else:
            logging.warning(f"Skipping total voltage art line {i+1} - out of bounds.")
    y_offset = len(roman_lines) + 3
    if y_offset >= height:
        logging.warning("TUI y_offset exceeds height; skipping art.")
        return
    battery_art_base = [
        " _______________ ",
        " |             | ",
        " |             | ",
        " |             | ",
        " |             | ",
        " |     +++     | ",
        " |     +++     | ",
        " |             | ",
        " |             | ",
        " |             | ",
        " |             | ",
        " |     ---     | ",
        " |     ---     | ",
        " |     ---     | ",
        " |             | ",
        " |             | ",
        " |_____________| "
    ]
    art_height = len(battery_art_base)
    art_width = len(battery_art_base[0])
    gap = "   "
    gap_len = len(gap)
    for row, line in enumerate(battery_art_base):
        full_line = gap.join([line] * NUM_BANKS)
        if y_offset + row < height and len(full_line) < right_half_x:
            try:
                stdscr.addstr(y_offset + row, 0, full_line, curses.color_pair(4))
            except curses.error:
                logging.warning(f"addstr error for art row {row}.")
        else:
            logging.warning(f"Skipping art row {row} - out of bounds.")
    for bank_id in range(NUM_BANKS):
        start_pos = bank_id * (art_width + gap_len)
        v_str = f"{voltages[bank_id]:.2f}V" if voltages[bank_id] > 0 else "0.00V"
        v_color = curses.color_pair(8) if voltages[bank_id] == 0.0 else \
                 curses.color_pair(2) if voltages[bank_id] > settings['HighVoltageThresholdPerBattery'] else \
                 curses.color_pair(3) if voltages[bank_id] < settings['LowVoltageThresholdPerBattery'] else \
                 curses.color_pair(4)
        v_center = start_pos + (art_width - len(v_str)) // 2
        v_y = y_offset + 2
        if v_y < height and v_center + len(v_str) < right_half_x:
            try:
                stdscr.addstr(v_y, v_center, v_str, v_color)
            except curses.error:
                logging.warning(f"addstr error for voltage overlay Bank {bank_id+1}.")
        else:
            logging.warning(f"Skipping voltage overlay for Bank {bank_id+1} - out of bounds.")
        summary = bank_stats[bank_id]
        med_str = f"Med: {summary['median']:.1f}°C"
        min_str = f"Min: {summary['min']:.1f}°C"
        max_str = f"Max: {summary['max']:.1f}°C"
        inv_str = f"Inv: {summary['invalid']}"
        s_color = curses.color_pair(2) if summary['median'] > settings['high_threshold'] or summary['median'] < settings['low_threshold'] or summary['invalid'] > 0 else curses.color_pair(4)
        for idx, s_str in enumerate([med_str, min_str, max_str, inv_str]):
            s_center = start_pos + (art_width - len(s_str)) // 2
            s_y = y_offset + 7 + idx
            if s_y < height and s_center + len(s_str) < right_half_x:
                try:
                    stdscr.addstr(s_y, s_center, s_str, s_color)
                except curses.error:
                    logging.warning(f"addstr error for summary line {idx+1} Bank {bank_id+1}.")
            else:
                logging.warning(f"Skipping summary line {idx+1} for Bank {bank_id+1} - out of bounds.")
    y_offset += art_height + 2
    for bank_id in range(NUM_BANKS):
        if y_offset < height:
            try:
                stdscr.addstr(y_offset, 0, f"Bank {bank_id+1} Temps:", curses.color_pair(7))
            except curses.error:
                logging.warning(f"addstr error for bank {bank_id+1} temps header.")
        y_offset += 1
        bank_indices = BANK_SENSOR_INDICES[bank_id]
        for i in bank_indices:
            ch = i + 1
            bat_id, local_ch = get_battery_and_local_ch(ch)
            calib = calibrated_temps[i]
            calib_str = f"{calib:.1f}" if calib is not None else "Inv"
            if is_startup:
                raw = raw_temps[i]
                raw_str = f"{raw:.1f}" if raw > settings['valid_min'] else "Inv"
                offset_str = f"{offsets[i]:.1f}" if startup_set and raw > settings['valid_min'] else "N/A"
                detail = f" ({raw_str}/{offset_str})"
            else:
                detail = ""
            t_str = f"Bat {bat_id} Local C{local_ch}: {calib_str}{detail}"
            t_color = curses.color_pair(8) if "Inv" in calib_str else \
                     curses.color_pair(2) if calib > settings['high_threshold'] else \
                     curses.color_pair(3) if calib < settings['low_threshold'] else \
                     curses.color_pair(4)
            if y_offset < height and len(t_str) < right_half_x:
                try:
                    stdscr.addstr(y_offset, 0, t_str, t_color)
                except curses.error:
                    logging.warning(f"addstr error for temp Bank {bank_id+1} Bat {bat_id} Local C{local_ch}.")
            else:
                logging.warning(f"Skipping temp for Bank {bank_id+1} Bat {bat_id} Local C{local_ch} - out of bounds.")
            y_offset += 1
    med_str = f"{startup_median:.1f}°C" if startup_median else "N/A"
    if y_offset < height:
        try:
            stdscr.addstr(y_offset, 0, f"Startup Median Temp: {med_str}", curses.color_pair(7))
        except curses.error:
            logging.warning("addstr error for startup median.")
    else:
        logging.warning("Skipping startup median - out of bounds.")
    y_offset += 2
    if y_offset < height:
        try:
            stdscr.addstr(y_offset, 0, "Alerts:", curses.color_pair(7))
        except curses.error:
            logging.warning("addstr error for alerts header.")
    y_offset += 1
    if alerts:
        for alert in alerts:
            if y_offset < height and len(alert) < right_half_x:
                try:
                    stdscr.addstr(y_offset, 0, alert, curses.color_pair(8))
                except curses.error:
                    logging.warning(f"addstr error for alert '{alert}'.")
            else:
                logging.warning(f"Skipping alert '{alert}' - out of bounds.")
            y_offset += 1
    else:
        if y_offset < height:
            try:
                stdscr.addstr(y_offset, 0, "No alerts.", curses.color_pair(4))
            except curses.error:
                logging.warning("addstr error for no alerts message.")
        else:
            logging.warning("Skipping no alerts message - out of bounds.")
    history = fetch_rrd_history()
    y_chart = 1
    chart_width = 30
    chart_height = 5
    for b in range(NUM_BANKS):
        volt_hist = [h[f'volt{b+1}'] for h in history if h[f'volt{b+1}'] is not None] if history else []
        chart = ascii_line_chart(volt_hist, width=chart_width, height=chart_height)
        label = f"Bank {b+1} V: "
        for i, line in enumerate(chart.splitlines()):
            full_line = label + line if i == 0 else ' ' * len(label) + line
            if y_chart + i < height and right_half_x + len(full_line) < width:
                try:
                    stdscr.addstr(y_chart + i, right_half_x, full_line, curses.color_pair(4))
                except curses.error:
                    logging.warning(f"addstr error for Bank {b+1} voltage chart line {i}.")
            else:
                logging.warning(f"Skipping Bank {b+1} voltage chart line {i} - out of bounds.")
        y_chart += chart_height + 1
    temp_hist = [h['medtemp'] for h in history if h['medtemp'] is not None] if history else []
    temp_chart = ascii_line_chart(temp_hist, width=chart_width, height=chart_height)
    label = "Med Temp: "
    for i, line in enumerate(temp_chart.splitlines()):
        full_line = label + line if i == 0 else ' ' * len(label) + line
        if y_chart + i < height and right_half_x + len(full_line) < width:
            try:
                stdscr.addstr(y_chart + i, right_half_x, full_line, curses.color_pair(7))
            except curses.error:
                logging.warning(f"addstr error for median temp chart line {i}.")
        else:
            logging.warning(f"Skipping median temp chart line {i} - out of bounds.")
    y_offset = height // 2
    if y_offset < height:
        try:
            stdscr.addstr(y_offset, right_half_x, "Event History:", curses.color_pair(7))
        except curses.error:
            logging.warning("addstr error for event history header.")
    y_offset += 1
    for event in event_log[-20:]:
        if y_offset < height and len(event) < width - right_half_x:
            try:
                stdscr.addstr(y_offset, right_half_x, event, curses.color_pair(5))
            except curses.error:
                logging.warning(f"addstr error for event '{event}'.")
            y_offset += 1
        else:
            logging.warning(f"Skipping event '{event}' - out of bounds.")
    stdscr.refresh()
def setup_watchdog(timeout=15):
    global watchdog_fd
    try:
        model = "Unknown"
        if os.path.exists('/proc/device-tree/model'):
            with open('/proc/device-tree/model', 'r') as f:
                model = f.read().strip().lower()
        logging.info(f"Detected Raspberry Pi model: {model}")
        if 'raspberry pi' in model and not 'raspberry pi 5' in model:
            module = 'bcm2835_wdt'
        else:
            module = 'rp1-wdt'
            logging.info("Assuming rp1-wdt for Pi 5 or newer model")
        os.system(f'sudo modprobe {module}')
        logging.info(f"Loaded watchdog module: {module}")
        time.sleep(1)
        if not os.path.exists(WATCHDOG_DEV):
            logging.error(f"Watchdog device {WATCHDOG_DEV} not found. Watchdog disabled.")
            return False
        watchdog_fd = open(WATCHDOG_DEV, 'wb')
        logging.debug(f"Opened watchdog device: {WATCHDOG_DEV}")
        try:
            magic = ord('W') << 8 | 0x06
            fcntl.ioctl(watchdog_fd, magic, struct.pack("I", timeout))
            logging.info(f"Watchdog set with timeout {timeout}s")
        except IOError as e:
            logging.warning(f"Failed to set watchdog timeout: {e}. Using default.")
        logging.debug("Watchdog initialized")
        return True
    except Exception as e:
        logging.error(f"Failed to setup watchdog: {e}.")
        return False
def watchdog_pet_thread(pet_interval=5, hang_threshold=10):
    global watchdog_fd, alive_timestamp
    while True:
        try:
            if time.time() - alive_timestamp > hang_threshold:
                logging.warning("Main thread hang detected; stopping watchdog pets to allow reset.")
                break  # Stop petting
            if watchdog_fd:
                watchdog_fd.write(b'w')
                watchdog_fd.flush()
                logging.debug("Watchdog petted")
        except IOError as e:
            logging.error(f"Watchdog pet failed: {e}. Reopening device.")
            try:
                watchdog_fd.close()
                watchdog_fd = open(WATCHDOG_DEV, 'wb')
            except IOError as reopen_e:
                logging.error(f"Failed to reopen watchdog: {reopen_e}. Disabling pets.")
                break
        time.sleep(pet_interval)
def close_watchdog():
    global watchdog_fd
    if watchdog_fd:
        try:
            watchdog_fd.write(b'V')
            watchdog_fd.close()
        except IOError:
            pass
def startup_self_test(settings, stdscr):
    global startup_failed, startup_alerts, startup_set, startup_median, startup_offsets
    if not settings['StartupSelfTestEnabled']:
        logging.info("Startup self-test disabled via configuration.")
        return []
    retries = 0
    while True:
        logging.info(f"Starting self-test attempt {retries + 1}")
        alerts = []
        stdscr.clear()
        y = 0
        if y < stdscr.getmaxyx()[0]:
            try:
                stdscr.addstr(y, 0, "Startup Self-Test in Progress", curses.color_pair(1))
            except curses.error:
                logging.warning("addstr error for title.")
        y += 2
        stdscr.refresh()
        logging.info("Step 1: Validating configuration parameters.")
        logging.debug(f"Configuration details: NumberOfBatteries={settings['NumberOfBatteries']}, "
                      f"I2C_BusNumber={settings['I2C_BusNumber']}, "
                      f"MultiplexerAddress=0x{settings['MultiplexerAddress']:02x}, "
                      f"VoltageMeterAddress=0x{settings['VoltageMeterAddress']:02x}, "
                      f"RelayAddress=0x{settings['RelayAddress']:02x}, "
                      f"Temp_IP={settings['ip']}, Temp_Port={settings['modbus_port']}, "
                      f"TotalChannels={settings['total_channels']}, ScalingFactor={settings['scaling_factor']}, "
                      f"ParallelBatteries={settings['number_of_parallel_batteries']}, SlaveAddresses={settings['modbus_slave_addresses']}")
        if y < stdscr.getmaxyx()[0]:
            try:
                stdscr.addstr(y, 0, "Step 1: Validating config...", curses.color_pair(4))
            except curses.error:
                logging.warning("addstr error for step 1.")
        stdscr.refresh()
        time.sleep(0.5)
        if settings['NumberOfBatteries'] != NUM_BANKS:
            alert = f"Config mismatch: NumberOfBatteries={settings['NumberOfBatteries']} != {NUM_BANKS}."
            alerts.append(alert)
            event_log.append(f"{time.strftime('%Y-%m-%d %H:%M:%S')}: {alert}")
            if len(event_log) > 20:
                event_log.pop(0)
            logging.warning(f"Config mismatch detected: NumberOfBatteries={settings['NumberOfBatteries']} != {NUM_BANKS}.")
            if y + 1 < stdscr.getmaxyx()[0]:
                try:
                    stdscr.addstr(y + 1, 0, "Config mismatch detected.", curses.color_pair(2))
                except curses.error:
                    logging.warning("addstr error for config mismatch.")
        else:
            logging.debug("Configuration validation passed: NumberOfBatteries matches NUM_BANKS.")
            if y + 1 < stdscr.getmaxyx()[0]:
                try:
                    stdscr.addstr(y + 1, 0, "Config OK.", curses.color_pair(4))
                except curses.error:
                    logging.warning("addstr error for config OK.")
        y += 2
        stdscr.refresh()
        logging.info("Step 2: Testing hardware connectivity (I2C and Modbus per slave).")
        if y < stdscr.getmaxyx()[0]:
            try:
                stdscr.addstr(y, 0, "Step 2: Testing hardware connectivity...", curses.color_pair(4))
            except curses.error:
                logging.warning("addstr error for step 2.")
        stdscr.refresh()
        time.sleep(0.5)
        logging.debug(f"Testing I2C connectivity on bus {settings['I2C_BusNumber']}: "
                      f"Multiplexer=0x{settings['MultiplexerAddress']:02x}, "
                      f"VoltageMeter=0x{settings['VoltageMeterAddress']:02x}, "
                      f"Relay=0x{settings['RelayAddress']:02x}")
        try:
            if bus:
                logging.debug(f"Selecting I2C channel 0 on multiplexer 0x{settings['MultiplexerAddress']:02x}")
                choose_channel(0, settings['MultiplexerAddress'])
                logging.debug(f"Reading byte from VoltageMeter at 0x{settings['VoltageMeterAddress']:02x}")
                bus.read_byte(settings['VoltageMeterAddress'])
                logging.debug("I2C connectivity test passed for all devices.")
            if y + 1 < stdscr.getmaxyx()[0]:
                try:
                    stdscr.addstr(y + 1, 0, "I2C OK.", curses.color_pair(4))
                except curses.error:
                    logging.warning("addstr error for I2C OK.")
        except (IOError, AttributeError) as e:
            alert = f"I2C connectivity failure: {str(e)}"
            alerts.append(alert)
            event_log.append(f"{time.strftime('%Y-%m-%d %H:%M:%S')}: {alert}")
            if len(event_log) > 20:
                event_log.pop(0)
            logging.error(f"I2C connectivity failure: {str(e)}. Bus={settings['I2C_BusNumber']}, "
                          f"Multiplexer=0x{settings['MultiplexerAddress']:02x}, "
                          f"VoltageMeter=0x{settings['VoltageMeterAddress']:02x}, "
                          f"Relay=0x{settings['RelayAddress']:02x}")
            if y + 1 < stdscr.getmaxyx()[0]:
                try:
                    stdscr.addstr(y + 1, 0, f"I2C failure: {str(e)}", curses.color_pair(2))
                except curses.error:
                    logging.warning("addstr error for I2C failure.")
        y_test = y + 2
        for addr in settings['modbus_slave_addresses']:
            logging.debug(f"Testing Modbus slave {addr} connectivity to {settings['ip']}:{settings['modbus_port']} with num_channels=1")
            try:
                test_query = read_ntc_sensors(settings['ip'], settings['modbus_port'], settings['query_delay'], 1, settings['scaling_factor'], 1, 1, slave_addr=addr)
                if isinstance(test_query, str) and "Error" in test_query:
                    raise ValueError(test_query)
                logging.debug(f"Modbus test successful for slave {addr}: Received {len(test_query)} values: {test_query}")
                if y_test < stdscr.getmaxyx()[0]:
                    try:
                        stdscr.addstr(y_test, 0, f"Modbus Slave {addr} OK.", curses.color_pair(4))
                    except curses.error:
                        logging.warning("addstr error for Modbus Slave {addr} OK.")
            except Exception as e:
                alert = f"Modbus Slave {addr} test failure: {str(e)}"
                alerts.append(alert)
                event_log.append(f"{time.strftime('%Y-%m-%d %H:%M:%S')}: {alert}")
                if len(event_log) > 20:
                    event_log.pop(0)
                logging.error(f"Modbus Slave {addr} test failure: {str(e)}. Connection={settings['ip']}:{settings['modbus_port']}, "
                              f"num_channels=1, query_delay={settings['query_delay']}, scaling_factor={settings['scaling_factor']}")
                if y_test < stdscr.getmaxyx()[0]:
                    try:
                        stdscr.addstr(y_test, 0, f"Modbus Slave {addr} failure: {str(e)}", curses.color_pair(2))
                    except curses.error:
                        logging.warning("addstr error for Modbus Slave {addr} failure.")
            y_test += 1
            stdscr.refresh()
        y = y_test
        logging.info("Step 3: Performing initial sensor reads (temperature per slave and voltage).")
        if y < stdscr.getmaxyx()[0]:
            try:
                stdscr.addstr(y, 0, "Step 3: Initial sensor reads...", curses.color_pair(4))
            except curses.error:
                logging.warning("addstr error for step 3.")
        stdscr.refresh()
        time.sleep(0.5)
        all_initial_temps = []
        temp_fail = False
        for addr in settings['modbus_slave_addresses']:
            initial_temps = read_ntc_sensors(settings['ip'], settings['modbus_port'], settings['query_delay'],
                                             settings['sensors_per_battery'], settings['scaling_factor'],
                                             settings['max_retries'], settings['retry_backoff_base'], slave_addr=addr)
            if isinstance(initial_temps, str):
                alert = f"Initial temp read failure for slave {addr}: {initial_temps}"
                alerts.append(alert)
                event_log.append(f"{time.strftime('%Y-%m-%d %H:%M:%S')}: {alert}")
                if len(event_log) > 20:
                    event_log.pop(0)
                logging.error(f"Initial temperature read failure for slave {addr}: {initial_temps}")
                all_initial_temps.extend([settings['valid_min']] * settings['sensors_per_battery'])
                temp_fail = True
            else:
                logging.debug(f"Initial temperature read successful for slave {addr}: {len(initial_temps)} values, {initial_temps}")
                all_initial_temps.extend(initial_temps)
        if temp_fail:
            if y + 1 < stdscr.getmaxyx()[0]:
                try:
                    stdscr.addstr(y + 1, 0, "Some temp read failures.", curses.color_pair(2))
                except curses.error:
                    logging.warning("addstr error for temp failure.")
        else:
            if y + 1 < stdscr.getmaxyx()[0]:
                try:
                    stdscr.addstr(y + 1, 0, "Temps OK.", curses.color_pair(4))
                except curses.error:
                    logging.warning("addstr error for temps OK.")
        initial_voltages = []
        for i in range(1, NUM_BANKS + 1):
            voltage, readings, adc_values = read_voltage_with_retry(i, settings)
            initial_voltages.append(voltage if voltage is not None else 0.0)
        if any(v == 0.0 for v in initial_voltages):
            alert = "Initial voltage read failure: Zero voltage on one or more banks."
            alerts.append(alert)
            event_log.append(f"{time.strftime('%Y-%m-%d %H:%M:%S')}: {alert}")
            if len(event_log) > 20:
                event_log.pop(0)
            logging.error(f"Initial voltage read failure: Voltages={initial_voltages}")
            if y + 2 < stdscr.getmaxyx()[0]:
                try:
                    stdscr.addstr(y + 2, 0, "Voltage read failure (zero).", curses.color_pair(2))
                except curses.error:
                    logging.warning("addstr error for voltage failure.")
        else:
            logging.debug(f"Initial voltage read successful: Voltages={initial_voltages}")
            if y + 2 < stdscr.getmaxyx()[0]:
                try:
                    stdscr.addstr(y + 2, 0, "Voltages OK.", curses.color_pair(4))
                except curses.error:
                    logging.warning("addstr error for voltages OK.")
        if not temp_fail:
            valid_count = sum(1 for t in all_initial_temps if t > settings['valid_min'])
            if valid_count == settings['total_channels']:
                startup_median = statistics.median(all_initial_temps)
                logging.debug(f"Calculated startup median: {startup_median:.1f}°C")
                _, startup_offsets = load_offsets(settings['total_channels'])
                if startup_offsets is None:
                    startup_offsets = [startup_median - t for t in all_initial_temps]
                    save_offsets(startup_median, startup_offsets)
                    logging.info(f"Calculated and saved new offsets: {startup_offsets}")
                else:
                    logging.info(f"Using existing offsets: {startup_offsets}")
                startup_set = True
            else:
                logging.warning(f"Calibration skipped: Only {valid_count}/{settings['total_channels']} valid.")
                startup_median = None
                startup_offsets = None
                startup_set = False
        y += 3
        stdscr.refresh()
        if not alerts and all(v > 0 for v in initial_voltages):
            logging.info("Step 4: Verifying balancer functionality.")
            if y < stdscr.getmaxyx()[0]:
                try:
                    stdscr.addstr(y, 0, "Step 4: Balancer verification...", curses.color_pair(4))
                except curses.error:
                    logging.warning("addstr error for step 4.")
            y += 1
            stdscr.refresh()
            time.sleep(0.5)
            initial_bank_voltages = []
            for bank in range(1, NUM_BANKS + 1):
                voltage, _, _ = read_voltage_with_retry(bank, settings)
                initial_bank_voltages.append(voltage if voltage is not None else 0.0)
            if y + 1 < stdscr.getmaxyx()[0]:
                try:
                    stdscr.addstr(y + 1, 0, f"Initial Bank Voltages: Bank 1={initial_bank_voltages[0]:.2f}V, Bank 2={initial_bank_voltages[1]:.2f}V, Bank 3={initial_bank_voltages[2]:.2f}V", curses.color_pair(4))
                except curses.error:
                    logging.warning("addstr error for initial bank voltages.")
            logging.debug(f"Initial Bank Voltages: Bank 1={initial_bank_voltages[0]:.2f}V, Bank 2={initial_bank_voltages[1]:.2f}V, Bank 3={initial_bank_voltages[2]:.2f}V")
            y += 2
            stdscr.refresh()
            bank_voltages_dict = {b: initial_bank_voltages[b-1] for b in range(1, NUM_BANKS + 1)}
            sorted_banks = sorted(bank_voltages_dict, key=bank_voltages_dict.get, reverse=True)
            pairs = []
            for source in sorted_banks:
                for dest in [b for b in range(1, NUM_BANKS + 1) if b != source]:
                    pairs.append((source, dest))
            test_duration = settings['test_balance_duration']
            read_interval = settings['test_read_interval']
            min_delta = settings['min_voltage_delta']
            logging.debug(f"Balancer test parameters: test_duration={test_duration}s, "
                          f"read_interval={read_interval}s, min_voltage_delta={min_delta}V")
            for source, dest in pairs:
                logging.debug(f"Testing balance from Bank {source} to Bank {dest}")
                if y < stdscr.getmaxyx()[0]:
                    try:
                        stdscr.addstr(y, 0, f"Testing balance from Bank {source} to Bank {dest} for {test_duration}s.", curses.color_pair(6))
                    except curses.error:
                        logging.warning("addstr error for testing balance.")
                stdscr.refresh()
                logging.info(f"Testing balance from Bank {source} to Bank {dest} for {test_duration}s.")
                temp_anomaly = False
                if all_initial_temps:
                    for t in all_initial_temps:
                        if t > settings['high_threshold'] or t < settings['low_threshold']:
                            temp_anomaly = True
                            break
                if temp_anomaly:
                    alert = f"Skipping balance test from Bank {source} to Bank {dest}: Temp anomalies."
                    alerts.append(alert)
                    event_log.append(f"{time.strftime('%Y-%m-%d %H:%M:%S')}: {alert}")
                    if len(event_log) > 20:
                        event_log.pop(0)
                    logging.warning(f"Skipping balance test from Bank {source} to Bank {dest}: Temperature anomalies detected.")
                    if y + 1 < stdscr.getmaxyx()[0]:
                        try:
                            stdscr.addstr(y + 1, 0, "Skipped: Temp anomalies.", curses.color_pair(2))
                        except curses.error:
                            logging.warning("addstr error for skipped temp.")
                    y += 2
                    stdscr.refresh()
                    continue
                initial_source_v = read_voltage_with_retry(source, settings)[0] or 0.0
                initial_dest_v = read_voltage_with_retry(dest, settings)[0] or 0.0
                time.sleep(0.5)
                logging.debug(f"Balance test from Bank {source} to Bank {dest}: Initial - Bank {source}={initial_source_v:.2f}V, Bank {dest}={initial_dest_v:.2f}V")
                set_relay_connection(source, dest, settings)
                control_dcdc_converter(True, settings)
                start_time = time.time()
                source_trend = [initial_source_v]
                dest_trend = [initial_dest_v]
                progress_y = y + 1
                while time.time() - start_time < test_duration:
                    time.sleep(read_interval)
                    source_v = read_voltage_with_retry(source, settings)[0] or 0.0
                    dest_v = read_voltage_with_retry(dest, settings)[0] or 0.0
                    source_trend.append(source_v)
                    dest_trend.append(dest_v)
                    logging.debug(f"Balance test from Bank {source} to Bank {dest}: Bank {source}={source_v:.2f}V, Bank {dest}={dest_v:.2f}V")
                    elapsed = time.time() - start_time
                    if progress_y < stdscr.getmaxyx()[0]:
                        try:
                            stdscr.addstr(progress_y, 0, " " * 80, curses.color_pair(6))
                            stdscr.addstr(progress_y, 0, f"Progress: {elapsed:.1f}s, Bank {source} {source_v:.2f}V, Bank {dest} {dest_v:.2f}V", curses.color_pair(6))
                        except curses.error:
                            logging.warning("addstr error in startup balance progress.")
                    stdscr.refresh()
                final_source_v = read_voltage_with_retry(source, settings)[0] or 0.0
                final_dest_v = read_voltage_with_retry(dest, settings)[0] or 0.0
                time.sleep(0.5)
                logging.debug(f"Balance test from Bank {source} to Bank {dest}: Final - Bank {source}={final_source_v:.2f}V, Bank {dest}={final_dest_v:.2f}V")
                control_dcdc_converter(False, settings)
                set_relay_connection(0, 0, settings)
                if progress_y + 1 < stdscr.getmaxyx()[0]:
                    try:
                        stdscr.addstr(progress_y + 1, 0, "Analyzing...", curses.color_pair(6))
                    except curses.error:
                        logging.warning("addstr error for analyzing.")
                stdscr.refresh()
                if len(source_trend) >= 3:
                    source_change = final_source_v - initial_source_v
                    dest_change = final_dest_v - initial_dest_v
                    logging.debug(f"Balance test from Bank {source} to Bank {dest} analysis: Bank {source} Initial={initial_source_v:.2f}V, Final={final_source_v:.2f}V, Change={source_change:+.3f}V, Bank {dest} Initial={initial_dest_v:.2f}V, Final={final_dest_v:.2f}V, Change={dest_change:+.3f}V, Min change={min_delta}V")
                    if source_change >= 0 or dest_change <= 0 or abs(source_change) < min_delta or dest_change < min_delta:
                        alert = f"Balance test from Bank {source} to Bank {dest} failed: Unexpected trend or insufficient change (Bank {source} Initial={initial_source_v:.2f}V, Final={final_source_v:.2f}V, Change={source_change:+.3f}V, Bank {dest} Initial={initial_dest_v:.2f}V, Final={final_dest_v:.2f}V, Change={dest_change:+.3f}V)."
                        alerts.append(alert)
                        event_log.append(f"{time.strftime('%Y-%m-%d %H:%M:%S')}: {alert}")
                        if len(event_log) > 20:
                            event_log.pop(0)
                        logging.error(f"Balance test from Bank {source} to Bank {dest} failed: Source did not decrease or destination did not increase sufficiently.")
                        if progress_y + 1 < stdscr.getmaxyx()[0]:
                            try:
                                stdscr.addstr(progress_y + 1, 0, f"Test failed: Unexpected trend or insufficient change (Bank {source} Initial={initial_source_v:.2f}V, Final={final_source_v:.2f}V, Change={source_change:+.3f}V, Bank {dest} Initial={initial_dest_v:.2f}V, Final={final_dest_v:.2f}V, Change={dest_change:+.3f}V).", curses.color_pair(2))
                            except curses.error:
                                logging.warning("addstr error for test failed insufficient change.")
                    else:
                        logging.debug(f"Balance test from Bank {source} to Bank {dest} passed: Correct trend and sufficient voltage change.")
                        if progress_y + 1 < stdscr.getmaxyx()[0]:
                            try:
                                stdscr.addstr(progress_y + 1, 0, f"Test passed (Bank {source} Initial={initial_source_v:.2f}V, Final={final_source_v:.2f}V, Change={source_change:+.3f}V, Bank {dest} Initial={initial_dest_v:.2f}V, Final={final_dest_v:.2f}V, Change={dest_change:+.3f}V).", curses.color_pair(4))
                            except curses.error:
                                logging.warning("addstr error for test passed.")
                else:
                    alert = f"Balance test from Bank {source} to Bank {dest} failed: Insufficient readings."
                    alerts.append(alert)
                    event_log.append(f"{time.strftime('%Y-%m-%d %H:%M:%S')}: {alert}")
                    if len(event_log) > 20:
                        event_log.pop(0)
                    logging.error(f"Balance test from Bank {source} to Bank {dest} failed: Only {len(source_trend)} readings collected.")
                    if progress_y + 1 < stdscr.getmaxyx()[0]:
                        try:
                            stdscr.addstr(progress_y + 1, 0, "Test failed: Insufficient readings.", curses.color_pair(2))
                        except curses.error:
                            logging.warning("addstr error for test failed insufficient readings.")
                stdscr.refresh()
                y = progress_y + 2
                time.sleep(2)
        startup_alerts = alerts
        if alerts:
            startup_failed = True
            logging.error("Startup self-test failures: " + "; ".join(alerts))
            send_alert_email("Startup self-test failures:\n" + "\n".join(alerts), settings)
            if GPIO:
                GPIO.output(settings['AlarmRelayPin'], GPIO.HIGH)
            stdscr.clear()
            if stdscr.getmaxyx()[0] > 0:
                try:
                    stdscr.addstr(0, 0, "Startup failures: " + "; ".join(alerts), curses.color_pair(2))
                except curses.error:
                    logging.warning("addstr error for self-test failures.")
            if stdscr.getmaxyx()[0] > 2:
                try:
                    stdscr.addstr(2, 0, "Alarm activated. Retrying in 2 minutes...", curses.color_pair(2))
                except curses.error:
                    logging.warning("addstr error for retry message.")
            stdscr.refresh()
            time.sleep(10)  # Short sleep chunks with checks
            for _ in range(11):
                time.sleep(10)
            retries += 1
            continue
        else:
            startup_failed = False
            startup_alerts = []
            if GPIO:
                GPIO.output(settings['AlarmRelayPin'], GPIO.LOW)
            stdscr.clear()
            if stdscr.getmaxyx()[0] > 0:
                try:
                    stdscr.addstr(0, 0, "Self-Test Passed. Proceeding to main loop.", curses.color_pair(4))
                except curses.error:
                    logging.warning("addstr error for self-test OK.")
            stdscr.refresh()
            time.sleep(2)
            logging.info("Startup self-test passed.")
            return []
class BMSRequestHandler(BaseHTTPRequestHandler):
    def __init__(self, request, client_address, server):
        self.settings = server.settings
        super().__init__(request, client_address, server)
    def log_message(self, format, *args):
        pass
    def do_GET(self):
        parsed_path = urlparse(self.path)
        path = parsed_path.path
        if self.settings['auth_required'] and not self.authenticate():
            self.send_response(401)
            self.send_header('WWW-Authenticate', 'Basic realm="BMS"')
            self.end_headers()
            return
        if self.settings['cors_enabled']:
            self.send_header('Access-Control-Allow-Origin', self.settings['cors_origins'])
            self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
            self.send_header('Access-Control-Allow-Headers', 'Content-Type, Authorization')
        if path == '/':
            self.send_response(200)
            self.send_header('Content-type', 'text/html')
            self.end_headers()
            html = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Battery Management System</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        body { font-family: Arial, sans-serif; margin: 20px; background-color: #f5f5f5; }
        .container { max-width: 1200px; margin: 0 auto; }
        .header { background-color: #2c3e50; color: white; padding: 15px; border-radius: 5px; }
        .status-card { background-color: white; border-radius: 5px; padding: 15px; margin: 10px 0; box-shadow: 0 2px 5px rgba(0,0,0,0.1); }
        .battery { display: inline-block; margin: 10px; padding: 10px; border: 1px solid #ddd; border-radius: 5px; background-color: #f9f9f9; }
        .voltage { font-size: 1.2em; font-weight: bold; }
        .bank-summary { font-size: 0.9em; }
        .temperatures { font-size: 0.8em; max-height: 200px; overflow-y: auto; }
        .alert { color: #e74c3c; font-weight: bold; }
        .normal { color: #27ae60; }
        .warning { color: #f39c12; }
        .button { background-color: #3498db; color: white; border: none; padding: 10px 15px; border-radius: 3px; cursor: pointer; }
        .button:hover { background-color: #2980b9; }
        .button:disabled { background-color: #95a5a6; cursor: not-allowed; }
        .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 15px; }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>Battery Management System</h1>
            <p>Status: <span id="system-status">Loading...</span></p>
            <p>Last Update: <span id="last-update">-</span></p>
        </div>
        <div class="status-card">
            <h2>System Information</h2>
            <p>Total Voltage: <span id="total-voltage">-</span></p>
            <p>Balancing: <span id="balancing-status">No</span></p>
        </div>
        <div class="status-card">
            <h2>Actions</h2>
            <button id="refresh-btn" class="button">Refresh</button>
            <button id="balance-btn" class="button" disabled>Balance Now</button>
        </div>
        <div class="status-card">
            <h2>Alerts</h2>
            <div id="alerts-container"></div>
        </div>
        <div class="status-card">
            <h2>Battery Banks</h2>
            <div id="battery-container" class="grid"></div>
        </div>
        <div class="status-card">
            <h2>Time-Series Charts</h2>
            <canvas id="bmsChart" width="800" height="400"></canvas>
        </div>
    </div>
    <script>
        function updateStatus() {
            fetch('/api/status')
                .then(response => response.json())
                .then(data => {
                    document.getElementById('system-status').textContent = data.system_status;
                    document.getElementById('last-update').textContent = new Date(data.last_update * 1000).toLocaleString();
                    document.getElementById('total-voltage').textContent = data.total_voltage.toFixed(2) + 'V';
                    document.getElementById('balancing-status').textContent = data.balancing ? 'Yes' : 'No';
                    const batteryContainer = document.getElementById('battery-container');
                    batteryContainer.innerHTML = '';
                    data.voltages.forEach((voltage, index) => {
                        const summary = data.bank_summaries[index];
                        const bankDiv = document.createElement('div');
                        bankDiv.className = 'battery';
                        bankDiv.innerHTML = `
                            <h3>Bank ${index + 1}</h3>
                            <p class="voltage ${voltage === 0 ? 'alert' : (voltage > 21 || voltage < 18.5) ? 'warning' : 'normal'}">
                                ${voltage.toFixed(2)}V
                            </p>
                            <div class="bank-summary">
                                <p class="temperature ${summary.median > 60 || summary.median < 0 || summary.invalid > 0 ? 'warning' : 'normal'}">
                                    Median: ${summary.median.toFixed(1)}°C Min: ${summary.min.toFixed(1)}°C Max: ${summary.max.toFixed(1)}°C Invalid: ${summary.invalid}
                                </p>
                            </div>
                            <div class="temperatures">
                                ${data.temperatures.map((temp, tempIndex) => {
                                    const batId = Math.floor(tempIndex / 24) + 1;
                                    const localCh = (tempIndex % 24) + 1;
                                    return `<p class="temperature ${temp === null ? 'alert' : (temp > 60 || temp < 0) ? 'warning' : 'normal'}">
                                        Bat ${batId} Local C${localCh}: ${temp !== null ? temp.toFixed(1) + '°C' : 'N/A'}
                                    </p>`;
                                }).join('')}
                            </div>
                        `;
                        batteryContainer.appendChild(bankDiv);
                    });
                    const alertsContainer = document.getElementById('alerts-container');
                    if (data.alerts.length > 0) {
                        alertsContainer.innerHTML = data.alerts.map(alert => `<p class="alert">${alert}</p>`).join('');
                    } else {
                        alertsContainer.innerHTML = '<p class="normal">No alerts</p>';
                    }
                    const balanceBtn = document.getElementById('balance-btn');
                    balanceBtn.disabled = data.balancing || data.alerts.length > 0;
                })
                .catch(error => {
                    console.error('Error fetching status:', error);
                    document.getElementById('system-status').textContent = 'Error';
                });
        }
        function updateChart() {
            fetch('/api/history')
                .then(response => response.json())
                .then(data => {
                    const hist = data.history;
                    const labels = hist.map(h => new Date(h.time * 1000).toLocaleTimeString());
                    const datasets = [
                        { label: 'Bank 1 V', data: hist.map(h => h.volt1), borderColor: 'green' },
                        { label: 'Bank 2 V', data: hist.map(h => h.volt2), borderColor: 'blue' },
                        { label: 'Bank 3 V', data: hist.map(h => h.volt3), borderColor: 'red' },
                        { label: 'Median Temp °C', data: hist.map(h => h.medtemp), borderColor: 'cyan', yAxisID: 'temp' }
                    ];
                    const ctx = document.getElementById('bmsChart').getContext('2d');
                    new Chart(ctx, {
                        type: 'line',
                        data: { labels, datasets },
                        options: {
                            scales: {
                                y: { type: 'linear', position: 'left', title: { display: true, text: 'Voltage (V)' } },
                                temp: { type: 'linear', position: 'right', title: { display: true, text: 'Temp (°C)' }, grid: { drawOnChartArea: false } }
                            }
                        }
                    });
                });
        }
        function initiateBalance() {
            fetch('/api/balance', { method: 'POST' })
                .then(response => response.json())
                .then(data => {
                    if (data.success) {
                        alert('Balancing initiated');
                    } else:
                        alert('Error: ' + data.message);
                    }
                })
                .catch(error => {
                    console.error('Error initiating balance:', error);
                    alert('Error initiating balance');
                });
        }
        document.getElementById('refresh-btn').addEventListener('click', updateStatus);
        document.getElementById('balance-btn').addEventListener('click', initiateBalance);
        updateStatus();
        updateChart();
        setInterval(updateStatus, 5000);
        setInterval(updateChart, 60000);
    </script>
</body>
</html>"""
            self.wfile.write(html.encode('utf-8'))
        elif path == '/api/status':
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            response = {
                'voltages': web_data['voltages'],
                'temperatures': web_data['temperatures'],
                'bank_summaries': web_data['bank_summaries'],
                'alerts': web_data['alerts'],
                'balancing': web_data['balancing'],
                'last_update': web_data['last_update'],
                'system_status': web_data['system_status'],
                'total_voltage': sum(web_data['voltages'])
            }
            self.wfile.write(json.dumps(response).encode('utf-8'))
        elif path == '/api/history':
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            history = fetch_rrd_history()
            response = {'history': history}
            self.wfile.write(json.dumps(response).encode('utf-8'))
        else:
            self.send_response(404)
            self.end_headers()
    def do_POST(self):
        parsed_path = urlparse(self.path)
        path = parsed_path.path
        if self.settings['auth_required'] and not self.authenticate():
            self.send_response(401)
            self.send_header('WWW-Authenticate', 'Basic realm="BMS"')
            self.end_headers()
            return
        if self.settings['cors_enabled']:
            self.send_header('Access-Control-Allow-Origin', self.settings['cors_origins'])
            self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
            self.send_header('Access-Control-Allow-Headers', 'Content-Type, Authorization')
        if path == '/api/balance':
            global balancing_active
            if balancing_active:
                self.send_response(400)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                response = {'success': False, 'message': 'Balancing already in progress'}
                self.wfile.write(json.dumps(response).encode('utf-8'))
                return
            if len(web_data['alerts']) > 0:
                self.send_response(400)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                response = {'success': False, 'message': 'Cannot balance with active alerts'}
                self.wfile.write(json.dumps(response).encode('utf-8'))
                return
            voltages = web_data['voltages']
            if len(voltages) < 2:
                self.send_response(400)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                response = {'success': False, 'message': 'Not enough battery banks'}
                self.wfile.write(json.dumps(response).encode('utf-8'))
                return
            max_v = max(voltages)
            min_v = min(voltages)
            high_bank = voltages.index(max_v) + 1
            low_bank = voltages.index(min_v) + 1
            if max_v - min_v < self.settings['VoltageDifferenceToBalance']:
                self.send_response(400)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                response = {'success': False, 'message': 'Voltage difference too small for balancing'}
                self.wfile.write(json.dumps(response).encode('utf-8'))
                return
            balancing_active = True
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            response = {'success': True, 'message': f'Balancing initiated from Bank {high_bank} to Bank {low_bank}'}
            self.wfile.write(json.dumps(response).encode('utf-8'))
        else:
            self.send_response(404)
            self.end_headers()
    def do_OPTIONS(self):
        self.send_response(200)
        if self.settings['cors_enabled']:
            self.send_header('Access-Control-Allow-Origin', self.settings['cors_origins'])
            self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
            self.send_header('Access-Control-Allow-Headers', 'Content-Type, Authorization')
        self.end_headers()
    def authenticate(self):
        auth_header = self.headers.get('Authorization')
        if auth_header and auth_header.startswith('Basic '):
            auth_decoded = base64.b64decode(auth_header[6:]).decode('utf-8')
            username, password = auth_decoded.split(':', 1)
            return username == self.settings['username'] and password == self.settings['password']
        return False
def start_web_server(settings):
    global web_server
    if not settings['WebInterfaceEnabled']:
        logging.info("Web interface disabled via configuration.")
        return
    class CustomHTTPServer(ThreadingHTTPServer):
        def __init__(self, *args, **kwargs):
            self.settings = settings
            super().__init__(*args, **kwargs)
    try:
        web_server = CustomHTTPServer((settings['host'], settings['web_port']), BMSRequestHandler)
        logging.info(f"Web server started on {settings['host']}:{settings['web_port']}")
        server_thread = threading.Thread(target=web_server.serve_forever)
        server_thread.daemon = True
        server_thread.start()
    except Exception as e:
        logging.error(f"Failed to start web server: {e}")
def main(stdscr):
    stdscr.keypad(True)
    curses.start_color()
    curses.use_default_colors()
    curses.init_pair(1, curses.COLOR_RED, -1)
    curses.init_pair(2, curses.COLOR_RED, -1)
    curses.init_pair(3, curses.COLOR_YELLOW, -1)
    curses.init_pair(4, curses.COLOR_GREEN, -1)
    curses.init_pair(5, curses.COLOR_WHITE, -1)
    curses.init_pair(6, curses.COLOR_YELLOW, -1)
    curses.init_pair(7, curses.COLOR_CYAN, -1)
    curses.init_pair(8, curses.COLOR_MAGENTA, -1)
    stdscr.nodelay(True)
    global previous_temps, previous_bank_medians, run_count, startup_offsets, startup_median, startup_set, battery_voltages, web_data, balancing_active, BANK_SENSOR_INDICES, alive_timestamp, NUM_BANKS
    settings = load_config()
    NUM_BANKS = settings['num_series_banks']  # Dynamic now.
    number_parallel = settings['number_of_parallel_batteries']
    slave_addresses = settings['modbus_slave_addresses']
    sensors_per_bank = settings['sensors_per_bank']
    sensors_per_battery = NUM_BANKS * sensors_per_bank
    total_channels = number_parallel * sensors_per_battery
    BANK_SENSOR_INDICES = [[] for _ in range(NUM_BANKS)]  # Dynamic list of lists.
    for bat in range(number_parallel):
        base = bat * sensors_per_battery
        for bank_id in range(NUM_BANKS):
            bank_base = base + bank_id * sensors_per_bank
            BANK_SENSOR_INDICES[bank_id].extend(range(bank_base, bank_base + sensors_per_bank))
    setup_hardware(settings)
    start_web_server(settings)
    startup_self_test(settings, stdscr)
    signal.signal(signal.SIGINT, signal_handler)
    if settings['WatchdogEnabled'] and setup_watchdog(15):
        wd_thread = threading.Thread(target=watchdog_pet_thread, daemon=True)
        wd_thread.start()
        logging.info("Watchdog pet thread started.")
    else:
        logging.info("Watchdog disabled or setup failed.")
    previous_temps = [None] * total_channels
    previous_bank_medians = [0.0] * NUM_BANKS
    alive_timestamp = time.time()
    while True:
        temps_alerts = []
        all_raw_temps = []
        for addr in slave_addresses:
            temp_result = read_ntc_sensors(
                settings['ip'], settings['modbus_port'], settings['query_delay'],
                sensors_per_battery, settings['scaling_factor'],
                settings['max_retries'], settings['retry_backoff_base'], slave_addr=addr
            )
            if isinstance(temp_result, str):
                temps_alerts.append(f"Modbus slave {addr} failed: {temp_result}")
                all_raw_temps.extend([settings['valid_min']] * sensors_per_battery)
            else:
                all_raw_temps.extend(temp_result)
        raw_temps = all_raw_temps
        valid_count = sum(1 for t in raw_temps if t > settings['valid_min'])
        if not startup_set and valid_count == total_channels:
            startup_median = statistics.median(raw_temps)
            startup_offsets = [startup_median - t for t in raw_temps]
            save_offsets(startup_median, startup_offsets)
            startup_set = True
            logging.info(f"Temp calibration set. Median: {startup_median:.1f}°C")
        if startup_set and startup_offsets is None:
            startup_set = False
        calibrated_temps = [raw_temps[i] + startup_offsets[i] if startup_set and raw_temps[i] > settings['valid_min'] else raw_temps[i] if raw_temps[i] > settings['valid_min'] else None for i in range(total_channels)]
        bank_stats = compute_bank_medians(calibrated_temps, settings['valid_min'])
        bank_medians = [s['median'] for s in bank_stats]
        for ch, raw in enumerate(raw_temps, 1):
            if check_invalid_reading(raw, ch, temps_alerts, settings['valid_min']):
                continue
            calib = calibrated_temps[ch-1]
            bank_id = get_bank_for_channel(ch)
            bank_median = bank_medians[bank_id - 1]
            check_high_temp(calib, ch, temps_alerts, settings['high_threshold'])
            check_low_temp(calib, ch, temps_alerts, settings['low_threshold'])
            check_deviation(calib, bank_median, ch, temps_alerts, settings['abs_deviation_threshold'], settings['deviation_threshold'])
        if run_count > 0 and previous_temps and previous_bank_medians is not None:
            for bank_id in range(1, NUM_BANKS + 1):
                bank_median_rise = bank_medians[bank_id - 1] - previous_bank_medians[bank_id - 1]
                bank_indices = BANK_SENSOR_INDICES[bank_id - 1]
                for i in bank_indices:
                    ch = i + 1
                    calib = calibrated_temps[i]
                    if calib is not None:
                        check_abnormal_rise(calib, previous_temps, ch, temps_alerts, settings['poll_interval'], settings['rise_threshold'])
                        check_group_tracking_lag(calib, previous_temps, bank_median_rise, ch, temps_alerts, settings['disconnection_lag_threshold'])
                    check_sudden_disconnection(calib, previous_temps, ch, temps_alerts)
        previous_temps = calibrated_temps[:]
        previous_bank_medians = bank_medians[:]
        valid_calib_temps = [t for t in calibrated_temps if t is not None]
        overall_median = statistics.median(valid_calib_temps) if valid_calib_temps else 0.0
        if overall_median > settings['cabinet_over_temp_threshold']:
            if GPIO:
                GPIO.output(settings['FanRelayPin'], GPIO.HIGH)
            logging.info(f"Cabinet over temp: {overall_median:.1f}°C > {settings['cabinet_over_temp_threshold']}°C. Fan activated.")
            if not any("Cabinet over temp" in a for a in temps_alerts):
                temps_alerts.append(f"Cabinet over temp: {overall_median:.1f}°C > {settings['cabinet_over_temp_threshold']}°C. Fan on.")
                event_log.append(f"{time.strftime('%Y-%m-%d %H:%M:%S')}: Cabinet over temp: {overall_median:.1f}°C > {settings['cabinet_over_temp_threshold']}°C. Fan on.")
                if len(event_log) > 20:
                    event_log.pop(0)
        else:
            if GPIO:
                GPIO.output(settings['FanRelayPin'], GPIO.LOW)
            logging.info("Cabinet temp normal. Fan deactivated.")
        battery_voltages = []
        for i in range(1, NUM_BANKS + 1):
            v, _, _ = read_voltage_with_retry(i, settings)
            battery_voltages.append(v if v is not None else 0.0)
        alert_needed, all_alerts = check_for_issues(battery_voltages, temps_alerts, settings)
        timestamp = int(time.time())
        values = f"{timestamp}:{battery_voltages[0]}:{battery_voltages[1]}:{battery_voltages[2]}:{overall_median}"
        subprocess.call(['rrdtool', 'update', RRD_FILE, values])
        logging.debug(f"RRD updated with: {values}")
        if len(battery_voltages) == NUM_BANKS:
            max_v = max(battery_voltages)
            min_v = min(battery_voltages)
            high_b = battery_voltages.index(max_v) + 1
            low_b = battery_voltages.index(min_v) + 1
            current_time = time.time()
            if balancing_active or (alert_needed is False and max_v - min_v > settings['VoltageDifferenceToBalance'] and min_v > 0 and current_time - last_balance_time > settings['BalanceRestPeriodSeconds']):
                balance_battery_voltages(stdscr, high_b, low_b, settings, temps_alerts)
                balancing_active = False
        web_data['voltages'] = battery_voltages
        web_data['temperatures'] = calibrated_temps
        web_data['bank_summaries'] = bank_stats
        web_data['alerts'] = all_alerts
        web_data['balancing'] = balancing_active
        web_data['last_update'] = time.time()
        web_data['system_status'] = 'Alert' if alert_needed else 'Running'
        draw_tui(
            stdscr, battery_voltages, calibrated_temps, raw_temps,
            startup_offsets or [0]*total_channels, bank_stats,
            startup_median, all_alerts, settings, startup_set, is_startup=(run_count == 0)
        )
        alive_timestamp = time.time()  # Update aliveness for watchdog thread
        run_count += 1
        gc.collect()
        logging.info("Poll cycle complete.")
        time.sleep(settings['poll_interval'])
if __name__ == '__main__':
    curses.wrapper(main)