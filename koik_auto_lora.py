from picamera2 import Picamera2
import time
from datetime import datetime
import os
import io
import base64
import board
import busio
import adafruit_bmp280
import smbus2
import serial
from PIL import Image

# =========================================================
# SEADISTUS
# =========================================================

# LoRa UART
LORA_PORT = "/dev/serial0"
LORA_BAUD = 57600

# Kaustad
ANDURI_KAUST = "/home/volans/andurid-logi"
KAAMERA_KAUST = "/home/volans/kaamera-logi"

# Failid
BARO_LOGI = os.path.join(ANDURI_KAUST, "baro_logi.csv")
IMU_LOGI = os.path.join(ANDURI_KAUST, "imu_logi.csv")

# Loogika
LOOP_DELAY_S = 1.0          # alusta aeglaselt
PREVIEW_EVERY_N_IMAGES = 10 # saada ainult iga 10. pildi preview
PREVIEW_SIZE = (160, 120)
PREVIEW_JPEG_QUALITY = 18
PREVIEW_CHUNK_SIZE = 40     # hoia LoRa paketid lühikesed

# BMP280 merepinna rõhk kõrguse arvutuseks
SEA_LEVEL_HPA = 1013.25

# MPU6500 registrid
MPU_AADRESS = 0x68
MPU_VOOL = 0x6B
KIIR_XOUT_H = 0x3B
GYRO_XOUT_H = 0x43

# =========================================================
# ABIFUNKTSIOONID
# =========================================================

def arvuta_korgus_m(pressure_hpa, sea_level_hpa=SEA_LEVEL_HPA):
    return 44330.0 * (1.0 - (pressure_hpa / sea_level_hpa) ** 0.1903)

def loe_imu(register):
    high = bus.read_byte_data(MPU_AADRESS, register)
    low = bus.read_byte_data(MPU_AADRESS, register + 1)
    value = (high << 8) + low
    if value >= 0x8000:
        value -= 65536
    return value

def saada_lora(rida):
    try:
        lora.write((rida + "\n").encode("utf-8"))
        return True
    except Exception as e:
        print("LoRa saatmise viga:", e)
        return False

def loo_preview_base64(pildi_asukoht):
    with Image.open(pildi_asukoht) as img:
        img = img.convert("L")
        img.thumbnail(PREVIEW_SIZE)
        puhver = io.BytesIO()
        img.save(puhver, format="JPEG", quality=PREVIEW_JPEG_QUALITY)
        return base64.b64encode(puhver.getvalue()).decode("ascii")

def saada_preview_lora(image_seq, timestamp_str, pildi_asukoht):
    try:
        preview_b64 = loo_preview_base64(pildi_asukoht)
        total = (len(preview_b64) + PREVIEW_CHUNK_SIZE - 1) // PREVIEW_CHUNK_SIZE

        # Pildi meta-pakett
        saada_lora(f"IMGMETA,{image_seq},{timestamp_str},{os.path.basename(pildi_asukoht)},{total}")

        # Pildi jupid
        for n in range(total):
            chunk = preview_b64[n * PREVIEW_CHUNK_SIZE:(n + 1) * PREVIEW_CHUNK_SIZE]
            saada_lora(f"IMG,{image_seq},{n},{total},{chunk}")
            time.sleep(0.03)

        print(f"Preview saadetud: image_seq={image_seq}, chunks={total}")
    except Exception as e:
        print("Preview loomise/saatmise viga:", e)

# =========================================================
# KAUSTAD JA FAILID
# =========================================================

os.makedirs(ANDURI_KAUST, exist_ok=True)
os.makedirs(KAAMERA_KAUST, exist_ok=True)

if not os.path.isfile(BARO_LOGI):
    with open(BARO_LOGI, "w", encoding="utf-8") as f:
        f.write("timestamp,temperature_C,pressure_hPa,altitude_m\n")

if not os.path.isfile(IMU_LOGI):
    with open(IMU_LOGI, "w", encoding="utf-8") as f:
        f.write("timestamp,kiirendus_x,kiirendus_y,kiirendus_z,gyro_x,gyro_y,gyro_z\n")

# =========================================================
# ANDURID
# =========================================================

# BMP280
i2c = busio.I2C(board.SCL, board.SDA)
bmp280 = adafruit_bmp280.Adafruit_BMP280_I2C(i2c, address=0x76)

# MPU6500
bus = smbus2.SMBus(1)
bus.write_byte_data(MPU_AADRESS, MPU_VOOL, 0)

# =========================================================
# LORA
# =========================================================

lora = serial.Serial(LORA_PORT, LORA_BAUD, timeout=0.2)

# =========================================================
# KAAMERA
# =========================================================

kaamera = Picamera2()
config = kaamera.create_still_configuration(
    main={"size": (3280, 2464)}
)
kaamera.configure(config)
kaamera.start()
time.sleep(2)

print("Login andmeid ja saadan LoRa kaudu. Ctrl+C peatamiseks.")

# =========================================================
# LOOP
# =========================================================

telemetry_seq = 0
image_seq = 0

try:
    while True:
        now_dt = datetime.now()
        now = now_dt.strftime("%Y-%m-%d %H:%M:%S")

        temp = None
        rohk = None
        alt = None

        ax_raw = None
        ay_raw = None
        az_raw = None
        gx_raw = None
        gy_raw = None
        gz_raw = None

        # -------------------------
        # BARO
        # -------------------------
        try:
            temp = bmp280.temperature
            rohk = bmp280.pressure
            alt = arvuta_korgus_m(rohk)

            with open(BARO_LOGI, "a", encoding="utf-8") as f:
                f.write(f"{now},{temp:.2f},{rohk:.2f},{alt:.2f}\n")

            print(f"{now} | BMP280 | {temp:.2f} C | {rohk:.2f} hPa | {alt:.2f} m")
        except Exception as e:
            print("BMP280 viga:", e)

        # -------------------------
        # IMU
        # -------------------------
        try:
            ax_raw = loe_imu(KIIR_XOUT_H)
            ay_raw = loe_imu(KIIR_XOUT_H + 2)
            az_raw = loe_imu(KIIR_XOUT_H + 4)

            gx_raw = loe_imu(GYRO_XOUT_H)
            gy_raw = loe_imu(GYRO_XOUT_H + 2)
            gz_raw = loe_imu(GYRO_XOUT_H + 4)

            with open(IMU_LOGI, "a", encoding="utf-8") as f:
                f.write(f"{now},{ax_raw},{ay_raw},{az_raw},{gx_raw},{gy_raw},{gz_raw}\n")

            print(f"{now} | MPU6500 | {ax_raw} | {ay_raw} | {az_raw} | {gx_raw} | {gy_raw} | {gz_raw}")
        except Exception as e:
            print("MPU6500 viga:", e)

        # -------------------------
        # TELEMETRY LORA
        # -------------------------
        try:
            telemetry_seq += 1

            temp_s = f"{temp:.2f}" if temp is not None else "nan"
            rohk_s = f"{rohk:.2f}" if rohk is not None else "nan"
            alt_s = f"{alt:.2f}" if alt is not None else "nan"

            ax_s = str(ax_raw) if ax_raw is not None else "nan"
            ay_s = str(ay_raw) if ay_raw is not None else "nan"
            az_s = str(az_raw) if az_raw is not None else "nan"
            gx_s = str(gx_raw) if gx_raw is not None else "nan"
            gy_s = str(gy_raw) if gy_raw is not None else "nan"
            gz_s = str(gz_raw) if gz_raw is not None else "nan"

            telemetry_packet = (
                f"TEL,{telemetry_seq},{now},"
                f"{temp_s},{rohk_s},{alt_s},"
                f"{ax_s},{ay_s},{az_s},{gx_s},{gy_s},{gz_s}"
            )

            saada_lora(telemetry_packet)
            print("LoRa TX:", telemetry_packet)
        except Exception as e:
            print("Telemeetria saatmise viga:", e)

        # -------------------------
        # KAAMERA
        # -------------------------
        try:
            image_seq += 1
            nimi = now_dt.strftime("%Y-%m-%d_%H-%M-%S.jpg")
            asukoht = os.path.join(KAAMERA_KAUST, nimi)

            kaamera.capture_file(asukoht)
            print("Salvestatud:", asukoht)

            # Saada ainult preview aeg-ajalt
            if image_seq % PREVIEW_EVERY_N_IMAGES == 0:
                saada_preview_lora(image_seq, now_dt.strftime("%Y-%m-%d_%H-%M-%S"), asukoht)

        except Exception as e:
            print("Kaamera viga:", e)

        time.sleep(LOOP_DELAY_S)

except KeyboardInterrupt:
    print("\nLogimine lõpetatud.")

finally:
    try:
        kaamera.stop()
    except Exception:
        pass

    try:
        lora.close()
    except Exception:
        pass

    try:
        bus.close()
    except Exception:
        pass