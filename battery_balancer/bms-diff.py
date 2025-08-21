--- bms.py (previous version)
+++ bms.py (updated version)

@@ load_config @@
-        'port': config_parser.getint('Temp', 'port', fallback=10001),
+        'modbus_port': config_parser.getint('Temp', 'modbus_port', fallback=10001),

@@ load_config @@
-        'port': config_parser.getint('Web', 'port', fallback=8080),
+        'web_port': config_parser.getint('Web', 'web_port', fallback=8080),

@@ read_ntc_sensors @@
-    logging.debug(f"Temp read attempt {attempt+1}: Connecting to {ip}:{port}")
+    logging.debug(f"Temp read attempt {attempt+1}: Connecting to {ip}:{modbus_port}")

@@ startup_self_test @@
-                  f"Temp_IP={settings['ip']}, Temp_Port={settings['port']}, "
+                  f"Temp_IP={settings['ip']}, Temp_Port={settings['modbus_port']}, "
...
-        test_query = read_ntc_sensors(settings['ip'], settings['port'], settings['query_delay'], 1, settings['scaling_factor'], 1, 1)
+        test_query = read_ntc_sensors(settings['ip'], settings['modbus_port'], settings['query_delay'], 1, settings['scaling_factor'], 1, 1)
...
-                      f"Connection={settings['ip']}:{settings['port']}, "
+                      f"Connection={settings['ip']}:{settings['modbus_port']}, "
...
-    initial_temps = read_ntc_sensors(settings['ip'], settings['port'], settings['query_delay'], 
+    initial_temps = read_ntc_sensors(settings['ip'], settings['modbus_port'], settings['query_delay'], 

@@ start_web_server @@
-        web_server = CustomHTTPServer((settings['host'], settings['port']), BMSRequestHandler)
+        web_server = CustomHTTPServer((settings['host'], settings['web_port']), BMSRequestHandler)
...
-        logging.info(f"Web server started on {settings['host']}:{settings['port']}")
+        logging.info(f"Web server started on {settings['host']}:{settings['web_port']}")