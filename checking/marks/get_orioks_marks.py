import json
import os
from dataclasses import dataclass

import aiohttp
from bs4 import BeautifulSoup

import config
from checking.marks.compares import file_compares, get_discipline_objs_from_diff
import utils
from utils.json_files import JsonFile
from utils.notify_to_user import SendToTelegram
from utils.make_request import get_request
from utils.my_isdigit import my_isdigit
from images.imager import Imager
from utils.delete_file import safe_delete


@dataclass
class DisciplineBall:
    current: float = 0
    might_be: float = 0


def _iterate_forang_version_with_list(forang: dict) -> list:
    json_to_save = []
    for discipline in forang['dises']:
        discipline_ball = DisciplineBall()
        one_discipline = []
        for mark in discipline['segments'][0]['allKms']:
            alias = mark['sh']
            if mark['sh'] in ('-', None, ' ', '') and mark['id'] == discipline['segments'][0]['allKms'][-1]['id']:
                alias = discipline['formControl']['name']  # issue 19: если нет алиаса на последнем КМ

            current_grade = mark['grade']['b']
            max_grade = mark['max_ball']

            one_discipline.append({'alias': alias, 'current_grade': current_grade, 'max_grade': max_grade})
            discipline_ball.current += current_grade if my_isdigit(current_grade) else 0
            discipline_ball.might_be += max_grade if my_isdigit(max_grade) and current_grade != '-' else 0
        json_to_save.append({
            'subject': discipline['name'],
            'tasks': one_discipline,
            'ball': {
                'current': discipline_ball.current,
                'might_be': discipline_ball.might_be,
            }
        })
    return json_to_save


def _iterate_forang_version_with_keys(forang: dict) -> list:
    json_to_save = []
    for discipline_index in forang['dises'].keys():
        discipline_ball = DisciplineBall()
        one_discipline = []
        for mark in forang['dises'][discipline_index]['segments'][0]['allKms']:
            alias = mark['sh']  # issue 19: если нет алиаса на последнем КМ
            if mark['sh'] in ('-', None, ' ', '') and \
                    mark['id'] == forang['dises'][discipline_index]['segments'][0]['allKms'][-1]['id']:
                alias = forang['dises'][discipline_index]['formControl']['name']

            current_grade = mark['grade']['b']
            max_grade = mark['max_ball']

            one_discipline.append({'alias': alias, 'current_grade': current_grade, 'max_grade': max_grade})
            discipline_ball.current += current_grade if my_isdigit(current_grade) else 0
            discipline_ball.might_be += max_grade if my_isdigit(max_grade) and current_grade != '-' else 0
        json_to_save.append({
            'subject': forang['dises'][discipline_index]['name'],
            'tasks': one_discipline,
            'ball': {
                'current': discipline_ball.current,
                'might_be': discipline_ball.might_be,
            }
        })
    return json_to_save


def _get_orioks_forang(raw_html: str):
    """return: [{'subject': s, 'tasks': [t], 'ball': {'current': c, 'might_be': m}, ...]"""
    bs_content = BeautifulSoup(raw_html, "html.parser")
    try:
        forang_raw = bs_content.find(id='forang').text
    except AttributeError:
        raise utils.exceptions.OrioksCantParseData
    forang = json.loads(forang_raw)

    if len(forang) == 0:
        raise utils.exceptions.OrioksEmptyForang

    try:
        json_to_save = _iterate_forang_version_with_list(forang=forang)
    except TypeError:
        json_to_save = _iterate_forang_version_with_keys(forang=forang)

    return json_to_save


async def get_orioks_marks(session: aiohttp.ClientSession):
    raw_html = await get_request(url=config.ORIOKS_PAGE_URLS['notify']['marks'], session=session)
    return _get_orioks_forang(raw_html)


async def user_marks_check(user_telegram_id: int, session: aiohttp.ClientSession) -> bool:
    """
    return is success, if not then check one more time
    """
    try:
        detailed_info = await get_orioks_marks(session=session)
    except FileNotFoundError:
        await SendToTelegram.message_to_admins(message=f'FileNotFoundError - {user_telegram_id}')
        raise Exception(f'FileNotFoundError - {user_telegram_id}')
    student_json_file = config.STUDENT_FILE_JSON_MASK.format(id=user_telegram_id)
    path_users_to_file = os.path.join(config.BASEDIR, 'users_data', 'tracking_data', 'marks', student_json_file)

    if student_json_file not in os.listdir(os.path.dirname(path_users_to_file)):
        await JsonFile.save(data=detailed_info, filename=path_users_to_file)
        return False
    old_json = await JsonFile.open(filename=path_users_to_file)
    try:
        diffs = file_compares(old_file=old_json, new_file=detailed_info)
    except utils.exceptions.FileCompareError:
        await JsonFile.save(data=detailed_info, filename=path_users_to_file)
        if old_json[0]['subject'] != detailed_info[0]['subject'] and \
                old_json[-1]['subject'] != detailed_info[-1]['subject']:
            await SendToTelegram.message_to_admins(message=f'Похоже, что начался новый семестр!')
            await SendToTelegram.text_message_to_user(
                user_telegram_id=user_telegram_id,
                message='🎉 Поздравляем с началом нового семестра и желаем успехов в учёбе!'
            )
        return False

    if len(diffs) > 0:
        for discipline_obj in get_discipline_objs_from_diff(diffs=diffs):
            photo_path = Imager().get_image_marks(
                current_grade=discipline_obj.current_grade,
                max_grade=discipline_obj.max_grade,
                title_text=discipline_obj.title_text,
                mark_change_text=discipline_obj.mark_change_text,
                side_text='Изменён балл за контрольное мероприятие'
            )
            await SendToTelegram.photo_message_to_user(
                user_telegram_id=user_telegram_id,
                photo_path=photo_path,
                caption=discipline_obj.caption
            )
            safe_delete(path=photo_path)
        await JsonFile.save(data=detailed_info, filename=path_users_to_file)
    return True
