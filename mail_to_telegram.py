import imaplib
import email
import json
import os
import re
import sys
from email.header import decode_header
from html import unescape
from urllib import request

YANDEX_BOT_TOKEN = os.environ['YANDEX_BOT_TOKEN']
YANDEX_RECIPIENT = os.environ['YANDEX_RECIPIENT']  # логин пользователя или chat_id группы
EMAIL_LOGIN = os.environ['EMAIL_LOGIN']
EMAIL_PASSWORD = os.environ['EMAIL_PASSWORD']
IMAP_HOST = os.getenv('IMAP_HOST', 'imap.yandex.ru')
IMAP_PORT = int(os.getenv('IMAP_PORT', '993'))
IMAP_FOLDER = os.getenv('IMAP_FOLDER', 'INBOX')
SUBJECT_FILTER = os.getenv('SUBJECT_FILTER', '').strip()
FROM_FILTER = os.getenv('FROM_FILTER', '').strip()
MAX_BODY = int(os.getenv('MAX_BODY', '1200'))


def decode_mime(value):
    if not value:
        return ''
    parts = decode_header(value)
    decoded = []
    for part, enc in parts:
        if isinstance(part, bytes):
            decoded.append(part.decode(enc or 'utf-8', errors='ignore'))
        else:
            decoded.append(part)
    return ''.join(decoded).strip()


def html_to_text(html):
    html = re.sub(r'(?is)<(script|style).*?>.*?</\1>', ' ', html)
    html = re.sub(r'(?i)<br\s*/?>', '\n', html)
    html = re.sub(r'(?i)</p>|</div>|</li>|</tr>|</h\d>', '\n', html)
    html = re.sub(r'<[^>]+>', ' ', html)
    html = unescape(html)
    html = re.sub(r'\r', '', html)
    html = re.sub(r'\n{3,}', '\n\n', html)
    html = re.sub(r'[ \t]{2,}', ' ', html)
    return html.strip()


def extract_body(msg):
    plain_text = None
    html_text = None
    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            disp = str(part.get('Content-Disposition', ''))
            if 'attachment' in disp.lower():
                continue
            payload = part.get_payload(decode=True)
            if not payload:
                continue
            charset = part.get_content_charset() or 'utf-8'
            try:
                text = payload.decode(charset, errors='ignore')
            except Exception:
                text = payload.decode('utf-8', errors='ignore')
            if ctype == 'text/plain' and not plain_text:
                plain_text = text.strip()
            elif ctype == 'text/html' and not html_text:
                html_text = html_to_text(text)
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            charset = msg.get_content_charset() or 'utf-8'
            try:
                text = payload.decode(charset, errors='ignore')
            except Exception:
                text = payload.decode('utf-8', errors='ignore')
            if msg.get_content_type() == 'text/html':
                html_text = html_to_text(text)
            else:
                plain_text = text.strip()
    body = plain_text or html_text or ''
    body = re.sub(r'\n{3,}', '\n\n', body).strip()
    return body[:MAX_BODY]


def yandex_send(text):
    url = 'https://botapi.messenger.yandex.net/bot/v1/messages/sendText/'
    recipient = YANDEX_RECIPIENT
    if recipient.startswith('0/') or recipient.startswith('1/'):
        body = json.dumps({'chat_id': recipient, 'text': text[:6000]})
    else:
        body = json.dumps({'login': recipient, 'text': text[:6000]})
    req = request.Request(
        url,
        data=body.encode('utf-8'),
        headers={
            'Authorization': f'OAuth {YANDEX_BOT_TOKEN}',
            'Content-Type': 'application/json',
        },
    )
    with request.urlopen(req, timeout=30) as resp:
        return resp.read().decode()


def main():
    mail = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
    mail.login(EMAIL_LOGIN, EMAIL_PASSWORD)
    status, _ = mail.select(IMAP_FOLDER)
    if status != 'OK':
        raise RuntimeError(f'Cannot open folder: {IMAP_FOLDER}')

    criteria = ['UNSEEN']
    charset = None

    if SUBJECT_FILTER:
        criteria.extend(['SUBJECT', f'"{SUBJECT_FILTER}"'])
        charset = 'UTF-8'

    if FROM_FILTER:
        criteria.extend(['FROM', f'"{FROM_FILTER}"'])
        charset = 'UTF-8'

    status, data = mail.search(charset, *criteria)
    if status != 'OK':
        raise RuntimeError('Cannot search unseen emails')

    ids = data[0].split()
    if not ids:
        print('No unseen emails')
        mail.logout()
        return

    forwarded = 0
    for msg_id in ids[-10:]:
        status, msg_data = mail.fetch(msg_id, '(RFC822)')
        if status != 'OK':
            continue
        raw = msg_data[0][1]
        msg = email.message_from_bytes(raw)
        subject = decode_mime(msg.get('Subject', '(без темы)'))
        sender = decode_mime(msg.get('From', '(неизвестный отправитель)'))
        date = decode_mime(msg.get('Date', ''))
        body = extract_body(msg)
        text = (
            f'**Новое письмо**\n'
            f'От: {sender}\n'
            f'Тема: {subject}\n'
            f'Дата: {date}\n\n'
            f'{body or "(текст письма пустой)"}'
        )
        yandex_send(text[:6000])
        mail.store(msg_id, '+FLAGS', '\\Seen')
        forwarded += 1

    mail.logout()
    print(f'Forwarded: {forwarded}')


if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        print(f'ERROR: {e}', file=sys.stderr)
        raise
