import sys
import os

# 🚀 针对打包环境的极速本地浏览器挂载 (完美移植自文件二)
if getattr(sys, 'frozen', False):
    base_path = sys._MEIPASS
else:
    base_path = os.path.abspath(os.path.dirname(__file__))

os.environ["PLAYWRIGHT_BROWSERS_PATH"] = os.path.join(base_path, "pw_browsers")

import customtkinter as ctk
import requests
import re
import json
import time
import concurrent.futures
import threading
import shutil
import urllib.parse
from PIL import Image
# 👇 新增这三行，让 Pillow 自动支持识别 HEIC 格式
try:
    from pillow_heif import register_heif_opener
    register_heif_opener()
except ImportError:
    pass
from tkinter import filedialog
import urllib3

# 引入真实浏览器内核 (完美移植自文件二)
try:
    from playwright.sync_api import sync_playwright
except ImportError:
    pass

# 禁用 requests 的 SSL 警告
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ─────────────────────────────────────────────────────────────
#  安全提取 __INITIAL_STATE__ 媒体数据（规避 Vue 循环引用崩溃）
#  直接在 JS 侧把需要的字段手动拷贝成干净的普通对象再 stringify
# ─────────────────────────────────────────────────────────────
_JS_EXTRACT_MEDIA = """
() => {
    try {
        var st = window.__INITIAL_STATE__;
        if (!st) return '';
        var nm = st.note && st.note.noteDetailMap;
        if (!nm) return '';
        var keys = Object.keys(nm);
        if (!keys.length) return '';
        var note = nm[keys[0]] && nm[keys[0]].note;
        if (!note) return '';
        var result = {
            note_type:   String(note.type || 'normal'),
            title:       String(note.title || ''),
            images:      [],
            videos:      [],
            live_videos: []
        };
        var imgList = note.imageList || [];
        for (var i = 0; i < imgList.length; i++) {
            var img = imgList[i];
            var iu = String(img.urlDefault || img.url || img.traceId || '');
            if (iu) result.images.push(iu);
            try {
                var lv = '';
                if (img.livePhotoInfo && img.livePhotoInfo.video)
                    lv = String(img.livePhotoInfo.video.media.stream.h264[0].masterUrl || '');
                else if (img.video && img.video.media)
                    lv = String(img.video.media.stream.h264[0].masterUrl || '');
                if (lv) result.live_videos.push(lv);
            } catch(e) {}
        }
        if (result.note_type === 'video' && !result.videos.length) {
            try {
                var vv = String(note.video.media.stream.h264[0].masterUrl || '');
                if (vv) result.videos.push(vv);
            } catch(e) {}
        }
        return JSON.stringify(result);
    } catch(e) { return ''; }
}
"""

# 设置外观
ctk.set_appearance_mode("System")
ctk.set_default_color_theme("blue")

class DualPlatformDownloader(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("全能解析下载器（无水印版）")
        self.geometry("680x660")
        
        if sys.platform.startswith("win"):
            self.config_file = os.path.join(os.environ["USERPROFILE"], ".dx_downloader_config.json")
        else:
            self.config_file = os.path.join(os.path.expanduser("~"), ".dx_downloader_config.json")
            
        self.config_data = self._load_config()
        self.base_download_dir = self.config_data["download_dir"]
        
        self.is_running = False
        self.proxies = {"http": None, "https": None}
        # 下载调度器：允许多个笔记并发下载（每个笔记内部还有5并发）
        self.download_queue_executor = concurrent.futures.ThreadPoolExecutor(max_workers=3)
        
        self.protocol("WM_DELETE_WINDOW", self.on_closing)
        self.setup_ui()

    def _get_default_dir(self):
        if sys.platform.startswith("win"):
            return os.path.join(os.environ["USERPROFILE"], "Downloads")
        else:
            return os.path.join(os.path.expanduser("~"), "Downloads")

    def _load_config(self):
        config = {
            "download_dir": self._get_default_dir(),
            "live_option": "全部下载",
            "video_option": "全部下载",
            "folder_mode": True,
        }
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, "r", encoding="utf-8") as f:
                    saved = json.load(f)
                    if os.path.isdir(saved.get("download_dir", "")):
                        config["download_dir"] = saved["download_dir"]
                    for k in ("live_option", "video_option", "folder_mode"):
                        if k in saved: config[k] = saved[k]
            except: pass
        return config

    def _save_config(self, *args):
        try:
            config = {
                "download_dir": self.base_download_dir,
                "live_option": self.live_option_var.get(),
                "video_option": self.video_option_var.get(),
                "folder_mode": self.folder_mode_var.get(),
            }
            with open(self.config_file, "w", encoding="utf-8") as f:
                json.dump(config, f, ensure_ascii=False)
        except: pass

    def _clean_folder_name(self, title, fallback_prefix):
        if not title: return f"{fallback_prefix}_{int(time.time())}"
        safe_title = re.sub(r'[\\/*?:"<>|\n\r]', "", title).strip()
        return safe_title[:50] if safe_title else f"{fallback_prefix}_{int(time.time())}"

    def on_closing(self):
        self.is_running = False
        self.destroy()
        os._exit(0) 

    def setup_ui(self):
        self.url_frame = ctk.CTkFrame(self)
        self.url_frame.pack(pady=(15, 5), padx=20, fill="x")
        
        self.url_label = ctk.CTkLabel(self.url_frame, text="分享文案\n（支持图文/视频）\n多链接请换行")
        self.url_label.pack(side="left", padx=10)
        
        self.url_textbox = ctk.CTkTextbox(self.url_frame, width=420, height=120)
        self.url_textbox.pack(side="left", padx=10, pady=10)

        self.option_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.option_frame.pack(pady=(5, 0), padx=25, fill="x")
        
        self.live_option_label = ctk.CTkLabel(self.option_frame, text="🍠小红书live图处理：", text_color="gray")
        self.live_option_label.pack(side="left")
        
        self.live_option_var = ctk.StringVar(value=self.config_data["live_option"])
        self.live_seg_btn = ctk.CTkSegmentedButton(self.option_frame, values=["全部下载", "仅下图片", "仅下视频"], variable=self.live_option_var, command=self._save_config)
        self.live_seg_btn.pack(side="left", padx=10)

        self.option_frame2 = ctk.CTkFrame(self, fg_color="transparent")
        self.option_frame2.pack(pady=5, padx=25, fill="x")
        
        self.video_option_label = ctk.CTkLabel(self.option_frame2, text="📺 纯视频链接处理： ", text_color="gray")
        self.video_option_label.pack(side="left")
        
        self.video_option_var = ctk.StringVar(value=self.config_data["video_option"])
        self.video_seg_btn = ctk.CTkSegmentedButton(self.option_frame2, values=["全部下载", "仅保存视频", "仅下载封面"], variable=self.video_option_var, command=self._save_config)
        self.video_seg_btn.pack(side="left", padx=10)

        self.folder_mode_var = ctk.BooleanVar(value=self.config_data["folder_mode"])
        self.folder_mode_switch = ctk.CTkSwitch(self.option_frame2, text="独立建文件夹", variable=self.folder_mode_var, command=self._save_config)
        self.folder_mode_switch.pack(side="right")

        self.path_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.path_frame.pack(pady=5, padx=25, fill="x")
        self.path_display = ctk.CTkLabel(self.path_frame, text=f"📂 保存至: {self.base_download_dir}", text_color="gray", wraplength=500)
        self.path_display.pack(side="left")
        self.change_btn = ctk.CTkButton(self.path_frame, text="更改目录", width=70, height=24, command=self.change_download_dir)
        self.change_btn.pack(side="right")

        self.btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.btn_frame.pack(pady=10)
        self.start_btn = ctk.CTkButton(self.btn_frame, text="⚡ 智能获取并极速下载", command=self.start_download)
        self.start_btn.pack(side="left", padx=10)
        self.stop_btn = ctk.CTkButton(self.btn_frame, text="🛑 停止", command=self.stop_download, fg_color="#dc3545", state="disabled")
        self.stop_btn.pack(side="left", padx=10)

        self.log_textbox = ctk.CTkTextbox(self, height=170)
        self.log_textbox.pack(pady=10, padx=20, fill="both", expand=True)
        self.log("🚀 全匿名模式：无需登录，手机链接/PC链接均支持，视频无水印下载。")

    def change_download_dir(self):
        new_dir = filedialog.askdirectory(initialdir=self.base_download_dir)
        if new_dir:
            self.base_download_dir = new_dir
            self.path_display.configure(text=f"📂 当前保存至: {self.base_download_dir}")
            self._save_config()

    def log(self, message):
        self.after(0, lambda: self._safe_log(message))

    def _safe_log(self, message):
        self.log_textbox.insert("end", message + "\n")
        self.log_textbox.see("end")

    def stop_download(self):
        if self.is_running:
            self.is_running = False
            self.stop_btn.configure(state="disabled")
            self.start_btn.configure(state="normal")
            self.log("\n⚠️ 任务已被强制终止。")

    def start_download(self):
        raw_text = self.url_textbox.get("1.0", "end").strip()
        if not raw_text: return
        urls = list(dict.fromkeys(re.findall(r'https?://[-A-Za-z0-9+&@#/%?=~_|!:,.;]+[-A-Za-z0-9+&@#/%=~_|]', raw_text)))
        if not urls:
            self.log("❌ 未发现有效链接，请检查分享文案。")
            return
            
        live_option = self.live_option_var.get()
        video_option = self.video_option_var.get()
        folder_mode = self.folder_mode_var.get()
        
        time_str = time.strftime("%Y%m%d_%H%M%S")
        global_folder = f"批量下载_{time_str}"
        
        self.is_running = True
        self.start_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        self.log("-" * 30)
        self.log(f"📋 锁定 {len(urls)} 个链接，正在全局嗅探...")
        threading.Thread(target=self._manager_worker, args=(urls, live_option, video_option, folder_mode, global_folder), daemon=True).start()

    def _manager_worker(self, urls, live_option, video_option, folder_mode, global_folder):
        # ── 流水线并行：解析完一个立即开始下载，不等其他链接解析完 ──
        # 解析线程池：最多 3 个并发（Playwright 实例吃内存，不宜太多）
        # 下载线程池：每个笔记内部 5 并发（图片/视频并行下载）
        # 整体效果：解析 ↔ 下载 完全重叠，链路利用率最大化

        # 预扫一遍 URL，判断是否有混合类型（视频 + 图文），用于决定是否分文件夹
        # 由于解析才知道类型，这里用一个共享列表收结果，动态更新路径策略
        completed_results = []   # [(title, media, platform, index), ...]
        results_lock = threading.Lock()
        download_futures = []

        def on_parsed(future):
            """解析完成回调：每个笔记根据自身内容类型独立决定路径，无竞态"""
            if not self.is_running:
                return
            res = future.result()
            if not res:
                return
            title, media, platform, index = res

            with results_lock:
                completed_results.append(res)

            is_pure_video = bool(media["videos"] and not media["live_videos"])

            if len(urls) == 1:
                # 单链接：直接放根目录/标题，不建批量文件夹
                save_path = os.path.join(self.base_download_dir, title) if folder_mode else self.base_download_dir
            else:
                # 多链接：统一在 global_folder 下，按内容类型分子文件夹
                # 每个笔记根据自身类型独立决定，不依赖其他链接的结果
                type_sub = "视频" if is_pure_video else "图文"
                base = os.path.join(self.base_download_dir, global_folder, type_sub)
                save_path = os.path.join(base, title) if folder_mode else base

            f = self.download_queue_executor.submit(
                self._submit_tasks, title, media, platform, index,
                save_path, folder_mode, live_option, video_option
            )
            download_futures.append(f)

        parse_executor = concurrent.futures.ThreadPoolExecutor(max_workers=3)
        futures = [parse_executor.submit(self._parse_single_url, url, i)
                   for i, url in enumerate(urls, 1)]
        for f in futures:
            f.add_done_callback(on_parsed)

        # 等所有解析任务结束
        parse_executor.shutdown(wait=True)

        if not self.is_running:
            return

        # 等所有已提交的下载任务完成，再宣告完成
        if download_futures:
            concurrent.futures.wait(download_futures)

        if self.is_running:
            self.log("-" * 30)
            self.log("🎉 所有任务已全部完成！")
            self.after(0, lambda: self.start_btn.configure(state="normal"))
            self.after(0, lambda: self.stop_btn.configure(state="disabled"))
            self.is_running = False

    def _parse_single_url(self, url, index):
        if not self.is_running: return None
        self.log(f"🔍 [{index}] 获取解析中: {url[:30]}...")
        
        platform = "xhs"
        if any(x in url for x in ["dewu.com", "poizon.com", "dw4.co"]): platform = "dewu"
        elif "douyin.com" in url: platform = "douyin"
        
        safe_title, media = "", {"videos": [], "images": [], "live_videos": []}
        parse_success = False
        last_error = ""

        for attempt in range(3):
            if not self.is_running: return None
            try:
                if platform == "dewu":
                    safe_title, media = self._extract_dewu(url, index)
                elif platform == "douyin":
                    safe_title, media = self._extract_douyin(url, index)
                else:
                    safe_title, media = self._extract_xhs(url, index)
                parse_success = True
                break
            except Exception as e:
                last_error = str(e)
                if attempt < 2: 
                    self.log(f"   ⚠️ [{index}] 解析受阻重试 ({attempt+2}/3) : {last_error}")
                    time.sleep(1.5)
                
        if not self.is_running: return None
        
        if parse_success and (media["videos"] or media["images"] or any(media.get("live_videos", []))):
            self.log(f"   ✅ [{index}] 解析完毕，准备入列。")
            return (safe_title, media, platform, index)
        else:
            self.log(f"❌ [{index}] 解析彻底失败: {last_error if last_error else '未发现有效媒体文件'}")
            return None

    # =========================================================================
    # 小红书提取：全匿名双轨
    #   轨道1: requests 匿名  (手机短链 xhslink.com，极速)
    #   轨道2: Playwright 匿名 (PC长链 / 短链风控降级，完全不注入Cookie)
    #
    #   关键原则：全程不携带任何用户身份信息
    #   → 服务端返回公共版 masterUrl（无用户参数）→ 下载无水印
    # =========================================================================
    def _extract_xhs(self, url, index):
        match = re.search(r'(https?://[-A-Za-z0-9+&@#/%?=~_|!:,.;]+[-A-Za-z0-9+&@#/%=~_|])', url)
        clean_url = match.group(1) if match else url

        # 统一规范化目标 URL：提取 24位 noteId 构造标准路径
        note_id_m = re.search(r'([0-9a-fA-F]{24})', clean_url)
        if note_id_m:
            target_url = f"https://www.xiaohongshu.com/explore/{note_id_m.group(1)}"
        elif "xhslink.com" in clean_url:
            target_url = clean_url   # 保留短链，让 requests 跟随跳转
        elif "/user/profile" in clean_url:
            raise Exception("暂不支持解析纯主页，请进入单篇笔记后复制链接！")
        else:
            target_url = clean_url

        title = "小红书下载"
        media = {"videos": [], "images": [], "live_videos": []}

        # ── 轨道1: requests 匿名（手机短链最快，约0.5s）────────────
        if "xhslink.com" in clean_url:
            try:
                self.log(f"   💡 [{index}] 手机短链 -> requests 匿名极速通道")
                hdrs = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}
                res  = requests.get(clean_url, headers=hdrs, proxies=self.proxies,
                                    timeout=15, verify=False, allow_redirects=True)
                html = res.text
                if "/404/sec_" in res.url or "滑块" in html or "captcha" in html.lower():
                    raise Exception("触发风控，切换浏览器通道")
                title_m = re.search(r'<meta[^>]+(?:name|property)="og:title"[^>]+content="([^"]+)"', html)
                if title_m:
                    title = self._clean_folder_name(title_m.group(1).replace(" - 小红书",""), "小红书")
                state_m = re.search(r'window\.__INITIAL_STATE__=(.*?)</script>', html)
                if not state_m:
                    raise Exception("无 __INITIAL_STATE__，切换浏览器通道")
                media = self._parse_raw_state_xhs(state_m.group(1).replace('\\u002F', '/'))
                if media["videos"] or media["images"] or media["live_videos"]:
                    self.log(f"   ✅ [{index}] requests通道成功")
                    return title, media
                raise Exception("媒体为空，切换浏览器通道")
            except Exception as e:
                self.log(f"   ⚠️ [{index}] {e}")

        # ── 轨道2: Playwright 匿名浏览器（PC链接 / 短链降级）────────
        # 完全不注入任何 Cookie，服务端无法识别用户身份
        # → __INITIAL_STATE__ 里的 masterUrl 是公共无水印版本
        self.log(f"   💡 [{index}] Playwright 匿名浏览器启动...")
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                # 不设置任何 Cookie，纯匿名上下文
                ctx  = browser.new_context(
                    viewport={'width': 1280, 'height': 800},
                    user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                )
                page = ctx.new_page()
                # 屏蔽图片/媒体/样式/字体，加速加载
                page.route("**/*", lambda r: r.abort()
                           if r.request.resource_type in ("image","media","stylesheet","font")
                           else r.continue_())
                page.goto(target_url, wait_until="domcontentloaded", timeout=25000)
                # 等待 JS 执行完成，__INITIAL_STATE__ 填充
                try:
                    page.wait_for_load_state("networkidle", timeout=3000)
                except: pass
                try:
                    t = page.title()
                    if t: title = self._clean_folder_name(t.replace(" - 小红书",""), "小红书")
                except: pass
                # 用安全提取脚本（避免 Vue 响应式循环引用崩溃）
                raw = page.evaluate(_JS_EXTRACT_MEDIA) or ""
                browser.close()
                if not raw or raw.strip() in ("","{}","null"):
                    raise Exception("浏览器提取数据为空（页面可能被风控拦截）")
                media = self._parse_raw_state_xhs(raw)
        except Exception as e:
            if "playwright" not in sys.modules:
                raise Exception("缺少核心模块，请执行: pip install playwright && playwright install")
            raise Exception(f"浏览器通道失败: {e}")

        if not media["videos"] and not media["images"] and not media["live_videos"]:
            raise Exception("未发现有效媒体数据（笔记可能需要登录才能查看）")
        return title, media

    # =========================================================================
    # 媒体数据解析：兼容格式A(JS安全提取) 和 格式B(完整__INITIAL_STATE__文本)
    # =========================================================================
    def _parse_raw_state_xhs(self, raw_state_str):
        media = {"videos": [], "images": [], "live_videos": []}
        if not raw_state_str or raw_state_str.strip() in ("","{}","null","undefined"):
            return media

        def cdn_fix(u):
            # 1. 切换到无水印专用 CDN 节点
            u = re.sub(r'sns-video-[a-z0-9]+\.xhscdn\.com', 'sns-video-al.xhscdn.com', u)
            # 2. 剥离所有查询参数（参数中可能含用户身份标识）
            return u.split('?')[0]

        try:
            obj = json.loads(re.sub(r':\s*undefined', ':null', raw_state_str))

            # 格式A: _JS_EXTRACT_MEDIA 安全提取输出
            if "images" in obj and "note_type" in obj:
                for iu in obj.get("images", []):
                    if not iu: continue
                    c  = iu.split("!")[0].split("?")[0]
                    m2 = re.search(r'/[a-f0-9]{32}/(.+)', c)
                    k  = m2.group(1) if m2 else c.split("/")[-1]
                    media["images"].append(f"https://sns-img-qc.xhscdn.com/{k}")
                for vv in obj.get("videos", []):
                    if vv: media["videos"].append(cdn_fix(vv))
                for lv in obj.get("live_videos", []):
                    if lv: media["live_videos"].append(cdn_fix(lv))
                return media

            # 格式B: 完整 __INITIAL_STATE__ 文本
            note_id   = list(obj["note"]["noteDetailMap"].keys())[0]
            note_data = obj["note"]["noteDetailMap"][note_id]["note"]
            note_type = note_data.get("type", "normal")
            for img_item in note_data.get("imageList", []):
                img_url = img_item.get("urlDefault") or img_item.get("url") or img_item.get("traceId","")
                if img_url:
                    clean = img_url.split("!")[0].split("?")[0]
                    m2    = re.search(r'/[a-f0-9]{32}/(.+)', clean)
                    key   = m2.group(1) if m2 else clean.split("/")[-1]
                    media["images"].append(f"https://sns-img-qc.xhscdn.com/{key}")
                try:
                    if "livePhotoInfo" in img_item:
                        media["live_videos"].append(cdn_fix(img_item["livePhotoInfo"]["video"]["media"]["stream"]["h264"][0]["masterUrl"]))
                    elif "video" in img_item:
                        media["videos"].append(cdn_fix(img_item["video"]["media"]["stream"]["h264"][0]["masterUrl"]))
                except: pass
            if note_type == "video" and not media["videos"]:
                try: media["videos"].append(cdn_fix(note_data["video"]["media"]["stream"]["h264"][0]["masterUrl"]))
                except: pass
        except: pass

        # 正则兜底
        if not media["videos"] and not media["live_videos"]:
            for v in list(dict.fromkeys(re.findall(r'"masterUrl":"([^"]+)"', raw_state_str))):
                vc = re.sub(r'sns-video-[a-z0-9]+\.xhscdn\.com', 'sns-video-al.xhscdn.com', v).split('?')[0]
                media["live_videos" if "livePhoto" in raw_state_str else "videos"].append(vc)
        if not media["images"]:
            for u in list(dict.fromkeys(re.findall(r'"urlDefault":"([^"]+)"', raw_state_str))):
                if "avatar" in u or "icon" in u: continue
                c  = u.split("!")[0].split("?")[0]
                m2 = re.search(r'/[a-f0-9]{32}/(.+)', c)
                k  = m2.group(1) if m2 else c.split("/")[-1]
                fi = f"https://sns-img-qc.xhscdn.com/{k}"
                if fi not in media["images"]: media["images"].append(fi)
        return media

    # ========================== 抖音 提取引擎（沿用 1.4 原装代码） ==========================
    def _extract_douyin(self, url, index):
        headers = {
            "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh-Hans;q=0.9",
            "Cookie": "ttwid=1; __ac_nonce=123456789012345678901;"
        }
        res = requests.get(url, headers=headers, allow_redirects=True, timeout=15, verify=False)
        html = res.text
        
        item = None
        match = re.search(r'window\._ROUTER_DATA\s*=\s*(.*?)</script>', html, re.DOTALL)
        if match:
            json_str = match.group(1).strip()
            if json_str.endswith(';'): json_str = json_str[:-1]
            try:
                router_data = json.loads(json_str)
                loader_data = router_data.get("loaderData", {})
                for key, val in loader_data.items():
                    if isinstance(val, dict) and "videoInfoRes" in val:
                        item_list = val["videoInfoRes"].get("item_list", [])
                        if item_list:
                            item = item_list[0]
                        break
            except Exception:
                pass
                
        title = self._clean_folder_name("抖音下载", "抖音")
        videos, images, live_videos = [], [], []
        
        if item:
            title = self._clean_folder_name(item.get("desc", ""), "抖音下载")
            images_data = item.get("images", [])
            if images_data:
                for img in images_data:
                    i_urls = img.get("url_list", [])
                    if i_urls: images.append(i_urls[-1]) 
                    l_vid = ""
                    vid_obj = img.get("video") or img.get("live_photo", {}).get("video")
                    if vid_obj and vid_obj.get("play_addr"):
                        v_urls = vid_obj["play_addr"].get("url_list", [])
                        if v_urls: l_vid = v_urls[0].replace("playwm", "play")
                    if l_vid: live_videos.append(l_vid)
            else:
                video_data = item.get("video", {})
                if video_data:
                    clean_video = ""
                    bit_rates = video_data.get("bit_rate", [])
                    if bit_rates:
                        bit_rates.sort(key=lambda x: (
                            x.get("play_addr", {}).get("width", 0) * x.get("play_addr", {}).get("height", 0),
                            x.get("bit_rate", 0)
                        ), reverse=True)
                        best_play_addr = bit_rates[0].get("play_addr", {})
                        v_urls = best_play_addr.get("url_list", [])
                        if v_urls: clean_video = v_urls[0].replace("playwm", "play")
                            
                    if not clean_video:
                        uri = video_data.get("play_addr", {}).get("uri")
                        if uri:
                            clean_video = f"https://aweme.snssdk.com/aweme/v1/play/?video_id={uri}&ratio=1080p&line=0"
                        elif video_data.get("play_addr"):
                            v_urls = video_data["play_addr"].get("url_list", [])
                            if v_urls: clean_video = v_urls[0].replace("playwm", "play")
                            
                    if clean_video: videos.append(clean_video)
                        
                if video_data.get("cover"):
                    c_urls = video_data["cover"].get("url_list", [])
                    if c_urls: images.append(c_urls[-1])
        else:
            t_match = re.search(r'"desc":"(.*?)"', html)
            if t_match: title = self._clean_folder_name(t_match.group(1), "抖音下载")
            
            v_matches = re.findall(r'"play_addr"[^}]*?"url_list":\["(.*?)"\]', html)
            if v_matches:
                videos.append(v_matches[0].replace("playwm", "play").replace("\\u002F", "/"))
            
            if not videos:
                uri_match = re.search(r'"play_addr"[^}]*?"uri":"(.*?)"', html)
                if uri_match:
                    videos.append(f"https://aweme.snssdk.com/aweme/v1/play/?video_id={uri_match.group(1)}&ratio=1080p&line=0")
            
            img_blocks = re.findall(r'"images":\[(.*?)\]', html)
            if img_blocks:
                for block in img_blocks:
                    urls = re.findall(r'"url_list":\["(.*?)"\]', block)
                    if urls: images.append(urls[-1].replace("\\u002F", "/"))
                        
            if not videos and not images:
                raise Exception("页面结构变动或遭遇滑块验证拦截")

        return title, {"videos": videos, "images": images, "live_videos": live_videos}

    # ========================== 得物 提取引擎（沿用 1.4 原装代码） ==========================
    def _extract_dewu(self, url, index):
        headers = {"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X)"}
        res = requests.get(url, headers=headers, proxies=self.proxies, timeout=15, verify=False)
        if "安全验证" in res.text: raise Exception("被得物风控拦截")
        
        match = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', res.text, re.DOTALL)
        if not match: raise Exception("未找到得物底层数据结构")
            
        data = json.loads(match.group(1))
        content = data["props"]["pageProps"]["metaOGInfo"]["data"][0]["content"]
        title = self._clean_folder_name(content.get("title", ""), "得物下载")
        
        videos, images = [], []
        if "video" in content and content["video"].get("url"):
            videos.append(content["video"]["url"])
            if content["video"].get("picUrl"): images.append(content["video"]["picUrl"])
        elif "media" in content and "list" in content["media"]:
            for item in content["media"]["list"]:
                if item.get("mediaType") == "video": videos.append(item.get("url"))
                else: images.append(item.get("url"))
                
        if videos: images = images[:1]
        return title, {"videos": videos, "images": images, "live_videos": []}

    def _submit_tasks(self, title, media, platform, index, save_path, folder_mode, live_option, video_option):
        if not self.is_running: return
        
        tasks = []
        l_vids = media.get("live_videos", [])
        
        if any(l_vids):
            if live_option in ["全部下载", "仅下图片"]:
                for i, img_url in enumerate(media["images"], 1):
                    name = str(i) if folder_mode else f"{title}_{i}"
                    tasks.append((img_url, name, save_path, "image")) 
            
            if live_option == "全部下载":
                live_path = os.path.join(save_path, "live短视频") if folder_mode else os.path.join(save_path, f"{title}_live短视频")
                for i, img_url in enumerate(media["images"], 1):
                    if i <= len(l_vids) and l_vids[i-1]:
                        name = str(i) if folder_mode else f"{title}_{i}"
                        tasks.append((l_vids[i-1], name, live_path, "video")) 
            
            elif live_option == "仅下视频":
                for i, img_url in enumerate(media["images"], 1):
                    if i <= len(l_vids) and l_vids[i-1]:
                        name = str(i) if folder_mode else f"{title}_{i}"
                        tasks.append((l_vids[i-1], name, save_path, "video"))
        
        elif media["videos"]:
            for i, v in enumerate(media["videos"], 1):
                if video_option in ["全部下载", "仅保存视频"]:
                    name = f"视频_{i}" if folder_mode and len(media["videos"])>1 else ("视频" if folder_mode else (f"{title}_{i}" if len(media["videos"])>1 else title))
                    tasks.append((v, name, save_path, "video"))
                    
            for i, img in enumerate(media["images"], 1):
                if video_option in ["全部下载", "仅下载封面"]:
                    name = f"视频封面_{i}" if folder_mode and len(media["images"])>1 else ("视频封面" if folder_mode else (f"{title}_{i}" if len(media["images"])>1 else title))
                    tasks.append((img, name, save_path, "image"))
        
        else:
            for i, img in enumerate(media["images"], 1):
                name = str(i) if folder_mode else f"{title}_{i}"
                tasks.append((img, name, save_path, "image"))
                
        if not tasks:
            self.log(f"\n📥 [{index}] <{title}> 被设置过滤，无需下载。")
            return
            
        for _, _, p, _ in tasks:
            if not os.path.exists(p): os.makedirs(p)
            
        self.log(f"\n📥 [{index}] 开始下载 <{title}> (共 {len(tasks)} 个文件)...")
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as ex:
            for u, n, p, t in tasks: ex.submit(self._download_media, u, n, p, t, platform)

    def _download_media(self, url, name, save_path, m_type, platform):
        if not self.is_running or not url: return
        if platform == "xhs":
            if m_type == "video":
                # XHS 视频：纯匿名裸请求，不带任何身份信息
                # 解析阶段已匿名 → masterUrl 是公共无水印版本
                # 下载阶段同样匿名 → CDN 不触发水印注入
                headers = {
                    "User-Agent":      "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
                    "Range":           "bytes=0-",
                    "Accept-Encoding": "identity",
                    "Connection":      "keep-alive",
                }
            else:
                # XHS 图片：带 Referer 防 403，不带 Cookie
                headers = {
                    "User-Agent":     "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
                    "Accept":         "image/webp,image/jpeg,image/png,*/*;q=0.8", # 💡 核心：明确要求 WebP/JPEG，拒绝 AVIF
                    "Referer":        "https://www.xiaohongshu.com/",
                    "Sec-Fetch-Dest": "image",
                    "Connection":     "keep-alive",
                }
        elif platform == "douyin":
            headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, Gecko) Chrome/124.0.0.0 Safari/537.36", "Connection": "keep-alive"}
            if m_type == "video":
                headers["Range"] = "bytes=0-"
                headers["Accept-Encoding"] = "identity"
        else:
            headers = {"User-Agent": "Mozilla/5.0", "Connection": "keep-alive"}
            if m_type == "video":
                headers["Range"] = "bytes=0-"
                headers["Accept-Encoding"] = "identity"
        
        last_error = ""
        allow_multi = True
        
        for attempt in range(3):
            if not self.is_running: break
            try:
                res = requests.get(url, headers=headers, proxies=self.proxies, stream=True, timeout=15, verify=False)
                if res.status_code not in [200, 206]:
                    last_error = f"服务器拒绝访问 (HTTP {res.status_code})"
                    time.sleep(1); continue
                    
                total = int(res.headers.get('content-length', 0))
                if res.status_code == 206:
                    cr = res.headers.get('Content-Range', '')
                    total = int(cr.split('/')[-1]) if '/' in cr else total
                
                ext = ".mp4" if m_type == "video" else ".jpg"
                final_file = os.path.join(save_path, f"{name}{ext}")

                if allow_multi and platform in ["xhs", "douyin"] and m_type == "video" and total > 1500000:
                    success = self._multi_thread_video(res.url, final_file, total, headers, name)
                    if success: 
                        self.log(f"   ✅ {name}{ext} (16核满载) 下载完毕！")
                        return
                    else:
                        allow_multi = False
                        raise Exception("CDN拒绝分片，准备降级为单线程拉取")
                else:
                    downloaded = 0
                    start_time = time.time()
                    last_print = 0
                    with open(final_file, 'wb') as f:
                        for chunk in res.iter_content(chunk_size=524288): 
                            if not self.is_running: break
                            f.write(chunk)
                            downloaded += len(chunk)
                            
                            if m_type == "video" and total > 0:
                                percent = int((downloaded / total) * 100)
                                if percent - last_print >= 15:
                                    elapsed = time.time() - start_time
                                    speed_mb = (downloaded / 1048576) / elapsed if elapsed > 0 else 0
                                    self.log(f"   ⚡ {name}{ext}: {percent}% ({speed_mb:.1f} MB/s)")
                                    last_print = percent
                                    
                    # 替换掉原有的 if m_type == "image" and (".webp" in... 的那一段
                    if m_type == "image":
                        try:
                            # 此时 Pillow 已经认识 HEIC 了，可以顺利 open
                            img = Image.open(final_file)
                            if img.format != "JPEG":
                                if img.mode != "RGB": 
                                    img = img.convert("RGB")
                                img.save(final_file, "JPEG", quality=95)
                        except Exception as e:
                            self.log(f"   ⚠️ 图片转码警告 {name}: {str(e)}")
                    if downloaded == 0:
                        raise Exception("服务器返回了0字节无效文件")
                        
                    self.log(f"   ✅ {name}{ext} 下载完毕！")
                    return 
            except Exception as e:
                last_error = str(e)
                time.sleep(1.5)
                
        if self.is_running:
            self.log(f"❌ '{name}' 获取断流失败: {last_error}")

    def _multi_thread_video(self, url, final_path, total, headers, file_name):
        num = 16
        part = total // num
        downloaded_size = 0
        last_print_percent = 0
        start_time = time.time()
        progress_lock = threading.Lock()

        def dl_part(i):
            nonlocal downloaded_size, last_print_percent
            if not self.is_running: return False
            h = headers.copy()
            start_b = i * part
            end_b = start_b + part - 1 if i < num - 1 else total - 1
            h["Range"] = f"bytes={start_b}-{end_b}"
            
            for _ in range(3):
                try:
                    r = requests.get(url, headers=h, proxies=self.proxies, stream=True, timeout=15, verify=False)
                    if i > 0 and r.status_code == 200: return False
                    if r.status_code not in [200, 206]: time.sleep(1); continue
                    with open(final_path + f".part{i}", "wb") as f: 
                        for chunk in r.iter_content(chunk_size=131072): 
                            if not self.is_running: return False
                            if chunk:
                                f.write(chunk)
                                with progress_lock:
                                    downloaded_size += len(chunk)
                                    percent = int((downloaded_size / total) * 100)
                                    if percent - last_print_percent >= 10: 
                                        elapsed = time.time() - start_time
                                        speed_mb = (downloaded_size / 1048576) / elapsed if elapsed > 0 else 0
                                        self.log(f"   🚀 {file_name}.mp4: {percent}% ({speed_mb:.1f} MB/s)")
                                        last_print_percent = percent
                    return True
                except: time.sleep(1)
            return False
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=num) as ex:
            results = list(ex.map(dl_part, range(num)))
            
        if self.is_running and all(results):
            with open(final_path, 'wb') as outfile:
                for i in range(num):
                    p = final_path + f".part{i}"
                    if os.path.exists(p):
                        with open(p, 'rb') as infile: shutil.copyfileobj(infile, outfile)
                        os.remove(p)
            return True
        else:
            for i in range(num):
                p = final_path + f".part{i}"
                if os.path.exists(p): os.remove(p)
            return False

if __name__ == "__main__":
    app = DualPlatformDownloader()
    app.mainloop()
