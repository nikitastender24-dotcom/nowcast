from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from PIL import Image, ImageDraw
import requests
import json
import math
import io
import os

# Точная палитра из 12 цветов (RGB + Alpha)
RADAR_PALETTE = {
    1: (156, 170, 179, 255),  # Обл. Сред. Яруса
    2: (162, 198, 254, 255),  # Сл. Образования
    3: (70, 254, 149, 255),   # Осадки слабые
    4: (1, 194, 94, 255),     # Осадки умеренные
    5: (1, 154, 8, 255),      # Осадки сильные
    6: (255, 255, 131, 255),  # Кучевая обл.
    7: (62, 137, 253, 255),   # Ливень слабый
    8: (1, 58, 255, 255),     # Ливень умеренный
    9: (2, 8, 119, 255),      # Ливень сильный
    10: (255, 171, 128, 255), # Гроза (R)
    11: (255, 89, 132, 255),  # Гроза R
    12: (253, 6, 9, 255)      # Гроза R+
}

def lat_to_mercator(lat):
    lat = max(min(lat, 89.5), -89.5)
    r_major = 6378137.0
    return r_major * math.log(math.tan(math.pi / 4.0 + math.radians(lat) / 2.0))

def lon_to_mercator(lon):
    r_major = 6378137.0
    return r_major * math.radians(lon)

class RadarHTTPServer(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        return  # Отключение спама в консоль

    def do_GET(self):
        parsed_url = urlparse(self.path)
        path = parsed_url.path
        query_params = parse_qs(parsed_url.query)

        # Главная страница
        if path == '/' or path == '/index.html':
            try:
                with open('index.html', 'rb') as f:
                    self.send_response(200)
                    self.send_header('Content-Type', 'text/html; charset=utf-8')
                    self.end_headers()
                    self.wfile.write(f.read())
            except FileNotFoundError:
                self.send_error(404, "File index.html not found")
            return

        # Подключение стилей
        elif path == '/style.css':
            try:
                with open('style.css', 'rb') as f:
                    self.send_response(200)
                    self.send_header('Content-Type', 'text/css')
                    self.end_headers()
                    self.wfile.write(f.read())
            except FileNotFoundError:
                self.send_error(404, "File style.css not found")
            return

        # Подключение логики карты
        elif path == '/demo.js':
            try:
                with open('demo.js', 'rb') as f:
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/javascript')
                    self.end_headers()
                    self.wfile.write(f.read())
            except FileNotFoundError:
                self.send_error(404, "File demo.js not found")
            return

        # Статическая легенда из папки static
        elif path == '/static/legend.png':
            if os.path.exists('static/legend.png'):
                with open('static/legend.png', 'rb') as f:
                    self.send_response(200)
                    self.send_header('Content-Type', 'image/png')
                    self.end_headers()
                    self.wfile.write(f.read())
            else:
                # Если файла физически нет, отдаем пустую заглушку во избежание краша UI
                img = Image.new('RGBA', (150, 300), (255, 255, 255, 0))
                img_io = io.BytesIO()
                img.save(img_io, 'PNG')
                self.send_response(200)
                self.send_header('Content-Type', 'image/png')
                self.end_headers()
                self.wfile.write(img_io.getvalue())
            return

        # Эндпоинт получения исходных данных явления
        elif path == '/get_data':
            try:
                url = "https://metlorad.ru/api/cwt/phenomena-new?frame=18"
                res = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(res.content)
            except Exception as e:
                self.send_error(500, f"Internal Server Error: {str(e)}")
            return

        # Эндпоинт генерации радарного PNG тайла
        elif path == '/get_tile.png':
            try:
                bbox_list = query_params.get('bbox')
                width_list = query_params.get('width', ['800'])
                height_list = query_params.get('height', ['600'])

                if not bbox_list:
                    self.send_response(400)
                    self.end_headers()
                    return

                bbox_str = bbox_list[0]
                width = int(width_list[0])
                height = int(height_list[0])

                min_lon, min_lat, max_lon, max_lat = map(float, bbox_str.split(','))

                x_min = lon_to_mercator(min_lon)
                x_max = lon_to_mercator(max_lon)
                y_min = lat_to_mercator(min_lat)
                y_max = lat_to_mercator(max_lat)

                img = Image.new('RGBA', (width, height), (0, 0, 0, 0))
                draw = ImageDraw.Draw(img)

                url = "https://metlorad.ru/api/cwt/phenomena-new?frame=0"
                res = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
                data = res.json()

                d_lat, d_lon = 0.036, 0.055
                scale_x = width / (x_max - x_min)
                scale_y = height / (y_max - y_min)

                for f in data.get('features', []):
                    strength = f.get('strength')
                    if not strength or strength not in RADAR_PALETTE:
                        continue

                    f_lat, f_lon = f['lat'], f['lon']
                    if not (min_lat - 0.5 <= f_lat <= max_lat + 0.5 and min_lon - 0.5 <= f_lon <= max_lon + 0.5):
                        continue

                    x1 = lon_to_mercator(f_lon - d_lon / 2)
                    x2 = lon_to_mercator(f_lon + d_lon / 2)
                    y1 = lat_to_mercator(f_lat - d_lat / 2)
                    y2 = lat_to_mercator(f_lat + d_lat / 2)

                    px1 = math.floor((x1 - x_min) * scale_x)
                    px2 = math.ceil((x2 - x_min) * scale_x)
                    py1 = math.floor((1.0 - (y2 - y_min) / (y_max - y_min)) * height)
                    py2 = math.ceil((1.0 - (y1 - y_min) / (y_max - y_min)) * height)

                    if px2 <= px1: px2 = px1 + 1
                    if py2 <= py1: py2 = py1 + 1

                    color = RADAR_PALETTE[strength]
                    draw.rectangle([px1, py1, px2, py2], fill=color)

                img_io = io.BytesIO()
                img.save(img_io, 'PNG')
                img_io.seek(0)

                self.send_response(200)
                self.send_header('Content-Type', 'image/png')
                self.end_headers()
                self.wfile.write(img_io.getvalue())

            except Exception as e:
                self.send_response(500)
                self.end_headers()
                self.wfile.write(str(e).encode('utf-8'))
            return

        else:
            self.send_error(404, "Not Found")

if __name__ == '__main__':
    if not os.path.exists('static'):
        os.makedirs('static')
    server_address = ('', 8080)
    httpd = HTTPServer(server_address, RadarHTTPServer)
    print("Сервер запущен на http://localhost:8080")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        httpd.server_close()
