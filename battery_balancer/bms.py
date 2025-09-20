# --------------------------------------------------------------------------------
# Battery Management System (BMS) Script Documentation
# --------------------------------------------------------------------------------
#
# **Script Name:** bms.py
# **Version:** 1.10 (As of September 07, 2025) - Added thread safety with locks for web_data to prevent race conditions. Enhanced error handling in API routes with try/except and JSON error responses.
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
# - Safety: Skips balancing if alerts active (e.g., anomalies) or if balancer_failed flag is set. Rests for BalanceRestPeriodSeconds (e.g., 60s) after balancing.
# - Balancing Verification: During startup and regular balancing, monitors voltage trends. If no expected decrease in source or increase in destination (min_delta, e.g., 0.01V), raises alert and sets balancer_failed=True to prevent future balancing until restart or manual reset.
# - Voltage Checks: Alerts if < LowVoltageThresholdPerBattery (e.g., 18.5V), > HighVoltageThresholdPerBattery (e.g., 21.0V), or zero.
# - **Alerts & Notifications:** Logs to 'battery_monitor.log'. Activates alarm relay on issues. Sends throttled emails (e.g., every 3600s) via SMTP.
# - **Watchdog:** If enabled, pets hardware watchdog via dedicated thread (every 5s with aliveness check via timestamp) to prevent resets on hangs. Uses /dev/watchdog with 15s timeout (Pi max).
# - **User Interfaces:**
# - **TUI (Terminal UI):** Uses curses for real-time display: ASCII art batteries (dynamic for num_series_banks) with voltages/temps, alerts, balancing progress bar/animation, last 20 events. Now includes ASCII line charts for voltage history per bank and median temperature, placed in the top-right section for visualization of trends over time.
# - **Web Dashboard:** HTTP server on port 8080 (configurable). Shows voltages, temps, alerts, balancing status. Supports API for status/balance/history. Optional auth/CORS. Now includes interactive time-series charts using Chart.js for voltages per bank and median temperature, placed at the top of the page after the header for easy viewing.
# - **Time-Series Logging:** Uses RRDTool for persistent storage of bank voltages and overall median temperature. Data is updated every poll interval (e.g., 10s), but RRD is configured with 1min steps for aggregation. History is limited to ~480 entries (e.g., 8 hours). Fetch functions retrieve data for TUI and web rendering.
# - **Startup Self-Test:** Validates config, hardware connections (I2C/Modbus per slave), initial reads, balancer (tests all pairs for voltage changes).
# - Retries on failure after 2min. After max retries, proceeds to main loop with startup_failed reset to False to allow balancing, avoiding perpetual blocking. Logs warnings.
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
# - Startup Check: Like a self-diagnostic when your car starts – ensures everything's connected and working before running. Proceeds after retries with flags reset for operation.
# - Time-Series: Tracks history of voltages and temps, shows trends in charts to spot patterns over time.
# - Balancing Fail-Safe: Verifies energy transfer by checking voltage changes; disables balancing if hardware issue detected (e.g., relays not switching).
# **How It Works (Step-by-Step for Non-Programmers):**
# 1. **Start:** Loads settings from INI file (like a recipe book).
# 2. **Setup:** Connects to hardware (sensors, relays) – if missing, runs in "pretend" mode. Creates/loads RRD database for history.
# 3. **Self-Test:** Checks if config makes sense, hardware responds (per Modbus slave), sensors give good readings, aggregated. Balancing actually changes voltages (verifies relay switching via voltage deltas). If fail, alerts and retries. After max retries, proceeds with flags reset.
# 4. **Main Loop (Repeats Forever):**
# - Read temperatures from all slaves, aggregate.
# - Calibrate them (adjust based on startup values for accuracy).
# - Check for temperature problems (too hot, too cold, etc.).
# - Read voltages from configured banks.
# - Check for voltage problems (too high, too low, zero).
# - Update RRD database with voltages and median temp.
# - If cold (<10°C anywhere), balance to heat up (with verification).
# - Else, if voltages differ too much, balance normally (with verification).
# - Skip if alerts active or balancer failed.
# - Fetch history from RRD for charts.
# - Update terminal (with ASCII charts and full temp lists)/web displays (with Chart.js and full lists).
# - Log events, send emails if issues.
# - Update alive timestamp for watchdog.
# - Wait a bit (e.g., 10s), repeat.
# 5. **Balancing Process:** Connects high to low bank with relays, turns on converter to transfer charge, monitors voltages for changes, shows progress, turns off after time. Verifies deltas; alerts/disables if failed.
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
| Balancer works? Verify deltas) |
| If fail: Alert, Retry |
| After max retries: Reset flags, Proceed |
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
| Balance for Heating (Verify Deltas) | |
| Else If Volt Diff > Th: | |
| Balance Normally (Verify Deltas) | |
| (High to Low Bank) | |
| Skip if Alerts/Balancer Failed | |
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
# - **Standard Python Libraries:** socket (networking), statistics (math like medians), time (timing/delays), configparser (read INI), logging (save logs), signal (handle shutdown), gc (memory cleanup), os (files), sys (exit), argparse (command-line), threading (web server and watchdog), json/http.server/urllib/base64 (web), traceback (errors), fcntl/struct (watchdog), subprocess (for rrdtool commands), xml.etree.ElementTree (for parsing RRD XML output).
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
# **Validate Config:** python bms.py --validate-config [--data-dir /path/to/config]
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
# - **Balancing Failures:** If voltage doesn't change during balancing, script detects it (no silent fail), alerts, and disables future balancing to prevent hardware damage.
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
    print("Flask not available - web interface disabled") # Warn user if Flask library is missing.
    Flask = None # Set to none if missing, so web features are skipped.
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
balancer_failed = False # New: Indicates if balancer hardware failed verification - prevents future balancing.
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
data_lock = threading.Lock() # Lock for thread-safe access to web_data

def check_dependencies():
    """
    Check for required and optional dependencies at startup.
    This function is like a pre-flight checklist for the script. It verifies if all the necessary software tools (libraries)
    are installed on the system. Critical ones (like hardware communication libraries) are mandatory—if missing, the script stops.
    Optional ones (like for web interface or charts) are noted but the script continues without them, using fallback modes.
    This prevents crashes later when the script tries to use missing tools. For non-programmers: Imagine checking if your toolbox
    has all hammers and screwdrivers before building something; if a hammer is missing, you can't proceed safely.
    
    Returns:
        None: Just logs messages and exits if critical issues found.
    """
    # Define lists of critical and optional dependencies.
    # Critical: Hardware-related libraries without which the script can't interact with physical devices.
    critical_deps = ['smbus', 'RPi.GPIO']
    # Optional: Features like time-series charts, ASCII art, or web server—nice to have but not essential.
    optional_deps = ['rrdtool', 'art', 'flask']
    # Lists to track missing items.
    missing_critical = []
    missing_optional = []
   
    # Loop through critical dependencies and try to import each one.
    # If import fails, add to missing list—like testing if a tool works by picking it up.
    for dep in critical_deps:
        try:
            __import__(dep)  # Attempt to load the library into memory.
        except ImportError:
            missing_critical.append(dep)  # Note it's missing if load fails.
   
    # Loop through optional dependencies and test them similarly.
    # For rrdtool, we run a command-line check instead of import, as it's an external tool.
    for dep in optional_deps:
        try:
            if dep == 'rrdtool':
                # For rrdtool, run a version check command silently (no output shown).
                subprocess.check_call(['rrdtool', '--version'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                __import__(dep)  # Standard import test for others.
        except (ImportError, subprocess.CalledProcessError, FileNotFoundError):
            missing_optional.append(dep)  # Note missing if test fails.
   
    # If any critical dependency is missing, log an error, print a message with install instructions, and exit the script.
    # This ensures the system is properly set up before proceeding.
    if missing_critical:
        msg = f"Critical dependencies missing: {', '.join(missing_critical)}. Install with: sudo apt install python3-{' python3-'.join(missing_critical)}. Exiting."
        logging.error(msg)  # Write error to log file.
        print(msg)  # Show on screen.
        sys.exit(1)  # Stop the script immediately.
   
    # For missing optional deps, log warnings and print helpful messages with install commands.
    # The script continues but with reduced features (e.g., no web dashboard if Flask missing).
    if missing_optional:
        for dep in missing_optional:
            if dep == 'rrdtool':
                msg = "Optional dependency 'rrdtool' missing. Time-series logging disabled. Install with: sudo apt install rrdtool."
            elif dep == 'art':
                msg = "Optional dependency 'art' missing. ASCII art disabled. Install with: pip install art."
            logging.warning(msg)  # Write to log.
            print(msg)  # Show on screen.
   
    # If all checks pass, log success.
    logging.info("Dependency check passed.")

def get_bank_for_channel(ch):
    """
    Find which battery bank a temperature sensor belongs to.
    This function is like a map or directory that tells you which "group" (battery bank) a specific sensor is monitoring.
    In a battery system, sensors are organized by banks (series groups). This helps analyze temperatures per bank.
    For example, if you have 3 banks with 8 sensors each, it figures out if channel 5 is in bank 1 or 2.
    Non-programmer analogy: Like finding which floor an apartment number belongs to in a building.
    
    Args:
        ch (int): Sensor channel number (1 to total_channels) - the sensor ID, starting from 1.
    
    Returns:
        int: Bank number (1 to num_series_banks) or None if the channel is invalid or out of range.
    """
    # Loop through each bank (starting from bank 1).
    # enumerate(BANK_SENSOR_INDICES, 1) gives bank_id (1,2,3...) and its list of sensor indices (0-based).
    for bank_id, indices in enumerate(BANK_SENSOR_INDICES, 1):
        # Check if the 0-based version of ch (ch-1) is in this bank's sensor list.
        if ch - 1 in indices:
            return bank_id  # Found it—return the bank number.
    # If not found in any bank, it's invalid.
    return None

def get_battery_and_local_ch(ch):
    """
    Find the parallel battery ID and local channel for a global channel.
    This function breaks down a global sensor ID into which parallel battery it's on and its local position within that battery.
    Batteries can be in parallel (multiple identical packs), each with their own sensors. Global channels are numbered across all.
    For example, with 4 parallel batteries and 24 sensors each, channel 25 would be battery 2, local channel 1.
    Non-programmer analogy: Like converting a full address (street number) to building number and room number.
    
    Args:
        ch (int): Global channel (1 to total_channels) - global ID, starting from 1.
    
    Returns:
        tuple: (battery_id, local_ch) - battery number (1+), local channel (1 to sensors_per_battery).
    """
    # Hardcoded: Each parallel battery has 24 sensors (num_series_banks * sensors_per_bank, assuming 3*8=24).
    sensors_per_battery = 24
    # Calculate which battery: Divide global index (0-based) by sensors per battery, add 1 for 1-based.
    bat_id = ((ch - 1) // sensors_per_battery) + 1
    # Local channel: Remainder of division, add 1 for 1-based.
    local_ch = ((ch - 1) % sensors_per_battery) + 1
    # Return as a pair (tuple).
    return bat_id, local_ch

def modbus_crc(data):
    """
    Calculate a checksum (CRC) to ensure data integrity for Modbus communication.
    Modbus is a protocol for talking to industrial devices like temperature sensors. CRC is like a fingerprint
    that verifies the message wasn't garbled during transmission (e.g., by electrical noise on wires).
    This function computes the CRC-16 checksum using the Modbus polynomial (0xA001), which is standard for error checking.
    Non-programmer analogy: Like double-checking a phone number by repeating it—ensures no digits were misheard.

    Args:
        data (bytes): Data to calculate the CRC for - the message bytes to checksum.

    Returns:
        bytes: 2-byte CRC value in little-endian order - the check code appended to messages.
    """
    # Start with initial CRC value of 0xFFFF (standard for Modbus).
    crc = 0xFFFF
    # Process each byte in the data.
    for byte in data:
        # XOR the current CRC with the byte (combines them bitwise).
        crc ^= byte
        # For 8 bits in the byte, shift and possibly XOR with polynomial.
        for _ in range(8):
            # If least significant bit is 1, shift right and XOR with 0xA001 (Modbus poly).
            if crc & 0x0001:
                crc = (crc >> 1) ^ 0xA001
            else:
                # Just shift right if LSB is 0.
                crc >>= 1
    # Convert the 16-bit CRC to 2 bytes, little-endian (low byte first).
    return crc.to_bytes(2, 'little')

def test_modbus_connectivity(ip, port):
    """
    Test network connectivity to the Modbus device.
    Attempts a socket connection with a short timeout to check if the device is reachable.
    Non-programmer analogy: Like knocking on a door to see if someone is home.

    Args:
        ip (str): IP address of the Modbus device.
        port (int): Port number.

    Returns:
        bool: True if connection succeeds, False otherwise.
    """
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(1)  # 1 second timeout
        s.connect((ip, port))
        s.close()
        return True
    except socket.error:
        return False

def read_ntc_sensors(ip, modbus_port, query_delay, num_channels, scaling_factor, max_retries, retry_backoff_base, slave_addr=1):
    """
    Read temperature measurements from NTC thermistor sensors.
    This function acts like a messenger: it calls up the temperature sensor device over the network,
    asks for the latest readings from all the sensors, waits for the response, checks that the data
    is valid and not corrupted, then converts the raw numbers into actual temperature values in Celsius.
    Imagine you're calling a weather station to get temperature readings from multiple locations.
    This function dials the number (IP address), asks for the data, waits patiently, and then
    translates the technical numbers into something you can understand. It retries on failures
    with increasing delays (backoff) to handle temporary network glitches. For each slave (parallel battery),
    it queries all sensors in that battery (num_channels = sensors_per_battery).
    
    Args:
        ip (str): The internet address of the sensor device (like a phone number for the device).
        modbus_port (int): The specific "door" or channel on the device to communicate through.
        query_delay (float): How long to wait after asking for data before expecting a reply (in seconds).
        num_channels (int): How many temperature sensors to read from this device (sensors per battery).
        scaling_factor (float): A math number to convert raw sensor data into Celsius degrees (e.g., 100.0).
        max_retries (int): If the first attempt fails, how many more times to try again (total attempts = max_retries).
        retry_backoff_base (int): How long to wait between retry attempts (gets longer each time, exponential).
        slave_addr (int): The specific sensor unit's ID number on the network (default is 1).
    
    Returns:
        list or str: Either a list of temperature values in Celsius (one per channel), or an error message string if something went wrong.
    """
    # Log start of read for this slave—helps debugging.
    logging.info(f"Starting temp read for slave {slave_addr}.")
    # Build the Modbus query packet: Slave addr + function code 3 (read holding registers) + start addr 0 + num registers.
    query_base = bytes([slave_addr, 3]) + (0).to_bytes(2, 'big') + (num_channels).to_bytes(2, 'big')
    # Calculate CRC checksum for the query.
    crc = modbus_crc(query_base)
    # Append CRC to complete the query packet.
    query = query_base + crc
    # Network retry counter for transient network issues.
    network_retry_count = 0
    # Retry loop: Try up to max_retries times if failures occur.
    for attempt in range(max_retries):
        try:
            # Log the attempt for debugging.
            logging.debug(f"Temp read attempt {attempt+1} for slave {slave_addr}: Connecting to {ip}:{modbus_port}")
            # Create a TCP socket for network communication.
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            # Set a 3-second timeout for the connection/response.
            s.settimeout(3)
            # Connect to the device's IP and port.
            s.connect((ip, modbus_port))
            # Send the query packet over the network.
            s.send(query)
            # Wait the specified delay for the device to process and respond.
            time.sleep(query_delay)
            # Receive up to 1024 bytes of response.
            response = s.recv(1024)
            # Close the socket connection.
            s.close()
            # Basic validation: Response too short to be valid.
            if len(response) < 5:
                raise ValueError("Short response")
            # Check if response length matches expected: 3 header bytes + byte_count + 2 CRC.
            if len(response) != 3 + response[2] + 2:
                raise ValueError("Invalid response length")
            # Calculate CRC on received data (excluding received CRC) and compare.
            calc_crc = modbus_crc(response[:-2])
            if calc_crc != response[-2:]:
                raise ValueError("CRC mismatch")
            # Extract header: slave addr, function code, byte count.
            slave, func, byte_count = response[0:3]
            # Validate header matches what we sent/expected.
            if slave != slave_addr or func != 3 or byte_count != num_channels * 2:
                # If function code has error bit set (0x80), it's a Modbus exception.
                if func & 0x80:
                    return f"Error: Modbus exception code {response[2]} for slave {slave_addr}"
                # Otherwise, invalid response.
                return f"Error: Invalid response header for slave {slave_addr}."
            # Extract the raw data bytes (2 bytes per channel, big-endian signed int).
            data = response[3:3 + byte_count]
            # Convert raw values to temperatures: Each pair of bytes to signed int, divide by scaling_factor.
            raw_temperatures = []
            for i in range(0, len(data), 2):
                val = int.from_bytes(data[i:i+2], 'big', signed=True) / scaling_factor
                raw_temperatures.append(val)
            # Log success.
            logging.info(f"Temp read successful for slave {slave_addr}.")
            # Return the list of temperatures.
            return raw_temperatures
        # Handle network errors (e.g., connection refused, timeout).
        except socket.error as e:
            # Log warning for this attempt.
            logging.warning(f"Temp read attempt {attempt+1} for slave {slave_addr} failed: {str(e)}. Checking network connectivity.")
            # Sleep 3 seconds for network recovery.
            time.sleep(3)
            # Test network connectivity.
            if test_modbus_connectivity(ip, modbus_port):
                # Network is up, so it's a legitimate device error—use original retry logic.
                logging.warning(f"Network is up, treating as device error for slave {slave_addr}.")
                if attempt < max_retries - 1:
                    time.sleep(retry_backoff_base ** attempt)
                else:
                    logging.error(f"Temp read for slave {slave_addr} failed after {max_retries} attempts - {str(e)}.")
                    return f"Error: Failed after {max_retries} attempts for slave {slave_addr} - {str(e)}."
            else:
                # Network is down, retry up to 3 times.
                network_retry_count += 1
                if network_retry_count < 3:
                    logging.warning(f"Network down, retrying ({network_retry_count}/3) for slave {slave_addr}.")
                    continue  # Retry without backoff for network issues
                else:
                    logging.error(f"Network down after 3 retries for slave {slave_addr} - {str(e)}.")
                    return f"Error: Network unreachable after retries for slave {slave_addr} - {str(e)}."
        # Handle validation errors (e.g., bad response format).
        except ValueError as e:
            logging.warning(f"Temp read attempt {attempt+1} for slave {slave_addr} failed (validation): {str(e)}. Checking network connectivity.")
            # Sleep 3 seconds for network recovery.
            time.sleep(3)
            # Test network connectivity.
            if test_modbus_connectivity(ip, modbus_port):
                # Network is up, so it's a legitimate device error—use original retry logic.
                logging.warning(f"Network is up, treating as device error for slave {slave_addr}.")
                if attempt < max_retries - 1:
                    time.sleep(retry_backoff_base ** attempt)
                else:
                    logging.error(f"Temp read for slave {slave_addr} failed after {max_retries} attempts - {str(e)}.")
                    return f"Error: Failed after {max_retries} attempts for slave {slave_addr} - {str(e)}."
            else:
                # Network is down, retry up to 3 times.
                network_retry_count += 1
                if network_retry_count < 3:
                    logging.warning(f"Network down, retrying ({network_retry_count}/3) for slave {slave_addr}.")
                    continue  # Retry without backoff for network issues
                else:
                    logging.error(f"Network down after 3 retries for slave {slave_addr} - {str(e)}.")
                    return f"Error: Network unreachable after retries for slave {slave_addr} - {str(e)}."
        # Catch any unexpected errors.
        except Exception as e:
            logging.error(f"Unexpected error in temp read attempt {attempt+1} for slave {slave_addr}: {str(e)}\n{traceback.format_exc()}")
            return f"Error: Unexpected failure for slave {slave_addr} - {str(e)}"

    # If loop exits without returning (e.g., due to continue on last attempt), return error
    return f"Error: All retries exhausted for slave {slave_addr}"

def load_config(data_dir):
    """
    Load and parse the configuration from the 'battery_monitor.ini' file.
    This function reads the settings file (like a customizable recipe) and extracts all parameters into a dictionary.
    It provides defaults for missing values to ensure the script always has something to use. Sections like [Temp], [General]
    organize settings (e.g., sensor IPs, thresholds). It also computes derived values like total channels and loads/saves
    calibration offsets. For non-programmers: Think of it as reading a form filled with your preferences and filling in blanks
    with safe defaults if something's missing. Validates and logs the loaded config.
    
    Args:
        data_dir (str): Directory path where the INI file and data files (like offsets.txt) are located.
    
    Returns:
        dict: A comprehensive dictionary with all settings, including computed values like total_channels and relay_mapping.
    """
    # Log the start of config loading.
    logging.info("Loading configuration from 'battery_monitor.ini'.")
    # Global: Reset alert states dictionary.
    global alert_states
    # Check if config has been read; if empty sections, file is missing or invalid.
    if not config_parser.sections():
        logging.error("Config file 'battery_monitor.ini' not found or empty.")
        raise FileNotFoundError("Config file 'battery_monitor.ini' not found.")
    # Temperature settings section: Extract with fallbacks (defaults if key missing).
    temp_settings = {
        'ip': config_parser.get('Temp', 'ip', fallback='192.168.15.240'),  # Device IP address.
        'modbus_port': config_parser.getint('Temp', 'modbus_port', fallback=10001),  # Modbus TCP port.
        'poll_interval': config_parser.getfloat('Temp', 'poll_interval', fallback=10.0),  # Seconds between checks.
        'rise_threshold': config_parser.getfloat('Temp', 'rise_threshold', fallback=2.0),  # Max temp rise per poll.
        'deviation_threshold': config_parser.getfloat('Temp', 'deviation_threshold', fallback=0.1),  # Relative deviation %.
        'disconnection_lag_threshold': config_parser.getfloat('Temp', 'disconnection_lag_threshold', fallback=0.5),  # Lag from group change.
        'high_threshold': config_parser.getfloat('Temp', 'high_threshold', fallback=42.0),  # Max safe temp °C.
        'low_threshold': config_parser.getfloat('Temp', 'low_threshold', fallback=0.0),  # Min safe temp °C.
        'scaling_factor': config_parser.getfloat('Temp', 'scaling_factor', fallback=100.0),  # Raw to °C conversion.
        'valid_min': config_parser.getfloat('Temp', 'valid_min', fallback=0.0),  # Minimum valid reading (below = disconnected).
        'max_retries': config_parser.getint('Temp', 'max_retries', fallback=3),  # Read retries.
        'retry_backoff_base': config_parser.getint('Temp', 'retry_backoff_base', fallback=1),  # Backoff multiplier.
        'query_delay': config_parser.getfloat('Temp', 'query_delay', fallback=0.25),  # Modbus response wait.
        'abs_deviation_threshold': config_parser.getfloat('Temp', 'abs_deviation_threshold', fallback=2.0),  # Absolute deviation °C.
        'cabinet_over_temp_threshold': config_parser.getfloat('Temp', 'cabinet_over_temp_threshold', fallback=35.0),  # Fan trigger temp.
        'number_of_parallel_batteries': config_parser.getint('Temp', 'number_of_parallel_batteries', fallback=1),  # Number of parallel packs.
        'modbus_slave_addresses': [int(x.strip()) for x in config_parser.get('Temp', 'modbus_slave_addresses', fallback='1').split(',')],  # List of slave IDs.
        'sensors_per_bank': config_parser.getint('Temp', 'sensors_per_bank', fallback=8), # New: sensors per bank per battery.
        'num_series_banks': config_parser.getint('General', 'num_series_banks', fallback=3) # New: number of series banks.
    }
    # Validate num_series_banks: Ensure it's reasonable (1-20).
    if temp_settings['num_series_banks'] < 1:
        logging.warning(f"num_series_banks={temp_settings['num_series_banks']} invalid. Setting to 1.")
        temp_settings['num_series_banks'] = 1
    elif temp_settings['num_series_banks'] > 20:
        logging.warning(f"num_series_banks={temp_settings['num_series_banks']} very high. Ensure hardware supports this.")
    # Compute derived: Sensors per battery = series banks * sensors per bank.
    temp_settings['sensors_per_battery'] = temp_settings['num_series_banks'] * temp_settings['sensors_per_bank'] # Calc per battery.
    # Total sensors across all parallel batteries.
    temp_settings['total_channels'] = temp_settings['number_of_parallel_batteries'] * temp_settings['sensors_per_battery'] # Total sensors.
    # Load existing calibration median and offsets from file.
    startup_median, startup_offsets = load_offsets(temp_settings['total_channels'], data_dir)
    # Voltage and general settings from [General] section.
    voltage_settings = {
        'VoltageDifferenceToBalance': config_parser.getfloat('General', 'VoltageDifferenceToBalance', fallback=0.1),  # Min diff to trigger balance V.
        'BalanceDurationSeconds': config_parser.getint('General', 'BalanceDurationSeconds', fallback=5),  # How long to balance s.
        'SleepTimeBetweenChecks': config_parser.getfloat('General', 'SleepTimeBetweenChecks', fallback=0.1),  # Delay between voltage reads.
        'BalanceRestPeriodSeconds': config_parser.getint('General', 'BalanceRestPeriodSeconds', fallback=60),  # Cooldown after balance s.
        'LowVoltageThresholdPerBattery': config_parser.getfloat('General', 'LowVoltageThresholdPerBattery', fallback=18.5),  # Low V alert per bank.
        'HighVoltageThresholdPerBattery': config_parser.getfloat('General', 'HighVoltageThresholdPerBattery', fallback=21.0),  # High V alert per bank.
        'EmailAlertIntervalSeconds': config_parser.getint('General', 'EmailAlertIntervalSeconds', fallback=3600),  # Min time between emails s.
        'I2C_BusNumber': config_parser.getint('General', 'I2C_BusNumber', fallback=1),  # I2C bus ID on Pi.
        'VoltageDividerRatio': config_parser.getfloat('General', 'VoltageDividerRatio', fallback=0.01592),  # ADC voltage scaling.
        'LoggingLevel': config_parser.get('General', 'LoggingLevel', fallback='INFO')  # Log verbosity (INFO, DEBUG, etc.).
    }
    # Boolean flags for features.
    general_flags = {
        'WebInterfaceEnabled': config_parser.getboolean('General', 'WebInterfaceEnabled', fallback=True),  # Enable web dashboard.
        'StartupSelfTestEnabled': config_parser.getboolean('General', 'StartupSelfTestEnabled', fallback=True),  # Run startup checks.
        'WatchdogEnabled': config_parser.getboolean('General', 'WatchdogEnabled', fallback=True),  # Use hardware watchdog.
        'EventLogSize': config_parser.getint('General', 'EventLogSize', fallback=20)  # Max events to keep in memory.
    }
    # I2C device addresses (hex).
    i2c_settings = {
        'MultiplexerAddress': int(config_parser.get('I2C', 'MultiplexerAddress', fallback='0x70'), 16),  # TCA9548A mux addr.
        'VoltageMeterAddress': int(config_parser.get('I2C', 'VoltageMeterAddress', fallback='0x49'), 16),  # ADS1115 ADC addr.
    }
    # GPIO pin assignments.
    gpio_settings = {
        'DC_DC_RelayPin': config_parser.getint('GPIO', 'DC_DC_RelayPin', fallback=5),  # Pin for DC-DC converter.
        'AlarmRelayPin': config_parser.getint('GPIO', 'AlarmRelayPin', fallback=6),  # Pin for alarm buzzer/light.
        'FanRelayPin': config_parser.getint('GPIO', 'FanRelayPin', fallback=4)  # Pin for cooling fan.
    }
    # Email SMTP settings.
    email_settings = {
        'SMTP_Server': config_parser.get('Email', 'SMTP_Server', fallback='smtp.gmail.com'),  # Mail server.
        'SMTP_Port': config_parser.getint('Email', 'SMTP_Port', fallback=587),  # Port (587 for TLS).
        'SenderEmail': config_parser.get('Email', 'SenderEmail', fallback='your_email@gmail.com'),  # From address.
        'RecipientEmail': config_parser.get('Email', 'RecipientEmail', fallback='recipient@example.com'),  # To address.
        'SMTP_Username': config_parser.get('Email', 'SMTP_Username', fallback='your_email@gmail.com'),  # Login user.
        'SMTP_Password': config_parser.get('Email', 'SMTP_Password', fallback='your_app_password')  # App password.
    }
    # ADC configuration registers (hex values).
    adc_settings = {
        'ConfigRegister': int(config_parser.get('ADC', 'ConfigRegister', fallback='0x01'), 16),  # Config reg addr.
        'ConversionRegister': int(config_parser.get('ADC', 'ConversionRegister', fallback='0x00'), 16),  # Conversion reg addr.
        'ContinuousModeConfig': int(config_parser.get('ADC', 'ContinuousModeConfig', fallback='0x0100'), 16),  # Mode bits.
        'SampleRateConfig': int(config_parser.get('ADC', 'SampleRateConfig', fallback='0x0080'), 16),  # Rate bits.
        'GainConfig': int(config_parser.get('ADC', 'GainConfig', fallback='0x0400'), 16)  # Gain bits.
    }
    # Per-bank calibration factors (multipliers for voltage accuracy).
    calibration_settings = {}
    for i in range(1, temp_settings['num_series_banks'] + 1):
        key = f'Sensor{i}_Calibration'
        calibration_settings[key] = config_parser.getfloat('Calibration', key, fallback=1.0)
    # Startup test parameters.
    startup_settings = {
        'test_balance_duration': config_parser.getint('Startup', 'test_balance_duration', fallback=15),  # Test balance time s.
        'min_voltage_delta': config_parser.getfloat('Startup', 'min_voltage_delta', fallback=0.01),  # Min change to verify V.
        'test_read_interval': config_parser.getfloat('Startup', 'test_read_interval', fallback=2.0)  # Read freq during test s.
    }
    # Web server settings.
    web_settings = {
        'host': config_parser.get('Web', 'host', fallback='0.0.0.0'),  # Bind address (0.0.0.0 = all interfaces).
        'web_port': config_parser.getint('Web', 'web_port', fallback=8080),  # Port for web access.
        'auth_required': config_parser.getboolean('Web', 'auth_required', fallback=False),  # Enable login.
        'username': config_parser.get('Web', 'username', fallback='admin'),  # Web login user.
        'password': config_parser.get('Web', 'password', fallback='admin123'),  # Web login pass.
        'api_enabled': config_parser.getboolean('Web', 'api_enabled', fallback=True),  # Enable API endpoints.
        'cors_enabled': config_parser.getboolean('Web', 'cors_enabled', fallback=False),  # Enable CORS for web.
        'cors_origins': config_parser.get('Web', 'cors_origins', fallback='*')  # Allowed origins.
    }
    # Relay mapping for balancing pairs (e.g., bank1-bank2 uses certain relay bits).
    relay_mapping = {}
    if config_parser.has_section('RelayMapping'):
        # Parse each key-value in section: Key like '1-2' maps to list of relay numbers.
        for key in config_parser['RelayMapping']:
            try:
                relays = [int(x.strip()) for x in config_parser['RelayMapping'][key].split(',')]  # Split comma-separated ints.
                relay_mapping[key] = relays
            except ValueError:
                logging.warning(f"Invalid relay mapping for {key}: {config_parser['RelayMapping'][key]}")  # Log bad format.
    # Set global logging level based on config (e.g., INFO shows normal events, DEBUG shows everything).
    log_level = getattr(logging, voltage_settings['LoggingLevel'].upper(), logging.INFO)
    logging.getLogger().setLevel(log_level)
    # Initialize alert states for each channel: Track last alert type and count to avoid spam.
    alert_states = {ch: {'last_type': None, 'count': 0} for ch in range(1, temp_settings['total_channels'] + 1)}
    # Log success.
    logging.info("Configuration loaded successfully.")
    # Combine all settings into one big dictionary.
    relay_pins = {
        f'Relay{i}_Pin': config_parser.getint('GPIO', f'Relay{i}_Pin', fallback=[17,18,27,22][i]) for i in range(4)
    }
    return {**temp_settings, **voltage_settings, **general_flags, **i2c_settings,
            **gpio_settings, **email_settings, **adc_settings, **calibration_settings,
            **startup_settings, **web_settings, 'relay_mapping': relay_mapping, **relay_pins}

def validate_config(settings):
    """
    Validate configuration settings for consistency and required values.
    This function double-checks the loaded settings for sanity: Ensures numbers are positive, counts match (e.g., slave addresses = parallel batteries),
    and required mappings exist (e.g., relays for every bank pair). If issues found, raises an error to stop the script early.
    Non-programmer analogy: Like proofreading a form for typos or missing info before submitting—catches problems before they cause failures later.
    
    Args:
        settings (dict): The loaded configuration dictionary to validate.
    
    Raises:
        ValueError: If any validation fails, with a message listing all errors.
    
    Returns:
        None
    """
    # List to collect error messages.
    errors = []
   
    # Check num_series_banks is at least 1 (can't have 0 banks).
    if settings['num_series_banks'] < 1:
        errors.append("num_series_banks must be at least 1.")
    # Warn if too many banks (hardware limit).
    if settings['num_series_banks'] > 20:
        errors.append("num_series_banks > 20 may cause issues.")
   
    # Sensors per bank must be positive.
    if settings['sensors_per_bank'] < 1:
        errors.append("sensors_per_bank must be at least 1.")
   
    # Parallel batteries must be at least 1.
    if settings['number_of_parallel_batteries'] < 1:
        errors.append("number_of_parallel_batteries must be at least 1.")
   
    # Number of slave addresses must match parallel batteries (one slave per battery).
    if len(settings['modbus_slave_addresses']) != settings['number_of_parallel_batteries']:
        errors.append("modbus_slave_addresses count must match number_of_parallel_batteries.")
   
    # For relay mapping, ensure every possible pair (high-low) has a mapping.
    if settings.get('relay_mapping'):
        expected_pairs = []
        for i in range(1, settings['num_series_banks'] + 1):
            for j in range(1, settings['num_series_banks'] + 1):
                if i != j:  # No self-balancing.
                    expected_pairs.append(f"{i}-{j}")
        for pair in expected_pairs:
            if pair not in settings['relay_mapping']:
                errors.append(f"Relay mapping missing for {pair}.")
   
    # If errors found, log them and raise exception with combined message.
    if errors:
        msg = "Configuration errors: " + "; ".join(errors)
        logging.error(msg)
        raise ValueError(msg)
   
    # All good—log success.
    logging.info("Configuration validation passed.")

def detect_hardware(settings):
    """
    Detect and log hardware connectivity at startup.
    This function pings the connected devices to see if they're responding, like knocking on doors to check if rooms are accessible.
    It tests I2C devices (voltage meter, relays, multiplexer) by trying to read a byte, and Modbus slaves by a simple query.
    Logs OK or warnings for each—helps diagnose wiring/network issues early. Non-programmer: Like a system scan in your computer
    to see if peripherals (printer, mouse) are plugged in right.
    
    Args:
        settings (dict): Configuration with addresses, IPs, etc., for testing.
    
    Returns:
        None: Just logs results.
    """
    # Log start of detection.
    logging.info("Detecting hardware connectivity.")
    # If I2C bus is available, test each device.
    if bus:
        try:
            # Select channel 0 on multiplexer (default) and read from it.
            choose_channel(0, settings['MultiplexerAddress'])
            logging.info("I2C multiplexer detected.")  # Success.
        except IOError as e:
            logging.warning(f"I2C multiplexer not accessible: {e}")  # Failure log.
       
        try:
            # Try reading a byte from voltage meter address.
            bus.read_byte(settings['VoltageMeterAddress'])
            logging.info("I2C voltage meter detected.")
        except IOError as e:
            logging.warning(f"I2C voltage meter not accessible: {e}")
    else:
        # No I2C—skip, likely test mode.
        logging.warning("I2C bus not available - hardware detection skipped.")
   
    # Test each Modbus slave individually.
    # Test Modbus slaves
    for addr in settings['modbus_slave_addresses']:
        try:
            # Try a minimal read (1 channel) to test connectivity.
            test_result = read_ntc_sensors(settings['ip'], settings['modbus_port'], settings['query_delay'], 1, settings['scaling_factor'], 1, 1, slave_addr=addr)
            if isinstance(test_result, str):
                # If error string, log warning.
                logging.warning(f"Modbus slave {addr} not accessible: {test_result}")
            else:
                # List of values means success.
                logging.info(f"Modbus slave {addr} detected.")
        except Exception as e:
            # Catch any unexpected issues.
            logging.warning(f"Modbus slave {addr} detection failed: {e}")
   
    # Log completion.
    logging.info("Hardware detection complete.")

def setup_hardware(settings):
    """
    Prepare the hardware connections for monitoring and controlling the batteries.
    This function sets up the communication channels to the physical devices:
    - I2C bus for talking to voltage sensors and relays (like a data highway)
    - GPIO pins for controlling switches and alarms (like light switches)
    - Time-series database for storing historical data
    If hardware libraries aren't available, it switches to "test mode" where
    everything works but uses fake data instead of real sensors. It also creates or validates the RRD database for logging trends.
    Non-programmer analogy: Like plugging in all cables, turning on switches, and setting up a logbook before starting work.
    
    Args:
        settings (dict): Configuration with bus numbers, pins, addresses, etc.
    
    Returns:
        None
    """
    # Global: Set up I2C bus.
    global bus
    # Log start.
    logging.info("Setting up hardware connections.")
    # Set up I2C communication (for voltage sensors and relays)
    if smbus:
        # Create SMBus object for the specified I2C bus number (usually 1 on Pi).
        bus = smbus.SMBus(settings['I2C_BusNumber']) # Create connection to I2C bus
    else:
        # No library—test mode with simulated data.
        logging.warning("I2C library not available - running in test mode with fake data")
        bus = None
    # Set up GPIO pins (for controlling relays and alarms)
    if GPIO:
        # Use BCM pin numbering (GPIO numbers, not physical pins).
        GPIO.setmode(GPIO.BCM) # Use Broadcom pin numbering
        # Set DC-DC relay pin as output, start low (off).
        GPIO.setup(settings['DC_DC_RelayPin'], GPIO.OUT, initial=GPIO.LOW) # DC-DC converter control
        # Alarm pin low (no alarm).
        GPIO.setup(settings['AlarmRelayPin'], GPIO.OUT, initial=GPIO.LOW) # Alarm buzzer/light
        # Fan pin low (off).
        GPIO.setup(settings['FanRelayPin'], GPIO.OUT, initial=GPIO.LOW) # Cooling fan control
        # Set up relay pins
        for i in range(4):
            pin = settings[f'Relay{i}_Pin']
            GPIO.setup(pin, GPIO.OUT, initial=GPIO.LOW)
            logging.info(f"Relay {i} GPIO pin {pin} set up.")
    else:
        logging.warning("GPIO library not available - running in test mode")
    # Nested function to create RRD database if needed.
    def create_rrd():
        # Build list of data sources (DS): One for median temp, one per bank voltage.
        ds_list = ['DS:medtemp:GAUGE:120:-20:100']  # Median temp: Gauge type (current value), 120s heartbeat, range -20 to 100°C.
        for i in range(1, settings['num_series_banks'] + 1):
            ds_list.append(f'DS:volt{i}:GAUGE:120:0:25')  # Voltage per bank: 0-25V range.
        # Run rrdtool create command: File, step 60s, DS list, Round-Robin Archives (RRA) for storage.
        # RRA: LAST consolidation, 0% XFF (no nulls tolerated), step 1 for 1440 points (~1 day), step 5 for 288 points (longer term).
        subprocess.check_call(['rrdtool', 'create', RRD_FILE,
                               '--step', '60'] + ds_list +
                               ['RRA:LAST:0.0:1:1440',
                                'RRA:LAST:0.0:5:288'])
        logging.info("Created RRD database for time-series logging.")
    # Try to set up RRD: Create if missing, or validate existing.
    try:
        if not os.path.exists(RRD_FILE):
            # No file—create new.
            create_rrd()
        else:
            # File exists—check schema with rrdtool info.
            try:
                output = subprocess.check_output(['rrdtool', 'info', RRD_FILE])
                # Count DS lines in output.
                ds_count = len([line for line in output.decode().split('\n') if line.startswith('ds[')])
                # Expected: 1 medtemp + num banks.
                expected_ds = 1 + settings['num_series_banks']
                if ds_count != expected_ds:
                    # Mismatch (e.g., config changed)—recreate.
                    logging.warning(f"RRD database schema mismatch: {ds_count} DS vs expected {expected_ds}. Recreating.")
                    os.remove(RRD_FILE)
                    create_rrd()
                else:
                    logging.info("Using existing RRD database with matching schema.")
            except subprocess.CalledProcessError as e:
                # Info command failed—recreate.
                logging.error(f"RRD info failed: {e}. Recreating database.")
                os.remove(RRD_FILE)
                create_rrd()
    except subprocess.CalledProcessError as e:
        logging.error(f"RRD creation failed: {e}")
    except FileNotFoundError:
        logging.error("rrdtool not found. Please install rrdtool (sudo apt install rrdtool).")
    except OSError as e:
        logging.error(f"RRD file operation failed: {e}")
    # Log completion.
    logging.info("Hardware setup complete, including RRD initialization.")
    # Run detection after setup.
    detect_hardware(settings)

def signal_handler(sig, frame):
    """
    Handle shutdown signals gracefully (e.g., Ctrl+C).
    This function is the "emergency exit" handler. When the user presses Ctrl+C (SIGINT) or another signal,
    it cleans up resources: Stops web server, resets GPIO pins, disables watchdog, and exits cleanly.
    Prevents hardware from being left in unsafe states (e.g., relays on). Non-programmer: Like turning off lights and locking doors
    before leaving a room, instead of just walking out.
    
    Args:
        sig (int): The signal number (e.g., signal.SIGINT for Ctrl+C).
        frame: The current stack frame (not used here).
    
    Returns:
        None: Just performs cleanup and exits.
    """
    # Log the shutdown reason.
    logging.info("Script stopped by user or signal.")
    # Global: Stop web server if running.
    global web_server
    if web_server:
        web_server.shutdown()  # Gracefully shut down Flask server.
    # Clean up GPIO: Reset all pins to default (input/low).
    if GPIO:
        GPIO.cleanup()
    # Disable watchdog to prevent accidental reset during shutdown.
    close_watchdog()
    # Exit with success code 0.
    sys.exit(0)

def load_offsets(num_channels, data_dir):
    """
    Load temperature calibration offsets from 'offsets.txt'.
    Offsets adjust sensor readings so all sensors agree on the same temperature (calibration).
    File format: First line = startup median, next lines = offsets per channel.
    If file missing/corrupt/wrong size, returns None to trigger recalibration.
    Non-programmer: Like loading saved eyeglass prescription adjustments for each eye's lens.
    
    Args:
        num_channels (int): Total number of sensors (for validation).
        data_dir (str): Directory where offsets.txt is stored.
    
    Returns:
        tuple: (startup_median float or None, list of offsets or None)
    """
    # Build file path.
    offsets_path = os.path.join(data_dir, 'offsets.txt')
    # Log attempt.
    logging.info(f"Loading startup offsets from '{offsets_path}'.")
    # Check if file exists.
    if os.path.exists(offsets_path):
        try:
            # Read all lines from file.
            with open(offsets_path, 'r') as f:
                lines = f.readlines()
            # Must have at least median line.
            if len(lines) < 1:
                logging.warning("Invalid offsets.txt; using none.")
                return None, None
            # Parse median (first line).
            startup_median = float(lines[0].strip())
            # Parse offsets (rest of lines).
            offsets = [float(line.strip()) for line in lines[1:]]
            # Validate count matches channels.
            if len(offsets) != num_channels:
                logging.warning(f"Invalid offsets count; expected {num_channels}, got {len(offsets)}. Using none.")
                return None, None
            # Log loaded values (debug level).
            logging.debug(f"Loaded median {startup_median} and {len(offsets)} offsets.")
            return startup_median, offsets
        except (ValueError, IndexError):
            # Parse errors (bad numbers).
            logging.warning("Corrupt offsets.txt; using none.")
            return None, None
    # No file—log and return None.
    logging.warning("No 'offsets.txt' found; using none.")
    return None, None

def save_offsets(startup_median, startup_offsets, data_dir):
    """
    Save temperature calibration offsets to 'offsets.txt'.
    Writes the median and list of offsets to file for persistence across restarts.
    Only called when new calibration is computed (all sensors valid on first run).
    Non-programmer: Like saving your custom settings to a file so next time you open the app, they're remembered.
    
    Args:
        startup_median (float): The overall median temperature at calibration time.
        startup_offsets (list): List of offset values per channel.
        data_dir (str): Directory to save the file in.
    
    Returns:
        None
    """
    # Build path.
    offsets_path = os.path.join(data_dir, 'offsets.txt')
    # Log attempt.
    logging.info(f"Saving startup offsets to '{offsets_path}'.")
    try:
        # Open file for writing (overwrites existing).
        with open(offsets_path, 'w') as f:
            # Write median first.
            f.write(f"{startup_median}\n")
            # Write each offset on a line.
            for offset in startup_offsets:
                f.write(f"{offset}\n")
        # Log success.
        logging.debug("Offsets saved.")
    except IOError as e:
        # File write error (e.g., permissions).
        logging.error(f"Failed to save offsets: {e}")

def check_invalid_reading(raw, ch, alerts, valid_min, settings):
    """
    Check if a raw temperature reading is invalid (disconnected sensor).
    If reading <= valid_min (e.g., 0°C), it's likely a disconnected or failed sensor.
    Adds alert message with battery/bank details and logs warning. Non-programmer: Like checking if a thermometer shows
    an impossible value (e.g., -100°C) and flagging it as broken.
    
    Args:
        raw (float): Raw temperature value from sensor.
        ch (int): Global channel number (1-based).
        alerts (list): List to append alert strings to.
        valid_min (float): Minimum valid temperature threshold.
        settings (dict): Config for event logging size.
    
    Returns:
        bool: True if invalid (alert added), False otherwise.
    """
    # Check if raw is invalid.
    if raw <= valid_min:
        # Get bank and battery/local details for descriptive alert.
        bank = get_bank_for_channel(ch)
        bat_id, local_ch = get_battery_and_local_ch(ch)
        # Build alert message with details.
        alert = f"Battery {bat_id} Bank {bank} Local Ch {local_ch}: Invalid reading (≤ {valid_min})."
        # Add to alerts list.
        alerts.append(alert)
        # Add to event log with timestamp.
        event_log.append(f"{time.strftime('%Y-%m-%d %H:%M:%S')}: {alert}")
        # Trim log if too long.
        if len(event_log) > settings.get('EventLogSize', 20):
            event_log.pop(0)
        # Log warning.
        logging.warning(f"Invalid reading on Battery {bat_id} Bank {bank} Local Ch {local_ch}: {raw} ≤ {valid_min}.")
        return True  # Invalid.
    return False  # Valid.

def check_high_temp(calibrated, ch, alerts, high_threshold, settings):
    """
    Check if calibrated temperature exceeds high threshold.
    If temp > high_threshold (e.g., 42°C), it's overheating—add alert and log.
    Non-programmer: Like a fire alarm going off if room gets too hot.
    
    Args:
        calibrated (float): Adjusted temperature value.
        ch (int): Channel number.
        alerts (list): List for alert messages.
        high_threshold (float): Max safe temperature.
        settings (dict): For event log size.
    
    Returns:
        None
    """
    # Check condition.
    if calibrated > high_threshold:
        # Get details.
        bank = get_bank_for_channel(ch)
        bat_id, local_ch = get_battery_and_local_ch(ch)
        # Alert with value.
        alert = f"Battery {bat_id} Bank {bank} Local Ch {local_ch}: High temp ({calibrated:.1f}°C > {high_threshold}°C)."
        alerts.append(alert)
        event_log.append(f"{time.strftime('%Y-%m-%d %H:%M:%S')}: {alert}")
        if len(event_log) > settings.get('EventLogSize', 20):
            event_log.pop(0)
        logging.warning(f"High temp alert on Battery {bat_id} Bank {bank} Local Ch {local_ch}: {calibrated:.1f} > {high_threshold}.")

def check_low_temp(calibrated, ch, alerts, low_threshold, settings):
    """
    Check if calibrated temperature is below low threshold.
    If temp < low_threshold (e.g., 0°C), it's too cold—add alert and log.
    Non-programmer: Like a frost warning if temperature drops too low.
    
    Args:
        calibrated (float): Adjusted temperature.
        ch (int): Channel.
        alerts (list): Alert list.
        low_threshold (float): Min safe temperature.
        settings (dict): Event log size.
    
    Returns:
        None
    """
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
    """
    Check if sensor temperature deviates too much from its bank's median.
    Deviation can be absolute (e.g., >2°C diff) or relative (e.g., >10% diff)—flags faulty sensor.
    Non-programmer: Like spotting one person in a group who's way off the average height—might be measurement error.
    
    Args:
        calibrated (float): Sensor temp.
        bank_median (float): Median of bank's sensors.
        ch (int): Channel.
        alerts (list): Alert list.
        abs_deviation_threshold (float): Absolute diff threshold °C.
        deviation_threshold (float): Relative diff threshold (fraction).
        settings (dict): Event log.
    
    Returns:
        None
    """
    # Calculate absolute deviation.
    abs_dev = abs(calibrated - bank_median)
    # Relative: abs_dev / |median|, avoid divide by zero.
    rel_dev = abs_dev / abs(bank_median) if bank_median != 0 else 0
    # Check either threshold exceeded.
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
    """
    Check for abnormal temperature rise since last poll.
    If increase > rise_threshold (e.g., 2°C in 10s), it might indicate a problem like short circuit.
    Only checks if previous reading exists. Non-programmer: Like detecting sudden fever spike—needs attention.
    
    Args:
        current (float): Current temp.
        previous_temps (list): List of previous temps.
        ch (int): Channel.
        alerts (list): Alerts.
        poll_interval (float): Time since last read s.
        rise_threshold (float): Max allowed rise °C.
        settings (dict): Event log.
    
    Returns:
        None
    """
    # Get previous for this channel (0-based index).
    previous = previous_temps[ch-1]
    # Only if previous exists.
    if previous is not None:
        # Type check for safety (avoid comparing wrong types).
        if not isinstance(current, (int, float)) or not isinstance(previous, (int, float)):
            logging.warning(f"Type error in check_abnormal_rise for ch {ch}: current={type(current)} {current}, previous={type(previous)} {previous}")
            return
        # Calculate rise.
        rise = current - previous
        # Check threshold.
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
    """
    Check if sensor's change lags behind the bank's median change (possible disconnection).
    If diff in changes > threshold, sensor isn't tracking group—might be loose wire.
    Non-programmer: Like one runner in a team falling behind while others keep pace—straggler alert.
    
    Args:
        current (float): Current temp.
        previous_temps (list): Previous temps.
        bank_median_rise (float): Bank's median change.
        ch (int): Channel.
        alerts (list): Alerts.
        disconnection_lag_threshold (float): Max lag °C.
        settings (dict): Event log.
    
    Returns:
        None
    """
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
    """
    Check for sudden sensor disconnection (was valid, now invalid).
    If previous was good but current is None/invalid, alert. Non-programmer: Like a light that was on suddenly going out—check the bulb.
    
    Args:
        current: Current temp (or None).
        previous_temps (list): Previous.
        ch (int): Channel.
        alerts (list): Alerts.
        settings (dict): Event log.
    
    Returns:
        None
    """
    previous = previous_temps[ch-1]
    # Type safety.
    if not isinstance(previous, (int, float, type(None))) or not isinstance(current, (int, float, type(None))):
        logging.warning(f"Type error in check_sudden_disconnection for ch {ch}: current={type(current)} {current}, previous={type(previous)} {previous}")
        return
    # Check transition to invalid.
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
    """
    Switch to a specific I2C channel using the TCA9548A multiplexer.
    The multiplexer allows accessing multiple I2C devices on different channels (like a switchboard).
    Writes a byte to the mux address with bit set for the channel (e.g., channel 0 = 0x01).
    Non-programmer: Like selecting which outlet to plug into on a power strip with switches.
    
    Args:
        channel (int): Channel number (0-7 typically).
        multiplexer_address (int): I2C address of the mux (e.g., 0x70).
    
    Returns:
        None
    """
    # Log for debug.
    logging.debug(f"Switching to I2C channel {channel}.")
    if bus:
        try:
            # Write byte: 1 shifted left by channel number (bitmask).
            bus.write_byte(multiplexer_address, 1 << channel)
        except IOError as e:
            logging.error(f"I2C error selecting channel {channel}: {str(e)}")

def setup_voltage_meter(settings):
    """
    Configure the ADS1115 ADC for voltage measurement.
    Sets continuous mode, sample rate, and gain via config register.
    Non-programmer: Like setting dials on a voltmeter for accurate reading (range, speed).
    
    Args:
        settings (dict): ADC config values.
    
    Returns:
        None
    """
    # Log.
    logging.debug("Configuring voltage meter ADC.")
    if bus:
        try:
            # Combine config bits: Continuous mode | sample rate | gain.
            config_value = (settings['ContinuousModeConfig'] |
                            settings['SampleRateConfig'] |
                            settings['GainConfig'])
            # Write to config register.
            bus.write_word_data(settings['VoltageMeterAddress'], settings['ConfigRegister'], config_value)
        except IOError as e:
            logging.error(f"I2C error configuring voltage meter: {str(e)}")

def read_voltage_with_retry(bank_id, settings):
    """
    Read voltage from a specific bank with retries and averaging.
    Selects I2C channel for the bank, configures ADC, reads raw ADC value twice, averages valid readings (filters outliers >5% diff).
    Converts raw to voltage using formula and calibration. Retries whole process up to 2 times on failure.
    Updates alive_timestamp during reads for watchdog. Non-programmer: Like measuring battery level with a multimeter,
    taking multiple samples and averaging to be sure.
    
    Args:
        bank_id (int): Bank number (1 to num_series_banks).
        settings (dict): Config for calibration, ratios, etc.
    
    Returns:
        tuple: (average_voltage float or None, list of valid readings, list of valid raw ADC)
    """
    # Global: Update timestamp.
    global alive_timestamp
    # Log start.
    logging.info(f"Starting voltage read for Bank {bank_id}.")
    # Validate bank_id.
    if bank_id > settings['num_series_banks']:
        logging.warning(f"Bank {bank_id} exceeds configured num_series_banks ({settings['num_series_banks']}). Cannot read voltage.")
        return None, [], []
    # Get scaling and calibration.
    voltage_divider_ratio = settings['VoltageDividerRatio']
    sensor_id = bank_id
    calibration_factor = settings[f'Sensor{sensor_id}_Calibration']
    # Retry up to 2 times.
    for attempt in range(2):
        # Update timestamp.
        alive_timestamp = time.time()
        logging.debug(f"Voltage read attempt {attempt+1} for Bank {bank_id}.")
        # Lists for readings.
        readings = []
        raw_values = []
        # Take 2 samples.
        for _ in range(2):
            # Update timestamp.
            alive_timestamp = time.time()
            # Channel = bank-1 (0-based).
            meter_channel = bank_id - 1 # Direct mapping: Bank 1 = Channel 0, Bank 2 = Channel 1, etc.
            # Select channel on mux.
            choose_channel(meter_channel, settings['MultiplexerAddress'])
            # Configure ADC.
            setup_voltage_meter(settings)
            if bus:
                try:
                    # Start conversion (write 0x01?).
                    bus.write_byte(settings['VoltageMeterAddress'], 0x01)
                    # Short delay for conversion.
                    time.sleep(0.05)
                    # Update timestamp.
                    alive_timestamp = time.time()
                    # Read 16-bit word from conversion reg, swap bytes (big-endian).
                    raw_adc = bus.read_word_data(settings['VoltageMeterAddress'], settings['ConversionRegister'])
                    raw_adc = (raw_adc & 0xFF) << 8 | (raw_adc >> 8)
                except IOError as e:
                    logging.error(f"I2C error in voltage read for Bank {bank_id}: {str(e)}")
                    raw_adc = 0
            else:
                # Test mode: Fake value.
                raw_adc = 16000 + bank_id * 100
            # Log raw.
            logging.debug(f"Raw ADC for Bank {bank_id} (Sensor {sensor_id}): {raw_adc}")
            # Convert if non-zero.
            if raw_adc != 0:
                # ADS1115 formula: Raw * (full scale / 32767), full scale 6.144V for gain.
                measured_voltage = raw_adc * (6.144 / 32767)
                # Apply divider and calibration.
                actual_voltage = (measured_voltage / voltage_divider_ratio) * calibration_factor
                readings.append(actual_voltage)
                raw_values.append(raw_adc)
            else:
                # Zero reading.
                readings.append(0.0)
                raw_values.append(0)
        # If readings, average.
        if readings:
            average = sum(readings) / len(readings)
            # Filter valid: Within 5% of average.
            valid_readings = [r for r in readings if abs(r - average) / (average if average != 0 else 1) <= 0.05]
            valid_adc = [raw_values[i] for i, r in enumerate(readings) if abs(r - average) / (average if average != 0 else 1) <= 0.05]
            if valid_readings:
                # Success—average valids.
                logging.info(f"Voltage read successful for Bank {bank_id}: {average:.2f}V.")
                return sum(valid_readings) / len(valid_readings), valid_readings, valid_adc
        # Inconsistent—retry.
        logging.debug(f"Readings for Bank {bank_id} inconsistent, retrying.")
    # All retries failed.
    logging.error(f"Couldn't get good voltage reading for Bank {bank_id} after 2 tries.")
    return None, [], []

def set_relay_connection(high, low, settings):
    """
    Set relay connections for balancing between high and low banks using GPIO.
    Looks up the relays for the pair in relay_mapping, sets corresponding GPIO pins HIGH to activate.
    For reset (high=0, low=0), sets all relay pins LOW. Assumes active-high relays.
    
    Args:
        high (int): Source bank (higher voltage), or 0 for reset.
        low (int): Destination bank, or 0 for reset.
        settings (dict): Config with relay_mapping and Relay{i}_Pin.
    
    Returns:
        None
    """
    try:
        # Validate banks unless reset.
        if high != 0 and low != 0:
            if high > settings['num_series_banks'] or low > settings['num_series_banks']:
                logging.warning(f"Bank {high} or {low} exceeds configured num_series_banks ({settings['num_series_banks']}). Cannot balance.")
                return
            logging.info(f"Attempting to set GPIO relays for connection from Bank {high} to {low}")
        else:
            logging.info("Resetting all GPIO relays to off")
        
        # Reset: Set all relay pins LOW
        if high == 0 and low == 0:
            for i in range(4):  # Assuming 4 relays
                pin = settings[f'Relay{i}_Pin']
                GPIO.output(pin, GPIO.LOW)
            logging.info("All relays deactivated")
            return
        
        # Key for mapping (e.g., '1-2')
        pair_key = f"{high}-{low}"
        if pair_key in settings.get('relay_mapping', {}):
            relays = settings['relay_mapping'][pair_key]
            logging.debug(f"Activating relays {relays} for {pair_key}")
            # First, deactivate all relays to ensure clean state
            for i in range(4):
                pin = settings[f'Relay{i}_Pin']
                GPIO.output(pin, GPIO.LOW)
            # Activate specific relays
            for relay in relays:
                if 0 <= relay < 4:  # Validate relay index
                    pin = settings[f'Relay{relay}_Pin']
                    GPIO.output(pin, GPIO.HIGH)
                    logging.debug(f"Relay {relay} on pin {pin} activated")
                else:
                    logging.warning(f"Invalid relay index {relay} for {pair_key}")
        else:
            logging.warning(f"No relay mapping found for {pair_key}. Cannot balance.")
            return
        
        logging.info(f"GPIO relay setup completed for balancing from Bank {high} to {low}")
    except Exception as e:
        logging.error(f"Error in set_relay_connection: {e}")

def control_dcdc_converter(turn_on, settings):
    """
    Turn DC-DC converter relay on/off via GPIO.
    The converter transfers power during balancing. Non-programmer: Like turning a pump on/off to move water.
    
    Args:
        turn_on (bool): True to activate, False to deactivate.
        settings (dict): GPIO pin for relay.
    
    Returns:
        None
    """
    try:
        if GPIO:
            # Set pin high (on) or low (off).
            GPIO.output(settings['DC_DC_RelayPin'], GPIO.HIGH if turn_on else GPIO.LOW)
        # Log status.
        logging.info(f"DC-DC Converter is now {'on' if turn_on else 'off'}")
    except Exception as e:
        logging.error(f"Problem controlling DC-DC converter: {e}")

def send_alert_email(message, settings):
    """
    Send an email alert if enough time has passed since last one (throttled).
    Builds MIME message, connects to SMTP, logs in, sends. Non-programmer: Like texting an alert but with spam control.
    
    Args:
        message (str): Alert text body.
        settings (dict): SMTP config.
    
    Returns:
        None
    """
    # Global: Check throttle.
    global last_email_time
    if time.time() - last_email_time < settings['EmailAlertIntervalSeconds']:
        logging.debug("Skipping alert email to avoid flooding.")
        return
    try:
        # Create text message.
        msg = MIMEText(message)
        msg['Subject'] = "Battery Monitor Alert"
        msg['From'] = settings['SenderEmail']
        msg['To'] = settings['RecipientEmail']
        # Connect to SMTP server.
        with smtplib.SMTP(settings['SMTP_Server'], settings['SMTP_Port']) as server:
            # Enable TLS encryption.
            server.starttls()
            # Login if credentials provided.
            if settings['SMTP_Username'] and settings['SMTP_Password']:
                server.login(settings['SMTP_Username'], settings['SMTP_Password'])
            # Send the message.
            server.send_message(msg)
        # Update timer.
        last_email_time = time.time()
        logging.info(f"Alert email sent: {message}")
    except Exception as e:
        logging.error(f"Failed to send alert email: {e}")

def check_for_issues(voltages, temps_alerts, settings):
    """
    Check voltages and combine with temp alerts; activate alarm if needed.
    Scans each bank's voltage for zero, high/low; adds alerts. If any issues or startup/balancer flags,
    turns on alarm relay and sends email (throttled). Non-programmer: Like a central alarm system checking all sensors.
    
    Args:
        voltages (list): List of bank voltages.
        temps_alerts (list): Existing temp alerts.
        settings (dict): Thresholds, GPIO, email.
    
    Returns:
        tuple: (alert_needed bool, list of all alerts)
    """
    # Global flags.
    global startup_failed, startup_alerts, balancer_failed
    # Log start.
    logging.info("Checking for voltage and temp issues.")
    # Initial: Check flags.
    alert_needed = startup_failed or balancer_failed
    # List for all alerts.
    alerts = []
    # Add startup alerts if failed.
    if startup_failed and startup_alerts:
        alerts.append("Startup failures: " + "; ".join(startup_alerts))
    # Add balancer flag alert.
    if balancer_failed:
        alerts.append("Balancer hardware failure detected - balancing disabled.")
    # Check each voltage.
    for i, v in enumerate(voltages, 1):
        if v is None or v == 0.0:
            # Zero/None: Disconnected or error.
            alert = f"Bank {i}: Zero voltage."
            alerts.append(alert)
            event_log.append(f"{time.strftime('%Y-%m-%d %H:%M:%S')}: {alert}")
            if len(event_log) > settings.get('EventLogSize', 20):
                event_log.pop(0)
            logging.warning(f"Zero voltage alert on Bank {i}.")
            alert_needed = True
        elif v > settings['HighVoltageThresholdPerBattery']:
            # Overvoltage.
            alert = f"Bank {i}: High voltage ({v:.2f}V)."
            alerts.append(alert)
            event_log.append(f"{time.strftime('%Y-%m-%d %H:%M:%S')}: {alert}")
            if len(event_log) > settings.get('EventLogSize', 20):
                event_log.pop(0)
            logging.warning(f"High voltage alert on Bank {i}: {v:.2f}V.")
            alert_needed = True
        elif v < settings['LowVoltageThresholdPerBattery']:
            # Undervoltage.
            alert = f"Bank {i}: Low voltage ({v:.2f}V)."
            alerts.append(alert)
            event_log.append(f"{time.strftime('%Y-%m-%d %H:%M:%S')}: {alert}")
            if len(event_log) > settings.get('EventLogSize', 20):
                event_log.pop(0)
            logging.warning(f"Low voltage alert on Bank {i}: {v:.2f}V.")
            alert_needed = True
    # Add temp alerts.
    if temps_alerts:
        alerts.extend(temps_alerts)
        alert_needed = True
    # If alerts needed, activate hardware alarm and send email.
    if alert_needed:
        if GPIO:
            GPIO.output(settings['AlarmRelayPin'], GPIO.HIGH)  # Turn on buzzer/light.
        logging.info("Alarm relay activated.")
        send_alert_email("\n".join(alerts), settings)
    else:
        # No issues—deactivate alarm.
        if GPIO:
            GPIO.output(settings['AlarmRelayPin'], GPIO.LOW)
        logging.info("No issues; alarm relay deactivated.")
    # Return status and full alerts.
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
    Now with verification: Monitors voltage changes to detect if balancing actually occurred (e.g., relays switched).
    If not, sets balancer_failed flag and alerts. For heating, balances regardless of diff to generate heat.
    Non-programmer: Like equalizing water levels in connected tanks, with a progress bar and safety locks.
    
    Args:
        stdscr: Curses screen object for TUI progress display.
        high (int): Bank number with higher voltage (source).
        low (int): Bank number with lower voltage (dest).
        settings (dict): Timings, thresholds, etc.
        temps_alerts (list): Temp issues—skips if any.
        is_heating (bool): True if for heating (ignore voltage diff).
    
    Returns:
        None
    """
    # Globals for state.
    global balance_start_time, last_balance_time, balancing_active, web_data, alive_timestamp, balancer_failed
    # Skip if temp alerts.
    if temps_alerts:
        logging.warning("Skipping balancing due to temperature anomalies in banks.")
        return
    # Mode name.
    mode = "Heating" if is_heating else "Normal"
    # Log start.
    logging.info(f"Starting {mode} balance from Bank {high} to {low}.")
    # Log event.
    event_log.append(f"{time.strftime('%Y-%m-%d %H:%M:%S')}: {mode} balancing started from Bank {high} to {low}")
    if len(event_log) > settings.get('EventLogSize', 20):
        event_log.pop(0)
    # Set flags.
    balancing_active = True
    web_data['balancing'] = True
    # Read initial voltages.
    initial_high_v, _, _ = read_voltage_with_retry(high, settings)
    initial_low_v, _, _ = read_voltage_with_retry(low, settings)
    # Skip if low is zero.
    if initial_low_v == 0.0:
        logging.warning(f"Cannot balance to Bank {low} (0.00V). Skipping.")
        balancing_active = False
        web_data['balancing'] = False
        return
    # Set relays.
    set_relay_connection(high, low, settings)
    # Turn on converter.
    control_dcdc_converter(True, settings)
    # Start timer.
    balance_start_time = time.time()
    # Initial trends.
    voltage_high = initial_high_v if initial_high_v is not None else 0.0
    voltage_low = initial_low_v if initial_low_v is not None else 0.0
    # Animation for progress.
    animation_frames = ['|', '/', '-', '\\']
    frame_index = 0
    # Screen dimensions for display.
    height, width = stdscr.getmaxyx()
    right_half_x = width // 2
    progress_y = 1
    high_trend = [voltage_high]
    low_trend = [voltage_low]
    # Read interval during balance (reuse startup).
    read_interval = settings['test_read_interval'] # Reuse from startup
    last_read = time.time()
    # Loop for duration.
    while time.time() - balance_start_time < settings['BalanceDurationSeconds']:
        # Update timestamp.
        alive_timestamp = time.time()
        # Progress calc.
        elapsed = time.time() - balance_start_time
        progress = min(1.0, elapsed / settings['BalanceDurationSeconds'])
        # Read voltages periodically.
        if time.time() - last_read >= read_interval:
            new_high, _, _ = read_voltage_with_retry(high, settings)
            new_low, _, _ = read_voltage_with_retry(low, settings)
            voltage_high = new_high if new_high is not None else voltage_high
            voltage_low = new_low if new_low is not None else voltage_low
            high_trend.append(voltage_high)
            low_trend.append(voltage_low)
            last_read = time.time()
        # Progress bar.
        bar_length = 20
        filled = int(bar_length * progress)
        bar = '=' * filled + ' ' * (bar_length - filled)
        # Display on TUI if space.
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
        # Log progress.
        logging.debug(f"Balancing progress: {progress * 100:.2f}%, High: {voltage_high:.2f}V, Low: {voltage_low:.2f}V")
        frame_index += 1
        # Short sleep for animation.
        time.sleep(0.01)
    # Final reads.
    final_high_v, _, _ = read_voltage_with_retry(high, settings)
    final_low_v, _, _ = read_voltage_with_retry(low, settings)
    final_high_v = final_high_v if final_high_v is not None else voltage_high
    final_low_v = final_low_v if final_low_v is not None else voltage_low
    high_trend.append(final_high_v)
    low_trend.append(final_low_v)
    # Turn off converter.
    control_dcdc_converter(False, settings)
    logging.info("Turning off DC-DC converter.")
    # Reset relays.
    set_relay_connection(0, 0, settings)
    logging.info("Resetting relay connections to default state.")
    # Reset flags.
    balancing_active = False
    web_data['balancing'] = False
    last_balance_time = time.time()
    # Verify: Check changes.
    if len(high_trend) >= 3 and len(low_trend) >= 3:
        high_change = final_high_v - initial_high_v
        low_change = final_low_v - initial_low_v
        min_delta = settings['min_voltage_delta']
        # Expected: High decreases, low increases by at least min_delta.
        if high_change >= 0 or low_change <= 0 or abs(high_change) < min_delta or low_change < min_delta:
            alert = f"Balancing failed from Bank {high} to {low}: No voltage change detected (High change: {high_change:.3f}V, Low change: {low_change:.3f}V). Possible relay failure."
            temps_alerts.append(alert) # Add to alerts (will trigger check_for_issues)
            event_log.append(f"{time.strftime('%Y-%m-%d %H:%M:%S')}: {alert}")
            if len(event_log) > settings.get('EventLogSize', 20):
                event_log.pop(0)
            logging.error(alert)
            balancer_failed = True
        else:
            logging.info(f"Balancing verified: High change {high_change:.3f}V, Low change {low_change:.3f}V.")
    else:
        logging.warning(f"Insufficient readings for balancing verification from {high} to {low}.")
    # Log end.
    logging.info(f"{mode} balancing process completed.")
    event_log.append(f"{time.strftime('%Y-%m-%d %H:%M:%S')}: {mode} balancing completed from Bank {high} to {low}")
    if len(event_log) > settings.get('EventLogSize', 20):
        event_log.pop(0)

def compute_bank_medians(calibrated_temps, valid_min):
    """
    Compute median, min, max, and invalid count for each bank's temperatures.
    Groups sensors by bank, filters valids (>valid_min), computes stats using statistics.median.
    Handles empty banks with zeros. Non-programmer: Like summarizing test scores for each class group: average, lowest, highest, misses.
    
    Args:
        calibrated_temps (list): List of temps (None for invalid).
        valid_min (float): Threshold for valid.
    
    Returns:
        list: Dict per bank with 'median', 'min', 'max', 'invalid' counts.
    """
    # List for bank stats.
    bank_stats = []
    # For each bank.
    for bank_indices in BANK_SENSOR_INDICES:
        # Get valid temps for this bank.
        bank_temps = [calibrated_temps[i] for i in bank_indices if calibrated_temps[i] is not None]
        # Count invalids.
        invalid_count = len(bank_indices) - len(bank_temps)
        if bank_temps:
            try:
                # Compute stats.
                med = statistics.median(bank_temps)
                mn = min(bank_temps)
                mx = max(bank_temps)
            except (TypeError, ValueError, statistics.StatisticsError) as e:
                # Error (e.g., all non-numeric)—default to 0.
                logging.warning(f"Error calculating stats for bank: {e}, temps={bank_temps}")
                med = mn = mx = 0.0
        else:
            # No valids.
            med = mn = mx = 0.0
        # Store as dict.
        bank_stats.append({'median': med, 'min': mn, 'max': mx, 'invalid': invalid_count})
    return bank_stats

def fetch_rrd_history(settings):
    """
    Fetch historical data from RRD database for charts.
    Uses rrdtool xport to get last HISTORY_LIMIT points (60s steps) for medtemp and each volt bank.
    Parses XML output into list of dicts with time and values (None for NaN). Non-programmer: Like pulling recent log entries
    from a journal for a trend graph.
    
    Args:
        settings (dict): Num banks for DS count.
    
    Returns:
        list: History entries reversed (newest first), or empty on error.
    """
    # Start time: Now minus limit * 60s.
    start = int(time.time()) - (HISTORY_LIMIT * 60)
    try:
        # Build DEF lines for each DS.
        def_list = [f'DEF:mt={RRD_FILE}:medtemp:LAST']
        xport_list = ['XPORT:mt:MedianTemp']
        for i in range(1, settings['num_series_banks'] + 1):
            def_list.append(f'DEF:v{i}={RRD_FILE}:volt{i}:LAST')
            xport_list.append(f'XPORT:v{i}:Bank{i}')
        # Run xport command.
        output = subprocess.check_output(['rrdtool', 'xport',
                                          '--start', str(start),
                                          '--end', 'now',
                                          '--step', '60'] + def_list + xport_list)
        # Log raw for debug.
        logging.debug(f"Raw RRD xport output: {output.decode()}")
        # Parse XML.
        root = ET.fromstring(output.decode())
        # Get meta if present.
        meta = root.find('meta')
        if meta is not None:
            meta_start = int(meta.find('start').text) if meta.find('start') is not None else start
            meta_step = int(meta.find('step').text) if meta.find('step') is not None else 60
        else:
            meta_start = start
            meta_step = 60
        # List for data.
        data = []
        current_time = meta_start
        # Expected values per row: medtemp + banks.
        expected_vs = settings['num_series_banks'] + 1 # medtemp + volts
        # Process each row.
        for row in root.findall('.//row'):
            vs = []
            # Parse each <v> element.
            for v in row.findall('v'):
                if v.text is None:
                    vs.append(None)
                    continue
                try:
                    # NaN to None.
                    vs.append(float(v.text) if v.text != 'NaN' else None)
                except ValueError:
                    vs.append(None)
            # Skip incomplete rows.
            if len(vs) != expected_vs:
                logging.warning(f"Skipping RRD row with incomplete values (got {len(vs)}, expected {expected_vs}).")
                continue
            # Build row dict.
            row_data = {'time': current_time, 'medtemp': vs[0]}
            for i in range(settings['num_series_banks']):
                row_data[f'volt{i+1}'] = vs[i+1]
            data.append(row_data)
            # Next timestamp.
            current_time += meta_step
        # Log count.
        logging.debug(f"Fetched {len(data)} history entries from RRD.")
        # Reverse for newest first.
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
    """
    Draw the Terminal User Interface (TUI) using curses.
    Renders ASCII art batteries with voltages/temps overlaid, bank summaries, full temp list, alerts, config info, event log.
    Colors for status (green normal, red alert). Handles screen size limits. Non-programmer: Like drawing a dashboard on your terminal screen
    with pictures, numbers, and warnings.
    
    Args:
        stdscr: Curses window.
        voltages (list): Bank voltages.
        calibrated_temps (list): Temps.
        raw_temps (list): Raw temps (for startup display).
        offsets (list): Offsets.
        bank_stats (list): Bank summaries.
        startup_median (float): Calibration median.
        alerts (list): Current alerts.
        settings (dict): Config for display.
        startup_set (bool): If calibrated.
        is_startup (bool): First run flag for extra info.
    
    Returns:
        None
    """
    # Log refresh.
    logging.debug("Refreshing TUI.")
    # Clear screen.
    stdscr.clear()
    # Setup colors.
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
    # Screen size.
    height, width = stdscr.getmaxyx()
    right_half_x = width // 2
    # Total voltage and color.
    total_v = sum(voltages)
    total_high = settings['HighVoltageThresholdPerBattery'] * NUM_BANKS
    total_low = settings['LowVoltageThresholdPerBattery'] * NUM_BANKS
    v_color = curses.color_pair(2) if total_v > total_high else curses.color_pair(3) if total_v < total_low else curses.color_pair(4)
    # ASCII art for total V.
    roman_v = text2art(f"{total_v:.2f}V", font='roman', chr_ignore=True)
    roman_lines = roman_v.splitlines()
    # Draw art lines.
    for i, line in enumerate(roman_lines):
        if i + 1 < height and len(line) < right_half_x:
            try:
                stdscr.addstr(i + 1, 0, line, v_color)
            except curses.error:
                logging.warning(f"addstr error for total voltage art line {i+1}.")
        else:
            logging.warning(f"Skipping total voltage art line {i+1} - out of bounds.")
    # Offset for next section.
    y_offset = len(roman_lines) + 3
    if y_offset >= height:
        logging.warning("TUI y_offset exceeds height; skipping art.")
        return
    # Base battery ASCII art (one bank).
    battery_art_base = [
        " _______________ ",
        " |             | ",
        " |             | ",
        " |             | ",
        " |             | ",
        " | +++         | ",
        " | +++         | ",
        " |             | ",
        " |             | ",
        " |             | ",
        " |             | ",
        " | ---         | ",
        " | ---         | ",
        " | ---         | ",
        " |             | ",
        " |             | ",
        " |_____________| "
    ]
    art_height = len(battery_art_base)
    art_width = len(battery_art_base[0])
    gap = " "
    gap_len = len(gap)
    # Draw multiple banks side by side.
    for row, line in enumerate(battery_art_base):
        full_line = gap.join([line] * NUM_BANKS)
        if y_offset + row < height and len(full_line) < right_half_x:
            try:
                stdscr.addstr(y_offset + row, 0, full_line, curses.color_pair(4))
            except curses.error:
                logging.warning(f"addstr error for art row {row}.")
        else:
            logging.warning(f"Skipping art row {row} - out of bounds.")
    # Overlay voltages on art.
    for bank_id in range(NUM_BANKS):
        start_pos = bank_id * (art_width + gap_len)
        v_str = f"{voltages[bank_id]:.2f}V" if voltages[bank_id] > 0 else "0.00V"
        # Color based on status.
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
        # Bank summary.
        summary = bank_stats[bank_id]
        med_str = f"Med: {summary['median']:.1f}°C"
        min_str = f"Min: {summary['min']:.1f}°C"
        max_str = f"Max: {summary['max']:.1f}°C"
        inv_str = f"Inv: {summary['invalid']}"
        # Color for summary.
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
    # Next offset.
    y_offset += art_height + 2
    # Full temps per bank.
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
            # Extra detail on startup.
            if is_startup:
                raw = raw_temps[i]
                raw_str = f"{raw:.1f}" if raw > settings['valid_min'] else "Inv"
                offset_str = f"{offsets[i]:.1f}" if startup_set and raw > settings['valid_min'] else "N/A"
                detail = f" ({raw_str}/{offset_str})"
            else:
                detail = ""
            t_str = f"Bat {bat_id} Local C{local_ch}: {calib_str}{detail}"
            # Color.
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
    # Startup median.
    med_str = f"{startup_median:.1f}°C" if startup_median else "N/A"
    if y_offset < height:
        try:
            stdscr.addstr(y_offset, 0, f"Startup Median Temp: {med_str}", curses.color_pair(7))
        except curses.error:
            logging.warning("addstr error for startup median.")
    else:
        logging.warning("Skipping startup median - out of bounds.")
    y_offset += 2
    # Alerts section.
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
    # Get local IP for web URL.
    local_ip = 'localhost'
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()
    except Exception:
        local_ip = socket.gethostbyname(socket.gethostname())
    # Config display in right half.
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
    # Draw config lines (simple column).
    for i, line in enumerate(config_lines):
        col = i // 20
        row = i % 20
        if col < num_cols and y_config + row < height:
            try:
                stdscr.addstr(y_config + row, right_half_x + col * col_width, line, curses.color_pair(7))
            except curses.error:
                pass
    # Event history in bottom right.
    y_offset = height // 2
    if y_offset < height:
        try:
            stdscr.addstr(y_offset, right_half_x, "Event History:", curses.color_pair(7))
        except curses.error:
            logging.warning("addstr error for event history header.")
    y_offset += 1
    # Last 20 events.
    for event in event_log[-20:]:
        if y_offset < height and len(event) < width - right_half_x:
            try:
                stdscr.addstr(y_offset, right_half_x, event, curses.color_pair(5))
            except curses.error:
                logging.warning(f"addstr error for event '{event}'.")
            y_offset += 1
        else:
            logging.warning(f"Skipping event '{event}' - out of bounds.")
    # Refresh screen.
    stdscr.refresh()

def setup_watchdog(timeout=15):
    """
    Initialize the hardware watchdog timer.
    Loads appropriate kernel module (bcm2835_wdt for older Pi, rp1-wdt for Pi5+), opens /dev/watchdog,
    sets timeout via ioctl. Returns True if successful. Non-programmer: Like setting a timer that resets the Pi if the script hangs.
    
    Args:
        timeout (int): Watchdog timeout in seconds (default 15s, Pi max).
    
    Returns:
        bool: True if setup OK, False on failure.
    """
    # Check if fcntl available (Linux-specific).
    if fcntl is None:
        logging.warning("fcntl not available - watchdog disabled")
        return False
    # Global fd.
    global watchdog_fd
    try:
        # Detect Pi model from /proc.
        model = "Unknown"
        if os.path.exists('/proc/device-tree/model'):
            with open('/proc/device-tree/model', 'r') as f:
                model = f.read().strip().lower()
        # Choose module: Older Pi vs Pi5+.
        logging.info(f"Detected Raspberry Pi model: {model}")
        if 'raspberry pi' in model and not 'raspberry pi 5' in model:
            module = 'bcm2835_wdt'
        else:
            module = 'rp1-wdt'
            logging.info("Assuming rp1-wdt for Pi 5 or newer model")
        # Load module.
        os.system(f'sudo modprobe {module}')
        logging.info(f"Loaded watchdog module: {module}")
        # Wait for load.
        time.sleep(1)
        # Check device file.
        if not os.path.exists(WATCHDOG_DEV):
            logging.error(f"Watchdog device {WATCHDOG_DEV} not found. Watchdog disabled.")
            return False
        # Open device.
        watchdog_fd = open(WATCHDOG_DEV, 'wb')
        logging.debug(f"Opened watchdog device: {WATCHDOG_DEV}")
        # Set timeout via ioctl (magic 'W' + 6, pack timeout).
        try:
            magic = ord('W') << 8 | 0x06
            fcntl.ioctl(watchdog_fd, magic, struct.pack("I", timeout))
            logging.info(f"Watchdog set with timeout {timeout}s")
        except IOError as e:
            logging.warning(f"Failed to set watchdog timeout: {e}. Using default.")
        # Log init.
        logging.debug("Watchdog initialized")
        return True
    except Exception as e:
        logging.error(f"Failed to setup watchdog: {e}.")
        return False

def watchdog_pet_thread(pet_interval=5, hang_threshold=12):
    """
    Dedicated thread to pet (reset) the watchdog every pet_interval seconds, but only if main thread is alive.
    Checks alive_timestamp; if diff > hang_threshold, assumes hang and stops petting (allows reset).
    Increased hang_threshold to 12s to prevent false hang detection during normal 10s poll_interval sleep, ensuring watchdog (15s timeout) is petted reliably.
    Non-programmer: Like a watchdog dog that you feed treats regularly; if you stop moving (hang), it barks and resets the system.
    
    Args:
        pet_interval (int): Seconds between pets (5s).
        hang_threshold (int): Max time without alive update before assuming hang (12s).
    
    Returns:
        None: Runs in loop until hang or error.
    """
    # Globals.
    global watchdog_fd, alive_timestamp
    # Infinite loop.
    while True:
        try:
            # Check if main hung (timestamp stale).
            if time.time() - alive_timestamp > hang_threshold:
                logging.warning("Main thread hang detected; stopping watchdog pets to allow reset.")
                break # Stop petting
            # Pet: Write 'w' to device.
            if watchdog_fd:
                watchdog_fd.write(b'w')
                watchdog_fd.flush()
                logging.debug("Watchdog petted")
        except IOError as e:
            # Pet failed—try reopen.
            logging.error(f"Watchdog pet failed: {e}. Reopening device.")
            try:
                watchdog_fd.close()
                watchdog_fd = open(WATCHDOG_DEV, 'wb')
            except IOError as reopen_e:
                logging.error(f"Failed to reopen watchdog: {reopen_e}. Disabling pets.")
                break
        # Wait.
        time.sleep(pet_interval)

def close_watchdog():
    """
    Disable watchdog by writing 'V' (disable) and closing file.
    Non-programmer: Like telling the watchdog "all good, go home—no reset needed."
    
    Returns:
        None
    """
    # Global.
    global watchdog_fd
    if watchdog_fd:
        try:
            # Write 'V' to disable.
            watchdog_fd.write(b'V')
            watchdog_fd.close()
        except IOError:
            pass  # Ignore errors on close.

def startup_self_test(settings, stdscr, data_dir):
    """
    Perform comprehensive startup self-test: Config, hardware, reads, calibration, balancer verification.
    Runs in loop with retries (up to 5, 2min wait). Displays progress on TUI. If fails max, resets flags and proceeds.
    Tests each step: Config valid, I2C/Modbus connect, initial reads, calibrate if all valid, test all balance pairs with delta check.
    Non-programmer: Like a car's startup diagnostic: Checks engine, lights, etc.; retries if issue, but drives if minor.
    
    Args:
        settings (dict): Config.
        stdscr: TUI screen.
        data_dir (str): For offsets.
    
    Returns:
        list: Empty if passed, or alerts (but proceeds anyway after retries).
    """
    # Globals.
    global startup_failed, startup_alerts, startup_set, startup_median, startup_offsets, balancer_failed
    # Skip if disabled.
    if not settings['StartupSelfTestEnabled']:
        logging.info("Startup self-test disabled via configuration.")
        return []
    # Max retries.
    max_retries = 5
    retries = 0
    # Retry loop.
    while retries < max_retries:
        # Log attempt.
        logging.info(f"Starting self-test attempt {retries + 1}")
        # Alerts for this run.
        alerts = []
        # Clear screen.
        stdscr.clear()
        y = 0
        # Title.
        if y < stdscr.getmaxyx()[0]:
            try:
                stdscr.addstr(y, 0, "Startup Self-Test in Progress", curses.color_pair(1))
            except curses.error:
                logging.warning("addstr error for title.")
        y += 2
        stdscr.refresh()
        # Step 1: Config.
        logging.info("Step 1: Validating configuration parameters.")
        logging.debug(
            f"Configuration details: I2C_BusNumber={settings['I2C_BusNumber']}, "
            f"MultiplexerAddress=0x{settings['MultiplexerAddress']:02x}, "
            f"VoltageMeterAddress=0x{settings['VoltageMeterAddress']:02x}, "
            f"Temp_IP={settings['ip']}, Temp_Port={settings['modbus_port']}, "
            f"TotalChannels={settings['total_channels']}, ScalingFactor={settings['scaling_factor']}, "
            f"ParallelBatteries={settings['number_of_parallel_batteries']}, SlaveAddresses={settings['modbus_slave_addresses']}"
        )
        if y < stdscr.getmaxyx()[0]:
            try:
                stdscr.addstr(y, 0, "Step 1: Validating config...", curses.color_pair(4))
            except curses.error:
                logging.warning("addstr error for step 1.")
        stdscr.refresh()
        time.sleep(0.5)
        # Assume passed (validate_config already called).
        logging.debug("Configuration validation passed.")
        if y + 1 < stdscr.getmaxyx()[0]:
            try:
                stdscr.addstr(y + 1, 0, "Config OK.", curses.color_pair(4))
            except curses.error:
                logging.warning("addstr error for config OK.")
        y += 2
        stdscr.refresh()
        # Step 2: Hardware.
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
                      f"VoltageMeter=0x{settings['VoltageMeterAddress']:02x}")
        try:
            if bus:
                logging.debug(f"Selecting I2C channel 0 on multiplexer 0x{settings['MultiplexerAddress']:02x}")
                choose_channel(0, settings['MultiplexerAddress'])
                logging.debug(f"Reading byte from VoltageMeter at 0x{settings['VoltageMeterAddress']:02x}")
                bus.read_byte(settings['VoltageMeterAddress'])
                logging.debug("I2C connectivity test passed for voltage meter.")
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
                          f"VoltageMeter=0x{settings['VoltageMeterAddress']:02x}")
            if y + 1 < stdscr.getmaxyx()[0]:
                try:
                    stdscr.addstr(y + 1, 0, f"I2C failure: {str(e)}", curses.color_pair(2))
                except curses.error:
                    logging.warning("addstr error for I2C failure.")
        # Test Modbus per slave.
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
        # Step 3: Initial reads.
        logging.info("Step 3: Performing initial sensor reads (temperature per slave and voltage).")
        if y < stdscr.getmaxyx()[0]:
            try:
                stdscr.addstr(y, 0, "Step 3: Initial sensor reads...", curses.color_pair(4))
            except curses.error:
                logging.warning("addstr error for step 3.")
        stdscr.refresh()
        time.sleep(0.5)
        # Temps.
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
        # Display temp result.
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
        # Voltages.
        initial_voltages = []
        for i in range(1, NUM_BANKS + 1):
            voltage, readings, adc_values = read_voltage_with_retry(i, settings)
            initial_voltages.append(voltage if voltage is not None else 0.0)
        # Check voltages.
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
        # Calibrate if all temps valid.
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
        # Step 4: Balancer test if no alerts and voltages OK.
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
            # Initial voltages for test.
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
            # Dict for sorting.
            bank_voltages_dict = {b: initial_bank_voltages[b-1] for b in range(1, NUM_BANKS + 1)}
            sorted_banks = sorted(bank_voltages_dict, key=bank_voltages_dict.get, reverse=True)
            # All possible pairs.
            pairs = []
            for source in sorted_banks:
                for dest in [b for b in range(1, NUM_BANKS + 1) if b != source]:
                    pairs.append((source, dest))
            # Test params.
            test_duration = settings['test_balance_duration']
            read_interval = settings['test_read_interval']
            min_delta = settings['min_voltage_delta']
            logging.debug(f"Balancer test parameters: test_duration={test_duration}s, "
                          f"read_interval={read_interval}s, min_voltage_delta={min_delta}V")
            # Test each pair.
            for source, dest in pairs:
                logging.debug(f"Testing balance from Bank {source} to Bank {dest}")
                if y < stdscr.getmaxyx()[0]:
                    try:
                        stdscr.addstr(y, 0, f"Testing balance from Bank {source} to Bank {dest} for {test_duration}s.", curses.color_pair(6))
                    except curses.error:
                        logging.warning("addstr error for testing balance.")
                stdscr.refresh()
                logging.info(f"Testing balance from Bank {source} to Bank {dest} for {test_duration}s.")
                # Check temps for anomalies.
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
                # Initial for test.
                initial_source_v = read_voltage_with_retry(source, settings)[0] or 0.0
                initial_dest_v = read_voltage_with_retry(dest, settings)[0] or 0.0
                time.sleep(0.5)
                logging.debug(f"Balance test from Bank {source} to Bank {dest}: Initial - Bank {source}={initial_source_v:.2f}V, Bank {dest}={initial_dest_v:.2f}V")
                # Start test balance.
                set_relay_connection(source, dest, settings)
                control_dcdc_converter(True, settings)
                start_time = time.time()
                source_trend = [initial_source_v]
                dest_trend = [initial_dest_v]
                progress_y = y + 1
                # Loop for duration.
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
                # Final.
                final_source_v = read_voltage_with_retry(source, settings)[0] or 0.0
                final_dest_v = read_voltage_with_retry(dest, settings)[0] or 0.0
                time.sleep(0.5)
                logging.debug(f"Balance test from Bank {source} to Bank {dest}: Final - Bank {source}={final_source_v:.2f}V, Bank {dest}={final_dest_v:.2f}V")
                # Stop.
                control_dcdc_converter(False, settings)
                set_relay_connection(0, 0, settings)
                if progress_y + 1 < stdscr.getmaxyx()[0]:
                    try:
                        stdscr.addstr(progress_y + 1, 0, "Analyzing...", curses.color_pair(6))
                    except curses.error:
                        logging.warning("addstr error for analyzing.")
                stdscr.refresh()
                # Analyze.
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
                        balancer_failed = True
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
                    balancer_failed = True
                    if progress_y + 1 < stdscr.getmaxyx()[0]:
                        try:
                            stdscr.addstr(progress_y + 1, 0, "Test failed: Insufficient readings.", curses.color_pair(2))
                        except curses.error:
                            logging.warning("addstr error for test failed insufficient readings.")
                stdscr.refresh()
                y = progress_y + 2
                time.sleep(2)
        # Set alerts.
        startup_alerts = alerts
        # If alerts, handle failure.
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
                    stdscr.addstr(2, 0, f"Alarm activated. Retry {retries + 1}/{max_retries}...", curses.color_pair(2))
                except curses.error:
                    logging.warning("addstr error for retry message.")
            stdscr.refresh()
            # Update web.
            web_data['system_status'] = f'Startup Self-Test Failed - Retry {retries + 1}/{max_retries}'
            web_data['alerts'] = startup_alerts
            web_data['last_update'] = time.time()
            retries += 1
            if retries >= max_retries:
                logging.warning("Max retries reached for startup self-test. Proceeding to main loop with startup_failed reset to False.")
                startup_failed = False # Reset to allow balancing
                break
            time.sleep(120) # Wait 2 minutes before retry
            continue
        else:
            # Success.
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
    # If here, passed or max retries.
    return []

def start_web_server(settings):
    """
    Start the Flask web server in a separate thread.
    Defines routes: / for dashboard HTML (with Chart.js), /api/status for data, /api/history for RRD, /api/balance for manual trigger.
    Includes auth and CORS if enabled. Dynamic JS for charts based on num_banks. Non-programmer: Like setting up a website
    on your Pi that shows battery status and graphs in a browser.
    
    Args:
        settings (dict): Web config (host, port, auth, etc.).
    
    Returns:
        None: Starts thread.
    """
    # Global.
    global web_server
    # Skip if disabled.
    if not settings['WebInterfaceEnabled']:
        logging.info("Web interface disabled via configuration.")
        return
    # Skip if Flask missing.
    if Flask is None:
        logging.warning("Flask not available - web interface cannot start.")
        return
    # Create app.
    app = Flask(__name__)
    # Route for main page.
    @app.route('/')
    def index():
        # Dynamic datasets for charts.
        colors = ['green', 'blue', 'red', 'orange', 'purple', 'brown', 'pink', 'gray']
        datasets_list = []
        for i in range(1, settings['num_series_banks'] + 1):
            color = colors[(i-1) % len(colors)]
            datasets_list.append(f"{{ label: 'Bank {i} V', data: hist.map(h => h.volt{i}), borderColor: '{color}' }}")
        datasets_list.append("{{ label: 'Median Temp °C', data: hist.map(h => h.medtemp), borderColor: 'cyan', yAxisID: 'temp' }}")
        datasets_array = ',\n'.join(datasets_list)
        logging.debug(f"Constructed datasets_array: {datasets_array}")
        # Full HTML with styles, JS for charts, dark mode, etc.
        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Battery Management System</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js@4.5.0"></script>
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
                    const sensorsPerBattery = data.sensors_per_battery;
                    data.voltages.forEach((voltage, index) => {{
                        const summary = data.bank_summaries[index];
                        const bankDiv = document.createElement('div');
                        bankDiv.className = 'battery';
                        bankDiv.innerHTML = `
                            <h3>Bank ${{index + 1}}</h3>
                            <p class="voltage ${{voltage === 0 || voltage === null ? 'alert' : (voltage > data.high_voltage_threshold || voltage < data.low_voltage_threshold) ? 'warning' : 'normal'}}">
                                ${{voltage !== null ? voltage.toFixed(2) : 'N/A'}}V
                            </p>
                            <div class="bank-summary">
                                <p class="temperature ${{summary.median > data.high_threshold || summary.median < data.low_threshold || summary.invalid > 0 ? 'warning' : 'normal'}}">
                                    Median: ${{summary.median.toFixed(1)}}°C Min: ${{summary.min.toFixed(1)}}°C Max: ${{summary.max.toFixed(1)}}°C Invalid: ${{summary.invalid}}
                                </p>
                            </div>
                            <div class="temperatures">
                                ${{data.temperatures.slice(index * sensorsPerBank, (index + 1) * sensorsPerBank).map((temp, localIndex) => {{
                                    const globalIndex = index * sensorsPerBank + localIndex;
                                    const batId = Math.floor(globalIndex / sensorsPerBattery) + 1;
                                    const localCh = (globalIndex % sensorsPerBattery) + 1;
                                    return `<p class="temperature ${{temp === null ? 'alert' : (temp > data.high_threshold || temp < data.low_threshold) ? 'warning' : 'normal'}}">
                                        Bat ${{batId}} Local C${{localCh}}: ${{temp !== null ? temp.toFixed(1) + '°C' : 'N/A'}}
                                    </p>`;
                                }}).join('')}}
                            </div>
                        `;
                        batteryContainer.appendChild(bankDiv);
                    }});
                    const alertsContainer = document.getElementById('alerts-container');
                    if (data.alerts.length > 0) {{
                        alertsContainer.innerHTML = data.alerts.map(alert => `<p class="alert">${{alert}}</p>`).join('');
                    }} else {{
                        alertsContainer.innerHTML = '<p class="normal">No alerts</p>';
                    }}
                    const balanceBtn = document.getElementById('balance-btn');
                    balanceBtn.disabled = data.balancing || data.alerts.length > 0;
                }})
                .catch(error => {{
                    console.error('Error fetching status:', error);
                    document.getElementById('system-status').textContent = 'Error: ' + error.message;
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
</html>"""
        return html
    # API for status.
    @app.route('/api/status')
    def api_status():
        try:
            # Lock for thread safety.
            with data_lock:
                voltages = [v if v is not None else 0.0 for v in web_data['voltages']]
                response = {
                    'voltages': web_data['voltages'],
                    'temperatures': web_data['temperatures'],
                    'bank_summaries': web_data['bank_summaries'],
                    'alerts': web_data['alerts'],
                    'balancing': web_data['balancing'],
                    'last_update': web_data['last_update'],
                    'system_status': web_data['system_status'],
                    'total_voltage': sum(voltages),
                    'high_threshold': settings['high_threshold'],
                    'low_threshold': settings['low_threshold'],
                    'high_voltage_threshold': settings['HighVoltageThresholdPerBattery'],
                    'low_voltage_threshold': settings['LowVoltageThresholdPerBattery'],
                    'sensors_per_battery': settings['sensors_per_battery']
                }
            return jsonify(response)
        except Exception as e:
            logging.error(f"Error in /api/status: {str(e)}\n{traceback.format_exc()}")
            return jsonify({'error': str(e)}), 500
    # API for history.
    @app.route('/api/history')
    def api_history():
        try:
            history = fetch_rrd_history(settings)
            return jsonify({'history': history})
        except Exception as e:
            logging.error(f"Error in /api/history: {str(e)}\n{traceback.format_exc()}")
            return jsonify({'error': str(e)}), 500
    # API for balance trigger.
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
    # Before each request: Auth and CORS.
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
    # Function to run app.
    def run_app():
        logging.info("Starting Flask app...")
        try:
            app.run(host=settings['host'], port=settings['web_port'], threaded=True, debug=False, use_reloader=False)
        except Exception as e:
            logging.error(f"Web server error: {e}\n{traceback.format_exc()}")
    # Start thread.
    server_thread = threading.Thread(target=run_app)
    server_thread.daemon = True
    server_thread.start()
    logging.info(f"Web server started on {settings['host']}:{settings['web_port']}")

def main(stdscr):
    """
    Main entry point: Initializes everything and runs the monitoring loop.
    Checks deps, loads config, setups hardware/web, self-test, starts watchdog, then infinite loop: Read temps/volts,
    check issues, update RRD/TUI/web, balance if needed, sleep. Non-programmer: The "heart" of the script—where the ongoing work happens.
    
    Args:
        stdscr: Curses screen (from wrapper).
    
    Returns:
        None: Runs forever until signal.
    """
    # Check deps.
    check_dependencies()
    # Curses setup.
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
    # Globals.
    global previous_temps, previous_bank_medians, run_count, startup_offsets, startup_median, startup_set, battery_voltages, web_data, balancing_active, BANK_SENSOR_INDICES, alive_timestamp, NUM_BANKS, balancer_failed
    # Load and validate config.
    settings = load_config(data_dir)
    validate_config(settings)
    # Set banks.
    NUM_BANKS = settings['num_series_banks'] # Dynamic now.
    number_parallel = settings['number_of_parallel_batteries']
    slave_addresses = settings['modbus_slave_addresses']
    sensors_per_bank = settings['sensors_per_bank']
    sensors_per_battery = NUM_BANKS * sensors_per_bank
    total_channels = number_parallel * sensors_per_battery
    # Bank indices.
    BANK_SENSOR_INDICES = [[] for _ in range(settings['num_series_banks'])] # Dynamic list of lists.
    # Init web_data.
    web_data['voltages'] = [0.0] * NUM_BANKS
    web_data['temperatures'] = [None] * total_channels
    web_data['bank_summaries'] = [{'median': 0.0, 'min': 0.0, 'max': 0.0, 'invalid': 0}] * NUM_BANKS
    # Build indices.
    for bat in range(number_parallel):
        base = bat * sensors_per_battery
        for bank_id in range(NUM_BANKS):
            bank_base = base + bank_id * sensors_per_bank
            BANK_SENSOR_INDICES[bank_id].extend(range(bank_base, bank_base + sensors_per_bank))
    # Setup.
    setup_hardware(settings)
    time.sleep(1) # Short delay to allow hardware initialization
    # Web.
    start_web_server(settings)
    # Self-test.
    startup_self_test(settings, stdscr, data_dir)
    # Signal handler.
    signal.signal(signal.SIGINT, signal_handler)
    # Watchdog.
    if settings['WatchdogEnabled'] and setup_watchdog(15):
        wd_thread = threading.Thread(target=watchdog_pet_thread, daemon=True)
        wd_thread.start()
        logging.info("Watchdog pet thread started.")
    else:
        logging.info("Watchdog disabled or setup failed.")
    # Init previous.
    previous_temps = [None] * total_channels
    previous_bank_medians = [0.0] * NUM_BANKS
    alive_timestamp = time.time()
    # Main loop.
    while True:
        # Temps alerts.
        temps_alerts = [] # List to collect any temperature problems we find
        all_raw_temps = [] # Will hold all raw temperature readings from all sensors
        # Read temps per slave.
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
        # Valid count.
        valid_count = sum(1 for t in raw_temps if t > settings['valid_min'])
        # Calibrate if first valid full read.
        if not startup_set and valid_count == total_channels:
            startup_median = statistics.median(raw_temps) # Find the middle temperature value
            startup_offsets = [startup_median - t for t in raw_temps] # Calculate adjustments
            save_offsets(startup_median, startup_offsets, data_dir) # Save to file
            startup_set = True
            logging.info(f"Temp calibration set. Median: {startup_median:.1f}°C")
        # Reset if offsets missing.
        if startup_set and startup_offsets is None:
            startup_set = False
        # Apply offsets.
        calibrated_temps = [raw_temps[i] + startup_offsets[i] if startup_set and raw_temps[i] > settings['valid_min'] else raw_temps[i] if raw_temps[i] > settings['valid_min'] else None for i in range(total_channels)]
        # Bank stats.
        bank_stats = compute_bank_medians(calibrated_temps, settings['valid_min'])
        bank_medians = [s['median'] for s in bank_stats]
        # Check static anomalies.
        for ch, raw in enumerate(raw_temps, 1):
            if check_invalid_reading(raw, ch, temps_alerts, settings['valid_min'], settings):
                continue
            calib = calibrated_temps[ch-1]
            bank_id = get_bank_for_channel(ch)
            bank_median = bank_medians[bank_id - 1]
            check_high_temp(calib, ch, temps_alerts, settings['high_threshold'], settings)
            check_low_temp(calib, ch, temps_alerts, settings['low_threshold'], settings)
            check_deviation(calib, bank_median, ch, temps_alerts, settings['abs_deviation_threshold'], settings['deviation_threshold'], settings)
        # Dynamic checks if not first run.
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
        # Update previous.
        previous_temps = calibrated_temps[:]
        previous_bank_medians = bank_medians[:]
        # Overall median.
        valid_calib_temps = [t for t in calibrated_temps if t is not None]
        try:
            overall_median = statistics.median(valid_calib_temps) if valid_calib_temps else 0.0
        except (TypeError, statistics.StatisticsError) as e:
            logging.warning(f"Error calculating overall median: {e}, using 0.0")
            overall_median = 0.0
        # Fan for cabinet overheat.
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
        # Read voltages.
        battery_voltages = []
        for i in range(1, NUM_BANKS + 1):
            v, _, _ = read_voltage_with_retry(i, settings) # Read voltage with error handling
            battery_voltages.append(v if v is not None else 0.0) # Use 0.0 if reading failed
        # Check issues.
        alert_needed, all_alerts = check_for_issues(battery_voltages, temps_alerts, settings)
        # Update RRD.
        timestamp = int(time.time())
        values = f"{timestamp}:{overall_median}:{':'.join(map(str, battery_voltages))}"
        subprocess.call(['rrdtool', 'update', RRD_FILE, values])
        logging.debug(f"RRD updated with: {values}")
        # Balance decision.
        if len(battery_voltages) == NUM_BANKS and not balancer_failed:
            max_v = max(battery_voltages) # Find highest voltage bank
            min_v = min(battery_voltages) # Find lowest voltage bank
            high_b = battery_voltages.index(max_v) + 1 # Bank number with highest voltage
            low_b = battery_voltages.index(min_v) + 1 # Bank number with lowest voltage
            current_time = time.time()
            any_low_temp = any(t is not None and t < 10 for t in calibrated_temps)
            # Condition.
            if balancing_active or (not alert_needed and (any_low_temp or max_v - min_v > settings['VoltageDifferenceToBalance']) and min_v > 0 and current_time - last_balance_time > settings['BalanceRestPeriodSeconds']):
                is_heating = any_low_temp
                balance_battery_voltages(stdscr, high_b, low_b, settings, temps_alerts, is_heating=is_heating) # Transfer charge
                balancing_active = False
        # Update web data (locked).
        with data_lock:
            web_data['voltages'] = battery_voltages
            web_data['temperatures'] = calibrated_temps
            web_data['bank_summaries'] = bank_stats
            web_data['alerts'] = all_alerts
            web_data['balancing'] = balancing_active
            web_data['last_update'] = time.time()
            web_data['system_status'] = 'Alert' if alert_needed else 'Running'
        # Draw TUI.
        draw_tui(
            stdscr, battery_voltages, calibrated_temps, raw_temps,
            startup_offsets or [0]*total_channels, bank_stats,
            startup_median, all_alerts, settings, startup_set, is_startup=(run_count == 0)
        )
        # Update alive.
        alive_timestamp = time.time() # Update aliveness for watchdog thread
        run_count += 1
        # Cleanup.
        gc.collect()
        logging.info("Poll cycle complete.")
        # Sleep.
        time.sleep(settings['poll_interval'])
      
if __name__ == '__main__':
    # Arg parser.
    parser = argparse.ArgumentParser(description='Battery Management System')
    parser.add_argument('--validate-config', action='store_true', help='Validate configuration and exit')
    parser.add_argument('--data-dir', default='.', help='Directory containing config files')
    args = parser.parse_args()
    data_dir = args.data_dir
    # If validate.
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
        # Setup logging.
        logging.basicConfig(
            filename=os.path.join(data_dir, 'battery_monitor.log'),
            level=logging.INFO,
            format='%(asctime)s - %(message)s'
        )
        # Read config.
        config_parser.read(os.path.join(data_dir, 'battery_monitor.ini'))
        # RRD path.
        RRD_FILE = os.path.join(data_dir, 'bms.rrd')
        # Run with curses wrapper (handles init/cleanup).
        curses.wrapper(main)