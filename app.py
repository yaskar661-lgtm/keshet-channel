import time
import threading
from flask import Flask, Response, request
import requests
import os

app = Flask(__name__)

HEADERS = {
    "User-Agent": "SmartOsTV",
    "Accept-Encoding": "identity",
}

CHANNELS = {
    "k12": {
        "master": "https://mako-streaming.akamaized.net/stream/hls/live/2033791/k12/index.m3u8",
        "base": "https://mako-streaming.akamaized.net/stream/hls/live/2033791/k12/",
    },
    "k12wad": {
        "master": "https://mako-streaming.akamaized.net/stream/hls/live/2033791/k12n12wad/index.m3u8",
        "base": "https://mako-streaming.akamaized.net/stream/hls/live/2033791/k12n12wad/",
    }
}

cache = {
    "ticket": None
}

def update_ticket_background():
    """Ticket'ı (hdnea) arka planda 4 saatte bir yeniler."""
    while True:
        try:
            s = requests.Session()
            res = s.get(
                "https://mass.mako.co.il/ClicksStatistics/entitlementsServicesV2.jsp?et=ngt&lp=/stream/hls/live/2033791/k12/index.m3u8?as=1&rv=AKAMAI",
                headers=HEADERS
            ).json()
            cache["ticket"] = res["tickets"][0]["ticket"]
            print("[INFO] Ticket başarıyla güncellendi.")
        except Exception as e:
            print(f"[ERROR] Ticket güncelleme hatası: {e}")
        
        time.sleep(14400) # 4 saat

@app.route("/isr/<name>.m3u8")
def serve_playlist(name):
    """Master m3u8 listesini çeker, master_url kontrolüyle biletleri işler."""
    if name not in CHANNELS:
        return "Channel not found", 404
    
    data = CHANNELS[name]
    toki = cache.get("ticket", "")
    
    master_url = data["master"]
    
    # Master URL bazlı .m3u8 ve bilet kontrolü
    if master_url.endswith(".m3u8") and toki:
        master_url_with_ticket = f"{master_url}?{toki}"
    else:
        master_url_with_ticket = master_url
    
    try:
        resp = requests.get(master_url_with_ticket, headers=HEADERS)
        if resp.status_code != 200:
            return "Failed to fetch master playlist", 500
            
        new_lines = []
        for line in resp.text.splitlines():
            line = line.strip()
            if not line:
                continue
            if line.startswith("#"):
                new_lines.append(line)
            else:
                if ".mp4" in line.lower():
                    target_url = data["base"] + line
                    if target_url.endswith(".mp4") and toki:
                        target_url += f"?{toki}"
                    proxied_url = f"/segment.ts?url={requests.utils.quote(target_url, safe='')}"
                elif line.endswith(".m3u8") or ".m3u8" in line.lower():
                    target_url = data["base"] + line
                    if master_url.endswith(".m3u8") and toki:
                        target_url += f"?{toki}"
                    proxied_url = f"/proxy?url={requests.utils.quote(target_url, safe='')}"
                else:
                    # TS veya diğer segmentler için de proxy?url= kullanalım
                    target_url = data["base"] + line
                    if toki:
                        target_url += f"?{toki}"
                    proxied_url = f"/proxy?url={requests.utils.quote(target_url, safe='')}"
                    
                new_lines.append(proxied_url)
                
        return Response("\n".join(new_lines), mimetype="application/vnd.apple.mpegurl")
    except Exception as e:
        return f"Error: {e}", 500

@app.route("/proxy")
def proxy():
    """
    Alt m3u8 veya TS segmentleri için proxy. 
    Eğer gelen url bir m3u8 ise, 4 saniyede bir güncel içeriği ve biletleri tazeleyerek sunar.
    """
    url = request.args.get("url")
    if not url:
        return "Missing URL", 400
    
    toki = cache.get("ticket", "")
    
    # Eğer istek atılan URL m3u8 uzantılıysa içeriğini anlık çekip güncelliyoruz (4 saniyede bir tetiklenmeye uygundur)
    if ".m3u8" in url:
        if toki and toki not in url:
            url += ("&" if "?" in url else "?") + toki
            
        try:
            resp = requests.get(url, headers=HEADERS)
            if resp.status_code == 200:
                # Alt m3u8 içindeki göreceli segment yollarını da /proxy?url= formatına çevirebiliriz
                base_url = url.rsplit("/", 1)[0] + "/"
                new_lines = []
                for line in resp.text.splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    if line.startswith("#"):
                        new_lines.append(line)
                    else:
                        if line.startswith("http"):
                            sub_target = line
                        else:
                            sub_target = base_url + line
                        
                        if toki and toki not in sub_target:
                            sub_target += ("&" if "?" in sub_target else "?") + toki
                            
                        proxied_sub = f"/proxy?url={requests.utils.quote(sub_target, safe='')}"
                        new_lines.append(proxied_sub)
                        
                return Response("\n".join(new_lines), mimetype="application/vnd.apple.mpegurl")
        except Exception:
            pass

    # Normal TS segmentleri veya diğer dosyalar için direkt akış (stream) proxy
    if toki and toki not in url:
        url += ("&" if "?" in url else "?") + toki

    def generate():
        try:
            with requests.get(url, headers=HEADERS, stream=True) as r:
                for chunk in r.iter_content(chunk_size=8192):
                    if chunk:
                        yield chunk
        except Exception:
            pass

    mimetype = "application/vnd.apple.mpegurl" if ".m3u8" in url else "video/mp2t"
    return Response(generate(), mimetype=mimetype)

@app.route("/segment.ts")
def segment_proxy():
    """MP4 segmentleri için şeffaf proxy."""
    url = request.args.get("url")
    if not url:
        return "Missing URL", 400
    
    toki = cache.get("ticket", "")
    if url.endswith(".mp4") and toki and toki not in url:
        url += ("&" if "?" in url else "?") + toki

    def generate():
        try:
            with requests.get(url, headers=HEADERS, stream=True) as r:
                for chunk in r.iter_content(chunk_size=8192):
                    if chunk:
                        yield chunk
        except Exception:
            pass

    return Response(generate(), mimetype="video/mp4")

if __name__ == "__main__":
    t = threading.Thread(target=update_ticket_background, daemon=True)
    t.start()
    
    try:
        s = requests.Session()
        res = s.get(
            "https://mass.mako.co.il/ClicksStatistics/entitlementsServicesV2.jsp?et=ngt&lp=/stream/hls/live/2033791/k12/index.m3u8?as=1&rv=AKAMAI",
            headers=HEADERS
        ).json()
        cache["ticket"] = res["tickets"][0]["ticket"]
    except:
        pass

    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=5000)
