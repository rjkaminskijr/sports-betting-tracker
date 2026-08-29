import email
import imaplib
import re
from email.header import decode_header
from io import BytesIO
from typing import List, Dict, Any

from PIL import Image

DK_LINK_RE = re.compile(r'https://sportsbook\.draftkings\.com/social/post/[0-9a-fA-F-]+\?slipAdd[^\s<>\"]*')


def _decode_header(value):
    if not value:
        return ''
    parts=[]
    for item, enc in decode_header(value):
        if isinstance(item, bytes):
            parts.append(item.decode(enc or 'utf-8', errors='replace'))
        else:
            parts.append(item)
    return ''.join(parts)


def _message_text(msg):
    chunks=[]
    if msg.is_multipart():
        for part in msg.walk():
            ctype=part.get_content_type()
            disp=(part.get('Content-Disposition') or '').lower()
            if ctype in ('text/plain','text/html') and 'attachment' not in disp:
                payload=part.get_payload(decode=True)
                if payload:
                    charset=part.get_content_charset() or 'utf-8'
                    chunks.append(payload.decode(charset, errors='replace'))
    else:
        payload=msg.get_payload(decode=True)
        if payload:
            chunks.append(payload.decode(msg.get_content_charset() or 'utf-8', errors='replace'))
    return '\n'.join(chunks)


def _extract_images(msg):
    out=[]
    for part in msg.walk():
        ctype=part.get_content_type()
        filename=_decode_header(part.get_filename()) if part.get_filename() else ''
        if ctype.startswith('image/') or filename.lower().endswith(('.png','.jpg','.jpeg','.webp')):
            raw=part.get_payload(decode=True)
            if not raw:
                continue
            try:
                img=Image.open(BytesIO(raw)).convert('RGB')
                out.append({'filename': filename or 'bet-slip.jpg', 'bytes': raw, 'image': img})
            except Exception:
                continue
    return out


def scan_label(email_address: str, app_password: str, label: str='Sports Bet Tracker', limit: int=100) -> List[Dict[str, Any]]:
    """Read recent messages from a Gmail IMAP label. Does not modify or delete mail."""
    imap=imaplib.IMAP4_SSL('imap.gmail.com', 993)
    try:
        imap.login(email_address, app_password.replace(' ', ''))
        status, _ = imap.select(f'"{label}"', readonly=True)
        if status != 'OK':
            # Gmail may expose the label without quoting depending on server response.
            status, _ = imap.select(label, readonly=True)
        if status != 'OK':
            raise RuntimeError(f'Could not open Gmail label: {label}')
        status, data=imap.search(None, 'ALL')
        if status!='OK':
            raise RuntimeError('Could not search Gmail label.')
        ids=(data[0].split() if data and data[0] else [])[-max(1,int(limit)):]
        results=[]
        for mid in reversed(ids):
            status, payload=imap.fetch(mid, '(RFC822)')
            if status!='OK' or not payload:
                continue
            raw_msg=None
            for item in payload:
                if isinstance(item, tuple) and len(item)>1:
                    raw_msg=item[1]; break
            if not raw_msg:
                continue
            msg=email.message_from_bytes(raw_msg)
            body=_message_text(msg)
            links=DK_LINK_RE.findall(body)
            images=_extract_images(msg)
            if not links and not images:
                continue
            results.append({
                'imap_id': mid.decode(errors='ignore'),
                'message_id': (msg.get('Message-ID') or '').strip(),
                'subject': _decode_header(msg.get('Subject')),
                'date': msg.get('Date') or '',
                'from': _decode_header(msg.get('From')),
                'draftkings_links': links,
                'images': images,
            })
        return results
    finally:
        try: imap.logout()
        except Exception: pass
