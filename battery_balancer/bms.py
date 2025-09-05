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
# - Invalid/Disconnected: Reading <= valid_min (e.g., 0.0°C).
# - High: > high_threshold (e.g., 42.0°C).
# - Low: < low_threshold (e.g., 0.0°C).
# - Deviation: Absolute > abs_deviation_threshold (e.g., 2.0°C) or relative > deviation_threshold (e.g., 10%) from bank median.
# - Abnormal Rise: Increase > rise_threshold (e.g., 2.0°C) since last poll.
# - Group Lag: Change differs from bank median change by > disconnection_lag_threshold (e.g., 0.5°C).
# - Sudden Disconnection: Was valid, now invalid.
# - **Voltage Monitoring & Balancing:** Uses ADS1115 ADC over I2C to measure voltages of num_series_banks banks. Balances if difference > VoltageDifferenceToBalance (e.g., 0.1V) by connecting high to low bank via relays and DC-DC converter (relay logic configurable via INI).
# - Heating Mode: If any temperature < 10°C, balances regardless of voltage difference to generate heat.
# - Safety: Skips balancing if alerts active (e.g., anomalies). Rests for BalanceRestPeriodSeconds (e.g., 60s) after balancing.
# - Voltage Checks: Alerts if < LowVoltageThresholdPerBattery (e.g., 18.5V), > HighVoltageThresholdPerBattery (e.g., 21.0V), or zero.
# - **Alerts & Notifications:** Logs to 'battery_monitor.log'. Activates alarm relay on issues. Sends throttled emails (e.g., every 3600s) via SMTP.
# - **Watchdog:** If enabled, pets hardware watchdog via dedicated thread (every 5s with aliveness check via timestamp) to prevent resets on hangs. Uses /dev/watchdog with 15s timeout (Pi max).
# - **User Interfaces:**
# - **TUI (Terminal UI):** Uses curses for real-time display: ASCII art batteries (dynamic for num_series_banks) with voltages/temps, alerts, balancing progress bar/animation, last 20 events. Now includes ASCII line charts for voltage history per bank and median temperature, placed in the top-right section for visualization of trends over time.
# - **Web Dashboard:** HTTP server on port 8080 (configurable). Shows voltages, temps, alerts, balancing status. Supports API for status/balance/history. Optional auth/CORS. Now includes interactive time-series charts using Chart.js for voltages per bank and median temperature, placed at the top of the page after the header for easy viewing.
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
# - Voltages: Measures "energy level" in each bank. If one has more energy than another, it transfers some to balance them, like pouring water between buckets.
# - Heating: In cold weather (<10°C), it deliberately transfers energy to create warmth inside the battery cabinet.
# - Alerts: If something's wrong, it logs it, turns on a buzzer/light (alarm relay), and emails you (but not too often to avoid spam).
# - Interfaces: Terminal shows a fancy text-based dashboard with ASCII charts for trends and lists all temps; web page lets you view from browser with interactive charts and full temp lists.
# - Startup Check: Like a self-diagnostic when your car starts – ensures everything's connected and working before running.
# - Time-Series: Tracks history of voltages and temps, shows trends in charts to spot patterns over time.
# **How It Works (Step-by-Step for Non-Programmers):**
# 1. **Start:** Loads settings from INI file (like a recipe book).
# 2. **Setup:** Connects to hardware (sensors, relays) – if missing, runs in "pretend" mode. Creates/loads RRD database for history.
# 3. **Self-Test:** Checks if config makes sense, hardware responds (per Modbus slave), sensors give good readings, aggregated. Balancing actually changes voltages. If fail, alerts and retries.
# 4. **Main Loop (Repeats Forever):**
# - Read temperatures from all slaves, aggregate.
# - Calibrate them (adjust based on startup values for accuracy).
# - Check for temperature problems (too hot, too cold, etc.).
# - Read voltages from configured banks.
# - Check for voltage problems (too high, too low, zero).
# - Update RRD database with voltages and median temp.
# - If cold (<10°C anywhere), balance to heat up.
# - Else, if voltages differ too much, balance to equalize.
# - Fetch history from RRD for charts.
# - Update terminal (with ASCII charts and full temp lists)/web displays (with Chart.js and full lists).
# - Log events, send emails if issues.
# - Update alive timestamp for watchdog.
# - Wait a bit (e.g., 10s), repeat.
# 5. **Balancing Process:** Connects high to low bank with relays, turns on converter to transfer charge, shows progress, turns off after time.
# 6. **Shutdown:** If you press Ctrl+C, cleans up connections safely.
# **Updated Logic Flow Diagram (ASCII - More Detailed):**
#
"""
+--------------------------------------+
| Load Config from INI |
| (Read settings file, incl. parallel) |
+--------------------------------------+
|
v
+--------------------------------------+
| Setup Hardware |
| (I2C bus, GPIO pins, RRD DB) |
| Compute sensor groupings |
+--------------------------------------+
|
v
+--------------------------------------+
| Startup Self-Test |
| (Config valid? |
| Hardware connected? Per slave? |
| Initial reads OK? Aggregated? |
| Balancer works?) |
| If fail: Alert, Retry |
+--------------------------------------+
|
v
+--------------------------------------+
| Start Watchdog Thread |
| (Pet every 5s if main alive) |
+--------------------------------------+
|
v
+--------------------------------------+ <---------------------+
| Main Loop (Repeat) | |
+--------------------------------------+ |
| |
v |
+--------------------------------------+ |
| Read Temps (Per Slave, Aggregate) | |
| (Handle errors per slave) | |
+--------------------------------------+ |
| |
v |
+--------------------------------------+ |
| Calibrate Temps | |
| (Apply offsets if set) | |
+--------------------------------------+ |
| |
v |
+--------------------------------------+ |
| Check Temp Issues | |
| (High/Low/Deviation/ | |
| Rise/Lag/Disconnect, with bat info) | |
+--------------------------------------+ |
| |
v |
+--------------------------------------+ |
| Read Voltages (ADC) | |
| (3 banks via I2C) | |
+--------------------------------------+ |
| |
v |
+--------------------------------------+ |
| Check Voltage Issues | |
| (High/Low/Zero) | |
+--------------------------------------+ |
| |
v |
+--------------------------------------+ |
| Update RRD with Data | |
| (Voltages, Median Temp) | |
+--------------------------------------+ |
| |
v |
+--------------------------------------+ |
| If Any Temp < 10°C: | |
| Balance for Heating | |
| Else If Volt Diff > Th: | |
| Balance Normally | |
| (High to Low Bank) | |
| Skip if Alerts Active | |
+--------------------------------------+ |
| |
v |
+--------------------------------------+ |
| Fetch RRD History | |
| (For Charts) | |
+--------------------------------------+ |
| |
v |
+--------------------------------------+ |
| Update TUI (Terminal) | |
| & Web Dashboard | |
| (Show status, alerts, | |
| ASCII/Chart.js Charts, full temps) | |
+--------------------------------------+ |
| |
v |
+--------------------------------------+ |
| Log Events, Send Email | |
| if Issues & Throttled | |
+--------------------------------------+ |
| |
v |
+--------------------------------------+
| Update Alive Timestamp |
+--------------------------------------+
| |
v |
+--------------------------------------+
| Sleep (Poll Interval) |
+--------------------------------------+
| |
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
#    **Validate Config:** python bms.py --validate-config [--data-dir /path/to/config]
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
# Import statements: These bring in tools and libraries that the script needs to work.
# Think of them as gathering the ingredients and tools before cooking.
import socket # Network communication tool - like a phone to call the temperature sensor device over the internet.
import statistics # Math helper for calculating averages and middle values of temperature readings.
import time # Time management - handles delays, waits, and records when things happen (like a clock).
import configparser # Settings reader - loads configuration from the INI file, like reading a recipe book.
import logging # Event recorder - writes messages about what's happening to a log file for later review.
import signal # Shutdown handler - catches when user presses Ctrl+C to stop the program nicely.
import gc # Memory cleaner - removes unused data from memory to keep the program running smoothly.
import os # File system manager - handles reading/writing files, like saving calibration data.
import sys # System controller - manages program exit and command-line arguments.
import argparse # Command-line argument parser - handles options like --validate-config.
import threading # Multi-tasking tool - runs the web server separately from the main program.
import json # Data formatter - converts data to/from a format that web browsers understand.
from urllib.parse import urlparse, parse_qs # Web request parser - breaks down web addresses and data.
import base64 # Secret code decoder - handles user login credentials for the web interface.
import traceback # Error detail recorder - captures full error information for debugging.
import subprocess # External program runner - executes other tools like the database updater.
import xml.etree.ElementTree as ET # XML data reader - parses database output files.
try:
    from flask import Flask, jsonify, request, make_response # Web server framework for reliable API handling.
except ImportError:
    print("Flask not available - web interface disabled") # Warn user.
    Flask = None # Set to none if missing.
try:
    import smbus # Communicates with I2C devices like the ADC and relays - hardware talker.
    import RPi.GPIO as GPIO # Controls Raspberry Pi GPIO pins for relays - pin controller.
except ImportError:
    print("Hardware libraries not available - running in test mode") # Warn user.
    smbus = None # Set to none if missing.
    GPIO = None # Set to none if missing.
from email.mime.text import MIMEText # Builds email messages - email builder.
import smtplib # Sends email alerts - email sender.
import curses # Creates the terminal-based Text User Interface (TUI) - terminal drawer.
from art import text2art # Generates ASCII art for the TUI display - art maker.
try:
    import fcntl # For watchdog ioctl - low-level control.
except ImportError:
    fcntl = None
def check_dependencies():
    """
    Check for required and optional dependencies at startup.
    Alerts user if missing and exits for critical ones.
    """
    critical_deps = ['smbus', 'RPi.GPIO']
    optional_deps = ['rrdtool', 'art', 'flask']
    missing_critical = []
    missing_optional = []
    
    for dep in critical_deps:
        try:
            __import__(dep)
        except ImportError:
            missing_critical.append(dep)
    
    for dep in optional_deps:
        try:
            if dep == 'rrdtool':
                subprocess.check_call(['rrdtool', '--version'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                __import__(dep)
        except (ImportError, subprocess.CalledProcessError, FileNotFoundError):
            missing_optional.append(dep)
    
    if missing_critical:
        msg = f"Critical dependencies missing: {', '.join(missing_critical)}. Install with: sudo apt install python3-{' python3-'.join(missing_critical)}. Exiting."
        logging.error(msg)
        print(msg)
        sys.exit(1)
    
    if missing_optional:
        for dep in missing_optional:
            if dep == 'rrdtool':
                msg = "Optional dependency 'rrdtool' missing. Time-series logging disabled. Install with: sudo apt install rrdtool."
            elif dep == 'art':
                msg = "Optional dependency 'art' missing. ASCII art disabled. Install with: pip install art."
            logging.warning(msg)
            print(msg)
    
    logging.info("Dependency check passed.")
import struct # For watchdog struct - data packer.
config_parser = configparser.ConfigParser(comment_prefixes=(';', '#')) # Object to read INI file - config reader, handles ; and # comments.
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
event_log = [] # Stores the last N events (configurable) - event history.
web_data = {
    'voltages': [], # Will be filled dynamically based on num_series_banks
    'temperatures': [], # Will be filled dynamically based on total_channels
    'bank_summaries': [], # Will be filled dynamically based on num_series_banks
    'alerts': [], # Current active alerts - alert list.
    'balancing': False, # Balancing status - balance flag.
    'last_update': time.time(), # Last data update timestamp - update time.
    'system_status': 'Initializing' # System status (e.g., Running, Alert) - status string.
}
BANK_SENSOR_INDICES = [] # Will be filled dynamically based on num_series_banks
NUM_BANKS = 3 # Will be overridden by config in main()
WATCHDOG_DEV = '/dev/watchdog' # Device file for watchdog - hardware reset preventer.
watchdog_fd = None # File handle for watchdog - open connection.
alive_timestamp = 0.0 # Shared timestamp updated by main to indicate aliveness - for watchdog thread.
RRD_FILE = 'bms.rrd' # RRD database file for storing time-series data - persistent storage.
HISTORY_LIMIT = 1440 # Number of historical entries to retain (e.g., ~24 hours at 1min steps) - limit for memory/efficiency.
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
    Read temperature measurements from NTC thermistor sensors.

    This function acts like a messenger: it calls up the temperature sensor device over the network,
    asks for the latest readings from all the sensors, waits for the response, checks that the data
    is valid and not corrupted, then converts the raw numbers into actual temperature values in Celsius.

    Imagine you're calling a weather station to get temperature readings from multiple locations.
    This function dials the number (IP address), asks for the data, waits patiently, and then
    translates the technical numbers into something you can understand.

    Args:
        ip (str): The internet address of the sensor device (like a phone number for the device).
        modbus_port (int): The specific "door" or channel on the device to communicate through.
        query_delay (float): How long to wait after asking for data before expecting a reply.
        num_channels (int): How many temperature sensors to read from this device.
        scaling_factor (float): A math number to convert raw sensor data into Celsius degrees.
        max_retries (int): If the first attempt fails, how many more times to try again.
        retry_backoff_base (int): How long to wait between retry attempts (gets longer each time).
        slave_addr (int): The specific sensor unit's ID number on the network (default is 1).

    Returns:
        list or str: Either a list of temperature values in Celsius, or an error message if something went wrong.
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
def load_config(data_dir):
    logging.info("Loading configuration from 'battery_monitor.ini'.")
    global alert_states
    if not config_parser.sections():
        logging.error("Config file 'battery_monitor.ini' not found or empty.")
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
        'sensors_per_bank': config_parser.getint('Temp', 'sensors_per_bank', fallback=8), # New: sensors per bank per battery.
        'num_series_banks': config_parser.getint('General', 'num_series_banks', fallback=3) # New: number of series banks.
    }
    # Validate num_series_banks
    if temp_settings['num_series_banks'] < 1:
        logging.warning(f"num_series_banks={temp_settings['num_series_banks']} invalid. Setting to 1.")
        temp_settings['num_series_banks'] = 1
    elif temp_settings['num_series_banks'] > 20:
        logging.warning(f"num_series_banks={temp_settings['num_series_banks']} very high. Ensure hardware supports this.")
    temp_settings['sensors_per_battery'] = temp_settings['num_series_banks'] * temp_settings['sensors_per_bank'] # Calc per battery.
    temp_settings['total_channels'] = temp_settings['number_of_parallel_batteries'] * temp_settings['sensors_per_battery'] # Total sensors.
    startup_median, startup_offsets = load_offsets(temp_settings['total_channels'], data_dir)
    voltage_settings = {
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
        'WatchdogEnabled': config_parser.getboolean('General', 'WatchdogEnabled', fallback=True),
        'EventLogSize': config_parser.getint('General', 'EventLogSize', fallback=20)
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
    calibration_settings = {}
    for i in range(1, temp_settings['num_series_banks'] + 1):
        key = f'Sensor{i}_Calibration'
        calibration_settings[key] = config_parser.getfloat('Calibration', key, fallback=1.0)
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
    relay_mapping = {}
    if config_parser.has_section('RelayMapping'):
        for key in config_parser['RelayMapping']:
            try:
                relays = [int(x.strip()) for x in config_parser['RelayMapping'][key].split(',')]
                relay_mapping[key] = relays
            except ValueError:
                logging.warning(f"Invalid relay mapping for {key}: {config_parser['RelayMapping'][key]}")
    log_level = getattr(logging, voltage_settings['LoggingLevel'].upper(), logging.INFO)
    logging.getLogger().setLevel(log_level)
    alert_states = {ch: {'last_type': None, 'count': 0} for ch in range(1, temp_settings['total_channels'] + 1)}
    logging.info("Configuration loaded successfully.")
    return {**temp_settings, **voltage_settings, **general_flags, **i2c_settings,
            **gpio_settings, **email_settings, **adc_settings, **calibration_settings,
            **startup_settings, **web_settings, 'relay_mapping': relay_mapping}
def validate_config(settings):
    """
    Validate configuration settings for consistency and required values.
    Raises ValueError if invalid.
    """
    errors = []
    
    if settings['num_series_banks'] < 1:
        errors.append("num_series_banks must be at least 1.")
    if settings['num_series_banks'] > 20:
        errors.append("num_series_banks > 20 may cause issues.")
    
    if settings['sensors_per_bank'] < 1:
        errors.append("sensors_per_bank must be at least 1.")
    
    if settings['number_of_parallel_batteries'] < 1:
        errors.append("number_of_parallel_batteries must be at least 1.")
    
    if len(settings['modbus_slave_addresses']) != settings['number_of_parallel_batteries']:
        errors.append("modbus_slave_addresses count must match number_of_parallel_batteries.")
    
    if settings.get('relay_mapping'):
        expected_pairs = []
        for i in range(1, settings['num_series_banks'] + 1):
            for j in range(1, settings['num_series_banks'] + 1):
                if i != j:
                    expected_pairs.append(f"{i}-{j}")
        for pair in expected_pairs:
            if pair not in settings['relay_mapping']:
                errors.append(f"Relay mapping missing for {pair}.")
    
    if errors:
        msg = "Configuration errors: " + "; ".join(errors)
        logging.error(msg)
        raise ValueError(msg)
    
    logging.info("Configuration validation passed.")

def detect_hardware(settings):
    """
    Detect and log hardware connectivity at startup.
    """
    logging.info("Detecting hardware connectivity.")
    if bus:
        try:
            # Test multiplexer
            choose_channel(0, settings['MultiplexerAddress'])
            logging.info("I2C multiplexer detected.")
        except IOError as e:
            logging.warning(f"I2C multiplexer not accessible: {e}")
        
        try:
            # Test voltage meter
            bus.read_byte(settings['VoltageMeterAddress'])
            logging.info("I2C voltage meter detected.")
        except IOError as e:
            logging.warning(f"I2C voltage meter not accessible: {e}")
        
        try:
            # Test relay
            bus.read_byte(settings['RelayAddress'])
            logging.info("I2C relay detected.")
        except IOError as e:
            logging.warning(f"I2C relay not accessible: {e}")
    else:
        logging.warning("I2C bus not available - hardware detection skipped.")
    
    # Test Modbus slaves
    for addr in settings['modbus_slave_addresses']:
        try:
            test_result = read_ntc_sensors(settings['ip'], settings['modbus_port'], settings['query_delay'], 1, settings['scaling_factor'], 1, 1, slave_addr=addr)
            if isinstance(test_result, str):
                logging.warning(f"Modbus slave {addr} not accessible: {test_result}")
            else:
                logging.info(f"Modbus slave {addr} detected.")
        except Exception as e:
            logging.warning(f"Modbus slave {addr} detection failed: {e}")
    
    logging.info("Hardware detection complete.")
def setup_hardware(settings):
    """
    Prepare the hardware connections for monitoring and controlling the batteries.

    This function sets up the communication channels to the physical devices:
    - I2C bus for talking to voltage sensors and relays (like a data highway)
    - GPIO pins for controlling switches and alarms (like light switches)
    - Time-series database for storing historical data

    If hardware libraries aren't available, it switches to "test mode" where
    everything works but uses fake data instead of real sensors.
    """
    global bus
    logging.info("Setting up hardware connections.")

    # Set up I2C communication (for voltage sensors and relays)
    if smbus:
        bus = smbus.SMBus(settings['I2C_BusNumber'])  # Create connection to I2C bus
    else:
        logging.warning("I2C library not available - running in test mode with fake data")
        bus = None

    # Set up GPIO pins (for controlling relays and alarms)
    if GPIO:
        GPIO.setmode(GPIO.BCM)  # Use Broadcom pin numbering
        GPIO.setup(settings['DC_DC_RelayPin'], GPIO.OUT, initial=GPIO.LOW)  # DC-DC converter control
        GPIO.setup(settings['AlarmRelayPin'], GPIO.OUT, initial=GPIO.LOW)  # Alarm buzzer/light
        GPIO.setup(settings['FanRelayPin'], GPIO.OUT, initial=GPIO.LOW)    # Cooling fan control
    else:
        logging.warning("GPIO library not available - running in test mode")

    def create_rrd():
        ds_list = ['DS:medtemp:GAUGE:120:-20:100']
        for i in range(1, settings['num_series_banks'] + 1):
            ds_list.append(f'DS:volt{i}:GAUGE:120:0:25')
        subprocess.check_call(['rrdtool', 'create', RRD_FILE,
                               '--step', '60'] + ds_list +
                               ['RRA:LAST:0.0:1:1440',
                                'RRA:LAST:0.0:5:288'])
        logging.info("Created RRD database for time-series logging.")

    try:
        if not os.path.exists(RRD_FILE):
            create_rrd()
        else:
            try:
                output = subprocess.check_output(['rrdtool', 'info', RRD_FILE])
                ds_count = len([line for line in output.decode().split('\n') if line.startswith('ds[')])
                expected_ds = 1 + settings['num_series_banks']
                if ds_count != expected_ds:
                    logging.warning(f"RRD database schema mismatch: {ds_count} DS vs expected {expected_ds}. Recreating.")
                    os.remove(RRD_FILE)
                    create_rrd()
                else:
                    logging.info("Using existing RRD database with matching schema.")
            except subprocess.CalledProcessError as e:
                logging.error(f"RRD info failed: {e}. Recreating database.")
                os.remove(RRD_FILE)
                create_rrd()
    except subprocess.CalledProcessError as e:
        logging.error(f"RRD creation failed: {e}")
    except FileNotFoundError:
        logging.error("rrdtool not found. Please install rrdtool (sudo apt install rrdtool).")
    except OSError as e:
        logging.error(f"RRD file operation failed: {e}")
    logging.info("Hardware setup complete, including RRD initialization.")
    detect_hardware(settings)
def signal_handler(sig, frame):
    logging.info("Script stopped by user or signal.")
    global web_server
    if web_server:
        web_server.shutdown()
    if GPIO:
        GPIO.cleanup()
    close_watchdog()
    sys.exit(0)
def load_offsets(num_channels, data_dir):
    offsets_path = os.path.join(data_dir, 'offsets.txt')
    logging.info(f"Loading startup offsets from '{offsets_path}'.")
    if os.path.exists(offsets_path):
        try:
            with open(offsets_path, 'r') as f:
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
def save_offsets(startup_median, startup_offsets, data_dir):
    offsets_path = os.path.join(data_dir, 'offsets.txt')
    logging.info(f"Saving startup offsets to '{offsets_path}'.")
    try:
        with open(offsets_path, 'w') as f:
            f.write(f"{startup_median}\n")
            for offset in startup_offsets:
                f.write(f"{offset}\n")
        logging.debug("Offsets saved.")
    except IOError as e:
        logging.error(f"Failed to save offsets: {e}")
def check_invalid_reading(raw, ch, alerts, valid_min, settings):
    if raw <= valid_min:
        bank = get_bank_for_channel(ch)
        bat_id, local_ch = get_battery_and_local_ch(ch)
        alert = f"Battery {bat_id} Bank {bank} Local Ch {local_ch}: Invalid reading (≤ {valid_min})."
        alerts.append(alert)
        event_log.append(f"{time.strftime('%Y-%m-%d %H:%M:%S')}: {alert}")
        if len(event_log) > settings.get('EventLogSize', 20):
            event_log.pop(0)
        logging.warning(f"Invalid reading on Battery {bat_id} Bank {bank} Local Ch {local_ch}: {raw} ≤ {valid_min}.")
        return True
    return False
def check_high_temp(calibrated, ch, alerts, high_threshold, settings):
    if calibrated > high_threshold:
        bank = get_bank_for_channel(ch)
        bat_id, local_ch = get_battery_and_local_ch(ch)
        alert = f"Battery {bat_id} Bank {bank} Local Ch {local_ch}: High temp ({calibrated:.1f}°C > {high_threshold}°C)."
        alerts.append(alert)
        event_log.append(f"{time.strftime('%Y-%m-%d %H:%M:%S')}: {alert}")
        if len(event_log) > settings.get('EventLogSize', 20):
            event_log.pop(0)
        logging.warning(f"High temp alert on Battery {bat_id} Bank {bank} Local Ch {local_ch}: {calibrated:.1f} > {high_threshold}.")
def check_low_temp(calibrated, ch, alerts, low_threshold, settings):
    if calibrated < low_threshold:
        bank = get_bank_for_channel(ch)
        bat_id, local_ch = get_battery_and_local_ch(ch)
        alert = f"Battery {bat_id} Bank {bank} Local Ch {local_ch}: Low temp ({calibrated:.1f}°C < {low_threshold}°C)."
        alerts.append(alert)
        event_log.append(f"{time.strftime('%Y-%m-%d %H:%M:%S')}: {alert}")
        if len(event_log) > settings.get('EventLogSize', 20):
            event_log.pop(0)
        logging.warning(f"Low temp alert on Battery {bat_id} Bank {bank} Local Ch {local_ch}: {calibrated:.1f} < {low_threshold}.")
def check_deviation(calibrated, bank_median, ch, alerts, abs_deviation_threshold, deviation_threshold, settings):
    abs_dev = abs(calibrated - bank_median)
    rel_dev = abs_dev / abs(bank_median) if bank_median != 0 else 0
    if abs_dev > abs_deviation_threshold or rel_dev > deviation_threshold:
        bank = get_bank_for_channel(ch)
        bat_id, local_ch = get_battery_and_local_ch(ch)
        alert = f"Battery {bat_id} Bank {bank} Local Ch {local_ch}: Deviation from bank median (abs {abs_dev:.1f}°C or {rel_dev:.2%})."
        alerts.append(alert)
        event_log.append(f"{time.strftime('%Y-%m-%d %H:%M:%S')}: {alert}")
        if len(event_log) > settings.get('EventLogSize', 20):
            event_log.pop(0)
        logging.warning(f"Deviation alert on Battery {bat_id} Bank {bank} Local Ch {local_ch}: abs {abs_dev:.1f}, rel {rel_dev:.2%}.")
def check_abnormal_rise(current, previous_temps, ch, alerts, poll_interval, rise_threshold, settings):
    previous = previous_temps[ch-1]
    if previous is not None:
        if not isinstance(current, (int, float)) or not isinstance(previous, (int, float)):
            logging.warning(f"Type error in check_abnormal_rise for ch {ch}: current={type(current)} {current}, previous={type(previous)} {previous}")
            return
        rise = current - previous
        if rise > rise_threshold:
            bank = get_bank_for_channel(ch)
            bat_id, local_ch = get_battery_and_local_ch(ch)
            alert = f"Battery {bat_id} Bank {bank} Local Ch {local_ch}: Abnormal rise ({rise:.1f}°C in {poll_interval}s)."
            alerts.append(alert)
            event_log.append(f"{time.strftime('%Y-%m-%d %H:%M:%S')}: {alert}")
            if len(event_log) > settings.get('EventLogSize', 20):
                event_log.pop(0)
            logging.warning(f"Abnormal rise alert on Battery {bat_id} Bank {bank} Local Ch {local_ch}: {rise:.1f}°C.")
def check_group_tracking_lag(current, previous_temps, bank_median_rise, ch, alerts, disconnection_lag_threshold, settings):
    previous = previous_temps[ch-1]
    if previous is not None:
        if not isinstance(current, (int, float)) or not isinstance(previous, (int, float)):
            logging.warning(f"Type error in check_group_tracking_lag for ch {ch}: current={type(current)} {current}, previous={type(previous)} {previous}")
            return
        rise = current - previous
        if abs(rise - bank_median_rise) > disconnection_lag_threshold:
            bank = get_bank_for_channel(ch)
            bat_id, local_ch = get_battery_and_local_ch(ch)
            alert = f"Battery {bat_id} Bank {bank} Local Ch {local_ch}: Lag from bank group ({rise:.1f}°C vs {bank_median_rise:.1f}°C)."
            alerts.append(alert)
            event_log.append(f"{time.strftime('%Y-%m-%d %H:%M:%S')}: {alert}")
            if len(event_log) > settings.get('EventLogSize', 20):
                event_log.pop(0)
            logging.warning(f"Lag alert on Battery {bat_id} Bank {bank} Local Ch {local_ch}: rise {rise:.1f} vs median {bank_median_rise:.1f}.")
def check_sudden_disconnection(current, previous_temps, ch, alerts, settings):
    previous = previous_temps[ch-1]
    if not isinstance(previous, (int, float, type(None))) or not isinstance(current, (int, float, type(None))):
        logging.warning(f"Type error in check_sudden_disconnection for ch {ch}: current={type(current)} {current}, previous={type(previous)} {previous}")
        return
    if previous is not None and current is None:
        bank = get_bank_for_channel(ch)
        bat_id, local_ch = get_battery_and_local_ch(ch)
        alert = f"Battery {bat_id} Bank {bank} Local Ch {local_ch}: Sudden disconnection."
        alerts.append(alert)
        event_log.append(f"{time.strftime('%Y-%m-%d %H:%M:%S')}: {alert}")
        if len(event_log) > settings.get('EventLogSize', 20):
            event_log.pop(0)
        logging.warning(f"Sudden disconnection alert on Battery {bat_id} Bank {bank} Local Ch {local_ch}.")
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
    global alive_timestamp
    logging.info(f"Starting voltage read for Bank {bank_id}.")
    if bank_id > settings['num_series_banks']:
        logging.warning(f"Bank {bank_id} exceeds configured num_series_banks ({settings['num_series_banks']}). Cannot read voltage.")
        return None, [], []
    voltage_divider_ratio = settings['VoltageDividerRatio']
    sensor_id = bank_id
    calibration_factor = settings[f'Sensor{sensor_id}_Calibration']
    for attempt in range(2):
        alive_timestamp = time.time()
        logging.debug(f"Voltage read attempt {attempt+1} for Bank {bank_id}.")
        readings = []
        raw_values = []
        for _ in range(2):
            alive_timestamp = time.time()
            meter_channel = bank_id - 1  # Direct mapping: Bank 1 = Channel 0, Bank 2 = Channel 1, etc.
            choose_channel(meter_channel, settings['MultiplexerAddress'])
            setup_voltage_meter(settings)
            if bus:
                try:
                    bus.write_byte(settings['VoltageMeterAddress'], 0x01)
                    time.sleep(0.05)
                    alive_timestamp = time.time()
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
        if high > settings['num_series_banks'] or low > settings['num_series_banks']:
            logging.warning(f"Bank {high} or {low} exceeds configured num_series_banks ({settings['num_series_banks']}). Cannot balance.")
            return
        logging.info(f"Attempting to set relay for connection from Bank {high} to {low}")
        logging.debug("Switching to relay control channel.")
        choose_channel(3, settings['MultiplexerAddress'])
        relay_state = 0
        pair_key = f"{high}-{low}"
        if pair_key in settings.get('relay_mapping', {}):
            relays = settings['relay_mapping'][pair_key]
            for relay in relays:
                relay_state |= (1 << relay)
            logging.debug(f"Relays {relays} activated for {pair_key}.")
        else:
            logging.warning(f"No relay mapping found for {pair_key}. Cannot balance.")
            return
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
            if len(event_log) > settings.get('EventLogSize', 20):
                event_log.pop(0)
            logging.warning(f"Zero voltage alert on Bank {i}.")
            alert_needed = True
        elif v > settings['HighVoltageThresholdPerBattery']:
            alert = f"Bank {i}: High voltage ({v:.2f}V)."
            alerts.append(alert)
            event_log.append(f"{time.strftime('%Y-%m-%d %H:%M:%S')}: {alert}")
            if len(event_log) > settings.get('EventLogSize', 20):
                event_log.pop(0)
            logging.warning(f"High voltage alert on Bank {i}: {v:.2f}V.")
            alert_needed = True
        elif v < settings['LowVoltageThresholdPerBattery']:
            alert = f"Bank {i}: Low voltage ({v:.2f}V)."
            alerts.append(alert)
            event_log.append(f"{time.strftime('%Y-%m-%d %H:%M:%S')}: {alert}")
            if len(event_log) > settings.get('EventLogSize', 20):
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
def balance_battery_voltages(stdscr, high, low, settings, temps_alerts, is_heating=False):
    """
    Balance the charge between two battery banks by transferring energy from high to low voltage.

    This function is like a water leveler for batteries. When one battery bank has more "energy level"
    (higher voltage) than another, it connects them through special hardware to move some charge
    from the fuller one to the emptier one, making their voltages more equal.

    It's like pouring water from a full bucket to an empty one to balance them out. The process
    takes time and shows progress on the screen. Safety checks prevent balancing if there are
    temperature problems or if it's too soon after the last balance.

    Args:
        stdscr: The terminal screen object for displaying progress.
        high (int): Bank number with higher voltage (source of charge).
        low (int): Bank number with lower voltage (destination for charge).
        settings: Configuration dictionary with timing and threshold values.
        temps_alerts: List of current temperature alerts (prevents balancing if not empty).
        is_heating: Flag to indicate if balancing is for heating mode.

    Returns:
        None
    """
    global balance_start_time, last_balance_time, balancing_active, web_data, alive_timestamp
    if temps_alerts:
        logging.warning("Skipping balancing due to temperature anomalies in banks.")
        return
    mode = "Heating" if is_heating else "Normal"
    logging.info(f"Starting {mode} balance from Bank {high} to {low}.")
    event_log.append(f"{time.strftime('%Y-%m-%d %H:%M:%S')}: {mode} balancing started from Bank {high} to {low}")
    if len(event_log) > settings.get('EventLogSize', 20):
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
        alive_timestamp = time.time()
        elapsed = time.time() - balance_start_time
        progress = min(1.0, elapsed / settings['BalanceDurationSeconds'])
        voltage_high, _, _ = read_voltage_with_retry(high, settings)
        voltage_low, _, _ = read_voltage_with_retry(low, settings)
        bar_length = 20
        filled = int(bar_length * progress)
        bar = '=' * filled + ' ' * (bar_length - filled)
        if progress_y < height and right_half_x + 50 < width:
            try:
                stdscr.addstr(progress_y, right_half_x, f"{mode} Balancing Bank {high} ({voltage_high:.2f}V) -> Bank {low} ({voltage_low:.2f}V)... [{animation_frames[frame_index % 4]}]", curses.color_pair(6))
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
    logging.info(f"{mode} balancing process completed.")
    event_log.append(f"{time.strftime('%Y-%m-%d %H:%M:%S')}: {mode} balancing completed from Bank {high} to {low}")
    if len(event_log) > settings.get('EventLogSize', 20):
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
            try:
                med = statistics.median(bank_temps)
                mn = min(bank_temps)
                mx = max(bank_temps)
            except (TypeError, ValueError, statistics.StatisticsError) as e:
                logging.warning(f"Error calculating stats for bank: {e}, temps={bank_temps}")
                med = mn = mx = 0.0
        else:
            med = mn = mx = 0.0
        bank_stats.append({'median': med, 'min': mn, 'max': mx, 'invalid': invalid_count})
    return bank_stats
def fetch_rrd_history(settings):
    start = int(time.time()) - (HISTORY_LIMIT * 60)
    try:
        def_list = [f'DEF:mt={RRD_FILE}:medtemp:LAST']
        xport_list = ['XPORT:mt:MedianTemp']
        for i in range(1, settings['num_series_banks'] + 1):
            def_list.append(f'DEF:v{i}={RRD_FILE}:volt{i}:LAST')
            xport_list.append(f'XPORT:v{i}:Bank{i}')
        output = subprocess.check_output(['rrdtool', 'xport',
                                          '--start', str(start),
                                          '--end', 'now',
                                          '--step', '60'] + def_list + xport_list)
        logging.debug(f"Raw RRD xport output: {output.decode()}")
        root = ET.fromstring(output.decode())
        meta = root.find('meta')
        if meta is not None:
            meta_start = int(meta.find('start').text) if meta.find('start') is not None else start
            meta_step = int(meta.find('step').text) if meta.find('step') is not None else 60
        else:
            meta_start = start
            meta_step = 60
        data = []
        current_time = meta_start
        expected_vs = settings['num_series_banks'] + 1  # medtemp + volts
        for row in root.findall('.//row'):
            vs = []
            for v in row.findall('v'):
                if v.text is None:
                    vs.append(None)
                    continue
                try:
                    vs.append(float(v.text) if v.text != 'NaN' else None)
                except ValueError:
                    vs.append(None)
            if len(vs) != expected_vs:
                logging.warning(f"Skipping RRD row with incomplete values (got {len(vs)}, expected {expected_vs}).")
                continue
            row_data = {'time': current_time, 'medtemp': vs[0]}
            for i in range(settings['num_series_banks']):
                row_data[f'volt{i+1}'] = vs[i+1]
            data.append(row_data)
            current_time += meta_step
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
    gap = " "
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
    local_ip = 'localhost'
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()
    except Exception:
        local_ip = socket.gethostbyname(socket.gethostname())
    y_config = 3
    config_lines = [
        f"Web Dashboard URL: http://{local_ip}:{settings['web_port']}",
        f"Number of Parallel Batteries: {settings['number_of_parallel_batteries']}",
        f"Number of Series Banks: {settings['num_series_banks']}",
        f"Sensors per Bank per Battery: {settings['sensors_per_bank']}",
        f"Polling Interval: {settings['poll_interval']} seconds",
        f"Temperature IP Address: {settings['ip']}",
        f"Modbus TCP Port: {settings['modbus_port']}",
        f"High Temperature Threshold: {settings['high_threshold']}°C",
        f"Low Temperature Threshold: {settings['low_threshold']}°C",
        f"Absolute Deviation Threshold: {settings['abs_deviation_threshold']}°C",
        f"Relative Deviation Threshold: {settings['deviation_threshold']}",
        f"Abnormal Rise Threshold: {settings['rise_threshold']}°C",
        f"Group Lag Threshold: {settings['disconnection_lag_threshold']}°C",
        f"Cabinet Over-Temp Threshold: {settings['cabinet_over_temp_threshold']}°C",
        f"Valid Minimum Temperature: {settings['valid_min']}°C",
        f"Low Voltage Threshold per Bank: {settings['LowVoltageThresholdPerBattery']}V",
        f"High Voltage Threshold per Bank: {settings['HighVoltageThresholdPerBattery']}V",
        f"Voltage Difference to Balance: {settings['VoltageDifferenceToBalance']}V",
        f"Balance Duration: {settings['BalanceDurationSeconds']} seconds",
        f"Balance Rest Period: {settings['BalanceRestPeriodSeconds']} seconds"
    ]
    col_width = max(len(line) for line in config_lines) + 2
    num_cols = 1
    for i, line in enumerate(config_lines):
        col = i // 20
        row = i % 20
        if col < num_cols and y_config + row < height:
            try:
                stdscr.addstr(y_config + row, right_half_x + col * col_width, line, curses.color_pair(7))
            except curses.error:
                pass
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
    if fcntl is None:
        logging.warning("fcntl not available - watchdog disabled")
        return False
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
                break # Stop petting
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
def startup_self_test(settings, stdscr, data_dir):
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
        logging.debug(f"Configuration details: "
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
        logging.debug("Configuration validation passed.")
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
            if len(event_log) > settings.get('EventLogSize', 20):
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
                if len(event_log) > settings.get('EventLogSize', 20):
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
                if len(event_log) > settings.get('EventLogSize', 20):
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
            if len(event_log) > settings.get('EventLogSize', 20):
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
                _, startup_offsets = load_offsets(settings['total_channels'], data_dir)
                if startup_offsets is None:
                    startup_offsets = [startup_median - t for t in all_initial_temps]
                    save_offsets(startup_median, startup_offsets, data_dir)
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
                    voltage_str = ", ".join([f"Bank {i+1}={v:.2f}V" if v is not None else f"Bank {i+1}=N/A" for i, v in enumerate(initial_bank_voltages)])
                    stdscr.addstr(y + 1, 0, f"Initial Bank Voltages: {voltage_str}", curses.color_pair(4))
                except curses.error:
                    logging.warning("addstr error for initial bank voltages.")
            voltage_debug = ", ".join([f"Bank {i+1}={v:.2f}V" if v is not None else f"Bank {i+1}=N/A" for i, v in enumerate(initial_bank_voltages)])
            logging.debug(f"Initial Bank Voltages: {voltage_debug}")
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
                    if len(event_log) > settings.get('EventLogSize', 20):
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
                        if len(event_log) > settings.get('EventLogSize', 20):
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
                    if len(event_log) > settings.get('EventLogSize', 20):
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
            time.sleep(10) # Short sleep chunks with checks
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
def start_web_server(settings):
    """
    Start a web server to provide a user-friendly dashboard in a web browser.

    This creates a simple website that shows:
    - Current battery voltages and temperatures
    - Any alerts or problems
    - Historical charts of voltage and temperature trends
    - Controls to manually trigger balancing

    The web server runs in the background and can be accessed from any device
    on the network using a web browser. It provides an easy way to monitor
    the battery system without needing to look at the terminal.
    """
    global web_server
    if not settings['WebInterfaceEnabled']:
        logging.info("Web interface disabled via configuration.")
        return
    if Flask is None:
        logging.warning("Flask not available - web interface cannot start.")
        return
    app = Flask(__name__)
    @app.route('/')
    def index():
        # Build dynamic datasets for voltage banks
        colors = ['green', 'blue', 'red', 'orange', 'purple', 'brown', 'pink', 'gray']
        datasets_js = ""
        for i in range(1, settings['num_series_banks'] + 1):
            color = colors[(i-1) % len(colors)]
            datasets_js += "{{ label: 'Bank " + str(i) + " V', data: hist.map(h => h.volt" + str(i) + "), borderColor: '" + color + "' }},\n                        "
        # Build the complete datasets array
        datasets_array = """
                        {datasets_js}
                        {{ label: 'Median Temp °C', data: hist.map(h => h.medtemp), borderColor: 'cyan', yAxisID: 'temp' }}
                    """.format(datasets_js=datasets_js)
        logging.debug("Constructed datasets_array: {0}".format(datasets_array))
        html = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Battery Management System</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        body {{ font-family: Arial, sans-serif; margin: 20px; transition: background-color 0.3s, color 0.3s; }}
        body.light {{ background-color: #f5f5f5; color: #000; }}
        body.dark {{ background-color: #1e1e1e; color: #fff; }}
        .container {{ max-width: 1200px; margin: 0 auto; }}
        .header {{ padding: 15px; border-radius: 5px; transition: background-color 0.3s, color 0.3s; }}
        .header.light {{ background-color: #2c3e50; color: white; }}
        .header.dark {{ background-color: #121212; color: #ddd; }}
        .status-card {{ border-radius: 5px; padding: 15px; margin: 10px 0; box-shadow: 0 2px 5px rgba(0,0,0,0.1); transition: background-color 0.3s, color 0.3s; }}
        .status-card.light {{ background-color: white; color: #000; }}
        .status-card.dark {{ background-color: #333; color: #ddd; box-shadow: 0 2px 5px rgba(255,255,255,0.1); }}
        .battery {{ display: inline-block; margin: 10px; padding: 10px; border: 1px solid #ddd; border-radius: 5px; transition: background-color 0.3s, border-color 0.3s; }}
        .battery.light {{ background-color: #f9f9f9; border-color: #ddd; }}
        .battery.dark {{ background-color: #444; border-color: #555; }}
        .voltage {{ font-size: 1.2em; font-weight: bold; }}
        .bank-summary {{ font-size: 0.9em; }}
        .temperatures {{ font-size: 0.8em; max-height: 200px; overflow-y: auto; }}
        .alert {{ color: #e74c3c; font-weight: bold; }}
        .normal {{ color: #27ae60; }}
        .warning {{ color: #f39c12; }}
        .button {{ background-color: #3498db; color: white; border: none; padding: 10px 15px; border-radius: 3px; cursor: pointer; transition: background-color 0.3s; }}
        .button:hover {{ background-color: #2980b9; }}
        .button:disabled {{ background-color: #95a5a6; cursor: not-allowed; }}
        .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 15px; }}
        #dark-mode-toggle {{ background-color: #555; color: white; margin-left: 10px; }}
        #dark-mode-toggle.light {{ background-color: #555; }}
        #dark-mode-toggle.dark {{ background-color: #aaa; color: #000; }}
    </style>
</head>
<body class="light">
    <div class="container">
        <div class="header light">
            <h1>Battery Management System</h1>
            <p>Status: <span id="system-status">Loading...</span></p>
            <p>Last Update: <span id="last-update">-</span></p>
            <button id="dark-mode-toggle" class="button">Dark Mode</button>
        </div>
        <div class="status-card light">
            <h2>System Information</h2>
            <p>Total Voltage: <span id="total-voltage">-</span></p>
            <p>Balancing: <span id="balancing-status">No</span></p>
        </div>
        <div class="status-card light">
            <h2>Actions</h2>
            <button id="refresh-btn" class="button">Refresh</button>
            <button id="balance-btn" class="button" disabled>Balance Now</button>
        </div>
        <div class="status-card light">
            <h2>Alerts</h2>
            <div id="alerts-container"></div>
        </div>
        <div class="status-card light">
            <h2>Battery Banks</h2>
            <div id="battery-container" class="grid"></div>
        </div>
        <div class="status-card light">
            <h2>Time-Series Charts</h2>
            <canvas id="bmsChart" width="800" height="400"></canvas>
        </div>
    </div>
    <script>
        const body = document.body;
        const header = document.querySelector('.header');
        const statusCards = document.querySelectorAll('.status-card');
        const darkModeToggle = document.getElementById('dark-mode-toggle');
        darkModeToggle.addEventListener('click', () => {{
            if (body.classList.contains('light')) {{
                body.classList.remove('light');
                body.classList.add('dark');
                header.classList.remove('light');
                header.classList.add('dark');
                statusCards.forEach(card => {{
                    card.classList.remove('light');
                    card.classList.add('dark');
                }});
                darkModeToggle.textContent = 'Light Mode';
            }} else {{
                body.classList.remove('dark');
                body.classList.add('light');
                header.classList.remove('dark');
                header.classList.add('light');
                statusCards.forEach(card => {{
                    card.classList.remove('dark');
                    card.classList.add('light');
                }});
                darkModeToggle.textContent = 'Dark Mode';
            }}
        }});
        function updateStatus() {{
            fetch('/api/status')
                .then(response => response.json())
                .then(data => {{
                    document.getElementById('system-status').textContent = data.system_status;
                    document.getElementById('last-update').textContent = new Date(data.last_update * 1000).toLocaleString();
                    document.getElementById('total-voltage').textContent = data.total_voltage.toFixed(2) + 'V';
                    document.getElementById('balancing-status').textContent = data.balancing ? 'Yes' : 'No';
                    const batteryContainer = document.getElementById('battery-container');
                    batteryContainer.innerHTML = '';
                    const sensorsPerBank = data.temperatures.length / data.voltages.length;
                    data.voltages.forEach((voltage, index) => {{
                        const summary = data.bank_summaries[index];
                        const bankDiv = document.createElement('div');
                        bankDiv.className = 'battery';
                        bankDiv.innerHTML = `
                            <h3>Bank ${{{index + 1}}}</h3>
                            <p class="voltage ${{{voltage === 0 || voltage === null ? 'alert' : (voltage > 21 || voltage < 18.5) ? 'warning' : 'normal'}}}}">
                                ${{{voltage !== null ? voltage.toFixed(2) : 'N/A'}}}V
                            </p>
                            <div class="bank-summary">
                                <p class="temperature ${{{summary.median > 60 || summary.median < 0 || summary.invalid > 0 ? 'warning' : 'normal'}}}}">
                                    Median: ${{{summary.median.toFixed(1)}}}°C Min: ${{{summary.min.toFixed(1)}}}°C Max: ${{{summary.max.toFixed(1)}}}°C Invalid: ${{{summary.invalid}}}
                                </p>
                            </div>
                            <div class="temperatures">
                                ${{{data.temperatures.slice(index * sensorsPerBank, (index + 1) * sensorsPerBank).map((temp, localIndex) => {{
                                    const globalIndex = index * sensorsPerBank + localIndex;
                                    const batId = Math.floor(globalIndex / 24) + 1;
                                    const localCh = (globalIndex % 24) + 1;
                                    return `<p class="temperature ${{{temp === null ? 'alert' : (temp > 60 || temp < 0) ? 'warning' : 'normal'}}}}">
                                        Bat ${{{batId}}} Local C${{{localCh}}}: ${{{temp !== null ? temp.toFixed(1) + '°C' : 'N/A'}}}
                                    </p>`;
                                }}).join('')}}}
                            </div>
                        `;
                        batteryContainer.appendChild(bankDiv);
                    }});
                    const alertsContainer = document.getElementById('alerts-container');
                    if (data.alerts.length > 0) {{
                        alertsContainer.innerHTML = data.alerts.map(alert => `<p class="alert">${{{alert}}}</p>`).join('');
                    }} else {{
                        alertsContainer.innerHTML = '<p class="normal">No alerts</p>';
                    }}
                    const balanceBtn = document.getElementById('balance-btn');
                    balanceBtn.disabled = data.balancing || data.alerts.length > 0;
                }})
                .catch(error => {{
                    console.error('Error fetching status:', error);
                    document.getElementById('system-status').textContent = 'Error';
                }});
        }}
        let myChart = null;
        function updateChart() {{
            fetch('/api/history')
                .then(response => response.json())
                .then(data => {{
                    const hist = data.history;
                    const labels = hist.map(h => new Date(h.time * 1000).toLocaleTimeString());
                    const datasets = [
                        {datasets_array}
                    ];
                    const ctx = document.getElementById('bmsChart').getContext('2d');
                    if (hist.length === 0) {{
                        ctx.fillStyle = 'red';
                        ctx.fillText('No history data available', 10, 50);
                        return;
                    }}
                    if (myChart) {{
                        myChart.destroy();
                    }}
                    myChart = new Chart(ctx, {{
                        type: 'line',
                        data: {{ labels, datasets }},
                        options: {{
                            scales: {{
                                y: {{ type: 'linear', position: 'left', title: {{ display: true, text: 'Voltage (V)' }} }},
                                temp: {{ type: 'linear', position: 'right', title: {{ display: true, text: 'Temp (°C)' }}, grid: {{ drawOnChartArea: false }} }}
                            }}
                        }}
                    }});
                }})
                .catch(error => console.error('Error fetching history:', error));
        }}
        function initiateBalance() {{
            fetch('/api/balance', {{ method: 'POST' }})
                .then(response => response.json())
                .then(data => {{
                    if (data.success) {{
                        alert('Balancing initiated');
                    }} else {{
                        alert('Error: ' + data.message);
                    }}
                }})
                .catch(error => {{
                    console.error('Error initiating balance:', error);
                    alert('Error initiating balance');
                }});
        }}
        document.getElementById('refresh-btn').addEventListener('click', updateStatus);
        document.getElementById('balance-btn').addEventListener('click', initiateBalance);
        updateStatus();
        updateChart();
        setInterval(updateStatus, 5000);
        setInterval(updateChart, 60000);
    </script>
</body>
</html>""".format(datasets_array=datasets_array)
        return html
    @app.route('/api/status')
    def api_status():
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
        return jsonify(response)
    @app.route('/api/history')
    def api_history():
        history = fetch_rrd_history(settings)
        return jsonify({'history': history})
    @app.route('/api/balance', methods=['POST'])
    def api_balance():
        global balancing_active
        if balancing_active:
            return jsonify({'success': False, 'message': 'Balancing already in progress'}), 400
        if len(web_data['alerts']) > 0:
            return jsonify({'success': False, 'message': 'Cannot balance with active alerts'}), 400
        voltages = web_data['voltages']
        if len(voltages) < 2:
            return jsonify({'success': False, 'message': 'Not enough battery banks'}), 400
        max_v = max(voltages)
        min_v = min(voltages)
        high_bank = voltages.index(max_v) + 1
        low_bank = voltages.index(min_v) + 1
        if max_v - min_v < settings['VoltageDifferenceToBalance']:
            return jsonify({'success': False, 'message': 'Voltage difference too small for balancing'}), 400
        balancing_active = True
        logging.info(f"Balancing initiated via web API from Bank {high_bank} to Bank {low_bank}")
        return jsonify({'success': True, 'message': f'Balancing initiated from Bank {high_bank} to Bank {low_bank}'})
    @app.before_request
    def before_request():
        if settings['auth_required']:
            auth = request.authorization
            if not auth or not (auth.username == settings['username'] and auth.password == settings['password']):
                return make_response('Unauthorized', 401, {'WWW-Authenticate': 'Basic realm="BMS"'})
        if settings['cors_enabled']:
            response = make_response()
            response.headers['Access-Control-Allow-Origin'] = settings['cors_origins']
            if request.method == 'OPTIONS':
                response.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
                response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization'
                return response
    def run_app():
        logging.info("Starting Flask app...")
        try:
            app.run(host=settings['host'], port=settings['web_port'], threaded=True, debug=False, use_reloader=False)
        except Exception as e:
            logging.error(f"Web server error: {e}\n{traceback.format_exc()}")
    server_thread = threading.Thread(target=run_app)
    server_thread.daemon = True
    server_thread.start()
    logging.info(f"Web server started on {settings['host']}:{settings['web_port']}")
def main(stdscr):
    check_dependencies()
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
    settings = load_config(data_dir)
    validate_config(settings)
    NUM_BANKS = settings['num_series_banks'] # Dynamic now.
    number_parallel = settings['number_of_parallel_batteries']
    slave_addresses = settings['modbus_slave_addresses']
    sensors_per_bank = settings['sensors_per_bank']
    sensors_per_battery = NUM_BANKS * sensors_per_bank
    total_channels = number_parallel * sensors_per_battery
    BANK_SENSOR_INDICES = [[] for _ in range(settings['num_series_banks'])] # Dynamic list of lists.

    # Initialize web_data arrays dynamically
    web_data['voltages'] = [0.0] * NUM_BANKS
    web_data['temperatures'] = [None] * total_channels
    web_data['bank_summaries'] = [{'median': 0.0, 'min': 0.0, 'max': 0.0, 'invalid': 0}] * NUM_BANKS
    for bat in range(number_parallel):
        base = bat * sensors_per_battery
        for bank_id in range(NUM_BANKS):
            bank_base = base + bank_id * sensors_per_bank
            BANK_SENSOR_INDICES[bank_id].extend(range(bank_base, bank_base + sensors_per_bank))
    setup_hardware(settings)
    start_web_server(settings)
    startup_self_test(settings, stdscr, data_dir)
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
        # Main monitoring loop - runs forever, checking batteries every few seconds
        temps_alerts = []  # List to collect any temperature problems we find
        all_raw_temps = []  # Will hold all raw temperature readings from all sensors

        # Step 1: Read temperatures from all battery packs
        # Loop through each parallel battery and ask its sensors for temperature data
        for addr in slave_addresses:
            temp_result = read_ntc_sensors(
                settings['ip'], settings['modbus_port'], settings['query_delay'],
                sensors_per_battery, settings['scaling_factor'],
                settings['max_retries'], settings['retry_backoff_base'], slave_addr=addr
            )
            if isinstance(temp_result, str):
                # If reading failed, add to alerts and use dummy values
                temps_alerts.append(f"Modbus slave {addr} failed: {temp_result}")
                all_raw_temps.extend([settings['valid_min']] * sensors_per_battery)
            else:
                # Success - add the readings to our collection
                all_raw_temps.extend(temp_result)
        raw_temps = all_raw_temps
        valid_count = sum(1 for t in raw_temps if t > settings['valid_min'])

        # Step 2: Calibrate temperature readings (adjust for sensor differences)
        # On first run with all sensors working, calculate average temperature and create
        # adjustment values for each sensor to make them all read the same in the future
        if not startup_set and valid_count == total_channels:
            startup_median = statistics.median(raw_temps)  # Find the middle temperature value
            startup_offsets = [startup_median - t for t in raw_temps]  # Calculate adjustments
            save_offsets(startup_median, startup_offsets, data_dir)  # Save to file
            startup_set = True
            logging.info(f"Temp calibration set. Median: {startup_median:.1f}°C")
        if startup_set and startup_offsets is None:
            startup_set = False

        # Apply calibration adjustments to get accurate temperatures
        calibrated_temps = [raw_temps[i] + startup_offsets[i] if startup_set and raw_temps[i] > settings['valid_min'] else raw_temps[i] if raw_temps[i] > settings['valid_min'] else None for i in range(total_channels)]
        bank_stats = compute_bank_medians(calibrated_temps, settings['valid_min'])
        bank_medians = [s['median'] for s in bank_stats]
        for ch, raw in enumerate(raw_temps, 1):
            if check_invalid_reading(raw, ch, temps_alerts, settings['valid_min'], settings):
                continue
            calib = calibrated_temps[ch-1]
            bank_id = get_bank_for_channel(ch)
            bank_median = bank_medians[bank_id - 1]
            check_high_temp(calib, ch, temps_alerts, settings['high_threshold'], settings)
            check_low_temp(calib, ch, temps_alerts, settings['low_threshold'], settings)
            check_deviation(calib, bank_median, ch, temps_alerts, settings['abs_deviation_threshold'], settings['deviation_threshold'], settings)
        if run_count > 0 and previous_temps and previous_bank_medians is not None:
            for bank_id in range(1, NUM_BANKS + 1):
                bank_median_rise = bank_medians[bank_id - 1] - previous_bank_medians[bank_id - 1]
                bank_indices = BANK_SENSOR_INDICES[bank_id - 1]
                for i in bank_indices:
                    ch = i + 1
                    calib = calibrated_temps[i]
                    if calib is not None:
                        check_abnormal_rise(calib, previous_temps, ch, temps_alerts, settings['poll_interval'], settings['rise_threshold'], settings)
                        check_group_tracking_lag(calib, previous_temps, bank_median_rise, ch, temps_alerts, settings['disconnection_lag_threshold'], settings)
                    check_sudden_disconnection(calib, previous_temps, ch, temps_alerts, settings)
        previous_temps = calibrated_temps[:]
        previous_bank_medians = bank_medians[:]
        valid_calib_temps = [t for t in calibrated_temps if t is not None]
        try:
            overall_median = statistics.median(valid_calib_temps) if valid_calib_temps else 0.0
        except (TypeError, statistics.StatisticsError) as e:
            logging.warning(f"Error calculating overall median: {e}, using 0.0")
            overall_median = 0.0
        if overall_median > settings['cabinet_over_temp_threshold']:
            if GPIO:
                GPIO.output(settings['FanRelayPin'], GPIO.HIGH)
            logging.info(f"Cabinet over temp: {overall_median:.1f}°C > {settings['cabinet_over_temp_threshold']}°C. Fan activated.")
            if not any("Cabinet over temp" in a for a in temps_alerts):
                temps_alerts.append(f"Cabinet over temp: {overall_median:.1f}°C > {settings['cabinet_over_temp_threshold']}°C. Fan on.")
                event_log.append(f"{time.strftime('%Y-%m-%d %H:%M:%S')}: Cabinet over temp: {overall_median:.1f}°C > {settings['cabinet_over_temp_threshold']}°C. Fan on.")
                if len(event_log) > settings.get('EventLogSize', 20):
                    event_log.pop(0)
        else:
            if GPIO:
                GPIO.output(settings['FanRelayPin'], GPIO.LOW)
            logging.info("Cabinet temp normal. Fan deactivated.")
        # Step 4: Read voltage levels from each battery bank
        battery_voltages = []
        for i in range(1, NUM_BANKS + 1):
            v, _, _ = read_voltage_with_retry(i, settings)  # Read voltage with error handling
            battery_voltages.append(v if v is not None else 0.0)  # Use 0.0 if reading failed

        # Step 5: Check for any problems (voltage too high/low, temperature issues)
        alert_needed, all_alerts = check_for_issues(battery_voltages, temps_alerts, settings)

        # Step 6: Save current data to the time-series database for charts
        timestamp = int(time.time())
        values = f"{timestamp}:{overall_median}:{':'.join(map(str, battery_voltages))}"
        subprocess.call(['rrdtool', 'update', RRD_FILE, values])
        logging.debug(f"RRD updated with: {values}")

        # Step 7: Decide if we need to balance the batteries
        if len(battery_voltages) == NUM_BANKS:
            max_v = max(battery_voltages)  # Find highest voltage bank
            min_v = min(battery_voltages)  # Find lowest voltage bank
            high_b = battery_voltages.index(max_v) + 1  # Bank number with highest voltage
            low_b = battery_voltages.index(min_v) + 1   # Bank number with lowest voltage
            current_time = time.time()
            any_low_temp = any(t is not None and t < 10 for t in calibrated_temps)

            # Balance if: already balancing, OR (no alerts AND (low temp or voltage difference big enough) AND lowest isn't zero AND enough time has passed since last balance)
            if balancing_active or (not alert_needed and (any_low_temp or max_v - min_v > settings['VoltageDifferenceToBalance']) and min_v > 0 and current_time - last_balance_time > settings['BalanceRestPeriodSeconds']):
                is_heating = any_low_temp
                balance_battery_voltages(stdscr, high_b, low_b, settings, temps_alerts, is_heating=is_heating)  # Transfer charge
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
        alive_timestamp = time.time() # Update aliveness for watchdog thread
        run_count += 1
        gc.collect()
        logging.info("Poll cycle complete.")
        time.sleep(settings['poll_interval'])
       
if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Battery Management System')
    parser.add_argument('--validate-config', action='store_true', help='Validate configuration and exit')
    parser.add_argument('--data-dir', default='.', help='Directory containing config files')
    args = parser.parse_args()
    data_dir = args.data_dir
    if args.validate_config:
        try:
            config_parser.read(os.path.join(data_dir, 'battery_monitor.ini'))
            settings = load_config(data_dir)
            validate_config(settings)
            print("Configuration validation passed.")
            sys.exit(0)
        except Exception as e:
            print(f"Configuration validation failed: {e}")
            sys.exit(1)
    else:
        logging.basicConfig(
            filename=os.path.join(data_dir, 'battery_monitor.log'),
            level=logging.INFO,
            format='%(asctime)s - %(message)s'
        )
        config_parser.read(os.path.join(data_dir, 'battery_monitor.ini'))
        RRD_FILE = os.path.join(data_dir, 'bms.rrd')
        curses.wrapper(main)
