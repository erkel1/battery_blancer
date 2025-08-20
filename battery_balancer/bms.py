"""
Combined Battery Temperature Monitoring and Balancing Script (Updated for 3s8p Configuration)

Extensive Summary:
This script serves as a comprehensive Battery Management System (BMS) for a 3s8p battery configuration (3 series-connected parallel battery banks, each with 8 cells). It integrates temperature monitoring from NTC sensors via a Lantronix EDS4100 device (using Modbus RTU over TCP) with voltage balancing using I2C-based ADC for readings and relays/GPIO for control. The system runs in an infinite loop, polling data at configurable intervals, detecting anomalies, balancing voltages if imbalances exceed thresholds, logging events, sending email alerts for critical issues, and displaying real-time status in a curses-based Text User Interface (TUI). Now includes an optional web interface for remote monitoring and manual balancing.

What it does:
- Reads temperatures from 24 NTC sensors (grouped into 3 banks: channels 1-8, 9-16, 17-24).
- Calibrates temperatures at startup (aligns to median offset if all valid) and persists offsets.
- Detects temp anomalies: invalid, high/low, deviation from bank median, abnormal rise, group lag, sudden disconnection.
- Reads voltages from 3 banks using ADS1115 ADC over I2C, with retries and calibration.
- Checks voltage issues: zero, high, low.
- Balances voltages: If max-min > threshold and no alerts, connects high to low bank via relays, turns on DC-DC converter for duration, shows progress.
- Alerts: Logs issues, activates GPIO alarm relay, sends throttled emails with auth.
- TUI: ASCII art batteries with voltages/temps inside (full details at startup, compact updates), ADC/readings, alerts; no pauses.
- Web: HTTP dashboard for status, alerts, manual balance (with auth/CORS).
- Handles shutdown: Ctrl+C cleans GPIO/web.
- Startup Self-Check: Configurable, validates config/hardware/reads; balancer only if no failures.

How it does it:
- Config loaded from 'battery_monitor.ini' with fallbacks.
- Hardware setup: I2C bus, GPIO pins.
- Startup check: Validate config, test connections/reads; alarm on failure.
- Infinite loop: Poll temps/voltages (retry on invalid), process/calibrate, check alerts, balance if needed, draw TUI, update web, sleep.
- Logging: To 'battery_monitor.log' at configurable level.
- Edges: Retries on reads, guards for None, exponential backoff, mock-safe for testing.

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

Dependencies: socket, statistics, time, configparser, logging, signal, gc, os, smbus, RPi.GPIO, smtplib, email.mime.text.MIMEText, curses, sys, art (pip install art), threading, json, http.server, urllib.parse, base64.
Note: Ensure EDS4100 configured, INI present, hardware connected. Web at http://<pi-ip>:8080.
"""

import socket  # Import socket for TCP connections to the EDS4100 device
import statistics  # Import statistics for median calculations on temperatures
import time  # Import time for delays and timing operations
import configparser  # Import configparser for loading settings from the INI file
import logging  # Import logging for logging events to file
import signal  # Import signal for handling Ctrl+C graceful shutdown
import gc  # Import gc for manual garbage collection in long-running loop
import os  # Import os for file operations like offsets.txt
import smbus  # Import smbus for I2C communication with ADC/relays
import RPi.GPIO as GPIO  # Import RPi.GPIO for GPIO control of relays/converter
from email.mime.text import MIMEText  # Import MIMEText for constructing email messages
import smtplib  # Import smtplib for sending emails
import curses  # Import curses for terminal-based TUI
import sys  # Import sys for sys.exit on shutdown
from art import text2art  # Import text2art from art for ASCII art total voltage
import threading  # Import threading for web server thread
import json  # Import json for API responses
from http.server import HTTPServer, BaseHTTPRequestHandler  # Import HTTPServer and BaseHTTPRequestHandler for web server
from urllib.parse import urlparse, parse_qs  # Import urlparse and parse_qs for parsing requests
import base64  # Import base64 for basic auth decoding

logging.basicConfig(filename='battery_monitor.log', level=logging.INFO, format='%(asctime)s - %(message)s')  # Setup basic logging configuration to file

# Global variables
config_parser = configparser.ConfigParser()  # Initialize ConfigParser for reading INI file
bus = None  # Initialize I2C bus object as None
last_email_time = 0  # Initialize timestamp for email throttling
balance_start_time = None  # Initialize timestamp for balancing duration
last_balance_time = 0  # Initialize timestamp for balancing rest period
battery_voltages = []  # Initialize list for current bank voltages
previous_temps = None  # Initialize previous calibrated temps for rise/lag checks
previous_bank_medians = None  # Initialize previous bank medians for rise checks
run_count = 0  # Initialize poll cycle counter (for startup check)
startup_offsets = None  # Initialize per-channel temp offsets from startup
startup_median = None  # Initialize startup median temp for reference
startup_set = False  # Initialize flag if startup calibration done
alert_states = {}  # Initialize per-channel alert tracking (unused in current version)
balancing_active = False  # Initialize flag if balancing in progress
startup_failed = False  # Initialize persistent flag for startup failures
startup_alerts = []  # Initialize list to store startup failures for TUI alerts
web_server = None  # Initialize web server instance
web_data = {  # Initialize shared data dict for web interface
    'voltages': [0.0] * 3,  # Initialize voltages list with 3 zeros
    'temperatures': [None] * 24,  # Initialize temperatures list with 24 Nones
    'alerts': [],  # Initialize empty list for alerts
    'balancing': False,  # Initialize balancing flag as False
    'last_update': time.time(),  # Initialize last update timestamp
    'system_status': 'Initializing'  # Initialize system status
}

# Bank definitions (dynamic based on config, but assume 8 channels per bank for 3s8p)
BANK_RANGES = [(1, 8), (9, 16), (17, 24)]  # Define channel ranges for each bank
NUM_BANKS = 3  # Define number of banks (updated from config)

def get_bank_for_channel(ch):
    """Get bank ID for a given channel."""
    for bank_id, (start, end) in enumerate(BANK_RANGES, 1):  # Loop through bank ranges with enumeration starting from 1
        if start <= ch <= end:  # Check if channel is within the range
            return bank_id  # Return the bank ID if match
    return None  # Return None if no match

def modbus_crc(data):
    """Calculate Modbus CRC for data integrity."""
    crc = 0xFFFF  # Initialize CRC to 0xFFFF
    for byte in data:  # Loop through each byte in data
        crc ^= byte  # XOR CRC with byte
        for _ in range(8):  # Loop 8 times for each bit
            if crc & 0x0001:  # Check if least significant bit is 1
                crc = (crc >> 1) ^ 0xA001  # Shift right and XOR with polynomial
            else:
                crc >>= 1  # Shift right
    return crc.to_bytes(2, 'little')  # Return CRC as 2 bytes in little-endian

def read_ntc_sensors(ip, port, query_delay, num_channels, scaling_factor, max_retries, retry_backoff_base):
    """Read NTC sensor temperatures via Modbus over TCP with retries."""
    logging.info("Starting temperature sensor read.")  # Log start of temperature read
    query_base = bytes([1, 3]) + (0).to_bytes(2, 'big') + (num_channels).to_bytes(2, 'big')  # Build Modbus query base
    crc = modbus_crc(query_base)  # Calculate CRC for query base
    query = query_base + crc  # Append CRC to query
    
    for attempt in range(max_retries):  # Loop for retry attempts
        try:
            logging.debug(f"Temp read attempt {attempt+1}: Connecting to {ip}:{port}")  # Log attempt
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)  # Create TCP socket
            s.settimeout(3)  # Set timeout to 3 seconds
            s.connect((ip, port))  # Connect to device
            s.send(query)  # Send query
            time.sleep(query_delay)  # Delay for response
            response = s.recv(1024)  # Receive response
            s.close()  # Close socket
            
            if len(response) < 5:  # Check if response is too short
                raise ValueError("Short response")
            
            if len(response) != 3 + response[2] + 2:  # Validate response length
                raise ValueError("Invalid response length")
            calc_crc = modbus_crc(response[:-2])  # Recalculate CRC
            if calc_crc != response[-2:]:  # Check if CRC matches
                raise ValueError("CRC mismatch")
            
            slave, func, byte_count = response[0:3]  # Parse header
            if slave != 1 or func != 3 or byte_count != num_channels * 2:  # Validate header
                if func & 0x80:
                    return f"Error: Modbus exception code {response[2]}"
                return "Error: Invalid response header."
            
            data = response[3:3 + byte_count]  # Extract data
            raw_temperatures = []  # Initialize list for raw temperatures
            for i in range(0, len(data), 2):  # Loop through data in 2-byte steps
                val = int.from_bytes(data[i:i+2], 'big', signed=True) / scaling_factor  # Convert to temperature
                raw_temperatures.append(val)  # Append to list
            
            logging.info("Temperature read successful.")  # Log success
            return raw_temperatures  # Return temperatures
        
        except Exception as e:  # Catch exceptions
            logging.warning(f"Temp read attempt {attempt+1} failed: {str(e)}. Retrying.")  # Log warning
            if attempt < max_retries - 1:  # Check if not last attempt
                time.sleep(retry_backoff_base ** attempt)  # Exponential backoff
            else:
                logging.error(f"Temp read failed after {max_retries} attempts - {str(e)}.")  # Log final error
                return f"Error: Failed after {max_retries} attempts - {str(e)}."  # Return error message

def load_config():
    """Load settings from 'battery_monitor.ini' with fallbacks."""
    logging.info("Loading configuration from 'battery_monitor.ini'.")  # Log loading start
    global alert_states  # Global alert_states
    if not config_parser.read('battery_monitor.ini'):  # Read INI file
        logging.error("Config file 'battery_monitor.ini' not found.")  # Log error if not found
        raise FileNotFoundError("Config file 'battery_monitor.ini' not found.")  # Raise error

    # Temp settings
    temp_settings = {  # Dictionary for temp settings
        'ip': config_parser.get('Temp', 'ip', fallback='192.168.15.240'),  # Get IP with fallback
        'port': config_parser.getint('Temp', 'port', fallback=10001),  # Get port with fallback
        'poll_interval': config_parser.getfloat('Temp', 'poll_interval', fallback=10.0),  # Get poll interval with fallback
        'rise_threshold': config_parser.getfloat('Temp', 'rise_threshold', fallback=2.0),  # Get rise threshold with fallback
        'deviation_threshold': config_parser.getfloat('Temp', 'deviation_threshold', fallback=0.1),  # Get deviation threshold with fallback
        'disconnection_lag_threshold': config_parser.getfloat('Temp', 'disconnection_lag_threshold', fallback=0.5),  # Get lag threshold with fallback
        'high_threshold': config_parser.getfloat('Temp', 'high_threshold', fallback=60.0),  # Get high threshold with fallback
        'low_threshold': config_parser.getfloat('Temp', 'low_threshold', fallback=0.0),  # Get low threshold with fallback
        'scaling_factor': config_parser.getfloat('Temp', 'scaling_factor', fallback=100.0),  # Get scaling factor with fallback
        'valid_min': config_parser.getfloat('Temp', 'valid_min', fallback=0.0),  # Get valid min with fallback
        'max_retries': config_parser.getint('Temp', 'max_retries', fallback=3),  # Get max retries with fallback
        'retry_backoff_base': config_parser.getint('Temp', 'retry_backoff_base', fallback=1),  # Get backoff base with fallback
        'query_delay': config_parser.getfloat('Temp', 'query_delay', fallback=0.25),  # Get query delay with fallback
        'num_channels': config_parser.getint('Temp', 'num_channels', fallback=24),  # Get num channels with fallback
        'abs_deviation_threshold': config_parser.getfloat('Temp', 'abs_deviation_threshold', fallback=2.0)  # Get abs deviation with fallback
    }
    
    # Voltage/Balance settings (updated from revised)
    voltage_settings = {  # Dictionary for voltage settings
        'NumberOfBatteries': config_parser.getint('General', 'NumberOfBatteries', fallback=3),  # Get number of batteries with fallback
        'VoltageDifferenceToBalance': config_parser.getfloat('General', 'VoltageDifferenceToBalance', fallback=0.1),  # Get balance difference with fallback
        'BalanceDurationSeconds': config_parser.getint('General', 'BalanceDurationSeconds', fallback=5),  # Get balance duration with fallback
        'SleepTimeBetweenChecks': config_parser.getfloat('General', 'SleepTimeBetweenChecks', fallback=0.1),  # Get sleep time with fallback
        'BalanceRestPeriodSeconds': config_parser.getint('General', 'BalanceRestPeriodSeconds', fallback=60),  # Get balance rest with fallback
        'LowVoltageThresholdPerBattery': config_parser.getfloat('General', 'LowVoltageThresholdPerBattery', fallback=18.5),  # Get low voltage threshold with fallback
        'HighVoltageThresholdPerBattery': config_parser.getfloat('General', 'HighVoltageThresholdPerBattery', fallback=21.0),  # Get high voltage threshold with fallback
        'EmailAlertIntervalSeconds': config_parser.getint('General', 'EmailAlertIntervalSeconds', fallback=3600),  # Get email interval with fallback
        'I2C_BusNumber': config_parser.getint('General', 'I2C_BusNumber', fallback=1),  # Get I2C bus number with fallback
        'VoltageDividerRatio': config_parser.getfloat('General', 'VoltageDividerRatio', fallback=0.01592),  # Get divider ratio with fallback
        'LoggingLevel': config_parser.get('General', 'LoggingLevel', fallback='INFO')  # Get logging level with fallback
    }
    
    # Startup settings
    startup_settings = {  # Dictionary for startup settings
        'test_balance_duration': config_parser.getint('Startup', 'test_balance_duration', fallback=15),  # Get test balance duration with fallback
        'min_voltage_delta': config_parser.getfloat('Startup', 'min_voltage_delta', fallback=0.01),  # Get min voltage delta with fallback
        'test_read_interval': config_parser.getfloat('Startup', 'test_read_interval', fallback=2.0)  # Get test read interval with fallback
    }
    
    if voltage_settings['NumberOfBatteries'] != NUM_BANKS:  # Check if number of batteries matches NUM_BANKS
        logging.warning(f"NumberOfBatteries ({voltage_settings['NumberOfBatteries']}) does not match NUM_BANKS ({NUM_BANKS}); using {NUM_BANKS} for banks.")  # Log warning if mismatch
    
    alert_states = {ch: {'last_type': None, 'count': 0} for ch in range(1, temp_settings['num_channels'] + 1)}  # Initialize alert states dictionary for each channel
    
    logging.info("Configuration loaded successfully.")  # Log successful configuration load
    return {**temp_settings, **voltage_settings, **startup_settings}  # Return combined settings dictionary

def setup_hardware(settings):
    """Initialize I2C bus and GPIO pins."""
    global bus  # Global bus variable
    logging.info("Setting up hardware.")  # Log hardware setup start
    bus = smbus.SMBus(settings['I2C_BusNumber'])  # Initialize SMBus with I2C bus number
    GPIO.setmode(GPIO.BCM)  # Set GPIO mode to BCM
    GPIO.setup(settings['DC_DC_RelayPin'], GPIO.OUT, initial=GPIO.LOW)  # Setup DC-DC relay pin as output, initial low
    GPIO.setup(settings['AlarmRelayPin'], GPIO.OUT, initial=GPIO.LOW)  # Setup alarm relay pin as output, initial low
    logging.info("Hardware setup complete.")  # Log hardware setup complete

def signal_handler(sig, frame):
    """Handle SIGINT for graceful shutdown."""
    logging.info("Script stopped by user or signal.")  # Log shutdown signal
    GPIO.cleanup()  # Clean up GPIO pins
    sys.exit(0)  # Exit script with status 0

def load_offsets():
    """Load temp offsets from file if exists."""
    logging.info("Loading startup offsets from 'offsets.txt'.")  # Log loading offsets
    if os.path.exists('offsets.txt'):  # Check if offsets.txt exists
        with open('offsets.txt', 'r') as f:  # Open file for reading
            lines = f.readlines()  # Read all lines
            if len(lines) < 1:  # Check if file is empty
                logging.warning("Invalid offsets.txt; using none.")  # Log warning
                return None  # Return None
            startup_median = float(lines[0].strip())  # Parse median from first line
            offsets = [float(line.strip()) for line in lines[1:]]  # Parse offsets from remaining lines
            if len(offsets) != 24:  # Check if number of offsets is 24
                logging.warning("Invalid offsets count; using none.")  # Log warning
                return None  # Return None
            logging.debug(f"Loaded median {startup_median} and {len(offsets)} offsets.")  # Log loaded data
            return startup_median, offsets  # Return median and offsets
    logging.warning("No 'offsets.txt' found; using none.")  # Log warning if file not found
    return None  # Return None

def save_offsets(startup_median, offsets):
    """Save temp median and offsets to file."""
    logging.info("Saving startup offsets to 'offsets.txt'.")  # Log saving offsets
    with open('offsets.txt', 'w') as f:  # Open file for writing
        f.write(f"{startup_median}\n")  # Write median to first line
        for offset in offsets:  # Loop through offsets
            f.write(f"{offset}\n")  # Write each offset to a line
    logging.debug("Offsets saved.")  # Log saved

def check_invalid_reading(raw, ch, alerts, valid_min):
    """Check if raw temp is invalid."""
    if raw <= valid_min:  # Check if raw is less than or equal to valid min
        bank = get_bank_for_channel(ch)  # Get bank for channel
        alerts.append(f"Bank {bank} Ch {ch}: Invalid reading (≤ {valid_min}).")  # Append alert
        logging.warning(f"Invalid reading on Bank {bank} Ch {ch}: {raw} ≤ {valid_min}.")  # Log warning
        return True  # Return True for invalid
    return False  # Return False for valid

def check_high_temp(calibrated, ch, alerts, high_threshold):
    """Check for high temperature."""
    if calibrated > high_threshold:  # Check if calibrated temp is high
        bank = get_bank_for_channel(ch)  # Get bank for channel
        alerts.append(f"Bank {bank} Ch {ch}: High temp ({calibrated:.1f}°C > {high_threshold}°C).")  # Append alert
        logging.warning(f"High temp alert on Bank {bank} Ch {ch}: {calibrated:.1f} > {high_threshold}.")  # Log warning

def check_low_temp(calibrated, ch, alerts, low_threshold):
    """Check for low temperature."""
    if calibrated < low_threshold:  # Check if calibrated temp is low
        bank = get_bank_for_channel(ch)  # Get bank for channel
        alerts.append(f"Bank {bank} Ch {ch}: Low temp ({calibrated:.1f}°C < {low_threshold}°C).")  # Append alert
        logging.warning(f"Low temp alert on Bank {bank} Ch {ch}: {calibrated:.1f} < {low_threshold}.")  # Log warning

def check_deviation(calibrated, bank_median, ch, alerts, abs_deviation_threshold, deviation_threshold):
    """Check deviation from bank median."""
    abs_dev = abs(calibrated - bank_median)  # Calculate absolute deviation
    rel_dev = abs_dev / abs(bank_median) if bank_median != 0 else 0  # Calculate relative deviation
    if abs_dev > abs_deviation_threshold or rel_dev > deviation_threshold:  # Check if deviation exceeds thresholds
        bank = get_bank_for_channel(ch)  # Get bank for channel
        alerts.append(f"Bank {bank} Ch {ch}: Deviation from bank median (abs {abs_dev:.1f}°C or {rel_dev:.2%}).")  # Append alert
        logging.warning(f"Deviation alert on Bank {bank} Ch {ch}: abs {abs_dev:.1f}, rel {rel_dev:.2%}.")  # Log warning

def check_abnormal_rise(current, previous_temps, ch, alerts, poll_interval, rise_threshold):
    """Check for abnormal temp rise since last poll."""
    previous = previous_temps[ch-1]  # Get previous temp for channel
    if previous is not None:  # Check if previous is not None
        rise = current - previous  # Calculate rise
        if rise > rise_threshold:  # Check if rise exceeds threshold
            bank = get_bank_for_channel(ch)  # Get bank for channel
            alerts.append(f"Bank {bank} Ch {ch}: Abnormal rise ({rise:.1f}°C in {poll_interval}s).")  # Append alert
            logging.warning(f"Abnormal rise alert on Bank {bank} Ch {ch}: {rise:.1f}°C.")  # Log warning

def check_group_tracking_lag(current, previous_temps, bank_median_rise, ch, alerts, disconnection_lag_threshold):
    """Check if channel rise lags bank median rise."""
    previous = previous_temps[ch-1]  # Get previous temp for channel
    if previous is not None:  # Check if previous is not None
        rise = current - previous  # Calculate rise
        if abs(rise - bank_median_rise) > disconnection_lag_threshold:  # Check if lag exceeds threshold
            bank = get_bank_for_channel(ch)  # Get bank for channel
            alerts.append(f"Bank {bank} Ch {ch}: Lag from bank group ({rise:.1f}°C vs {bank_median_rise:.1f}°C).")  # Append alert
            logging.warning(f"Lag alert on Bank {bank} Ch {ch}: rise {rise:.1f} vs median {bank_median_rise:.1f}.")  # Log warning

def check_sudden_disconnection(current, previous_temps, ch, alerts):
    """Check for sudden sensor disconnection."""
    previous = previous_temps[ch-1]  # Get previous temp for channel
    if previous is not None and current is None:  # Check if disconnected
        bank = get_bank_for_channel(ch)  # Get bank for channel
        alerts.append(f"Bank {bank} Ch {ch}: Sudden disconnection.")  # Append alert
        logging.warning(f"Sudden disconnection alert on Bank {bank} Ch {ch}.")  # Log warning

def choose_channel(channel, multiplexer_address):
    """Select I2C multiplexer channel."""
    logging.debug(f"Switching to I2C channel {channel}.")  # Log channel switch
    bus.write_byte(multiplexer_address, 1 << channel)  # Write channel select byte

def setup_voltage_meter(settings):
    """Configure ADC for voltage measurement."""
    logging.debug("Configuring voltage meter ADC.")  # Log ADC config
    config_value = (settings['ContinuousModeConfig'] | 
                    settings['SampleRateConfig'] | 
                    settings['GainConfig'])  # Combine config bits
    bus.write_word_data(settings['VoltageMeterAddress'], settings['ConfigRegister'], config_value)  # Write config to ADC

def read_voltage_with_retry(bank_id, settings):
    """Read bank voltage with retries and averaging."""
    logging.info(f"Starting voltage read for Bank {bank_id}.")  # Log start of voltage read
    voltage_divider_ratio = settings['VoltageDividerRatio']  # Get divider ratio
    sensor_id = bank_id  # Set sensor ID to bank ID
    calibration_factor = settings[f'Sensor{sensor_id}_Calibration']  # Get calibration factor for sensor
    for attempt in range(2):  # Loop for 2 attempts
        logging.debug(f"Voltage read attempt {attempt+1} for Bank {bank_id}.")  # Log attempt
        readings = []  # Initialize list for readings
        raw_values = []  # Initialize list for raw ADC values
        for _ in range(2):  # Loop for 2 samples
            meter_channel = (bank_id - 1) % 3  # Calculate meter channel
            choose_channel(meter_channel, settings['MultiplexerAddress'])  # Select channel
            setup_voltage_meter(settings)  # Configure ADC
            bus.write_byte(settings['VoltageMeterAddress'], 0x01)  # Start conversion
            time.sleep(0.05)  # Delay for conversion
            raw_adc = bus.read_word_data(settings['VoltageMeterAddress'], settings['ConversionRegister'])  # Read raw ADC
            raw_adc = (raw_adc & 0xFF) << 8 | (raw_adc >> 8)  # Swap for little-endian
            logging.debug(f"Raw ADC for Bank {bank_id} (Sensor {sensor_id}): {raw_adc}")  # Log raw ADC
            if raw_adc != 0:  # Check if raw ADC is not zero
                measured_voltage = raw_adc * (6.144 / 32767)  # Convert to voltage
                actual_voltage = (measured_voltage / voltage_divider_ratio) * calibration_factor  # Apply divider and calib
                readings.append(actual_voltage)  # Append to readings
                raw_values.append(raw_adc)  # Append to raw values
            else:
                readings.append(0.0)  # Append zero to readings
                raw_values.append(0)  # Append zero to raw values
        if readings:  # Check if readings list is not empty
            average = sum(readings) / len(readings)  # Calculate average
            valid_readings = [r for r in readings if abs(r - average) / (average if average != 0 else 1) <= 0.05]  # Filter outliers
            valid_adc = [raw_values[i] for i, r in enumerate(readings) if abs(r - average) / (average if average != 0 else 1) <= 0.05]  # Filter valid ADC
            if valid_readings:  # Check if valid readings exist
                logging.info(f"Voltage read successful for Bank {bank_id}: {average:.2f}V.")  # Log success
                return sum(valid_readings) / len(valid_readings), valid_readings, valid_adc  # Return average, valid readings, valid ADC
        logging.debug(f"Readings for Bank {bank_id} inconsistent, retrying.")  # Log retry
    logging.error(f"Couldn't get good voltage reading for Bank {bank_id} after 2 tries.")  # Log failure
    return None, [], []  # Return failure

def set_relay_connection(high, low, settings):
    """Set relays for balancing between banks."""
    try:  # Try block for error handling
        logging.info(f"Attempting to set relay for connection from Bank {high} to {low}")  # Log attempt
        logging.debug("Switching to relay control channel.")  # Log channel switch
        choose_channel(3, settings['MultiplexerAddress'])  # Select relay channel
        relay_state = 0  # Initialize relay state
        if high == 1 and low == 2:  # Check for pair 1->2
            relay_state |= (1 << 0) | (1 << 1) | (1 << 3)  # Set bits for relays
            logging.debug("Relays 1, 2, and 4 activated for high to low.")  # Log relays
        elif high == 1 and low == 3:  # Check for pair 1->3
            relay_state |= (1 << 1) | (1 << 2) | (1 << 3)  # Set bits for relays
            logging.debug("Relays 2, 3, and 4 activated for high to low.")  # Log relays
        elif high == 2 and low == 1:  # Check for pair 2->1
            relay_state |= (1 << 0) | (1 << 2) | (1 << 3)  # Set bits for relays
            logging.debug("Relays 1, 3, and 4 activated for high to low.")  # Log relays
        elif high == 2 and low == 3:  # Check for pair 2->3
            relay_state |= (1 << 0) | (1 << 1) | (1 << 2)  # Set bits for relays
            logging.debug("Relays 1, 2, and 3 activated for high to low.")  # Log relays
        elif high == 3 and low == 1:  # Check for pair 3->1
            relay_state |= (1 << 0) | (1 << 1) | (1 << 2)  # Set bits for relays
            logging.debug("Relays 1, 2, and 3 activated for high to low.")  # Log relays
        elif high == 3 and low == 2:  # Check for pair 3->2
            relay_state |= (1 << 0) | (1 << 1) | (1 << 3)  # Set bits for relays
            logging.debug("Relays 1, 2, and 4 activated for high to low.")  # Log relays

        logging.debug(f"Final relay state: {bin(relay_state)}")  # Log final state
        logging.info(f"Sending relay state command to hardware.")  # Log sending command
        bus.write_byte_data(settings['RelayAddress'], 0x11, relay_state)  # Write state to relay
        logging.info(f"Relay setup completed for balancing from Bank {high} to {low}")  # Log completion
    except IOError as e:  # Catch IO error
        logging.error(f"I/O error while setting up relay: {e}")  # Log IO error
    except Exception as e:  # Catch general exception
        logging.error(f"Unexpected error in set_relay_connection: {e}")  # Log unexpected error

def control_dcdc_converter(turn_on, settings):
    """Turn DC-DC converter on/off via GPIO."""
    try:  # Try block for error handling
        GPIO.output(settings['DC_DC_RelayPin'], GPIO.HIGH if turn_on else GPIO.LOW)  # Set GPIO pin high or low
        logging.info(f"DC-DC Converter is now {'on' if turn_on else 'off'}")  # Log converter state
    except Exception as e:  # Catch exception
        logging.error(f"Problem controlling DC-DC converter: {e}")  # Log error

def send_alert_email(message, settings):
    """Send email alert with throttling and authentication."""
    global last_email_time  # Global last email time
    if time.time() - last_email_time < settings['EmailAlertIntervalSeconds']:  # Check if interval has passed
        logging.debug("Skipping alert email to avoid flooding.")  # Log skip
        return  # Return if throttling
    try:  # Try block for error handling
        msg = MIMEText(message)  # Create MIME text message
        msg['Subject'] = "Battery Monitor Alert"  # Set subject
        msg['From'] = settings['SenderEmail']  # Set from email
        msg['To'] = settings['RecipientEmail']  # Set to email
        with smtplib.SMTP(settings['SMTP_Server'], settings['SMTP_Port']) as server:  # Open SMTP connection
            server.starttls()  # Start TLS
            if settings['SMTP_Username'] and settings['SMTP_Password']:  # Check if username and password are set
                server.login(settings['SMTP_Username'], settings['SMTP_Password'])  # Login to SMTP
            server.send_message(msg)  # Send message
        last_email_time = time.time()  # Update last email time
        logging.info(f"Alert email sent: {message}")  # Log sent email
    except Exception as e:  # Catch exception
        logging.error(f"Failed to send alert email: {e}")  # Log failure

def check_for_issues(voltages, temps_alerts, settings):
    """Check voltage/temp issues, trigger alerts/relay."""
    global startup_failed, startup_alerts  # Global startup failed and alerts
    logging.info("Checking for voltage and temp issues.")  # Log check start
    alert_needed = startup_failed  # Set alert needed to startup failed
    alerts = []  # Initialize alerts list
    if startup_failed and startup_alerts:  # Check if startup failed
        alerts.append("Startup failures: " + "; ".join(startup_alerts))  # Append startup failures
    for i, v in enumerate(voltages, 1):  # Loop through voltages with enumeration
        if v is None or v == 0.0:  # Check for zero or None voltage
            alerts.append(f"Bank {i}: Zero voltage.")  # Append alert
            logging.warning(f"Zero voltage alert on Bank {i}.")  # Log warning
            alert_needed = True  # Set alert needed
        elif v > settings['HighVoltageThresholdPerBattery']:  # Check for high voltage
            alerts.append(f"Bank {i}: High voltage ({v:.2f}V).")  # Append alert
            logging.warning(f"High voltage alert on Bank {i}: {v:.2f}V.")  # Log warning
            alert_needed = True  # Set alert needed
        elif v < settings['LowVoltageThresholdPerBattery']:  # Check for low voltage
            alerts.append(f"Bank {i}: Low voltage ({v:.2f}V).")  # Append alert
            logging.warning(f"Low voltage alert on Bank {i}: {v:.2f}V.")  # Log warning
            alert_needed = True  # Set alert needed
    if temps_alerts:  # Check if temp alerts exist
        alerts.extend(temps_alerts)  # Extend alerts with temp alerts
        alert_needed = True  # Set alert needed
    if alert_needed:  # Check if alert needed
        GPIO.output(settings['AlarmRelayPin'], GPIO.HIGH)  # Activate alarm relay
        logging.info("Alarm relay activated.")  # Log activation
        send_alert_email("\n".join(alerts), settings)  # Send email
    else:  # No alert needed
        GPIO.output(settings['AlarmRelayPin'], GPIO.LOW)  # Deactivate alarm relay
        logging.info("No issues; alarm relay deactivated.")  # Log no issues
    return alert_needed, alerts  # Return alert needed and alerts

def balance_battery_voltages(stdscr, high, low, settings, temps_alerts):
    global balance_start_time, last_balance_time, balancing_active, web_data
    if temps_alerts:
        logging.warning("Skipping balancing due to temperature anomalies in banks.")
        return
    logging.info(f"Starting balance from Bank {high} to {low}.")
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
    progress_y = 17 + 6 + 2
    height, _ = stdscr.getmaxyx()
    while time.time() - balance_start_time < settings['BalanceDurationSeconds']:
        elapsed = time.time() - balance_start_time
        progress = min(1.0, elapsed / settings['BalanceDurationSeconds'])
        voltage_high, _, _ = read_voltage_with_retry(high, settings)
        voltage_low, _, _ = read_voltage_with_retry(low, settings)
        bar_length = 20
        filled = int(bar_length * progress)
        bar = '=' * filled + ' ' * (bar_length - filled)
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
    logging.info("Balancing process completed.")
    control_dcdc_converter(False, settings)
    logging.info("Turning off DC-DC converter.")
    set_relay_connection(0, 0, settings)
    logging.info("Resetting relay connections to default state.")
    balancing_active = False
    web_data['balancing'] = False
    last_balance_time = time.time()

def compute_bank_medians(calibrated_temps, valid_min):
    bank_medians = []
    for start, end in BANK_RANGES:
        bank_temps = [calibrated_temps[i-1] for i in range(start, end+1) if calibrated_temps[i-1] is not None]
        bank_median = statistics.median(bank_temps) if bank_temps else 0.0
        bank_medians.append(bank_median)
    return bank_medians

def draw_tui(stdscr, voltages, calibrated_temps, raw_temps, offsets, bank_medians, startup_median, alerts, settings, startup_set, is_startup):
    logging.debug("Refreshing TUI.")
    stdscr.clear()
    curses.start_color()
    curses.use_default_colors()
    TITLE_COLOR = curses.color_pair(1)
    HIGH_V = curses.color_pair(2)
    LOW_V = curses.color_pair(3)
    OK_V = curses.color_pair(4)
    ADC_C = curses.color_pair(5)
    BAL_C = curses.color_pair(6)
    INFO_C = curses.color_pair(7)
    ERR_C = curses.color_pair(8)
    curses.init_pair(1, curses.COLOR_RED, -1)
    curses.init_pair(2, curses.COLOR_RED, -1)
    curses.init_pair(3, curses.COLOR_YELLOW, -1)
    curses.init_pair(4, curses.COLOR_GREEN, -1)
    curses.init_pair(5, curses.COLOR_WHITE, -1)
    curses.init_pair(6, curses.COLOR_YELLOW, -1)
    curses.init_pair(7, curses.COLOR_CYAN, -1)
    curses.init_pair(8, curses.COLOR_MAGENTA, -1)
    
    height, width = stdscr.getmaxyx()
    
    total_v = sum(voltages)
    total_high = settings['HighVoltageThresholdPerBattery'] * NUM_BANKS
    total_low = settings['LowVoltageThresholdPerBattery'] * NUM_BANKS
    v_color = HIGH_V if total_v > total_high else LOW_V if total_v < total_low else OK_V
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
    
    battery_art_base = [
        "   ___________   ",
        "  |           |  ",
        "  |           |  ",
        "  |           |  ",
        "  |           |  ",
        "  |    +++    |  ",
        "  |    +++    |  ",
        "  |           |  ",
        "  |           |  ",
        "  |           |  ",
        "  |           |  ",
        "  |    ---    |  ",
        "  |    ---    |  ",
        "  |    ---    |  ",
        "  |           |  ",
        "  |           |  ",
        "  |___________|  "
    ]
    art_height = len(battery_art_base)
    art_width = len(battery_art_base[0])
    
    for row, line in enumerate(battery_art_base):
        full_line = line * NUM_BANKS
        if y_offset + row < height and len(full_line) < width:
            try:
                stdscr.addstr(y_offset + row, 0, full_line, OK_V)
            except curses.error:
                logging.warning(f"addstr error for art row {row}.")
        else:
            logging.warning(f"Skipping art row {row} - out of bounds.")
    
    for bank_id in range(NUM_BANKS):
        start_pos = bank_id * art_width
        v_str = f"{voltages[bank_id]:.2f}V" if voltages[bank_id] > 0 else "0.00V"
        v_color = ERR_C if voltages[bank_id] == 0.0 else HIGH_V if voltages[bank_id] > settings['HighVoltageThresholdPerBattery'] else LOW_V if voltages[bank_id] < settings['LowVoltageThresholdPerBattery'] else OK_V
        v_center = start_pos + (art_width - len(v_str)) // 2
        v_y = y_offset + 1
        if v_y < height and v_center + len(v_str) < width:
            try:
                stdscr.addstr(v_y, v_center, v_str, v_color)
            except curses.error:
                logging.warning(f"addstr error for voltage overlay Bank {bank_id+1}.")
        else:
            logging.warning(f"Skipping voltage overlay for Bank {bank_id+1} - out of bounds.")
        
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
            t_color = ERR_C if "Inv" in calib_str else HIGH_V if calib > settings['high_threshold'] else LOW_V if calib < settings['low_threshold'] else OK_V
            t_center = start_pos + (art_width - len(t_str)) // 2
            t_y = y_offset + 2 + local_ch
            if t_y < height and t_center + len(t_str) < width:
                try:
                    stdscr.addstr(t_y, t_center, t_str, t_color)
                except curses.error:
                    logging.warning(f"addstr error for temp overlay Bank {bank_id+1} C{local_ch+1}.")
            else:
                logging.warning(f"Skipping temp overlay for Bank {bank_id+1} C{local_ch+1} - out of bounds.")
        
        med_str = f"Med: {bank_medians[bank_id]:.1f}°C"
        med_center = start_pos + (art_width - len(med_str)) // 2
        med_y = y_offset + 15
        if med_y < height and med_center + len(med_str) < width:
            try:
                stdscr.addstr(med_y, med_center, med_str, INFO_C)
            except curses.error:
                logging.warning(f"addstr error for median overlay Bank {bank_id+1}.")
        else:
            logging.warning(f"Skipping median overlay for Bank {bank_id+1} - out of bounds.")
    
    y_offset += art_height + 2
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
                    stdscr.addstr(y_offset, 0, f"Bank {i}: (ADC: {adc_values[0] if adc_values else 'N/A'})", ADC_C)
                except curses.error:
                    logging.warning(f"addstr error for ADC Bank {i}.")
            else:
                logging.warning(f"Skipping ADC for Bank {i} - out of bounds.")
            y_offset += 1
            if y_offset < height:
                try:
                    if readings:
                        stdscr.addstr(y_offset, 0, f"[Readings: {', '.join(f'{v:.2f}' for v in readings)}]", ADC_C)
                    else:
                        stdscr.addstr(y_offset, 0, "  [Readings: No data]", ADC_C)
                except curses.error:
                    logging.warning(f"addstr error for readings Bank {i}.")
            else:
                logging.warning(f"Skipping readings for Bank {i} - out of bounds.")
            y_offset += 1
    
    y_offset += 1
    
    med_str = f"{startup_median:.1f}°C" if startup_median else "N/A"
    if y_offset < height:
        try:
            stdscr.addstr(y_offset, 0, f"Startup Median Temp: {med_str}", INFO_C)
        except curses.error:
            logging.warning("addstr error for startup median.")
    else:
        logging.warning("Skipping startup median - out of bounds.")
    y_offset += 2
    
    if y_offset < height:
        try:
            stdscr.addstr(y_offset, 0, "Alerts:", INFO_C)
        except curses.error:
            logging.warning("addstr error for alerts header.")
    y_offset += 1
    if alerts:
        for alert in alerts:
            if y_offset < height:
                try:
                    stdscr.addstr(y_offset, 0, alert, ERR_C)
                except curses.error:
                    logging.warning(f"addstr error for alert '{alert}'.")
            else:
                logging.warning(f"Skipping alert '{alert}' - out of bounds.")
            y_offset += 1
    else:
        if y_offset < height:
            try:
                stdscr.addstr(y_offset, 0, "No alerts.", OK_V)
            except curses.error:
                logging.warning("addstr error for no alerts message.")
        else:
            logging.warning("Skipping no alerts message - out of bounds.")
    
    stdscr.refresh()

def startup_self_test(settings, stdscr):
    global startup_failed, startup_alerts, startup_set, startup_median, startup_offsets
    if not settings['StartupSelfTestEnabled']:
        logging.info("Startup self-test disabled via configuration.")
        return []
    logging.info("Starting self-test: Validating config, connectivity, sensors, and balancer.")
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
    
    if y < stdscr.getmaxyx()[0]:
        try:
            stdscr.addstr(y, 0, "Step 1: Validating config...", curses.color_pair(4))
        except curses.error:
            logging.warning("addstr error for step 1.")
    stdscr.refresh()
    time.sleep(0.5)
    if settings['NumberOfBatteries'] != NUM_BANKS:
        alerts.append("Config mismatch: NumberOfBatteries != 3.")
        if y + 1 < stdscr.getmaxyx()[0]:
            try:
                stdscr.addstr(y + 1, 0, "Config mismatch detected.", curses.color_pair(2))
            except curses.error:
                logging.warning("addstr error for config mismatch.")
    else:
        if y + 1 < stdscr.getmaxyx()[0]:
            try:
                stdscr.addstr(y + 1, 0, "Config OK.", curses.color_pair(4))
            except curses.error:
                logging.warning("addstr error for config OK.")
    y += 2
    stdscr.refresh()
    
    if y < stdscr.getmaxyx()[0]:
        try:
            stdscr.addstr(y, 0, "Step 2: Testing hardware connectivity...", curses.color_pair(4))
        except curses.error:
            logging.warning("addstr error for step 2.")
    stdscr.refresh()
    time.sleep(0.5)
    try:
        choose_channel(0, settings['MultiplexerAddress'])
        bus.read_byte(settings['VoltageMeterAddress'])
        bus.read_byte(settings['RelayAddress'])
        if y + 1 < stdscr.getmaxyx()[0]:
            try:
                stdscr.addstr(y + 1, 0, "I2C OK.", curses.color_pair(4))
            except curses.error:
                logging.warning("addstr error for I2C OK.")
    except IOError as e:
        alerts.append(f"I2C connectivity failure: {str(e)}")
        if y + 1 < stdscr.getmaxyx()[0]:
            try:
                stdscr.addstr(y + 1, 0, f"I2C failure: {str(e)}", curses.color_pair(2))
            except curses.error:
                logging.warning("addstr error for I2C failure.")
    try:
        test_query = read_ntc_sensors(settings['ip'], settings['port'], settings['query_delay'], 1, settings['scaling_factor'], 1, 1)
        if isinstance(test_query, str) and "Error" in test_query:
            raise ValueError(test_query)
        if y + 2 < stdscr.getmaxyx()[0]:
            try:
                stdscr.addstr(y + 2, 0, "Modbus OK.", curses.color_pair(4))
            except curses.error:
                logging.warning("addstr error for Modbus OK.")
    except Exception as e:
        alerts.append(f"Modbus test failure: {str(e)}")
        if y + 2 < stdscr.getmaxyx()[0]:
            try:
                stdscr.addstr(y + 2, 0, f"Modbus failure: {str(e)}", curses.color_pair(2))
            except curses.error:
                logging.warning("addstr error for Modbus failure.")
    y += 3
    stdscr.refresh()
    
    if y < stdscr.getmaxyx()[0]:
        try:
            stdscr.addstr(y, 0, "Step 3: Initial sensor reads...", curses.color_pair(4))
        except curses.error:
            logging.warning("addstr error for step 3.")
    stdscr.refresh()
    time.sleep(0.5)
    initial_temps = read_ntc_sensors(settings['ip'], settings['port'], settings['query_delay'], settings['num_channels'], settings['scaling_factor'], settings['max_retries'], settings['retry_backoff_base'])
    if isinstance(initial_temps, str):
        alerts.append(f"Initial temp read failure: {initial_temps}")
        if y + 1 < stdscr.getmaxyx()[0]:
            try:
                stdscr.addstr(y + 1, 0, "Temp read failure.", curses.color_pair(2))
            except curses.error:
                logging.warning("addstr error for temp failure.")
    else:
        if y + 1 < stdscr.getmaxyx()[0]:
            try:
                stdscr.addstr(y + 1, 0, "Temps OK.", curses.color_pair(4))
            except curses.error:
                logging.warning("addstr error for temps OK.")
    initial_voltages = [read_voltage_with_retry(i, settings)[0] or 0.0 for i in range(1, NUM_BANKS + 1)]
    if any(v == 0.0 for v in initial_voltages):
        alerts.append("Initial voltage read failure: Zero voltage on one or more banks.")
        if y + 2 < stdscr.getmaxyx()[0]:
            try:
                stdscr.addstr(y + 2, 0, "Voltage read failure (zero).", curses.color_pair(2))
            except curses.error:
                logging.warning("addstr error for voltage failure.")
    else:
        if y + 2 < stdscr.getmaxyx()[0]:
            try:
                stdscr.addstr(y + 2, 0, "Voltages OK.", curses.color_pair(4))
            except curses.error:
                logging.warning("addstr error for voltages OK.")
    if isinstance(initial_temps, list):
        valid_count = sum(1 for t in initial_temps if t > settings['valid_min'])
        if valid_count == settings['num_channels']:
            startup_median = statistics.median(initial_temps)
            startup_offsets = [startup_median - t for t in initial_temps]
            save_offsets(startup_median, startup_offsets)
            startup_set = True
            logging.info(f"Temp calibration set during startup. Median: {startup_median:.1f}°C")
    y += 3
    stdscr.refresh()
    
    if not alerts:
        if y < stdscr.getmaxyx()[0]:
            try:
                stdscr.addstr(y, 0, "Step 4: Balancer verification...", curses.color_pair(4))
            except curses.error:
                logging.warning("addstr error for step 4.")
        y += 1
        stdscr.refresh()
        time.sleep(0.5)
        pairs = [(1,2), (1,3), (2,1), (2,3), (3,1), (3,2)]
        test_duration = settings['test_balance_duration']
        read_interval = settings['test_read_interval']
        min_delta = settings['min_voltage_delta']
        
        for high, low in pairs:
            if y < stdscr.getmaxyx()[0]:
                try:
                    stdscr.addstr(y, 0, f"Testing balance: Bank {high} -> {low} for {test_duration}s.", curses.color_pair(6))
                except curses.error:
                    logging.warning("addstr error for testing balance.")
            stdscr.refresh()
            logging.info(f"Testing balance: Bank {high} -> {low} for {test_duration}s.")
            
            temp_anomaly = False
            if initial_temps and isinstance(initial_temps, list):
                for t in initial_temps:
                    if t > settings['high_threshold'] or t < settings['low_threshold']:
                        temp_anomaly = True
                        break
            if temp_anomaly:
                alerts.append(f"Skipping balance test {high}->{low}: Temp anomalies.")
                if y + 1 < stdscr.getmaxyx()[0]:
                    try:
                        stdscr.addstr(y + 1, 0, "Skipped: Temp anomalies.", curses.color_pair(2))
                    except curses.error:
                        logging.warning("addstr error for skipped temp.")
                y += 2
                stdscr.refresh()
                continue
            
            set_relay_connection(high, low, settings)
            control_dcdc_converter(True, settings)
            start_time = time.time()
            
            high_trend = []
            low_trend = []
            progress_y = y + 1
            while time.time() - start_time < test_duration:
                time.sleep(read_interval)
                high_v = read_voltage_with_retry(high, settings)[0] or 0.0
                low_v = read_voltage_with_retry(low, settings)[0] or 0.0
                high_trend.append(high_v)
                low_trend.append(low_v)
                elapsed = time.time() - start_time
                if progress_y < stdscr.getmaxyx()[0]:
                    try:
                        stdscr.addstr(progress_y, 0, " " * 80, curses.color_pair(6))
                        stdscr.addstr(progress_y, 0, f"Progress: {elapsed:.1f}s, High {high_v:.2f}V, Low {low_v:.2f}V", curses.color_pair(6))
                    except curses.error:
                        logging.warning("addstr error in startup balance progress.")
                stdscr.refresh()
                logging.debug(f"Trend read: High {high_v:.2f}V, Low {low_v:.2f}V")
            
            control_dcdc_converter(False, settings)
            set_relay_connection(0, 0, settings)
            
            if progress_y + 1 < stdscr.getmaxyx()[0]:
                try:
                    stdscr.addstr(progress_y + 1, 0, "Analyzing...", curses.color_pair(6))
                except curses.error:
                    logging.warning("addstr error for analyzing.")
            stdscr.refresh()
            if len(high_trend) >= 3:
                high_delta = high_trend[0] - high_trend[-1]
                low_delta = low_trend[-1] - low_trend[0]
                if high_delta < min_delta or low_delta < min_delta:
                    alerts.append(f"Balance test {high}->{low} failed: Insufficient change (High Δ={high_delta:.3f}V, Low Δ={low_delta:.3f}V).")
                    if progress_y + 1 < stdscr.getmaxyx()[0]:
                        try:
                            stdscr.addstr(progress_y + 1, 0, "Test failed: Insufficient voltage change.", curses.color_pair(2))
                        except curses.error:
                            logging.warning("addstr error for test failed insufficient change.")
                else:
                    if progress_y + 1 < stdscr.getmaxyx()[0]:
                        try:
                            stdscr.addstr(progress_y + 1, 0, "Test passed.", curses.color_pair(4))
                        except curses.error:
                            logging.warning("addstr error for test passed.")
            else:
                alerts.append(f"Balance test {high}->{low} failed: Insufficient readings.")
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
    def __init__(self, request, client_address, server):
        self.settings = server.settings
        super().__init__(request, client_address, server)
    
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
        
        function initiateBalance() {
            fetch('/api/balance', { method: 'POST' })
                .then(response => response.json())
                .then(data => {
                    if (data.success) {
                        alert('Balancing initiated');
                    } else {
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
</html>
            """
            self.wfile.write(html.encode('utf-8'))
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
    class CustomHTTPServer(HTTPServer):
        def __init__(self, *args, **kwargs):
            self.settings = settings
            super().__init__(*args, **kwargs)
    
    try:
        web_server = CustomHTTPServer((settings['host'], settings['port']), BMSRequestHandler)
        logging.info(f"Web server started on {settings['host']}:{settings['port']}")
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
    global previous_temps, previous_bank_medians, run_count, startup_offsets, startup_median, startup_set, battery_voltages, web_data
    settings = load_config()
    setup_hardware(settings)
    start_web_server(settings)
    startup_self_test(settings, stdscr)
    signal.signal(signal.SIGINT, signal_handler)
    
    startup_median, startup_offsets = load_offsets()
    if startup_offsets and len(startup_offsets) == settings['num_channels']:
        startup_set = True
        logging.info(f"Loaded startup median: {startup_median:.1f}°C")
    previous_temps = None
    previous_bank_medians = [None] * NUM_BANKS
    run_count = 0
    web_data['system_status'] = 'Running'
    
    while True:
        logging.info("Starting poll cycle.")
        web_data['last_update'] = time.time()
        
        temp_result = read_ntc_sensors(settings['ip'], settings['port'], settings['query_delay'], settings['num_channels'], settings['scaling_factor'], settings['max_retries'], settings['retry_backoff_base'])
        temps_alerts = []
        if isinstance(temp_result, str):
            temps_alerts.append(temp_result)
            calibrated_temps = [None] * settings['num_channels']
            raw_temps = [settings['valid_min']] * settings['num_channels']
            bank_medians = [0.0] * NUM_BANKS
        else:
            valid_count = sum(1 for t in temp_result if t > settings['valid_min'])
            if not startup_set and valid_count == settings['num_channels']:
                startup_median = statistics.median(temp_result)
                startup_offsets = [startup_median - raw for raw in temp_result]
                save_offsets(startup_median, startup_offsets)
                startup_set = True
                logging.info(f"Temp calibration set. Median: {startup_median:.1f}°C")
            
            if startup_set and startup_offsets is None:
                startup_set = False
            
            calibrated_temps = [temp_result[i] + startup_offsets[i] if startup_set and temp_result[i] > settings['valid_min'] else temp_result[i] if temp_result[i] > settings['valid_min'] else None for i in range(settings['num_channels'])]
            raw_temps = temp_result
            bank_medians = compute_bank_medians(calibrated_temps, settings['valid_min'])
            
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
                    start, end = BANK_RANGES[bank_id - 1]
                    for ch in range(start, end + 1):
                        calib = calibrated_temps[ch - 1]
                        if calib is not None:
                            check_abnormal_rise(calib, previous_temps, ch, temps_alerts, settings['poll_interval'], settings['rise_threshold'])
                            check_group_tracking_lag(calib, previous_temps, bank_median_rise, ch, temps_alerts, settings['disconnection_lag_threshold'])
                        check_sudden_disconnection(calib, previous_temps, ch, temps_alerts)
            
            previous_temps = calibrated_temps[:]
            previous_bank_medians = bank_medians[:]
        
        battery_voltages = []
        for i in range(1, NUM_BANKS + 1):
            v, _, _ = read_voltage_with_retry(i, settings)
            battery_voltages.append(v if v is not None else 0.0)
        
        alert_needed, all_alerts = check_for_issues(battery_voltages, temps_alerts, settings)
        
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
        web_data['alerts'] = all_alerts
        web_data['balancing'] = balancing_active
        web_data['last_update'] = time.time()
        web_data['system_status'] = 'Alert' if alert_needed else 'Running'
        
        draw_tui(stdscr, battery_voltages, calibrated_temps, raw_temps, startup_offsets or [0]*settings['num_channels'], bank_medians, startup_median, all_alerts, settings, startup_set, is_startup=(run_count == 0))
        
        run_count += 1
        gc.collect()
        logging.info("Poll cycle complete.")
        time.sleep(min(settings['poll_interval'], settings['SleepTimeBetweenChecks']))

if __name__ == '__main__':
    curses.wrapper(main)