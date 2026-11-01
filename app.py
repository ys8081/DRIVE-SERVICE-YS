"""
Drive Manager - single file (backend + UI) | token.json / env variable se Drive access

Features:
  - Startup password (default 808177, env APP_PASSWORD se badal sakte ho)
  - Startup folder (default: tumhara folder, env START_FOLDER_ID se badal sakte ho)
  - Multi-select + folder download (server par streaming ZIP)
  - Open file (Drive viewer), rename, trash, upload, search
  - Android + desktop dono ke liye native-style UI, light / dark theme

Local chalane ke liye:   python app.py
Render start command:    gunicorn app:app --timeout 300 --workers 1 --threads 16
"""
import hashlib
import hmac
import io
import json
import mimetypes
import os
import re
import threading
import time
import uuid
import zipfile
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from urllib.parse import quote

from flask import Flask, Response, jsonify, request, session
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload

SCOPES = ["https://www.googleapis.com/auth/drive"]
TOKEN_FILE = "token.json"
FOLDER_MIME = "application/vnd.google-apps.folder"

APP_PASSWORD = os.environ.get("APP_PASSWORD", "808177")
START_FOLDER = os.environ.get("START_FOLDER_ID", "1eCXD2AA3sxCW4UKeZjbsEkRmFazrKQFy")

EXPORTS = {
    "application/vnd.google-apps.document": ("application/pdf", ".pdf"),
    "application/vnd.google-apps.spreadsheet": (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".xlsx",
    ),
    "application/vnd.google-apps.presentation": ("application/pdf", ".pdf"),
    "application/vnd.google-apps.drawing": ("application/pdf", ".pdf"),
}

INDEX_HTML = r'''<!DOCTYPE html>
<html lang="en" data-theme="light">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#ffffff">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-capable" content="yes">
<title>Drive Manager</title>
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>☁️</text></svg>">
<style>
:root{
  --bg:#f4f5fb; --surface:#ffffff; --text:#1b1f2e; --muted:#6c7389; --line:#e4e7f1;
  --primary:#4f6bff; --primary-soft:#e9ecff; --danger:#e5484d; --danger-soft:#fdeceb;
  --shadow:0 1px 2px rgba(20,28,60,.06),0 8px 24px rgba(20,28,60,.08);
  --radius:16px;
}
[data-theme="dark"]{
  --bg:#0d0f16; --surface:#171a24; --text:#e9ebf4; --muted:#8e95ab; --line:#262b3a;
  --primary:#7b8fff; --primary-soft:#242b52; --danger:#ff6369; --danger-soft:#3a1d20;
  --shadow:0 1px 2px rgba(0,0,0,.45),0 8px 24px rgba(0,0,0,.4);
}
*{box-sizing:border-box;margin:0;padding:0;-webkit-tap-highlight-color:transparent}
html,body{height:100%}
body{font-family:Inter,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;background:var(--bg);color:var(--text);font-size:14px;display:flex;flex-direction:column;height:100dvh;overflow:hidden;-webkit-font-smoothing:antialiased;overscroll-behavior:none}
button{font:inherit;color:inherit;cursor:pointer;border:none;background:none;touch-action:manipulation}
button:disabled{opacity:.55;cursor:default}
input{font:inherit;color:inherit}
.i{width:20px;height:20px;fill:none;stroke:currentColor;stroke-width:2;stroke-linecap:round;stroke-linejoin:round;flex-shrink:0;display:block}

/* Header */
header{display:flex;align-items:center;gap:14px;padding:10px 22px;padding-top:calc(10px + env(safe-area-inset-top));background:var(--surface);border-bottom:1px solid var(--line);z-index:20;flex-shrink:0}
.logo{display:flex;align-items:center;gap:10px;font-weight:700;font-size:17px;letter-spacing:-.2px;min-width:190px}
.logo-mark{width:36px;height:36px;border-radius:11px;background:linear-gradient(135deg,#4f6bff,#8a5cff);display:grid;place-items:center;color:#fff}
.search{flex:1;max-width:620px;position:relative}
.search input{width:100%;height:44px;padding:0 16px 0 46px;border-radius:22px;border:1px solid transparent;background:var(--bg);outline:none;transition:.15s;font-size:15px}
.search input:focus{border-color:var(--primary);background:var(--surface);box-shadow:0 0 0 3px var(--primary-soft)}
.search .i{position:absolute;left:15px;top:50%;transform:translateY(-50%);color:var(--muted)}
.hdr-right{display:flex;align-items:center;gap:4px;margin-left:auto}
.icon-btn{width:44px;height:44px;border-radius:50%;display:grid;place-items:center;color:var(--muted);transition:.15s}
.icon-btn:hover,.icon-btn:active{background:var(--primary-soft);color:var(--primary)}
.avatar{width:36px;height:36px;border-radius:50%;background:linear-gradient(135deg,#ff9a5c,#ff5c8a);color:#fff;display:grid;place-items:center;font-weight:600;margin-left:6px}

/* Layout */
.layout{flex:1;display:flex;min-height:0}
aside{width:250px;padding:18px 14px;background:var(--surface);border-right:1px solid var(--line);display:flex;flex-direction:column;gap:6px;flex-shrink:0}
.btn{display:inline-flex;align-items:center;justify-content:center;gap:8px;height:42px;padding:0 18px;border-radius:21px;font-weight:600;transition:.15s;white-space:nowrap}
.btn-primary{background:var(--primary);color:#fff;box-shadow:0 4px 14px color-mix(in srgb,var(--primary) 35%,transparent)}
.btn-primary:hover{filter:brightness(1.08)}
.btn-ghost{background:var(--surface);border:1px solid var(--line)}
.btn-ghost:hover{border-color:var(--primary);color:var(--primary)}
.btn-danger{background:var(--danger);color:#fff}
.side-btn{width:100%;height:48px;border-radius:16px;margin-bottom:6px}
.nav{display:flex;align-items:center;gap:12px;height:44px;padding:0 14px;border-radius:22px;color:var(--muted);font-weight:600;width:100%;text-align:left}
.nav.active{background:var(--primary-soft);color:var(--primary)}
.nav:hover{background:var(--primary-soft)}
.nav span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.storage{margin-top:auto;padding:14px;border-radius:16px;background:var(--bg)}
.storage .t{font-weight:600;margin-bottom:8px;display:flex;justify-content:space-between}
.bar{height:7px;border-radius:99px;background:var(--line);overflow:hidden}
.bar>i{display:block;height:100%;width:0;background:linear-gradient(90deg,#4f6bff,#8a5cff);border-radius:99px;transition:width .6s}
.storage small{display:block;margin-top:8px;color:var(--muted);font-size:12px}

main{flex:1;overflow:auto;padding:20px 26px 120px;position:relative;overscroll-behavior:contain}
.toolbar{display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:16px}
.crumbs{display:flex;align-items:center;gap:2px;font-size:20px;font-weight:700;letter-spacing:-.3px;flex:1;min-width:200px;overflow-x:auto;white-space:nowrap;scrollbar-width:none}
.crumbs::-webkit-scrollbar{display:none}
.crumbs button{padding:6px 10px;border-radius:10px;color:var(--muted);font-weight:600;flex-shrink:0;font-size:inherit}
.crumbs button:hover{background:var(--primary-soft);color:var(--primary)}
.crumbs button:last-child{color:var(--text)}
.crumbs .sep{color:var(--muted);opacity:.5}
.tb-actions{display:flex;gap:8px;align-items:center}
.seg{display:flex;background:var(--surface);border:1px solid var(--line);border-radius:22px;padding:3px}
.seg button{width:38px;height:34px;border-radius:19px;display:grid;place-items:center;color:var(--muted)}
.seg button.active{background:var(--primary-soft);color:var(--primary)}

/* Items */
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(176px,1fr));gap:14px}
.item{position:relative;background:var(--surface);border:1px solid var(--line);border-radius:var(--radius);cursor:pointer;transition:.18s;overflow:hidden;animation:pop .25s ease both;user-select:none;-webkit-user-select:none;-webkit-touch-callout:none}
.item:hover{box-shadow:var(--shadow);border-color:color-mix(in srgb,var(--primary) 50%,var(--line))}
.item.sel{background:var(--primary-soft);border-color:var(--primary)}
@keyframes pop{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:none}}
@keyframes slide{from{transform:translateY(100%)}to{transform:none}}
.thumb{height:104px;display:grid;place-items:center;background:var(--bg);margin:6px 6px 0;border-radius:12px}
.grid .item.folder .thumb{background:var(--primary-soft)}
.item.folder .thumb{color:var(--primary)}
.thumb .fi{width:46px;height:58px;filter:drop-shadow(0 6px 8px rgba(0,0,0,.18))}
.thumb .fold{width:64px;height:64px}
.info{padding:11px 14px 13px}
.name{font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.meta{color:var(--muted);font-size:12px;margin-top:3px;display:flex;gap:6px}
.m-size:not(:empty)::before{content:"·";margin-right:6px}
.chk{position:absolute;top:12px;left:12px;width:24px;height:24px;border-radius:50%;border:2px solid var(--muted);background:var(--surface);color:transparent;display:grid;place-items:center;opacity:0;transition:.15s;z-index:2}
.chk .i{width:14px;height:14px;stroke-width:3.2}
.item:hover .chk,body.selmode .chk{opacity:1}
.item.sel .chk{opacity:1;background:var(--primary);border-color:var(--primary);color:#fff}
.mbtn{position:absolute;top:10px;right:10px;width:34px;height:34px;border-radius:50%;display:grid;place-items:center;color:var(--muted);opacity:0;background:var(--surface);border:1px solid var(--line);transition:.15s;z-index:2}
.mbtn .i{width:18px;height:18px}
.item:hover .mbtn{opacity:1}
.mbtn:hover{color:var(--primary);border-color:var(--primary)}
@media (hover:none){.mbtn{opacity:1}}

/* List view */
.list{display:flex;flex-direction:column;gap:6px}
.list .item{display:grid;grid-template-columns:40px 40px minmax(0,1fr) 130px 80px 44px;align-items:center;min-height:56px;border-radius:14px;padding:0 4px}
.list .chk{position:static;margin:0 auto}
.list .thumb{height:auto;width:40px;background:none;margin:0;display:grid;place-items:center}
.list .thumb .fi{width:24px;height:30px;filter:none}
.list .thumb .fold{width:30px;height:30px}
.list .info,.list .meta{display:contents}
.list .name{padding:0 10px}
.list .m-date,.list .m-size{color:var(--muted);font-size:12.5px;padding:0 6px;margin:0}
.list .m-size::before{content:none}
.list .mbtn{position:static;opacity:1;background:none;border:none;margin:0 auto}
.list-head{display:grid;grid-template-columns:40px 40px minmax(0,1fr) 130px 80px 44px;padding:0 4px 8px;color:var(--muted);font-size:12px;font-weight:600;text-transform:uppercase;letter-spacing:.5px}
.list-head span:nth-child(n+4){padding:0 6px}
.list-head span:nth-child(3){padding:0 10px}

/* States */
.state{display:flex;flex-direction:column;align-items:center;justify-content:center;gap:10px;padding:70px 20px;color:var(--muted);text-align:center}
.state h3{color:var(--text);font-size:18px}
.state .i{width:64px;height:64px;stroke-width:1.4;opacity:.6}
.spinner{width:34px;height:34px;border-radius:50%;border:3px solid var(--line);border-top-color:var(--primary);animation:spin .8s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}
.more{display:flex;justify-content:center;margin-top:22px}

/* Selection bar */
.selbar{position:fixed;left:50%;bottom:calc(22px + env(safe-area-inset-bottom));transform:translateX(-50%);display:none;align-items:center;gap:4px;background:var(--surface);border:1px solid var(--line);border-radius:30px;padding:6px 8px;box-shadow:0 10px 40px rgba(0,0,0,.25);z-index:35;animation:pop .2s}
.selbar.show{display:flex}
.selbar b{padding:0 12px 0 4px;font-size:15px;white-space:nowrap}
.selbar .sb{height:42px;min-width:42px;padding:0 12px;border-radius:21px;display:inline-flex;align-items:center;justify-content:center;gap:8px;font-weight:600;color:var(--muted)}
.selbar .sb:hover{background:var(--primary-soft);color:var(--primary)}
.selbar .sb.pri{background:var(--primary);color:#fff}
.selbar .sb.pri:hover{filter:brightness(1.08)}
.selbar .sb.dng:hover{background:var(--danger-soft);color:var(--danger)}
.selbar .sp{flex:1;min-width:6px}

/* FAB (mobile) */
.fab-wrap{position:fixed;right:16px;bottom:calc(16px + env(safe-area-inset-bottom));z-index:30;display:none;flex-direction:column;align-items:flex-end;gap:12px}
.fab{width:60px;height:60px;border-radius:20px;background:var(--primary);color:#fff;display:grid;place-items:center;box-shadow:0 8px 24px color-mix(in srgb,var(--primary) 45%,transparent);transition:.2s}
.fab .i{width:26px;height:26px;transition:.2s;stroke-width:2.4}
.fab-wrap.open .fab .i{transform:rotate(45deg)}
.fab-mini{display:none;align-items:center;gap:10px;height:48px;padding:0 18px;border-radius:16px;background:var(--surface);border:1px solid var(--line);box-shadow:var(--shadow);font-weight:600;color:var(--primary)}
.fab-wrap.open .fab-mini{display:flex;animation:pop .18s}

/* Menu / bottom sheet */
.scrim{position:fixed;inset:0;z-index:75;display:none}
.scrim.show{display:block}
.menu{position:fixed;z-index:80;background:var(--surface);border:1px solid var(--line);border-radius:16px;box-shadow:0 12px 40px rgba(0,0,0,.3);padding:6px;width:228px;display:none}
.menu.show{display:block;animation:pop .15s}
.menu .mh{display:none;padding:10px 12px 12px;font-weight:700;font-size:15px;border-bottom:1px solid var(--line);margin-bottom:6px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.menu button{display:flex;align-items:center;gap:14px;width:100%;padding:11px 12px;border-radius:11px;font-weight:500;text-align:left}
.menu button:hover,.menu button:active{background:var(--primary-soft)}
.menu button.dng{color:var(--danger)}
.menu button.dng:hover{background:var(--danger-soft)}
.menu button .i{color:var(--muted)}
.menu button.dng .i{color:var(--danger)}

/* Drop overlay */
.drop{position:fixed;inset:0;background:color-mix(in srgb,var(--primary) 14%,transparent);backdrop-filter:blur(3px);z-index:50;display:none;align-items:center;justify-content:center;pointer-events:none}
.drop.show{display:flex}
.drop div{border:3px dashed var(--primary);border-radius:24px;padding:60px 80px;background:var(--surface);text-align:center;box-shadow:var(--shadow);display:flex;flex-direction:column;align-items:center}
.drop .i{width:56px;height:56px;color:var(--primary)}
.drop h2{margin-top:10px;color:var(--primary)}

/* Upload panel */
.uploads{position:fixed;right:24px;bottom:24px;width:360px;max-width:calc(100vw - 32px);background:var(--surface);border:1px solid var(--line);border-radius:18px;box-shadow:var(--shadow);z-index:40;display:none;overflow:hidden}
.uploads.show{display:block;animation:pop .25s}
.up-head{display:flex;align-items:center;justify-content:space-between;padding:8px 8px 8px 16px;background:var(--primary);color:#fff;font-weight:600}
.up-head button{color:#fff;opacity:.9;width:36px;height:36px;border-radius:50%;display:grid;place-items:center}
.up-head button:hover{background:rgba(255,255,255,.18)}
.up-list{max-height:260px;overflow:auto}
.up-row{padding:12px 16px;border-bottom:1px solid var(--line)}
.up-row:last-child{border:none}
.up-top{display:flex;justify-content:space-between;gap:10px;margin-bottom:7px}
.up-top b{font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.up-top span{color:var(--muted);font-size:12px;flex-shrink:0}
.up-row.done .bar>i{background:#2fb344}
.up-row.err .bar>i{background:var(--danger)}
.up-row.err .up-top span{color:var(--danger)}

/* ZIP progress panel */
.zippanel{position:fixed;left:50%;bottom:calc(28px + env(safe-area-inset-bottom));transform:translateX(-50%);width:340px;max-width:calc(100vw - 32px);background:var(--surface);border:1px solid var(--line);border-radius:20px;box-shadow:0 12px 40px rgba(0,0,0,.28);z-index:45;display:none;overflow:hidden}
.zippanel.show{display:block;animation:pop .25s}
.zip-head{display:flex;align-items:center;gap:10px;padding:12px 14px 10px;background:linear-gradient(135deg,#4f6bff,#8a5cff);color:#fff}
.zip-head svg{flex-shrink:0}
.zip-head b{flex:1;font-size:14px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.zip-head button{color:#fff;opacity:.85;width:32px;height:32px;border-radius:50%;display:grid;place-items:center;flex-shrink:0}
.zip-head button:hover{background:rgba(255,255,255,.2)}
.zip-body{padding:14px 16px 16px}
.zip-file{font-size:12px;color:var(--muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin-bottom:8px;min-height:16px}
.zip-barwrap{height:8px;border-radius:99px;background:var(--line);overflow:hidden}
.zip-fill{height:100%;width:0%;background:linear-gradient(90deg,#4f6bff,#8a5cff);border-radius:99px;transition:width .3s ease}
.zip-foot{display:flex;justify-content:space-between;align-items:center;margin-top:8px}
.zip-pct{font-weight:700;font-size:13px;color:var(--primary)}
.zip-count{font-size:12px;color:var(--muted)}

/* Modal */
.modal-bg{position:fixed;inset:0;background:rgba(10,12,22,.5);backdrop-filter:blur(3px);display:none;align-items:center;justify-content:center;z-index:90;padding:16px}
.modal-bg.show{display:flex}
.modal{background:var(--surface);border-radius:26px;padding:24px;width:420px;max-width:100%;box-shadow:0 20px 60px rgba(0,0,0,.35);animation:pop .2s}
.modal h3{font-size:19px;margin-bottom:8px}
.modal p{color:var(--muted);margin-bottom:14px;line-height:1.5;word-break:break-word}
.modal input{width:100%;height:46px;padding:0 14px;border-radius:14px;border:1px solid var(--line);background:var(--bg);outline:none;margin-bottom:6px;font-size:15px}
.modal input:focus{border-color:var(--primary);box-shadow:0 0 0 3px var(--primary-soft)}
.modal .row{display:flex;justify-content:flex-end;gap:10px;margin-top:16px}

/* Toasts */
.toasts{position:fixed;left:50%;top:calc(76px + env(safe-area-inset-top));transform:translateX(-50%);display:flex;flex-direction:column;gap:8px;z-index:120;align-items:center;pointer-events:none;width:max-content;max-width:calc(100vw - 32px)}
.toast{background:#1b1f2e;color:#fff;padding:12px 20px;border-radius:16px;box-shadow:var(--shadow);animation:pop .25s;font-weight:500;text-align:center}
.toast.err{background:var(--danger)}

/* Lock screen */
.lock{position:fixed;inset:0;z-index:110;display:none;align-items:center;justify-content:center;padding:20px;background:radial-gradient(1200px 600px at 20% -10%,color-mix(in srgb,var(--primary) 22%,var(--bg)),var(--bg))}
.lock.show{display:flex}
.lock-card{width:360px;max-width:100%;background:var(--surface);border:1px solid var(--line);border-radius:28px;padding:34px 26px 26px;box-shadow:var(--shadow);text-align:center}
.lock-card.shake{animation:shake .4s}
@keyframes shake{20%,60%{transform:translateX(-9px)}40%,80%{transform:translateX(9px)}}
.lock-mark{width:68px;height:68px;border-radius:22px;background:linear-gradient(135deg,#4f6bff,#8a5cff);display:grid;place-items:center;color:#fff;margin:0 auto 16px}
.lock-mark .i{width:32px;height:32px}
.lock h2{font-size:22px;letter-spacing:-.3px}
.lock p{color:var(--muted);margin:6px 0 20px}
.lock input{width:100%;height:54px;border-radius:16px;border:1px solid var(--line);background:var(--bg);text-align:center;font-size:24px;letter-spacing:8px;outline:none;margin-bottom:10px}
.lock input:focus{border-color:var(--primary);box-shadow:0 0 0 3px var(--primary-soft)}
.lock .btn{width:100%;height:50px;border-radius:16px}
.lock .err{color:var(--danger);min-height:20px;margin-bottom:10px;font-weight:500}

/* Desktop only / mobile only */
.fab-wrap{display:none}

@media (max-width:860px){
  aside{display:none}
  header{padding:8px 12px;padding-top:calc(8px + env(safe-area-inset-top));gap:6px}
  .logo span{display:none}.logo{min-width:0}
  .avatar{display:none}
  .search input{height:42px}
  main{padding:14px 12px 130px}
  .crumbs{font-size:18px;min-width:0}
  .desk-only{display:none!important}
  .fab-wrap{display:flex}
  body.selmode .fab-wrap{display:none}
  .grid{grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:12px}
  .list .item{grid-template-columns:40px 40px minmax(0,1fr) 70px 44px}
  .list .m-date,.list-head span:nth-child(4){display:none}
  .list-head{grid-template-columns:40px 40px minmax(0,1fr) 70px 44px}
  .selbar{left:10px;right:10px;transform:none;bottom:calc(12px + env(safe-area-inset-bottom))}
  .selbar b{flex:0}
  .selbar .sb .lbl{display:none}
  .selbar .sb.pri .lbl{display:inline}
  .uploads{right:12px;left:12px;width:auto;bottom:calc(92px + env(safe-area-inset-bottom))}
  .scrim.show{background:rgba(10,12,22,.45)}
  .menu{left:0!important;right:0;bottom:0;top:auto!important;width:auto;border-radius:26px 26px 0 0;padding:8px 12px calc(14px + env(safe-area-inset-bottom));animation:slide .22s ease}
  .menu.show{animation:slide .22s ease}
  .menu .mh{display:block}
  .menu button{padding:15px 12px;font-size:15px}
  .toasts{top:calc(64px + env(safe-area-inset-top))}
}
</style>
</head>
<body>

<svg width="0" height="0" style="position:absolute" aria-hidden="true"><defs>
  <symbol id="i-cloud" viewBox="0 0 24 24"><path d="M17.5 19a4.5 4.5 0 1 0-1.4-8.8A6 6 0 1 0 5 14.5 4 4 0 0 0 6 19z"/></symbol>
  <symbol id="i-search" viewBox="0 0 24 24"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></symbol>
  <symbol id="i-upload" viewBox="0 0 24 24"><path d="M12 16V4m0 0-4 4m4-4 4 4M4 20h16"/></symbol>
  <symbol id="i-download" viewBox="0 0 24 24"><path d="M12 4v12m0 0-4-4m4 4 4-4M4 20h16"/></symbol>
  <symbol id="i-zip" viewBox="0 0 24 24"><path d="M4 7l1.5-3h13L20 7v12a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1zM4 7h16M10 11h4"/></symbol>
  <symbol id="i-folder" viewBox="0 0 24 24"><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/></symbol>
  <symbol id="i-folder-plus" viewBox="0 0 24 24"><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><path d="M12 11v5M9.5 13.5h5"/></symbol>
  <symbol id="i-home" viewBox="0 0 24 24"><path d="m3 11 9-8 9 8v9a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1z"/></symbol>
  <symbol id="i-refresh" viewBox="0 0 24 24"><path d="M21 12a9 9 0 1 1-3-6.7L21 8"/><path d="M21 3v5h-5"/></symbol>
  <symbol id="i-sun" viewBox="0 0 24 24"><circle cx="12" cy="12" r="4"/><path d="M12 2v2m0 16v2M4.9 4.9l1.4 1.4m11.4 11.4 1.4 1.4M2 12h2m16 0h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></symbol>
  <symbol id="i-moon" viewBox="0 0 24 24"><path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/></symbol>
  <symbol id="i-grid" viewBox="0 0 24 24"><rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/></symbol>
  <symbol id="i-list" viewBox="0 0 24 24"><path d="M4 6h16M4 12h16M4 18h16"/></symbol>
  <symbol id="i-edit" viewBox="0 0 24 24"><path d="M12 20h9M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z"/></symbol>
  <symbol id="i-trash" viewBox="0 0 24 24"><path d="M3 6h18M8 6V4h8v2m-9 0 1 14h8l1-14M10 11v6M14 11v6"/></symbol>
  <symbol id="i-open" viewBox="0 0 24 24"><path d="M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/></symbol>
  <symbol id="i-check" viewBox="0 0 24 24"><path d="m5 12.5 4.5 4.5L19 7.5"/></symbol>
  <symbol id="i-close" viewBox="0 0 24 24"><path d="M6 6l12 12M18 6 6 18"/></symbol>
  <symbol id="i-plus" viewBox="0 0 24 24"><path d="M12 5v14M5 12h14"/></symbol>
  <symbol id="i-lock" viewBox="0 0 24 24"><rect x="4" y="10.5" width="16" height="10" rx="2.5"/><path d="M8 10.5V7.5a4 4 0 0 1 8 0v3"/></symbol>
  <symbol id="i-logout" viewBox="0 0 24 24"><path d="M10 4H6a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h4M15 8l4 4-4 4M19 12H9"/></symbol>
  <symbol id="i-more" viewBox="0 0 24 24"><circle cx="12" cy="5" r="1.7" fill="currentColor" stroke="none"/><circle cx="12" cy="12" r="1.7" fill="currentColor" stroke="none"/><circle cx="12" cy="19" r="1.7" fill="currentColor" stroke="none"/></symbol>
  <symbol id="i-selall" viewBox="0 0 24 24"><rect x="4" y="4" width="16" height="16" rx="3.5"/><path d="m8.5 12 2.5 2.5 5-5"/></symbol>
  <symbol id="i-select" viewBox="0 0 24 24"><circle cx="12" cy="12" r="8.5"/><path d="m8.5 12 2.5 2.5 4.5-5"/></symbol>
</defs></svg>

<header>
  <div class="logo">
    <div class="logo-mark"><svg class="i"><use href="#i-cloud"/></svg></div>
    <span>Drive Manager</span>
  </div>
  <div class="search">
    <svg class="i"><use href="#i-search"/></svg>
    <input id="search" type="search" placeholder="Is folder mein search karo…" autocomplete="off">
  </div>
  <div class="hdr-right">
    <button class="icon-btn" id="themeBtn" title="Dark / Light mode" aria-label="Theme"></button>
    <button class="icon-btn" id="refreshBtn" title="Refresh" aria-label="Refresh"><svg class="i"><use href="#i-refresh"/></svg></button>
    <button class="icon-btn" id="logoutBtn" title="Lock" aria-label="Lock"><svg class="i"><use href="#i-lock"/></svg></button>
    <div class="avatar" id="avatar" title="">D</div>
  </div>
</header>

<div class="layout">
  <aside>
    <button class="btn btn-primary side-btn" id="sideUpload"><svg class="i"><use href="#i-upload"/></svg>Upload files</button>
    <button class="btn btn-ghost side-btn" id="sideFolder"><svg class="i"><use href="#i-folder-plus"/></svg>New folder</button>
    <button class="nav active" id="navHome"><svg class="i"><use href="#i-home"/></svg><span id="navName">Startup folder</span></button>
    <button class="nav" id="navLock"><svg class="i"><use href="#i-logout"/></svg><span>Lock app</span></button>
    <div class="storage">
      <div class="t"><span>Storage</span><span id="stPct">–</span></div>
      <div class="bar"><i id="stBar"></i></div>
      <small id="stText">Loading…</small>
    </div>
  </aside>

  <main id="main">
    <div class="toolbar">
      <div class="crumbs" id="crumbs"></div>
      <div class="tb-actions">
        <button class="btn btn-ghost" id="tbSelect"><svg class="i"><use href="#i-select"/></svg><span class="desk-only">Select</span></button>
        <button class="btn btn-ghost desk-only" id="tbFolder"><svg class="i"><use href="#i-folder-plus"/></svg>New folder</button>
        <button class="btn btn-primary desk-only" id="tbUpload"><svg class="i"><use href="#i-upload"/></svg>Upload</button>
        <div class="seg">
          <button id="vGrid" title="Grid view" aria-label="Grid"><svg class="i"><use href="#i-grid"/></svg></button>
          <button id="vList" title="List view" aria-label="List"><svg class="i"><use href="#i-list"/></svg></button>
        </div>
      </div>
    </div>
    <div id="content"></div>
    <div class="more" id="more" style="display:none"><button class="btn btn-ghost" id="moreBtn">Load more</button></div>
  </main>
</div>

<!-- Selection bar -->
<div class="selbar" id="selbar">
  <button class="sb" data-s="cancel" title="Cancel" aria-label="Cancel"><svg class="i"><use href="#i-close"/></svg></button>
  <b id="selCount">0 selected</b>
  <span class="sp"></span>
  <button class="sb" data-s="all" title="Select all"><svg class="i"><use href="#i-selall"/></svg><span class="lbl">All</span></button>
  <button class="sb dng" data-s="del" title="Move to trash"><svg class="i"><use href="#i-trash"/></svg><span class="lbl">Trash</span></button>
  <button class="sb pri" data-s="dl" title="Download"><svg class="i"><use href="#i-download"/></svg><span class="lbl">Download</span></button>
</div>

<!-- FAB (mobile) -->
<div class="fab-wrap" id="fabWrap">
  <button class="fab-mini" id="fabFolder"><svg class="i"><use href="#i-folder-plus"/></svg>New folder</button>
  <button class="fab-mini" id="fabUpload"><svg class="i"><use href="#i-upload"/></svg>Upload</button>
  <button class="fab" id="fab" aria-label="Add"><svg class="i"><use href="#i-plus"/></svg></button>
</div>

<div class="scrim" id="scrim"></div>
<div class="menu" id="menu"></div>

<input type="file" id="fileInput" multiple hidden>
<iframe name="dlframe" id="dlframe" style="display:none" title="download"></iframe>

<div class="drop" id="drop"><div>
  <svg class="i"><use href="#i-upload"/></svg>
  <h2>Drop files to upload</h2>
</div></div>

<div class="uploads" id="uploads">
  <div class="up-head"><span id="upTitle">Uploading</span><button id="upClose" title="Close" aria-label="Close"><svg class="i"><use href="#i-close"/></svg></button></div>
  <div class="up-list" id="upList"></div>
</div>

<div class="zippanel" id="zipPanel">
  <div class="zip-head">
    <svg class="i" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" width="20" height="20"><path d="M4 7l1.5-3h13L20 7v12a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1zM4 7h16M10 11h4"/></svg>
    <b id="zipPanelName">ZIP ban rahi hai…</b>
    <button id="zipPanelClose" title="Close" aria-label="Close"><svg class="i" width="16" height="16"><use href="#i-close"/></svg></button>
  </div>
  <div class="zip-body">
    <div class="zip-file" id="zipCurFile">Preparing…</div>
    <div class="zip-barwrap"><div class="zip-fill" id="zipFill"></div></div>
    <div class="zip-foot">
      <span class="zip-pct" id="zipPct">0%</span>
      <span class="zip-count" id="zipCount"></span>
    </div>
  </div>
</div>

<div class="modal-bg" id="modalBg"><div class="modal">
  <h3 id="mTitle"></h3><p id="mText"></p>
  <input id="mInput" autocomplete="off">
  <div class="row"><button class="btn btn-ghost" id="mCancel">Cancel</button><button class="btn btn-primary" id="mOk">OK</button></div>
</div></div>

<div class="lock" id="lock">
  <div class="lock-card" id="lockCard">
    <div class="lock-mark"><svg class="i"><use href="#i-lock"/></svg></div>
    <h2>Drive Manager</h2>
    <p>Password daalo</p>
    <input id="pw" type="password" inputmode="numeric" autocomplete="current-password" placeholder="••••••">
    <div class="err" id="lockErr"></div>
    <button class="btn btn-primary" id="unlock">Unlock</button>
  </div>
</div>

<div class="toasts" id="toasts"></div>

<script>
const $ = (s) => document.querySelector(s);
const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const FOLDER = 'application/vnd.google-apps.folder';
const isMobile = () => matchMedia('(max-width:860px)').matches;

const state = { path: [{ id: 'root', name: 'Startup folder' }], files: [], next: null, query: '', view: 'grid', loading: false, sel: new Set(), selMode: false, seq: 0 };
const store = {
  get(k, d) { try { return localStorage.getItem(k) || d; } catch (e) { return d; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch (e) {} }
};
const curFolder = () => state.path[state.path.length - 1];
const byId = (id) => state.files.find((x) => x.id === id);
const ic = (n, c) => '<svg class="i' + (c ? ' ' + c : '') + '" aria-hidden="true"><use href="#i-' + n + '"/></svg>';

/* ---------- File type icons (SVG) ---------- */
const GLYPH = {
  image: '<rect x="2" y="3" width="20" height="16" rx="2.5"/><circle cx="8.5" cy="9" r="1.7"/><path d="m22 15-5-5-9 9"/>',
  video: '<rect x="2" y="4" width="20" height="16" rx="4"/><path d="m10 9 5 3-5 3z" fill="#fff"/>',
  audio: '<path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/>',
  doc: '<path d="M4 6h16M4 11h16M4 16h10"/>',
  sheet: '<rect x="3" y="4" width="18" height="16" rx="2.5"/><path d="M3 10h18M3 15h18M9 4v16"/>',
  slides: '<rect x="2" y="4" width="20" height="13" rx="2.5"/><path d="M8 21h8M12 17v4"/>',
  archive: '<path d="M12 3v3m0 3v3m0 3v3"/><rect x="5" y="3" width="14" height="18" rx="3"/>',
  code: '<path d="m8 7-5 5 5 5M16 7l5 5-5 5"/>',
  file: '<path d="M5 8h14M5 13h14M5 18h8"/>'
};
function kind(f) {
  const ext = (f.name.split('.').pop() || '').toLowerCase();
  const m = f.mimeType || '';
  if (m === FOLDER) return { g: 'folder', c: 'var(--primary)' };
  if (m.startsWith('image/') || ['png','jpg','jpeg','gif','webp','svg','bmp','heic'].includes(ext)) return { g: 'image', c: '#a855f7' };
  if (m.startsWith('video/') || ['mp4','mkv','mov','avi','webm'].includes(ext)) return { g: 'video', c: '#ec4899' };
  if (m.startsWith('audio/') || ['mp3','wav','flac','m4a','ogg','aac'].includes(ext)) return { g: 'audio', c: '#14b8a6' };
  if (ext === 'pdf') return { g: 'doc', c: '#e5484d' };
  if (m.includes('spreadsheet') || ['xls','xlsx','csv','ods'].includes(ext)) return { g: 'sheet', c: '#16a34a' };
  if (m.includes('presentation') || ['ppt','pptx','odp'].includes(ext)) return { g: 'slides', c: '#f97316' };
  if (m.includes('document') || ['doc','docx','txt','rtf','odt','md'].includes(ext)) return { g: 'doc', c: '#3b82f6' };
  if (['zip','rar','7z','tar','gz'].includes(ext)) return { g: 'archive', c: '#a16207' };
  if (['py','js','ts','html','css','json','java','c','cpp','go','rs','sh','php'].includes(ext)) return { g: 'code', c: '#475569' };
  return { g: 'file', c: '#64748b' };
}
function fileSvg(k) {
  return '<svg class="fi" viewBox="0 0 48 60" aria-hidden="true">' +
    '<path d="M6 4a4 4 0 0 1 4-4h20l12 12v44a4 4 0 0 1-4 4H10a4 4 0 0 1-4-4z" fill="' + k.c + '"/>' +
    '<path d="M30 0l12 12h-8a4 4 0 0 1-4-4z" fill="#fff" opacity=".4"/>' +
    '<g transform="translate(12 27)" fill="none" stroke="#fff" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' + GLYPH[k.g] + '</g></svg>';
}
const FOLDER_SVG = '<svg class="fold" viewBox="0 0 24 24" aria-hidden="true"><path fill="currentColor" d="M3 6.5A2.5 2.5 0 0 1 5.5 4h3.6c.6 0 1.2.3 1.6.8L12 6.3h6.5A2.5 2.5 0 0 1 21 8.8v8.7a2.5 2.5 0 0 1-2.5 2.5h-13A2.5 2.5 0 0 1 3 17.5z"/></svg>';

const fmtSize = (b) => {
  b = Number(b); if (!b && b !== 0) return '–';
  const u = ['B','KB','MB','GB','TB']; let i = 0;
  while (b >= 1024 && i < u.length - 1) { b /= 1024; i++; }
  return (i ? b.toFixed(b < 10 ? 1 : 0) : b) + ' ' + u[i];
};
const fmtDate = (d) => d ? new Date(d).toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' }) : '';

/* ---------- API ---------- */
async function api(url, opts = {}) {
  const r = await fetch(url, opts);
  let data = null;
  try { data = await r.json(); } catch (e) {}
  if (r.status === 401 && !url.startsWith('/api/login')) showLock();
  if (!r.ok) throw new Error((data && data.error) || 'Request failed (' + r.status + ')');
  return data;
}
const jpost = (url, body) => api(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) });
function toast(msg, err) {
  const el = document.createElement('div');
  el.className = 'toast' + (err ? ' err' : '');
  el.textContent = msg;
  $('#toasts').appendChild(el);
  setTimeout(() => el.remove(), 3500);
}

/* ---------- Rendering ---------- */
function renderCrumbs() {
  const c = $('#crumbs');
  if (state.query) { c.innerHTML = '<button disabled>Search: “' + esc(state.query) + '”</button>'; return; }
  c.innerHTML = state.path.map((p, i) =>
    (i ? '<span class="sep">/</span>' : '') + '<button data-i="' + i + '">' + esc(p.name) + '</button>'
  ).join('');
  c.scrollLeft = c.scrollWidth;
}

function itemHTML(f, idx) {
  const k = kind(f), isF = f.mimeType === FOLDER;
  return '<div class="item ' + (isF ? 'folder' : 'file') + (state.sel.has(f.id) ? ' sel' : '') + '" data-id="' + esc(f.id) + '" style="animation-delay:' + Math.min(idx, 20) * 12 + 'ms">' +
    '<button class="chk" data-act="chk" aria-label="Select">' + ic('check') + '</button>' +
    '<div class="thumb">' + (isF ? FOLDER_SVG : fileSvg(k)) + '</div>' +
    '<div class="info"><div class="name" title="' + esc(f.name) + '">' + esc(f.name) + '</div>' +
    '<div class="meta"><span class="m-date">' + fmtDate(f.modifiedTime) + '</span><span class="m-size">' + (isF ? '' : fmtSize(f.size)) + '</span></div></div>' +
    '<button class="mbtn" data-act="menu" aria-label="More">' + ic('more') + '</button></div>';
}

function render() {
  renderCrumbs();
  const c = $('#content');
  $('#vGrid').classList.toggle('active', state.view === 'grid');
  $('#vList').classList.toggle('active', state.view === 'list');
  if (state.loading && !state.files.length) { c.innerHTML = '<div class="state"><div class="spinner"></div>Loading…</div>'; $('#more').style.display = 'none'; return; }
  if (!state.files.length) {
    c.innerHTML = '<div class="state">' + ic('folder') + '<h3>' +
      (state.query ? 'Kuch nahi mila' : 'Ye folder khali hai') + '</h3><div>' + (state.query ? 'Dusra naam try karo.' : 'Files drag karo ya Upload dabao.') + '</div></div>';
    $('#more').style.display = 'none'; return;
  }
  const items = state.files.map(itemHTML).join('');
  c.innerHTML = state.view === 'grid'
    ? '<div class="grid">' + items + '</div>'
    : '<div class="list-head"><span></span><span></span><span>Name</span><span>Modified</span><span>Size</span><span></span></div><div class="list">' + items + '</div>';
  $('#more').style.display = state.next ? 'flex' : 'none';
  refreshSel();
}

async function load(append = false) {
  if (!append) { state.files = []; state.next = null; state.sel.clear(); state.selMode = false; }
  const seq = ++state.seq;
  state.loading = true; render();
  try {
    const p = new URLSearchParams({ folder: curFolder().id });
    if (state.query) p.set('q', state.query);
    if (append && state.next) p.set('page', state.next);
    const d = await api('/api/files?' + p);
    if (seq !== state.seq) return;
    state.files = append ? state.files.concat(d.files) : d.files;
    state.next = d.next;
  } catch (e) { if (seq === state.seq) toast(e.message, true); }
  if (seq !== state.seq) return;
  state.loading = false; render();
}

async function loadAbout() {
  try {
    const a = await api('/api/about');
    $('#avatar').textContent = (a.name || a.email || 'D')[0].toUpperCase();
    $('#avatar').title = a.email || '';
    if (a.root_name) { state.path[0].name = a.root_name; $('#navName').textContent = a.root_name; renderCrumbs(); }
    if (a.limit) {
      const pct = Math.min(100, (a.usage / a.limit) * 100);
      $('#stPct').textContent = pct.toFixed(0) + '%';
      $('#stBar').style.width = pct + '%';
      $('#stText').textContent = fmtSize(a.usage) + ' of ' + fmtSize(a.limit) + ' used';
    } else {
      $('#stBar').style.width = '8%';
      $('#stText').textContent = fmtSize(a.usage) + ' used';
    }
  } catch (e) { $('#stText').textContent = 'Storage info unavailable'; }
}

/* ---------- Selection ---------- */
function refreshSel() {
  document.body.classList.toggle('selmode', state.selMode);
  document.querySelectorAll('.item').forEach((el) => el.classList.toggle('sel', state.sel.has(el.dataset.id)));
  $('#selbar').classList.toggle('show', state.selMode);
  $('#selCount').textContent = state.sel.size + ' selected';
}
function toggleSel(id, force) {
  if (force === true) state.sel.add(id);
  else if (state.sel.has(id)) state.sel.delete(id); else state.sel.add(id);
  refreshSel();
}
function exitSel() { state.selMode = false; state.sel.clear(); refreshSel(); }
$('#tbSelect').onclick = () => { if (state.selMode) exitSel(); else { state.selMode = true; refreshSel(); } };

function selectedFiles() { return [...state.sel].map(byId).filter(Boolean); }
function downloadMany(files) {
  if (!files.length) return toast('Pehle kuch select karo', true);
  if (files.length === 1 && files[0].mimeType !== FOLDER) return dl(files[0].id);
  // Sirf files (folder nahi) -> har file seedha Google se, ZIP/Render ke bina
  if (files.length <= 25 && files.every((f) => f.mimeType !== FOLDER)) {
    toast(files.length + ' files seedha download ho rahi hain\u2026');
    (async () => { for (const f of files) { await dl(f.id); await new Promise((r) => setTimeout(r, 900)); } })();
    return;
  }
  zipDl(files.map((f) => f.id));
}
async function trashMany(files) {
  if (!files.length) return toast('Pehle kuch select karo', true);
  const ok = await ask({ title: 'Move to trash?', text: files.length + ' item' + (files.length > 1 ? 's' : '') + ' Drive ke Trash mein jayenge. Wahan se restore kar sakte ho.', ok: 'Move to trash', danger: true });
  if (!ok) return;
  const res = await Promise.allSettled(files.map((f) => api('/api/files/' + f.id, { method: 'DELETE' })));
  const bad = res.filter((r) => r.status === 'rejected').length;
  toast(bad ? bad + ' item fail hue' : 'Trash mein bhej diya', !!bad);
  load(); loadAbout();
}
$('#selbar').addEventListener('click', (e) => {
  const b = e.target.closest('[data-s]'); if (!b) return;
  const a = b.dataset.s;
  if (a === 'cancel') exitSel();
  else if (a === 'all') { state.files.forEach((f) => state.sel.add(f.id)); refreshSel(); }
  else if (a === 'dl') { downloadMany(selectedFiles()); exitSel(); }
  else if (a === 'del') trashMany(selectedFiles());
});

/* ---------- Navigation ---------- */
function openFolder(id, name) { state.query = ''; $('#search').value = ''; state.path.push({ id, name }); load(); }
$('#crumbs').addEventListener('click', (e) => {
  const b = e.target.closest('button[data-i]'); if (!b) return;
  state.path = state.path.slice(0, Number(b.dataset.i) + 1); state.query = ''; $('#search').value = ''; load();
});
$('#navHome').onclick = () => { state.path = [state.path[0]]; state.query = ''; $('#search').value = ''; load(); };
$('#refreshBtn').onclick = () => { load(); loadAbout(); };
$('#moreBtn').onclick = () => load(true);

let st;
$('#search').addEventListener('input', (e) => {
  clearTimeout(st);
  st = setTimeout(() => { state.query = e.target.value.trim(); load(); }, 350);
});

/* ---------- File actions ---------- */
function _go(href) {
  const a = document.createElement('a');
  a.href = href;
  document.body.appendChild(a); a.click(); a.remove();
}
async function dl(id) {
  // Seedha Google se download (Render ka CPU beech mein nahi). Fail ho to purana server route.
  try {
    const r = await fetch('/api/direct/' + encodeURIComponent(id), { cache: 'no-store' });
    const d = await r.json();
    if (r.ok && d.url) return _go(d.url);
  } catch (e) {}
  _go('/api/download/' + encodeURIComponent(id));
}
/* ---------- ZIP (direct streaming + live progress) ---------- */
let _zipPollTimer = null;
let _zipJobId = null;
const zipPanel   = $('#zipPanel');
const zipFill    = $('#zipFill');
const zipPct     = $('#zipPct');
const zipCount   = $('#zipCount');
const zipCurFile = $('#zipCurFile');
const zipName    = $('#zipPanelName');

function zipPanelShow() { zipPanel.classList.add('show'); }
function zipPanelHide() { zipPanel.classList.remove('show'); }
function _mb(n) { return n >= 1073741824 ? (n / 1073741824).toFixed(2) + ' GB' : (n / 1048576).toFixed(1) + ' MB'; }

$('#zipPanelClose').onclick = () => {
  clearTimeout(_zipPollTimer);
  _zipJobId = null;
  zipPanelHide();
};

function zipSetProgress(pct, count, text) {
  zipFill.style.width = pct + '%';
  zipFill.style.background = '';
  zipPct.textContent = pct + '%';
  zipCount.textContent = count || '';
  if (text) zipCurFile.textContent = text;
}

function _zipFail(msg) {
  clearTimeout(_zipPollTimer);
  _zipJobId = null;
  zipCurFile.textContent = '\u274c ' + msg;
  zipPct.textContent = 'Error';
  zipFill.style.background = 'var(--danger)';
  toast(msg, true);
}

function _pollZip(jobId, misses) {
  if (_zipJobId !== jobId) return;
  fetch('/api/zip/status/' + jobId)
    .then((r) => r.json().catch(() => ({ status: 'missing' })))
    .then((s) => {
      if (_zipJobId !== jobId) return;
      if (s.status === 'missing') {
        if (misses > 25) return _zipFail('ZIP shuru nahi hui. Page refresh karke dobara try karo.');
        _zipPollTimer = setTimeout(() => _pollZip(jobId, misses + 1), 500);
        return;
      }
      if (s.name) zipName.textContent = s.name;
      if (s.status === 'preparing') {
        zipSetProgress(0, '', 'Files ki list ban rahi hai\u2026');
      } else if (s.status === 'working') {
        const pct = s.total_bytes > 0 ? Math.min(99, Math.floor((s.bytes / s.total_bytes) * 100))
                  : (s.total ? Math.floor((s.done / s.total) * 100) : 0);
        zipSetProgress(pct, s.done + ' / ' + s.total + ' files \u00b7 ' + _mb(s.bytes), s.cur_file || 'Download ho rahi hai\u2026');
      } else if (s.status === 'done') {
        zipSetProgress(100, s.total + ' files \u00b7 ' + _mb(s.bytes), '\u2705 Ho gaya! Browser ke Downloads check karo');
        _zipJobId = null;
        setTimeout(zipPanelHide, 3500);
        return;
      } else if (s.status === 'error') {
        return _zipFail(s.error || 'ZIP fail ho gayi');
      } else if (s.status === 'cancelled') {
        _zipJobId = null; zipPanelHide(); return;
      }
      _zipPollTimer = setTimeout(() => _pollZip(jobId, 0), 500);
    })
    .catch(() => { _zipPollTimer = setTimeout(() => _pollZip(jobId, misses + 1), 1000); });
}

function zipDlServer(ids) {
  clearTimeout(_zipPollTimer);
  const rnd = crypto.getRandomValues(new Uint8Array(16));
  const jobId = Array.from(rnd).map((b) => b.toString(16).padStart(2, '0')).join('');
  _zipJobId = jobId;

  zipName.textContent = 'ZIP ban rahi hai\u2026';
  zipSetProgress(0, '', 'Shuru ho rahi hai\u2026');
  zipPanelShow();

  // Direct streaming download: browser ko turant file milti hai (koi blob/RAM nahi)
  const f = document.createElement('form');
  f.method = 'POST'; f.action = '/api/zip'; f.target = 'dlframe'; f.style.display = 'none';
  [['ids', JSON.stringify(ids)], ['job', jobId]].forEach(([k, v]) => {
    const i = document.createElement('input');
    i.type = 'hidden'; i.name = k; i.value = v; f.appendChild(i);
  });
  document.body.appendChild(f); f.submit(); f.remove();

  _pollZip(jobId, 0);
}
/* ---------- Folder / bahut saari files: har file seedha Google ke link se (ZIP nahi, Render nahi) ---------- */
async function zipDl(ids) {
  toast('Files ki list ban rahi hai\u2026');
  let t;
  try {
    const r = await fetch('/api/tree', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ids }), cache: 'no-store' });
    t = await r.json();
    if (!r.ok) throw new Error(t.error || 'List nahi ban saki');
  } catch (e) { return toast(e.message || 'List nahi ban saki', true); }
  const files = t.entries.filter((e) => !e.d);
  if (!files.length) return toast('Download karne ke liye koi file nahi mili', true);
  if (files.length > 30) {
    const ok = await ask({ title: files.length + ' files download karein?', text: 'Har file alag se seedha Drive se download hogi. Browser multiple downloads ki permission maang sakta hai.', ok: 'Download' });
    if (!ok) return;
  }
  toast(files.length + ' files download shuru ho rahi hain\u2026');
  for (const f of files) {
    const base = 'https://www.googleapis.com/drive/v3/files/' + encodeURIComponent(f.id);
    _go(f.x ? base + '/export?mimeType=' + encodeURIComponent(f.x) + '&access_token=' + encodeURIComponent(t.token)
            : base + '?alt=media&supportsAllDrives=true&access_token=' + encodeURIComponent(t.token));
    await new Promise((r) => setTimeout(r, 800));
  }
  if (t.errors && t.errors.length) toast(t.errors.length + ' item skip hue (unsupported)', true);
}

function openFile(f) {
  window.open(f.webViewLink || ('https://drive.google.com/file/d/' + f.id + '/view'), '_blank', 'noopener');
}
async function renameFile(f) {
  const v = await ask({ title: 'Rename', input: true, value: f.name, ok: 'Rename' });
  if (!v || v === f.name) return;
  try { await jpost('/api/rename/' + f.id, { name: v }); toast('Rename ho gaya'); load(); }
  catch (err) { toast(err.message, true); }
}

/* ---------- Menu / bottom sheet ---------- */
function closeMenu() { $('#menu').classList.remove('show'); $('#scrim').classList.remove('show'); }
function openMenu(f, x, y) {
  const isF = f.mimeType === FOLDER;
  const rows = [
    [isF ? 'Open folder' : 'Open file', 'open', () => (isF ? openFolder(f.id, f.name) : openFile(f))],
    ...(isF ? [
      ['ZIP via Google Drive (fastest)', 'zip', () => { window.open('https://drive.google.com/drive/folders/' + encodeURIComponent(f.id), '_blank', 'noopener'); toast('Drive khul gaya: folder par right-click \u2192 Download dabao, Google khud ZIP banayega'); }],
      ['Download files separately', 'download', () => zipDl([f.id])]
    ] : [['Download', 'download', () => dl(f.id)]]),
    ['Rename', 'edit', () => renameFile(f)],
    ['Select', 'select', () => { state.selMode = true; toggleSel(f.id, true); }],
    ['Move to trash', 'trash', () => trashMany([f]), true]
  ];
  const m = $('#menu');
  m.innerHTML = '<div class="mh">' + esc(f.name) + '</div>' + rows.map((r, i) =>
    '<button data-i="' + i + '"' + (r[3] ? ' class="dng"' : '') + '>' + ic(r[1]) + '<span>' + r[0] + '</span></button>').join('');
  m.onclick = (e) => {
    const b = e.target.closest('button[data-i]'); if (!b) return;
    closeMenu(); rows[Number(b.dataset.i)][2]();
  };
  m.classList.add('show'); $('#scrim').classList.add('show');
  if (!isMobile()) {
    const w = m.offsetWidth, h = m.offsetHeight;
    m.style.left = Math.max(8, Math.min(x, innerWidth - w - 8)) + 'px';
    m.style.top = Math.max(8, Math.min(y, innerHeight - h - 8)) + 'px';
  }
}
$('#scrim').addEventListener('click', closeMenu);

/* ---------- Item interaction ---------- */
const content = $('#content');
let lpTimer = null, lpFired = false, lpX = 0, lpY = 0;
content.addEventListener('pointerdown', (e) => {
  const el = e.target.closest('.item');
  if (!el || e.pointerType === 'mouse' || e.target.closest('[data-act]')) return;
  lpFired = false; lpX = e.clientX; lpY = e.clientY;
  clearTimeout(lpTimer);
  lpTimer = setTimeout(() => {
    lpFired = true;
    if (navigator.vibrate) navigator.vibrate(18);
    state.selMode = true; toggleSel(el.dataset.id, true);
  }, 450);
});
['pointerup', 'pointercancel', 'pointerleave'].forEach((t) => content.addEventListener(t, () => clearTimeout(lpTimer)));
content.addEventListener('pointermove', (e) => {
  if (Math.abs(e.clientX - lpX) > 8 || Math.abs(e.clientY - lpY) > 8) clearTimeout(lpTimer);
});
content.addEventListener('scroll', () => clearTimeout(lpTimer), true);
content.addEventListener('contextmenu', (e) => {
  const el = e.target.closest('.item'); if (!el) return;
  e.preventDefault();
  if (lpFired) return;
  const f = byId(el.dataset.id); if (f) openMenu(f, e.clientX, e.clientY);
});
content.addEventListener('click', (e) => {
  const el = e.target.closest('.item'); if (!el) return;
  if (lpFired) { lpFired = false; return; }
  const f = byId(el.dataset.id); if (!f) return;
  const act = e.target.closest('[data-act]');
  if (act && act.dataset.act === 'menu') { e.stopPropagation(); const r = act.getBoundingClientRect(); return openMenu(f, r.right - 228, r.bottom + 4); }
  if (act && act.dataset.act === 'chk') { state.selMode = true; return toggleSel(f.id); }
  if (state.selMode) return toggleSel(f.id);
  if (f.mimeType === FOLDER) openFolder(f.id, f.name); else openFile(f);
});

document.addEventListener('keydown', (e) => {
  const typing = /^(INPUT|TEXTAREA)$/.test((e.target.tagName || ''));
  if (e.key === 'Escape') {
    if ($('#menu').classList.contains('show')) return closeMenu();
    if (!$('#modalBg').classList.contains('show') && state.selMode) exitSel();
  }
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'a' && !typing && !$('#lock').classList.contains('show')) {
    e.preventDefault(); state.selMode = true; state.files.forEach((f) => state.sel.add(f.id)); refreshSel();
  }
});

/* ---------- New folder ---------- */
async function newFolder() {
  const v = await ask({ title: 'New folder', input: true, value: 'Untitled folder', ok: 'Create' });
  if (!v) return;
  try { await jpost('/api/folder', { name: v, parent: curFolder().id }); toast('Folder ban gaya'); load(); }
  catch (e) { toast(e.message, true); }
}
$('#tbFolder').onclick = $('#sideFolder').onclick = newFolder;

/* ---------- FAB ---------- */
$('#fab').onclick = (e) => { e.stopPropagation(); $('#fabWrap').classList.toggle('open'); };
document.addEventListener('click', () => $('#fabWrap').classList.remove('open'));
$('#fabFolder').onclick = newFolder;
$('#fabUpload').onclick = () => $('#fileInput').click();

/* ---------- Modal ---------- */
function ask({ title, text = '', input = false, value = '', ok = 'OK', danger = false }) {
  return new Promise((resolve) => {
    $('#mTitle').textContent = title;
    $('#mText').textContent = text; $('#mText').style.display = text ? '' : 'none';
    const inp = $('#mInput'); inp.style.display = input ? '' : 'none'; inp.value = value;
    const okb = $('#mOk'); okb.textContent = ok; okb.className = 'btn ' + (danger ? 'btn-danger' : 'btn-primary');
    $('#modalBg').classList.add('show');
    if (input) { inp.focus(); inp.select(); }
    const done = (r) => { $('#modalBg').classList.remove('show'); okb.onclick = $('#mCancel').onclick = inp.onkeydown = null; resolve(r); };
    okb.onclick = () => done(input ? inp.value.trim() : true);
    $('#mCancel').onclick = () => done(null);
    inp.onkeydown = (e) => { if (e.key === 'Enter') okb.click(); if (e.key === 'Escape') done(null); };
  });
}
$('#modalBg').addEventListener('click', (e) => { if (e.target.id === 'modalBg') $('#mCancel').click(); });

/* ---------- Upload ---------- */
const queue = []; let uploading = false;
$('#tbUpload').onclick = $('#sideUpload').onclick = () => $('#fileInput').click();
$('#fileInput').onchange = (e) => { addFiles(e.target.files); e.target.value = ''; };
$('#upClose').onclick = () => $('#uploads').classList.remove('show');

function addFiles(list) {
  const folder = curFolder().id;
  [...list].forEach((file) => {
    const row = document.createElement('div');
    row.className = 'up-row';
    row.innerHTML = '<div class="up-top"><b title="' + esc(file.name) + '">' + esc(file.name) + '</b><span>Waiting…</span></div><div class="bar"><i></i></div>';
    $('#upList').prepend(row);
    queue.push({ file, folder, row });
  });
  if (queue.length) $('#uploads').classList.add('show');
  runQueue();
}
async function runQueue() {
  if (uploading) return; uploading = true;
  while (queue.length) {
    const job = queue.shift();
    $('#upTitle').textContent = 'Uploading ' + (queue.length + 1) + ' file' + (queue.length ? 's' : '');
    try { await uploadOne(job); } catch (e) {
      job.row.classList.add('err');
      job.row.querySelector('span').textContent = 'Failed';
      toast(job.file.name + ': ' + e.message, true);
    }
  }
  uploading = false;
  $('#upTitle').textContent = 'Uploads finished';
  load(); loadAbout();
}
function uploadOne({ file, folder, row }) {
  return new Promise((resolve, reject) => {
    const bar = row.querySelector('.bar i'), label = row.querySelector('span');
    const fd = new FormData(); fd.append('folder', folder); fd.append('file', file, file.name);
    const x = new XMLHttpRequest();
    x.open('POST', '/api/upload');
    x.upload.onprogress = (e) => {
      if (!e.lengthComputable) return;
      const p = Math.round((e.loaded / e.total) * 100);
      bar.style.width = p + '%';
      label.textContent = p >= 100 ? 'Saving to Drive…' : fmtSize(e.loaded) + ' / ' + fmtSize(e.total);
    };
    x.onload = () => {
      if (x.status >= 200 && x.status < 300) { bar.style.width = '100%'; row.classList.add('done'); label.textContent = 'Done ✓'; resolve(); }
      else { let m = 'Upload failed'; try { m = JSON.parse(x.responseText).error || m; } catch (e) {} if (x.status === 401) showLock(); reject(new Error(m)); }
    };
    x.onerror = () => reject(new Error('Network error'));
    x.send(fd);
  });
}

/* Drag & drop */
let dragN = 0;
const hasFiles = (e) => e.dataTransfer && [...e.dataTransfer.types].includes('Files');
window.addEventListener('dragenter', (e) => { if (!hasFiles(e)) return; e.preventDefault(); dragN++; $('#drop').classList.add('show'); });
window.addEventListener('dragover', (e) => { if (hasFiles(e)) e.preventDefault(); });
window.addEventListener('dragleave', (e) => { if (!hasFiles(e)) return; dragN = Math.max(0, dragN - 1); if (!dragN) $('#drop').classList.remove('show'); });
window.addEventListener('drop', (e) => {
  if (!hasFiles(e)) return; e.preventDefault(); dragN = 0; $('#drop').classList.remove('show');
  const files = [...e.dataTransfer.files];
  if (files.length) addFiles(files);
});

/* ---------- Theme & view ---------- */
function setTheme(t) {
  document.documentElement.dataset.theme = t; store.set('theme', t);
  $('#themeBtn').innerHTML = ic(t === 'dark' ? 'sun' : 'moon');
  const m = document.querySelector('meta[name=theme-color]');
  if (m) m.content = t === 'dark' ? '#171a24' : '#ffffff';
}
$('#themeBtn').onclick = () => setTheme(document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark');
$('#vGrid').onclick = () => { state.view = 'grid'; store.set('view', 'grid'); render(); };
$('#vList').onclick = () => { state.view = 'list'; store.set('view', 'list'); render(); };

/* ---------- Lock / login ---------- */
function showLock() {
  $('#lock').classList.add('show');
  setTimeout(() => $('#pw').focus(), 60);
}
let busy = false;
async function tryLogin() {
  const pw = $('#pw').value;
  if (!pw || busy) return;
  busy = true; $('#lockErr').textContent = ''; $('#unlock').disabled = true;
  try {
    await api('/api/login', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ password: pw }) });
    $('#lock').classList.remove('show'); $('#pw').value = '';
    boot();
  } catch (e) {
    $('#lockErr').textContent = e.message; $('#pw').value = '';
    const c = $('#lockCard'); c.classList.remove('shake'); void c.offsetWidth; c.classList.add('shake');
    $('#pw').focus();
  }
  busy = false; $('#unlock').disabled = false;
}
$('#unlock').onclick = tryLogin;
$('#pw').addEventListener('keydown', (e) => { if (e.key === 'Enter') tryLogin(); });
$('#pw').addEventListener('input', (e) => { if (/^\d{6}$/.test(e.target.value)) tryLogin(); });
async function lockApp() {
  try { await jpost('/api/logout'); } catch (e) {}
  state.path = [state.path[0]]; state.files = []; state.query = ''; $('#search').value = '';
  exitSel(); render(); showLock();
}
$('#logoutBtn').onclick = $('#navLock').onclick = lockApp;

/* ---------- Init ---------- */
function boot() { state.path = [state.path[0]]; load(); loadAbout(); }
setTheme(store.get('theme', matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'));
state.view = store.get('view', 'grid');
render();
fetch('/api/session').then((r) => r.json()).then((d) => { if (d.ok) boot(); else showLock(); }).catch(() => showLock());
</script>
</body>
</html>
'''


app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = None  # upload size limit nahi
app.secret_key = os.environ.get("SECRET_KEY") or hashlib.sha256(
    ("drive-manager|" + APP_PASSWORD + "|" + START_FOLDER).encode()
).hexdigest()
app.permanent_session_lifetime = timedelta(days=30)
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax")

_creds = None
_attempts = {}  # ip -> [count, first_ts]
MAX_ATTEMPTS = 5
BLOCK_SECONDS = 600

# ZIP job store: token -> {data, name, ts}  (max 15 min ttl)
_zip_jobs: dict = {}
_zip_lock = threading.Lock()
ZIP_TTL = 900  # seconds (last activity ke baad)

def _zip_cleanup():
    now = time.time()
    with _zip_lock:
        dead = [k for k, v in _zip_jobs.items() if now - v["ts"] > ZIP_TTL]
        for k in dead:
            del _zip_jobs[k]


_creds_lock = threading.Lock()


def get_creds():
    global _creds
    with _creds_lock:  # 8 download threads ek saath refresh na karein
        if _creds is None:
            raw = os.environ.get("GOOGLE_TOKEN_JSON")
            if raw:
                info = json.loads(raw)
            elif os.path.exists(TOKEN_FILE):
                with open(TOKEN_FILE, encoding="utf-8") as f:
                    info = json.load(f)
            else:
                raise RuntimeError(
                    "Token nahi mila. token.json isi folder mein rakho "
                    "ya GOOGLE_TOKEN_JSON env variable set karo."
                )
            _creds = Credentials.from_authorized_user_info(info, SCOPES)
        if not _creds.valid:
            _creds.refresh(Request())
            if not os.environ.get("GOOGLE_TOKEN_JSON") and os.path.exists(TOKEN_FILE):
                with open(TOKEN_FILE, "w", encoding="utf-8") as f:
                    f.write(_creds.to_json())
        return _creds


def drive():
    # Har request ke liye naya service object (httplib2 thread-safe nahi hota)
    return build("drive", "v3", credentials=get_creds(), cache_discovery=False)


def safe_id(value, default="root"):
    value = (value or default).strip()
    if value == "root":
        return START_FOLDER  # "root" ka matlab hamesha startup folder
    if not re.fullmatch(r"[\w\-]+", value):
        raise ValueError("Invalid id")
    return value


def clean_name(name):
    name = re.sub(r"[\x00-\x1f\\/:*?\"<>|]", "_", name or "").strip(" .")
    return name or "unnamed"


# ---------------------------------------------------------------- auth
@app.before_request
def guard():
    if request.path in ("/", "/api/login", "/api/session"):
        return None
    if request.path.startswith("/api/") and not session.get("ok"):
        return jsonify(error="Login required"), 401
    return None


@app.get("/api/session")
def session_state():
    return jsonify(ok=bool(session.get("ok")))


@app.post("/api/login")
def login():
    ip = (request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
          or request.remote_addr or "?")
    now = time.time()
    rec = _attempts.get(ip)
    if rec and now - rec[1] > BLOCK_SECONDS:
        rec = None
        _attempts.pop(ip, None)
    if rec and rec[0] >= MAX_ATTEMPTS:
        return jsonify(error="Bahut zyada galat attempts. 10 minute baad try karo."), 429

    data = request.get_json(silent=True) or {}
    pw = str(data.get("password") or "")
    if hmac.compare_digest(pw.encode(), APP_PASSWORD.encode()):
        _attempts.pop(ip, None)
        session.clear()
        session.permanent = True
        session["ok"] = True
        return jsonify(ok=True)

    if rec:
        rec[0] += 1
    else:
        _attempts[ip] = [1, now]
    time.sleep(0.8)
    left = MAX_ATTEMPTS - _attempts[ip][0]
    return jsonify(error=f"Galat password ({max(left, 0)} try bache)"), 401


@app.post("/api/logout")
def logout():
    session.clear()
    return jsonify(ok=True)


# ---------------------------------------------------------------- errors
@app.errorhandler(HttpError)
def on_http_error(e):
    try:
        msg = json.loads(e.content.decode())["error"]["message"]
    except Exception:
        msg = str(e)
    return jsonify(error=msg), e.resp.status if e.resp.status >= 400 else 500


@app.errorhandler(Exception)
def on_error(e):
    code = getattr(e, "code", 500)
    if not isinstance(code, int):
        code = 500
    return jsonify(error=str(e)), code


# ---------------------------------------------------------------- pages / api
@app.get("/")
def index():
    return Response(INDEX_HTML, mimetype="text/html", headers={"Cache-Control": "no-store"})


@app.get("/api/about")
def about():
    svc = drive()
    res = svc.about().get(fields="user(displayName,emailAddress),storageQuota").execute()
    q = res.get("storageQuota", {})
    root_name = None
    try:
        root_name = (
            svc.files().get(fileId=START_FOLDER, fields="name", supportsAllDrives=True).execute().get("name")
        )
    except Exception:
        pass
    return jsonify(
        name=res["user"].get("displayName"),
        email=res["user"].get("emailAddress"),
        usage=int(q.get("usage", 0)),
        limit=int(q["limit"]) if q.get("limit") else None,
        root_name=root_name,
    )


@app.get("/api/files")
def list_files():
    folder = safe_id(request.args.get("folder"))
    search = (request.args.get("q") or "").strip()
    page = request.args.get("page") or None

    clauses = ["trashed = false", f"'{folder}' in parents"]
    if search:
        esc = search.replace("\\", "\\\\").replace("'", "\\'")
        clauses.append(f"name contains '{esc}'")

    res = (
        drive()
        .files()
        .list(
            q=" and ".join(clauses),
            pageSize=100,
            pageToken=page,
            orderBy="folder,name_natural",
            fields="nextPageToken,files(id,name,mimeType,size,modifiedTime,webViewLink)",
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
        )
        .execute()
    )
    return jsonify(files=res.get("files", []), next=res.get("nextPageToken"))


@app.post("/api/upload")
def upload():
    folder = safe_id(request.form.get("folder"))
    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify(error="File nahi mili"), 400

    f.stream.seek(0, os.SEEK_END)
    size = f.stream.tell()
    f.stream.seek(0)

    mime = f.mimetype or mimetypes.guess_type(f.filename)[0] or "application/octet-stream"
    media = MediaIoBaseUpload(
        f.stream, mimetype=mime, chunksize=5 * 1024 * 1024, resumable=size > 0
    )
    req = drive().files().create(
        body={"name": f.filename, "parents": [folder]},
        media_body=media,
        fields="id,name,size",
        supportsAllDrives=True,
    )
    if size > 0:
        resp = None
        while resp is None:
            _, resp = req.next_chunk()
    else:
        resp = req.execute()
    return jsonify(resp)


# ---------------------------------------------------------------- download
# Fast + low-memory design:
#   * Har file 8MB ke "segments" (HTTP Range requests) mein tooti hai
#   * FETCH_THREADS segments EK SAATH Drive se aate hain (badi single file bhi parallel)
#   * Sirf WINDOW segments memory mein rehte hain  ->  RAM bounded (~80MB), 512MB mein safe
SEG_SIZE = 8 * 1024 * 1024
FETCH_THREADS = 8
WINDOW = 10
_tls = threading.local()


def _thread_drive():
    """Har worker thread ka apna Drive service (httplib2 thread-safe nahi hota)."""
    svc = getattr(_tls, "svc", None)
    if svc is None:
        svc = drive()
        _tls.svc = svc
    return svc


_RETRY_REASONS = ("ratelimitexceeded", "userratelimitexceeded", "backenderror", "internalerror")


def _retryable(e):
    st = e.resp.status
    if st in (408, 429, 500, 502, 503, 504):
        return True
    if st == 403:
        try:
            return any(r in e.content.decode("utf-8", "ignore").lower() for r in _RETRY_REASONS)
        except Exception:  # noqa: BLE001
            return False
    return False


def _fetch_seg(file_id, mime, start, end):
    """Ek segment download karo (retry + length check ke saath)."""
    last = None
    for attempt in range(5):
        try:
            svc = _thread_drive()
            if mime in EXPORTS:
                data = svc.files().export_media(
                    fileId=file_id, mimeType=EXPORTS[mime][0]
                ).execute()
            else:
                req = svc.files().get_media(fileId=file_id, supportsAllDrives=True)
                req.headers["Range"] = f"bytes={start}-{end}"
                data = req.execute()
                want = end - start + 1
                if len(data) != want:
                    raise IOError(f"short read {len(data)}/{want}")
            return data
        except HttpError as e:
            last = e
            if not _retryable(e):
                raise
            _tls.svc = None
        except Exception as e:  # noqa: BLE001
            last = e
            _tls.svc = None  # naya connection
        time.sleep(min(2 ** attempt, 8) * 0.4)
    raise last


def _segments(m):
    """File ke (start, end) byte ranges. Google Docs export = ek hi segment."""
    if m["mimeType"] in EXPORTS:
        return [(0, 0)]
    size = int(m.get("size") or 0)
    if size <= 0:
        return []
    return [(x, min(x + SEG_SIZE, size) - 1) for x in range(0, size, SEG_SIZE)]


def fetch_ordered(pool, tasks):
    """tasks ke (data, error) ORDER mein yield karta hai; WINDOW tak parallel prefetch."""
    it = iter(tasks)
    pending = deque()
    try:
        while True:
            while len(pending) < WINDOW:
                t = next(it, None)
                if t is None:
                    break
                pending.append(pool.submit(_fetch_seg, *t))
            if not pending:
                return
            fut = pending.popleft()
            try:
                yield fut.result(), None
            except Exception as e:  # noqa: BLE001
                yield None, e
    finally:
        for f in pending:
            f.cancel()


def iter_file(svc, file_id, mime):
    """(Purana helper) sequential chunks - ab sirf fallback ke liye."""
    if mime in EXPORTS:
        req = svc.files().export_media(fileId=file_id, mimeType=EXPORTS[mime][0])
    else:
        req = svc.files().get_media(fileId=file_id, supportsAllDrives=True)
    buf = io.BytesIO()
    dl = MediaIoBaseDownload(buf, req, chunksize=4 * 1024 * 1024)
    done = False
    while not done:
        _, done = dl.next_chunk()
        data = buf.getvalue()
        buf.seek(0)
        buf.truncate(0)
        if data:
            yield data


def _fresh_token():
    """(access_token, expires_in_seconds) - kam se kam ~10 min chalne wala."""
    creds = get_creds()
    with _creds_lock:
        exp = creds.expiry
        if not creds.token or (exp and (exp - datetime.utcnow()).total_seconds() < 600):
            creds.refresh(Request())
        left = int((creds.expiry - datetime.utcnow()).total_seconds()) if creds.expiry else 3000
        return creds.token, max(left, 60)


@app.get("/api/token")
def api_token():
    t, left = _fresh_token()
    r = jsonify(token=t, expires_in=left)
    r.headers["Cache-Control"] = "no-store"
    return r


@app.post("/api/tree")
def api_tree():
    """Folder/selection ki flat file list (sirf metadata) + token. File data Render se nahi guzarta."""
    ids = (request.get_json(silent=True) or {}).get("ids")
    if not isinstance(ids, list) or not ids:
        return jsonify(error="Kuch select nahi hai"), 400
    ids = [safe_id(i) for i in ids[:5000]]
    svc = drive()

    def get_meta(i):
        return _thread_drive().files().get(
            fileId=i, fields="id,name,mimeType,size", supportsAllDrives=True
        ).execute()

    with ThreadPoolExecutor(max_workers=min(8, len(ids))) as p:
        metas = list(p.map(get_meta, ids))
    errors, out = [], []
    for path, m in walk(svc, metas, errors):
        if m is None:
            out.append({"p": path, "d": 1})
        elif m["mimeType"] in EXPORTS:
            out.append({"p": path, "id": m["id"], "s": 0, "x": EXPORTS[m["mimeType"]][0]})
        else:
            out.append({"p": path, "id": m["id"], "s": int(m.get("size") or 0)})
    t, left = _fresh_token()
    r = jsonify(entries=out, errors=errors, token=t, expires_in=left)
    r.headers["Cache-Control"] = "no-store"
    return r


@app.get("/api/direct/<file_id>")
def direct_link(file_id):
    """Browser ko Google ka seedha download URL do - data Render se hokar nahi guzarta."""
    if os.environ.get("DIRECT_DL", "1") == "0":
        return jsonify(error="direct off"), 404
    file_id = safe_id(file_id)
    meta = drive().files().get(
        fileId=file_id, fields="id,name,mimeType", supportsAllDrives=True
    ).execute()
    mime = meta["mimeType"]
    if mime == FOLDER_MIME:
        return jsonify(error="Folder ke liye ZIP download use karo"), 400

    token = _fresh_token()[0]

    base = "https://www.googleapis.com/drive/v3/files/" + quote(file_id, safe="")
    if mime in EXPORTS:
        url = f"{base}/export?mimeType={quote(EXPORTS[mime][0], safe='')}&access_token={quote(token, safe='')}"
    elif mime.startswith("application/vnd.google-apps."):
        return jsonify(error="Ye Google file type download nahi hota"), 400
    else:
        url = f"{base}?alt=media&supportsAllDrives=true&acknowledgeAbuse=false&access_token={quote(token, safe='')}"
    resp = jsonify(url=url, name=meta["name"])
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.get("/api/download/<file_id>")
def download(file_id):
    file_id = safe_id(file_id)
    svc = drive()
    meta = svc.files().get(fileId=file_id, fields="id,name,mimeType,size", supportsAllDrives=True).execute()
    name, mime = meta["name"], meta["mimeType"]

    if mime == FOLDER_MIME:
        return jsonify(error="Folder ke liye ZIP download use karo"), 400

    size = None
    out_mime = mime
    if mime in EXPORTS:
        out_mime, ext = EXPORTS[mime]
        if not name.lower().endswith(ext):
            name += ext
    else:
        size = meta.get("size")

    tasks = [(file_id, mime, st, en) for st, en in _segments(meta)]
    pool = ThreadPoolExecutor(max_workers=FETCH_THREADS)

    def stream():
        try:
            for data, err in fetch_ordered(pool, tasks):
                if err:
                    raise err
                yield data
        finally:
            pool.shutdown(wait=False, cancel_futures=True)

    gen = stream()
    first = next(gen, b"")  # error ho to response shuru hone se pehle pakad lo

    def out():
        yield first
        yield from gen

    headers = {"Content-Disposition": f"attachment; filename*=UTF-8\'\'{quote(name)}"}
    if size:
        headers["Content-Length"] = size
    return Response(out(), mimetype=out_mime, headers=headers)


# ---------------------------------------------------------------- ZIP (multi-select / folder)
class ZipSink:
    """Unseekable buffer: zipfile isme likhta hai, hum chunks bahar bhejte hain."""

    def __init__(self):
        self.buf = bytearray()
        self.pos = 0

    def write(self, b):
        self.buf += b
        self.pos += len(b)
        return len(b)

    def tell(self):
        return self.pos

    def flush(self):
        pass

    def take(self):
        data = bytes(self.buf)
        self.buf.clear()
        return data


def list_children(svc, folder_id):
    token = None
    while True:
        res = (
            svc.files()
            .list(
                q=f"'{folder_id}' in parents and trashed = false",
                pageSize=1000,
                pageToken=token,
                fields="nextPageToken,files(id,name,mimeType,size)",
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
            )
            .execute()
        )
        yield from res.get("files", [])
        token = res.get("nextPageToken")
        if not token:
            break


def walk(svc, metas, errors):
    """(zip_path, meta) yield karta hai. Folder ke liye meta=None aur path '/' se khatam."""
    used = set()

    def uniq(path, is_dir=False):
        stem, ext = (path, "") if is_dir else os.path.splitext(path)
        cand, n = path, 1
        while cand.lower() in used:
            n += 1
            cand = f"{stem} ({n}){ext}"
        used.add(cand.lower())
        return cand

    def rec(items, prefix):
        for m in items:
            name = clean_name(m["name"])
            mime = m["mimeType"]
            if mime == FOLDER_MIME:
                path = uniq(prefix + name, True)
                yield path + "/", None
                try:
                    yield from rec(list(list_children(svc, m["id"])), path + "/")
                except Exception as e:  # noqa: BLE001
                    errors.append(f"Folder list nahi hua: {path} ({e})")
            elif mime.startswith("application/vnd.google-apps.") and mime not in EXPORTS:
                errors.append(f"Skipped (unsupported Google type): {prefix}{name}")
            else:
                if mime in EXPORTS and not name.lower().endswith(EXPORTS[mime][1]):
                    name += EXPORTS[mime][1]
                yield uniq(prefix + name), m

    yield from rec(metas, "")


def zip_stream(entries, errors, job):
    """
    ZIP seedha user ko stream hoti hai (Drive jaisa): koi "prepare" wait nahi,
    RAM mein poori ZIP kabhi nahi banti. Files/segments parallel fetch hote hain.
    """
    pool = ThreadPoolExecutor(max_workers=FETCH_THREADS)
    sink = ZipSink()
    zf = zipfile.ZipFile(sink, "w", zipfile.ZIP_STORED, allowZip64=True)
    now = time.localtime()[:6]

    plan, tasks = [], []
    for path, m in entries:
        if m is None:
            plan.append((path, None, 0))
            continue
        segs = _segments(m)
        plan.append((path, m, len(segs)))
        for st, en in segs:
            tasks.append((m["id"], m["mimeType"], st, en))

    stream = fetch_ordered(pool, tasks)
    try:
        for path, m, nseg in plan:
            info = zipfile.ZipInfo(path, now)
            info.compress_type = zipfile.ZIP_STORED
            if m is None:  # folder entry
                info.external_attr = (0o40775 << 16) | 0x10
                zf.writestr(info, b"")
                out = sink.take()
                if out:
                    yield out
                continue

            info.external_attr = 0o100644 << 16
            job["cur_file"] = m["name"]
            failed = None
            with zf.open(info, "w", force_zip64=True) as dest:
                for _ in range(nseg):
                    data, err = next(stream)
                    if failed or err:
                        failed = failed or err
                        continue
                    dest.write(data)
                    job["bytes"] += len(data)
                    job["ts"] = time.time()
                    out = sink.take()
                    if out:
                        yield out
            if failed:
                errors.append(f"Download fail: {path} ({failed})")
            job["done"] += 1
            job["ts"] = time.time()
            out = sink.take()
            if out:
                yield out

        if errors:
            zf.writestr("_errors.txt", "\n".join(errors))
        zf.close()
        out = sink.take()
        if out:
            yield out
    finally:
        pool.shutdown(wait=False, cancel_futures=True)


@app.post("/api/zip")
def zip_download():
    raw = request.form.get("ids")
    if raw is None:
        raw = (request.get_json(silent=True) or {}).get("ids")
    ids = json.loads(raw) if isinstance(raw, str) else raw
    if not isinstance(ids, list) or not ids:
        return jsonify(error="Kuch select nahi hai"), 400
    ids = [safe_id(i) for i in ids[:5000]]

    job_id = request.form.get("job") or ""
    if not re.fullmatch(r"[0-9a-f]{32}", job_id):
        job_id = uuid.uuid4().hex

    svc = drive()
    _zip_cleanup()
    job = {
        "status": "preparing", "name": "ZIP", "total": 0, "done": 0,
        "bytes": 0, "total_bytes": 0, "cur_file": "", "error": "", "ts": time.time(),
    }
    with _zip_lock:
        _zip_jobs[job_id] = job  # poll turant kaam kare, "missing" kabhi na aaye

    def get_meta(i):
        return _thread_drive().files().get(
            fileId=i, fields="id,name,mimeType,size", supportsAllDrives=True
        ).execute()

    try:
        with ThreadPoolExecutor(max_workers=min(8, len(ids))) as p:
            metas = list(p.map(get_meta, ids))
    except Exception as e:  # noqa: BLE001
        job["status"] = "error"
        job["error"] = str(e)
        raise

    if len(metas) == 1 and metas[0]["mimeType"] == FOLDER_MIME:
        zname = clean_name(metas[0]["name"]) + ".zip"
    else:
        zname = "Drive_files_" + time.strftime("%Y%m%d_%H%M") + ".zip"
    job["name"] = zname

    def gen():
        try:
            errors = []
            entries = list(walk(svc, metas, errors))
            files = [m for _, m in entries if m is not None]
            job["total"] = len(files)
            job["total_bytes"] = sum(int(m.get("size") or 0) for m in files)
            job["status"] = "working"
            yield from zip_stream(entries, errors, job)
            job["status"] = "done"
        except GeneratorExit:
            job["status"] = "cancelled"
            raise
        except Exception as e:  # noqa: BLE001
            job["status"] = "error"
            job["error"] = str(e)
            raise

    headers = {
        "Content-Disposition": f"attachment; filename*=UTF-8\'\'{quote(zname)}",
        "Cache-Control": "no-store",
        "X-Accel-Buffering": "no",
    }
    return Response(gen(), mimetype="application/zip", headers=headers)


@app.get("/api/zip/status/<job_id>")
def zip_status(job_id):
    if not re.fullmatch(r"[0-9a-f]{32}", job_id):
        return jsonify(error="Invalid job_id"), 400
    with _zip_lock:
        job = _zip_jobs.get(job_id)
    if not job:
        return jsonify(status="missing"), 404
    return jsonify({k: job[k] for k in
                    ("status", "name", "total", "done", "bytes", "total_bytes", "cur_file", "error")})


# ---------------------------------------------------------------- folder / rename / trash
@app.post("/api/folder")
def new_folder():
    data = request.get_json(force=True)
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify(error="Naam khali hai"), 400
    res = (
        drive()
        .files()
        .create(
            body={"name": name, "mimeType": FOLDER_MIME, "parents": [safe_id(data.get("parent"))]},
            fields="id,name",
            supportsAllDrives=True,
        )
        .execute()
    )
    return jsonify(res)


@app.post("/api/rename/<file_id>")
def rename(file_id):
    name = (request.get_json(force=True).get("name") or "").strip()
    if not name:
        return jsonify(error="Naam khali hai"), 400
    res = (
        drive()
        .files()
        .update(fileId=safe_id(file_id), body={"name": name}, fields="id,name", supportsAllDrives=True)
        .execute()
    )
    return jsonify(res)


@app.delete("/api/files/<file_id>")
def trash(file_id):
    # Permanent delete nahi, Drive ke Trash mein jata hai (wapas mil sakta hai)
    drive().files().update(
        fileId=safe_id(file_id), body={"trashed": True}, fields="id", supportsAllDrives=True
    ).execute()
    return jsonify(ok=True)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)
