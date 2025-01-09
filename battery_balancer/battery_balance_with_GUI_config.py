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

# Setup logging
logging.basicConfig(level=logging.DEBUG, 
                    format='%(asctime)s - %(levelname)s - %(message)s',
                    filename='battery_balancer.log',
                    filemode='w')

"""
Battery Balancer Script

This script controls a battery balancing system for multiple lithium cells. 
It uses:
- I2C for reading battery voltages via an ADC (like ADS1115).
- I2C for controlling relays via an M5Stack 4Relay module.
- GPIO for controlling DC-DC converters.
- Curses for a Text User Interface (TUI) to display real-time battery status.
- Threading to manage voltage balancing without blocking UI updates.

Configuration:
- Reads parameters from a config.ini file, including I2C addresses, GPIO pins, etc.
- Logs operations and errors to battery_balancer.log for debugging.
"""

# Read configuration
config = configparser.ConfigParser()
if not config.read('config.ini'):
    logging.error("Failed to read config.ini")
    sys.exit(1)

# Extract configuration values
try:
    NUM_CELLS = config.getint('General', 'NUM_CELLS')
    BALANCE_THRESHOLD = config.getfloat('General', 'BALANCE_THRESHOLD')
    BALANCE_TIME = config.getint('General', 'BALANCE_TIME')
    SLEEP_TIME = config.getint('General', 'SLEEP_TIME')
    BALANCE_REST_PERIOD = config.getint('General', 'BALANCE_REST_PERIOD')
    ALARM_VOLTAGE_THRESHOLD = config.getfloat('General', 'ALARM_VOLTAGE_THRESHOLD')
    NUM_SAMPLES = config.getint('General', 'NUM_SAMPLES')
    MAX_RETRIES = config.getint('General', 'MAX_RETRIES')

    PAHUB2_ADDR = int(config.get('I2C', 'PAHUB2_ADDR'), 16)
    VMETER_ADDR = int(config.get('I2C', 'VMETER_ADDR'), 16)
    RELAY_ADDR = int(config.get('I2C', 'RELAY_ADDR'), 16)

    DC_DC_RELAY_PIN = config.getint('GPIO', 'DC_DC_RELAY_PIN')
    ALARM_RELAY_PIN = config.getint('GPIO', 'ALARM_RELAY_PIN')

    SMTP_SERVER = config.get('Email', 'SMTP_SERVER')
    SMTP_PORT = config.getint('Email', 'SMTP_PORT')
    SENDER_EMAIL = config.get('Email', 'SENDER_EMAIL')
    RECIPIENT_EMAIL = config.get('Email', 'RECIPIENT_EMAIL')
except (configparser.NoOptionError, ValueError) as e:
    logging.error(f"Configuration error: {e}")
    sys.exit(1)

# Initialize I2C bus and GPIO
try:
    bus = smbus.SMBus(1)  # Use bus 1 for newer Raspberry Pi models
    GPIO.setmode(GPIO.BCM)
    GPIO.setup(DC_DC_RELAY_PIN, GPIO.OUT)
    GPIO.setup(ALARM_RELAY_PIN, GPIO.OUT, initial=GPIO.LOW)  # Alarm relay starts off
except Exception as e:
    logging.error(f"Error initializing I2C or GPIO: {e}")
    sys.exit(1)

# ADS1115 configuration settings
REG_CONFIG = 0x01
REG_CONVERSION = 0x00
CONFIG_CONTINUOUS = 0x0000  # Continuous conversion mode
CONFIG_RATE_8 = 0x0000
CONFIG_PGA_6144 = 0x0200  # Gain setting for ±6.144V

# Global variable for managing the balancing thread
balancing_thread = None

# Function to select a channel on PaHUB2
def select_channel(channel):
    """
    Selects the specified channel on the PaHUB2 I2C multiplexer.

    Args:
        channel (int): The channel number to select (0-indexed).
    """
    try:
        bus.write_byte(PAHUB2_ADDR, 1 << channel)
        logging.debug(f"Selected channel {channel}")
    except IOError as e:
        logging.error(f"I2C Error while selecting channel {channel}: {e}")

# Function to configure ADS1115 for VMeter reading
def config_vmeter():
    """
    Configures the ADS1115 for continuous conversion with a gain suitable for battery voltage measurement.
    """
    config = CONFIG_CONTINUOUS | CONFIG_RATE_8 | CONFIG_PGA_6144
    try:
        bus.write_word_data(VMETER_ADDR, REG_CONFIG, config)
        logging.debug("Configured VMeter")
    except IOError as e:
        logging.error(f"Failed to configure VMeter: {e}")

# Function to read voltage with retry
def read_voltage_with_retry(cell_id, num_samples=5, tolerance=0.01, max_retries=5):
    """
    Reads voltage from a battery cell with retries for consistency.

    Args:
        cell_id (int): The ID of the cell to measure (0-indexed).
        num_samples (int): Number of samples to take for each reading.
        tolerance (float): Percentage tolerance for sample consistency.
        max_retries (int): Maximum number of retry attempts.

    Returns:
        tuple: (average_voltage, list of readings, list of raw ADC values) or (None, [], []) on failure.
    """
    valid_readings = []
    valid_raw_adc = []
    for attempt in range(max_retries):
        readings = []
        raw_adc_values = []
        for _ in range(num_samples):
            try:
                vmeter_channel = cell_id % 3
                select_channel(vmeter_channel)
                config_vmeter()
                bus.write_byte(VMETER_ADDR, 0x01)
                time.sleep(0.2)
                adc_raw = bus.read_word_data(VMETER_ADDR, REG_CONVERSION) & 0xFFFF
                logging.debug(f"Raw ADC reading for Cell {cell_id + 1}: {adc_raw}")
                
                # Avoid division by zero
                if adc_raw != 0:
                    voltage = adc_raw * (6.144 / 32767)
                    readings.append(voltage)
                    raw_adc_values.append(adc_raw)
                else:
                    logging.warning(f"ADC returned zero for Cell {cell_id + 1}")
                    readings.append(0.0001)  # Very small value to avoid division by zero in later checks
                    raw_adc_values.append(0)
            except IOError as e:
                logging.warning(f"Voltage reading attempt for Cell {cell_id + 1}: {e}")
                continue

        # Check if readings are within tolerance
        if len(readings) >= num_samples:
            average = sum(readings) / len(readings)
            if all(abs(r - average) / average <= tolerance for r in readings):
                valid_readings.extend(readings)
                valid_raw_adc.extend(raw_adc_values)
                break  # Exit if we've got valid readings
            else:
                logging.debug(f"Readings for Cell {cell_id + 1} not consistent enough, retrying.")
        
        time.sleep(5)  # Wait 5 seconds before next attempt if necessary

    if valid_readings:
        # Filter out readings more than 5% off from the average
        avg = sum(valid_readings) / len(valid_readings)
        filtered_readings = [r for r in valid_readings if abs(r - avg) / avg <= 0.05]
        filtered_adc = [raw_adc_values[i] for i, r in enumerate(valid_readings) if abs(r - avg) / avg <= 0.05]
        if filtered_readings:
            # Ensure we don't divide by zero here either
            if len(filtered_readings) > 0:
                return sum(filtered_readings) / len(filtered_readings), filtered_readings, filtered_adc
            else:
                logging.warning(f"Filtered readings for Cell {cell_id + 1} are empty")
                return None, [], []
        else:
            logging.warning(f"All readings for Cell {cell_id + 1} were too far off from average.")
            return None, [], []

    logging.error(f"Failed to read consistent voltage for Cell {cell_id + 1} after {max_retries} attempts")
    return None, [], []

# Function to control the relays and DC-DC converter
def set_relay(high_cell, low_cell):
    """
    Sets the relay configuration for balancing from one cell to another using the M5Stack 4Relay module.

    Args:
        high_cell (int): Index of the cell with higher voltage (0-indexed).
        low_cell (int): Index of the cell with lower voltage (0-indexed).
    """
    try:
        select_channel(3)  # Switch to channel 3 where the 4Relay module is connected
        relay_state = 0
        if high_cell == low_cell or high_cell < 0 or low_cell < 0:
            relay_state = 0  # All relays off
        else:
            # Relay mapping logic
            if high_cell == 2 and low_cell == 1:  # 2->1
                relay_state |= (1 << 0) | (1 << 2) | (1 << 4)  # Relay 1 Pole 3, Relay 2 Pole 1, Relay 3 Pole 1
            elif high_cell == 3 and low_cell == 1:  # 3->1
                relay_state |= (1 << 1) | (1 << 3) | (1 << 4)  # Relay 4 Pole 2, Relay 2 Pole 2, Relay 3 Pole 1
            elif high_cell == 1 and low_cell == 2:  # 1->2
                relay_state |= (1 << 0) | (1 << 4) | (1 << 2)  # Relay 1 Pole 1, Relay 3 Pole 1, Relay 2 Pole 1
            elif high_cell == 1 and low_cell == 3:  # 1->3
                relay_state |= (1 << 1) | (1 << 5) | (1 << 3)  # Relay 1 Pole 2, Relay 3 Pole 2, Relay 2 Pole 2
            elif high_cell == 2 and low_cell == 3:  # 2->3
                relay_state |= (1 << 2) | (1 << 3) | (1 << 5)  # Relay 4 Pole 1, Relay 2 Pole 2, Relay 3 Pole 2
            elif high_cell == 3 and low_cell == 2:  # 3->2
                relay_state |= (1 << 3) | (1 << 5) | (1 << 2)  # Relay 4 Pole 2, Relay 3 Pole 2, Relay 2 Pole 1

        bus.write_byte_data(RELAY_ADDR, 0x10, relay_state)
        logging.info(f"Set relay state for balancing from Cell {high_cell + 1} to Cell {low_cell + 1}")
    except IOError as e:
        logging.error(f"Relay setting error: {e}")

# Function to control DC-DC converter via GPIO
def control_dc_dc(enable):
    """
    Controls the DC-DC converter through GPIO.

    Args:
        enable (bool): If True, the converter is enabled; if False, it's disabled.
    """
    try:
        GPIO.output(DC_DC_RELAY_PIN, GPIO.HIGH if enable else GPIO.LOW)
        logging.info(f"DC-DC Converter {'enabled' if enable else 'disabled'}")
    except GPIO.GPIOError as e:
        logging.error(f"Error controlling DC-DC converter: {e}")

# Function to send an email alarm
def send_email_alarm():
    """
    Sends an email alarm when a cell voltage exceeds the predefined threshold.
    """
    msg = MIMEText(f'Warning: Cell voltage exceeded {ALARM_VOLTAGE_THRESHOLD}V!')
    msg['Subject'] = 'Battery Alarm'
    msg['From'] = SENDER_EMAIL
    msg['To'] = RECIPIENT_EMAIL

    try:
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.send_message(msg)  
        logging.info("Email alarm sent successfully")
    except Exception as e:
        logging.error(f"Failed to send email: {e}")

# New function to handle overvoltage alarm
def check_overvoltage_alarm(voltages):
    """
    Checks if any cell voltage exceeds the alarm threshold and activates alarms if necessary.

    Args:
        voltages (list): List of current voltages for each cell.

    Returns:
        bool: True if an overvoltage was detected and alarm was triggered, False otherwise.
    """
    for i, voltage in enumerate(voltages):
        if voltage and voltage > ALARM_VOLTAGE_THRESHOLD:
            logging.warning(f"ALARM: Cell {i+1} voltage is {voltage:.2f}V, exceeding threshold!")
            try:
                GPIO.output(ALARM_RELAY_PIN, GPIO.HIGH)
                send_email_alarm()
            except Exception as e:
                logging.error(f"Error during overvoltage alarm: {e}")
            return True
    try:
        GPIO.output(ALARM_RELAY_PIN, GPIO.LOW)
    except Exception as e:
        logging.error(f"Error resetting alarm relay: {e}")
    return False

def balance_cells(stdscr, high_cell, low_cell):
    """
    Perform cell balancing from a higher voltage cell to a lower one.

    This function updates the TUI with the current balancing status, 
    including an animation to show activity. It controls the DC-DC converter 
    and relays to manage voltage transfer.

    Args:
        stdscr (curses window object): The curses window for UI updates.
        high_cell (int): Index of the cell with higher voltage (0-indexed).
        low_cell (int): Index of the cell with lower voltage (0-indexed).
    """
    # Read current voltages
    voltage_high, _, adc_raw_high = read_voltage_with_retry(high_cell)
    voltage_low, _, _ = read_voltage_with_retry(low_cell)

    # Use default values if voltage readings are None
    voltage_high = voltage_high if voltage_high is not None else 0.0
    voltage_low = voltage_low if voltage_low is not None else 0.0

    # Animation frames for visual feedback in the TUI
    animation_frames = ['|', '/', '-', '\\']
    
    # Initial display of balancing action
    for i, frame in enumerate(animation_frames * 5):  # Loop the animation 5 times
        stdscr.addstr(10, 0, f"Balancing Cell {high_cell+1} ({voltage_high:.2f}V) -> Cell {low_cell+1} ({voltage_low:.2f}V)... [{frame}]")
        stdscr.refresh()  # Refresh screen to update animation
        time.sleep(0.1)  # Small delay for animation
        
        # Only need to set up the relay and control the DC-DC once
        if i == 0:
            set_relay(high_cell, low_cell)  # Configure relay for balancing
            control_dc_dc(False)  # Turn off DC-DC converter before switching
            time.sleep(0.1)  # Short delay to ensure no voltage is present during switch
            control_dc_dc(True)  # Turn on DC-DC converter for balancing

    # After balancing, ensure DC-DC is off
    control_dc_dc(False)

def main(stdscr):
    global balancing_thread
    
    try:
        curses.noecho()
        curses.cbreak()
        stdscr.keypad(True)
        stdscr.clear()
        curses.start_color()
        curses.use_default_colors()
        for i in range(1, curses.COLORS):
            curses.init_pair(i, i, -1)

        # Define colors for better readability
        TITLE_COLOR = curses.color_pair(1)    # Red for title
        HIGH_VOLTAGE_COLOR = curses.color_pair(2)  # Red for high voltage
        LOW_VOLTAGE_COLOR = curses.color_pair(3)   # Yellow for low voltage
        OK_VOLTAGE_COLOR = curses.color_pair(4)    # Green for OK voltage
        ADC_READINGS_COLOR = curses.color_pair(5)  # Lighter color for ADC readings
        BALANCE_COLOR = curses.color_pair(6)       # Yellow for balancing
        INFO_COLOR = curses.color_pair(7)          # Blue for info
        ERROR_COLOR = curses.color_pair(8)         # Magenta for errors

        # ASCII art for the GUI
        ascii_art = [
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
            stdscr.clear()
            # Display ASCII art with voltages
            voltages = []
            for i in range(NUM_CELLS):
                voltage, readings, adc_values = read_voltage_with_retry(i, num_samples=NUM_SAMPLES)
                if voltage is None:
                    voltages.append("Error")
                else:
                    voltages.append(voltage)
            
            for i, line in enumerate(ascii_art):
                # Determine color based on voltage
                for j, volt in enumerate(voltages):
                    if volt == "Error":
                        color = ERROR_COLOR
                    elif isinstance(volt, float):
                        if volt > ALARM_VOLTAGE_THRESHOLD:
                            color = HIGH_VOLTAGE_COLOR
                        elif volt < ALARM_VOLTAGE_THRESHOLD - BALANCE_THRESHOLD:
                            color = LOW_VOLTAGE_COLOR
                        else:
                            color = OK_VOLTAGE_COLOR
                    else:
                        color = INFO_COLOR  # Default color if voltage can't be interpreted

                    # Adjust the position where the battery starts on the line
                    start_pos = j * 17  # Assuming each battery takes up 17 characters width
                    end_pos = start_pos + 17
                    
                    # Slice the line for the specific battery and apply color
                    stdscr.addstr(i, start_pos, line[start_pos:end_pos], color)

                # Add voltage inside the battery cell at the 7th line
                for j, volt in enumerate(voltages):
                    if isinstance(volt, float):
                        voltage_str = f"{volt:.2f}V"
                        color = OK_VOLTAGE_COLOR if volt <= ALARM_VOLTAGE_THRESHOLD else HIGH_VOLTAGE_COLOR
                        color = LOW_VOLTAGE_COLOR if volt < ALARM_VOLTAGE_THRESHOLD - BALANCE_THRESHOLD else color
                        stdscr.addstr(6, 17 * j + 3, voltage_str.center(11), color)  # Centering the voltage string
                    else:
                        stdscr.addstr(6, 17 * j + 3, "Error".center(11), ERROR_COLOR)

            stdscr.addstr(len(ascii_art) + 1, 0, "Battery Balancer TUI", TITLE_COLOR)
            stdscr.hline(len(ascii_art) + 2, 0, curses.ACS_HLINE, curses.COLS - 1)
            
            y_offset = len(ascii_art) + 3
            for i in range(NUM_CELLS):
                voltage, readings, adc_values = read_voltage_with_retry(i, num_samples=NUM_SAMPLES)
                if voltage is None:
                    stdscr.addstr(y_offset + i, 0, f"Cell {i+1}: Error reading voltage", ERROR_COLOR)
                else:
                    stdscr.addstr(y_offset + i, 0, f"Cell {i+1}: (ADC: {adc_values[0] if adc_values else 'N/A'})", ADC_READINGS_COLOR)
                
                if readings:
                    stdscr.addstr(y_offset + i + 1, 0, f"  [Readings: {', '.join(f'{v:.2f}' for v in readings)}]", ADC_READINGS_COLOR)

            if len(voltages) == NUM_CELLS:
                if balancing_thread is None or not balancing_thread.is_alive():
                    max_voltage = max([v for v in voltages if isinstance(v, float)], default=0)
                    min_voltage = min([v for v in voltages if isinstance(v, float)], default=0)
                    high_cell = voltages.index(max_voltage)
                    low_cell = voltages.index(min_voltage)

                    if max_voltage - min_voltage > BALANCE_THRESHOLD:
                        balancing_thread = threading.Thread(target=balance_cells, args=(stdscr, high_cell, low_cell))
                        balancing_thread.start()
                        # Large balancing icon
                        stdscr.addstr(y_offset + NUM_CELLS + 2, 0, "  <======>", BALANCE_COLOR)
                        stdscr.addstr(y_offset + NUM_CELLS + 3, 0, f" Balancing Cell {high_cell+1} ({max_voltage:.2f}V) -> Cell {low_cell+1} ({min_voltage:.2f}V)", BALANCE_COLOR)
                    else:
                        stdscr.addstr(y_offset + NUM_CELLS + 2, 0, "  [ OK ]", OK_VOLTAGE_COLOR)
                        stdscr.addstr(y_offset + NUM_CELLS + 3, 0, "No balancing required; voltages within threshold.", INFO_COLOR)
                else:
                    # Large balancing icon with animation
                    frame = int(time.time() * 2) % 4  # Change frame every 0.5 seconds
                    animations = ['<======>', '>======<', '<======>', '>======<']
                    stdscr.addstr(y_offset + NUM_CELLS + 2, 0, f"  {animations[frame]}", BALANCE_COLOR)
                    stdscr.addstr(y_offset + NUM_CELLS + 3, 0, f" Balancing Cell {high_cell+1} ({max_voltage:.2f}V) -> Cell {low_cell+1} ({min_voltage:.2f}V)", BALANCE_COLOR)

            stdscr.refresh()
            time.sleep(0.5)  # Slower update for animation visibility

    except Exception as e:
        logging.error(f"Error in main loop: {e}")
        stdscr.addstr(y_offset + NUM_CELLS + 4, 0, f"Error: {e}", ERROR_COLOR)

if __name__ == '__main__':
    try:
        logging.info("Starting the Battery Balancer script")
        curses.wrapper(main)
    except Exception as e:
        logging.error(f"An unexpected error occurred in script execution: {e}")
    finally:
        GPIO.cleanup()
        logging.info("Program terminated. GPIO cleanup completed.")