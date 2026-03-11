import serial
import time

modem = serial.Serial('/dev/ttyUSB0', 9600, timeout=2)
time.sleep(2)

print("Setting SMS Service Center to Vodafone Italy...")
# Try common Italian SMS centers
modem.write(b'AT+CSCA="+393958000001",145\r\n')
time.sleep(1)
response = modem.read(100)
print(f"Response: {response}")

time.sleep(1)
print("\nVerifying...")
modem.write(b'AT+CSCA?\r\n')
time.sleep(0.5)
response = modem.read(100)
print(f"Current SMS center: {response}")

modem.close()
