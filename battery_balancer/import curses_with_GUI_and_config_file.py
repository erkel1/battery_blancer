import curses
import time
import smbus
import configparser

# Read configuration
config = configparser.ConfigParser()
config.read('config.ini')

# Extract configuration values
NUM_CELLS = config.getint('General', 'NUM_CELLS')
BALANCE_THRESHOLD = config.getfloat('General', 'BALANCE_THRESHOLD')
BALANCE_TIME = config.getint('General', 'BALANCE_TIME')
SLEEP_TIME = config.getint('General', 'SLEEP_TIME')
BALANCE_REST_PERIOD = config.getint('General', 'BALANCE_REST_PERIOD')

VMETER_ADDRS = [int(addr, 16) for addr in config.get('I2C', 'VMETER_ADDRS').split(',')]
RELAY_ADDR = int(config.get('I2C', 'RELAY_ADDR'), 16)

# Initialize I2C bus
bus = smbus.SMBus(1)  # Use bus 1 for newer Raspberry Pi models

# Function to read voltage from a specific cell
def read_voltage(cell_id):
    vmeter_index = cell_id // (NUM_CELLS // len(VMETER_ADDRS))  # Simple distribution, adjust if needed
    vmeter_addr = VMETER_ADDRS[vmeter_index]
    channel = cell_id % (NUM_CELLS // len(VMETER_ADDRS))
    
    bus.write_byte(vmeter_addr, 0x01 | (channel << 4))  # Start conversion on specified channel
    time.sleep(0.001)  # Small delay for conversion
    raw_adc = bus.read_word_data(vmeter_addr, 0x00)  # Read conversion result
    
    voltage = (raw_adc * 16.0) / 32767.0  # 32767 for 15-bit signed, adjust if different
    return voltage

# Function to control the M5 4-Relay unit for all possible high-low cell combinations
def set_relay(high_cell, low_cell, activate_dc_dc=False):
    relay_state = 0
    
    if high_cell == low_cell or high_cell < 0 or low_cell < 0:
        relay_state = 0
    else:
        if high_cell == 1:
            if low_cell == 2: relay_state |= (1 << 2)
            elif low_cell == 3: relay_state |= (1 << 2) | (1 << 3)
        elif high_cell == 2:
            if low_cell == 1: relay_state |= (1 << 0)
            elif low_cell == 3: relay_state |= (1 << 0) | (1 << 2) | (1 << 3)
        elif high_cell == 3:
            if low_cell == 1: relay_state |= (1 << 0) | (1 << 1)
            elif low_cell == 2: relay_state |= (1 << 1) | (1 << 2) | (1 << 3)

    if activate_dc_dc:
        relay_state |= (1 << 3)  # Turn on DC-DC converter only if needed

    bus.write_byte_data(RELAY_ADDR, 0x11, relay_state)

def balance_cells(stdscr):
    voltages = []
    for cell in range(NUM_CELLS):
        voltages.append(read_voltage(cell))
    
    max_voltage = max(voltages)
    min_voltage = min(voltages)
    high_cell = voltages.index(max_voltage)
    low_cell = voltages.index(min_voltage)

    if max_voltage - min_voltage > BALANCE_THRESHOLD:
        stdscr.addstr(10, 0, f"Balancing Cell {high_cell+1} to Cell {low_cell+1}...")
        set_relay(high_cell, low_cell, False)
        time.sleep(0.1)  # Brief delay to ensure no voltage is present
        set_relay(high_cell, low_cell, True)
        time.sleep(BALANCE_TIME)
        set_relay(high_cell, low_cell, False)
        stdscr.addstr(10, 0, "Balancing completed. Waiting for stabilization...")
        time.sleep(BALANCE_REST_PERIOD)  # Wait for natural balancing
        stdscr.addstr(10, 0, "Stabilization complete.                     ")
    else:
        stdscr.addstr(10, 0, "No balancing needed.                      ")

def main(stdscr):
    # Setup curses
    curses.noecho()
    curses.cbreak()
    stdscr.keypad(True)
    stdscr.clear()
    curses.start_color()
    curses.use_default_colors()
    for i in range(1, curses.COLORS):
        curses.init_pair(i, i, -1)

    # Main loop
    while True:
        stdscr.clear()
        stdscr.addstr(0, 0, "Battery Balancer TUI", curses.color_pair(1))
        stdscr.hline(1, 0, curses.ACS_HLINE, curses.COLS - 1)
        for i in range(NUM_CELLS):
            voltage = read_voltage(i)
            voltage_color = curses.color_pair(2) if voltage < BALANCE_THRESHOLD else curses.color_pair(3)
            stdscr.addstr(i + 2, 0, f"Cell {i+1}: {voltage:.2f}V", voltage_color)

        # Perform balancing in the main loop
        balance_cells(stdscr)

        # User input area
        stdscr.addstr(NUM_CELLS + 4, 0, "Enter command (quit): ")
        stdscr.refresh()

        # Check for user input to quit the program
        command = stdscr.getstr(NUM_CELLS + 4, 20, 10).decode('utf-8').strip().lower()
        if command == 'quit':
            break

        # Wait before refreshing to not overload the system
        time.sleep(SLEEP_TIME)

    # Clean up before exiting
    stdscr.keypad(False)
    curses.echo()
    curses.nocbreak()
    curses.endwin()

if __name__ == '__main__':
    curses.wrapper(main)