import serial
import time

class GSMModem:
    def __init__(self, port, baudrate=9600):
        self.serial = serial.Serial(port, baudrate, timeout=1)
        time.sleep(1)

    def test_connection(self):
        self.serial.write(b'AT\r\n')
        response = self.serial.read(100)
        return response.decode('utf-8')

    def read_sms(self):
        self.serial.write(b'AT+CMGF=1\r\n')  # Set SMS mode to text
        time.sleep(1)
        self.serial.write(b'AT+CMGR=1\r\n')  # Read SMS at index 1
        time.sleep(1)
        response = self.serial.read(100)
        return response.decode('utf-8')

if __name__ == '__main__':
    modem = GSMModem('/dev/ttyUSB0')  # Adjust port as necessary
    print('Testing modem connection...')
    print(modem.test_connection())
    print('Reading SMS...')
    print(modem.read_sms())
