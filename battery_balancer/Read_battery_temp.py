"""
Overview of the Script:
This Python script is designed for continuous monitoring of temperatures from a 24-channel NTC temperature acquisition module connected via Modbus RTU over TCP through a Lantronix EDS4100 device. It reads temperature data, applies calibration based on a startup median (assuming all batteries start at identical temperatures), detects anomalies like deviations from the median, abnormal temperature rises, disconnection lags, absolute high/low thresholds, and invalid readings. The script runs in an infinite loop, polling at configurable intervals, with built-in reliability features like retries, logging, graceful shutdown, and garbage collection.

Key Features:
- Batch reads all 24 channels in one Modbus query for efficiency.
- Scales raw values (e.g., /100 for 0.1°C resolution).
- Calculates a fixed startup median for calibration (does not update after startup).
- Alerts on deviations (>10% from median), rises (>2°C per poll), lags from group rise, absolute extremes, and invalid readings.
- Suppresses repeated alerts to avoid spam (every 5 polls for persistent issues).
- Logs all readings and alerts to 'battery_log.txt'.
- Configurable via 'read_battery_temp.ini' (with defaults if file missing).
- Handles network errors with retries and exponential backoff.
- Graceful exit on Ctrl+C, manual memory cleanup for long runs.

Dependencies: socket, statistics, time, configparser, logging, signal, gc, os.
Usage: Run with Python 3 (e.g., python Read_battery_temp.py). Create 'read_battery_temp.ini' for custom settings.
Potential Improvements: Add email/SMS for alerts, multi-poll averaging for rise detection, or integration with a database for historical data.
"""

import socket  # This imports the socket library, which allows the script to communicate over the network, like connecting to the EDS4100 device.
import statistics  # This imports the statistics library, used to calculate things like the median (middle value) of the temperatures.
import time  # This imports the time library, used to add delays in the code, like waiting a short time after sending data.
import configparser  # This imports the configparser library, used to read settings from an INI file instead of hardcoding them in the script.
import logging  # This imports the logging library, used to write events and data to a file for persistent records.
import signal  # This imports the signal library, used to handle interruptions like Ctrl+C for graceful shutdown.
import gc  # This imports the garbage collector, used to manually clean up memory in long-running loops.
import os  # This imports the os library, used to get the current working directory for debugging file paths.

def modbus_crc(data):  # This defines a function to calculate the CRC (Cyclic Redundancy Check), which is a way to verify that the data sent or received hasn't been corrupted.
    crc = 0xFFFF  # Starts the CRC value at 65535 (in hexadecimal, that's FFFF), a standard starting point for Modbus CRC.
    for byte in data:  # Loops through each byte in the data.
        crc ^= byte  # Performs a bitwise XOR operation between the current CRC and the byte.
        for _ in range(8):  # Loops through 8 times (since each byte has 8 bits).
            if crc & 0x0001:  # Checks if the least significant bit (rightmost bit) is 1.
                crc = (crc >> 1) ^ 0xA001  # Shifts the CRC right by 1 bit and XORs with the Modbus polynomial (A001).
            else:  # If the bit is 0.
                crc >>= 1  # Just shifts the CRC right by 1 bit.
    return crc.to_bytes(2, 'little')  # Converts the final CRC to 2 bytes in little-endian order (low byte first) and returns it.

def read_ntc_sensors(ip, port, query_delay, max_retries=3, retry_backoff_base=1):  # This defines the main function to read the sensor data from the device using the given IP address and port, with added retries for timeouts.
    # Modbus RTU query: Slave 1, Function 03 (read holding registers), Start 0, Count 24 (0x18 in hex), CRC will be calculated.
    query_base = bytes([1, 3]) + (0).to_bytes(2, 'big') + (24).to_bytes(2, 'big')  # Builds the base part of the query message without CRC: slave ID 1, function 3, start register 0 (2 bytes), quantity 24 (2 bytes).
    crc = modbus_crc(query_base)  # Calculates the CRC for the base query.
    query = query_base + crc  # Adds the CRC to the end of the query to complete the message.
    
    for attempt in range(max_retries):  # Loops for retries on failure, up to max_retries times to improve reliability in unstable networks.
        try:  # Starts a try block to handle any errors that might occur during network communication.
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)  # Creates a new TCP socket for internet communication (AF_INET) in stream mode (reliable connection).
            s.settimeout(3)  # Sets a timeout of 3 seconds for the socket operations, so it doesn't wait forever if there's no response.
            s.connect((ip, port))  # Connects to the device's IP and port (like opening a phone line to the EDS4100).
            s.send(query)  # Sends the Modbus query message to the device.
            
            time.sleep(query_delay)  # Waits the configurable delay (e.g., 0.25 seconds) to give the device time to process and respond, as some devices need a short delay.
            
            response = s.recv(1024)  # Receives up to 1024 bytes of response from the device (sufficient for 53-byte expected response: 3 header + 48 data + 2 CRC).
            s.close()  # Closes the socket connection to free up resources and prevent resource leaks in long-running loops.
            
            if len(response) < 5:  # Checks if the response is too short (less than 5 bytes), which means it's invalid or incomplete.
                raise ValueError("Short response")  # Raises an error to trigger retry, as short responses indicate communication issues.
            
            slave, func, byte_count = response[0:3]  # Extracts the first 3 bytes: slave ID, function code, and byte count of data.
            if slave != 1 or func != 3 or byte_count != 48:  # Verifies the response header: slave should be 1, function 3, and 48 data bytes (2 bytes per channel x 24).
                if func & 0x80:  # Checks if it's an error response (function code with high bit set, indicating Modbus exception).
                    return f"Error: Modbus exception code {response[2]}"  # Returns the exception code (e.g., 2 for invalid address) for logging/alerting.
                return "Error: Invalid response header. Verify slave ID (1) and function (03)."  # Returns a custom error if header is wrong, likely configuration mismatch.
            
            data = response[3:3+48]  # Extracts the 48 data bytes (temperature values) from the response (after header, before CRC).
            raw_temperatures = []  # Creates an empty list to store the temperatures.
            for i in range(0, 48, 2):  # Loops through the data in steps of 2 (each temperature is 2 bytes).
                val = int.from_bytes(data[i:i+2], 'big', signed=True) / scaling_factor  # Converts 2 bytes to a signed integer (big-endian order, meaning high byte first), divides by scaling_factor (from INI) for correct temperature scaling (e.g., /100 for 0.1°C resolution).
                raw_temperatures.append(val)  # Adds the scaled temperature to the list.
            
            return raw_temperatures  # Returns the list of 24 temperatures if successful.
        
        except Exception as e:  # Catches any errors during the attempt (e.g., connection failure, timeout).
            logging.warning(f"Read attempt {attempt+1} failed: {str(e)}. Retrying after {retry_backoff_base ** attempt} seconds.")  # Logs the warning for the failed attempt.
            if attempt < max_retries - 1:  # If not the last attempt.
                time.sleep(retry_backoff_base ** attempt)  # Waits with exponential backoff (e.g., 1s, 2s, 4s) to avoid flooding the network during temporary issues.
            else:  # If all retries fail.
                return f"Error: Failed after {max_retries} attempts - {str(e)}. Check network/EDS4100."  # Returns a final error message after max retries.
    
# Signal handler for graceful shutdown (e.g., Ctrl+C)
def signal_handler(sig, frame):  # Defines a function to handle signals like SIGINT (Ctrl+C).
    logging.info("Script stopped by user or signal.")  # Logs the shutdown event.
    print("Script stopped gracefully.")  # Prints a message to console.
    exit(0)  # Exits the script cleanly with status 0 (success).

# Load configuration from INI file with fallbacks
config = configparser.ConfigParser()  # Creates a config parser object to read INI files.
try:  # Tries to read and parse the INI file.
    if not config.read('read_battery_temp.ini'):  # Reads the INI file named 'read_battery_temp.ini'; returns list of successfully read files.
        raise FileNotFoundError("INI file not found or empty")  # Raises error if no file was read (e.g., missing).
    ip = config['settings']['ip']  # Reads the IP address from [settings] section.
    port = int(config['settings']['port'])  # Reads the port as integer.
    poll_interval = float(config['settings']['poll_interval'])  # Reads poll interval as float.
    rise_threshold = float(config['settings']['rise_threshold'])  # Reads rise threshold as float.
    deviation_threshold = float(config['settings']['deviation_threshold'])  # Reads deviation threshold as float.
    disconnection_lag_threshold = float(config['settings']['disconnection_lag_threshold'])  # Reads disconnection lag threshold as float.
    high_threshold = float(config['settings']['high_threshold'])  # Reads high temperature threshold as float.
    low_threshold = float(config['settings']['low_threshold'])  # Reads low temperature threshold as float.
    scaling_factor = float(config['settings']['scaling_factor'])  # Reads scaling factor as float.
    valid_min = float(config['settings']['valid_min'])  # Reads minimum valid temperature as float.
    max_retries = int(config['settings']['max_retries'])  # Reads max retries as integer.
    retry_backoff_base = int(config['settings']['retry_backoff_base'])  # Reads retry backoff base as integer.
    query_delay = float(config['settings']['query_delay'])  # Reads query delay as float.
except Exception as e:  # Catches errors like file not found, missing keys, or invalid values.
    logging.warning(f"Config error: {str(e)}. Using defaults. Current dir: {os.getcwd()}")  # Logs the error with current directory for debugging (e.g., to check if INI is in the right place).
    print(f"Config error: {str(e)}. Using defaults. Current dir: {os.getcwd()}")  # Prints the error and directory to console for immediate feedback.
    ip = '192.168.15.240'  # Default IP if INI fails.
    port = 10001  # Default port if INI fails.
    poll_interval = 10.0  # Default poll interval if INI fails.
    rise_threshold = 2.0  # Default rise threshold if INI fails.
    deviation_threshold = 0.1  # Default deviation threshold if INI fails.
    disconnection_lag_threshold = 0.5  # Default disconnection lag threshold if INI fails.
    high_threshold = 60.0  # Default high threshold if INI fails.
    low_threshold = 0.0  # Default low threshold if INI fails.
    scaling_factor = 100.0  # Default scaling factor if INI fails.
    valid_min = 0.0  # Default valid min if INI fails.
    max_retries = 3  # Default max retries if INI fails.
    retry_backoff_base = 1  # Default retry backoff base if INI fails.
    query_delay = 0.25  # Default query delay if INI fails.

# Setup logging to file for persistent records
logging.basicConfig(filename='battery_log.txt', level=logging.INFO, format='%(asctime)s - %(message)s')  # Configures logging to write INFO level and above to 'battery_log.txt' with timestamp format.

# Attach signal handler for Ctrl+C (SIGINT) for graceful exit
signal.signal(signal.SIGINT, signal_handler)  # Registers the signal_handler function to run on SIGINT (Ctrl+C), ensuring clean shutdown.

startup_median = None  # To store the fixed median calculated at startup for calibration (does not update after first run).
previous_temps = None  # To store previous readings for rise/disconnection detection.
previous_median = None  # To store previous median for group rise check.
run_count = 0  # Counter to skip detection on first run.
alert_states = {ch: None for ch in range(1, 25)}  # Track per-channel alert states to suppress repeats (e.g., {'last_alert': 'type', 'count': 0}).

# Main logic in a loop for continuous monitoring.
while True:  # Infinite loop to keep reading temperatures every poll_interval seconds for ongoing operation.
    result = read_ntc_sensors(ip, port, query_delay, max_retries, retry_backoff_base)  # Calls the function to read the sensors with retries.

    if isinstance(result, str):  # If result is an error string (e.g., from timeouts or invalid response).
        logging.error(result)  # Logs the error to file.
        print(result)  # Prints the error to console.
    else:  # If successful reading, process the data.
        # Startup validation on first run: Check if all 24 channels are valid.
        if run_count == 0:  # Only on the first poll.
            valid_count = sum(1 for t in result if t > valid_min)  # Counts how many temperatures are above valid_min (valid sensors).
            if valid_count < 24:  # If less than 24 valid, alert on possible wiring issues.
                msg = f"Startup Alert: Only {valid_count}/24 channels valid. Check sensors/wiring."
                logging.warning(msg)  # Logs the warning.
                print(msg)  # Prints the warning.
        
        # Print raw scaled values for debug - shows the original readings before any adjustments.
        print("\nRaw Scaled Readings (before calibration):")
        for ch, raw in enumerate(result, start=1):  # Loops through the temperatures with channel numbers starting from 1.
            print(f"Channel {ch}: {raw:.1f} °C")  # Prints each raw temperature to 1 decimal place.
            logging.info(f"Channel {ch} raw: {raw:.1f} °C")  # Logs the raw temperature.
        
        # Filter valid temperatures (exclude <= valid_min as invalid) - removes bad readings for median calculation.
        valid_temps = [t for t in result if t > valid_min]  # Creates a list of only valid temperatures.
        
        if not valid_temps:  # If no valid temperatures.
            msg = "Error: No valid sensor readings. All channels show invalid values."
            logging.error(msg)  # Logs the error.
            print(msg)  # Prints the error.
        else:  # If there are valid temperatures.
            # Compute current median - the middle value of valid temperatures for reference.
            current_median = statistics.median(valid_temps)  # Calculates the median of valid temperatures.
            print(f"\nCurrent reference temperature (median): {current_median:.1f} °C")  # Prints the current median to 1 decimal place.
            logging.info(f"Current median: {current_median:.1f} °C")  # Logs the current median.
            
            # Calculate startup median only on first run - locks the reference for calibration.
            if run_count == 0:  # Only on the first poll.
                startup_median = current_median  # Sets the startup median to the current one.
                print(f"Startup median locked at: {startup_median:.1f} °C")  # Prints the locked startup median.
                logging.info(f"Startup median locked at: {startup_median:.1f} °C")  # Logs the locked startup median.
            
            # Use startup_median for calibration - applies fixed reference for deviation and factor calculation.
            calibrated_temps = []  # List to store adjusted temperatures.
            alerts = []  # List to store any warnings.
            
            for ch, raw in enumerate(result, start=1):  # Loops through each channel's raw temperature.
                if raw <= valid_min:  # If the reading is invalid (0 or negative).
                    calibrated_temps.append("No sensor/open circuit/invalid")  # Marks it as invalid in the calibrated list.
                    alerts.append(f"Channel {ch}: Alert - Invalid reading (≤ {valid_min}). Check sensor/wiring.")  # Adds an alert for invalid reading.
                else:  # If valid reading.
                    # Absolute high/low alerts - checks if raw is outside safe range.
                    if raw > high_threshold:  # If above high threshold.
                        alerts.append(f"Channel {ch}: Alert - High temperature ({raw:.1f} °C > {high_threshold} °C). Risk of damage!")  # Adds high temp alert.
                    elif raw < low_threshold:  # If below low threshold.
                        alerts.append(f"Channel {ch}: Alert - Low temperature ({raw:.1f} °C < {low_threshold} °C). Check environment.")  # Adds low temp alert.
                    
                    deviation = abs(raw - startup_median) / abs(startup_median) if startup_median != 0 else 0  # Calculates relative deviation from fixed startup median.
                    if deviation > deviation_threshold:  # If deviation exceeds threshold (e.g., 10%).
                        calibrated_temps.append(raw)  # Keeps the raw value (no calibration applied).
                        alerts.append(f"Channel {ch}: Alert - Deviation from startup median ({deviation:.2%} > {deviation_threshold*100:.0f}%), possible charging/discharging issue or abnormal heating. Displaying raw value.")  # Adds deviation alert.
                    else:  # If within threshold.
                        factor = startup_median / raw  # Calibration factor to align with startup median.
                        calibrated = raw * factor  # Applies the factor to the raw value.
                        calibrated_temps.append(calibrated)  # Adds the calibrated value to the list.
            
            # Display final calibrated readings - shows the adjusted temperatures.
            print("\nFinal Calibrated Readings (assuming identical startup temp):")
            for ch, temp in enumerate(calibrated_temps, start=1):  # Loops through calibrated values.
                print(f"Channel {ch}: {temp:.1f} °C" if isinstance(temp, float) else f"Channel {ch}: {temp}")  # Prints each to 1 decimal place, formatting floats as °C or strings as is.
                logging.info(f"Channel {ch} calibrated: {temp:.1f} °C" if isinstance(temp, float) else f"Channel {ch}: {temp}")  # Logs the calibrated value.
            
            # Abnormal temperature rise/detection (after first run) - checks for changes since last poll.
            if run_count > 0 and previous_temps:  # Only if not the first run and previous data exists.
                print("\nTemperature Change Check:")
                median_rise = current_median - previous_median  # Calculate group (median) rise since last poll.
                for ch, current in enumerate(calibrated_temps, start=1):  # Loops through each channel.
                    if isinstance(current, float) and isinstance(previous_temps[ch-1], float):  # If both current and previous are valid numbers.
                        rise = current - previous_temps[ch-1]  # Calculate channel-specific rise.
                        if rise > rise_threshold:  # If rise exceeds threshold (e.g., 2°C per poll).
                            alerts.append(f"Channel {ch}: Alert - Abnormal temperature rise ({rise:.1f} °C in {poll_interval}s). Check battery!")  # Adds rise alert.
                        # Enhanced disconnection/lag: Bidirectional deviation from group rise.
                        if abs(rise - median_rise) > disconnection_lag_threshold:  # If channel rise deviates from group rise.
                            alerts.append(f"Channel {ch}: Alert - Temperature not tracking group (channel rise {rise:.1f} °C vs group {median_rise:.1f} °C). Possible disconnection or fault.")  # Adds lag/disconnection alert.
                    elif isinstance(previous_temps[ch-1], float) and not isinstance(current, float):  # If previous was valid but current is invalid.
                        alerts.append(f"Channel {ch}: Alert - Sudden disconnection (was valid {previous_temps[ch-1]:.1f} °C, now invalid). Check sensor/wiring.")  # Adds sudden disconnection alert.
            
            # Update previous for next loop - saves current calibrated values and median for the next poll's comparisons.
            previous_temps = [t if isinstance(t, float) else valid_min for t in calibrated_temps]  # Replaces invalid with valid_min to keep list numeric.
            previous_median = current_median  # Updates the previous median.
            
            # Suppress repeated alerts: Track state per channel to avoid spamming the same alert every poll.
            current_alerts = []  # List for alerts to display/log this poll.
            for alert in alerts:  # Loops through new alerts.
                ch_str = alert.split(':')[0]  # Extracts "Channel X" from alert string.
                alert_type = alert.split(' - ')[1].split(' (')[0]  # Extracts the alert type (e.g., "Invalid reading").
                ch_num = int(ch_str.split()[1])  # Gets the channel number as int.
                if alert_states[ch_num] and alert_states[ch_num]['last_alert'] == alert_type:  # If same type as last alert for this channel.
                    alert_states[ch_num]['count'] += 1  # Increments the count of consecutive same alerts.
                    if alert_states[ch_num]['count'] % 5 != 0:  # If not a multiple of 5, skip displaying (suppresses repeats).
                        continue  # Skips adding to current_alerts.
                else:  # New alert type for this channel.
                    alert_states[ch_num] = {'last_alert': alert_type, 'count': 1}  # Resets state for new type.
                current_alerts.append(alert)  # Adds to display/log list.
            
            if current_alerts:  # If there are alerts to show this poll.
                print("\nAlerts:")
                for alert in current_alerts:  # Loops through current alerts.
                    print(alert)  # Prints to console.
                    logging.warning(alert)  # Logs to file.
    
    run_count += 1  # Increment the run counter after processing.
    gc.collect()  # Manually collects garbage to free memory, preventing leaks in long runs.
    time.sleep(poll_interval)  # Waits the configurable poll interval before the next loop iteration.