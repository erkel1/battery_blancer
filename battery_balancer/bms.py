"""
Combined Battery Temperature Monitoring and Balancing Script (Updated for 3s8p Configuration)

Extensive Summary:
This script serves as a comprehensive Battery Management System (BMS) for a 3s8p battery configuration (3 series-connected parallel battery banks, each with 8 cells). It integrates temperature monitoring from NTC sensors via a Lantronix EDS4100 device (using Modbus RTU over TCP) with voltage balancing using I2C-based ADC for readings and relays/GPIO for control. The system runs in an infinite loop, polling data at configurable intervals, detecting anomalies, balancing voltages if imbalances exceed thresholds, logging events, sending email alerts for critical issues, and displaying real-time status in a curses-based Text User Interface (TUI).

What it does:
- Reads temperatures from 24 NTC sensors (grouped into 3 banks: channels 1-8, 9-16, 17-24).
- Calibrates temperatures at startup (aligns to median offset if all valid) and persists offsets.
- Detects temp anomalies: invalid, high/low, deviation from bank median, abnormal rise, group lag, sudden disconnection.
- Reads voltages from 3 banks using ADS1115 ADC over I2C, with retries and calibration.
- Checks voltage issues: zero, high, low.
- Balances voltages: If max-min > threshold and no alerts, connects high to low bank via relays, turns on DC-DC converter for duration, shows progress.
- Alerts: Logs issues, activates GPIO alarm relay, sends throttled emails.
- TUI: ASCII art batteries with voltages/temps inside (full details at startup, compact updates), ADC/readings, alerts; no pauses.
- Handles shutdown: Ctrl+C cleans GPIO.
- Startup Self-Check: Validates config, hardware connectivity, initial sensor reads; alarms if fails but continues monitoring.

How it does it:
- Config loaded from 'battery_monitor.ini' with fallbacks.
- Hardware setup: I2C bus, GPIO pins.
- Startup check: Validate config, test connections/reads; alarm on failure.
- Infinite loop: Poll temps/voltages (retry on invalid), process/calibrate, check alerts, balance if needed, draw TUI, sleep.
- Logging: To 'battery_monitor.log' at INFO level, verbose for steps/errors.
- Edges: Retries on reads, guards for None, exponential backoff.

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

Dependencies: socket, statistics, time, configparser, logging, signal, gc, os, smbus, RPi.GPIO, smtplib, email.mime.text.MIMEText, curses, sys, art (pip install art).
Note: Ensure EDS4100 configured for Modbus RTU tunneling, INI file present, hardware connected.
Relay Documentation: The script uses two relay systems:
- Balancing Relays: Controlled via I2C at RelayAddress (e.g., PCA9536), bits set for bank pairs (hardcoded logic for 3 banks, not configurable beyond address).
- Alarm Relay: GPIO pin (AlarmRelayPin) activated on any alert (temp/volt issues), for buzzer/light; deactivates when clear.
Undocumented Features: offsets.txt (generated for temp calibration persistence, not user-editable); hardcoded NUM_BANKS=3 (overrides config if mismatch); TUI art/colors fixed (no config); no SMTP login (assumes open or env vars for auth if needed).
"""

import socket  # For TCP connection to EDS4100 device
import statistics  # For median calculations on temperatures
import time  # For delays and timing
import configparser  # For loading settings from INI file
import logging  # For logging events to file
import signal  # For handling Ctrl+C graceful shutdown
import gc  # For manual garbage collection in long-running loop
import os  # For file operations like offsets.txt
import smbus  # For I2C communication with ADC/relays
import RPi.GPIO as GPIO  # For GPIO control of relays/converter
from email.mime.text import MIMEText  # For constructing email messages
import smtplib  # For sending emails
import curses  # For terminal-based TUI
import sys  # For sys.exit on shutdown
from art import text2art  # For ASCII art total voltage

logging.basicConfig(filename='battery_monitor.log', level=logging.INFO, format='%(asctime)s - %(message)s')  # Setup logging early

# Global variables
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
alert_states = {}  # Per-channel alert tracking (unused in current version)
balancing_active = False  # Flag if balancing in progress
startup_failed = False  # Persistent flag for startup failures
startup_alerts = []  # New: Store startup failures for TUI alerts

# Bank definitions
BANK_RANGES = [(1, 8), (9, 16), (17, 24)]  # Channel ranges for each bank
NUM_BANKS = 3  # Number of banks (hardcoded for 3s8p)

def get_bank_for_channel(ch):
    """Get bank ID for a given channel."""
    for bank_id, (start, end) in enumerate(BANK_RANGES, 1):
        if start <= ch <= end:
            return bank_id
    return None

def modbus_crc(data):
    """Calculate Modbus CRC for data integrity."""
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 0x0001:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return crc.to_bytes(2, 'little')

def read_ntc_sensors(ip, port, query_delay, num_channels, scaling_factor, max_retries, retry_backoff_base):
    """Read NTC sensor temperatures via Modbus over TCP with retries."""
    logging.info("Starting temperature sensor read.")
    query_base = bytes([1, 3]) + (0).to_bytes(2, 'big') + (num_channels).to_bytes(2, 'big')
    crc = modbus_crc(query_base)
    query = query_base + crc
    
    for attempt in range(max_retries):
        try:
            logging.debug(f"Temp read attempt {attempt+1}: Connecting to {ip}:{port}")
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)  # Create TCP socket
            s.settimeout(3)  # Set timeout for operations
            s.connect((ip, port))  # Connect to device
            s.send(query)  # Send Modbus query
            time.sleep(query_delay)  # Delay for device response
            response = s.recv(1024)  # Receive response
            s.close()  # Close socket
            
            if len(response) < 5:
                raise ValueError("Short response")  # Check minimum length
            
            if len(response) != 3 + response[2] + 2:
                raise ValueError("Invalid response length")  # Validate length
            calc_crc = modbus_crc(response[:-2])  # Recalculate CRC
            if calc_crc != response[-2:]:
                raise ValueError("CRC mismatch")  # Check CRC
            
            slave, func, byte_count = response[0:3]  # Parse header
            if slave != 1 or func != 3 or byte_count != num_channels * 2:
                if func & 0x80:
                    return f"Error: Modbus exception code {response[2]}"  # Handle exception
                return "Error: Invalid response header."  # Invalid header
            
            data = response[3:3 + byte_count]  # Extract data
            raw_temperatures = []  # List for raw temps
            for i in range(0, len(data), 2):
                val = int.from_bytes(data[i:i+2], 'big', signed=True) / scaling_factor  # Convert to temp
                raw_temperatures.append(val)
            
            logging.info("Temperature read successful.")
            return raw_temperatures
        
        except Exception as e:
            logging.warning(f"Temp read attempt {attempt+1} failed: {str(e)}. Retrying.")
            if attempt < max_retries - 1:
                time.sleep(retry_backoff_base ** attempt)  # Exponential backoff
            else:
                logging.error(f"Temp read failed after {max_retries} attempts - {str(e)}.")
                return f"Error: Failed after {max_retries} attempts - {str(e)}."

def load_config():
    """Load settings from 'battery_monitor.ini' with fallbacks."""
    logging.info("Loading configuration from 'battery_monitor.ini'.")
    global alert_states
    if not config_parser.read('battery_monitor.ini'):
        logging.error("Config file 'battery_monitor.ini' not found.")
        raise FileNotFoundError("Config file 'battery_monitor.ini' not found.")
    
    # Temp settings
    temp_settings = {
        'ip': config_parser.get('Temp', 'ip', fallback='192.168.15.240'),  # Device IP
        'port': config_parser.getint('Temp', 'port', fallback=10001),  # Device port
        'poll_interval': config_parser.getfloat('Temp', 'poll_interval', fallback=10.0),  # Poll frequency
        'rise_threshold': config_parser.getfloat('Temp', 'rise_threshold', fallback=2.0),  # Rise alert threshold
        'deviation_threshold': config_parser.getfloat('Temp', 'deviation_threshold', fallback=0.1),  # Relative deviation
        'disconnection_lag_threshold': config_parser.getfloat('Temp', 'disconnection_lag_threshold', fallback=0.5),  # Lag threshold
        'high_threshold': config_parser.getfloat('Temp', 'high_threshold', fallback=60.0),  # High temp threshold
        'low_threshold': config_parser.getfloat('Temp', 'low_threshold', fallback=0.0),  # Low temp threshold
        'scaling_factor': config_parser.getfloat('Temp', 'scaling_factor', fallback=100.0),  # Raw value scaling
        'valid_min': config_parser.getfloat('Temp', 'valid_min', fallback=0.0),  # Min valid temp
        'max_retries': config_parser.getint('Temp', 'max_retries', fallback=3),  # Read retries
        'retry_backoff_base': config_parser.getint('Temp', 'retry_backoff_base', fallback=1),  # Backoff base
        'query_delay': config_parser.getfloat('Temp', 'query_delay', fallback=0.25),  # Query delay
        'num_channels': config_parser.getint('Temp', 'num_channels', fallback=24),  # Number of channels
        'abs_deviation_threshold': config_parser.getfloat('Temp', 'abs_deviation_threshold', fallback=2.0)  # Absolute deviation
    }
    
    # Voltage/Balance settings (updated from revised)
    voltage_settings = {
        'NumberOfBatteries': config_parser.getint('General', 'NumberOfBatteries', fallback=3),  # Number of banks
        'VoltageDifferenceToBalance': config_parser.getfloat('General', 'VoltageDifferenceToBalance', fallback=0.1),  # Balance trigger
        'BalanceDurationSeconds': config_parser.getint('General', 'BalanceDurationSeconds', fallback=10),  # Balance time
        'SleepTimeBetweenChecks': config_parser.getfloat('General', 'SleepTimeBetweenChecks', fallback=5.0),  # Poll sleep
        'BalanceRestPeriodSeconds': config_parser.getint('General', 'BalanceRestPeriodSeconds', fallback=30),  # Rest after balance
        'LowVoltageThresholdPerBattery': config_parser.getfloat('General', 'LowVoltageThresholdPerBattery', fallback=3.0),  # Low volt threshold
        'HighVoltageThresholdPerBattery': config_parser.getfloat('General', 'HighVoltageThresholdPerBattery', fallback=4.2),  # High volt threshold
        'EmailAlertIntervalSeconds': config_parser.getint('General', 'EmailAlertIntervalSeconds', fallback=300),  # Email throttle
        'I2C_BusNumber': config_parser.getint('General', 'I2C_BusNumber', fallback=1),  # I2C bus
        'VoltageDividerRatio': config_parser.getfloat('General', 'VoltageDividerRatio', fallback=0.01592),  # Divider ratio
        'MultiplexerAddress': int(config_parser.get('I2C', 'MultiplexerAddress', fallback='0x70'), 16),  # Multiplexer addr
        'VoltageMeterAddress': int(config_parser.get('I2C', 'VoltageMeterAddress', fallback='0x48'), 16),  # ADC addr
        'RelayAddress': int(config_parser.get('I2C', 'RelayAddress', fallback='0x10'), 16),  # Relay addr
        'DC_DC_RelayPin': config_parser.getint('GPIO', 'DC_DC_RelayPin', fallback=17),  # DC-DC pin
        'AlarmRelayPin': config_parser.getint('GPIO', 'AlarmRelayPin', fallback=27),  # Alarm pin
        'SMTP_Server': config_parser.get('Email', 'SMTP_Server', fallback='smtp.example.com'),  # SMTP server
        'SMTP_Port': config_parser.getint('Email', 'SMTP_Port', fallback=587),  # SMTP port
        'SenderEmail': config_parser.get('Email', 'SenderEmail', fallback='alert@example.com'),  # Sender email
        'RecipientEmail': config_parser.get('Email', 'RecipientEmail', fallback='admin@example.com'),  # Recipient email
        'ConfigRegister': int(config_parser.get('ADC', 'ConfigRegister', fallback='0x01'), 16),  # ADC config reg
        'ConversionRegister': int(config_parser.get('ADC', 'ConversionRegister', fallback='0x00'), 16),  # ADC conv reg
        'ContinuousModeConfig': int(config_parser.get('ADC', 'ContinuousModeConfig', fallback='0x4000'), 16),  # ADC mode
        'SampleRateConfig': int(config_parser.get('ADC', 'SampleRateConfig', fallback='0x1400'), 16),  # ADC rate
        'GainConfig': int(config_parser.get('ADC', 'GainConfig', fallback='0x2000'), 16),  # ADC gain
        'Sensor1_Calibration': config_parser.getfloat('Calibration', 'Sensor1_Calibration', fallback=1.0),  # Sensor 1 calib
        'Sensor2_Calibration': config_parser.getfloat('Calibration', 'Sensor2_Calibration', fallback=1.0),  # Sensor 2 calib
        'Sensor3_Calibration': config_parser.getfloat('Calibration', 'Sensor3_Calibration', fallback=1.0)  # Sensor 3 calib
    }
    
    # Startup settings
    startup_settings = {
        'test_balance_duration': config_parser.getint('Startup', 'test_balance_duration', fallback=15),  # Balance test duration
        'min_voltage_delta': config_parser.getfloat('Startup', 'min_voltage_delta', fallback=0.01),  # Min delta for pass
        'test_read_interval': config_parser.getfloat('Startup', 'test_read_interval', fallback=2.0)  # Read interval during test
    }
    
    if voltage_settings['NumberOfBatteries'] != NUM_BANKS:
        logging.warning(f"NumberOfBatteries ({voltage_settings['NumberOfBatteries']}) does not match NUM_BANKS ({NUM_BANKS}); using {NUM_BANKS} for banks.")
    
    alert_states = {ch: {'last_type': None, 'count': 0} for ch in range(1, temp_settings['num_channels'] + 1)}  # Per-channel alert states (unused currently)
    
    logging.info("Configuration loaded successfully.")
    return {**temp_settings, **voltage_settings, **startup_settings}

def setup_hardware(settings):
    """Initialize I2C bus and GPIO pins."""
    global bus
    logging.info("Setting up hardware.")
    bus = smbus.SMBus(settings['I2C_BusNumber'])  # Initialize I2C bus
    GPIO.setmode(GPIO.BCM)  # Set GPIO mode
    GPIO.setup(settings['DC_DC_RelayPin'], GPIO.OUT, initial=GPIO.LOW)  # Setup DC-DC pin low
    GPIO.setup(settings['AlarmRelayPin'], GPIO.OUT, initial=GPIO.LOW)  # Setup alarm pin low
    logging.info("Hardware setup complete.")

def signal_handler(sig, frame):
    """Handle SIGINT for graceful shutdown."""
    logging.info("Script stopped by user or signal.")
    GPIO.cleanup()  # Clean GPIO
    sys.exit(0)  # Exit script

def load_offsets():
    """Load temp offsets from file if exists."""
    logging.info("Loading startup offsets from 'offsets.txt'.")
    if os.path.exists('offsets.txt'):
        with open('offsets.txt', 'r') as f:
            lines = f.readlines()
            if len(lines) < 1:
                logging.warning("Invalid offsets.txt; using none.")
                return None
            startup_median = float(lines[0].strip())
            offsets = [float(line.strip()) for line in lines[1:]]
            if len(offsets) != 24:  # Assume num_channels=24
                logging.warning("Invalid offsets count; using none.")
                return None
            logging.debug(f"Loaded median {startup_median} and {len(offsets)} offsets.")
            return startup_median, offsets
    logging.warning("No 'offsets.txt' found; using none.")
    return None, None

def save_offsets(startup_median, offsets):
    """Save temp median and offsets to file."""
    logging.info("Saving startup offsets to 'offsets.txt'.")
    with open('offsets.txt', 'w') as f:
        f.write(f"{startup_median}\n")  # First line: median
        for offset in offsets:
            f.write(f"{offset}\n")  # Then offsets
    logging.debug("Offsets saved.")

def check_invalid_reading(raw, ch, alerts, valid_min):
    """Check if raw temp is invalid."""
    if raw <= valid_min:
        bank = get_bank_for_channel(ch)  # Get bank
        alerts.append(f"Bank {bank} Ch {ch}: Invalid reading (≤ {valid_min}).")  # Add alert
        logging.warning(f"Invalid reading on Bank {bank} Ch {ch}: {raw} ≤ {valid_min}.")  # Log warning
        return True  # Invalid
    return False  # Valid

def check_high_temp(calibrated, ch, alerts, high_threshold):
    """Check for high temperature."""
    if calibrated > high_threshold:
        bank = get_bank_for_channel(ch)  # Get bank
        alerts.append(f"Bank {bank} Ch {ch}: High temp ({calibrated:.1f}°C > {high_threshold}°C).")  # Add alert
        logging.warning(f"High temp alert on Bank {bank} Ch {ch}: {calibrated:.1f} > {high_threshold}.")  # Log warning

def check_low_temp(calibrated, ch, alerts, low_threshold):
    """Check for low temperature."""
    if calibrated < low_threshold:
        bank = get_bank_for_channel(ch)  # Get bank
        alerts.append(f"Bank {bank} Ch {ch}: Low temp ({calibrated:.1f}°C < {low_threshold}°C).")  # Add alert
        logging.warning(f"Low temp alert on Bank {bank} Ch {ch}: {calibrated:.1f} < {low_threshold}.")  # Log warning

def check_deviation(calibrated, bank_median, ch, alerts, abs_deviation_threshold, deviation_threshold):
    """Check deviation from bank median."""
    abs_dev = abs(calibrated - bank_median)  # Absolute deviation
    rel_dev = abs_dev / abs(bank_median) if bank_median != 0 else 0  # Relative deviation
    if abs_dev > abs_deviation_threshold or rel_dev > deviation_threshold:
        bank = get_bank_for_channel(ch)  # Get bank
        alerts.append(f"Bank {bank} Ch {ch}: Deviation from bank median (abs {abs_dev:.1f}°C or {rel_dev:.2%}).")  # Add alert
        logging.warning(f"Deviation alert on Bank {bank} Ch {ch}: abs {abs_dev:.1f}, rel {rel_dev:.2%}.")  # Log warning

def check_abnormal_rise(current, previous_temps, ch, alerts, poll_interval, rise_threshold):
    """Check for abnormal temp rise since last poll."""
    previous = previous_temps[ch-1]  # Previous calib
    if previous is not None:
        rise = current - previous  # Calculate rise
        if rise > rise_threshold:
            bank = get_bank_for_channel(ch)  # Get bank
            alerts.append(f"Bank {bank} Ch {ch}: Abnormal rise ({rise:.1f}°C in {poll_interval}s).")  # Add alert
            logging.warning(f"Abnormal rise alert on Bank {bank} Ch {ch}: {rise:.1f}°C.")  # Log warning

def check_group_tracking_lag(current, previous_temps, bank_median_rise, ch, alerts, disconnection_lag_threshold):
    """Check if channel rise lags bank median rise."""
    previous = previous_temps[ch-1]  # Previous calib
    if previous is not None:
        rise = current - previous  # Calculate rise
        if abs(rise - bank_median_rise) > disconnection_lag_threshold:
            bank = get_bank_for_channel(ch)  # Get bank
            alerts.append(f"Bank {bank} Ch {ch}: Lag from bank group ({rise:.1f}°C vs {bank_median_rise:.1f}°C).")  # Add alert
            logging.warning(f"Lag alert on Bank {bank} Ch {ch}: rise {rise:.1f} vs median {bank_median_rise:.1f}.")  # Log warning

def check_sudden_disconnection(current, previous_temps, ch, alerts):
    """Check for sudden sensor disconnection."""
    previous = previous_temps[ch-1]  # Previous calib
    if previous is not None and current is None:
        bank = get_bank_for_channel(ch)  # Get bank
        alerts.append(f"Bank {bank} Ch {ch}: Sudden disconnection.")  # Add alert
        logging.warning(f"Sudden disconnection alert on Bank {bank} Ch {ch}.")  # Log warning

def choose_channel(channel, multiplexer_address):
    """Select I2C multiplexer channel."""
    logging.debug(f"Switching to I2C channel {channel}.")
    bus.write_byte(multiplexer_address, 1 << channel)  # Write channel select

def setup_voltage_meter(settings):
    """Configure ADC for voltage measurement."""
    logging.debug("Configuring voltage meter ADC.")
    config_value = (settings['ContinuousModeConfig'] | 
                    settings['SampleRateConfig'] | 
                    settings['GainConfig'])  # Combine config bits
    bus.write_word_data(settings['VoltageMeterAddress'], settings['ConfigRegister'], config_value)  # Write to ADC

def read_voltage_with_retry(bank_id, settings):
    """Read bank voltage with retries and averaging."""
    logging.info(f"Starting voltage read for Bank {bank_id}.")
    voltage_divider_ratio = settings['VoltageDividerRatio']  # Divider ratio
    sensor_id = bank_id  # Sensor ID matches bank
    calibration_factor = settings[f'Sensor{sensor_id}_Calibration']  # Calibration factor
    for attempt in range(2):  # 2 attempts
        logging.debug(f"Voltage read attempt {attempt+1} for Bank {bank_id}.")
        readings = []  # List for voltage readings
        raw_values = []  # List for raw ADC
        for _ in range(2):  # 2 samples
            meter_channel = (bank_id - 1) % 3  # Channel for bank
            choose_channel(meter_channel, settings['MultiplexerAddress'])  # Select channel
            setup_voltage_meter(settings)  # Configure ADC
            bus.write_byte(settings['VoltageMeterAddress'], 0x01)  # Start conversion
            time.sleep(0.05)  # Delay for conversion
            raw_adc = bus.read_word_data(settings['VoltageMeterAddress'], settings['ConversionRegister'])  # Read raw
            raw_adc = (raw_adc & 0xFF) << 8 | (raw_adc >> 8)  # Swap for little-endian
            logging.debug(f"Raw ADC for Bank {bank_id} (Sensor {sensor_id}): {raw_adc}")  # Log raw
            if raw_adc != 0:
                measured_voltage = raw_adc * (6.144 / 32767)  # Convert to voltage
                actual_voltage = (measured_voltage / voltage_divider_ratio) * calibration_factor  # Apply divider/calib
                readings.append(actual_voltage)  # Add reading
                raw_values.append(raw_adc)  # Add raw
            else:
                readings.append(0.0)  # Zero reading
                raw_values.append(0)  # Zero raw
        if readings:
            average = sum(readings) / len(readings)  # Average readings
            valid_readings = [r for r in readings if abs(r - average) / (average if average != 0 else 1) <= 0.05]  # Filter outliers
            valid_adc = [raw_values[i] for i, r in enumerate(readings) if abs(r - average) / (average if average != 0 else 1) <= 0.05]  # Filter ADC
            if valid_readings:
                logging.info(f"Voltage read successful for Bank {bank_id}: {average:.2f}V.")
                return sum(valid_readings) / len(valid_readings), valid_readings, valid_adc  # Return average, readings, ADC
        logging.debug(f"Readings for Bank {bank_id} inconsistent, retrying.")
    logging.error(f"Couldn't get good voltage reading for Bank {bank_id} after 2 tries.")
    return None, [], []  # Failure return

def set_relay_connection(high, low, settings):
    """Set relays for balancing between banks."""
    try:
        logging.info(f"Attempting to set relay for connection from Bank {high} to {low}")
        logging.debug("Switching to relay control channel.")
        choose_channel(3, settings['MultiplexerAddress'])  # Select relay channel
        relay_state = 0  # Initial state
        if high == 1 and low == 2:
            relay_state |= (1 << 0) | (1 << 1) | (1 << 3)  # Example: Relays 1,2,4 on
            logging.debug("Relays 1, 2, and 4 activated for high to low.")
        elif high == 1 and low == 3:
            relay_state |= (1 << 1) | (1 << 2) | (1 << 3)  # Relays 2,3,4 on
            logging.debug("Relays 2, 3, and 4 activated for high to low.")
        elif high == 2 and low == 1:
            relay_state |= (1 << 0) | (1 << 2) | (1 << 3)  # Relays 1,3,4 on
            logging.debug("Relays 1, 3, and 4 activated for high to low.")
        elif high == 2 and low == 3:
            relay_state |= (1 << 0) | (1 << 1) | (1 << 2)  # Relays 1,2,3 on
            logging.debug("Relays 1, 2, and 3 activated for high to low.")
        elif high == 3 and low == 1:
            relay_state |= (1 << 0) | (1 << 1) | (1 << 2)  # Relays 1,2,3 on
            logging.debug("Relays 1, 2, and 3 activated for high to low.")
        elif high == 3 and low == 2:
            relay_state |= (1 << 0) | (1 << 1) | (1 << 3)  # Relays 1,2,4 on
            logging.debug("Relays 1, 2, and 4 activated for high to low.")

        logging.debug(f"Final relay state: {bin(relay_state)}")  # Log final state
        logging.info(f"Sending relay state command to hardware.")  # Log send
        bus.write_byte_data(settings['RelayAddress'], 0x11, relay_state)  # Write state
        logging.info(f"Relay setup completed for balancing from Bank {high} to {low}")  # Log completion
    except IOError as e:
        logging.error(f"I/O error while setting up relay: {e}")  # Log IO error
    except Exception as e:
        logging.error(f"Unexpected error in set_relay_connection: {e}")  # Log unexpected

def control_dcdc_converter(turn_on, settings):
    """Turn DC-DC converter on/off via GPIO."""
    try:
        GPIO.output(settings['DC_DC_RelayPin'], GPIO.HIGH if turn_on else GPIO.LOW)  # Set pin state
        logging.info(f"DC-DC Converter is now {'on' if turn_on else 'off'}")  # Log state
    except Exception as e:
        logging.error(f"Problem controlling DC-DC converter: {e}")  # Log error

def send_alert_email(message, settings):
    """Send email alert with throttling."""
    global last_email_time
    if time.time() - last_email_time < settings['EmailAlertIntervalSeconds']:
        logging.debug("Skipping alert email to avoid flooding.")  # Log skip
        return
    try:
        msg = MIMEText(message)  # Create message
        msg['Subject'] = "Battery Monitor Alert"  # Set subject
        msg['From'] = settings['SenderEmail']  # Set from
        msg['To'] = settings['RecipientEmail']  # Set to
        with smtplib.SMTP(settings['SMTP_Server'], settings['SMTP_Port']) as server:
            server.send_message(msg)  # Send email
        last_email_time = time.time()  # Update timestamp
        logging.info(f"Alert email sent: {message}")  # Log sent
    except Exception as e:
        logging.error(f"Failed to send alert email: {e}")  # Log failure

def check_for_issues(voltages, temps_alerts, settings):
    """Check voltage/temp issues, trigger alerts/relay."""
    global startup_failed, startup_alerts
    logging.info("Checking for voltage and temp issues.")
    alert_needed = startup_failed  # Persistent from startup
    alerts = []
    if startup_failed and startup_alerts:
        alerts.append("Startup failures: " + "; ".join(startup_alerts))
    for i, v in enumerate(voltages, 1):
        if v is None or v == 0.0:
            alerts.append(f"Bank {i}: Zero voltage.")  # Add alert
            logging.warning(f"Zero voltage alert on Bank {i}.")
            alert_needed = True
        elif v > settings['HighVoltageThresholdPerBattery']:
            alerts.append(f"Bank {i}: High voltage ({v:.2f}V).")  # Add alert
            logging.warning(f"High voltage alert on Bank {i}: {v:.2f}V.")  # Log
            alert_needed = True
        elif v < settings['LowVoltageThresholdPerBattery']:
            alerts.append(f"Bank {i}: Low voltage ({v:.2f}V).")  # Add alert
            logging.warning(f"Low voltage alert on Bank {i}: {v:.2f}V.")  # Log
            alert_needed = True
    if temps_alerts:
        alerts.extend(temps_alerts)  # Add temp alerts
        alert_needed = True
    if alert_needed:
        GPIO.output(settings['AlarmRelayPin'], GPIO.HIGH)  # Activate relay
        logging.info("Alarm relay activated.")  # Log activation
        send_alert_email("\n".join(alerts), settings)  # Send email
    else:
        GPIO.output(settings['AlarmRelayPin'], GPIO.LOW)  # Deactivate relay
        logging.info("No issues; alarm relay deactivated.")  # Log no issues
    return alert_needed, alerts

def balance_battery_voltages(stdscr, high, low, settings, temps_alerts):
    """Balance voltages between high and low banks with progress in TUI."""
    global balance_start_time, last_balance_time, balancing_active
    if temps_alerts:
        logging.warning("Skipping balancing due to temperature anomalies in banks.")
        return
    logging.info(f"Starting balance from Bank {high} to {low}.")
    balancing_active = True
    voltage_high, _, _ = read_voltage_with_retry(high, settings)  # Read high
    voltage_low, _, _ = read_voltage_with_retry(low, settings)  # Read low
    if voltage_low == 0.0:
        logging.warning(f"Cannot balance to Bank {low} (0.00V). Skipping.")
        balancing_active = False
        return
    set_relay_connection(high, low, settings)  # Set relays
    control_dcdc_converter(True, settings)  # Turn on converter
    balance_start_time = time.time()  # Start timer
    animation_frames = ['|', '/', '-', '\\']  # Spinner frames
    frame_index = 0  # Spinner index
    progress_y = 17 + 6 + 2  # Hardcoded position below art + ADC + margin
    height, _ = stdscr.getmaxyx()  # Get height for bounds
    while time.time() - balance_start_time < settings['BalanceDurationSeconds']:
        elapsed = time.time() - balance_start_time  # Elapsed time
        progress = min(1.0, elapsed / settings['BalanceDurationSeconds'])  # Progress fraction
        voltage_high, _, _ = read_voltage_with_retry(high, settings)  # Re-read high
        voltage_low, _, _ = read_voltage_with_retry(low, settings)  # Re-read low
        bar_length = 20  # Bar length
        filled = int(bar_length * progress)  # Filled part
        bar = '=' * filled + ' ' * (bar_length - filled)  # Build bar
        if progress_y < height and progress_y + 1 < height:
            try:
                stdscr.addstr(progress_y, 0, f"Balancing Bank {high} ({voltage_high:.2f}V) -> Bank {low} ({voltage_low:.2f}V)... [{animation_frames[frame_index % 4]}]", curses.color_pair(6))  # Show status if in bounds
            except curses.error:
                logging.warning("addstr error for balancing status.")
            try:
                stdscr.addstr(progress_y + 1, 0, f"Progress: [{bar}] {int(progress * 100)}%", curses.color_pair(6))  # Show progress if in bounds
            except curses.error:
                logging.warning("addstr error for balancing progress bar.")
        else:
            logging.warning("Skipping balancing progress display - out of bounds.")
        stdscr.refresh()  # Refresh TUI
        logging.debug(f"Balancing progress: {progress * 100:.2f}%, High: {voltage_high:.2f}V, Low: {voltage_low:.2f}V")  # Log progress
        frame_index += 1  # Next frame
        time.sleep(0.01)  # Small delay
    logging.info("Balancing process completed.")
    control_dcdc_converter(False, settings)  # Turn off converter
    logging.info("Turning off DC-DC converter.")
    set_relay_connection(0, 0, settings)  # Reset relays
    logging.info("Resetting relay connections to default state.")
    balancing_active = False  # Reset flag
    last_balance_time = time.time()  # Update last time

def compute_bank_medians(calibrated_temps, valid_min):
    """Compute median temps per bank."""
    bank_medians = []
    for start, end in BANK_RANGES:
        bank_temps = [calibrated_temps[i-1] for i in range(start, end+1) if calibrated_temps[i-1] is not None]  # Valid temps
        bank_median = statistics.median(bank_temps) if bank_temps else 0.0  # Median or 0
        bank_medians.append(bank_median)
    return bank_medians

def draw_tui(stdscr, voltages, calibrated_temps, raw_temps, offsets, bank_medians, startup_median, alerts, settings, startup_set, is_startup):
    """Draw the TUI with battery art, temps inside, ADC, alerts."""
    logging.debug("Refreshing TUI.")
    stdscr.clear()  # Clear screen
    # Colors
    curses.start_color()  # Start color mode
    curses.use_default_colors()  # Use terminal defaults
    TITLE_COLOR = curses.color_pair(1)  # Red for title
    HIGH_V = curses.color_pair(2)  # Red for high
    LOW_V = curses.color_pair(3)  # Yellow for low
    OK_V = curses.color_pair(4)  # Green for OK
    ADC_C = curses.color_pair(5)  # Light for ADC
    BAL_C = curses.color_pair(6)  # Yellow for balancing
    INFO_C = curses.color_pair(7)  # Cyan for info
    ERR_C = curses.color_pair(8)  # Magenta for errors
    curses.init_pair(1, curses.COLOR_RED, -1)  # Init pair 1
    curses.init_pair(2, curses.COLOR_RED, -1)  # Init pair 2
    curses.init_pair(3, curses.COLOR_YELLOW, -1)  # Init pair 3
    curses.init_pair(4, curses.COLOR_GREEN, -1)  # Init pair 4
    curses.init_pair(5, curses.COLOR_WHITE, -1)  # Init pair 5
    curses.init_pair(6, curses.COLOR_YELLOW, -1)  # Init pair 6
    curses.init_pair(7, curses.COLOR_CYAN, -1)  # Init pair 7
    curses.init_pair(8, curses.COLOR_MAGENTA, -1)  # Init pair 8
    
    # Get screen dimensions for bounds checks
    height, width = stdscr.getmaxyx()  # Screen height/width
    
    # Total voltage
    total_v = sum(voltages)  # Sum bank voltages
    total_high = settings['HighVoltageThresholdPerBattery'] * NUM_BANKS  # High threshold total
    total_low = settings['LowVoltageThresholdPerBattery'] * NUM_BANKS  # Low threshold total
    v_color = HIGH_V if total_v > total_high else LOW_V if total_v < total_low else OK_V  # Color for total
    roman_v = text2art(f"{total_v:.2f}V", font='roman', chr_ignore=True)  # Art for total
    roman_lines = roman_v.splitlines()  # Split lines
    for i, line in enumerate(roman_lines):
        if i + 1 < height and len(line) < width:
            try:
                stdscr.addstr(i + 1, 0, line, v_color)  # Draw if in bounds
            except curses.error:
                logging.warning(f"addstr error for total voltage art line {i+1}.")
        else:
            logging.warning(f"Skipping total voltage art line {i+1} - out of bounds.")
    
    y_offset = len(roman_lines) + 2  # Offset after total art
    if y_offset >= height:
        logging.warning("TUI y_offset exceeds height; skipping art.")
        return  # Early exit if no space
    
    # Tall battery art with temps inside
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
    art_height = len(battery_art_base)  # Art height
    art_width = len(battery_art_base[0])  # Art width per bank
    
    # Draw base art for all banks side-by-side
    for row, line in enumerate(battery_art_base):
        full_line = line * NUM_BANKS  # Repeat for banks
        if y_offset + row < height and len(full_line) < width:
            try:
                stdscr.addstr(y_offset + row, 0, full_line, OK_V)  # Draw line if in bounds
            except curses.error:
                logging.warning(f"addstr error for art row {row}.")
        else:
            logging.warning(f"Skipping art row {row} - out of bounds.")
    
    # Overlay content inside each bank
    for bank_id in range(NUM_BANKS):
        start_pos = bank_id * art_width  # Start position for bank
        # Voltage on line 1, centered
        v_str = f"{voltages[bank_id]:.2f}V" if voltages[bank_id] > 0 else "0.00V"  # Voltage string
        v_color = ERR_C if voltages[bank_id] == 0.0 else HIGH_V if voltages[bank_id] > settings['HighVoltageThresholdPerBattery'] else LOW_V if voltages[bank_id] < settings['LowVoltageThresholdPerBattery'] else OK_V  # Fixed color check
        v_center = start_pos + (art_width - len(v_str)) // 2  # Center position
        v_y = y_offset + 1  # Y position
        if v_y < height and v_center + len(v_str) < width:
            try:
                stdscr.addstr(v_y, v_center, v_str, v_color)  # Overlay if in bounds
            except curses.error:
                logging.warning(f"addstr error for voltage overlay Bank {bank_id+1}.")
        else:
            logging.warning(f"Skipping voltage overlay for Bank {bank_id+1} - out of bounds.")
        
        # Temps on lines 2-9 (C1-C8)
        start, end = BANK_RANGES[bank_id]  # Channel range
        for local_ch, ch in enumerate(range(start, end + 1), 0):
            idx = ch - 1  # Index
            raw = raw_temps[idx] if idx < len(raw_temps) else 0  # Raw temp
            calib = calibrated_temps[idx]  # Calib temp
            calib_str = f"{calib:.1f}" if calib is not None else "Inv"  # Calib string
            if is_startup:
                raw_str = f"{raw:.1f}" if raw > settings['valid_min'] else "Inv"  # Raw string
                offset_str = f"{offsets[idx]:.1f}" if startup_set and raw > settings['valid_min'] else "N/A"  # Offset string
                detail = f" ({raw_str}/{offset_str})"  # Detail for startup
            else:
                detail = ""  # No detail for update
            t_str = f"C{local_ch+1}: {calib_str}{detail}"  # Temp string
            t_color = ERR_C if "Inv" in calib_str else HIGH_V if calib > settings['high_threshold'] else LOW_V if calib < settings['low_threshold'] else OK_V  # Color for temp
            t_center = start_pos + (art_width - len(t_str)) // 2  # Center position
            t_y = y_offset + 2 + local_ch  # Y position
            if t_y < height and t_center + len(t_str) < width:
                try:
                    stdscr.addstr(t_y, t_center, t_str, t_color)  # Overlay temp if in bounds
                except curses.error:
                    logging.warning(f"addstr error for temp overlay Bank {bank_id+1} C{local_ch+1}.")
            else:
                logging.warning(f"Skipping temp overlay for Bank {bank_id+1} C{local_ch+1} - out of bounds.")
        
        # Median on line 15
        med_str = f"Med: {bank_medians[bank_id]:.1f}°C"  # Median string
        med_center = start_pos + (art_width - len(med_str)) // 2  # Center position
        med_y = y_offset + 15  # Y position
        if med_y < height and med_center + len(med_str) < width:
            try:
                stdscr.addstr(med_y, med_center, med_str, INFO_C)  # Overlay if in bounds
            except curses.error:
                logging.warning(f"addstr error for median overlay Bank {bank_id+1}.")
        else:
            logging.warning(f"Skipping median overlay for Bank {bank_id+1} - out of bounds.")
    
    y_offset += art_height + 2  # Offset after art
    if y_offset >= height:
        logging.warning("Skipping ADC/readings - out of bounds.")
    else:
        # ADC/readings
        for i in range(1, NUM_BANKS + 1):
            voltage, readings, adc_values = read_voltage_with_retry(i, settings)  # Read with retry
            logging.debug(f"Bank {i} - Voltage: {voltage}, ADC: {adc_values}, Readings: {readings}")  # Log
            if voltage is None:
                voltage = 0.0  # Default on failure
            if y_offset < height:
                try:
                    stdscr.addstr(y_offset, 0, f"Bank {i}: (ADC: {adc_values[0] if adc_values else 'N/A'})", ADC_C)  # Show ADC if in bounds
                except curses.error:
                    logging.warning(f"addstr error for ADC Bank {i}.")
            else:
                logging.warning(f"Skipping ADC for Bank {i} - out of bounds.")
            y_offset += 1  # Next line
            if y_offset < height:
                try:
                    if readings:
                        stdscr.addstr(y_offset, 0, f"[Readings: {', '.join(f'{v:.2f}' for v in readings)}]", ADC_C)  # Show readings if in bounds
                    else:
                        stdscr.addstr(y_offset, 0, "  [Readings: No data]", ADC_C)  # No data
                except curses.error:
                    logging.warning(f"addstr error for readings Bank {i}.")
            else:
                logging.warning(f"Skipping readings for Bank {i} - out of bounds.")
            y_offset += 1  # Next line
    
    y_offset += 1  # Extra space
    
    # Startup median, fixed formatting
    med_str = f"{startup_median:.1f}°C" if startup_median else "N/A"  # Safe string
    if y_offset < height:
        try:
            stdscr.addstr(y_offset, 0, f"Startup Median Temp: {med_str}", INFO_C)  # Show median if in bounds
        except curses.error:
            logging.warning("addstr error for startup median.")
    else:
        logging.warning("Skipping startup median - out of bounds.")
    y_offset += 2  # Next lines
    
    # Alerts
    if y_offset < height:
        try:
            stdscr.addstr(y_offset, 0, "Alerts:", INFO_C)  # Alerts header if in bounds
        except curses.error:
            logging.warning("addstr error for alerts header.")
    y_offset += 1  # Next line
    if alerts:
        for alert in alerts:
            if y_offset < height:
                try:
                    stdscr.addstr(y_offset, 0, alert, ERR_C)  # Show alert if in bounds
                except curses.error:
                    logging.warning(f"addstr error for alert '{alert}'.")
            else:
                logging.warning(f"Skipping alert '{alert}' - out of bounds.")
            y_offset += 1  # Next line
    else:
        if y_offset < height:
            try:
                stdscr.addstr(y_offset, 0, "No alerts.", OK_V)  # No alerts if in bounds
            except curses.error:
                logging.warning("addstr error for no alerts message.")
        else:
            logging.warning("Skipping no alerts message - out of bounds.")
    
    stdscr.refresh()  # Refresh screen

def startup_self_test(settings, stdscr):
    """Perform startup self-test: config validation, hardware connectivity, initial reads, and balancer verification, with TUI progress."""
    global startup_failed, startup_alerts, startup_set, startup_median, startup_offsets
    logging.info("Starting self-test: Validating config, connectivity, sensors, and balancer.")
    alerts = []  # Collect test alerts
    stdscr.clear()  # Clear for startup TUI
    y = 0
    if y < stdscr.getmaxyx()[0]:
        try:
            stdscr.addstr(y, 0, "Startup Self-Test in Progress", curses.color_pair(1))  # Title
        except curses.error:
            logging.warning("addstr error for title.")
    y += 2
    stdscr.refresh()
    
    # Step 1: Config validation
    if y < stdscr.getmaxyx()[0]:
        try:
            stdscr.addstr(y, 0, "Step 1: Validating config...", curses.color_pair(4))  # Green
        except curses.error:
            logging.warning("addstr error for step 1.")
    stdscr.refresh()
    time.sleep(0.5)  # Sim delay for user read
    if settings['NumberOfBatteries'] != NUM_BANKS:
        alerts.append("Config mismatch: NumberOfBatteries != 3.")
        if y + 1 < stdscr.getmaxyx()[0]:
            try:
                stdscr.addstr(y + 1, 0, "Config mismatch detected.", curses.color_pair(2))  # Red
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
    
    # Step 2: Hardware connectivity
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
    
    # Step 3: Initial sensor reads
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
    # Set calibration if all temps valid
    if isinstance(initial_temps, list):
        valid_count = sum(1 for t in initial_temps if t > settings['valid_min'])
        if valid_count == settings['num_channels']:
            startup_median = statistics.median(initial_temps)
            startup_offsets = [startup_median - t for t in initial_temps]
            save_offsets(startup_median, startup_offsets)  # Updated save
            startup_set = True
            logging.info(f"Temp calibration set during startup. Median: {startup_median:.1f}°C")
    y += 3
    stdscr.refresh()
    
    # Step 4: Balancer test
    if y < stdscr.getmaxyx()[0]:
        try:
            stdscr.addstr(y, 0, "Step 4: Balancer verification...", curses.color_pair(4))
        except curses.error:
            logging.warning("addstr error for step 4.")
    y += 1
    stdscr.refresh()
    time.sleep(0.5)
    pairs = [(1,2), (1,3), (2,1), (2,3), (3,1), (3,2)]  # All directional pairs
    test_duration = settings['test_balance_duration']  # From config
    read_interval = settings['test_read_interval']  # From config
    min_delta = settings['min_voltage_delta']  # From config
    
    for high, low in pairs:
        if y < stdscr.getmaxyx()[0]:
            try:
                stdscr.addstr(y, 0, f"Testing balance: Bank {high} -> {low} for {test_duration}s.", curses.color_pair(6))
            except curses.error:
                logging.warning("addstr error for testing balance.")
        stdscr.refresh()
        logging.info(f"Testing balance: Bank {high} -> {low} for {test_duration}s.")
        
        # Pre-check temps (skip if anomalous)
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
        
        # Start balancing
        set_relay_connection(high, low, settings)
        control_dcdc_converter(True, settings)
        start_time = time.time()
        
        high_trend = []  # List of high bank voltages over time
        low_trend = []   # List of low bank voltages over time
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
                    stdscr.addstr(progress_y, 0, " " * 80, curses.color_pair(6))  # Clear line
                    stdscr.addstr(progress_y, 0, f"Progress: {elapsed:.1f}s, High {high_v:.2f}V, Low {low_v:.2f}V", curses.color_pair(6))
                except curses.error:
                    logging.warning("addstr error in startup balance progress.")
            stdscr.refresh()
            logging.debug(f"Trend read: High {high_v:.2f}V, Low {low_v:.2f}V")
        
        # Stop balancing
        control_dcdc_converter(False, settings)
        set_relay_connection(0, 0, settings)  # Reset
        
        # Analyze trends
        if progress_y + 1 < stdscr.getmaxyx()[0]:
            try:
                stdscr.addstr(progress_y + 1, 0, "Analyzing...", curses.color_pair(6))
            except curses.error:
                logging.warning("addstr error for analyzing.")
        stdscr.refresh()
        if len(high_trend) >= 3:  # Need at least 3 readings for trend
            high_delta = high_trend[0] - high_trend[-1]  # Expected drop
            low_delta = low_trend[-1] - low_trend[0]     # Expected rise
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
        time.sleep(5)  # Short rest between pair tests
    
    # Handle test results
    startup_alerts = alerts  # Store for persistent TUI
    if alerts:
        startup_failed = True
        logging.error("Startup self-test failures: " + "; ".join(alerts))
        send_alert_email("Startup self-test failures:\n" + "\n".join(alerts), settings)
        GPIO.output(settings['AlarmRelayPin'], GPIO.HIGH)  # Activate alarm
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
    time.sleep(5)  # Pause to view results, no user input
    return alerts

def main(stdscr):
    """Main loop for polling, processing, balancing, and TUI."""
    stdscr.keypad(True)  # Enable keypad
    # Initialize colors early, before any TUI drawing
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
    global previous_temps, previous_bank_medians, run_count, startup_offsets, startup_median, startup_set, battery_voltages
    settings = load_config()  # Load settings
    setup_hardware(settings)  # Setup hardware
    startup_self_test(settings, stdscr)  # Perform startup self-test with TUI
    signal.signal(signal.SIGINT, signal_handler)  # Register handler
    
    startup_median, startup_offsets = load_offsets()  # Updated load
    if startup_offsets and len(startup_offsets) == settings['num_channels']:
        startup_set = True  # Set flag
        logging.info(f"Loaded startup median: {startup_median:.1f}°C")
    previous_temps = None  # Init previous temps
    previous_bank_medians = [None] * NUM_BANKS  # Init previous medians
    run_count = 0  # Init run count
    
    while True:
        logging.info("Starting poll cycle.")  # Log cycle start
        # Read temps
        temp_result = read_ntc_sensors(settings['ip'], settings['port'], settings['query_delay'], settings['num_channels'], settings['scaling_factor'], settings['max_retries'], settings['retry_backoff_base'])  # Read temps
        temps_alerts = []  # Temp alerts list
        if isinstance(temp_result, str):
            temps_alerts.append(temp_result)  # Error as alert
            calibrated_temps = [None] * settings['num_channels']  # None calibrated
            raw_temps = [settings['valid_min']] * settings['num_channels']  # Min raw
            bank_medians = [0.0] * NUM_BANKS  # Zero medians
        else:
            valid_count = sum(1 for t in temp_result if t > settings['valid_min'])  # Count valid
            if not startup_set and valid_count == settings['num_channels']:
                startup_median = statistics.median(temp_result)  # Calculate median
                startup_offsets = [startup_median - raw for raw in temp_result]  # Calculate offsets
                save_offsets(startup_median, startup_offsets)  # Updated save
                startup_set = True  # Set flag
                logging.info(f"Temp calibration set. Median: {startup_median:.1f}°C")  # Log
            # Guard for None offsets
            if startup_set and startup_offsets is None:
                startup_set = False  # Reset if None
            calibrated_temps = [temp_result[i] + startup_offsets[i] if startup_set and temp_result[i] > settings['valid_min'] else temp_result[i] if temp_result[i] > settings['valid_min'] else None for i in range(settings['num_channels'])]  # Calibrate temps
            raw_temps = temp_result  # Raw list
            bank_medians = compute_bank_medians(calibrated_temps, settings['valid_min'])  # Bank medians
            
            for ch, raw in enumerate(raw_temps, 1):
                if check_invalid_reading(raw, ch, temps_alerts, settings['valid_min']):
                    continue  # Skip invalid
                calib = calibrated_temps[ch-1]  # Get calib
                bank_id = get_bank_for_channel(ch)  # Get bank
                bank_median = bank_medians[bank_id - 1]  # Bank median
                check_high_temp(calib, ch, temps_alerts, settings['high_threshold'])  # Check high
                check_low_temp(calib, ch, temps_alerts, settings['low_threshold'])  # Check low
                check_deviation(calib, bank_median, ch, temps_alerts, settings['abs_deviation_threshold'], settings['deviation_threshold'])  # Check deviation
            
            if run_count > 0 and previous_temps and previous_bank_medians is not None:
                for bank_id in range(1, NUM_BANKS + 1):
                    bank_median_rise = bank_medians[bank_id - 1] - previous_bank_medians[bank_id - 1]  # Rise
                    start, end = BANK_RANGES[bank_id - 1]  # Range
                    for ch in range(start, end + 1):
                        calib = calibrated_temps[ch - 1]  # Calib
                        if calib is not None:
                            check_abnormal_rise(calib, previous_temps, ch, temps_alerts, settings['poll_interval'], settings['rise_threshold'])  # Check rise
                            check_group_tracking_lag(calib, previous_temps, bank_median_rise, ch, temps_alerts, settings['disconnection_lag_threshold'])  # Check lag
                        check_sudden_disconnection(calib, previous_temps, ch, temps_alerts)  # Check disconnect
            
            previous_temps = calibrated_temps[:]  # Update previous
            previous_bank_medians = bank_medians[:]  # Update medians
        
        # Read voltages (per bank)
        battery_voltages = []
        for i in range(1, NUM_BANKS + 1):
            v, _, _ = read_voltage_with_retry(i, settings)  # Read voltage
            battery_voltages.append(v if v is not None else 0.0)  # Append or zero
        
        # Check issues (combined)
        alert_needed, all_alerts = check_for_issues(battery_voltages, temps_alerts, settings)  # Check and alert
        
        # Balance if needed, but skip if any alerts (new safety)
        if len(battery_voltages) == NUM_BANKS:
            max_v = max(battery_voltages)  # Max voltage
            min_v = min(battery_voltages)  # Min voltage
            high_b = battery_voltages.index(max_v) + 1  # High bank
            low_b = battery_voltages.index(min_v) + 1  # Low bank
            current_time = time.time()  # Current time
            if alert_needed:
                logging.warning("Skipping balancing due to active alerts.")
            elif max_v - min_v > settings['VoltageDifferenceToBalance'] and min_v > 0 and current_time - last_balance_time > settings['BalanceRestPeriodSeconds']:
                balance_battery_voltages(stdscr, high_b, low_b, settings, temps_alerts)  # Balance
        
        # Draw TUI with is_startup
        draw_tui(stdscr, battery_voltages, calibrated_temps, raw_temps, startup_offsets or [0]*settings['num_channels'], bank_medians, startup_median, all_alerts, settings, startup_set, is_startup=(run_count == 0))  # Draw
        
        run_count += 1  # Increment count
        gc.collect()  # Collect garbage
        logging.info("Poll cycle complete.")  # Log end
        time.sleep(min(settings['poll_interval'], settings['SleepTimeBetweenChecks']))  # Sleep

if __name__ == '__main__':
    curses.wrapper(main)  # Run main in curses wrapper