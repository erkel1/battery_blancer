import smbus
import time
import configparser
import RPi.GPIO as GPIO
import smtplib
from email.mime.text import MIMEText
import curses
import logging
import sys
import threading
import os
import signal
from art import text2art  # Importing the art library

# Load settings from config.ini
config = configparser.ConfigParser()
config.read('config.ini')

# Setup logging for tracking what's happening
# Set logging level from config file
logging_level = getattr(logging, config['General']['LoggingLevel'].upper(), None)
if not isinstance(logging_level, int):
    raise ValueError('Invalid log level: %s' % config['General']['LoggingLevel'])
logging.basicConfig(level=logging_level, 
                    format='%(asctime)s - %(levelname)s - %(message)s',
                    filename='battery_balancer.log',
                    filemode='a')  

# Lock to prevent problems when multiple parts of the code try to use the same thing at once
shared_lock = threading.Lock()

"""
Battery Balancer Script

This script manages a system to balance the charge of multiple lithium battery cells. 
It does this by:
- Reading battery voltages using an Analog-to-Digital Converter (ADC).
- Controlling relays to move charge between cells.
- Using GPIO to manage DC-DC converters.
- Showing battery status on screen with a Text User Interface (TUI).
- Running multiple tasks at once with threading.

Configuration:
- Loads settings from 'config.ini', like how many cells, voltage thresholds, etc.
- Logs what's happening or any issues to 'battery_balancer.log'.
"""

def load_settings():
    """
    Load all the settings from a configuration file.
    This makes sure we know how to handle the batteries correctly.
    """
    config = configparser.ConfigParser()
    if not config.read('config.ini'):
        logging.error("Couldn't read the config file!")
        raise FileNotFoundError("We can't find or read the config file!")
    
    try:
        settings = {
            'General': {
                'NumberOfBatteries': config.getint('General', 'NumberOfBatteries'),
                'VoltageDifferenceToBalance': config.getfloat('General', 'VoltageDifferenceToBalance'),
                'BalanceDurationSeconds': config.getint('General', 'BalanceDurationSeconds'),
                'SleepTimeBetweenChecks': config.getfloat('General', 'SleepTimeBetweenChecks'),
                'BalanceRestPeriodSeconds': config.getint('General', 'BalanceRestPeriodSeconds'),
                'AlarmVoltageThreshold': config.getfloat('General', 'AlarmVoltageThreshold'),
                'NumberOfSamples': config.getint('General', 'NumberOfSamples'),
                'MaxRetries': config.getint('General', 'MaxRetries'),
                'EmailAlertIntervalSeconds': config.getint('General', 'EmailAlertIntervalSeconds'),
                'I2C_BusNumber': config.getint('General', 'I2C_BusNumber'),
                'LoggingLevel': config.get('General', 'LoggingLevel', fallback='INFO').upper()
            },
            'I2C': {
                'MultiplexerAddress': int(config.get('I2C', 'MultiplexerAddress'), 16),
                'VoltageMeterAddress': int(config.get('I2C', 'VoltageMeterAddress'), 16),
                'RelayAddress': int(config.get('I2C', 'RelayAddress'), 16),
            },
            'GPIO': {
                'DC_DC_RelayPin': config.getint('GPIO', 'DC_DC_RelayPin'),
                'AlarmRelayPin': config.getint('GPIO', 'AlarmRelayPin'),
            },
            'Email': {
                'SMTP_Server': config.get('Email', 'SMTP_Server'),
                'SMTP_Port': config.getint('Email', 'SMTP_Port'),
                'SenderEmail': config.get('Email', 'SenderEmail'),
                'RecipientEmail': config.get('Email', 'RecipientEmail'),
            },
            'ADC': {
                'ConfigRegister': int(config.get('ADC', 'ConfigRegister'), 16),
                'ConversionRegister': int(config.get('ADC', 'ConversionRegister'), 16),
                'ContinuousModeConfig': int(config.get('ADC', 'ContinuousModeConfig'), 16),
                'SampleRateConfig': int(config.get('ADC', 'SampleRateConfig'), 16),
                'GainConfig': int(config.get('ADC', 'GainConfig'), 16),
            }
        }

        # Check if our balance threshold makes sense
        if settings['General']['VoltageDifferenceToBalance'] <= 0:
            raise ValueError("The voltage difference for balancing must be positive.")

        return settings
    except (configparser.NoOptionError, ValueError) as e:
        logging.error(f"Something's wrong with the config file: {e}")
        raise

# Global variables to keep track of things
config = None
bus = None
balancing_task = None
last_email_time = 0
balance_start_time = None  # Track when balancing begins

def setup_hardware():
    """
    Prepare all the hardware we need for battery balancing.
    This includes setting up communication with devices and configuring GPIO pins.
    """
    global bus, config
    try:
        config = load_settings()
        bus = smbus.SMBus(config['General']['I2C_BusNumber'])
        GPIO.setmode(GPIO.BCM)
        GPIO.setup(config['GPIO']['DC_DC_RelayPin'], GPIO.OUT)
        GPIO.setup(config['GPIO']['AlarmRelayPin'], GPIO.OUT, initial=GPIO.LOW)
        logging.info("All hardware is set up and ready!")
    except Exception as e:
        logging.critical(f"Problem setting up hardware: {e}")
        raise

def choose_channel(channel):
    """
    Pick which channel on the I2C multiplexer we want to talk to.
    
    Args:
        channel (int): Which channel to talk to (numbered from 0).
    """
    try:
        logging.debug(f"Attempting to acquire shared lock for channel {channel}")
        if not shared_lock.acquire(timeout=5):  # 5 seconds timeout
            logging.error(f"Failed to acquire shared_lock for channel {channel} after 5 seconds.")
            return  # or handle this situation appropriately
        logging.debug(f"Shared lock acquired for channel {channel}")
        try:
            logging.debug(f"About to write to bus for channel {channel}")
            bus.write_byte(config['I2C']['MultiplexerAddress'], 1 << channel)
            logging.debug(f"Successfully switched to channel {channel}")
        finally:
            logging.debug(f"Releasing shared_lock after operation on channel {channel}")
            shared_lock.release()
    except IOError as e:
        logging.error(f"Trouble selecting channel {channel}: {e}")

def setup_voltage_meter():
    """
    Set up the ADC to measure battery voltage correctly.
    """
    config_value = (config['ADC']['ContinuousModeConfig'] | 
                    config['ADC']['SampleRateConfig'] | 
                    config['ADC']['GainConfig'])
    try:
        with shared_lock:
            bus.write_word_data(config['I2C']['VoltageMeterAddress'], config['ADC']['ConfigRegister'], config_value)
        logging.debug("Voltage meter is now configured")
    except IOError as e:
        logging.error(f"Couldn't set up the voltage meter: {e}")

def read_voltage_with_retry(battery_id, number_of_samples=2, allowed_difference=0.01, max_attempts=2):
    """
    Try to read the voltage of a battery several times to get a reliable measurement.
    
    Args:
        battery_id (int): Which battery we're checking (starts from 1).
        number_of_samples (int): How many readings to take.
        allowed_difference (float): How much variation we allow in readings.
        max_attempts (int): How many times to try if readings are inconsistent.

    Returns:
        tuple: (average_voltage, list of readings, list of raw ADC values) or (None, [], []) if it fails.
    """
    for attempt in range(max_attempts):
        try:
            readings = []
            raw_values = []
            for _ in range(number_of_samples):
                meter_channel = (battery_id - 1) % 3  # Adjust for 1-indexed batteries
                choose_channel(meter_channel)
                
                # Increased setup attempt in case of initial failure
                for setup_attempt in range(3):
                    try:
                        setup_voltage_meter()
                        break  # success, exit loop
                    except IOError:
                        if setup_attempt == 2:  # all attempts failed
                            raise
                        time.sleep(0.01)  # wait before next attempt
                
                with shared_lock:
                    bus.write_byte(config['I2C']['VoltageMeterAddress'], 0x01)  # Start conversion
                time.sleep(0.05)  # Decreased delay for faster readings
                with shared_lock:
                    raw_adc = bus.read_word_data(config['I2C']['VoltageMeterAddress'], config['ADC']['ConversionRegister']) & 0xFFFF
                logging.debug(f"Raw ADC value for Battery {battery_id}: {raw_adc}")
                
                if raw_adc != 0:
                    battery_voltage = raw_adc * (6.144 / 32767)  # Ensure this conversion factor matches your setup
                    readings.append(battery_voltage)
                    raw_values.append(raw_adc)
                else:
                    readings.append(0.0)
                    raw_values.append(0)

            if readings:
                average = sum(readings) / len(readings)
                if average == 0.0 or all(abs(r - average) / (average if average != 0 else 1) <= allowed_difference for r in readings):
                    valid_readings = [r for r in readings if abs(r - average) / (average if average != 0 else 1) <= 0.05]
                    valid_adc = [raw_values[i] for i, r in enumerate(readings) if abs(r - average) / (average if average != 0 else 1) <= 0.05]
                    if valid_readings:
                        return sum(valid_readings) / len(valid_readings), valid_readings, valid_adc
                else:
                    logging.debug(f"Readings for Battery {battery_id} aren't consistent, trying again.")
        except IOError as e:
            logging.warning(f"Couldn't read voltage for Battery {battery_id}: {e}")
            continue
        time.sleep(0.01)  # Reduced from 5, adjust as needed

    logging.error(f"Couldn't get a good voltage reading for Battery {battery_id} after {max_attempts} tries")
    return None, [], []

def set_relay_connection(high_voltage_battery, low_voltage_battery):
    """
    Set up the relays to balance charge between two batteries using M5Stack 4Relay module.
    
    Args:
        high_voltage_battery (int): Battery with higher voltage (1-indexed).
        low_voltage_battery (int): Battery with lower voltage (1-indexed).
    """
    try:
        logging.info(f"Attempting to set relay for connection from Battery {high_voltage_battery} to {low_voltage_battery}")
        logging.debug("Before acquiring shared_lock")
        
        if not shared_lock.acquire(timeout=5):  # 5 seconds timeout
            logging.error("Failed to acquire shared_lock for relay setup after 5 seconds.")
            return  # or handle as needed

        try:
            logging.debug("Shared lock acquired")
            logging.debug("Switching to relay control channel.")
            choose_channel(3)  # Select channel 3 for relay operations
            relay_state = 0
            logging.debug(f"Initial relay state: {bin(relay_state)}")

            if high_voltage_battery == low_voltage_battery or high_voltage_battery < 1 or low_voltage_battery < 1:
                logging.debug("No need for relay activation; all relays off.")
                relay_state = 0  # All relays off, cell 1 links to cell 1
            else:
                # Relay mapping (actual setup might differ based on hardware)
                if high_voltage_battery == 2 and low_voltage_battery == 1:
                    relay_state |= (1 << 0)  # Turn on relay 1
                    logging.debug("Relay 1 activated.")
                elif high_voltage_battery == 3 and low_voltage_battery == 1:
                    relay_state |= (1 << 0)  # Turn on relay 1
                    relay_state |= (1 << 1)  # Turn on relay 2
                    logging.debug("Relays 1 and 2 activated.")
                elif high_voltage_battery == 1 and low_voltage_battery == 2:
                    relay_state |= (1 << 2)  # Turn on relay 3
                    logging.debug("Relay 3 activated.")
                elif high_voltage_battery == 1 and low_voltage_battery == 3:
                    relay_state |= (1 << 2)  # Turn on relay 3
                    relay_state |= (1 << 3)  # Turn on relay 4
                    logging.debug("Relays 3 and 4 activated.")
                elif high_voltage_battery == 2 and low_voltage_battery == 3:
                    relay_state |= (1 << 0)  # Turn on relay 1
                    relay_state |= (1 << 2)  # Turn on relay 3
                    relay_state |= (1 << 3)  # Turn on relay 4
                    logging.debug("Relays 1, 3, and 4 activated.")
                elif high_voltage_battery == 3 and low_voltage_battery == 2:
                    relay_state |= (1 << 1)  # Turn on relay 2
                    relay_state |= (1 << 2)  # Turn on relay 3
                    relay_state |= (1 << 3)  # Turn on relay 4
                    logging.debug("Relays 2, 3, and 4 activated.")

            logging.debug(f"Final relay state: {bin(relay_state)}")
            logging.info(f"Sending relay state command to hardware.")
            bus.write_byte_data(config['I2C']['RelayAddress'], 0x10, relay_state)
        finally:
            logging.debug("Releasing shared_lock")
            shared_lock.release()  # Release the lock after all operations
        
        logging.info(f"Relay setup completed for balancing from Battery {high_voltage_battery} to Battery {low_voltage_battery}")
    except IOError as e:
        logging.error(f"I/O error while setting up relay: {e}")
    except Exception as e:
        logging.error(f"Unexpected error in set_relay_connection: {e}")

        
def control_dcdc_converter(turn_on):
    """
    Turn the DC-DC converter on or off using GPIO.
    
    Args:
        turn_on (bool): True to turn on, False to turn off.
    """
    try:
        with shared_lock:
            GPIO.output(config['GPIO']['DC_DC_RelayPin'], GPIO.HIGH if turn_on else GPIO.LOW)
        logging.info(f"DC-DC Converter is now {'on' if turn_on else 'off'}")
    except GPIO.GPIOError as e:
        logging.error(f"Problem controlling DC-DC converter: {e}")

def send_alert_email(voltage=None, battery_id=None):
    """
    Send an email when something goes wrong with battery voltage.
    
    Args:
        voltage (float or None): The voltage causing the alert.
        battery_id (int or None): Which battery caused the alert.
    """
    global last_email_time
    
    if time.time() - last_email_time < config['General']['EmailAlertIntervalSeconds']:
        logging.debug("Skipping this alert email to avoid flooding.")
        return

    try:
        subject = "Battery Alert"
        if voltage is None and battery_id is None:
            content = "Warning: Something's wrong with a battery's voltage!"
        elif voltage == 0.0:
            content = f"Warning: Battery {battery_id} has no voltage!"
            subject = f"Battery Alert: Battery {battery_id} Voltage Zero"
        else:
            content = f"Warning: Battery {battery_id} voltage is too high! Current voltage: {voltage:.2f}V"
            subject = f"Battery Alert: Battery {battery_id} Overvoltage"

        msg = MIMEText(content)
        msg['Subject'] = subject
        msg['From'] = config['Email']['SenderEmail']
        msg['To'] = config['Email']['RecipientEmail']

        with smtplib.SMTP(config['Email']['SMTP_Server'], config['Email']['SMTP_Port']) as server:
            server.send_message(msg)  
        last_email_time = time.time()
        logging.info(f"Alert email sent: {subject}")
    except Exception as e:
        logging.error(f"Failed to send alert email: {e}")

def check_for_voltage_issues(voltages):
    """
    Check if any battery voltage is too high or too low, set off alarms if necessary.
    
    Args:
        voltages (list): List of current voltages for each battery.

    Returns:
        bool: True if an alert was triggered, False otherwise.
    """
    alert_needed = False
    
    for i, voltage in enumerate(voltages, 1):  # Start from 1 for battery_id
        if voltage is None or voltage == 0.0:
            logging.warning(f"ALERT: Battery {i} voltage is {voltage}V, which is not right!")
            try:
                with shared_lock:
                    GPIO.output(config['GPIO']['AlarmRelayPin'], GPIO.HIGH)
                send_alert_email(voltage, i)
                alert_needed = True
            except Exception as e:
                logging.error(f"Problem activating alarm for zero voltage: {e}")
        elif voltage > config['General']['AlarmVoltageThreshold']:
            logging.warning(f"ALERT: Battery {i} voltage is {voltage:.2f}V, too high!")
            try:
                with shared_lock:
                    GPIO.output(config['GPIO']['AlarmRelayPin'], GPIO.HIGH)
                send_alert_email(voltage, i)
                alert_needed = True
            except Exception as e:
                logging.error(f"Problem with high voltage alert: {e}")
    
    if not alert_needed:
        try:
            with shared_lock:
                GPIO.output(config['GPIO']['AlarmRelayPin'], GPIO.LOW)
        except Exception as e:
            logging.error(f"Problem turning off alarm: {e}")
    
    return alert_needed

def balance_battery_voltages(stdscr, high_voltage_battery, low_voltage_battery):
    """
    Balance charge from a battery with higher voltage to one with lower voltage.
    
    This function shows what's happening on the screen, including a progress bar.
    It controls the hardware to move charge between batteries. It will not balance
    if the low voltage battery reads 0.00V to prevent ineffective or harmful operations.

    Args:
        stdscr (curses window object): Where we show what's happening.
        high_voltage_battery (int): Battery with higher voltage (1-indexed).
        low_voltage_battery (int): Battery with lower voltage (1-indexed).
    """
    try:    
        global balance_start_time
        logging.info(f"Starting balance from Battery {high_voltage_battery} to {low_voltage_battery}")

        logging.info(f"Reading voltage for high battery {high_voltage_battery}")
        voltage_high, _, _ = read_voltage_with_retry(high_voltage_battery)
        logging.debug(f"Voltage high: {voltage_high}")

        logging.info(f"Reading voltage for low battery {low_voltage_battery}")
        voltage_low, _, _ = read_voltage_with_retry(low_voltage_battery)
        logging.debug(f"Voltage low: {voltage_low}")

        voltage_high = voltage_high if voltage_high is not None else 0.0
        voltage_low = voltage_low if voltage_low is not None else 0.0

        if voltage_low == 0.0:
            logging.warning(f"Cannot balance to Battery {low_voltage_battery} as it shows 0.00V. Skipping balancing.")
            with shared_lock:
                stdscr.addstr(10, 0, f"Cannot balance to Battery {low_voltage_battery} (0.00V).", curses.color_pair(8))
                stdscr.refresh()
            return

        animation_frames = ['|', '/', '-', '\\']
        balance_start_time = time.time()  # Start timer for balancing
        frame_index = 0

        logging.info("Setting up relay connections for balancing.")
        set_relay_connection(high_voltage_battery, low_voltage_battery)

        logging.info("Turning on DC-DC converter for balancing.")
        control_dcdc_converter(True)

        logging.info("Starting balancing process.")
        while time.time() - balance_start_time < config['General']['BalanceDurationSeconds']:
            elapsed_time = time.time() - balance_start_time
            progress = min(1.0, elapsed_time / config['General']['BalanceDurationSeconds'])
            
            # Make a simple progress bar for the screen
            bar_length = 20
            filled_length = int(bar_length * progress)
            bar = '=' * filled_length + ' ' * (bar_length - filled_length)
            
            with shared_lock:
                stdscr.addstr(10, 0, f"Balancing Battery {high_voltage_battery} ({voltage_high:.2f}V) -> Battery {low_voltage_battery} ({voltage_low:.2f}V)... [{animation_frames[frame_index % len(animation_frames)]}]")
                stdscr.addstr(11, 0, f"Progress: [{bar}] {int(progress * 100)}%")
                stdscr.refresh()
            
            logging.debug(f"Balancing progress: {progress * 100:.2f}%")
            
            frame_index += 1
            time.sleep(0.01)  # Small delay to not update too frequently

        logging.info("Balancing process completed.")
        logging.info("Turning off DC-DC converter.")
        control_dcdc_converter(False)  # Turn off after balancing

        # Reset relay state here if necessary
        logging.info("Resetting relay connections to default state.")
        set_relay_connection(1, 1)  # Assuming 1 means all off for 1-indexed batteries
    
    except Exception as e:
        logging.error(f"Error during balancing process: {e}")
        # Decide what to do with the thread here, e.g., stop it or let it exit
        # Here you might want to handle the error, perhaps by resetting hardware or stopping the thread

# Function to keep an eye on the main task
def keep_watching():
    global balancing_task
    while True:
        time.sleep(60)  # Check every 60 seconds
        if balancing_task and not balancing_task.is_alive():
            logging.debug("Before acquiring shared_lock for watchdog")
            with shared_lock:  # Ensure lock is used correctly here
                logging.error("Balancing task stopped unexpectedly! Restarting.")
                os.execv(sys.executable, ['python'] + sys.argv)
            logging.debug("Shared lock released by watchdog")
        # Reset balancing_task if it was running but has now finished
        elif balancing_task and not balancing_task.is_alive():
            logging.debug("Before resetting balancing_task")
            with shared_lock:  # Use lock even for resetting
                balancing_task = None
            logging.debug("Balancing task reset")
        time.sleep(1)  # Small delay to not overload CPU

# Handle signals for clean shutdown
def shutdown_handler(signum, frame):
    logging.info("Received shutdown signal, cleaning up.")
    GPIO.cleanup()
    sys.exit(0)

# Register to handle shutdown signals
signal.signal(signal.SIGTERM, shutdown_handler)
signal.signal(signal.SIGINT, shutdown_handler)

def main_program(stdscr):
    global balancing_task
    
    try:
        curses.noecho()
        curses.cbreak()
        stdscr.keypad(True)
        stdscr.clear()
        curses.start_color()
        curses.use_default_colors()
        for i in range(1, curses.COLORS):
            curses.init_pair(i, i, -1)

        # Colors for better screen readability
        TITLE_COLOR = curses.color_pair(1)    # Red for title
        HIGH_VOLTAGE_COLOR = curses.color_pair(2)  # Red for high voltage
        LOW_VOLTAGE_COLOR = curses.color_pair(3)   # Yellow for low voltage
        OK_VOLTAGE_COLOR = curses.color_pair(4)    # Green for OK voltage
        ADC_READINGS_COLOR = curses.color_pair(5)  # Lighter color for ADC readings
        BALANCE_COLOR = curses.color_pair(6)       # Yellow for balancing
        INFO_COLOR = curses.color_pair(7)          # Light bright blue or turquoise
        ERROR_COLOR = curses.color_pair(8)         # Magenta for errors

        # Initialize the new color for INFO_COLOR
        curses.init_pair(7, curses.COLOR_CYAN, -1)  # Cyan is the closest to turquoise in curses
        
        # Simple graphic for the GUI
        battery_art = [
            "   ___________   ___________   ___________   ",
            "  |           | |           | |           |  ",
            "  |           | |           | |           |  ",
            "  |           | |           | |           |  ",
            "  |           | |           | |           |  ",
            "  |    +++    | |    +++    | |    +++    |  ",
            "  |    +++    | |    +++    | |    +++    |  ",
            "  |           | |           | |           |  ",
            "  |           | |           | |           |  ",
            "  |           | |           | |           |  ",
            "  |           | |           | |           |  ",
            "  |    ---    | |    ---    | |    ---    |  ",
            "  |    ---    | |    ---    | |    ---    |  ",
            "  |    ---    | |    ---    | |    ---    |  ",
            "  |           | |           | |           |  ",
            "  |           | |           | |           |  ",
            "  |___________| |___________| |___________|  "
        ]

        while True:
            try:
                stdscr.clear()
                battery_voltages = []
                for i in range(1, config['General']['NumberOfBatteries'] + 1):
                    voltage, _, _ = read_voltage_with_retry(i, number_of_samples=2, max_attempts=2)
                    battery_voltages.append(voltage if voltage is not None else 0.0)
                
                # Total voltage of all batteries
                total_voltage = sum(battery_voltages)
                
                # Determine color based on total battery voltage
                total_voltage_high = config['General']['AlarmVoltageThreshold'] * config['General']['NumberOfBatteries']
                total_voltage_low = total_voltage_high - config['General']['VoltageDifferenceToBalance'] * config['General']['NumberOfBatteries']
                
                if total_voltage > total_voltage_high:
                    color = HIGH_VOLTAGE_COLOR
                elif total_voltage < total_voltage_low:
                    color = LOW_VOLTAGE_COLOR
                else:
                    color = OK_VOLTAGE_COLOR

                # Use the art library to display the total voltage in Roman font
                roman_voltage = text2art(f"{total_voltage:.2f}V", font='roman', chr_ignore=True)
                
                with shared_lock:
                    stdscr.addstr(0, 0, "Battery Balancer GUI", TITLE_COLOR)
                    for i, line in enumerate(roman_voltage.splitlines()):
                        stdscr.addstr(i + 1, 0, line, color)
                    stdscr.hline(len(roman_voltage.splitlines()) + 1, 0, curses.ACS_HLINE, curses.COLS - 1)
                
                y_offset = len(roman_voltage.splitlines()) + 2
                for i, line in enumerate(battery_art):
                    for j, volt in enumerate(battery_voltages):
                        if volt == 0.0:
                            color = ERROR_COLOR
                        elif volt > config['General']['AlarmVoltageThreshold']:
                            color = HIGH_VOLTAGE_COLOR
                        elif volt < config['General']['AlarmVoltageThreshold'] - config['General']['VoltageDifferenceToBalance']:
                            color = LOW_VOLTAGE_COLOR
                        else:
                            color = OK_VOLTAGE_COLOR
                        
                        start_pos = j * 17
                        end_pos = start_pos + 17
                        with shared_lock:
                            stdscr.addstr(i + y_offset, start_pos, line[start_pos:end_pos], color)

                    for j, volt in enumerate(battery_voltages):
                        if volt == 0.0:
                            voltage_str = "0.00V"
                            color = ERROR_COLOR
                        else:
                            voltage_str = f"{volt:.2f}V"
                            color = OK_VOLTAGE_COLOR if volt <= config['General']['AlarmVoltageThreshold'] else HIGH_VOLTAGE_COLOR
                            color = LOW_VOLTAGE_COLOR if volt < config['General']['AlarmVoltageThreshold'] - config['General']['VoltageDifferenceToBalance'] else color
                        
                        # Adjust position for each cell
                        if j == 1:  # Second cell (0-indexed)
                            center_pos = 17 * j + 3 - 3  # Move 3 spaces to the left
                        elif j == 2:  # Third cell (0-indexed)
                            center_pos = 17 * j + 3 - 6  # Move 6 spaces to the left
                        else:
                            center_pos = 17 * j + 3  # Default position for the first cell
                        
                        with shared_lock:
                            stdscr.addstr(y_offset + 6, center_pos, voltage_str.center(11), color)

                y_offset += len(battery_art)  # Move cursor down after drawing
                for i in range(1, config['General']['NumberOfBatteries'] + 1):
                    voltage, readings, adc_values = read_voltage_with_retry(i, number_of_samples=2, max_attempts=2)
                    logging.debug(f"Battery {i} - Voltage: {voltage}, ADC: {adc_values}, Readings: {readings}")
                    if voltage is None:
                        voltage = 0.0
                    with shared_lock:
                        stdscr.addstr(y_offset + i - 1, 0, f"Battery {i}: (ADC: {adc_values[0] if adc_values else 'N/A'})", ADC_READINGS_COLOR)
                    
                    if readings:
                        with shared_lock:
                            stdscr.addstr(y_offset + i, 0, f"[Readings: {', '.join(f'{v:.2f}' for v in readings)}]", ADC_READINGS_COLOR)
                    else:
                        with shared_lock:
                            stdscr.addstr(y_offset + i, 0, "  [Readings: No data]", ADC_READINGS_COLOR)
                    y_offset += 1  # Increment y_offset for each battery's readings line

                if len(battery_voltages) == config['General']['NumberOfBatteries']:
                    if balancing_task is None or not balancing_task.is_alive():
                        max_voltage = max(battery_voltages)
                        min_voltage = min(battery_voltages)
                        high_battery = battery_voltages.index(max_voltage) + 1  # +1 for 1-indexed
                        low_battery = battery_voltages.index(min_voltage) + 1  # +1 for 1-indexed

                        if max_voltage - min_voltage > config['General']['VoltageDifferenceToBalance'] and min_voltage > 0:
                            balancing_task = threading.Thread(target=balance_battery_voltages, args=(stdscr, high_battery, low_battery))
                            balancing_task.start()
                            with shared_lock:
                                stdscr.addstr(y_offset + config['General']['NumberOfBatteries'] + 2, 0, "  <======>", BALANCE_COLOR)
                                stdscr.addstr(y_offset + config['General']['NumberOfBatteries'] + 3, 0, f" Balancing Battery {high_battery} ({max_voltage:.2f}V) -> Battery {low_battery} ({min_voltage:.2f}V)", BALANCE_COLOR)
                        else:
                            with shared_lock:
                                stdscr.addstr(y_offset + config['General']['NumberOfBatteries'] + 2, 0, "  [ OK ]", OK_VOLTAGE_COLOR)
                                if min_voltage == 0:
                                    stdscr.addstr(y_offset + config['General']['NumberOfBatteries'] + 3, 0, "No balancing possible due to zero voltage battery.", ERROR_COLOR)
                                else:
                                    stdscr.addstr(y_offset + config['General']['NumberOfBatteries'] + 3, 0, "No need to balance, voltages are good.", INFO_COLOR)
                    else:
                        # Animation for balancing in progress
                        frame = int(time.time() * 2) % 4  # Change frame every 0.5 seconds
                        animations = ['<======>', '>======<', '<======>', '>======<']
                        with shared_lock:
                            stdscr.addstr(y_offset + config['General']['NumberOfBatteries'] + 2, 0, f"  {animations[frame]}", BALANCE_COLOR)
                            stdscr.addstr(y_offset + config['General']['NumberOfBatteries'] + 3, 0, f" Balancing Battery {high_battery} ({max_voltage:.2f}V) -> Battery {low_battery} ({min_voltage:.2f}V)", BALANCE_COLOR)
                            # Show progress bar
                            if balance_start_time is not None:
                                elapsed_time = time.time() - balance_start_time
                                progress = min(1.0, elapsed_time / config['General']['BalanceDurationSeconds'])
                                progress_bar_length = 20
                                filled_length = int(progress_bar_length * progress)
                                bar = '=' * filled_length + ' ' * (progress_bar_length - filled_length)
                                stdscr.addstr(y_offset + config['General']['NumberOfBatteries'] + 4, 0, f" Progress: [{bar}] {int(progress * 100)}%", BALANCE_COLOR)

                # Check if we need to sound any alarms
                check_for_voltage_issues(battery_voltages)

                with shared_lock:
                    stdscr.refresh()
                
                time.sleep(config['General']['SleepTimeBetweenChecks'])

            except Exception as e:
                logging.error(f"Something went wrong in the main loop: {e}")
                with shared_lock:
                    stdscr.addstr(y_offset + config['General']['NumberOfBatteries'] + 5, 0, f"Error: {e}", ERROR_COLOR)
                stdscr.refresh()  # Refresh to show the error
                time.sleep(0.1)  # Keep this brief

    except Exception as e:
        logging.critical(f"A serious error in the main loop: {e}")
        raise

if __name__ == '__main__':
    try:
        logging.info("Starting the Battery Balancer program")
        setup_hardware()

        # Start the watch-dog thread
        watch_dog_thread = threading.Thread(target=keep_watching, daemon=True)
        watch_dog_thread.start()

        curses.wrapper(main_program)  # Use curses to manage screen setup and cleanup
    except Exception as e:
        logging.critical(f"Something unexpected happened while running the script: {e}")
        sys.exit(1)
    finally:
        GPIO.cleanup()  # Make sure to clean up GPIO even if something goes wrong
        logging.info("Program finished. Cleaned up GPIO.")