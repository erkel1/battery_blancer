import smbus
import time
import configparser
import RPi.GPIO as GPIO
import smtplib
from email.mime.text import MIMEText
import curses
import logging
import sys
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

"""
Battery Balancer Script

This script manages a system to balance the charge of multiple lithium battery cells. 
It does this by:
- Reading battery voltages using an Analog-to-Digital Converter (ADC).
- Controlling relays to move charge between cells.
- Using GPIO to manage DC-DC converters.
- Showing battery status on screen with a Text User Interface (TUI).

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
                'LowVoltageThresholdPerBattery': config.getfloat('General', 'LowVoltageThresholdPerBattery'),
                'HighVoltageThresholdPerBattery': config.getfloat('General', 'HighVoltageThresholdPerBattery'),
                'NumberOfCellsInSeries': config.getint('General', 'NumberOfCellsInSeries'),
                'NumberOfSamples': config.getint('General', 'NumberOfSamples'),
                'MaxRetries': config.getint('General', 'MaxRetries'),
                'EmailAlertIntervalSeconds': config.getint('General', 'EmailAlertIntervalSeconds'),
                'I2C_BusNumber': config.getint('General', 'I2C_BusNumber'),
                'LoggingLevel': config.get('General', 'LoggingLevel', fallback='INFO').upper(),
                'VoltageDividerRatio': config.getfloat('General', 'VoltageDividerRatio', fallback=0.01592)
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
            },
            'Calibration': {
                'Sensor1_Calibration': config.getfloat('Calibration', 'Sensor1_Calibration', fallback=1.0),
                'Sensor2_Calibration': config.getfloat('Calibration', 'Sensor2_Calibration', fallback=1.0),
                'Sensor3_Calibration': config.getfloat('Calibration', 'Sensor3_Calibration', fallback=1.0),
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
        logging.debug(f"Attempting to select channel {channel}")
        bus.write_byte(config['I2C']['MultiplexerAddress'], 1 << channel)
        logging.debug(f"Successfully switched to channel {channel}")
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
        bus.write_word_data(config['I2C']['VoltageMeterAddress'], config['ADC']['ConfigRegister'], config_value)
        logging.debug("Voltage meter is now configured")
    except IOError as e:
        logging.error(f"Couldn't set up the voltage meter: {e}")

def read_voltage_with_retry(battery_id, number_of_samples=2, allowed_difference=0.01, max_attempts=2):
    """
    Try to read the voltage of a battery several times to get a reliable measurement, accounting for voltage divider.
    
    Args:
        battery_id (int): Which battery we're checking (starts from 1).
        number_of_samples (int): How many readings to take.
        allowed_difference (float): How much variation we allow in readings.
        max_attempts (int): How many times to try if readings are inconsistent.

    Returns:
        tuple: (average_actual_voltage, list of actual voltage readings, list of raw ADC values) or (None, [], []) if it fails.
    """
    voltage_divider_ratio = config['General']['VoltageDividerRatio']
    sensor_id = (battery_id - 1) % 3 + 1  # Assuming each battery is associated with a sensor in sequence
    calibration_factor = config['Calibration'][f'Sensor{sensor_id}_Calibration']
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
                
                bus.write_byte(config['I2C']['VoltageMeterAddress'], 0x01)  # Start conversion
                time.sleep(0.05)  # Decreased delay for faster readings
                
                # Read ADC value in little endian format
                raw_adc = bus.read_word_data(config['I2C']['VoltageMeterAddress'], config['ADC']['ConversionRegister'])
                # Ensure we're using little endian by swapping bytes if necessary
                raw_adc = (raw_adc & 0xFF) << 8 | (raw_adc >> 8)  # Swap bytes for little endian
                
                logging.debug(f"Raw ADC value for Battery {battery_id} (Sensor {sensor_id}): {raw_adc}")
                
                if raw_adc != 0:
                    measured_voltage = raw_adc * (6.144 / 32767)  # Measured voltage after divider
                    actual_voltage = (measured_voltage / voltage_divider_ratio) * calibration_factor  # Apply calibration factor here
                    readings.append(actual_voltage)
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
                    logging.debug(f"Readings for Battery {battery_id} (Sensor {sensor_id}) aren't consistent, trying again.")
        except IOError as e:
            logging.warning(f"Couldn't read voltage for Battery {battery_id} (Sensor {sensor_id}): {e}")
            continue
        time.sleep(0.01)  # Reduced from 5, adjust as needed

    logging.error(f"Couldn't get a good voltage reading for Battery {battery_id} (Sensor {sensor_id}) after {max_attempts} tries")
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
                relay_state |= (1 << 1)  # Turn on relay 2
                logging.debug("Relay 2 activated.")
            elif high_voltage_battery == 1 and low_voltage_battery == 3:
                relay_state |= (1 << 1)  # Turn on relay 2
                relay_state |= (1 << 2)  # Turn on relay 3
                logging.debug("Relays 2 and 3 activated.")
            elif high_voltage_battery == 2 and low_voltage_battery == 3:
                relay_state |= (1 << 0)  # Turn on relay 1
                relay_state |= (1 << 2)  # Turn on relay 3
                logging.debug("Relays 1 and 3 activated.")
            elif high_voltage_battery == 3 and low_voltage_battery == 2:
                relay_state |= (1 << 1)  # Turn on relay 2
                relay_state |= (1 << 2)  # Turn on relay 3
                logging.debug("Relays 2 and 3 activated.")

        logging.debug(f"Final relay state: {bin(relay_state)}")
        logging.info(f"Sending relay state command to hardware.")
        bus.write_byte_data(config['I2C']['RelayAddress'], 0x11, relay_state)  # Changed from 0x10 to 0x11
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
        GPIO.output(config['GPIO']['DC_DC_RelayPin'], GPIO.HIGH if turn_on else GPIO.LOW)
        logging.info(f"DC-DC Converter is now {'on' if turn_on else 'off'}")
    except GPIO.GPIOError as e:
        logging.error(f"Problem controlling DC-DC converter: {e}")

def send_alert_email(voltage=None, battery_id=None, message_type="high"):
    """
    Send an email when something goes wrong with battery voltage.
    
    Args:
        voltage (float or None): The voltage causing the alert.
        battery_id (int or None): Which battery caused the alert.
        message_type (str): Type of alert, either "high", "low", or "zero".
    """
    global last_email_time
    
    if time.time() - last_email_time < config['General']['EmailAlertIntervalSeconds']:
        logging.debug("Skipping this alert email to avoid flooding.")
        return

    try:
        subject = "Battery Alert"
        if message_type == "zero":
            content = f"Warning: Battery {battery_id} has no voltage!"
            subject = f"Battery Alert: Battery {battery_id} Voltage Zero"
        elif message_type == "high":
            content = f"Warning: Battery {battery_id} voltage is too high! Current voltage: {voltage:.2f}V"
            subject = f"Battery Alert: Battery {battery_id} Overvoltage"
        else:  # low voltage alert
            content = f"Warning: Battery {battery_id} voltage is critically low! Current voltage: {voltage:.2f}V"
            subject = f"Battery Alert: Battery {battery_id} Low Voltage"

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
    Check if any battery voltage is too high, too low, or at a critical low level, set off alarms if necessary.
    
    Args:
        voltages (list): List of current voltages for each battery.

    Returns:
        bool: True if an alert was triggered, False otherwise.
    """
    alert_needed = False
    low_voltage_threshold = config['General']['LowVoltageThresholdPerBattery']
    high_voltage_threshold = config['General']['HighVoltageThresholdPerBattery']

    for i, voltage in enumerate(voltages, 1):  # Start from 1 for battery_id
        if voltage is None or voltage == 0.0:
            logging.warning(f"ALERT: Battery {i} voltage is {voltage}V, which is not right!")
            try:
                GPIO.output(config['GPIO']['AlarmRelayPin'], GPIO.HIGH)
                send_alert_email(voltage, i, message_type="zero")
                alert_needed = True
            except Exception as e:
                logging.error(f"Problem activating alarm for zero voltage: {e}")
        elif voltage > high_voltage_threshold:
            logging.warning(f"ALERT: Battery {i} voltage is {voltage:.2f}V, too high!")
            try:
                GPIO.output(config['GPIO']['AlarmRelayPin'], GPIO.HIGH)
                send_alert_email(voltage, i, message_type="high")
                alert_needed = True
            except Exception as e:
                logging.error(f"Problem with high voltage alert: {e}")
        elif voltage <= low_voltage_threshold:
            logging.warning(f"LOW VOLTAGE ALERT: Battery {i} voltage is at {voltage:.2f}V, critically low!")
            try:
                GPIO.output(config['GPIO']['AlarmRelayPin'], GPIO.HIGH)
                send_alert_email(voltage, i, message_type="low")
                alert_needed = True
            except Exception as e:
                logging.error(f"Problem with low voltage alert: {e}")
    
    if not alert_needed:
        try:
            GPIO.output(config['GPIO']['AlarmRelayPin'], GPIO.LOW)
        except Exception as e:
            logging.error(f"Problem turning off alarm: {e}")
    
    return alert_needed

def balance_battery_voltages(stdscr, high_voltage_battery, low_voltage_battery):
    """
    Balance charge from a battery with higher voltage to one with lower voltage.
    
    Args:
        stdscr (curses window object): Where we show what's happening.
        high_voltage_battery (int): Battery with higher voltage (1-indexed).
        low_voltage_battery (int): Battery with lower voltage (1-indexed).
    """
    try:
        global balance_start_time, battery_voltages
        balancing_active = True  # Flag to indicate balancing is occurring

        logging.info(f"Starting balance from Battery {high_voltage_battery} to {low_voltage_battery}")

        # Initial voltage reading
        voltage_high, _, _ = read_voltage_with_retry(high_voltage_battery)
        voltage_low, _, _ = read_voltage_with_retry(low_voltage_battery)
        
        if voltage_low == 0.0:
            logging.warning(f"Cannot balance to Battery {low_voltage_battery} as it shows 0.00V. Skipping balancing.")
            stdscr.addstr(10, 0, f"Cannot balance to Battery {low_voltage_battery} (0.00V).", curses.color_pair(8))
            stdscr.refresh()
            
            # Reset relays even if balancing is skipped due to zero voltage
            logging.info("Resetting relay connections to default state due to zero voltage battery.")
            set_relay_connection(0, 0)  # All relays off
            balancing_active = False
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
            
            # Re-read voltages during balancing
            voltage_high, _, _ = read_voltage_with_retry(high_voltage_battery)
            voltage_low, _, _ = read_voltage_with_retry(low_voltage_battery)
            
            # Update the global list of battery voltages
            battery_voltages[high_voltage_battery - 1] = voltage_high if voltage_high is not None else 0.0
            battery_voltages[low_voltage_battery - 1] = voltage_low if voltage_low is not None else 0.0

            # Make a simple progress bar for the screen
            bar_length = 20
            filled_length = int(bar_length * progress)
            bar = '=' * filled_length + ' ' * (bar_length - filled_length)
            
            stdscr.addstr(10, 0, f"Balancing Battery {high_voltage_battery} ({voltage_high:.2f}V) -> Battery {low_voltage_battery} ({voltage_low:.2f}V)... [{animation_frames[frame_index % len(animation_frames)]}]", curses.color_pair(6))  # BALANCE_COLOR
            stdscr.addstr(11, 0, f"Progress: [{bar}] {int(progress * 100)}%", curses.color_pair(6))  # BALANCE_COLOR
            stdscr.refresh()  # Update the screen with new voltage readings
            
            logging.debug(f"Balancing progress: {progress * 100:.2f}%, High Voltage: {voltage_high:.2f}V, Low Voltage: {voltage_low:.2f}V")
            
            frame_index += 1
            time.sleep(0.01)  # Small delay to not update too frequently

        logging.info("Balancing process completed.")
        logging.info("Turning off DC-DC converter.")
        control_dcdc_converter(False)  # Turn off after balancing

        # Reset relay state here if necessary
        logging.info("Resetting relay connections to default state after balancing.")
        set_relay_connection(0, 0)  # All relays off

    except Exception as e:
        logging.error(f"Error during balancing process: {e}")
        # Reset relays on any error
        logging.info("Error occurred, resetting relay connections to default state.")
        set_relay_connection(0, 0)  # Ensure all relays are turned off
    finally:
        # Ensure relays are off even if an exception occurs outside the try block
        control_dcdc_converter(False)  # Turn off DC-DC converter
        set_relay_connection(0, 0)  # All relays off
        balancing_active = False  # Ensure flag is reset on any exit condition
        global last_balance_time
        last_balance_time = time.time()  # Update last balance time when exiting balancing



def main_program(stdscr):
    global battery_voltages, balance_start_time, balancing_active, last_balance_time

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

        balancing_active = False  # Flag to indicate if balancing is active
        last_balance_time = 0  # New global variable for balancing timer

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
                total_voltage_high = config['General']['HighVoltageThresholdPerBattery'] * config['General']['NumberOfBatteries']
                total_voltage_low = config['General']['LowVoltageThresholdPerBattery'] * config['General']['NumberOfBatteries']
                
                if total_voltage > total_voltage_high:
                    color = HIGH_VOLTAGE_COLOR
                elif total_voltage < total_voltage_low:
                    color = LOW_VOLTAGE_COLOR
                else:
                    color = OK_VOLTAGE_COLOR

                # Use the art library to display the total voltage in Roman font
                roman_voltage = text2art(f"{total_voltage:.2f}V", font='roman', chr_ignore=True)
                
                stdscr.addstr(0, 0, "Battery Balancer GUI", TITLE_COLOR)
                for i, line in enumerate(roman_voltage.splitlines()):
                    stdscr.addstr(i + 1, 0, line, color)
                stdscr.hline(len(roman_voltage.splitlines()) + 1, 0, curses.ACS_HLINE, curses.COLS - 1)
                
                y_offset = len(roman_voltage.splitlines()) + 2
                for i, line in enumerate(battery_art):
                    for j, volt in enumerate(battery_voltages):
                        if volt == 0.0:
                            color = ERROR_COLOR
                        elif volt > config['General']['HighVoltageThresholdPerBattery']:
                            color = HIGH_VOLTAGE_COLOR
                        elif volt < config['General']['LowVoltageThresholdPerBattery']:
                            color = LOW_VOLTAGE_COLOR
                        else:
                            color = OK_VOLTAGE_COLOR
                        
                        start_pos = j * 17
                        end_pos = start_pos + 17
                        stdscr.addstr(i + y_offset, start_pos, line[start_pos:end_pos], color)

                    for j, volt in enumerate(battery_voltages):
                        if volt == 0.0:
                            voltage_str = "0.00V"
                            color = ERROR_COLOR
                        else:
                            voltage_str = f"{volt:.2f}V"
                            if volt > config['General']['HighVoltageThresholdPerBattery']:
                                color = HIGH_VOLTAGE_COLOR
                            elif volt < config['General']['LowVoltageThresholdPerBattery']:
                                color = LOW_VOLTAGE_COLOR
                            else:
                                color = OK_VOLTAGE_COLOR
                        
                        if j == 1:  # Second cell (0-indexed)
                            center_pos = 17 * j + 3 - 3  # Move 3 spaces to the left
                        elif j == 2:  # Third cell (0-indexed)
                            center_pos = 17 * j + 3 - 6  # Move 6 spaces to the left
                        else:
                            center_pos = 17 * j + 3  # Default position for the first cell
                        
                        stdscr.addstr(y_offset + 6, center_pos, voltage_str.center(11), color)

                y_offset += len(battery_art)  # Move cursor down after drawing
                for i in range(1, config['General']['NumberOfBatteries'] + 1):
                    voltage, readings, adc_values = read_voltage_with_retry(i, number_of_samples=2, max_attempts=2)
                    logging.debug(f"Battery {i} - Voltage: {voltage}, ADC: {adc_values}, Readings: {readings}")
                    if voltage is None:
                        voltage = 0.0
                    stdscr.addstr(y_offset + i - 1, 0, f"Battery {i}: (ADC: {adc_values[0] if adc_values else 'N/A'})", ADC_READINGS_COLOR)
                    
                    if readings:
                        stdscr.addstr(y_offset + i, 0, f"[Readings: {', '.join(f'{v:.2f}' for v in readings)}]", ADC_READINGS_COLOR)
                    else:
                        stdscr.addstr(y_offset + i, 0, "  [Readings: No data]", ADC_READINGS_COLOR)
                    y_offset += 1  # Increment y_offset for each battery's readings line

                if len(battery_voltages) == config['General']['NumberOfBatteries']:
                    max_voltage = max(battery_voltages)
                    min_voltage = min(battery_voltages)
                    high_battery = battery_voltages.index(max_voltage) + 1  # +1 for 1-indexed
                    low_battery = battery_voltages.index(min_voltage) + 1  # +1 for 1-indexed

                    # Check if balancing should be deferred
                    current_time = time.time()
                    if max_voltage - min_voltage > config['General']['VoltageDifferenceToBalance'] and min_voltage > 0:
                        if current_time - last_balance_time > config['General']['BalanceRestPeriodSeconds']:
                            balancing_active = True
                            balance_battery_voltages(stdscr, high_battery, low_battery)
                            balancing_active = False
                        else:
                            # Inform user that balancing is deferred
                            stdscr.addstr(y_offset + config['General']['NumberOfBatteries'] + 2, 0, "  [ WAIT ]", INFO_COLOR)
                            stdscr.addstr(y_offset + config['General']['NumberOfBatteries'] + 3, 0, f"Balancing deferred for {int(config['General']['BalanceRestPeriodSeconds'] - (current_time - last_balance_time))} more seconds.", INFO_COLOR)
                    else:
                        stdscr.addstr(y_offset + config['General']['NumberOfBatteries'] + 2, 0, "  [ OK ]", OK_VOLTAGE_COLOR)
                        if min_voltage == 0:
                            stdscr.addstr(y_offset + config['General']['NumberOfBatteries'] + 3, 0, "No balancing possible due to zero voltage battery.", ERROR_COLOR)
                        else:
                            stdscr.addstr(y_offset + config['General']['NumberOfBatteries'] + 3, 0, "No need to balance, voltages are good.", INFO_COLOR)

                # Check if we need to sound any alarms
                check_for_voltage_issues(battery_voltages)

                stdscr.refresh()
                
                time.sleep(config['General']['SleepTimeBetweenChecks'])

            except Exception as e:
                logging.error(f"Something went wrong in the main loop: {e}")
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
        curses.wrapper(main_program)  # Use curses to manage screen setup and cleanup
    except Exception as e:
        logging.critical(f"Something unexpected happened while running the script: {e}")
        sys.exit(1)
    finally:
        GPIO.cleanup()  # Make sure to clean up GPIO even if something goes wrong
        logging.info("Program finished. Cleaned up GPIO.")