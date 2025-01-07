import smbus
import time

# Constants for timing and thresholds
NUM_CELLS = 3  # Three cells in this example
BALANCE_THRESHOLD = 0.05  # Voltage difference in volts to trigger balancing
BALANCE_TIME = 60  # Time in seconds to keep balancing active
SLEEP_TIME = 300  # Time in seconds to wait before next check

# I2C addresses for M5 Units
VMETER_ADDRS = [0x49, 0x4A, 0x4B]  # M5 VMeter Addresses for 3 VMeters
RELAY_ADDR = 0x26  # M5 4-Relay Unit Address

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
    
    # No balancing needed or error state
    if high_cell == low_cell or high_cell < 0 or low_cell < 0:
        relay_state = 0
    else:
        # Ordered by high_cell for readability
        if high_cell == 1:  # High cell is 1
            if low_cell == 2:  # Cell 1 to Cell 2
                relay_state |= (1 << 2)  # Relay 3
            elif low_cell == 3:  # Cell 1 to Cell 3
                relay_state |= (1 << 2) | (1 << 3)  # Relay 3 and 4
        elif high_cell == 2:  # High cell is 2
            if low_cell == 1:  # Cell 2 to Cell 1
                relay_state |= (1 << 0)  # Relay 1
            elif low_cell == 3:  # Cell 2 to Cell 3
                relay_state |= (1 << 0) | (1 << 2) | (1 << 3)  # Relay 1, 3, and 4
        elif high_cell == 3:  # High cell is 3
            if low_cell == 1:  # Cell 3 to Cell 1
                relay_state |= (1 << 0) | (1 << 1)  # Relay 1 and 2
            elif low_cell == 2:  # Cell 3 to Cell 2
                relay_state |= (1 << 1) | (1 << 2) | (1 << 3)  # Relay 2, 3, and 4 (assumed pattern)

    # Control of the DC-DC converter - only on if balancing and requested
    if activate_dc_dc:
        relay_state |= (1 << 3)  # Turn on DC-DC converter only if needed

    bus.write_byte_data(RELAY_ADDR, 0x11, relay_state)

# Main loop
while True:
    try:
        # Read voltages
        voltages = []
        for cell in range(NUM_CELLS):
            voltages.append(read_voltage(cell))
        
        # Find the highest and lowest voltage cells
        max_voltage = max(voltages)
        min_voltage = min(voltages)
        high_cell = voltages.index(max_voltage)
        low_cell = voltages.index(min_voltage)

        # Balance by connecting highest to lowest if necessary
        if max_voltage - min_voltage > BALANCE_THRESHOLD:
            print(f"Balancing: Cell {high_cell + 1} ({max_voltage:.2f}V) to Cell {low_cell + 1} ({min_voltage:.2f}V)")
            
            # Disable DC-DC converter before switching relays
            set_relay(high_cell, low_cell, False)  # DC-DC off, cells connected for zero voltage state
            time.sleep(0.1)  # Brief delay to ensure no voltage is present
            
            # Enable DC-DC converter after relays are set
            set_relay(high_cell, low_cell, True)  # DC-DC on, start balancing
            
            time.sleep(BALANCE_TIME)  # Balance for specified time
            
            # Disable DC-DC converter before disconnecting cells
            set_relay(high_cell, low_cell, False)  # DC-DC off, stop balancing
        else:
            print("No balancing needed.")
            # Ensure all relays are off if not balancing, including DC-DC converter
            set_relay(-1, -1, False)  # -1 indicates no cell connection, all off

    except Exception as e:
        print(f"An error occurred: {e}")
        # Here you might want to log errors or take other safety measures

    # Sleep before next cycle
    time.sleep(SLEEP_TIME)