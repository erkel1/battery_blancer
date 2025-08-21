"""
Combined Battery Temperature Monitoring and Balancing Script (Updated for 3s8p Configuration)

Extensive Summary:
This script serves as a comprehensive Battery Management System (BMS) for a configurable battery configuration (default 3s8p: 3 series-connected parallel battery banks, each with 8 cells). It integrates temperature monitoring from NTC sensors via a Lantronix EDS4100 device (using Modbus RTU over TCP) with voltage balancing using I2C-based ADC for readings and relays/GPIO for control. The system runs in an infinite loop, polling data at configurable intervals, detecting anomalies, balancing voltages if imbalances exceed thresholds, logging events, sending email alerts for critical issues, and displaying real-time status in a curses-based Text User Interface (TUI). Includes an optional web interface for remote monitoring and manual balancing.

Key Features and Architecture:
- **Temperature Monitoring:** Reads configurable NTC sensors (default 24) via Modbus TCP. Calibrates to median at startup (offsets calculated on first run), detects invalid/high/low/deviation/rise/lag/disconnection anomalies.
- **Voltage Monitoring and Balancing:** Reads bank voltages via ADS1115 ADC over I2C with retries. Balances by connecting high to low bank via relays and activating DC-DC converter if difference > threshold and no alerts.
- **Alerts and Notifications:** Logs issues, activates GPIO alarm relay, sends throttled SMTP emails.
- **User Interfaces:** Curses TUI with ASCII art for real-time display (live reads); optional HTTP web dashboard with API for status and manual balance.
- **Startup Self-Test:** Validates config, hardware connectivity, sensor reads, and balancer functionality. Calculates startup_median at startup; offsets on first run only.
- **Error Handling:** Retries on reads, guards for None, exponential backoff, test mode for no hardware libs, specific exception handling with tracebacks.
- **Configuration:** Loaded from 'battery_monitor.ini' with fallbacks; changes require restart.
- **Shutdown Handling:** Graceful cleanup on Ctrl+C (SIGINT).

What it does (Detailed):
- Reads temperatures from NTC sensors (default 24, grouped into banks).
- Calculates startup_median at each startup in startup_self_test; loads/calculates startup_offsets from offsets.txt (first run only).
- Detects temp anomalies: invalid (≤ valid_min), high/low (thresholds), deviation from bank median (abs/rel), abnormal rise (> threshold in poll interval), group lag (dev from median rise), sudden disconnection (prev valid, now None).
- Reads voltages from banks using ADS1115 ADC over I2C, with retries (2 attempts, consistency check) and calibration multipliers.
- Checks voltage issues: zero/None (read failure), high/low (per bank thresholds).
- Balances voltages: If max-min > threshold, no alerts, and rest period elapsed, connects high to low bank via relays, turns on DC-DC converter for duration, shows progress in TUI/web.
- Alerts: Logs issues at configurable level, activates GPIO alarm relay on any alert, sends throttled emails with details.
- TUI: ASCII art batteries with live voltages/temps (re-reads for freshness), ADC/readings, alerts; handles screen bounds to avoid errors.
- Web: HTTP dashboard for status, alerts, manual balance (with auth/CORS); API endpoints /status and /balance.
- Handles shutdown: Ctrl+C cleans GPIO, shuts web server.
- Startup Self-Check: Configurable, validates config/hardware/reads; tests balancer on all pairs if no failures, with voltage trend analysis.

How it does it (Detailed Flow):
- Config loaded from 'battery_monitor.ini' with fallbacks (e.g., defaults if keys missing).
- Hardware setup: I2C bus, GPIO pins initialized (low state).
- Startup check: Validates config (bank count), tests I2C/Modbus connectivity, initial sensor reads, balancer (if no fails: test pairs for voltage delta). Calculates startup_median; calculates startup_offsets if offsets.txt missing.
- Infinite loop: Poll temps/voltages (retry on invalid), process/calibrate temps, check alerts (voltage/temp), balance if needed (high->low), draw TUI, update web data, sleep.
- Logging: To 'battery_monitor.log' at configurable level (e.g., INFO for key events, DEBUG for verbose).
- Edges: Retries on reads (Modbus/I2C), guards for None/0 values, exponential backoff on failures, mock-safe for testing (no hardware libs).

Logic Diagram (ASCII Flowchart of Execution):
+----------------+
|   Start Script |
+----------------+
          |
          v
+----------------+
|   Load Config  |
+----------------+
          |
          v
+----------------+
| Setup Hardware |
+----------------+
          |
          v
+----------------+
| Startup Check  |
+----------------+
          | fail
          v
  (Alarm + Continue)
          |
          v
+----------------+
|  Infinite Loop |
+----------------+
          |
          v
  /---------------\   /---------------\
  |   Read Temps  |   | Read Voltages |
  \---------------/   \---------------/
          |                 |
          v                 v
+----------------+  +----------------+
| Process Temps  |  | Check Issues   |
| & Alerts       |  | & Alerts       |
+----------------+  +----------------+
          |                 |
          \-----------------/
          |
          v
+----------------+
| Need Balance?  |
+----------------+
          | yes
          v
+----------------+
|   Balance      |
|   Banks        |
+----------------+
          | no
          v
+----------------+
|    Draw TUI    |
+----------------+
          |
          v
+----------------+
| Sleep & Repeat |
+----------------+
          ^
          | (loop back)
          |

Dependencies: socket, statistics, time, configparser, logging, signal, gc, os, smbus, RPi.GPIO, smtplib, email.mime.text.MIMEText, curses, sys, art (pip install art), threading, json, http.server, urllib.parse, base64, traceback.
Installation:
- Python 3.11+: sudo apt install python3
- Hardware libs: sudo apt install python3-smbus python3-rpi.gpio
- Art: pip install art
- Enable I2C: sudo raspi-config > Interfacing Options > I2C > Enable; reboot
- INI File: Create battery_monitor.ini (template in prior responses)
- Run: sudo python bms.py (root for GPIO/I2C)
Note: Ensure EDS4100 configured, INI present, hardware connected. Web at http://<pi-ip>:8080.
"""

# Standard library imports
import socket  # For TCP connection to EDS4100 device
import statistics  # For median calculations on temperatures
import time  # For delays and timing
import configparser  # For loading settings from INI file
import logging  # For logging events to file
import signal  # For handling Ctrl+C graceful shutdown
import gc  # For manual garbage collection in long-running loop
import os  # For file operations like offsets.txt
import sys  # For sys.exit on shutdown
import threading  # For web server thread
import json  # For API responses
from urllib.parse import urlparse, parse_qs  # For parsing requests
import base64  # For basic auth decoding
import traceback  # For logging stack traces in exceptions

# Hardware-specific imports (may need installation)
try:
    import smbus  # For I2C communication with ADC/relays
    import RPi.GPIO as GPIO  # For GPIO control of relays/converter
except ImportError:
    # Fallback for testing without hardware
    print("Hardware libraries not available - running in test mode")
    smbus = None
    GPIO = None

# Email and web imports
from email.mime.text import MIMEText  # For constructing email messages
import smtplib  # For sending emails
from http.server import HTTPServer, BaseHTTPRequestHandler  # For web server

# UI imports
import curses  # For terminal-based TUI
from art import text2art  # For ASCII art total voltage

# Setup logging early to capture all events
logging.basicConfig(filename='battery_monitor.log', level=logging.INFO, format='%(asctime)s - %(message)s')

# Global variables for state management
config_parser = configparser.ConfigParser()  # Parser for INI config
bus = None  # I2C bus object
last_email_time = 0  # Timestamp for email throttling
balance_start_time = None  # Timestamp for balancing duration
last_balance_time = 0  # Timestamp for balancing rest period
battery_voltages = []  # List of current bank voltages
previous_temps = None  # Previous calibrated temps for rise/lag checks
previous_bank_medians = None  # Previous bank medians for rise checks
run_count = 0  # Poll cycle counter (for startup check)
startup_offsets = None  # Per-channel temp offsets from startup
startup_median = None  # Startup median temp for reference
startup_set = False  # Flag if startup calibration done
alert_states = {}  # Per-channel alert tracking
balancing_active = False  # Flag if balancing in progress
startup_failed = False  # Persistent flag for startup failures
startup_alerts = []  # Store startup failures for TUI alerts
web_server = None  # Web server instance

# Web interface shared data structure
web_data = {
    'voltages': [0.0] * 3,  # Current bank voltages
    'temperatures': [None] * 24,  # Current temperature readings
    'alerts': [],  # Active alerts
    'balancing': False,  # Balancing status
    'last_update': time.time(),  # Last update timestamp
    'system_status': 'Initializing'  # Overall system status
}

# Bank definitions for 3s8p configuration
BANK_RANGES = [(1, 8), (9, 16), (17, 24)]  # Channel ranges for each bank
NUM_BANKS = 3  # Number of banks (hardcoded for 3s8p)

def get_bank_for_channel(ch):
    """
    Get bank ID for a given temperature channel.
    
    Args:
        ch (int): Channel number (1-24)
    
    Returns:
        int: Bank ID (1-3) or None if invalid channel
    """
    for bank_id, (start, end) in enumerate(BANK_RANGES, 1):
        if start <= ch <= end:
            return bank_id
    return None

def modbus_crc(data):
    """
    Calculate Modbus CRC for data integrity.
    
    Args:
        data (bytes): Data to calculate CRC for
    
    Returns:
        bytes: 2-byte CRC value in little-endian order
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

def read_ntc_sensors(ip, modbus_port, query_delay, num_channels, scaling_factor, max_retries, retry_backoff_base):
    """
    Read NTC sensor temperatures via Modbus over TCP with retries.
    
    Args:
        ip (str): IP address of EDS4100 device
        modbus_port (int): Port number for Modbus TCP
        query_delay (float): Delay between query and response
        num_channels (int): Number of temperature channels
        scaling_factor (float): Scaling factor to convert raw to °C
        max_retries (int): Maximum number of retry attempts
        retry_backoff_base (int): Base for exponential backoff
    
    Returns:
        list: List of temperature readings or error message string
    """
    logging.info("Starting temperature sensor read.")
    # Construct Modbus query
    query_base = bytes([1, 3]) + (0).to_bytes(2, 'big') + (num_channels).to_bytes(2, 'big')
    crc = modbus_crc(query_base)
    query = query_base + crc
    
    # Retry loop with exponential backoff
    for attempt in range(max_retries):
        try:
            logging.debug(f"Temp read attempt {attempt+1}: Connecting to {ip}:{modbus_port}")
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(3)
            s.connect((ip, modbus_port))
            s.send(query)
            time.sleep(query_delay)
            response = s.recv(1024)
            s.close()
            
            # Validate response length
            if len(response) < 5:
                raise ValueError("Short response")
            
            # Validate response structure
            if len(response) != 3 + response[2] + 2:
                raise ValueError("Invalid response length")
            
            # Validate CRC
            calc_crc = modbus_crc(response[:-2])
            if calc_crc != response[-2:]:
                raise ValueError("CRC mismatch")
            
            # Parse response header
            slave, func, byte_count = response[0:3]
            if slave != 1 or func != 3 or byte_count != num_channels * 2:
                if func & 0x80:
                    return f"Error: Modbus exception code {response[2]}"
                return "Error: Invalid response header."
            
            # Extract temperature data
            data = response[3:3 + byte_count]
            raw_temperatures = []
            for i in range(0, len(data), 2):
                val = int.from_bytes(data[i:i+2], 'big', signed=True) / scaling_factor
                raw_temperatures.append(val)
            
            logging.info("Temperature read successful.")
            return raw_temperatures
        
        except socket.error as e:
            logging.warning(f"Temp read attempt {attempt+1} failed: {str(e)}. Retrying.")
            if attempt < max_retries - 1:
                time.sleep(retry_backoff_base ** attempt)
            else:
                logging.error(f"Temp read failed after {max_retries} attempts - {str(e)}.")
                return f"Error: Failed after {max_retries} attempts - {str(e)}."
        except ValueError as e:
            logging.warning(f"Temp read attempt {attempt+1} failed (validation): {str(e)}. Retrying.")
            if attempt < max_retries - 1:
                time.sleep(retry_backoff_base ** attempt)
            else:
                logging.error(f"Temp read failed after {max_retries} attempts - {str(e)}.")
                return f"Error: Failed after {max_retries} attempts - {str(e)}."
        except Exception as e:
            logging.error(f"Unexpected error in temp read attempt {attempt+1}: {str(e)}\n{traceback.format_exc()}")
            return f"Error: Unexpected failure - {str(e)}"

def load_config():
    """
    Load settings from 'battery_monitor.ini' with fallback values.
    
    Returns:
        dict: Combined dictionary of all configuration settings
    
    Raises:
        FileNotFoundError: If config file is not found
    """
    logging.info("Loading configuration from 'battery_monitor.ini'.")
    global alert_states
    
    # Read config file
    if not config_parser.read('battery_monitor.ini'):
        logging.error("Config file 'battery_monitor.ini' not found.")
        raise FileNotFoundError("Config file 'battery_monitor.ini' not found.")
    
    # Temperature settings section
    temp_settings = {
        'ip': config_parser.get('Temp', 'ip', fallback='192.168.15.240'),
        'modbus_port': config_parser.getint('Temp', 'modbus_port', fallback=10001),
        'poll_interval': config_parser.getfloat('Temp', 'poll_interval', fallback=10.0),
        'rise_threshold': config_parser.getfloat('Temp', 'rise_threshold', fallback=2.0),
        'deviation_threshold': config_parser.getfloat('Temp', 'deviation_threshold', fallback=0.1),
        'disconnection_lag_threshold': config_parser.getfloat('Temp', 'disconnection_lag_threshold', fallback=0.5),
        'high_threshold': config_parser.getfloat('Temp', 'high_threshold', fallback=60.0),
        'low_threshold': config_parser.getfloat('Temp', 'low_threshold', fallback=0.0),
        'scaling_factor': config_parser.getfloat('Temp', 'scaling_factor', fallback=100.0),
        'valid_min': config_parser.getfloat('Temp', 'valid_min', fallback=0.0),
        'max_retries': config_parser.getint('Temp', 'max_retries', fallback=3),
        'retry_backoff_base': config_parser.getint('Temp', 'retry_backoff_base', fallback=1),
        'query_delay': config_parser.getfloat('Temp', 'query_delay', fallback=0.25),
        'num_channels': config_parser.getint('Temp', 'num_channels', fallback=24),
        'abs_deviation_threshold': config_parser.getfloat('Temp', 'abs_deviation_threshold', fallback=2.0)
    }
    
    # Voltage and balancing settings section
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
    
    # General flags section
    general_flags = {
        'WebInterfaceEnabled': config_parser.getboolean('General', 'WebInterfaceEnabled', fallback=True),
        'StartupSelfTestEnabled': config_parser.getboolean('General', 'StartupSelfTestEnabled', fallback=True)
    }
    
    # I2C device addresses section
    i2c_settings = {
        'MultiplexerAddress': int(config_parser.get('I2C', 'MultiplexerAddress', fallback='0x70'), 16),
        'VoltageMeterAddress': int(config_parser.get('I2C', 'VoltageMeterAddress', fallback='0x49'), 16),
        'RelayAddress': int(config_parser.get('I2C', 'RelayAddress', fallback='0x26'), 16)
    }
    
    # GPIO pin settings section
    gpio_settings = {
        'DC_DC_RelayPin': config_parser.getint('GPIO', 'DC_DC_RelayPin', fallback=17),
        'AlarmRelayPin': config_parser.getint('GPIO', 'AlarmRelayPin', fallback=27)
    }
    
    # Email alert settings section
    email_settings = {
        'SMTP_Server': config_parser.get('Email', 'SMTP_Server', fallback='smtp.gmail.com'),
        'SMTP_Port': config_parser.getint('Email', 'SMTP_Port', fallback=587),
        'SenderEmail': config_parser.get('Email', 'SenderEmail', fallback='your_email@gmail.com'),
        'RecipientEmail': config_parser.get('Email', 'RecipientEmail', fallback='recipient@example.com'),
        'SMTP_Username': config_parser.get('Email', 'SMTP_Username', fallback='your_email@gmail.com'),
        'SMTP_Password': config_parser.get('Email', 'SMTP_Password', fallback='your_app_password')
    }
    
    # ADC configuration settings section
    adc_settings = {
        'ConfigRegister': int(config_parser.get('ADC', 'ConfigRegister', fallback='0x01'), 16),
        'ConversionRegister': int(config_parser.get('ADC', 'ConversionRegister', fallback='0x00'), 16),
        'ContinuousModeConfig': int(config_parser.get('ADC', 'ContinuousModeConfig', fallback='0x0100'), 16),
        'SampleRateConfig': int(config_parser.get('ADC', 'SampleRateConfig', fallback='0x0080'), 16),
        'GainConfig': int(config_parser.get('ADC', 'GainConfig', fallback='0x0400'), 16)
    }
    
    # Voltage sensor calibration section
    calibration_settings = {
        'Sensor1_Calibration': config_parser.getfloat('Calibration', 'Sensor1_Calibration', fallback=0.99856),
        'Sensor2_Calibration': config_parser.getfloat('Calibration', 'Sensor2_Calibration', fallback=0.99856),
        'Sensor3_Calibration': config_parser.getfloat('Calibration', 'Sensor3_Calibration', fallback=0.99809)
    }
    
    # Startup self-test settings section
    startup_settings = {
        'test_balance_duration': config_parser.getint('Startup', 'test_balance_duration', fallback=15),
        'min_voltage_delta': config_parser.getfloat('Startup', 'min_voltage_delta', fallback=0.01),
        'test_read_interval': config_parser.getfloat('Startup', 'test_read_interval', fallback=2.0)
    }
    
    # Web interface settings section
    web_settings = {
        'host': config_parser.get('Web', 'host', fallback='0.0.0.0'),
        'web_port': config_parser.getint('Web', 'web_port', fallback=8080),
        'auth_required': config_parser.getboolean('Web', 'auth_required', fallback=False),
        'username': config_parser.get('Web', 'username', fallback='admin'),
        'password': config_parser.get('Web', 'password', fallback='admin123'),
        'api_enabled': config_parser.getboolean('Web', 'api_enabled', fallback=True),
        'cors_enabled': config_parser.getboolean('Web', 'cors_enabled', fallback=True),
        'cors_origins': config_parser.get('Web', 'cors_origins', fallback='*')
    }
    
    # Set logging level dynamically based on config
    log_level = getattr(logging, voltage_settings['LoggingLevel'].upper(), logging.INFO)
    logging.getLogger().setLevel(log_level)
    
    # Initialize alert states for all channels
    alert_states = {ch: {'last_type': None, 'count': 0} for ch in range(1, temp_settings['num_channels'] + 1)}
    
    logging.info("Configuration loaded successfully.")
    
    # Combine all settings into single dictionary
    return {**temp_settings, **voltage_settings, **general_flags, **i2c_settings, 
            **gpio_settings, **email_settings, **adc_settings, **calibration_settings, 
            **startup_settings, **web_settings}

def setup_hardware(settings):
    """
    Initialize I2C bus and GPIO pins for hardware communication.
    
    Args:
        settings (dict): Configuration settings dictionary
    """
    global bus
    logging.info("Setting up hardware.")
    
    # Initialize I2C bus if available
    if smbus:
        bus = smbus.SMBus(settings['I2C_BusNumber'])
    else:
        logging.warning("smbus not available - running in test mode")
        bus = None
    
    # Initialize GPIO if available
    if GPIO:
        GPIO.setmode(GPIO.BCM)
        GPIO.setup(settings['DC_DC_RelayPin'], GPIO.OUT, initial=GPIO.LOW)
        GPIO.setup(settings['AlarmRelayPin'], GPIO.OUT, initial=GPIO.LOW)
    else:
        logging.warning("RPi.GPIO not available - running in test mode")
    
    logging.info("Hardware setup complete.")

def signal_handler(sig, frame):
    """
    Handle SIGINT (Ctrl+C) for graceful shutdown.
    
    Args:
        sig: Signal number
        frame: Current stack frame
    """
    logging.info("Script stopped by user or signal.")
    global web_server
    
    # Shutdown web server if running
    if web_server:
        web_server.shutdown()
    
    # Clean up GPIO
    if GPIO:
        GPIO.cleanup()
    
    sys.exit(0)

def load_offsets(num_channels):
    """
    Load temperature offsets from file if exists, for dynamic num_channels.
    
    Args:
        num_channels (int): Number of expected temperature channels
    
    Returns:
        tuple: (startup_median, offsets) or (None, None) if file doesn't exist or is invalid
    """
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
            
            # Validate offsets count matches num_channels
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
    """
    Save temperature median and offsets to file.
    
    Args:
        startup_median (float): Median temperature value
        startup_offsets (list): List of temperature offsets per channel
    """
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
    """
    Check if temperature reading is invalid.
    
    Args:
        raw (float): Raw temperature reading
        ch (int): Channel number
        alerts (list): List to append alerts to
        valid_min (float): Minimum valid temperature
    
    Returns:
        bool: True if reading is invalid, False otherwise
    """
    if raw <= valid_min:
        bank = get_bank_for_channel(ch)
        alerts.append(f"Bank {bank} Ch {ch}: Invalid reading (≤ {valid_min}).")
        logging.warning(f"Invalid reading on Bank {bank} Ch {ch}: {raw} ≤ {valid_min}.")
        return True
    return False

def check_high_temp(calibrated, ch, alerts, high_threshold):
    """
    Check if temperature is above high threshold.
    
    Args:
        calibrated (float): Calibrated temperature
        ch (int): Channel number
        alerts (list): List to append alerts to
        high_threshold (float): High temperature threshold
    """
    if calibrated > high_threshold:
        bank = get_bank_for_channel(ch)
        alerts.append(f"Bank {bank} Ch {ch}: High temp ({calibrated:.1f}°C > {high_threshold}°C).")
        logging.warning(f"High temp alert on Bank {bank} Ch {ch}: {calibrated:.1f} > {high_threshold}.")

def check_low_temp(calibrated, ch, alerts, low_threshold):
    """
    Check if temperature is below low threshold.
    
    Args:
        calibrated (float): Calibrated temperature
        ch (int): Channel number
        alerts (list): List to append alerts to
        low_threshold (float): Low temperature threshold
    """
    if calibrated < low_threshold:
        bank = get_bank_for_channel(ch)
        alerts.append(f"Bank {bank} Ch {ch}: Low temp ({calibrated:.1f}°C < {low_threshold}°C).")
        logging.warning(f"Low temp alert on Bank {bank} Ch {ch}: {calibrated:.1f} < {low_threshold}.")

def check_deviation(calibrated, bank_median, ch, alerts, abs_deviation_threshold, deviation_threshold):
    """
    Check if temperature deviates significantly from bank median.
    
    Args:
        calibrated (float): Calibrated temperature
        bank_median (float): Bank median temperature
        ch (int): Channel number
        alerts (list): List to append alerts to
        abs_deviation_threshold (float): Absolute deviation threshold
        deviation_threshold (float): Relative deviation threshold
    """
    abs_dev = abs(calibrated - bank_median)
    rel_dev = abs_dev / abs(bank_median) if bank_median != 0 else 0
    
    if abs_dev > abs_deviation_threshold or rel_dev > deviation_threshold:
        bank = get_bank_for_channel(ch)
        alerts.append(f"Bank {bank} Ch {ch}: Deviation from bank median (abs {abs_dev:.1f}°C or {rel_dev:.2%}).")
        logging.warning(f"Deviation alert on Bank {bank} Ch {ch}: abs {abs_dev:.1f}, rel {rel_dev:.2%}.")

def check_abnormal_rise(current, previous_temps, ch, alerts, poll_interval, rise_threshold):
    """
    Check for abnormal temperature rise between polls.
    
    Args:
        current (float): Current temperature
        previous_temps (list): Previous temperature readings
        ch (int): Channel number
        alerts (list): List to append alerts to
        poll_interval (float): Time between polls
        rise_threshold (float): Rise threshold
    """
    previous = previous_temps[ch-1]
    if previous is not None:
        rise = current - previous
        if rise > rise_threshold:
            bank = get_bank_for_channel(ch)
            alerts.append(f"Bank {bank} Ch {ch}: Abnormal rise ({rise:.1f}°C in {poll_interval}s).")
            logging.warning(f"Abnormal rise alert on Bank {bank} Ch {ch}: {rise:.1f}°C.")

def check_group_tracking_lag(current, previous_temps, bank_median_rise, ch, alerts, disconnection_lag_threshold):
    """
    Check if temperature is lagging behind bank group changes.
    
    Args:
        current (float): Current temperature
        previous_temps (list): Previous temperature readings
        bank_median_rise (float): Bank median temperature rise
        ch (int): Channel number
        alerts (list): List to append alerts to
        disconnection_lag_threshold (float): Lag threshold
    """
    previous = previous_temps[ch-1]
    if previous is not None:
        rise = current - previous
        if abs(rise - bank_median_rise) > disconnection_lag_threshold:
            bank = get_bank_for_channel(ch)
            alerts.append(f"Bank {bank} Ch {ch}: Lag from bank group ({rise:.1f}°C vs {bank_median_rise:.1f}°C).")
            logging.warning(f"Lag alert on Bank {bank} Ch {ch}: rise {rise:.1f} vs median {bank_median_rise:.1f}.")

def check_sudden_disconnection(current, previous_temps, ch, alerts):
    """
    Check for sudden sensor disconnection.
    
    Args:
        current: Current temperature reading
        previous_temps (list): Previous temperature readings
        ch (int): Channel number
        alerts (list): List to append alerts to
    """
    previous = previous_temps[ch-1]
    if previous is not None and current is None:
        bank = get_bank_for_channel(ch)
        alerts.append(f"Bank {bank} Ch {ch}: Sudden disconnection.")
        logging.warning(f"Sudden disconnection alert on Bank {bank} Ch {ch}.")

def choose_channel(channel, multiplexer_address):
    """
    Select I2C channel on multiplexer.
    
    Args:
        channel (int): Channel number to select
        multiplexer_address (int): I2C address of multiplexer
    """
    logging.debug(f"Switching to I2C channel {channel}.")
    if bus:
        try:
            bus.write_byte(multiplexer_address, 1 << channel)
        except IOError as e:
            logging.error(f"I2C error selecting channel {channel}: {str(e)}")

def setup_voltage_meter(settings):
    """
    Configure voltage meter ADC settings.
    
    Args:
        settings (dict): Configuration settings
    """
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
    """
    Read voltage from specified bank with retry logic.
    
    Args:
        bank_id (int): Bank number (1-3)
        settings (dict): Configuration settings
    
    Returns:
        tuple: (average_voltage, valid_readings, raw_adc_values) or (None, [], []) on failure
    """
    logging.info(f"Starting voltage read for Bank {bank_id}.")
    voltage_divider_ratio = settings['VoltageDividerRatio']
    sensor_id = bank_id
    calibration_factor = settings[f'Sensor{sensor_id}_Calibration']
    
    # Two attempts to get good reading
    for attempt in range(2):
        logging.debug(f"Voltage read attempt {attempt+1} for Bank {bank_id}.")
        readings = []
        raw_values = []
        
        # Take two readings for consistency check
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
                # Mock reading for testing
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
        
        # Check for consistent readings
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
    """
    Set relay connections for balancing between banks.
    
    Args:
        high (int): High voltage bank number
        low (int): Low voltage bank number
        settings (dict): Configuration settings
    """
    try:
        logging.info(f"Attempting to set relay for connection from Bank {high} to {low}")
        logging.debug("Switching to relay control channel.")
        choose_channel(3, settings['MultiplexerAddress'])
        
        relay_state = 0
        # Define relay patterns for each bank combination
        if high == 1 and low == 2:
            relay_state |= (1 << 0) | (1 << 1) | (1 << 3)
            logging.debug("Relays 1, 2, and 4 activated for high to low.")
        elif high == 1 and low == 3:
            relay_state |= (1 << 1) | (1 << 2) | (1 << 3)
            logging.debug("Relays 2, 3, and 4 activated for high to low.")
        elif high == 2 and low == 1:
            relay_state |= (1 << 0) | (1 << 2) | (1 << 3)
            logging.debug("Relays 1, 3, and 4 activated for high to low.")
        elif high == 2 and low == 3:
            relay_state |= (1 << 0) | (1 << 1) | (1 << 2)
            logging.debug("Relays 1, 2, and 3 activated for high to low.")
        elif high == 3 and low == 1:
            relay_state |= (1 << 0) | (1 << 1) | (1 << 2)
            logging.debug("Relays 1, 2, and 3 activated for high to low.")
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
    """
    Control DC-DC converter relay.
    
    Args:
        turn_on (bool): True to turn on, False to turn off
        settings (dict): Configuration settings
    """
    try:
        if GPIO:
            GPIO.output(settings['DC_DC_RelayPin'], GPIO.HIGH if turn_on else GPIO.LOW)
        logging.info(f"DC-DC Converter is now {'on' if turn_on else 'off'}")
    except Exception as e:
        logging.error(f"Problem controlling DC-DC converter: {e}")

def send_alert_email(message, settings):
    """
    Send alert email with throttling to prevent spam.
    
    Args:
        message (str): Email message content
        settings (dict): Configuration settings
    """
    global last_email_time
    
    # Throttle email sending
    if time.time() - last_email_time < settings['EmailAlertIntervalSeconds']:
        logging.debug("Skipping alert email to avoid flooding.")
        return
    
    try:
        # Create email message
        msg = MIMEText(message)
        msg['Subject'] = "Battery Monitor Alert"
        msg['From'] = settings['SenderEmail']
        msg['To'] = settings['RecipientEmail']
        
        # Send email
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
    """
    Check for voltage and temperature issues and trigger alerts.
    
    Args:
        voltages (list): List of bank voltages
        temps_alerts (list): List of temperature alerts
        settings (dict): Configuration settings
    
    Returns:
        tuple: (alert_needed, alerts_list)
    """
    global startup_failed, startup_alerts
    logging.info("Checking for voltage and temp issues.")
    
    alert_needed = startup_failed
    alerts = []
    
    # Add startup failures to alerts
    if startup_failed and startup_alerts:
        alerts.append("Startup failures: " + "; ".join(startup_alerts))
    
    # Check voltage issues
    for i, v in enumerate(voltages, 1):
        if v is None or v == 0.0:
            alerts.append(f"Bank {i}: Zero voltage.")
            logging.warning(f"Zero voltage alert on Bank {i}.")
            alert_needed = True
        elif v > settings['HighVoltageThresholdPerBattery']:
            alerts.append(f"Bank {i}: High voltage ({v:.2f}V).")
            logging.warning(f"High voltage alert on Bank {i}: {v:.2f}V.")
            alert_needed = True
        elif v < settings['LowVoltageThresholdPerBattery']:
            alerts.append(f"Bank {i}: Low voltage ({v:.2f}V).")
            logging.warning(f"Low voltage alert on Bank {i}: {v:.2f}V.")
            alert_needed = True
    
    # Add temperature alerts
    if temps_alerts:
        alerts.extend(temps_alerts)
        alert_needed = True
    
    # Control alarm relay
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
    """
    Perform battery balancing operation between specified banks.
    
    Args:
        stdscr: Curses screen object
        high (int): High voltage bank number
        low (int): Low voltage bank number
        settings (dict): Configuration settings
        temps_alerts (list): List of temperature alerts
    """
    global balance_start_time, last_balance_time, balancing_active, web_data
    
    # Skip balancing if temperature anomalies exist
    if temps_alerts:
        logging.warning("Skipping balancing due to temperature anomalies in banks.")
        return
    
    logging.info(f"Starting balance from Bank {high} to {low}.")
    balancing_active = True
    web_data['balancing'] = True
    
    # Get current voltages
    voltage_high, _, _ = read_voltage_with_retry(high, settings)
    voltage_low, _, _ = read_voltage_with_retry(low, settings)
    
    # Safety check
    if voltage_low == 0.0:
        logging.warning(f"Cannot balance to Bank {low} (0.00V). Skipping.")
        balancing_active = False
        web_data['balancing'] = False
        return
    
    # Setup relays and start balancing
    set_relay_connection(high, low, settings)
    control_dcdc_converter(True, settings)
    balance_start_time = time.time()
    
    # Animation frames for progress display
    animation_frames = ['|', '/', '-', '\\']
    frame_index = 0
    progress_y = 17 + 6 + 2
    height, _ = stdscr.getmaxyx()
    
    # Balancing loop
    while time.time() - balance_start_time < settings['BalanceDurationSeconds']:
        elapsed = time.time() - balance_start_time
        progress = min(1.0, elapsed / settings['BalanceDurationSeconds'])
        
        # Read current voltages
        voltage_high, _, _ = read_voltage_with_retry(high, settings)
        voltage_low, _, _ = read_voltage_with_retry(low, settings)
        
        # Display progress bar
        bar_length = 20
        filled = int(bar_length * progress)
        bar = '=' * filled + ' ' * (bar_length - filled)
        
        # Update display if within screen bounds
        if progress_y < height and progress_y + 1 < height:
            try:
                stdscr.addstr(progress_y, 0, f"Balancing Bank {high} ({voltage_high:.2f}V) -> Bank {low} ({voltage_low:.2f}V)... [{animation_frames[frame_index % 4]}]", curses.color_pair(6))
            except curses.error:
                logging.warning("addstr error for balancing status.")
            try:
                stdscr.addstr(progress_y + 1, 0, f"Progress: [{bar}] {int(progress * 100)}%", curses.color_pair(6))
            except curses.error:
                logging.warning("addstr error for balancing progress bar.")
        else:
            logging.warning("Skipping balancing progress display - out of bounds.")
        
        stdscr.refresh()
        logging.debug(f"Balancing progress: {progress * 100:.2f}%, High: {voltage_high:.2f}V, Low: {voltage_low:.2f}V")
        frame_index += 1
        time.sleep(0.01)
    
    # Cleanup after balancing
    logging.info("Balancing process completed.")
    control_dcdc_converter(False, settings)
    logging.info("Turning off DC-DC converter.")
    set_relay_connection(0, 0, settings)
    logging.info("Resetting relay connections to default state.")
    
    balancing_active = False
    web_data['balancing'] = False
    last_balance_time = time.time()

def compute_bank_medians(calibrated_temps, valid_min):
    """
    Compute median temperatures for each bank.
    
    Args:
        calibrated_temps (list): List of calibrated temperatures
        valid_min (float): Minimum valid temperature
    
    Returns:
        list: List of bank median temperatures
    """
    bank_medians = []
    for start, end in BANK_RANGES:
        bank_temps = [calibrated_temps[i-1] for i in range(start, end+1) if calibrated_temps[i-1] is not None]
        bank_median = statistics.median(bank_temps) if bank_temps else 0.0
        bank_medians.append(bank_median)
    return bank_medians

def draw_tui(stdscr, voltages, calibrated_temps, raw_temps, offsets, bank_medians, startup_median, alerts, settings, startup_set, is_startup):
    """
    Draw the Text User Interface with battery status and alerts.
    
    Args:
        stdscr: Curses screen object
        voltages (list): List of bank voltages
        calibrated_temps (list): List of calibrated temperatures
        raw_temps (list): List of raw temperature readings
        offsets (list): List of temperature offsets
        bank_medians (list): List of bank median temperatures
        startup_median (float): Startup median temperature
        alerts (list): List of active alerts
        settings (dict): Configuration settings
        startup_set (bool): Whether startup calibration is set
        is_startup (bool): Whether this is the startup display
    """
    logging.debug("Refreshing TUI.")
    stdscr.clear()
    
    # Initialize color pairs
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
    
    # Display total voltage as ASCII art
    total_v = sum(voltages)
    total_high = settings['HighVoltageThresholdPerBattery'] * NUM_BANKS
    total_low = settings['LowVoltageThresholdPerBattery'] * NUM_BANKS
    v_color = curses.color_pair(2) if total_v > total_high else curses.color_pair(3) if total_v < total_low else curses.color_pair(4)
    
    roman_v = text2art(f"{total_v:.2f}V", font='roman', chr_ignore=True)
    roman_lines = roman_v.splitlines()
    
    for i, line in enumerate(roman_lines):
        if i + 1 < height and len(line) < width:
            try:
                stdscr.addstr(i + 1, 0, line, v_color)
            except curses.error:
                logging.warning(f"addstr error for total voltage art line {i+1}.")
        else:
            logging.warning(f"Skipping total voltage art line {i+1} - out of bounds.")
    
    y_offset = len(roman_lines) + 2
    if y_offset >= height:
        logging.warning("TUI y_offset exceeds height; skipping art.")
        return
    
    # Battery art template
    battery_art_base = [
        " ___________ ",
        " | | ",
        " | | ",
        " | | ",
        " | | ",
        " | +++ | ",
        " | +++ | ",
        " | | ",
        " | | ",
        " | | ",
        " | | ",
        " | --- | ",
        " | --- | ",
        " | --- | ",
        " | | ",
        " | | ",
        " |___________| "
    ]
    
    art_height = len(battery_art_base)
    art_width = len(battery_art_base[0])
    
    # Draw battery art for each bank
    for row, line in enumerate(battery_art_base):
        full_line = line * NUM_BANKS
        if y_offset + row < height and len(full_line) < width:
            try:
                stdscr.addstr(y_offset + row, 0, full_line, curses.color_pair(4))
            except curses.error:
                logging.warning(f"addstr error for art row {row}.")
        else:
            logging.warning(f"Skipping art row {row} - out of bounds.")
    
    # Add voltage and temperature data to battery display
    for bank_id in range(NUM_BANKS):
        start_pos = bank_id * art_width
        
        # Display voltage
        v_str = f"{voltages[bank_id]:.2f}V" if voltages[bank_id] > 0 else "0.00V"
        v_color = curses.color_pair(8) if voltages[bank_id] == 0.0 else \
                 curses.color_pair(2) if voltages[bank_id] > settings['HighVoltageThresholdPerBattery'] else \
                 curses.color_pair(3) if voltages[bank_id] < settings['LowVoltageThresholdPerBattery'] else \
                 curses.color_pair(4)
        
        v_center = start_pos + (art_width - len(v_str)) // 2
        v_y = y_offset + 1
        
        if v_y < height and v_center + len(v_str) < width:
            try:
                stdscr.addstr(v_y, v_center, v_str, v_color)
            except curses.error:
                logging.warning(f"addstr error for voltage overlay Bank {bank_id+1}.")
        else:
            logging.warning(f"Skipping voltage overlay for Bank {bank_id+1} - out of bounds.")
        
        # Display temperatures
        start, end = BANK_RANGES[bank_id]
        for local_ch, ch in enumerate(range(start, end + 1), 0):
            idx = ch - 1
            raw = raw_temps[idx] if idx < len(raw_temps) else 0
            calib = calibrated_temps[idx]
            calib_str = f"{calib:.1f}" if calib is not None else "Inv"
            
            if is_startup:
                raw_str = f"{raw:.1f}" if raw > settings['valid_min'] else "Inv"
                offset_str = f"{offsets[idx]:.1f}" if startup_set and raw > settings['valid_min'] else "N/A"
                detail = f" ({raw_str}/{offset_str})"
            else:
                detail = ""
            
            t_str = f"C{local_ch+1}: {calib_str}{detail}"
            t_color = curses.color_pair(8) if "Inv" in calib_str else \
                     curses.color_pair(2) if calib > settings['high_threshold'] else \
                     curses.color_pair(3) if calib < settings['low_threshold'] else \
                     curses.color_pair(4)
            
            t_center = start_pos + (art_width - len(t_str)) // 2
            t_y = y_offset + 2 + local_ch
            
            if t_y < height and t_center + len(t_str) < width:
                try:
                    stdscr.addstr(t_y, t_center, t_str, t_color)
                except curses.error:
                    logging.warning(f"addstr error for temp overlay Bank {bank_id+1} C{local_ch+1}.")
            else:
                logging.warning(f"Skipping temp overlay for Bank {bank_id+1} C{local_ch+1} - out of bounds.")
        
        # Display bank median temperature
        med_str = f"Med: {bank_medians[bank_id]:.1f}°C"
        med_center = start_pos + (art_width - len(med_str)) // 2
        med_y = y_offset + 15
        
        if med_y < height and med_center + len(med_str) < width:
            try:
                stdscr.addstr(med_y, med_center, med_str, curses.color_pair(7))
            except curses.error:
                logging.warning(f"addstr error for median overlay Bank {bank_id+1}.")
        else:
            logging.warning(f"Skipping median overlay for Bank {bank_id+1} - out of bounds.")
    
    y_offset += art_height + 2
    
    # Display ADC readings if space available
    if y_offset >= height:
        logging.warning("Skipping ADC/readings - out of bounds.")
    else:
        for i in range(1, NUM_BANKS + 1):
            voltage, readings, adc_values = read_voltage_with_retry(i, settings)
            logging.debug(f"Bank {i} - Voltage: {voltage}, ADC: {adc_values}, Readings: {readings}")
            
            if voltage is None:
                voltage = 0.0
            
            if y_offset < height:
                try:
                    stdscr.addstr(y_offset, 0, f"Bank {i}: (ADC: {adc_values[0] if adc_values else 'N/A'})", curses.color_pair(5))
                except curses.error:
                    logging.warning(f"addstr error for ADC Bank {i}.")
            else:
                logging.warning(f"Skipping ADC for Bank {i} - out of bounds.")
            
            y_offset += 1
            
            if y_offset < height:
                try:
                    if readings:
                        stdscr.addstr(y_offset, 0, f"[Readings: {', '.join(f'{v:.2f}' for v in readings)}]", curses.color_pair(5))
                    else:
                        stdscr.addstr(y_offset, 0, " [Readings: No data]", curses.color_pair(5))
                except curses.error:
                    logging.warning(f"addstr error for readings Bank {i}.")
            else:
                logging.warning(f"Skipping readings for Bank {i} - out of bounds.")
            
            y_offset += 1
    
    y_offset += 1
    
    # Display startup median temperature
    med_str = f"{startup_median:.1f}°C" if startup_median else "N/A"
    if y_offset < height:
        try:
            stdscr.addstr(y_offset, 0, f"Startup Median Temp: {med_str}", curses.color_pair(7))
        except curses.error:
            logging.warning("addstr error for startup median.")
    else:
        logging.warning("Skipping startup median - out of bounds.")
    
    y_offset += 2
    
    # Display alerts section
    if y_offset < height:
        try:
            stdscr.addstr(y_offset, 0, "Alerts:", curses.color_pair(7))
        except curses.error:
            logging.warning("addstr error for alerts header.")
    
    y_offset += 1
    
    # Display individual alerts
    if alerts:
        for alert in alerts:
            if y_offset < height:
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
    
    stdscr.refresh()

def startup_self_test(settings, stdscr):
    """
    Perform comprehensive startup self-test of hardware and functionality with verbose logging.
    
    Args:
        settings (dict): Configuration settings
        stdscr: Curses screen object
    
    Returns:
        list: List of alert messages from failed tests
    """
    global startup_failed, startup_alerts, startup_set, startup_median, startup_offsets
    
    # Skip if disabled in config
    if not settings['StartupSelfTestEnabled']:
        logging.info("Startup self-test disabled via configuration.")
        return []
    
    logging.info("Starting self-test: Validating config, connectivity, sensors, and balancer.")
    alerts = []
    stdscr.clear()
    y = 0
    
    # Display test title
    if y < stdscr.getmaxyx()[0]:
        try:
            stdscr.addstr(y, 0, "Startup Self-Test in Progress", curses.color_pair(1))
        except curses.error:
            logging.warning("addstr error for title.")
    
    y += 2
    stdscr.refresh()
    
    # Step 1: Config validation
    logging.info("Step 1: Validating configuration parameters.")
    logging.debug(f"Configuration details: NumberOfBatteries={settings['NumberOfBatteries']}, "
                  f"I2C_BusNumber={settings['I2C_BusNumber']}, "
                  f"MultiplexerAddress=0x{settings['MultiplexerAddress']:02x}, "
                  f"VoltageMeterAddress=0x{settings['VoltageMeterAddress']:02x}, "
                  f"RelayAddress=0x{settings['RelayAddress']:02x}, "
                  f"Temp_IP={settings['ip']}, Temp_Port={settings['modbus_port']}, "
                  f"NumChannels={settings['num_channels']}, ScalingFactor={settings['scaling_factor']}")
    
    if y < stdscr.getmaxyx()[0]:
        try:
            stdscr.addstr(y, 0, "Step 1: Validating config...", curses.color_pair(4))
        except curses.error:
            logging.warning("addstr error for step 1.")
    
    stdscr.refresh()
    time.sleep(0.5)
    
    # Check config consistency
    if settings['NumberOfBatteries'] != NUM_BANKS:
        alerts.append(f"Config mismatch: NumberOfBatteries={settings['NumberOfBatteries']} != {NUM_BANKS}.")
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
    
    # Step 2: Hardware connectivity
    logging.info("Step 2: Testing hardware connectivity (I2C and Modbus).")
    if y < stdscr.getmaxyx()[0]:
        try:
            stdscr.addstr(y, 0, "Step 2: Testing hardware connectivity...", curses.color_pair(4))
        except curses.error:
            logging.warning("addstr error for step 2.")
    
    stdscr.refresh()
    time.sleep(0.5)
    
    # Test I2C connectivity
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
            logging.debug(f"Reading byte from Relay at 0x{settings['RelayAddress']:02x}")
            bus.read_byte(settings['RelayAddress'])
            logging.debug("I2C connectivity test passed for all devices.")
        
        if y + 1 < stdscr.getmaxyx()[0]:
            try:
                stdscr.addstr(y + 1, 0, "I2C OK.", curses.color_pair(4))
            except curses.error:
                logging.warning("addstr error for I2C OK.")
    except (IOError, AttributeError) as e:
        alerts.append(f"I2C connectivity failure: {str(e)}")
        logging.error(f"I2C connectivity failure: {str(e)}. Bus={settings['I2C_BusNumber']}, "
                      f"Multiplexer=0x{settings['MultiplexerAddress']:02x}, "
                      f"VoltageMeter=0x{settings['VoltageMeterAddress']:02x}, "
                      f"Relay=0x{settings['RelayAddress']:02x}")
        if y + 1 < stdscr.getmaxyx()[0]:
            try:
                stdscr.addstr(y + 1, 0, f"I2C failure: {str(e)}", curses.color_pair(2))
            except curses.error:
                logging.warning("addstr error for I2C failure.")

    # Test Modbus connectivity
    logging.debug(f"Testing Modbus connectivity to {settings['ip']}:{settings['modbus_port']} with "
                  f"num_channels=1, query_delay={settings['query_delay']}, scaling_factor={settings['scaling_factor']}")
    try:
        test_query = read_ntc_sensors(settings['ip'], settings['modbus_port'], settings['query_delay'], 1, settings['scaling_factor'], 1, 1)
        if isinstance(test_query, str) and "Error" in test_query:
            raise ValueError(test_query)
        logging.debug(f"Modbus test successful: Received {len(test_query)} values: {test_query}")

        if y + 2 < stdscr.getmaxyx()[0]:
            try:
                stdscr.addstr(y + 2, 0, "Modbus OK.", curses.color_pair(4))
            except curses.error:
                logging.warning("addstr error for Modbus OK.")
    except Exception as e:
        alerts.append(f"Modbus test failure: {str(e)}")
        logging.error(f"Modbus test failure: {str(e)}. Connection={settings['ip']}:{settings['pmodbus_ort']}, "
                      f"num_channels=1, query_delay={settings['query_delay']}, scaling_factor={settings['scaling_factor']}")
        if y + 2 < stdscr.getmaxyx()[0]:
            try:
                stdscr.addstr(y + 2, 0, f"Modbus failure: {str(e)}", curses.color_pair(2))
            except curses.error:
                logging.warning("addstr error for Modbus failure.")

    y += 3
    stdscr.refresh()

    # Step 3: Initial sensor reads
    logging.info("Step 3: Performing initial sensor reads (temperature and voltage).")
    if y < stdscr.getmaxyx()[0]:
        try:
            stdscr.addstr(y, 0, "Step 3: Initial sensor reads...", curses.color_pair(4))
        except curses.error:
            logging.warning("addstr error for step 3.")
    
    stdscr.refresh()
    time.sleep(0.5)
    
    # Test temperature sensor reading
    logging.debug(f"Reading {settings['num_channels']} temperature channels from {settings['ip']}:{settings['modbus_port']} "
                  f"with query_delay={settings['query_delay']}, scaling_factor={settings['scaling_factor']}, "
                  f"max_retries={settings['max_retries']}, retry_backoff_base={settings['retry_backoff_base']}")
    initial_temps = read_ntc_sensors(settings['ip'], settings['modbus_port'], settings['query_delay'], 
                                     settings['num_channels'], settings['scaling_factor'], 
                                     settings['max_retries'], settings['retry_backoff_base'])

    if isinstance(initial_temps, str):
        alerts.append(f"Initial temp read failure: {initial_temps}")
        logging.error(f"Initial temperature read failure: {initial_temps}")
        if y + 1 < stdscr.getmaxyx()[0]:
            try:
                stdscr.addstr(y + 1, 0, "Temp read failure.", curses.color_pair(2))
            except curses.error:
                logging.warning("addstr error for temp failure.")
    else:
        logging.debug(f"Initial temperature read successful: {len(initial_temps)} values, {initial_temps}")
        valid_count = sum(1 for t in initial_temps if t > settings['valid_min'])
        logging.debug(f"Valid temperature readings: {valid_count}/{settings['num_channels']}, valid_min={settings['valid_min']}")
        if y + 1 < stdscr.getmaxyx()[0]:
            try:
                stdscr.addstr(y + 1, 0, "Temps OK.", curses.color_pair(4))
            except curses.error:
                logging.warning("addstr error for temps OK.")

    # Test voltage reading
    logging.debug(f"Reading voltages for {NUM_BANKS} banks with VoltageDividerRatio={settings['VoltageDividerRatio']}")
    initial_voltages = []
    for i in range(1, NUM_BANKS + 1):
        voltage, readings, adc_values = read_voltage_with_retry(i, settings)
        logging.debug(f"Bank {i} voltage read: Voltage={voltage}, Readings={readings}, ADC={adc_values}, "
                      f"CalibrationFactor={settings[f'Sensor{i}_Calibration']}")
        initial_voltages.append(voltage if voltage is not None else 0.0)

    if any(v == 0.0 for v in initial_voltages):
        alerts.append("Initial voltage read failure: Zero voltage on one or more banks.")
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

    # Set up temperature calibration if all readings are valid
    if isinstance(initial_temps, list):
        valid_count = sum(1 for t in initial_temps if t > settings['valid_min'])
        if valid_count == settings['num_channels']:
            startup_median = statistics.median(initial_temps)
            logging.debug(f"Calculated startup median: {startup_median:.1f}°C")
            # Load existing offsets or calculate new ones if offsets.txt missing
            _, startup_offsets = load_offsets(settings['num_channels'])
            if startup_offsets is None:
                startup_offsets = [startup_median - t for t in initial_temps]
                save_offsets(startup_median, startup_offsets)
                logging.info(f"Calculated and saved new offsets on first run: {startup_offsets}")
            else:
                logging.info(f"Using existing offsets from offsets.txt: {startup_offsets}")
            startup_set = True
        else:
            logging.warning(f"Temperature calibration skipped: Only {valid_count}/{settings['num_channels']} valid readings.")
            startup_median = None
            startup_offsets = None
            startup_set = False

    y += 3
    stdscr.refresh()

    # Step 4: Balancer verification (only if no previous failures and valid voltages)
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

        # Test all possible bank balancing combinations
        pairs = [(1,2), (1,3), (2,1), (2,3), (3,1), (3,2)]
        test_duration = settings['test_balance_duration']
        read_interval = settings['test_read_interval']
        min_delta = settings['min_voltage_delta']
        logging.debug(f"Balancer test parameters: test_duration={test_duration}s, "
                      f"read_interval={read_interval}s, min_voltage_delta={min_delta}V")

        for high, low in pairs:
            logging.debug(f"Testing balance: Bank {high} -> {low}")
            if y < stdscr.getmaxyx()[0]:
                try:
                    stdscr.addstr(y, 0, f"Testing balance: Bank {high} -> {low} for {test_duration}s.", curses.color_pair(6))
                except curses.error:
                    logging.warning("addstr error for testing balance.")

            stdscr.refresh()
            logging.info(f"Testing balance: Bank {high} -> {low} for {test_duration}s.")

            # Skip if temperature anomalies exist
            temp_anomaly = False
            if initial_temps and isinstance(initial_temps, list):
                for t in initial_temps:
                    if t > settings['high_threshold'] or t < settings['low_threshold']:
                        temp_anomaly = True
                        break

            if temp_anomaly:
                alerts.append(f"Skipping balance test {high}->{low}: Temp anomalies.")
                logging.warning(f"Skipping balance test {high}->{low}: Temperature anomalies detected.")
                if y + 1 < stdscr.getmaxyx()[0]:
                    try:
                        stdscr.addstr(y + 1, 0, "Skipped: Temp anomalies.", curses.color_pair(2))
                    except curses.error:
                        logging.warning("addstr error for skipped temp.")

                y += 2
                stdscr.refresh()
                continue

            # Start balance test
            set_relay_connection(high, low, settings)
            control_dcdc_converter(True, settings)
            start_time = time.time()

            high_trend = []
            low_trend = []
            progress_y = y + 1

            # Monitor voltage changes during test
            while time.time() - start_time < test_duration:
                time.sleep(read_interval)
                high_v = read_voltage_with_retry(high, settings)[0] or 0.0
                low_v = read_voltage_with_retry(low, settings)[0] or 0.0
                high_trend.append(high_v)
                low_trend.append(low_v)
                logging.debug(f"Balance test {high}->{low}: High={high_v:.2f}V, Low={low_v:.2f}V")

                elapsed = time.time() - start_time
                if progress_y < stdscr.getmaxyx()[0]:
                    try:
                        stdscr.addstr(progress_y, 0, " " * 80, curses.color_pair(6))
                        stdscr.addstr(progress_y, 0, f"Progress: {elapsed:.1f}s, High {high_v:.2f}V, Low {low_v:.2f}V", curses.color_pair(6))
                    except curses.error:
                        logging.warning("addstr error in startup balance progress.")

                stdscr.refresh()

            # Clean up after test
            control_dcdc_converter(False, settings)
            set_relay_connection(0, 0, settings)

            if progress_y + 1 < stdscr.getmaxyx()[0]:
                try:
                    stdscr.addstr(progress_y + 1, 0, "Analyzing...", curses.color_pair(6))
                except curses.error:
                    logging.warning("addstr error for analyzing.")

            stdscr.refresh()

            # Analyze test results
            if len(high_trend) >= 3:
                high_delta = high_trend[0] - high_trend[-1]
                low_delta = low_trend[-1] - low_trend[0]
                logging.debug(f"Balance test {high}->{low} analysis: High Δ={high_delta:.3f}V, Low Δ={low_delta:.3f}V, "
                              f"Min Δ={min_delta}V")

                if high_delta < min_delta or low_delta < min_delta:
                    alerts.append(f"Balance test {high}->{low} failed: Insufficient change (High Δ={high_delta:.3f}V, Low Δ={low_delta:.3f}V).")
                    logging.error(f"Balance test {high}->{low} failed: Insufficient voltage change.")
                    if progress_y + 1 < stdscr.getmaxyx()[0]:
                        try:
                            stdscr.addstr(progress_y + 1, 0, "Test failed: Insufficient voltage change.", curses.color_pair(2))
                        except curses.error:
                            logging.warning("addstr error for test failed insufficient change.")
                else:
                    logging.debug(f"Balance test {high}->{low} passed: Sufficient voltage change.")
                    if progress_y + 1 < stdscr.getmaxyx()[0]:
                        try:
                            stdscr.addstr(progress_y + 1, 0, "Test passed.", curses.color_pair(4))
                        except curses.error:
                            logging.warning("addstr error for test passed.")
            else:
                alerts.append(f"Balance test {high}->{low} failed: Insufficient readings.")
                logging.error(f"Balance test {high}->{low} failed: Only {len(high_trend)} readings collected.")
                if progress_y + 1 < stdscr.getmaxyx()[0]:
                    try:
                        stdscr.addstr(progress_y + 1, 0, "Test failed: Insufficient readings.", curses.color_pair(2))
                    except curses.error:
                        logging.warning("addstr error for test failed insufficient readings.")

            stdscr.refresh()
            y = progress_y + 2
            time.sleep(2)
    
    # Store test results
    startup_alerts = alerts
    
    if alerts:
        startup_failed = True
        logging.error("Startup self-test failures: " + "; ".join(alerts))
        send_alert_email("Startup self-test failures:\n" + "\n".join(alerts), settings)
        
        if GPIO:
            GPIO.output(settings['AlarmRelayPin'], GPIO.HIGH)
        
        if y < stdscr.getmaxyx()[0]:
            try:
                stdscr.addstr(y, 0, "Self-Test Complete with Failures. Continuing with warnings.", curses.color_pair(2))
            except curses.error:
                logging.warning("addstr error for self-test failures.")
    else:
        if y < stdscr.getmaxyx()[0]:
            try:
                stdscr.addstr(y, 0, "Self-Test Complete. All OK.", curses.color_pair(4))
            except curses.error:
                logging.warning("addstr error for self-test OK.")
        
        logging.info("Startup self-test passed.")
    
    stdscr.refresh()
    time.sleep(5)
    return alerts

class BMSRequestHandler(BaseHTTPRequestHandler):
    """
    HTTP request handler for web interface and API endpoints.
    """
    def __init__(self, request, client_address, server):
        """
        Initialize request handler with settings from server.
        """
        self.settings = server.settings
        super().__init__(request, client_address, server)
    
    def do_GET(self):
        """
        Handle GET requests for web interface and API.
        """
        parsed_path = urlparse(self.path)
        path = parsed_path.path
        
        # Check authentication if required
        if self.settings['auth_required'] and not self.authenticate():
            self.send_response(401)
            self.send_header('WWW-Authenticate', 'Basic realm="BMS"')
            self.end_headers()
            return
        
        # Set CORS headers if enabled
        if self.settings['cors_enabled']:
            self.send_header('Access-Control-Allow-Origin', self.settings['cors_origins'])
            self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
            self.send_header('Access-Control-Allow-Headers', 'Content-Type, Authorization')
        
        # Serve main dashboard page
        if path == '/':
            self.send_response(200)
            self.send_header('Content-type', 'text/html')
            self.end_headers()
            
            # HTML dashboard content
            html = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Battery Management System</title>
    <style>
        body { font-family: Arial, sans-serif; margin: 20px; background-color: #f5f5f5; }
        .container { max-width: 1200px; margin: 0 auto; }
        .header { background-color: #2c3e50; color: white; padding: 15px; border-radius: 5px; }
        .status-card { background-color: white; border-radius: 5px; padding: 15px; margin: 10px 0; box-shadow: 0 2px 5px rgba(0,0,0,0.1); }
        .battery { display: inline-block; margin: 10px; padding: 10px; border: 1px solid #ddd; border-radius: 5px; background-color: #f9f9f9; }
        .voltage { font-size: 1.2em; font-weight: bold; }
        .temperature { font-size: 0.9em; }
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
            <h2>Battery Banks</h2>
            <div id="battery-container" class="grid"></div>
        </div>
        
        <div class="status-card">
            <h2>Alerts</h2>
            <div id="alerts-container"></div>
        </div>
        
        <div class="status-card">
            <h2>Actions</h2>
            <button id="refresh-btn" class="button">Refresh</button>
            <button id="balance-btn" class="button" disabled>Balance Now</button>
        </div>
        
        <div class="status-card">
            <h2>System Information</h2>
            <p>Total Voltage: <span id="total-voltage">-</span></p>
            <p>Balancing: <span id="balancing-status">No</span></p>
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
                        const bankDiv = document.createElement('div');
                        bankDiv.className = 'battery';
                        bankDiv.innerHTML = `
                            <h3>Bank ${index + 1}</h3>
                            <p class="voltage ${voltage === 0 ? 'alert' : (voltage > 21 || voltage < 18.5) ? 'warning' : 'normal'}">
                                ${voltage.toFixed(2)}V
                            </p>
                            <div class="temperatures">
                                ${data.temperatures.slice(index * 8, (index + 1) * 8).map((temp, tempIndex) => `
                                    <p class="temperature ${temp === null ? 'alert' : (temp > 60 || temp < 0) ? 'warning' : 'normal'}">
                                        C${tempIndex + 1}: ${temp !== null ? temp.toFixed(1) + '°C' : 'N/A'}
                                    </p>
                                `).join('')}
                            </div>
                        `;
                        batteryContainer.appendChild(bankDiv);
                    });
                    
                    const alertsContainer = document.getElementById('alerts-container');
                    if (data.alerts.length > 0) {
                        alertsContainer.innerHTML = data.alerts.map(alert => `<p class="alert">${alert}</p>`).join('');
                    } else:
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
        setInterval(updateStatus, 5000);
    </script>
</body>
</html>"""
            
            self.wfile.write(html.encode('utf-8'))
        
        # Serve API status endpoint
        elif path == '/api/status':
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            
            # Prepare status response
            response = {
                'voltages': web_data['voltages'],
                'temperatures': web_data['temperatures'],
                'alerts': web_data['alerts'],
                'balancing': web_data['balancing'],
                'last_update': web_data['last_update'],
                'system_status': web_data['system_status'],
                'total_voltage': sum(web_data['voltages'])
            }
            
            self.wfile.write(json.dumps(response).encode('utf-8'))
        
        else:
            self.send_response(404)
            self.end_headers()
    
    def do_POST(self):
        """
        Handle POST requests for API actions.
        """
        parsed_path = urlparse(self.path)
        path = parsed_path.path
        
        # Check authentication if required
        if self.settings['auth_required'] and not self.authenticate():
            self.send_response(401)
            self.send_header('WWW-Authenticate', 'Basic realm="BMS"')
            self.end_headers()
            return
        
        # Set CORS headers if enabled
        if self.settings['cors_enabled']:
            self.send_header('Access-Control-Allow-Origin', self.settings['cors_origins'])
            self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
            self.send_header('Access-Control-Allow-Headers', 'Content-Type, Authorization')
        
        # Handle balance initiation request
        if path == '/api/balance':
            global balancing_active
            
            # Check if already balancing
            if balancing_active:
                self.send_response(400)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                response = {'success': False, 'message': 'Balancing already in progress'}
                self.wfile.write(json.dumps(response).encode('utf-8'))
                return
            
            # Check for active alerts
            if len(web_data['alerts']) > 0:
                self.send_response(400)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                response = {'success': False, 'message': 'Cannot balance with active alerts'}
                self.wfile.write(json.dumps(response).encode('utf-8'))
                return
            
            voltages = web_data['voltages']
            
            # Check if enough banks available
            if len(voltages) < 2:
                self.send_response(400)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                response = {'success': False, 'message': 'Not enough battery banks'}
                self.wfile.write(json.dumps(response).encode('utf-8'))
                return
            
            # Find banks with max and min voltage
            max_v = max(voltages)
            min_v = min(voltages)
            high_bank = voltages.index(max_v) + 1
            low_bank = voltages.index(min_v) + 1
            
            # Check if voltage difference is sufficient
            if max_v - min_v < self.settings['VoltageDifferenceToBalance']:
                self.send_response(400)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                response = {'success': False, 'message': 'Voltage difference too small for balancing'}
                self.wfile.write(json.dumps(response).encode('utf-8'))
                return
            
            # Start balancing
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
        """
        Handle OPTIONS requests for CORS preflight.
        """
        self.send_response(200)
        if self.settings['cors_enabled']:
            self.send_header('Access-Control-Allow-Origin', self.settings['cors_origins'])
            self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
            self.send_header('Access-Control-Allow-Headers', 'Content-Type, Authorization')
        self.end_headers()
    
    def authenticate(self):
        """
        Authenticate request using Basic Auth.
        
        Returns:
            bool: True if authentication successful, False otherwise
        """
        auth_header = self.headers.get('Authorization')
        if auth_header and auth_header.startswith('Basic '):
            auth_decoded = base64.b64decode(auth_header[6:]).decode('utf-8')
            username, password = auth_decoded.split(':', 1)
            return username == self.settings['username'] and password == self.settings['password']
        return False

def start_web_server(settings):
    """
    Start web server for remote monitoring and control.
    
    Args:
        settings (dict): Configuration settings
    """
    global web_server
    
    # Skip if disabled in config
    if not settings['WebInterfaceEnabled']:
        logging.info("Web interface disabled via configuration.")
        return
    
    # Custom HTTP server that shares settings with request handler
    class CustomHTTPServer(HTTPServer):
        def __init__(self, *args, **kwargs):
            self.settings = settings
            super().__init__(*args, **kwargs)
    
    try:
        # Start web server in separate thread
        web_server = CustomHTTPServer((settings['host'], settings['web_port']), BMSRequestHandler)
        logging.info(f"Web server started on {settings['host']}:{settings['web_port']}")
        
        server_thread = threading.Thread(target=web_server.serve_forever)
        server_thread.daemon = True
        server_thread.start()
        
    except Exception as e:
        logging.error(f"Failed to start web server: {e}")

def main(stdscr):
    """
    Main application function with curses wrapper.
    
    Args:
        stdscr: Curses screen object
    """
    # Initialize curses settings
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
    
    # Global variables for state management
    global previous_temps, previous_bank_medians, run_count, startup_offsets, startup_median, startup_set, battery_voltages, web_data, balancing_active
    
    # Load configuration and setup hardware
    settings = load_config()
    setup_hardware(settings)
    
    # Start web server if enabled
    start_web_server(settings)
    
    # Run startup self-test
    startup_self_test(settings, stdscr)
    
    # Setup signal handler for graceful shutdown
    signal.signal(signal.SIGINT, signal_handler)
    
    # Load temperature offsets if available
    startup_median, startup_offsets = load_offsets()
    if startup_offsets and len(startup_offsets) == settings['num_channels']:
        startup_set = True
        logging.info(f"Loaded startup median: {startup_median:.1f}°C")
    
    # Initialize state variables
    previous_temps = None
    previous_bank_medians = [None] * NUM_BANKS
    run_count = 0
    web_data['system_status'] = 'Running'
    
    # Main monitoring loop
    while True:
        logging.info("Starting poll cycle.")
        web_data['last_update'] = time.time()
        
        # Read temperature sensors
        temp_result = read_ntc_sensors(settings['ip'], settings['modbus_port'], settings['query_delay'], settings['num_channels'], settings['scaling_factor'], settings['max_retries'], settings['retry_backoff_base'])
        temps_alerts = []
        
        # Process temperature readings
        if isinstance(temp_result, str):
            # Handle temperature read error
            temps_alerts.append(temp_result)
            calibrated_temps = [None] * settings['num_channels']
            raw_temps = [settings['valid_min']] * settings['num_channels']
            bank_medians = [0.0] * NUM_BANKS
        else:
            # Process valid temperature readings
            valid_count = sum(1 for t in temp_result if t > settings['valid_min'])
            
            # Set up calibration if all readings are valid and not already set
            if not startup_set and valid_count == settings['num_channels']:
                startup_median = statistics.median(temp_result)
                startup_offsets = [startup_median - raw for raw in temp_result]
                save_offsets(startup_median, startup_offsets)
                startup_set = True
                logging.info(f"Temp calibration set. Median: {startup_median:.1f}°C")
            
            # Reset calibration if offsets are missing
            if startup_set and startup_offsets is None:
                startup_set = False
            
            # Apply calibration to temperatures
            calibrated_temps = [temp_result[i] + startup_offsets[i] if startup_set and temp_result[i] > settings['valid_min'] else temp_result[i] if temp_result[i] > settings['valid_min'] else None for i in range(settings['num_channels'])]
            raw_temps = temp_result
            bank_medians = compute_bank_medians(calibrated_temps, settings['valid_min'])
            
            # Check for temperature anomalies
            for ch, raw in enumerate(raw_temps, 1):
                if check_invalid_reading(raw, ch, temps_alerts, settings['valid_min']):
                    continue
                
                calib = calibrated_temps[ch-1]
                bank_id = get_bank_for_channel(ch)
                bank_median = bank_medians[bank_id - 1]
                
                check_high_temp(calib, ch, temps_alerts, settings['high_threshold'])
                check_low_temp(calib, ch, temps_alerts, settings['low_threshold'])
                check_deviation(calib, bank_median, ch, temps_alerts, settings['abs_deviation_threshold'], settings['deviation_threshold'])
            
            # Check for temporal anomalies if this is not the first run
            if run_count > 0 and previous_temps and previous_bank_medians is not None:
                for bank_id in range(1, NUM_BANKS + 1):
                    bank_median_rise = bank_medians[bank_id - 1] - previous_bank_medians[bank_id - 1]
                    start, end = BANK_RANGES[bank_id - 1]
                    
                    for ch in range(start, end + 1):
                        calib = calibrated_temps[ch - 1]
                        if calib is not None:
                            check_abnormal_rise(calib, previous_temps, ch, temps_alerts, settings['poll_interval'], settings['rise_threshold'])
                            check_group_tracking_lag(calib, previous_temps, bank_median_rise, ch, temps_alerts, settings['disconnection_lag_threshold'])
                        
                        check_sudden_disconnection(calib, previous_temps, ch, temps_alerts)
            
            # Store current readings for next comparison
            previous_temps = calibrated_temps[:]
            previous_bank_medians = bank_medians[:]
        
        # Read battery voltages
        battery_voltages = []
        for i in range(1, NUM_BANKS + 1):
            v, _, _ = read_voltage_with_retry(i, settings)
            battery_voltages.append(v if v is not None else 0.0)
        
        # Check for issues and trigger alerts
        alert_needed, all_alerts = check_for_issues(battery_voltages, temps_alerts, settings)
        
        # Check if balancing is needed
        if len(battery_voltages) == NUM_BANKS:
            max_v = max(battery_voltages)
            min_v = min(battery_voltages)
            high_b = battery_voltages.index(max_v) + 1
            low_b = battery_voltages.index(min_v) + 1
            current_time = time.time()
            
            # Start balancing if conditions are met
            if balancing_active or (alert_needed is False and max_v - min_v > settings['VoltageDifferenceToBalance'] and min_v > 0 and current_time - last_balance_time > settings['BalanceRestPeriodSeconds']):
                balance_battery_voltages(stdscr, high_b, low_b, settings, temps_alerts)
                balancing_active = False
        
        # Update web interface data
        web_data['voltages'] = battery_voltages
        web_data['temperatures'] = calibrated_temps
        web_data['alerts'] = all_alerts
        web_data['balancing'] = balancing_active
        web_data['last_update'] = time.time()
        web_data['system_status'] = 'Alert' if alert_needed else 'Running'
        
        # Update TUI display
        draw_tui(stdscr, battery_voltages, calibrated_temps, raw_temps, startup_offsets or [0]*settings['num_channels'], bank_medians, startup_median, all_alerts, settings, startup_set, is_startup=(run_count == 0))
        
        # Increment run counter and perform cleanup
        run_count += 1
        gc.collect()
        logging.info("Poll cycle complete.")
        
        # Sleep until next poll
        time.sleep(min(settings['poll_interval'], settings['SleepTimeBetweenChecks']))

# Entry point for the application
if __name__ == '__main__':
    curses.wrapper(main)