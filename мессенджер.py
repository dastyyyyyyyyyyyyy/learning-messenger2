import base64
import binascii
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse


HOST = os.environ.get("HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", "8000"))
DATABASE = Path(os.environ.get(
    "MESSENGER_DB", str(Path(__file__).with_name("messenger.db"))
))
COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "").lower() in {"1", "true", "yes"}
SESSION_TTL = 12 * 60 * 60
PASSWORD_ITERATIONS = 310_000
MAX_MEDIA_BYTES = 20 * 1024 * 1024
MAX_MEDIA_REQUEST_BYTES = 28 * 1024 * 1024
MAX_TOTAL_MEDIA_BYTES = 100 * 1024 * 1024
sessions = {}
sessions_lock = threading.Lock()


PAGE = """<!doctype html>
<html lang="ru">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Мессенджер</title>
    <style>
        * { box-sizing: border-box; }
        body {
            margin: 0;
            min-height: 100vh;
            padding: 20px;
            display: grid;
            place-items: center;
            background: linear-gradient(135deg, #dce5ed, #edf2f6);
            color: #17212b;
            font-family: Inter, "Segoe UI", Arial, sans-serif;
        }
        button, input { font: inherit; }
        button { cursor: pointer; }
        .hidden { display: none !important; }
        .auth {
            width: min(390px, calc(100% - 28px));
            padding: 28px;
            border: 1px solid #e8edf1;
            border-radius: 22px;
            background: white;
            box-shadow: 0 20px 60px #17212b1c;
        }
        .auth h1 { margin: 0; color: #2481cc; letter-spacing: -.5px; }
        .auth > p:not(.notice) { color: #71808e; line-height: 1.5; }
        .auth label { display: block; margin-top: 15px; }
        .auth input, .auth button {
            width: 100%;
            min-height: 44px;
            margin-top: 7px;
            padding: 10px 12px;
            border: 1px solid #d5dfe8;
            border-radius: 10px;
        }
        input:focus-visible, button:focus-visible {
            outline: 3px solid #3390ec66;
            outline-offset: 2px;
        }
        .primary {
            border: 0 !important;
            background: linear-gradient(135deg, #3390ec, #2481cc);
            color: white;
            font-weight: bold;
        }
        .link-button {
            border: 0 !important;
            background: transparent;
            color: #2481cc;
        }
        .link-button:hover { background: #eef6fc; }
        .notice { min-height: 22px; color: #bd3b44; }
        .chat-app {
            width: min(1120px, 100%);
            height: min(800px, calc(100dvh - 40px));
            min-height: 480px;
            display: grid;
            grid-template-columns: 320px 1fr;
            overflow: hidden;
            border: 1px solid #e0e7ed;
            border-radius: 18px;
            background: white;
            box-shadow: 0 22px 65px #17212b24;
        }
        .sidebar {
            min-width: 0;
            display: flex;
            flex-direction: column;
            border-right: 1px solid #e6ebef;
            background: #fff;
        }
        .sidebar-top {
            padding: 18px 16px 14px;
            background: #fff;
        }
        .sidebar-title {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 8px;
        }
        .sidebar-title h1 { margin: 0; font-size: 21px; color: #2481cc; letter-spacing: -.4px; }
        .sidebar-title .link-button {
            width: auto;
            padding: 8px 10px;
            border-radius: 9px;
            font-size: 13px;
        }
        .sidebar-title .settings-button { font-size: 18px; }
        .sidebar-actions {
            display: flex;
            align-items: center;
            gap: 4px;
        }
        .sidebar-top input {
            width: 100%;
            margin-top: 14px;
            padding: 11px 15px;
            border: 0;
            border-radius: 18px;
            background: #f1f3f5;
            outline-color: #3390ec;
        }
        .user-list { flex: 1; overflow-y: auto; padding: 4px 8px; }
        .user-list > .empty-chat {
            margin: 16px 4px;
            font-size: 13px;
            line-height: 1.45;
        }
        .user-row {
            width: 100%;
            display: flex;
            align-items: center;
            gap: 12px;
            padding: 11px 10px;
            border: 0;
            border-radius: 12px;
            background: transparent;
            text-align: left;
            transition: background .15s ease;
        }
        .user-row:hover, .user-row.selected { background: #eef6fc; }
        .group-list {
            max-height: 180px;
            overflow-y: auto;
            padding: 2px 8px;
            border-bottom: 1px solid #e6ebef;
        }
        .group-actions { display: flex; gap: 6px; margin-top: 10px; }
        .group-actions button {
            flex: 1;
            min-height: 34px;
            border: 1px solid #d5dfe8;
            border-radius: 9px;
            background: white;
            color: #2481cc;
            font-size: 12px;
        }
        .group-row .user-last { max-width: 220px; }
        .call-actions { display: flex; gap: 5px; margin-left: auto; }
        .call-actions button {
            width: 38px;
            height: 38px;
            border: 0;
            border-radius: 50%;
            background: #eef6fc;
            color: #2481cc;
            font-size: 17px;
        }
        .call-dialog { width: min(600px, calc(100% - 28px)); }
        .call-videos {
            position: relative;
            min-height: 220px;
            margin: 18px 20px 10px;
            overflow: hidden;
            border-radius: 14px;
            background: #111a23;
        }
        .call-videos video { display: block; width: 100%; max-height: 55vh; object-fit: contain; }
        #localVideo {
            position: absolute;
            right: 12px;
            bottom: 12px;
            width: min(30%, 150px);
            border: 2px solid white;
            border-radius: 9px;
        }
        .call-controls { display: flex; gap: 8px; padding: 10px 20px 18px; }
        .call-controls button {
            flex: 1;
            min-height: 42px;
            border: 0;
            border-radius: 9px;
            background: #3390ec;
            color: white;
            font-weight: 600;
        }
        .call-controls #callEnd { background: #d94343; }
        .call-status { min-height: 20px; margin: 6px 20px; color: #71808e; }
        .avatar {
            width: 46px;
            height: 46px;
            flex: 0 0 46px;
            display: grid;
            place-items: center;
            border-radius: 50%;
            background: linear-gradient(145deg, #7bc2eb, #438bd0);
            color: white;
            font-size: 20px;
            font-weight: 600;
            text-transform: uppercase;
        }
        .user-info { min-width: 0; }
        .user-name { display: block; font-weight: 600; }
        .user-last {
            display: block;
            max-width: 220px;
            margin-top: 4px;
            overflow: hidden;
            color: #71808e;
            font-size: 13px;
            text-overflow: ellipsis;
            white-space: nowrap;
        }
        .chat {
            min-width: 0;
            display: flex;
            flex-direction: column;
            background-color: #e9eff3;
            background-image: radial-gradient(#d7e0e6 0.8px, transparent 0.8px);
            background-size: 20px 20px;
        }
        .chat-header {
            min-height: 72px;
            display: flex;
            align-items: center;
            gap: 12px;
            padding: 10px 20px;
            background: #fff;
            box-shadow: 0 2px 6px #17212b0d;
        }
        .chat-header strong { display: block; font-size: 15px; }
        .chat-header small { display: block; margin-top: 4px; color: #71808e; font-size: 12px; }
        .messages {
            flex: 1;
            display: flex;
            flex-direction: column;
            gap: 9px;
            overflow-y: auto;
            padding: 22px max(18px, 7%);
            scroll-behavior: smooth;
        }
        .bubble {
            max-width: min(78%, 560px);
            padding: 10px 13px 7px;
            border-radius: 14px 14px 14px 4px;
            background: white;
            box-shadow: 0 2px 5px #17212b12;
            overflow-wrap: anywhere;
            line-height: 1.4;
        }
        .bubble.mine {
            align-self: flex-end;
            border-radius: 14px 14px 4px 14px;
            background: #d9fdd3;
        }
        .bubble-time {
            margin-top: 5px;
            color: #71808e;
            font-size: 11px;
            text-align: right;
        }
        .empty-chat {
            margin: auto;
            padding: 11px 18px;
            border: 1px solid #ffffff99;
            border-radius: 18px;
            background: #dce6ecdd;
            color: #536675;
            font-size: 13px;
            text-align: center;
        }
        .composer {
            position: relative;
            display: flex;
            align-items: center;
            gap: 12px;
            padding: 12px 18px;
            background: #f5f7f8;
        }
        .composer input {
            min-width: 0;
            flex: 1;
            min-height: 46px;
            padding: 12px 18px;
            border: 0;
            border-radius: 22px;
            background: white;
            outline-color: #3390ec;
        }
        .composer button {
            width: 46px;
            height: 46px;
            border: 0;
            border-radius: 50%;
            background: linear-gradient(145deg, #3390ec, #2481cc);
            color: white;
            font-size: 20px;
            box-shadow: 0 3px 8px #2481cc44;
        }
        .composer .tool-button {
            flex: 0 0 42px;
            width: 42px;
            height: 42px;
            background: transparent;
            color: #2481cc;
            font-size: 21px;
            box-shadow: none;
        }
        .composer .send-button {
            flex: 0 0 46px;
        }
        .attachment-input { display: none; }
        .composer label.tool-button { cursor: pointer; }
        .emoji-picker {
            position: absolute;
            z-index: 2;
            bottom: 66px;
            left: 12px;
            display: grid;
            grid-template-columns: repeat(6, 40px);
            gap: 4px;
            padding: 8px;
            border: 1px solid #e0e7ed;
            border-radius: 14px;
            background: white;
            box-shadow: 0 8px 24px #17212b24;
        }
        .emoji-picker button {
            width: 40px;
            height: 40px;
            border: 0;
            border-radius: 9px;
            background: transparent;
            font-size: 23px;
        }
        .emoji-picker button:hover { background: #eef6fc; }
        .media-message { max-width: min(78%, 560px); }
        .media-message img,
        .media-message video {
            display: block;
            max-width: 100%;
            max-height: 360px;
            border-radius: 9px;
        }
        .media-name { margin-top: 6px; color: #71808e; font-size: 12px; }
        .poll-card {
            min-width: 230px;
            max-width: 420px;
        }
        .poll-question { margin-bottom: 10px; font-weight: 700; }
        .poll-option {
            display: flex;
            align-items: center;
            gap: 8px;
            margin: 8px 0;
            font-size: 14px;
        }
        .poll-option input { width: 17px; height: 17px; accent-color: #3390ec; }
        .poll-bar {
            height: 5px;
            margin-top: 4px;
            overflow: hidden;
            border-radius: 4px;
            background: #e6ebef;
        }
        .poll-bar span { display: block; height: 100%; background: #3390ec; }
        .poll-card .poll-vote-button {
            width: 100%;
            min-height: 34px;
            margin-top: 10px;
            border: 0;
            border-radius: 8px;
            background: #3390ec;
            color: white;
            font-weight: 600;
        }
        .poll-footer { margin-top: 8px; color: #71808e; font-size: 12px; }
        .poll-compose-dialog { width: min(430px, calc(100% - 28px)); }
        .poll-options-input { display: grid; gap: 8px; }
        .show-password {
            display: flex !important;
            align-items: center;
            gap: 8px;
            font-size: 13px;
        }
        .show-password input { width: 17px !important; min-height: 17px !important; margin: 0 !important; }
        .mobile-back { display: none; }
        html[data-font-size="small"] { font-size: 14px; }
        html[data-font-size="medium"] { font-size: 16px; }
        html[data-font-size="large"] { font-size: 18px; }
        .settings-dialog {
            width: min(460px, calc(100% - 28px));
            max-height: min(85vh, 720px);
            padding: 0;
            overflow: auto;
            border: 0;
            border-radius: 18px;
            background: white;
            color: #17212b;
            box-shadow: 0 24px 70px #0005;
        }
        .settings-dialog::backdrop { background: #10192388; }
        .settings-header {
            position: sticky;
            top: 0;
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding: 16px 20px;
            border-bottom: 1px solid #e6ebef;
            background: white;
        }
        .settings-header h2 { margin: 0; font-size: 20px; }
        .settings-close {
            width: 36px;
            height: 36px;
            border: 0;
            border-radius: 50%;
            background: #f1f3f5;
            color: inherit;
            font-size: 20px;
        }
        .settings-content { padding: 4px 20px 20px; }
        .settings-section { padding: 14px 0; border-bottom: 1px solid #e6ebef; }
        .settings-section:last-child { border-bottom: 0; }
        .settings-section h3 { margin: 0 0 10px; font-size: 15px; }
        .settings-section label { display: block; margin: 10px 0 5px; font-size: 13px; }
        .settings-section input,
        .settings-section select,
        .settings-section button {
            width: 100%;
            min-height: 40px;
            padding: 9px 11px;
            border: 1px solid #d5dfe8;
            border-radius: 9px;
            background: white;
            color: #17212b;
            font: inherit;
        }
        .settings-section button {
            margin-top: 10px;
            border: 0;
            background: #3390ec;
            color: white;
            font-weight: 600;
        }
        .settings-section .settings-note { margin: 8px 0 0; color: #71808e; font-size: 12px; }
        .settings-status { min-height: 18px; margin: 8px 0 0; color: #24804a; font-size: 13px; }
        body.dark-theme {
            background: linear-gradient(135deg, #121923, #1d2733);
            color: #e6edf3;
        }
        body.dark-theme .chat-app,
        body.dark-theme .auth {
            border-color: #303c49;
            background: #1b2530;
            color: #e6edf3;
        }
        body.dark-theme .sidebar,
        body.dark-theme .sidebar-top,
        body.dark-theme .chat-header,
        body.dark-theme .user-row {
            background: #1b2530;
            color: #e6edf3;
        }
        body.dark-theme .sidebar { border-color: #303c49; }
        body.dark-theme .group-list { border-color: #303c49; }
        body.dark-theme .group-actions button { border-color: #394755; background: #1b2530; }
        body.dark-theme .user-row:hover,
        body.dark-theme .user-row.selected { background: #26394b; }
        body.dark-theme .sidebar-top input,
        body.dark-theme .composer input {
            background: #111a23;
            color: #e6edf3;
        }
        body.dark-theme .chat {
            background-color: #111a23;
            background-image: radial-gradient(#263542 0.8px, transparent 0.8px);
        }
        body.dark-theme .bubble {
            background: #263542;
            color: #e6edf3;
        }
        body.dark-theme .bubble.mine { background: #254a3b; }
        body.dark-theme .composer { background: #1b2530; }
        body.dark-theme .empty-chat { background: #263542dd; color: #c1ccd5; }
        body.dark-theme .auth input { border-color: #394755; background: #111a23; color: #e6edf3; }
        body.dark-theme .settings-dialog { background: #1b2530; color: #e6edf3; }
        body.dark-theme .settings-header { border-color: #303c49; background: #1b2530; }
        body.dark-theme .settings-close { background: #263542; color: #e6edf3; }
        body.dark-theme .settings-section { border-color: #303c49; }
        body.dark-theme .settings-section input,
        body.dark-theme .settings-section select {
            border-color: #394755;
            background: #111a23;
            color: #e6edf3;
        }
        body.dark-theme .settings-note { color: #a7b5c2; }
        body.dark-theme .emoji-picker { border-color: #394755; background: #1b2530; }
        body.dark-theme .emoji-picker button:hover { background: #26394b; }
        body.dark-theme .poll-bar { background: #394755; }
        body.dark-theme .media-name,
        body.dark-theme .poll-footer { color: #a7b5c2; }
        body.dark-theme .poll-vote-button { color: white; }
        body.dark-theme .auth > p:not(.notice),
        body.dark-theme .user-last,
        body.dark-theme .chat-header small,
        body.dark-theme .bubble-time { color: #a7b5c2; }
        body.dark-theme .call-actions button { background: #26394b; color: #90caff; }
        @media (max-width: 650px) {
            body { display: block; padding: 0; }
            .chat-app {
                width: 100%;
                height: 100dvh;
                min-height: 0;
                grid-template-columns: 1fr;
                border: 0;
                border-radius: 0;
            }
            .chat-app.chat-open .sidebar { display: none; }
            .chat-app:not(.chat-open) .chat { display: none; }
            .mobile-back {
                display: inline-grid;
                width: 36px;
                height: 36px;
                place-items: center;
                border: 0;
                border-radius: 50%;
                background: transparent;
                color: #2481cc;
                font-size: 22px;
            }
            .messages { padding: 16px 12px; }
            .bubble { max-width: 88%; }
            .composer { padding-bottom: max(12px, env(safe-area-inset-bottom)); }
        }
    </style>
</head>
<body>
    <section id="authView" class="auth">
        <h1>Мессенджер</h1>
        <p>Открытая демо-версия: данные могут пропасть. Не используй настоящий пароль и не отправляй личные данные.</p>
        <form id="authForm">
            <label for="authUsername">Имя пользователя</label>
            <input id="authUsername" minlength="3" maxlength="20" required
                   autocomplete="username" placeholder="Например, sasha_1">
            <label for="authPassword">Пароль</label>
            <input id="authPassword" type="password" minlength="8" maxlength="128"
                   required autocomplete="current-password" placeholder="Не короче 8 символов">
            <label class="show-password"><input id="showAuthPassword" type="checkbox"> Показать пароль</label>
            <button id="authSubmit" class="primary" type="submit">Войти</button>
        </form>
        <button id="authToggle" class="link-button" type="button">Создать аккаунт</button>
        <p id="authNotice" class="notice" role="status"></p>
    </section>

    <main id="chatApp" class="chat-app hidden">
        <aside class="sidebar">
            <div class="sidebar-top">
                <div class="sidebar-title">
                    <h1>Сообщения</h1>
                    <div class="sidebar-actions">
                        <button id="settingsOpen" class="link-button settings-button" type="button"
                                aria-label="Открыть настройки">⚙</button>
                        <button id="themeToggle" class="link-button" type="button"
                                aria-label="Включить тёмную тему" aria-pressed="false">🌙</button>
                        <button id="logoutButton" class="link-button" type="button">Выйти</button>
                    </div>
                </div>
                <input id="userSearch" type="search" placeholder="Найти пользователя"
                       aria-label="Найти пользователя" maxlength="20" autocomplete="off">
                <div class="group-actions">
                    <button id="groupCreate" type="button">＋ Создать группу</button>
                    <button id="groupJoin" type="button">Войти по коду</button>
                </div>
            </div>
            <div id="groupList" class="group-list" aria-label="Мои группы"></div>
            <div id="userList" class="user-list"></div>
        </aside>
        <section class="chat">
            <header class="chat-header">
                <button id="backButton" class="mobile-back" type="button" aria-label="Назад">‹</button>
                <div id="chatAvatar" class="avatar">?</div>
                <div>
                    <strong id="chatTitle">Выбери собеседника</strong>
                    <small id="chatSubtitle">Личные сообщения</small>
                </div>
                <div id="callActions" class="call-actions hidden" aria-label="Позвонить">
                    <button id="callAudio" type="button" aria-label="Голосовой звонок" title="Голосовой звонок">☎</button>
                    <button id="callVideo" type="button" aria-label="Видеозвонок" title="Видеозвонок">▣</button>
                </div>
            </header>
            <div id="messages" class="messages" aria-live="polite">
                <div class="empty-chat">Выбери пользователя слева, чтобы открыть переписку.</div>
            </div>
            <form id="messageForm" class="composer hidden">
                <button id="emojiToggle" class="tool-button" type="button" aria-label="Добавить смайлик">😊</button>
                <button id="pollOpen" class="tool-button" type="button" aria-label="Создать опрос">▤</button>
                <label class="tool-button" for="mediaInput" aria-label="Прикрепить фото или видео" title="Фото или видео до 20 МБ">＋</label>
                <input id="mediaInput" class="attachment-input" type="file"
                       accept="image/jpeg,image/png,image/gif,image/webp,video/mp4,video/webm">
                <input id="messageInput" maxlength="1000" autocomplete="off"
                       placeholder="Сообщение" aria-label="Сообщение" required>
                <button class="send-button" type="submit" aria-label="Отправить">➤</button>
                <div id="emojiPicker" class="emoji-picker hidden" aria-label="Выбери смайлик"></div>
            </form>
        </section>
    </main>

    <dialog id="callDialog" class="settings-dialog call-dialog" aria-labelledby="callTitle">
        <div class="settings-header">
            <h2 id="callTitle">Звонок</h2>
        </div>
        <div class="call-videos">
            <video id="remoteVideo" autoplay playsinline></video>
            <video id="localVideo" autoplay muted playsinline></video>
        </div>
        <p id="callStatus" class="call-status" role="status"></p>
        <p class="settings-note" style="padding: 0 20px;">
            Звонки идут напрямую между браузерами; собеседник может видеть сетевой IP. Для части сетей нужен TURN-сервер.
        </p>
        <div class="call-controls">
            <button id="screenShare" type="button">Показать экран</button>
            <button id="callEnd" type="button">Завершить звонок</button>
        </div>
    </dialog>

    <dialog id="groupDialog" class="settings-dialog" aria-labelledby="groupDialogTitle">
        <div class="settings-header">
            <h2 id="groupDialogTitle">Группы</h2>
            <button id="groupDialogClose" class="settings-close" type="button" aria-label="Закрыть">×</button>
        </div>
        <div class="settings-content">
            <section class="settings-section">
                <h3>Создать группу</h3>
                <label for="groupNameInput">Название (от 3 до 40 символов)</label>
                <input id="groupNameInput" maxlength="40" autocomplete="off">
                <button id="groupCreateSubmit" type="button">Создать</button>
            </section>
            <section class="settings-section">
                <h3>Войти в группу</h3>
                <label for="groupInviteInput">Код приглашения</label>
                <input id="groupInviteInput" maxlength="20" autocomplete="off">
                <button id="groupJoinSubmit" type="button">Войти</button>
            </section>
            <p id="groupStatus" class="settings-status" role="status"></p>
        </div>
    </dialog>

    <dialog id="pollDialog" class="settings-dialog poll-compose-dialog" aria-labelledby="pollTitle">
        <div class="settings-header">
            <h2 id="pollTitle">Новый опрос</h2>
            <button id="pollClose" class="settings-close" type="button" aria-label="Закрыть">×</button>
        </div>
        <form id="pollForm" class="settings-content">
            <section class="settings-section">
                <label for="pollQuestion">Вопрос</label>
                <input id="pollQuestion" maxlength="200" required placeholder="О чём спросим?">
            </section>
            <section class="settings-section">
                <h3>Варианты ответа</h3>
                <div class="poll-options-input">
                    <input class="poll-option-input" maxlength="100" required placeholder="Вариант 1">
                    <input class="poll-option-input" maxlength="100" required placeholder="Вариант 2">
                    <input class="poll-option-input" maxlength="100" placeholder="Вариант 3 (необязательно)">
                    <input class="poll-option-input" maxlength="100" placeholder="Вариант 4 (необязательно)">
                    <input class="poll-option-input" maxlength="100" placeholder="Вариант 5 (необязательно)">
                    <input class="poll-option-input" maxlength="100" placeholder="Вариант 6 (необязательно)">
                </div>
                <p class="settings-note">Можно выбрать несколько ответов. Опрос виден только собеседникам этого чата.</p>
            </section>
            <button class="primary" type="submit">Отправить опрос</button>
            <p id="pollStatus" class="settings-status" role="status"></p>
        </form>
    </dialog>

    <dialog id="settingsDialog" class="settings-dialog" aria-labelledby="settingsTitle">
        <div class="settings-header">
            <h2 id="settingsTitle">Настройки</h2>
            <button id="settingsClose" class="settings-close" type="button" aria-label="Закрыть">×</button>
        </div>
        <div class="settings-content">
            <section class="settings-section">
                <h3>Профиль</h3>
                <label for="displayNameInput">Отображаемое имя</label>
                <input id="displayNameInput" maxlength="30" autocomplete="nickname">
                <p class="settings-note">Имя для собеседников. Имя входа не меняется.</p>
                <button id="saveProfile" type="button">Сохранить имя</button>
                <p id="profileStatus" class="settings-status" role="status"></p>
            </section>
            <section class="settings-section">
                <h3>Внешний вид</h3>
                <label for="themeSelect">Тема</label>
                <select id="themeSelect">
                    <option value="light">Светлая</option>
                    <option value="dark">Тёмная</option>
                </select>
                <label for="fontSizeSelect">Размер текста</label>
                <select id="fontSizeSelect">
                    <option value="small">Маленький</option>
                    <option value="medium">Обычный</option>
                    <option value="large">Большой</option>
                </select>
            </section>
            <section class="settings-section">
                <h3>Уведомления</h3>
                <p class="settings-note">Браузер покажет уведомление о новом сообщении, когда вкладка неактивна. Нужна поддержка уведомлений и разрешение браузера.</p>
                <button id="notificationsToggle" type="button">Включить уведомления</button>
                <p id="notificationsStatus" class="settings-status" role="status"></p>
            </section>
            <section class="settings-section">
                <h3>Смена пароля</h3>
                <label class="show-password"><input id="showSettingsPasswords" type="checkbox"> Показать пароли</label>
                <label for="oldPasswordInput">Текущий пароль</label>
                <input id="oldPasswordInput" type="password" maxlength="128" autocomplete="current-password">
                <label for="newPasswordInput">Новый пароль</label>
                <input id="newPasswordInput" type="password" minlength="8" maxlength="128" autocomplete="new-password">
                <label for="confirmPasswordInput">Повтори новый пароль</label>
                <input id="confirmPasswordInput" type="password" minlength="8" maxlength="128" autocomplete="new-password">
                <button id="changePassword" type="button">Сменить пароль</button>
                <p id="passwordStatus" class="settings-status" role="status"></p>
            </section>
        </div>
    </dialog>

    <script>
        const authView = document.getElementById("authView");
        const authForm = document.getElementById("authForm");
        const authNotice = document.getElementById("authNotice");
        const authToggle = document.getElementById("authToggle");
        const authSubmit = document.getElementById("authSubmit");
        const authUsername = document.getElementById("authUsername");
        const authPassword = document.getElementById("authPassword");
        const showAuthPassword = document.getElementById("showAuthPassword");
        const chatApp = document.getElementById("chatApp");
        const userList = document.getElementById("userList");
        const messagesBox = document.getElementById("messages");
        const messageForm = document.getElementById("messageForm");
        const messageInput = document.getElementById("messageInput");
        const chatTitle = document.getElementById("chatTitle");
        const chatSubtitle = document.getElementById("chatSubtitle");
        const chatAvatar = document.getElementById("chatAvatar");
        const groupList = document.getElementById("groupList");
        let isRegistering = false;
        let currentUser = null;
        let activeChat = null;
        let activeChatType = null;
        let activeGroup = null;
        let lastMessageId = 0;
        let pollTimer = null;
        let loadingMessages = false;
        let knownUsers = [];
        let knownGroups = [];
        let searchTimer = null;
        let searchRequest = 0;
        let incomingCallTimer = null;
        let activeCall = null;
        let peerConnection = null;
        let localStream = null;
        let screenStream = null;
        let cameraTrack = null;
        let pendingIceCandidates = [];
        let callSignalCursor = 0;
        let callPollTimer = null;
        const processedIncomingCalls = new Set();
        const themeToggle = document.getElementById("themeToggle");
        const settingsDialog = document.getElementById("settingsDialog");
        const displayNameInput = document.getElementById("displayNameInput");
        const profileStatus = document.getElementById("profileStatus");
        const notificationsToggle = document.getElementById("notificationsToggle");
        const notificationsStatus = document.getElementById("notificationsStatus");
        const pollDialog = document.getElementById("pollDialog");
        const emojiPicker = document.getElementById("emojiPicker");
        const mediaInput = document.getElementById("mediaInput");
        const callDialog = document.getElementById("callDialog");
        const groupDialog = document.getElementById("groupDialog");
        const callStatus = document.getElementById("callStatus");
        const MESSAGE_EMOJIS = ["😀", "😂", "😊", "😍", "🥰", "😎", "😭", "😡", "👍", "👎", "❤️", "🎉", "🔥", "🙏", "🤔", "✅", "👋", "💯"];

        async function api(path, options) {
            let response;
            try {
                response = await fetch(path, options);
            } catch (error) {
                throw new Error("Не удалось связаться с сервером. Проверь, что программа запущена и интернет работает.");
            }
            let data;
            try {
                data = await response.json();
            } catch (error) {
                throw new Error("Сервер вернул непонятный ответ. Попробуй обновить страницу.");
            }
            if (!response.ok) {
                throw new Error(data && typeof data.error === "string"
                    ? data.error : "Запрос не выполнен.");
            }
            return data;
        }

        function showAuth(message) {
            currentUser = null;
            activeChat = null;
            activeChatType = null;
            activeGroup = null;
            isRegistering = false;
            authSubmit.textContent = "Войти";
            authToggle.textContent = "Создать аккаунт";
            authPassword.value = "";
            authPassword.type = "password";
            authPassword.autocomplete = "current-password";
            showAuthPassword.checked = false;
            if (settingsDialog.open) settingsDialog.close();
            if (incomingCallTimer) window.clearTimeout(incomingCallTimer);
            if (pollTimer) window.clearTimeout(pollTimer);
            if (callPollTimer) window.clearTimeout(callPollTimer);
            cleanupCall();
            chatApp.classList.add("hidden");
            authView.classList.remove("hidden");
            authNotice.textContent = message || "";
        }

        function showChatApp(user) {
            currentUser = user;
            authView.classList.add("hidden");
            chatApp.classList.remove("hidden");
            document.getElementById("userSearch").value = "";
            knownUsers = [];
            activeChat = null;
            activeChatType = null;
            activeGroup = null;
            displayNameInput.value = user.display_name || user.username;
            updateNotificationsButton();
            renderUsers("Введи не меньше 2 символов, чтобы найти пользователя.");
            loadGroups().catch(error => window.alert(error.message));
            pollIncomingCalls();
        }

        authToggle.addEventListener("click", function () {
            isRegistering = !isRegistering;
            authSubmit.textContent = isRegistering ? "Создать аккаунт" : "Войти";
            authToggle.textContent = isRegistering ? "Уже есть аккаунт? Войти" : "Создать аккаунт";
            authPassword.autocomplete = isRegistering ? "new-password" : "current-password";
            authNotice.textContent = "";
        });

        showAuthPassword.addEventListener("change", function () {
            authPassword.type = this.checked ? "text" : "password";
        });
        document.getElementById("showSettingsPasswords").addEventListener("change", function () {
            const type = this.checked ? "text" : "password";
            document.querySelectorAll("#oldPasswordInput, #newPasswordInput, #confirmPasswordInput")
                .forEach(input => { input.type = type; });
        });

        authForm.addEventListener("submit", async function (event) {
            event.preventDefault();
            authSubmit.disabled = true;
            authNotice.textContent = "Проверяю данные...";
            try {
                const user = await api(isRegistering ? "/api/register" : "/api/login", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({
                        username: authUsername.value.trim(),
                        password: authPassword.value
                    })
                });
                authPassword.value = "";
                showChatApp(user);
            } catch (error) {
                authNotice.textContent = error.message;
            } finally {
                authSubmit.disabled = false;
            }
        });

        async function searchUsers() {
            const query = document.getElementById("userSearch").value.trim();
            const requestId = ++searchRequest;
            if (query.length < 2) {
                knownUsers = [];
                renderUsers(query ? "Введи ещё хотя бы один символ." : "Введи имя пользователя для поиска.");
                return;
            }

            try {
                knownUsers = await api("/api/users?search=" + encodeURIComponent(query));
                if (requestId !== searchRequest) return;
                renderUsers(knownUsers.length ? "" : "Пользователь не найден.");
            } catch (error) {
                if (requestId === searchRequest) {
                    knownUsers = [];
                    renderUsers(error.message);
                    if (error.message === "Нужно войти в аккаунт.") showAuth(error.message);
                }
            }
        }

        function renderUsers(emptyMessage) {
            userList.replaceChildren();

            if (knownUsers.length === 0) {
                const empty = document.createElement("p");
                empty.className = "empty-chat";
                empty.textContent = emptyMessage || "Пользователь не найден.";
                userList.append(empty);
                return;
            }

            for (const user of knownUsers) {
                const button = document.createElement("button");
                button.type = "button";
                button.className = "user-row" + (activeChat === user.username ? " selected" : "");
                const avatar = document.createElement("span");
                avatar.className = "avatar";
                avatar.textContent = user.username.charAt(0).toUpperCase();
                const info = document.createElement("span");
                info.className = "user-info";
                const name = document.createElement("span");
                name.className = "user-name";
                name.textContent = user.display_name || user.username;
                const preview = document.createElement("span");
                preview.className = "user-last";
                preview.textContent = user.last_message
                    ? "@" + user.username + " · " + user.last_message
                    : "@" + user.username;
                info.append(name, preview);
                button.append(avatar, info);
                button.addEventListener("click", function () {
                    openChat(user.username, user.display_name);
                });
                userList.append(button);
            }
        }

        function renderGroups() {
            groupList.replaceChildren();
            for (const group of knownGroups) {
                const button = document.createElement("button");
                button.type = "button";
                button.className = "user-row group-row"
                    + (activeChat === "group:" + group.id ? " selected" : "");
                const avatar = document.createElement("span");
                avatar.className = "avatar";
                avatar.textContent = group.name.charAt(0).toUpperCase();
                const info = document.createElement("span");
                info.className = "user-info";
                const name = document.createElement("span");
                name.className = "user-name";
                name.textContent = group.name;
                const details = document.createElement("span");
                details.className = "user-last";
                details.textContent = "Код " + group.invite_code + " · " + group.member_count + " участников";
                info.append(name, details);
                button.append(avatar, info);
                button.addEventListener("click", function () { openGroup(group); });
                groupList.append(button);
            }
        }

        async function loadGroups() {
            knownGroups = await api("/api/groups");
            renderGroups();
        }

        document.getElementById("groupCreate").addEventListener("click", function () {
            document.getElementById("groupStatus").textContent = "";
            groupDialog.showModal();
            document.getElementById("groupNameInput").focus();
        });

        document.getElementById("groupJoin").addEventListener("click", function () {
            document.getElementById("groupStatus").textContent = "";
            groupDialog.showModal();
            document.getElementById("groupInviteInput").focus();
        });

        document.getElementById("groupDialogClose").addEventListener("click", function () {
            groupDialog.close();
        });

        document.getElementById("groupCreateSubmit").addEventListener("click", async function () {
            const name = document.getElementById("groupNameInput").value.trim();
            const status = document.getElementById("groupStatus");
            if (!name) {
                status.textContent = "Введи название группы.";
                return;
            }
            try {
                const group = await api("/api/groups", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ name })
                });
                await loadGroups();
                await openGroup(knownGroups.find(item => item.id === group.id) || group);
                status.textContent = "Группа создана. Код приглашения: " + group.invite_code;
            } catch (error) {
                status.textContent = error.message;
            }
        });

        document.getElementById("groupJoinSubmit").addEventListener("click", async function () {
            const inviteCode = document.getElementById("groupInviteInput").value.trim();
            const status = document.getElementById("groupStatus");
            if (!inviteCode) {
                status.textContent = "Введи код приглашения.";
                return;
            }
            try {
                const group = await api("/api/groups/join", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ invite_code: inviteCode })
                });
                await loadGroups();
                await openGroup(knownGroups.find(item => item.id === group.id) || group);
                status.textContent = "Ты вошёл в группу.";
            } catch (error) {
                status.textContent = error.message;
            }
        });

        async function openChat(username, displayName) {
            if (pollTimer) window.clearTimeout(pollTimer);
            activeChat = username;
            activeChatType = "user";
            activeGroup = null;
            lastMessageId = 0;
            chatTitle.textContent = displayName || username;
            chatSubtitle.textContent = "Личные сообщения";
            chatAvatar.textContent = (displayName || username).charAt(0).toUpperCase();
            document.getElementById("callActions").classList.remove("hidden");
            messageForm.classList.remove("hidden");
            chatApp.classList.add("chat-open");
            renderUsers();
            renderGroups();
            messagesBox.replaceChildren();
            await loadMessages(true);
            messageInput.focus();
        }

        async function openGroup(group) {
            if (pollTimer) window.clearTimeout(pollTimer);
            activeChat = "group:" + group.id;
            activeChatType = "group";
            activeGroup = group;
            lastMessageId = 0;
            chatTitle.textContent = group.name;
            chatSubtitle.textContent = "Группа · " + (group.member_count || 1) + " участников";
            chatAvatar.textContent = group.name.charAt(0).toUpperCase();
            document.getElementById("callActions").classList.add("hidden");
            messageForm.classList.remove("hidden");
            chatApp.classList.add("chat-open");
            renderUsers();
            renderGroups();
            messagesBox.replaceChildren();
            await loadMessages(true);
            messageInput.focus();
        }

        function callSignalUrl(callId) {
            return "/api/calls/" + encodeURIComponent(callId) + "/signal";
        }

        async function sendCallSignal(kind, payload) {
            if (!activeCall) return;
            await api(callSignalUrl(activeCall.id), {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ kind, payload: payload || {} })
            });
        }

        function showCallDialog(call, status) {
            document.getElementById("callTitle").textContent =
                (call.mode === "video" ? "Видеозвонок" : "Голосовой звонок")
                + (call.peer ? " · " + call.peer : "");
            callStatus.textContent = status;
            document.getElementById("screenShare").disabled = true;
            if (!callDialog.open) callDialog.showModal();
        }

        async function startCall(mode) {
            if (!activeChat || activeChatType !== "user" || activeCall) return;
            try {
                const call = await api("/api/calls", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ to: activeChat, mode })
                });
                activeCall = {
                    id: call.id, mode, peer: activeChat, isCaller: true
                };
                callSignalCursor = 0;
                pendingIceCandidates = [];
                showCallDialog(activeCall, "Звоним. Ждём ответа...");
                pollActiveCall();
            } catch (error) {
                window.alert(error.message);
            }
        }

        document.getElementById("callAudio").addEventListener("click", function () {
            startCall("audio");
        });
        document.getElementById("callVideo").addEventListener("click", function () {
            startCall("video");
        });

        async function processIncomingCall(call) {
            processedIncomingCalls.add(call.id);
            const accept = window.confirm(
                (call.mode === "video" ? "Видеозвонок" : "Голосовой звонок")
                + " от " + call.display_name + " (@" + call.username + "). Ответить?"
            );
            if (!accept) {
                try {
                    await api(callSignalUrl(call.id), {
                        method: "POST",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({ kind: "reject" })
                    });
                } catch (error) {
                    window.alert(error.message);
                }
                return;
            }

            activeCall = {
                id: call.id, mode: call.mode, peer: call.username, isCaller: false
            };
            callSignalCursor = 0;
            pendingIceCandidates = [];
            showCallDialog(activeCall, "Подключаем камеру и микрофон...");
            try {
                await sendCallSignal("accept");
                await setupPeerConnection();
                pollActiveCall();
            } catch (error) {
                callStatus.textContent = error.message;
                try {
                    await sendCallSignal("end");
                } catch (signalError) {
                    callStatus.textContent = error.message + " " + signalError.message;
                }
                cleanupCall();
            }
        }

        async function pollIncomingCalls() {
            if (!currentUser) return;
            try {
                const calls = await api("/api/calls/incoming");
                const incoming = calls.find(call => !processedIncomingCalls.has(call.id));
                if (incoming && !activeCall) await processIncomingCall(incoming);
            } catch (error) {
                if (error.message === "Нужно войти в аккаунт.") {
                    showAuth(error.message);
                    return;
                }
            } finally {
                if (currentUser) {
                    incomingCallTimer = window.setTimeout(pollIncomingCalls, 2500);
                }
            }
        }

        async function setupPeerConnection() {
            if (!activeCall || peerConnection) return;
            if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
                throw new Error("Браузер не поддерживает звонки. Открой сайт по HTTPS.");
            }
            localStream = await navigator.mediaDevices.getUserMedia({
                audio: true,
                video: activeCall.mode === "video"
            });
            cameraTrack = localStream.getVideoTracks()[0] || null;
            document.getElementById("localVideo").srcObject = localStream;
            peerConnection = new RTCPeerConnection({
                iceServers: [{ urls: "stun:stun.l.google.com:19302" }]
            });
            document.getElementById("screenShare").disabled = activeCall.mode !== "video";
            for (const track of localStream.getTracks()) {
                peerConnection.addTrack(track, localStream);
            }
            peerConnection.ontrack = function (event) {
                document.getElementById("remoteVideo").srcObject =
                    event.streams[0] || new MediaStream([event.track]);
                callStatus.textContent = "Соединение установлено.";
            };
            peerConnection.onicecandidate = function (event) {
                if (event.candidate) {
                    sendCallSignal("ice", event.candidate.toJSON()).catch(error => {
                        callStatus.textContent = error.message;
                    });
                }
            };
            peerConnection.onconnectionstatechange = function () {
                if (peerConnection && peerConnection.connectionState === "failed") {
                    callStatus.textContent =
                        "Не удалось соединиться. Сеть может требовать TURN-сервер.";
                }
            };

            if (activeCall.isCaller) {
                const offer = await peerConnection.createOffer();
                await peerConnection.setLocalDescription(offer);
                await sendCallSignal("offer", peerConnection.localDescription.toJSON());
                callStatus.textContent = "Соединяемся...";
            } else {
                callStatus.textContent = "Ожидаем соединение...";
            }
        }

        async function applyPendingIceCandidates() {
            if (!peerConnection || !peerConnection.remoteDescription) return;
            const candidates = pendingIceCandidates;
            pendingIceCandidates = [];
            for (const candidate of candidates) {
                await peerConnection.addIceCandidate(candidate);
            }
        }

        async function receiveCallSignal(signal) {
            if (!peerConnection && signal.kind === "offer") {
                await setupPeerConnection();
            }
            if (signal.kind === "ice") {
                if (peerConnection && peerConnection.remoteDescription) {
                    await peerConnection.addIceCandidate(signal.payload);
                } else {
                    pendingIceCandidates.push(signal.payload);
                }
                return;
            }
            if (signal.kind === "offer" && peerConnection) {
                await peerConnection.setRemoteDescription(signal.payload);
                await applyPendingIceCandidates();
                const answer = await peerConnection.createAnswer();
                await peerConnection.setLocalDescription(answer);
                await sendCallSignal("answer", peerConnection.localDescription.toJSON());
                callStatus.textContent = "Соединяемся...";
            } else if (signal.kind === "answer" && peerConnection) {
                await peerConnection.setRemoteDescription(signal.payload);
                await applyPendingIceCandidates();
            }
        }

        async function pollActiveCall() {
            if (!activeCall) return;
            const call = activeCall;
            try {
                const state = await api(
                    "/api/calls/" + encodeURIComponent(call.id)
                    + "?after=" + callSignalCursor
                );
                if (!activeCall || activeCall.id !== call.id) return;
                if (state.status === "rejected") {
                    callStatus.textContent = "Звонок отклонён.";
                    cleanupCall();
                    return;
                }
                if (state.status === "ended") {
                    callStatus.textContent = "Звонок завершён.";
                    cleanupCall();
                    return;
                }
                if (state.status === "accepted" && call.isCaller && !peerConnection) {
                    call.accepted = true;
                    await setupPeerConnection();
                }
                for (const signal of state.signals) {
                    callSignalCursor = Math.max(callSignalCursor, signal.id);
                    await receiveCallSignal(signal);
                }
            } catch (error) {
                if (call.isCaller && call.accepted && !peerConnection) {
                    try {
                        await sendCallSignal("end");
                    } catch (signalError) {
                        callStatus.textContent = signalError.message;
                    }
                    window.alert(error.message);
                    cleanupCall();
                    return;
                }
                if (error.message === "Звонок не найден.") {
                    callStatus.textContent = error.message;
                    cleanupCall();
                    return;
                }
                callStatus.textContent = error.message;
            } finally {
                if (activeCall && activeCall.id === call.id) {
                    callPollTimer = window.setTimeout(pollActiveCall, 900);
                }
            }
        }

        function cleanupCall() {
            if (callPollTimer) window.clearTimeout(callPollTimer);
            callPollTimer = null;
            if (peerConnection) peerConnection.close();
            peerConnection = null;
            if (localStream) {
                for (const track of localStream.getTracks()) track.stop();
            }
            if (screenStream) {
                for (const track of screenStream.getTracks()) track.stop();
            }
            localStream = null;
            screenStream = null;
            cameraTrack = null;
            pendingIceCandidates = [];
            document.getElementById("localVideo").srcObject = null;
            document.getElementById("remoteVideo").srcObject = null;
            activeCall = null;
            if (callDialog.open) callDialog.close();
        }

        document.getElementById("callEnd").addEventListener("click", async function () {
            if (activeCall) {
                try {
                    await sendCallSignal("end");
                } catch (error) {
                    window.alert(error.message);
                }
            }
            cleanupCall();
        });

        document.getElementById("screenShare").addEventListener("click", async function () {
            if (!activeCall || activeCall.mode !== "video" || !peerConnection) return;
            if (!navigator.mediaDevices || !navigator.mediaDevices.getDisplayMedia) {
                callStatus.textContent = "Браузер не поддерживает демонстрацию экрана.";
                return;
            }
            try {
                screenStream = await navigator.mediaDevices.getDisplayMedia({ video: true });
                const screenTrack = screenStream.getVideoTracks()[0];
                const videoSender = peerConnection.getSenders()
                    .find(sender => sender.track && sender.track.kind === "video");
                if (!screenTrack || !videoSender) {
                    screenStream.getTracks().forEach(track => track.stop());
                    screenStream = null;
                    callStatus.textContent = "Не удалось включить демонстрацию экрана.";
                    return;
                }
                await videoSender.replaceTrack(screenTrack);
                document.getElementById("localVideo").srcObject = screenStream;
                screenTrack.onended = async function () {
                    if (peerConnection && cameraTrack && videoSender.track === screenTrack) {
                        await videoSender.replaceTrack(cameraTrack);
                        document.getElementById("localVideo").srcObject = localStream;
                    }
                    screenStream = null;
                };
                callStatus.textContent = "Ты показываешь экран.";
            } catch (error) {
                callStatus.textContent = "Не удалось показать экран: " + error.message;
            }
        });

        function appendMessage(message) {
            const bubble = document.createElement("article");
            bubble.className = "bubble" + (message.sender === currentUser.username ? " mine" : "");
            bubble.dataset.messageId = String(message.id);
            if (message.sender !== currentUser.username && message.sender_display) {
                const senderName = document.createElement("strong");
                senderName.textContent = message.sender_display;
                senderName.style.display = "block";
                senderName.style.marginBottom = "4px";
                bubble.append(senderName);
            }
            if (message.kind === "media") {
                bubble.classList.add("media-message");
                const url = "/api/media?id=" + encodeURIComponent(message.id);
                if (message.media_type.startsWith("image/")) {
                    const image = document.createElement("img");
                    image.src = url;
                    image.alt = message.media_name || "Изображение";
                    image.loading = "lazy";
                    bubble.append(image);
                } else {
                    const video = document.createElement("video");
                    video.src = url;
                    video.controls = true;
                    video.preload = "metadata";
                    video.setAttribute("aria-label", message.media_name || "Видео");
                    bubble.append(video);
                }
                const fileName = document.createElement("div");
                fileName.className = "media-name";
                fileName.textContent = message.media_name || "Медиафайл";
                bubble.append(fileName);
            } else if (message.kind === "poll" && message.poll) {
                bubble.append(buildPollCard(message.poll));
            } else {
                const text = document.createElement("div");
                text.textContent = message.text;
                bubble.append(text);
            }
            const time = document.createElement("div");
            time.className = "bubble-time";
            time.textContent = message.time;
            bubble.append(time);
            messagesBox.append(bubble);
        }

        function buildPollCard(poll) {
            const card = document.createElement("section");
            card.className = "poll-card";
            card.dataset.pollId = String(poll.id);
            const question = document.createElement("div");
            question.className = "poll-question";
            question.textContent = poll.question;
            card.append(question);
            const totalVotes = poll.options.reduce((total, option) => total + option.votes, 0);

            for (const option of poll.options) {
                const label = document.createElement("label");
                label.className = "poll-option";
                const checkbox = document.createElement("input");
                checkbox.type = "checkbox";
                checkbox.value = String(option.id);
                checkbox.checked = poll.selected.includes(option.id);
                const choice = document.createElement("span");
                choice.textContent = option.text + " (" + option.votes + ")";
                label.append(checkbox, choice);
                card.append(label);
                const bar = document.createElement("div");
                bar.className = "poll-bar";
                const fill = document.createElement("span");
                fill.style.width = (totalVotes ? option.votes / totalVotes * 100 : 0) + "%";
                bar.append(fill);
                card.append(bar);
            }

            const voteButton = document.createElement("button");
            voteButton.className = "poll-vote-button";
            voteButton.type = "button";
            voteButton.textContent = "Голосовать";
            voteButton.addEventListener("click", async function () {
                const optionIds = Array.from(card.querySelectorAll('input[type="checkbox"]:checked'))
                    .map(input => Number(input.value));
                if (optionIds.length === 0) {
                    window.alert("Выбери хотя бы один вариант.");
                    return;
                }
                voteButton.disabled = true;
                try {
                    await api("/api/polls/" + poll.id + "/vote", {
                        method: "POST",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({ option_ids: optionIds })
                    });
                    await refreshPolls();
                } catch (error) {
                    window.alert(error.message);
                } finally {
                    voteButton.disabled = false;
                }
            });
            card.append(voteButton);
            const footer = document.createElement("div");
            footer.className = "poll-footer";
            footer.textContent = totalVotes + (totalVotes === 1 ? " голос" : " голосов") + " · можно выбрать несколько";
            card.append(footer);
            return card;
        }

        async function refreshPolls() {
            if (!activeChat) return;
            const polls = await api("/api/polls?with=" + encodeURIComponent(activeChat));
            for (const poll of polls) {
                const oldCard = messagesBox.querySelector('[data-poll-id="' + poll.id + '"]');
                if (oldCard) oldCard.replaceWith(buildPollCard(poll));
            }
        }

        async function loadMessages(initial) {
            if (!activeChat || loadingMessages) return;
            const requestedChat = activeChat;
            loadingMessages = true;
            try {
                const params = new URLSearchParams({
                    with: requestedChat,
                    after: initial ? "0" : String(lastMessageId)
                });
                const newMessages = await api("/api/messages?" + params.toString());
                if (requestedChat !== activeChat) return;
                for (const message of newMessages) {
                    appendMessage(message);
                    lastMessageId = Math.max(lastMessageId, message.id);
                    if (!initial && message.sender !== currentUser.username) {
                        notifyNewMessage(message);
                    }
                }
                if (newMessages.length > 0) messagesBox.scrollTop = messagesBox.scrollHeight;
                if (!initial) await refreshPolls();
                chatSubtitle.textContent = activeChatType === "group"
                    ? "Группа · " + (activeGroup ? activeGroup.member_count : 1) + " участников"
                    : "Личные сообщения";
            } catch (error) {
                if (error.message === "Нужно войти в аккаунт.") showAuth(error.message);
                else if (requestedChat === activeChat) {
                    chatSubtitle.textContent = "Не удалось обновить: " + error.message;
                }
            } finally {
                loadingMessages = false;
                if (activeChat) {
                    if (pollTimer) window.clearTimeout(pollTimer);
                    pollTimer = window.setTimeout(function () { loadMessages(false); }, 1200);
                }
            }
        }

        messageForm.addEventListener("submit", async function (event) {
            event.preventDefault();
            const text = messageInput.value.trim();
            if (!text || !activeChat) return;
            const submit = messageForm.querySelector("button");
            submit.disabled = true;
            try {
                await api("/api/messages", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ to: activeChat, text })
                });
                messageInput.value = "";
                await loadMessages(false);
                const selectedUser = knownUsers.find(user => user.username === activeChat);
                if (selectedUser) selectedUser.last_message = text;
                renderUsers();
                messageInput.focus();
            } catch (error) {
                window.alert(error.message);
            } finally {
                submit.disabled = false;
            }
        });

        document.getElementById("userSearch").addEventListener("input", function () {
            if (searchTimer) window.clearTimeout(searchTimer);
            searchTimer = window.setTimeout(searchUsers, 250);
        });
        for (const emoji of MESSAGE_EMOJIS) {
            const emojiButton = document.createElement("button");
            emojiButton.type = "button";
            emojiButton.textContent = emoji;
            emojiButton.setAttribute("aria-label", "Вставить " + emoji);
            emojiButton.addEventListener("click", function () {
                const start = messageInput.selectionStart;
                const end = messageInput.selectionEnd;
                messageInput.setRangeText(emoji, start, end, "end");
                messageInput.focus();
            });
            emojiPicker.append(emojiButton);
        }
        document.getElementById("emojiToggle").addEventListener("click", function () {
            emojiPicker.classList.toggle("hidden");
        });
        messageInput.addEventListener("input", function () {
            emojiPicker.classList.add("hidden");
        });
        document.getElementById("pollOpen").addEventListener("click", function () {
            if (!activeChat) return;
            document.getElementById("pollStatus").textContent = "";
            document.getElementById("pollForm").reset();
            pollDialog.showModal();
        });
        document.getElementById("pollClose").addEventListener("click", function () {
            pollDialog.close();
        });
        document.getElementById("pollForm").addEventListener("submit", async function (event) {
            event.preventDefault();
            if (!activeChat) return;
            const optionInputs = Array.from(document.querySelectorAll(".poll-option-input"));
            const options = optionInputs.map(input => input.value.trim()).filter(Boolean);
            const question = document.getElementById("pollQuestion").value.trim();
            if (options.length < 2) {
                document.getElementById("pollStatus").textContent = "Добавь хотя бы два варианта.";
                return;
            }
            try {
                await api("/api/polls", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ to: activeChat, question, options })
                });
                pollDialog.close();
                await loadMessages(false);
            } catch (error) {
                document.getElementById("pollStatus").textContent = error.message;
            }
        });

        async function uploadMedia(file) {
            if (!file) return;
            if (file.size > 20 * 1024 * 1024) {
                window.alert("Файл больше 20 МБ.");
                mediaInput.value = "";
                return;
            }
            if (!file.type.startsWith("image/") && !file.type.startsWith("video/")) {
                window.alert("Можно отправлять только фото или видео.");
                mediaInput.value = "";
                return;
            }
            if (!activeChat) {
                window.alert("Сначала открой чат с собеседником.");
                mediaInput.value = "";
                return;
            }

            const reader = new FileReader();
            reader.onerror = function () {
                window.alert("Не удалось прочитать файл.");
                mediaInput.value = "";
            };
            reader.onload = async function () {
                try {
                    const encoded = String(reader.result).split(",", 2)[1];
                    await api("/api/media", {
                        method: "POST",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({
                            to: activeChat,
                            name: file.name,
                            mime: file.type,
                            data: encoded
                        })
                    });
                    await loadMessages(false);
                } catch (error) {
                    window.alert(error.message);
                } finally {
                    mediaInput.value = "";
                }
            };
            reader.readAsDataURL(file);
        }
        mediaInput.addEventListener("change", function () {
            uploadMedia(this.files[0]);
        });
        document.getElementById("backButton").addEventListener("click", function () {
            chatApp.classList.remove("chat-open");
        });

        function setDarkTheme(enabled) {
            document.body.classList.toggle("dark-theme", enabled);
            themeToggle.textContent = enabled ? "☀️" : "🌙";
            themeToggle.setAttribute("aria-pressed", String(enabled));
            themeToggle.setAttribute("aria-label", enabled ? "Включить светлую тему" : "Включить тёмную тему");
            localStorage.setItem("messenger-theme", enabled ? "dark" : "light");
            const themeSelect = document.getElementById("themeSelect");
            if (themeSelect) themeSelect.value = enabled ? "dark" : "light";
        }

        setDarkTheme(localStorage.getItem("messenger-theme") === "dark");
        themeToggle.addEventListener("click", function () {
            setDarkTheme(!document.body.classList.contains("dark-theme"));
        });
        document.getElementById("themeSelect").addEventListener("change", function () {
            setDarkTheme(this.value === "dark");
        });

        const savedFontSize = localStorage.getItem("messenger-font-size");
        document.documentElement.dataset.fontSize =
            ["small", "medium", "large"].includes(savedFontSize) ? savedFontSize : "medium";
        const fontSizeSelect = document.getElementById("fontSizeSelect");
        fontSizeSelect.value = document.documentElement.dataset.fontSize;
        fontSizeSelect.addEventListener("change", function () {
            document.documentElement.dataset.fontSize = this.value;
            localStorage.setItem("messenger-font-size", this.value);
        });

        document.getElementById("settingsOpen").addEventListener("click", async function () {
            profileStatus.textContent = "";
            notificationsStatus.textContent = "";
            document.getElementById("passwordStatus").textContent = "";
            try {
                currentUser = await api("/api/me");
                displayNameInput.value = currentUser.display_name || currentUser.username;
                settingsDialog.showModal();
            } catch (error) {
                showAuth(error.message);
            }
        });
        document.getElementById("settingsClose").addEventListener("click", function () {
            settingsDialog.close();
        });
        settingsDialog.addEventListener("click", function (event) {
            if (event.target === settingsDialog) settingsDialog.close();
        });

        document.getElementById("saveProfile").addEventListener("click", async function () {
            const displayName = displayNameInput.value.trim();
            if (!displayName || displayName.length > 30) {
                profileStatus.textContent = "Имя должно содержать от 1 до 30 символов.";
                return;
            }
            try {
                const user = await api("/api/profile", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ display_name: displayName })
                });
                currentUser = user;
                profileStatus.textContent = "Имя сохранено.";
                if (activeChat) {
                    const selectedUser = knownUsers.find(item => item.username === activeChat);
                    if (selectedUser) chatTitle.textContent = selectedUser.display_name || selectedUser.username;
                }
                searchUsers();
            } catch (error) {
                profileStatus.textContent = error.message;
            }
        });

        function updateNotificationsButton() {
            const enabled = localStorage.getItem("messenger-notifications") === "true";
            notificationsToggle.textContent = enabled ? "Выключить уведомления" : "Включить уведомления";
            if (!("Notification" in window)) {
                notificationsToggle.disabled = true;
                notificationsStatus.textContent = "Этот браузер не поддерживает уведомления.";
            } else if (Notification.permission === "denied") {
                notificationsToggle.disabled = true;
                notificationsStatus.textContent = "Уведомления запрещены в настройках браузера.";
            } else {
                notificationsToggle.disabled = false;
                notificationsStatus.textContent = enabled ? "Уведомления включены." : "";
            }
        }

        notificationsToggle.addEventListener("click", async function () {
            if (localStorage.getItem("messenger-notifications") === "true") {
                localStorage.setItem("messenger-notifications", "false");
                updateNotificationsButton();
                return;
            }
            if (!("Notification" in window)) {
                updateNotificationsButton();
                return;
            }
            try {
                const permission = Notification.permission === "default"
                    ? await Notification.requestPermission()
                    : Notification.permission;
                if (permission === "granted") {
                    localStorage.setItem("messenger-notifications", "true");
                } else {
                    notificationsStatus.textContent = "Браузер не разрешил уведомления.";
                }
            } catch (error) {
                notificationsStatus.textContent = "Не удалось запросить разрешение уведомлений.";
            }
            updateNotificationsButton();
        });

        function notifyNewMessage(message) {
            if (document.visibilityState === "visible"
                || localStorage.getItem("messenger-notifications") !== "true"
                || !("Notification" in window)
                || Notification.permission !== "granted") {
                return;
            }
            const notification = new Notification(message.sender_display || message.sender, {
                body: message.text,
                tag: "message-" + message.id
            });
            notification.onclick = function () {
                window.focus();
                notification.close();
            };
        }

        document.getElementById("changePassword").addEventListener("click", async function () {
            const oldPassword = document.getElementById("oldPasswordInput").value;
            const newPassword = document.getElementById("newPasswordInput").value;
            const confirmation = document.getElementById("confirmPasswordInput").value;
            const status = document.getElementById("passwordStatus");
            if (newPassword.length < 8 || newPassword.length > 128) {
                status.textContent = "Новый пароль должен содержать от 8 до 128 символов.";
                return;
            }
            if (newPassword !== confirmation) {
                status.textContent = "Новые пароли не совпадают.";
                return;
            }
            try {
                await api("/api/password", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ old_password: oldPassword, new_password: newPassword })
                });
                document.getElementById("oldPasswordInput").value = "";
                document.getElementById("newPasswordInput").value = "";
                document.getElementById("confirmPasswordInput").value = "";
                status.textContent = "Пароль изменён.";
            } catch (error) {
                status.textContent = error.message;
            }
        });

        document.getElementById("logoutButton").addEventListener("click", async function () {
            try {
                await api("/api/logout", { method: "POST" });
            } finally {
                showAuth("Ты вышел из аккаунта.");
            }
        });

        api("/api/me")
            .then(showChatApp)
            .catch(function () { showAuth(""); });
    </script>
</body>
</html>
"""


@contextmanager
def connect_database():
    connection = sqlite3.connect(DATABASE, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def initialize_database():
    with connect_database() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY,
                username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                password_salt BLOB NOT NULL,
                password_hash BLOB NOT NULL,
                display_name TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sender_id INTEGER NOT NULL REFERENCES users(id),
                recipient_id INTEGER NOT NULL REFERENCES users(id),
                text TEXT NOT NULL,
                created_at TEXT NOT NULL,
                kind TEXT NOT NULL DEFAULT 'text',
                media_name TEXT,
                media_type TEXT,
                media_data BLOB,
                group_id INTEGER REFERENCES chat_groups(id)
            );
            CREATE INDEX IF NOT EXISTS messages_between_users
                ON messages(sender_id, recipient_id, id);
            CREATE TABLE IF NOT EXISTS polls (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                message_id INTEGER NOT NULL UNIQUE REFERENCES messages(id) ON DELETE CASCADE,
                question TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS poll_options (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                poll_id INTEGER NOT NULL REFERENCES polls(id) ON DELETE CASCADE,
                text TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS poll_votes (
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                option_id INTEGER NOT NULL REFERENCES poll_options(id) ON DELETE CASCADE,
                PRIMARY KEY (user_id, option_id)
            );
            CREATE TABLE IF NOT EXISTS chat_groups (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                invite_code TEXT NOT NULL UNIQUE,
                created_by INTEGER NOT NULL REFERENCES users(id),
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS group_members (
                group_id INTEGER NOT NULL REFERENCES chat_groups(id) ON DELETE CASCADE,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                joined_at TEXT NOT NULL,
                PRIMARY KEY (group_id, user_id)
            );
            CREATE TABLE IF NOT EXISTS call_sessions (
                id TEXT PRIMARY KEY,
                caller_id INTEGER NOT NULL REFERENCES users(id),
                callee_id INTEGER NOT NULL REFERENCES users(id),
                mode TEXT NOT NULL CHECK (mode IN ('audio', 'video')),
                status TEXT NOT NULL CHECK (status IN ('ringing', 'accepted', 'rejected', 'ended')),
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS call_signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                call_id TEXT NOT NULL REFERENCES call_sessions(id) ON DELETE CASCADE,
                sender_id INTEGER NOT NULL REFERENCES users(id),
                kind TEXT NOT NULL,
                payload TEXT NOT NULL,
                created_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS call_signals_by_call
                ON call_signals(call_id, id);
            """
        )
        message_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(messages)")
        }
        for column, definition in (
            ("kind", "TEXT NOT NULL DEFAULT 'text'"),
            ("media_name", "TEXT"),
            ("media_type", "TEXT"),
            ("media_data", "BLOB"),
            ("group_id", "INTEGER REFERENCES chat_groups(id)"),
        ):
            if column not in message_columns:
                connection.execute(
                    "ALTER TABLE messages ADD COLUMN %s %s" % (column, definition)
                )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS group_messages_by_group ON messages(group_id, id)"
        )
        user_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(users)")
        }
        if "display_name" not in user_columns:
            connection.execute(
                "ALTER TABLE users ADD COLUMN display_name TEXT NOT NULL DEFAULT ''"
            )
        connection.execute(
            "UPDATE users SET display_name = username WHERE display_name = ''"
        )


def make_password_hash(password, salt):
    return hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, PASSWORD_ITERATIONS
    )


def valid_origin(handler):
    origin = handler.headers.get("Origin")
    return origin is not None and urlparse(origin).netloc == handler.headers.get("Host")


def load_poll(connection, poll_id, user_id):
    poll = connection.execute(
        """
        SELECT p.id, p.question
        FROM polls p
        JOIN messages m ON m.id = p.message_id
        WHERE p.id = ? AND (
            m.sender_id = ? OR m.recipient_id = ?
            OR EXISTS (
                SELECT 1 FROM group_members gm
                WHERE gm.group_id = m.group_id AND gm.user_id = ?
            )
        )
        """,
        (poll_id, user_id, user_id, user_id),
    ).fetchone()
    if poll is None:
        return None

    options = connection.execute(
        """
        SELECT o.id, o.text, COUNT(v.user_id) AS votes
        FROM poll_options o
        LEFT JOIN poll_votes v ON v.option_id = o.id
        WHERE o.poll_id = ?
        GROUP BY o.id
        ORDER BY o.id
        """,
        (poll_id,),
    ).fetchall()
    selected = connection.execute(
        """
        SELECT o.id
        FROM poll_options o
        JOIN poll_votes v ON v.option_id = o.id
        WHERE o.poll_id = ? AND v.user_id = ?
        """,
        (poll_id, user_id),
    ).fetchall()
    return {
        "id": poll["id"],
        "question": poll["question"],
        "options": [
            {"id": option["id"], "text": option["text"], "votes": option["votes"]}
            for option in options
        ],
        "selected": [option["id"] for option in selected],
    }


class ChatHandler(BaseHTTPRequestHandler):
    def send_json(self, status_code, data, extra_headers=None):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        if extra_headers:
            for name, value in extra_headers:
                self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def send_page(self):
        body = PAGE.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def read_json(self, max_bytes=8192):
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
            if content_length <= 0 or content_length > max_bytes:
                raise ValueError("Размер запроса неправильный.")
            data = json.loads(self.rfile.read(content_length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
            self.send_json(400, {"error": "Не удалось прочитать данные формы."})
            return None
        if not isinstance(data, dict):
            self.send_json(400, {"error": "Ожидались данные формы."})
            return None
        return data

    def get_session(self):
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get("Cookie", ""))
            token = cookie["session"].value
        except (KeyError, ValueError):
            return None

        now = time.time()
        with sessions_lock:
            session = sessions.get(token)
            if session is None:
                return None
            if session["expires"] < now:
                del sessions[token]
                return None
            session["expires"] = now + SESSION_TTL
            return session["user_id"]

    def set_session(self, user_id):
        token = secrets.token_urlsafe(32)
        with sessions_lock:
            sessions[token] = {"user_id": user_id, "expires": time.time() + SESSION_TTL}
        cookie = "session=%s; Path=/; HttpOnly; SameSite=Strict; Max-Age=%s" % (
            token, SESSION_TTL
        )
        if COOKIE_SECURE:
            cookie += "; Secure"
        return [("Set-Cookie", cookie)]

    def clear_session(self):
        cookie = SimpleCookie()
        cookie.load(self.headers.get("Cookie", ""))
        token = cookie["session"].value if "session" in cookie else None
        if token:
            with sessions_lock:
                sessions.pop(token, None)
        return [("Set-Cookie", "session=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0")]

    def do_GET(self):
        path = urlparse(self.path)
        if path.path == "/":
            self.send_page()
            return

        user_id = self.get_session()
        if user_id is None:
            self.send_json(401, {"error": "Нужно войти в аккаунт."})
            return

        if path.path == "/api/me":
            with connect_database() as connection:
                user = connection.execute(
                    "SELECT id, username, display_name FROM users WHERE id = ?", (user_id,)
                ).fetchone()
            if user is None:
                self.send_json(401, {"error": "Аккаунт не найден."})
            else:
                self.send_json(200, {
                    "id": user["id"],
                    "username": user["username"],
                    "display_name": user["display_name"] or user["username"]
                })
            return

        if path.path == "/api/calls/incoming":
            now = time.time()
            with connect_database() as connection:
                connection.execute(
                    """
                    UPDATE call_sessions SET status = 'ended', updated_at = ?
                    WHERE status = 'ringing' AND created_at < ?
                    """,
                    (now, now - 60),
                )
                calls = connection.execute(
                    """
                    SELECT c.id, c.mode, u.username, u.display_name
                    FROM call_sessions c
                    JOIN users u ON u.id = c.caller_id
                    WHERE c.callee_id = ? AND c.status = 'ringing'
                    ORDER BY c.created_at DESC LIMIT 5
                    """,
                    (user_id,),
                ).fetchall()
            self.send_json(200, [
                {"id": row["id"], "mode": row["mode"], "username": row["username"],
                 "display_name": row["display_name"] or row["username"]}
                for row in calls
            ])
            return

        call_match = re.fullmatch(r"/api/calls/([A-Za-z0-9_-]+)", path.path)
        if call_match:
            query = parse_qs(path.query)
            try:
                after_id = max(0, int(query.get("after", ["0"])[0]))
            except ValueError:
                self.send_json(400, {"error": "Неверный номер сигнала."})
                return
            call_id = call_match.group(1)
            with connect_database() as connection:
                connection.execute(
                    """
                    UPDATE call_sessions SET status = 'ended', updated_at = ?
                    WHERE id = ? AND status = 'ringing' AND created_at < ?
                    """,
                    (time.time(), call_id, time.time() - 60),
                )
                call = connection.execute(
                    """
                    SELECT id, caller_id, callee_id, mode, status
                    FROM call_sessions
                    WHERE id = ? AND (caller_id = ? OR callee_id = ?)
                    """,
                    (call_id, user_id, user_id),
                ).fetchone()
                if call is None:
                    self.send_json(404, {"error": "Звонок не найден."})
                    return
                signals = connection.execute(
                    """
                    SELECT id, kind, payload FROM call_signals
                    WHERE call_id = ? AND sender_id != ? AND id > ?
                    ORDER BY id LIMIT 100
                    """,
                    (call_id, user_id, after_id),
                ).fetchall()
            self.send_json(200, {
                "id": call["id"],
                "caller_id": call["caller_id"],
                "callee_id": call["callee_id"],
                "mode": call["mode"],
                "status": call["status"],
                "signals": [
                    {"id": row["id"], "kind": row["kind"], "payload": json.loads(row["payload"])}
                    for row in signals
                ],
            })
            return

        if path.path == "/api/groups":
            with connect_database() as connection:
                groups = connection.execute(
                    """
                    SELECT g.id, g.name, g.invite_code, COUNT(members.user_id) AS member_count
                    FROM chat_groups g
                    JOIN group_members mine ON mine.group_id = g.id AND mine.user_id = ?
                    JOIN group_members members ON members.group_id = g.id
                    GROUP BY g.id
                    ORDER BY g.id DESC
                    """,
                    (user_id,),
                ).fetchall()
            self.send_json(200, [
                {"id": row["id"], "name": row["name"],
                 "invite_code": row["invite_code"], "member_count": row["member_count"]}
                for row in groups
            ])
            return

        if path.path == "/api/media":
            query = parse_qs(path.query)
            try:
                message_id = int(query.get("id", ["0"])[0])
            except ValueError:
                self.send_json(400, {"error": "Неверный номер файла."})
                return
            with connect_database() as connection:
                media = connection.execute(
                    """
                    SELECT media_type, media_data
                    FROM messages
                    WHERE id = ? AND kind = 'media'
                        AND (
                            sender_id = ? OR recipient_id = ?
                            OR EXISTS (
                                SELECT 1 FROM group_members gm
                                WHERE gm.group_id = messages.group_id AND gm.user_id = ?
                            )
                        )
                    """,
                    (message_id, user_id, user_id, user_id),
                ).fetchone()
            if media is None:
                self.send_json(404, {"error": "Файл не найден."})
                return
            body = media["media_data"]
            self.send_response(200)
            self.send_header("Content-Type", media["media_type"])
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Content-Disposition", "inline; filename=\"media\"")
            self.send_header("Cache-Control", "private, no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)
            return

        if path.path == "/api/users":
            query = parse_qs(path.query)
            search = query.get("search", [""])[0].strip()
            if len(search) < 2:
                self.send_json(200, [])
                return
            search = search[:30].casefold()
            with connect_database() as connection:
                rows = connection.execute(
                    """
                    SELECT u.id, u.username, u.display_name,
                        (SELECT m.text FROM messages m
                         WHERE (m.sender_id = ? AND m.recipient_id = u.id)
                            OR (m.sender_id = u.id AND m.recipient_id = ?)
                         ORDER BY m.id DESC LIMIT 1) AS last_message
                    FROM users u
                    WHERE u.id != ?
                    ORDER BY u.username COLLATE NOCASE
                    LIMIT 5000
                    """,
                    (user_id, user_id, user_id),
                ).fetchall()
            matching_rows = [
                row for row in rows
                if search in row["username"].casefold()
                or search in row["display_name"].casefold()
            ][:50]
            self.send_json(200, [
                {"id": row["id"], "username": row["username"],
                 "display_name": row["display_name"] or row["username"],
                 "last_message": row["last_message"] or ""}
                for row in matching_rows
            ])
            return

        if path.path == "/api/polls":
            query = parse_qs(path.query)
            recipient_name = query.get("with", [""])[0]
            with connect_database() as connection:
                group_match = re.fullmatch(r"group:(\d{1,12})", recipient_name)
                if group_match:
                    group_id = int(group_match.group(1))
                    group = connection.execute(
                        "SELECT 1 FROM group_members WHERE group_id = ? AND user_id = ?",
                        (group_id, user_id),
                    ).fetchone()
                    if group is None:
                        self.send_json(404, {"error": "Группа не найдена."})
                        return
                    poll_rows = connection.execute(
                        """
                        SELECT p.id FROM polls p
                        JOIN messages m ON m.id = p.message_id
                        WHERE m.group_id = ?
                        """,
                        (group_id,),
                    ).fetchall()
                else:
                    recipient = connection.execute(
                        "SELECT id FROM users WHERE username = ? COLLATE NOCASE",
                        (recipient_name,),
                    ).fetchone()
                    if recipient is None or recipient["id"] == user_id:
                        self.send_json(404, {"error": "Собеседник не найден."})
                        return
                    poll_rows = connection.execute(
                        """
                        SELECT p.id
                        FROM polls p
                        JOIN messages m ON m.id = p.message_id
                        WHERE (m.sender_id = ? AND m.recipient_id = ?)
                           OR (m.sender_id = ? AND m.recipient_id = ?)
                        """,
                        (user_id, recipient["id"], recipient["id"], user_id),
                    ).fetchall()
                result = [load_poll(connection, row["id"], user_id) for row in poll_rows]
            self.send_json(200, result)
            return

        if path.path == "/api/messages":
            query = parse_qs(path.query)
            recipient_name = query.get("with", [""])[0]
            try:
                after_id = max(0, int(query.get("after", ["0"])[0]))
            except ValueError:
                self.send_json(400, {"error": "Неверный номер сообщения."})
                return

            with connect_database() as connection:
                group_match = re.fullmatch(r"group:(\d{1,12})", recipient_name)
                if group_match:
                    group_id = int(group_match.group(1))
                    group = connection.execute(
                        """
                        SELECT g.id, g.name FROM chat_groups g
                        JOIN group_members gm ON gm.group_id = g.id
                        WHERE g.id = ? AND gm.user_id = ?
                        """,
                        (group_id, user_id),
                    ).fetchone()
                    if group is None:
                        self.send_json(404, {"error": "Группа не найдена."})
                        return
                    query = """
                        SELECT m.id, m.sender_id, sender.username AS sender,
                               sender.display_name AS sender_display, m.text, m.created_at,
                               m.kind, m.media_name, m.media_type, p.id AS poll_id
                        FROM messages m
                        JOIN users sender ON sender.id = m.sender_id
                        LEFT JOIN polls p ON p.message_id = m.id
                        WHERE m.group_id = ? AND m.id > ?
                        ORDER BY m.id DESC
                        LIMIT 100
                    """
                    params = (group_id, after_id)
                else:
                    recipient = connection.execute(
                        "SELECT id FROM users WHERE username = ? COLLATE NOCASE",
                        (recipient_name,),
                    ).fetchone()
                    if recipient is None or recipient["id"] == user_id:
                        self.send_json(404, {"error": "Собеседник не найден."})
                        return
                    query = """
                        SELECT m.id, m.sender_id, sender.username AS sender,
                               sender.display_name AS sender_display, m.text, m.created_at,
                               m.kind, m.media_name, m.media_type, p.id AS poll_id
                        FROM messages m
                        JOIN users sender ON sender.id = m.sender_id
                        LEFT JOIN polls p ON p.message_id = m.id
                        WHERE m.id > ? AND (
                            (m.sender_id = ? AND m.recipient_id = ?)
                            OR (m.sender_id = ? AND m.recipient_id = ?)
                        )
                        ORDER BY m.id DESC
                        LIMIT 100
                    """
                    params = (after_id, user_id, recipient["id"], recipient["id"], user_id)
                rows = connection.execute(query, params).fetchall()
                result = []
                for row in reversed(rows):
                    message = {
                        "id": row["id"],
                        "sender": row["sender"],
                        "sender_display": row["sender_display"] or row["sender"],
                        "text": row["text"],
                        "time": row["created_at"],
                        "kind": row["kind"],
                        "media_name": row["media_name"],
                        "media_type": row["media_type"],
                    }
                    if row["poll_id"] is not None:
                        message["poll"] = load_poll(connection, row["poll_id"], user_id)
                    result.append(message)
            self.send_json(200, result)
            return

        self.send_json(404, {"error": "Страница не найдена."})

    def do_POST(self):
        if not valid_origin(self):
            self.send_json(403, {"error": "Запрос отклонён: неверный источник."})
            return

        path = urlparse(self.path).path
        if path == "/api/logout":
            self.send_json(200, {"ok": True}, self.clear_session())
            return

        if path == "/api/media":
            user_id = self.get_session()
            if user_id is None:
                self.send_json(401, {"error": "Нужно войти в аккаунт."})
                return
            data = self.read_json(MAX_MEDIA_REQUEST_BYTES)
            if data is None:
                return
            recipient_name = data.get("to")
            file_name = data.get("name")
            encoded_data = data.get("data")
            if not all(isinstance(value, str) for value in (recipient_name, file_name, encoded_data)):
                self.send_json(400, {"error": "Не удалось прочитать файл."})
                return
            try:
                media_data = base64.b64decode(encoded_data, validate=True)
            except (binascii.Error, ValueError):
                self.send_json(400, {"error": "Файл повреждён."})
                return
            if not media_data or len(media_data) > MAX_MEDIA_BYTES:
                self.send_json(413, {"error": "Размер файла должен быть не больше 20 МБ."})
                return

            media_signatures = (
                (b"\xff\xd8\xff", "image/jpeg", ".jpg"),
                (b"\x89PNG\r\n\x1a\n", "image/png", ".png"),
                (b"GIF87a", "image/gif", ".gif"),
                (b"GIF89a", "image/gif", ".gif"),
            )
            detected_media = next(
                ((mime, extension) for signature, mime, extension in media_signatures
                 if media_data.startswith(signature)),
                None,
            )
            if detected_media is None and len(media_data) >= 12:
                if media_data.startswith(b"RIFF") and media_data[8:12] == b"WEBP":
                    detected_media = ("image/webp", ".webp")
                elif media_data[4:8] == b"ftyp":
                    detected_media = ("video/mp4", ".mp4")
                elif media_data.startswith(b"\x1a\x45\xdf\xa3"):
                    detected_media = ("video/webm", ".webm")
            if detected_media is None:
                self.send_json(400, {"error": "Поддерживаются только фото JPEG/PNG/GIF/WebP и видео MP4/WebM."})
                return
            media_type, extension = detected_media
            safe_name = file_name.replace("\\", "/").split("/")[-1]
            safe_name = "".join(
                character for character in safe_name
                if character.isprintable() and character not in "\r\n"
            ).strip()[:120]
            if not safe_name:
                safe_name = "media" + extension

            with connect_database() as connection:
                group_match = re.fullmatch(r"group:(\d{1,12})", recipient_name.strip())
                group_id = None
                if group_match:
                    group_id = int(group_match.group(1))
                    member = connection.execute(
                        "SELECT 1 FROM group_members WHERE group_id = ? AND user_id = ?",
                        (group_id, user_id),
                    ).fetchone()
                    if member is None:
                        self.send_json(404, {"error": "Группа не найдена."})
                        return
                    recipient_id = user_id
                else:
                    recipient = connection.execute(
                        "SELECT id FROM users WHERE username = ? COLLATE NOCASE",
                        (recipient_name.strip(),),
                    ).fetchone()
                    if recipient is None or recipient["id"] == user_id:
                        self.send_json(404, {"error": "Собеседник не найден."})
                        return
                    recipient_id = recipient["id"]
                stored_bytes = connection.execute(
                    "SELECT COALESCE(SUM(length(media_data)), 0) FROM messages "
                    "WHERE kind = 'media'"
                ).fetchone()[0]
                if stored_bytes + len(media_data) > MAX_TOTAL_MEDIA_BYTES:
                    self.send_json(507, {
                        "error": "Хранилище демо-чата заполнено. Сейчас общий лимит медиа — 100 МБ."
                    })
                    return
                cursor = connection.execute(
                    """
                    INSERT INTO messages
                        (sender_id, recipient_id, text, created_at, kind,
                         media_name, media_type, media_data, group_id)
                    VALUES (?, ?, '', ?, 'media', ?, ?, ?, ?)
                    """,
                    (user_id, recipient_id, datetime.now().strftime("%H:%M"),
                     safe_name, media_type, media_data, group_id),
                )
                message_id = cursor.lastrowid
            self.send_json(201, {"ok": True, "id": message_id})
            return

        call_signal_match = re.fullmatch(r"/api/calls/([A-Za-z0-9_-]+)/signal", path)
        data = self.read_json(65536 if path == "/api/calls" or call_signal_match else 8192)
        if data is None:
            return

        if path == "/api/calls":
            user_id = self.get_session()
            if user_id is None:
                self.send_json(401, {"error": "Нужно войти в аккаунт."})
                return
            recipient_name = data.get("to")
            mode = data.get("mode")
            if not isinstance(recipient_name, str) or mode not in ("audio", "video"):
                self.send_json(400, {"error": "Укажи собеседника и тип звонка."})
                return
            now = time.time()
            with connect_database() as connection:
                connection.execute(
                    """
                    UPDATE call_sessions SET status = 'ended', updated_at = ?
                    WHERE (status = 'ringing' AND created_at < ?)
                       OR (status = 'accepted' AND updated_at < ?)
                    """,
                    (now, now - 60, now - 6 * 60 * 60),
                )
                connection.execute(
                    """
                    DELETE FROM call_sessions
                    WHERE status IN ('rejected', 'ended') AND updated_at < ?
                    """,
                    (now - 24 * 60 * 60,),
                )
                recipient = connection.execute(
                    "SELECT id FROM users WHERE username = ? COLLATE NOCASE",
                    (recipient_name.strip(),),
                ).fetchone()
                if recipient is None or recipient["id"] == user_id:
                    self.send_json(404, {"error": "Собеседник не найден."})
                    return
                busy = connection.execute(
                    """
                    SELECT 1 FROM call_sessions
                    WHERE status IN ('ringing', 'accepted')
                      AND (caller_id IN (?, ?) OR callee_id IN (?, ?))
                    LIMIT 1
                    """,
                    (user_id, recipient["id"], user_id, recipient["id"]),
                ).fetchone()
                if busy:
                    self.send_json(409, {"error": "Один из участников уже разговаривает."})
                    return
                call_id = secrets.token_urlsafe(18)
                connection.execute(
                    """
                    INSERT INTO call_sessions
                        (id, caller_id, callee_id, mode, status, created_at, updated_at)
                    VALUES (?, ?, ?, ?, 'ringing', ?, ?)
                    """,
                    (call_id, user_id, recipient["id"], mode, now, now),
                )
            self.send_json(201, {"id": call_id, "status": "ringing"})
            return

        if call_signal_match:
            user_id = self.get_session()
            if user_id is None:
                self.send_json(401, {"error": "Нужно войти в аккаунт."})
                return
            call_id = call_signal_match.group(1)
            kind = data.get("kind")
            payload = data.get("payload", {})
            if kind not in ("accept", "reject", "end", "offer", "answer", "ice"):
                self.send_json(400, {"error": "Неизвестный сигнал звонка."})
                return
            if not isinstance(payload, dict) or len(json.dumps(payload)) > 32000:
                self.send_json(400, {"error": "Данные звонка слишком большие или неверные."})
                return
            if kind in ("offer", "answer") and (
                not isinstance(payload.get("sdp"), str)
                or payload.get("type") not in ("offer", "answer")
            ):
                self.send_json(400, {"error": "Неверное описание соединения."})
                return
            if kind == "ice" and not isinstance(payload.get("candidate"), str):
                self.send_json(400, {"error": "Неверный ICE-кандидат."})
                return

            now = time.time()
            with connect_database() as connection:
                call = connection.execute(
                    """
                    SELECT caller_id, callee_id, status FROM call_sessions
                    WHERE id = ? AND (caller_id = ? OR callee_id = ?)
                    """,
                    (call_id, user_id, user_id),
                ).fetchone()
                if call is None:
                    self.send_json(404, {"error": "Звонок не найден."})
                    return
                is_caller = call["caller_id"] == user_id
                if kind in ("accept", "reject") and is_caller:
                    self.send_json(403, {"error": "Ответить на звонок может только получатель."})
                    return
                if kind == "accept":
                    if call["status"] == "ringing":
                        connection.execute(
                            "UPDATE call_sessions SET status = 'accepted', updated_at = ? WHERE id = ?",
                            (now, call_id),
                        )
                    elif call["status"] != "accepted":
                        self.send_json(409, {"error": "Звонок уже завершён."})
                        return
                elif kind == "reject":
                    if call["status"] != "ringing":
                        self.send_json(409, {"error": "На этот звонок уже нельзя ответить."})
                        return
                    connection.execute(
                        "UPDATE call_sessions SET status = 'rejected', updated_at = ? WHERE id = ?",
                        (now, call_id),
                    )
                elif kind == "end":
                    if call["status"] in ("ringing", "accepted"):
                        connection.execute(
                            "UPDATE call_sessions SET status = 'ended', updated_at = ? WHERE id = ?",
                            (now, call_id),
                        )
                else:
                    expected_sender = call["caller_id"] if kind == "offer" else call["callee_id"]
                    if call["status"] != "accepted" or user_id != expected_sender and kind != "ice":
                        self.send_json(409, {"error": "Сигнал не подходит для текущего состояния звонка."})
                        return
                    connection.execute(
                        """
                        INSERT INTO call_signals (call_id, sender_id, kind, payload, created_at)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (call_id, user_id, kind, json.dumps(payload), now),
                    )
                    connection.execute(
                        "UPDATE call_sessions SET updated_at = ? WHERE id = ?",
                        (now, call_id),
                    )
            self.send_json(200, {"ok": True})
            return

        if path in ("/api/groups", "/api/groups/join"):
            user_id = self.get_session()
            if user_id is None:
                self.send_json(401, {"error": "Нужно войти в аккаунт."})
                return

            with connect_database() as connection:
                if path == "/api/groups":
                    name = data.get("name")
                    if not isinstance(name, str):
                        self.send_json(400, {"error": "Укажи название группы."})
                        return
                    name = name.strip()
                    if not 3 <= len(name) <= 40:
                        self.send_json(400, {"error": "Название группы: от 3 до 40 символов."})
                        return
                    invite_code = secrets.token_hex(6).upper()
                    cursor = connection.execute(
                        """
                        INSERT INTO chat_groups (name, invite_code, created_by, created_at)
                        VALUES (?, ?, ?, ?)
                        """,
                        (name, invite_code, user_id, datetime.now().strftime("%Y-%m-%d %H:%M")),
                    )
                    group_id = cursor.lastrowid
                    connection.execute(
                        "INSERT INTO group_members (group_id, user_id, joined_at) VALUES (?, ?, ?)",
                        (group_id, user_id, datetime.now().isoformat(timespec="seconds")),
                    )
                else:
                    invite_code = data.get("invite_code")
                    if not isinstance(invite_code, str):
                        self.send_json(400, {"error": "Введи код приглашения."})
                        return
                    group = connection.execute(
                        """
                        SELECT id, name, invite_code FROM chat_groups
                        WHERE invite_code = ? COLLATE NOCASE
                        """,
                        (invite_code.strip(),),
                    ).fetchone()
                    if group is None:
                        self.send_json(404, {"error": "Группа с таким кодом не найдена."})
                        return
                    group_id = group["id"]
                    invite_code = group["invite_code"]
                    name = group["name"]
                    connection.execute(
                        """
                        INSERT OR IGNORE INTO group_members (group_id, user_id, joined_at)
                        VALUES (?, ?, ?)
                        """,
                        (group_id, user_id, datetime.now().isoformat(timespec="seconds")),
                    )
            self.send_json(201, {
                "id": group_id, "name": name, "invite_code": invite_code
            })
            return

        if path in ("/api/register", "/api/login"):
            username = data.get("username")
            password = data.get("password")
            if not isinstance(username, str) or not isinstance(password, str):
                self.send_json(400, {"error": "Введи имя пользователя и пароль."})
                return

            username = username.strip()
            if not re.fullmatch(r"[A-Za-z0-9_]{3,20}", username):
                self.send_json(400, {
                    "error": "Имя: 3–20 символов, только латинские буквы, цифры и _."
                })
                return
            if len(password) < 8 or len(password) > 128:
                self.send_json(400, {"error": "Пароль должен содержать от 8 до 128 символов."})
                return

            with connect_database() as connection:
                user = connection.execute(
                    "SELECT id, username, password_salt, password_hash, display_name "
                    "FROM users WHERE username = ? COLLATE NOCASE",
                    (username,),
                ).fetchone()
                if path == "/api/register":
                    if user is not None:
                        self.send_json(409, {"error": "Это имя уже занято."})
                        return
                    salt = secrets.token_bytes(16)
                    password_hash = make_password_hash(password, salt)
                    try:
                        cursor = connection.execute(
                            "INSERT INTO users "
                            "(username, password_salt, password_hash, display_name) "
                            "VALUES (?, ?, ?, ?)",
                            (username, salt, password_hash, username),
                        )
                    except sqlite3.IntegrityError:
                        self.send_json(409, {"error": "Это имя уже занято."})
                        return
                    user_id = cursor.lastrowid
                    saved_username = username
                    saved_display_name = username
                else:
                    if user is None:
                        self.send_json(401, {"error": "Неверное имя или пароль."})
                        return
                    candidate = make_password_hash(password, user["password_salt"])
                    if not hmac.compare_digest(candidate, user["password_hash"]):
                        self.send_json(401, {"error": "Неверное имя или пароль."})
                        return
                    user_id = user["id"]
                    saved_username = user["username"]
                    saved_display_name = user["display_name"] or user["username"]

            headers = self.set_session(user_id)
            self.send_json(200, {
                "id": user_id,
                "username": saved_username,
                "display_name": saved_display_name
            }, headers)
            return

        if path == "/api/profile":
            user_id = self.get_session()
            if user_id is None:
                self.send_json(401, {"error": "Нужно войти в аккаунт."})
                return
            display_name = data.get("display_name")
            if not isinstance(display_name, str):
                self.send_json(400, {"error": "Имя профиля должно быть текстом."})
                return
            display_name = display_name.strip()
            if not display_name or len(display_name) > 30:
                self.send_json(400, {"error": "Имя должно содержать от 1 до 30 символов."})
                return
            with connect_database() as connection:
                connection.execute(
                    "UPDATE users SET display_name = ? WHERE id = ?",
                    (display_name, user_id),
                )
                user = connection.execute(
                    "SELECT id, username, display_name FROM users WHERE id = ?",
                    (user_id,),
                ).fetchone()
            self.send_json(200, {
                "id": user["id"],
                "username": user["username"],
                "display_name": user["display_name"]
            })
            return

        if path == "/api/password":
            user_id = self.get_session()
            if user_id is None:
                self.send_json(401, {"error": "Нужно войти в аккаунт."})
                return
            old_password = data.get("old_password")
            new_password = data.get("new_password")
            if not isinstance(old_password, str) or not isinstance(new_password, str):
                self.send_json(400, {"error": "Введи текущий и новый пароль."})
                return
            if len(new_password) < 8 or len(new_password) > 128:
                self.send_json(400, {"error": "Новый пароль должен содержать от 8 до 128 символов."})
                return
            with connect_database() as connection:
                user = connection.execute(
                    "SELECT password_salt, password_hash FROM users WHERE id = ?",
                    (user_id,),
                ).fetchone()
                if user is None:
                    self.send_json(401, {"error": "Аккаунт не найден."})
                    return
                candidate = make_password_hash(old_password, user["password_salt"])
                if not hmac.compare_digest(candidate, user["password_hash"]):
                    self.send_json(400, {"error": "Текущий пароль введён неверно."})
                    return
                salt = secrets.token_bytes(16)
                password_hash = make_password_hash(new_password, salt)
                connection.execute(
                    "UPDATE users SET password_salt = ?, password_hash = ? WHERE id = ?",
                    (salt, password_hash, user_id),
                )
            self.send_json(200, {"ok": True})
            return

        if path == "/api/messages":
            user_id = self.get_session()
            if user_id is None:
                self.send_json(401, {"error": "Нужно войти в аккаунт."})
                return

            recipient_name = data.get("to")
            text = data.get("text")
            if not isinstance(recipient_name, str) or not isinstance(text, str):
                self.send_json(400, {"error": "Нужны имя собеседника и текст сообщения."})
                return
            recipient_name = recipient_name.strip()
            text = text.strip()
            if not text or len(text) > 1000:
                self.send_json(400, {"error": "Сообщение должно содержать от 1 до 1000 символов."})
                return

            with connect_database() as connection:
                group_match = re.fullmatch(r"group:(\d{1,12})", recipient_name)
                group_id = None
                if group_match:
                    group_id = int(group_match.group(1))
                    member = connection.execute(
                        "SELECT 1 FROM group_members WHERE group_id = ? AND user_id = ?",
                        (group_id, user_id),
                    ).fetchone()
                    if member is None:
                        self.send_json(404, {"error": "Группа не найдена."})
                        return
                    recipient_id = user_id
                else:
                    recipient = connection.execute(
                        "SELECT id FROM users WHERE username = ? COLLATE NOCASE",
                        (recipient_name,),
                    ).fetchone()
                    if recipient is None or recipient["id"] == user_id:
                        self.send_json(404, {"error": "Собеседник не найден."})
                        return
                    recipient_id = recipient["id"]
                created_at = datetime.now().strftime("%H:%M")
                connection.execute(
                    """
                    INSERT INTO messages (sender_id, recipient_id, text, created_at, group_id)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (user_id, recipient_id, text, created_at, group_id),
                )
            self.send_json(201, {"ok": True})
            return

        if path == "/api/polls":
            user_id = self.get_session()
            if user_id is None:
                self.send_json(401, {"error": "Нужно войти в аккаунт."})
                return
            recipient_name = data.get("to")
            question = data.get("question")
            options = data.get("options")
            if not isinstance(recipient_name, str) or not isinstance(question, str):
                self.send_json(400, {"error": "Укажи собеседника и вопрос."})
                return
            if not isinstance(options, list) or not 2 <= len(options) <= 6:
                self.send_json(400, {"error": "В опросе должно быть от 2 до 6 вариантов."})
                return
            question = question.strip()
            options = [option.strip() if isinstance(option, str) else "" for option in options]
            if not question or len(question) > 200:
                self.send_json(400, {"error": "Вопрос должен содержать от 1 до 200 символов."})
                return
            if any(not option or len(option) > 100 for option in options):
                self.send_json(400, {"error": "Каждый вариант должен содержать от 1 до 100 символов."})
                return
            if len({option.casefold() for option in options}) != len(options):
                self.send_json(400, {"error": "Варианты ответа не должны повторяться."})
                return

            with connect_database() as connection:
                group_match = re.fullmatch(r"group:(\d{1,12})", recipient_name.strip())
                group_id = None
                if group_match:
                    group_id = int(group_match.group(1))
                    member = connection.execute(
                        "SELECT 1 FROM group_members WHERE group_id = ? AND user_id = ?",
                        (group_id, user_id),
                    ).fetchone()
                    if member is None:
                        self.send_json(404, {"error": "Группа не найдена."})
                        return
                    recipient_id = user_id
                else:
                    recipient = connection.execute(
                        "SELECT id FROM users WHERE username = ? COLLATE NOCASE",
                        (recipient_name.strip(),),
                    ).fetchone()
                    if recipient is None or recipient["id"] == user_id:
                        self.send_json(404, {"error": "Собеседник не найден."})
                        return
                    recipient_id = recipient["id"]
                cursor = connection.execute(
                    """
                    INSERT INTO messages
                        (sender_id, recipient_id, text, created_at, kind, group_id)
                    VALUES (?, ?, ?, ?, 'poll', ?)
                    """,
                    (user_id, recipient_id, question, datetime.now().strftime("%H:%M"), group_id),
                )
                poll_cursor = connection.execute(
                    "INSERT INTO polls (message_id, question) VALUES (?, ?)",
                    (cursor.lastrowid, question),
                )
                connection.executemany(
                    "INSERT INTO poll_options (poll_id, text) VALUES (?, ?)",
                    [(poll_cursor.lastrowid, option) for option in options],
                )
            self.send_json(201, {"ok": True})
            return

        vote_match = re.fullmatch(r"/api/polls/(\d+)/vote", path)
        if vote_match:
            user_id = self.get_session()
            if user_id is None:
                self.send_json(401, {"error": "Нужно войти в аккаунт."})
                return
            try:
                poll_id = int(vote_match.group(1))
            except ValueError:
                self.send_json(400, {"error": "Неверный номер опроса."})
                return
            option_ids = data.get("option_ids")
            if (not isinstance(option_ids, list) or not option_ids
                    or any(type(option_id) is not int for option_id in option_ids)
                    or len(set(option_ids)) != len(option_ids)):
                self.send_json(400, {"error": "Выбери один или несколько вариантов."})
                return
            with connect_database() as connection:
                poll = connection.execute(
                    """
                    SELECT p.id
                    FROM polls p JOIN messages m ON m.id = p.message_id
                    WHERE p.id = ? AND (
                        m.sender_id = ? OR m.recipient_id = ?
                        OR EXISTS (
                            SELECT 1 FROM group_members gm
                            WHERE gm.group_id = m.group_id AND gm.user_id = ?
                        )
                    )
                    """,
                    (poll_id, user_id, user_id, user_id),
                ).fetchone()
                if poll is None:
                    self.send_json(404, {"error": "Опрос не найден."})
                    return
                valid_options = connection.execute(
                    "SELECT id FROM poll_options WHERE poll_id = ?",
                    (poll_id,),
                ).fetchall()
                valid_option_ids = {row["id"] for row in valid_options}
                if len(option_ids) > len(valid_option_ids) or not set(option_ids) <= valid_option_ids:
                    self.send_json(400, {"error": "Выбраны неизвестные варианты ответа."})
                    return
                connection.execute(
                    """
                    DELETE FROM poll_votes
                    WHERE user_id = ? AND option_id IN (
                        SELECT id FROM poll_options WHERE poll_id = ?
                    )
                    """,
                    (user_id, poll_id),
                )
                connection.executemany(
                    "INSERT INTO poll_votes (user_id, option_id) VALUES (?, ?)",
                    [(user_id, option_id) for option_id in option_ids],
                )
                poll_data = load_poll(connection, poll_id, user_id)
            self.send_json(200, poll_data)
            return

        self.send_json(404, {"error": "Страница не найдена."})

    def log_message(self, format_string, *args):
        print("%s - %s" % (self.address_string(), format_string % args))


def main():
    initialize_database()
    server = ThreadingHTTPServer((HOST, PORT), ChatHandler)

    print("Учебный мессенджер запущен.")
    if HOST == "127.0.0.1":
        print("Открой в браузере: http://localhost:%s" % PORT)
        print("Сервер доступен только на этом компьютере.")
    else:
        print("Сервер слушает %s:%s" % (HOST, PORT))
    print("Остановить сервер можно сочетанием Ctrl+C.")
    print("Это демо: на бесплатном хостинге база может очищаться; не отправляй личные данные.")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nЗавершаю работу мессенджера.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
