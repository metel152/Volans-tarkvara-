import time
import math
import csv
from datetime import datetime
import os
import board
import busio
import adafruit_bmp280
import smbus2

#baro
i2c = busio.I2C(board.SCL, board.SDA)
bmp280 = adafruit_bmp280.Adafruit_BMP280_I2C(i2c, address=0x76)

#imu
MPU_AADRESS = 0x68
MPU_VOOL = 0x6B
KIIR_XOUT_H = 0x3B
GYRO_XOUT_H = 0x43

#imu sleepist valja
bus = smbus2.SMBus(1)
bus.write_byte_data(MPU_AADRESS, MPU_VOOL, 0)

#imu andmete funktsioon (kuidas registrist loeb)
def loe_imu(register):
    high = bus.read_byte_data(MPU_AADRESS, register)
    low = bus.read_byte_data(MPU_AADRESS, register + 1)
    value = (high << 8) + low
    if value >= 0x8000:
        value -= 65536
    return value

#baro logi csv
baro_logi = "/home/volans/andurid-logi/baro_logi.csv"

#kirjuta failile p2is kui faili pole olemas
if not os.path.isfile(baro_logi):
    with open(baro_logi, "w") as f:
        f.write("timestamp,temperature_C,pressure_hPa\n")

#imu logi csv
imu_logi = "/home/volans/andurid-logi/imu_logi.csv"

#kirjuta failile p2is kui faili pole olemas
if not os.path.isfile(imu_logi):
    with open(imu_logi, "w") as f:
        f.write("timestamp,kiirendus_x,kiirendus_y,kiirendus_z,gyro_x,gyro_y,gyro_z\n")

print("Login andmeid (baro ja imu). Ctrl+c peatumiseks.")

#loop
try:
    while True:

      #baro andmed
        temp = bmp280.temperature
        rohk = bmp280.pressure

      #imu andmed
        ax_raw = loe_imu(KIIR_XOUT_H)
        ay_raw = loe_imu(KIIR_XOUT_H + 2)
        az_raw = loe_imu(KIIR_XOUT_H + 4)

        gx_raw = loe_imu(GYRO_XOUT_H)
        gy_raw = loe_imu(GYRO_XOUT_H + 2)
        gz_raw = loe_imu(GYRO_XOUT_H + 4)

      #hetke kuupaev ja kell
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

      #baro andmete logi
        with open(baro_logi, "a") as f:
            f.write(f"{now},{temp:.2f},{rohk:.2f}\n")

        print(f"{now} | {temp:.2f} C | {rohk:.2f} hPa") #test

      #imu andmete logi
        with open(imu_logi, "a") as f:
            f.write(f"{now},{ax_raw},{ay_raw},{az_raw},{gx_raw},{gy_raw},{gz_raw}\n")

        print(f"{now} | {ax_raw} | {ay_raw} | {az_raw} | {gx_raw} | {gy_raw} | {gz_raw}") #test

        time.sleep(1)

except KeyboardInterrupt:
    print("\nLogimine l6petatud.")

