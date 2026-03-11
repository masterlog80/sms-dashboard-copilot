import serial
import time

modem = serial.Serial('/dev/ttyUSB0', 9600, timeout=2)
time.sleep(2)

print("Deleting all SMS from SIM card...")
modem.write(b'AT+CMGD=1,4\r\n')  # Delete all messages
time.sleep(1)
response = modem.read(100)
print(f"Response: {response}")

time.sleep(1)
print("\nChecking storage...")
modem.write(b'AT+CPMS?\r\n')
time.sleep(0.5)
response = modem.read(200)
print(f"Storage status: {response}")

modem.close()
