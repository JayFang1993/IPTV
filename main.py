import os
import socket
import subprocess
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse

import m3u8

import re
import requests
import logging
from collections import OrderedDict
from datetime import datetime
import config

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s', handlers=[logging.FileHandler("function.log", "w", encoding="utf-8"), logging.StreamHandler()])

def parse_template(template_file):
    template_channels = OrderedDict()
    current_category = None

    with open(template_file, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                if "#genre#" in line:
                    current_category = line.split(",")[0].strip()
                    template_channels[current_category] = []
                elif current_category:
                    channel_name = line.split(",")[0].strip()
                    template_channels[current_category].append(channel_name)

    return template_channels

def fetch_channels(url):
    channels = OrderedDict()

    try:
        response = requests.get(url)
        response.raise_for_status()
        response.encoding = 'utf-8'
        lines = response.text.split("\n")
        current_category = None
        is_m3u = any("#EXTINF" in line for line in lines[:15])
        source_type = "m3u" if is_m3u else "txt"
        logging.info(f"url: {url} 获取成功，判断为{source_type}格式")

        if is_m3u:
            for line in lines:
                line = line.strip()
                if line.startswith("#EXTINF"):
                    match = re.search(r'group-title="(.*?)",(.*)', line)
                    if match:
                        current_category = match.group(1).strip()
                        channel_name = match.group(2).strip()
                        if current_category not in channels:
                            channels[current_category] = []
                elif line and not line.startswith("#"):
                    channel_url = line.strip()
                    if current_category and channel_name:
                        channels[current_category].append((channel_name, channel_url))
        else:
            for line in lines:
                line = line.strip()
                if "#genre#" in line:
                    current_category = line.split(",")[0].strip()
                    channels[current_category] = []
                elif current_category:
                    match = re.match(r"^(.*?),(.*?)$", line)
                    if match:
                        channel_name = match.group(1).strip()
                        channel_url = match.group(2).strip()
                        channels[current_category].append((channel_name, channel_url))
                    elif line:
                        channels[current_category].append((line, ''))
        if channels:
            categories = ", ".join(channels.keys())
            logging.info(f"url: {url} 爬取成功✅，包含频道分类: {categories}")
    except requests.RequestException as e:
        logging.error(f"url: {url} 爬取失败❌, Error: {e}")

    return channels

def match_channels(template_channels, all_channels):
    matched_channels = OrderedDict()

    host_pings = {}
    for category, channel_list in template_channels.items():
        matched_channels[category] = OrderedDict()
        for channel_name in channel_list:
            logging.info(f"url: 检查频道: {channel_name}")
            livednow_urls = []
            other_urls = []
            for online_category, online_channel_list in all_channels.items():
                for online_channel_name, online_channel_url in online_channel_list:
                    if channel_name == online_channel_name:
                        logging.info(f"url: 检查: {online_channel_url}")
                        # Check if the host domain of online_channel_url can be pinged
                        # if "api.livednow.org" in online_channel_url:
                            # livednow_urls.append(online_channel_url)
                        if "chinamobile.com" in online_channel_url:
                            other_urls.append(online_channel_url)
            if livednow_urls or other_urls:
               matched_channels[category].setdefault(channel_name, []).extend(livednow_urls + other_urls)
    return matched_channels

def filter_source_urls(template_file):
    template_channels = parse_template(template_file)
    source_urls = config.source_urls

    all_channels = OrderedDict()
    for url in source_urls:
        fetched_channels = fetch_channels(url)
        for category, channel_list in fetched_channels.items():
            if category in all_channels:
                all_channels[category].extend(channel_list)
            else:
                all_channels[category] = channel_list

    matched_channels = match_channels(template_channels, all_channels)

    return matched_channels, template_channels

def is_ipv6(url):
    return re.match(r'^http:\/\/\[[0-9a-fA-F:]+\]', url) is not None

def updateChannelUrlsM3U(channels, template_channels):
    written_urls = set()

    current_date = datetime.now().strftime("%Y-%m-%d")
    for group in config.announcements:
        for announcement in group['entries']:
            if announcement['name'] is None:
                announcement['name'] = current_date

    with open("live.m3u", "w", encoding="utf-8") as f_m3u:
        f_m3u.write(f"""#EXTM3U x-tvg-url={",".join(f'"{epg_url}"' for epg_url in config.epg_urls)}\n""")

        with open("live.txt", "w", encoding="utf-8") as f_txt:
            for group in config.announcements:
                f_txt.write(f"{group['channel']},#genre#\n")
                for announcement in group['entries']:
                    f_m3u.write(f"""#EXTINF:-1 tvg-id="1" tvg-name="{announcement['name']}" tvg-logo="{announcement['logo']}" group-title="{group['channel']}",{announcement['name']}\n""")
                    f_m3u.write(f"{announcement['url']}\n")
                    f_txt.write(f"{announcement['name']},{announcement['url']}\n")

            for category, channel_list in template_channels.items():
                f_txt.write(f"{category},#genre#\n")
                if category in channels:
                    for channel_name in channel_list:
                        if channel_name in channels[category]:
                            sorted_urls = sorted(channels[category][channel_name], key=lambda url: not is_ipv6(url) if config.ip_version_priority == "ipv6" else is_ipv6(url))
                            filtered_urls = []
                            for url in sorted_urls:
                                if url and url not in written_urls and not any(blacklist in url for blacklist in config.url_blacklist):
                                    filtered_urls.append(url)
                                    written_urls.add(url)

                            total_urls = len(filtered_urls)
                            for index, url in enumerate(filtered_urls, start=1):
                                if is_ipv6(url):
                                    # url_suffix = f"$LR•IPV6" if total_urls == 1 else f"$LR•IPV6『线路{index}』"
                                    continue
                                else:
                                    url_suffix = f"$" if total_urls == 1 else f"$线路{index}"
                                if '$' in url:
                                    base_url = url.split('$', 1)[0]
                                else:
                                    base_url = url

                                new_url = f"{base_url}{url_suffix}"

                                f_m3u.write(f"#EXTINF:-1 tvg-id=\"{index}\" tvg-name=\"{channel_name}\" tvg-logo=\"https://gcore.jsdelivr.net/gh/yuanzl77/TVlogo@master/png/{channel_name}.png\" group-title=\"{category}\",{channel_name}\n")
                                f_m3u.write(new_url + "\n")
                                f_txt.write(f"{channel_name},{new_url}\n")

            f_txt.write("\n")

def check_stream(url, timeout=10):
    """
    检查单个 IPTV 流地址是否有效

    参数:
        url: IPTV 流地址
        timeout: 超时时间(秒)

    返回:
        (bool, str): (是否有效, 详细信息)
    """
    try:
        # 检查 URL 格式
        parsed = urlparse(url)
        if not parsed.scheme or not parsed.netloc:
            return False, "无效的 URL 格式"

        # 检查域名是否可解析
        # try:
        #     socket.gethostbyname(parsed.netloc)
        # except socket.gaierror:
        #     return False, "域名无法解析"

        # 对于 m3u8 流
        if url.lower().endswith('m3u8'):
            try:
                response = requests.get(url, timeout=timeout)
                if response.status_code != 200:
                    return False, f"HTTP 状态码: {response.status_code}"

                m3u8_obj = m3u8.loads(response.text)
                if not m3u8_obj.segments:
                    return False, "M3U8 文件无有效片段"

                # 检查第一个片段是否可访问
                first_segment = m3u8_obj.segments[0]
                if not first_segment.uri.startswith('http'):
                    base_url = url.rsplit('/', 1)[0]
                    segment_url = f"{base_url}/{first_segment.uri}"
                else:
                    segment_url = first_segment.uri

                segment_response = requests.head(segment_url, timeout=timeout)
                if segment_response.status_code != 200:
                    return False, "无法访问媒体片段"

            except Exception as e:
                return False, f"M3U8 解析错误: {str(e)}"

        # 对于其他类型的流 (比如 .ts, .flv 等)
        else:
            try:
                # 使用 ffprobe 检查媒体信息
                command = [
                    'ffprobe',
                    '-v', 'quiet',
                    '-print_format', 'json',
                    '-show_format',
                    '-show_streams',
                    '-i', url
                ]

                process = subprocess.Popen(
                    command,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE
                )

                process.communicate(timeout=timeout)

                if process.returncode != 0:
                    return False, "媒体流无法解析"

            except subprocess.TimeoutExpired:
                return False, "连接超时"
            except Exception as e:
                return False, f"流媒体检查错误: {str(e)}"

        return True, "源有效"

    except Exception as e:
        return False, f"检查过程发生错误: {str(e)}"



if __name__ == "__main__":
    template_file = "demo.txt"
    channels, template_channels = filter_source_urls(template_file)
    updateChannelUrlsM3U(channels, template_channels)
