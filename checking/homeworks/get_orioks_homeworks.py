import os

import re
import aiohttp
from bs4 import BeautifulSoup

import config
from utils import exceptions
from utils.json_files import JsonFile
from utils.make_request import get_request
from utils.notify_to_user import SendToTelegram
import aiogram.utils.markdown as md


def _orioks_parse_homeworks(raw_html: str) -> list:
    bs_content = BeautifulSoup(raw_html, "html.parser")
    table_raw = bs_content.select('.table.table-condensed.table-thread tr:not(:first-child)')
    homeworks = []
    for tr in table_raw:
        _thread_id = int(re.findall(r'\d+$', tr.find_all('td')[2].select_one('a')['href'])[0])
        homeworks.append({
            'thread_id': _thread_id,
            'status': tr.find_all('td')[1].text,
            'new_messages': int(tr.find_all('td')[8].select_one('b').text),
            'about': {
                'discipline': tr.find_all('td')[3].text,
                'task': tr.find_all('td')[4].text,
                'url': config.ORIOKS_PAGE_URLS['masks']['homeworks'].format(id=_thread_id),
            },
        })
    return homeworks


async def get_orioks_homeworks(session: aiohttp.ClientSession) -> list:
    raw_html = await get_request(url=config.ORIOKS_PAGE_URLS['notify']['homeworks'], session=session)
    return _orioks_parse_homeworks(raw_html)


async def get_homeworks_to_msg(diffs: list) -> str:
    message = ''
    for diff in diffs:
        if diff['type'] == 'new_status':
            message += md.text(
                md.text(
                    md.text('📝'),
                    md.hbold(diff['about']['task']),
                    md.text('по'),
                    md.text(f"«{diff['about']['discipline']}»"),
                    sep=' '
                ),
                md.text(
                    md.text('Cтатус домашнего задания изменён на:'),
                    md.hcode(diff['current_status']),
                    sep=' ',
                ),
                md.text(),
                md.text(
                    md.text('Подробности по ссылке:'),
                    md.text(diff['about']['url']),
                    sep=' ',
                ),
                sep='\n',
            )
        elif diff['type'] == 'new_message':
            message += md.text(
                md.text(
                    md.text('📝'),
                    md.hbold(diff['about']['task']),
                    md.text('по'),
                    md.text(f"«{diff['about']['discipline']}»"),
                    sep=' '
                ),
                md.text(
                    md.text('Получено личное сообщение от преподавателя.'),
                    md.text(
                        md.text('Количество новых сообщений:'),
                        md.hcode(diff['current_messages']),
                        sep=' ',
                    ),
                    sep=' ',
                ),
                md.text(),
                md.text(
                    md.text('Подробности по ссылке:'),
                    md.text(diff['about']['url']),
                    sep=' ',
                ),
                sep='\n',
            )
        message += '\n' * 3
    return message


def compare(old_list: list, new_list: list) -> list:
    diffs = []
    for old, new in zip(old_list, new_list):
        if old['thread_id'] != new['thread_id']:
            raise exceptions.FileCompareError
        if old['status'] != new['status']:
            diffs.append({
                'type': 'new_status',  # or `new_message`
                'current_status': new['status'],
                'about': new['about'],
            })
        elif new['new_messages'] > old['new_messages']:
            diffs.append({
                'type': 'new_message',  # or `new_status`
                'current_messages': new['new_messages'],
                'about': new['about'],
            })
    return diffs


async def user_homeworks_check(user_telegram_id: int, session: aiohttp.ClientSession):
    homeworks_list = await get_orioks_homeworks(session=session)
    student_json_file = config.STUDENT_FILE_JSON_MASK.format(id=user_telegram_id)
    path_users_to_file = os.path.join(config.BASEDIR, 'users_data', 'tracking_data', 'homeworks', student_json_file)
    if student_json_file not in os.listdir(os.path.dirname(path_users_to_file)):
        await JsonFile.save(data=homeworks_list, filename=path_users_to_file)
        return False

    old_json = await JsonFile.open(filename=path_users_to_file)
    if len(homeworks_list) != len(old_json):
        await JsonFile.save(data=homeworks_list, filename=path_users_to_file)
        return False
    try:
        diffs = compare(old_list=old_json, new_list=homeworks_list)
    except exceptions.FileCompareError:
        await JsonFile.save(data=homeworks_list, filename=path_users_to_file)
        return False

    if len(diffs) > 0:
        msg_to_send = await get_homeworks_to_msg(diffs=diffs)
        await SendToTelegram.text_message_to_user(user_telegram_id=user_telegram_id, message=msg_to_send)
    await JsonFile.save(data=homeworks_list, filename=path_users_to_file)
    return True
