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
    1: (156, 170, 179, 255),
    2: (162, 198, 254, 255),
    3: (70, 254, 149, 255),
    4: (1, 194, 94, 255),
    5: (1, 154, 8, 255),
    6: (255, 255, 131, 255),
    7: (62, 137, 253, 255),
    8: (1, 58, 255, 255),
    9: (2, 8, 119, 255),
    10: (255, 171, 128, 255),
    11: (255, 89, 132, 255),
    12: (253, 6, 9, 255),
    13: (205, 105, 8),
    14: (143, 73, 15),
    15: (88, 14, 8),
    16: (255, 171, 255),
    17: (255, 88, 255),
    18: (200, 9, 202),
}

def lat_to_mercator(lat):
    lat = max(min(lat, 89.5), -89.5)
    r_major = 6378137.0
    return r_major * math.log(math.tan(math.pi / 4.0 + math.radians(lat) / 2.0))

def lon_to_mercator(lon):
    r_major = 6378137.0
    return r_major * math.radians(lon)

def haversine(lat1, lon1, lat2, lon2):
    R = 6371000  # радиус Земли в метрах
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlambda/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))

class RadarHTTPServer(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        return  # тишина

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
                self.send_error(404, "index.html not found")
            return

        # Эндпоинт получения полных данных (для отладки, не обязателен)
        elif path == '/get_data':
            try:
                url = "https://metlorad.ru/api/cwt/phenomena-new?frame=35"
                res = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(res.content)
            except Exception as e:
                self.send_error(500, f"Error: {str(e)}")
            return

        # Генерация тайла PNG с учётом фильтра по радару
        elif path == '/get_tile.png':
            try:
                bbox_list = query_params.get('bbox')
                width_list = query_params.get('width', ['800'])
                height_list = query_params.get('height', ['600'])
                radar_filter = query_params.get('radar', [None])[0]  # None, 'all' или конкретный stringId

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

                url = "https://metlorad.ru/api/cwt/phenomena-new?frame=35"
                res = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
                data = res.json()

                d_lat, d_lon = 0.036, 0.055
                scale_x = width / (x_max - x_min)
                scale_y = height / (y_max - y_min)

                for f in data.get('features', []):
                    # Фильтр по радару
                    if radar_filter and radar_filter != 'all':
                        if f.get('stringId') != radar_filter:
                            continue

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

                    draw.rectangle([px1, py1, px2, py2], fill=RADAR_PALETTE[strength])

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

        # Информация о точке при клике
        elif path == '/get_point_info':
            try:
                lat = float(query_params.get('lat', [None])[0])
                lon = float(query_params.get('lon', [None])[0])
                radar_filter = query_params.get('radar', [None])[0]  # None, 'all' или stringId

                if lat is None or lon is None:
                    self.send_response(400)
                    self.end_headers()
                    return

                url = "https://metlorad.ru/api/cwt/phenomena-new?frame=35"
                res = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
                data = res.json()

                results = []
                for f in data.get('features', []):
                    if radar_filter and radar_filter != 'all':
                        if f.get('stringId') != radar_filter:
                            continue

                    strength = f.get('strength')
                    if not strength or strength not in RADAR_PALETTE:
                        continue

                    f_lat = f['lat']
                    f_lon = f['lon']
                    # Радиус поиска ~20 км
                    if haversine(lat, lon, f_lat, f_lon) > 20000:
                        continue

                    results.append({
                        "lat": f_lat,
                        "lon": f_lon,
                        "strength": strength,
                        "radarId": f.get('radarId'),
                        "stringId": f.get('stringId'),
                        "distance_m": haversine(lat, lon, f_lat, f_lon)
                    })

                # Сортируем по расстоянию
                results.sort(key=lambda x: x['distance_m'])
                # Берем не более 5 ближайших
                results = results[:5]

                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps(results, ensure_ascii=False).encode('utf-8'))

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
