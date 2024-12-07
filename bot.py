from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    ReplyKeyboardMarkup, KeyboardButton
)
from telegram.ext import (
    Application, CommandHandler, ContextTypes,
    ConversationHandler, MessageHandler, CallbackQueryHandler, filters
)
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
import datetime
from dateutil import parser
from pytz import timezone
import matplotlib.pyplot as plt
import io
import os
from dotenv import load_dotenv
load_dotenv()


token = os.getenv("TELEGRAM_TOKEN")
creds_file = os.getenv("GOOGLE_CLIENT_SECRET_FILE")

SCOPES = ['https://www.googleapis.com/auth/calendar']

def authenticate_google():
    flow = InstalledAppFlow.from_client_secrets_file(
        creds_file, SCOPES)
    creds = flow.run_local_server(port=8080)
    service = build('calendar', 'v3', credentials=creds)
    return service

calendar_service = authenticate_google()
local_tz = timezone("Europe/Moscow")

(
    TITLE, DATE, TIME, END_TIME, ATTENDEES, DESCRIPTION,
    FIND_TIME_DATE, FIND_TIME_DURATION, FIND_TIME_ATTENDEES, FIND_TIME_HOURS, FIND_TIME_SELECT_SLOT,
    FIND_TIME_CONFIRM_OVERLAP,
    STATS_DATE_RANGE,
    MODIFY_SELECT_EVENT, MODIFY_CHOICE, MODIFY_FIELD,
    TODAY_DATE
) = range(17)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    menu_buttons = [
        [KeyboardButton("📅 Добавить событие"), KeyboardButton("✏️ Изменить событие")],
        [KeyboardButton("🔍 Найти свободное время"), KeyboardButton("📊 Статистика")],
        [KeyboardButton("📖 Расписание на день"), KeyboardButton("🚫 Отмена")]
    ]
    reply_markup = ReplyKeyboardMarkup(menu_buttons, resize_keyboard=True)
    await update.message.reply_text(
        "Выберите действие:",
        reply_markup=reply_markup
    )

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Операция отменена.")
    return ConversationHandler.END

async def add_event_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Введите название события:")
    return TITLE

async def get_title(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['title'] = update.message.text
    await update.message.reply_text("Введите дату события (ГГГГ-ММ-ДД):")
    return DATE

async def get_date(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['date'] = update.message.text
    await update.message.reply_text("Введите время начала события (ЧЧ:ММ):")
    return TIME

async def get_time(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['time'] = update.message.text
    await update.message.reply_text("Введите время окончания события (ЧЧ:ММ):")
    return END_TIME

async def get_end_time(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['end_time'] = update.message.text
    await update.message.reply_text("Введите email участников через запятую (или 'нет'):")
    return ATTENDEES

async def get_attendees(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    attendees_text = update.message.text
    if attendees_text.lower() != 'нет':
        context.user_data['attendees'] = attendees_text
    else:
        context.user_data['attendees'] = ''
    await update.message.reply_text("Добавить описание события? (да/нет)")
    return DESCRIPTION

async def get_description(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message.text.lower() == 'да':
        await update.message.reply_text("Введите описание события:")
        return DESCRIPTION
    else:
        context.user_data['description'] = ''
        return await create_event(update, context)

async def get_description_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['description'] = update.message.text
    return await create_event(update, context)

async def send_event_notification(context: ContextTypes.DEFAULT_TYPE):
    job_data = context.job.data
    chat_id = job_data['chat_id']
    event_title = job_data['title']
    event_time = job_data['time']
    await context.bot.send_message(chat_id=chat_id, text=f"Напоминание: '{event_title}' в {event_time}")

async def create_event(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        start_datetime_str = f"{context.user_data['date']}T{context.user_data['time']}:00"
        end_datetime_str = f"{context.user_data['date']}T{context.user_data['end_time']}:00"
        start_datetime = local_tz.localize(datetime.datetime.strptime(start_datetime_str, "%Y-%m-%dT%H:%M:%S"))
        end_datetime = local_tz.localize(datetime.datetime.strptime(end_datetime_str, "%Y-%m-%dT%H:%M:%S"))

        attendees = [{'email': email.strip()} for email in context.user_data['attendees'].split(',') if email.strip()]
        description = context.user_data.get('description', '')

        # Проверка пересечения с уже существующими событиями
        if await check_event_overlap(start_datetime, end_datetime, attendees):
            # Есть пересечение, спрашиваем подтверждение
            context.user_data['pending_event'] = {
                'summary': context.user_data['title'],
                'description': description,
                'start': start_datetime,
                'end': end_datetime,
                'attendees': attendees
            }
            buttons = [
                [InlineKeyboardButton("Да", callback_data='confirm_yes'),
                 InlineKeyboardButton("Нет", callback_data='confirm_no')]
            ]
            await update.message.reply_text("Время пересекается с другим событием. Вы уверены?",
                                            reply_markup=InlineKeyboardMarkup(buttons))
            return FIND_TIME_CONFIRM_OVERLAP
        else:
            event = {
                'summary': context.user_data['title'],
                'description': description,
                'start': {'dateTime': start_datetime.isoformat()},
                'end': {'dateTime': end_datetime.isoformat()},
                'attendees': attendees,
                'reminders': {
                    'useDefault': False,
                    'overrides': [
                        {'method': 'email', 'minutes': 15},
                        {'method': 'popup', 'minutes': 15}
                    ]
                }
            }
            created_event = calendar_service.events().insert(calendarId='primary', body=event).execute()
            await update.message.reply_text(f"Событие создано: {created_event.get('htmlLink', 'Нет ссылки')}")

            notification_time = start_datetime - datetime.timedelta(minutes=5)
            now = datetime.datetime.now(local_tz)
            if notification_time > now:
                context.job_queue.run_once(
                    send_event_notification,
                    when=notification_time,
                    data={
                        'chat_id': update.effective_chat.id,
                        'title': context.user_data['title'],
                        'time': context.user_data['time']
                    },
                    name=f"event_notification_{created_event['id']}"
                )
                await update.message.reply_text("Напоминание будет отправлено за 5 минут до начала.")
            return ConversationHandler.END
    except Exception as e:
        await update.message.reply_text(f"Ошибка при создании: {e}")
        return ConversationHandler.END

async def check_event_overlap(start_dt, end_dt, attendees_emails):
    # Проверяем пересечения для всех участников
    # Чтобы учесть всех, делаем freebusy запрос для всех участников.
    items = [{'id': 'primary'}]
    for em in attendees_emails:
        items.append({'id': em})

    body = {
        "timeMin": start_dt.isoformat(),
        "timeMax": end_dt.isoformat(),
        "items": items,
        "timeZone": 'Europe/Moscow'
    }

    freebusy_result = calendar_service.freebusy().query(body=body).execute()
    calendars = freebusy_result.get('calendars', {})

    # Если на этом интервале у кого-то есть busy, значит пересечение есть.
    for cal_id, data in calendars.items():
        if data.get('busy', []):
            return True
    return False

async def confirm_overlap(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    choice = query.data
    if choice == 'confirm_yes':
        pending_event = context.user_data.get('pending_event')
        if not pending_event:
            await query.edit_message_text("Ошибка: нет данных события.")
            return ConversationHandler.END
        event_body = {
            'summary': pending_event['summary'],
            'description': pending_event['description'],
            'start': {'dateTime': pending_event['start'].isoformat()},
            'end': {'dateTime': pending_event['end'].isoformat()},
            'attendees': pending_event['attendees'],
            'reminders': {
                'useDefault': False,
                'overrides': [
                    {'method': 'email', 'minutes': 15},
                    {'method': 'popup', 'minutes': 15}
                ]
            }
        }
        created_event = calendar_service.events().insert(calendarId='primary', body=event_body).execute()
        await query.edit_message_text(f"Событие создано: {created_event.get('htmlLink', 'Нет ссылки')}")
    else:
        await query.edit_message_text("Создание события отменено.")
    return ConversationHandler.END

async def modify_event_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Получаю список предстоящих событий на год...")
    now = datetime.datetime.now(local_tz)
    end_of_year = local_tz.localize(datetime.datetime(now.year, 12, 31, 23, 59, 59))

    events_result = calendar_service.events().list(
        calendarId='primary',
        timeMin=now.isoformat(),
        timeMax=end_of_year.isoformat(),
        maxResults=20,
        singleEvents=True,
        orderBy='startTime'
    ).execute()
    events = events_result.get('items', [])

    if not events:
        await update.message.reply_text("Нет предстоящих событий для изменения.")
        return ConversationHandler.END

    buttons = []
    context.user_data['events'] = {}
    for event in events:
        event_id = event['id']
        start = event['start'].get('dateTime', event['start'].get('date'))
        start_dt = parser.isoparse(start).astimezone(local_tz)
        title = event.get('summary', 'Без названия')
        button_text = f"{title} ({start_dt.strftime('%Y-%m-%d %H:%M')})"
        buttons.append([InlineKeyboardButton(button_text, callback_data=event_id)])
        context.user_data['events'][event_id] = event

    reply_markup = InlineKeyboardMarkup(buttons)
    await update.message.reply_text("Выберите событие:", reply_markup=reply_markup)
    return MODIFY_SELECT_EVENT

async def select_event_to_modify(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    event_id = query.data
    context.user_data['selected_event_id'] = event_id

    buttons = [
        [InlineKeyboardButton("Изменить дату/время", callback_data='datetime')],
        [InlineKeyboardButton("Изменить название", callback_data='title')],
        [InlineKeyboardButton("Изменить описание", callback_data='description')],
        [InlineKeyboardButton("Удалить событие", callback_data='delete')],
    ]
    reply_markup = InlineKeyboardMarkup(buttons)
    await query.edit_message_text("Что изменить?", reply_markup=reply_markup)
    return MODIFY_CHOICE

async def modify_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    choice = query.data
    context.user_data['modify_choice'] = choice

    if choice == 'datetime':
        await query.edit_message_text("Введите новую дату и время начала (ГГГГ-ММ-ДД ЧЧ:ММ):")
    elif choice == 'title':
        await query.edit_message_text("Введите новое название:")
    elif choice == 'description':
        await query.edit_message_text("Введите новое описание:")
    elif choice == 'delete':
        event_id = context.user_data['selected_event_id']
        calendar_service.events().delete(calendarId='primary', eventId=event_id).execute()
        await query.edit_message_text("Событие удалено.")
        return ConversationHandler.END
    return MODIFY_FIELD

async def modify_field(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    event_id = context.user_data['selected_event_id']
    event = context.user_data['events'][event_id]
    choice = context.user_data['modify_choice']
    new_value = update.message.text

    if choice == 'datetime':
        try:
            start_datetime = local_tz.localize(
                datetime.datetime.strptime(new_value, "%Y-%m-%d %H:%M")
            )
            duration = parser.isoparse(event['end']['dateTime']) - parser.isoparse(event['start']['dateTime'])
            end_datetime = start_datetime + duration
            event['start']['dateTime'] = start_datetime.isoformat()
            event['end']['dateTime'] = end_datetime.isoformat()
        except Exception as e:
            await update.message.reply_text(f"Ошибка даты/времени: {e}")
            return ConversationHandler.END
    elif choice == 'title':
        event['summary'] = new_value
    elif choice == 'description':
        event['description'] = new_value

    updated_event = calendar_service.events().update(calendarId='primary', eventId=event_id, body=event).execute()
    await update.message.reply_text(f"Событие обновлено: {updated_event.get('htmlLink', 'Нет ссылки')}")
    return ConversationHandler.END

async def find_time_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Введите дату (ГГГГ-ММ-ДД):")
    return FIND_TIME_DATE

async def find_time_date(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['date'] = update.message.text
    await update.message.reply_text("Введите желаемую продолжительность встречи в минутах:")
    return FIND_TIME_DURATION

async def find_time_duration(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['duration'] = int(update.message.text)
    await update.message.reply_text("Введите email участников через запятую:")
    return FIND_TIME_ATTENDEES

async def find_time_attendees(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['attendees'] = update.message.text
    await update.message.reply_text("Введите желаемый интервал времени, например '10:00-18:00':")
    return FIND_TIME_HOURS

async def find_time_hours(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    hours_str = update.message.text
    try:
        start_h_str, end_h_str = hours_str.split('-')
        start_hh, start_mm = map(int, start_h_str.strip().split(':'))
        end_hh, end_mm = map(int, end_h_str.strip().split(':'))
        context.user_data['start_hour'] = start_hh
        context.user_data['start_minute'] = start_mm
        context.user_data['end_hour'] = end_hh
        context.user_data['end_minute'] = end_mm
    except:
        await update.message.reply_text("Неверный формат. Введите диапазон, например '10:00-18:00':")
        return FIND_TIME_HOURS
    return await find_time_process(update, context)

async def find_time_process(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        date_str = context.user_data['date']
        target_date = datetime.datetime.strptime(date_str, "%Y-%m-%d").date()
        duration = datetime.timedelta(minutes=context.user_data['duration'])

        start_hour = context.user_data['start_hour']
        start_minute = context.user_data['start_minute']
        end_hour = context.user_data['end_hour']
        end_minute = context.user_data['end_minute']

        start_datetime = local_tz.localize(datetime.datetime.combine(target_date, datetime.time(start_hour, start_minute)))
        end_datetime = local_tz.localize(datetime.datetime.combine(target_date, datetime.time(end_hour, end_minute)))

        attendees_emails = [email.strip() for email in context.user_data['attendees'].split(',') if email.strip()]

        # Собираем все календари (включая свой) для проверки
        items = [{"id": 'primary'}]
        for em in attendees_emails:
            items.append({"id": em})

        body = {
            "timeMin": start_datetime.isoformat(),
            "timeMax": end_datetime.isoformat(),
            "items": items,
            "timeZone": 'Europe/Moscow'
        }

        freebusy_result = calendar_service.freebusy().query(body=body).execute()
        calendars = freebusy_result.get('calendars', {})

        # Собираем занятое время всех участников
        busy_times = []
        for cal_id in calendars:
            busy_times.extend(calendars[cal_id].get('busy', []))

        # Объединяем занятые интервалы
        busy_times = sorted(busy_times, key=lambda x: x['start'])
        merged_busy_times = []
        for busy in busy_times:
            busy_start = parser.isoparse(busy['start']).astimezone(local_tz)
            busy_end = parser.isoparse(busy['end']).astimezone(local_tz)
            if not merged_busy_times or busy_start > merged_busy_times[-1]['end']:
                merged_busy_times.append({'start': busy_start, 'end': busy_end})
            else:
                merged_busy_times[-1]['end'] = max(merged_busy_times[-1]['end'], busy_end)

        # Ищем свободные слоты - те, которые не пересекаются ни с одним занятым интервалом
        free_slots = []
        current_time = start_datetime
        while current_time + duration <= end_datetime:
            slot_end = current_time + duration
            overlap = False
            for busy in merged_busy_times:
                if (current_time < busy['end']) and (slot_end > busy['start']):
                    overlap = True
                    break
            if not overlap:
                free_slots.append((current_time, slot_end))
            current_time += datetime.timedelta(minutes=15)

        if not free_slots:
            await update.message.reply_text("Нет доступных слотов. Проверить другой день? (да/нет)")
            return FIND_TIME_DATE
        else:
            buttons = []
            for idx, (start, end) in enumerate(free_slots):
                button_text = f"{start.strftime('%H:%M')} - {end.strftime('%H:%M')}"
                buttons.append([InlineKeyboardButton(button_text, callback_data=str(idx))])

            reply_markup = InlineKeyboardMarkup(buttons)
            await update.message.reply_text("Выберите время:", reply_markup=reply_markup)
            context.user_data['free_slots'] = free_slots
            context.user_data['attendees_emails'] = attendees_emails
            return FIND_TIME_SELECT_SLOT

    except ValueError as ve:
        await update.message.reply_text(f"Ошибка даты: {ve}")
        return FIND_TIME_DATE
    except Exception as e:
        await update.message.reply_text(f"Ошибка: {e}")
        return ConversationHandler.END

async def select_time_slot(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    slot_idx = int(query.data)
    selected_slot = context.user_data['free_slots'][slot_idx]
    start_time = selected_slot[0]
    end_time = selected_slot[1]
    attendees = [{'email': email} for email in context.user_data['attendees_emails']]

    # Проверяем еще раз пересечение перед созданием (на случай изменения)
    if await check_event_overlap(start_time, end_time, context.user_data['attendees_emails']):
        context.user_data['pending_event'] = {
            'summary': 'Встреча',
            'description': '',
            'start': start_time,
            'end': end_time,
            'attendees': attendees
        }
        buttons = [
            [InlineKeyboardButton("Да", callback_data='confirm_yes'),
             InlineKeyboardButton("Нет", callback_data='confirm_no')]
        ]
        await query.edit_message_text("Есть пересечение с другим событием. Продолжить?",
                                      reply_markup=InlineKeyboardMarkup(buttons))
        return FIND_TIME_CONFIRM_OVERLAP
    else:
        event = {
            'summary': 'Встреча',
            'start': {'dateTime': start_time.isoformat()},
            'end': {'dateTime': end_time.isoformat()},
            'attendees': attendees,
            'reminders': {
                'useDefault': False,
                'overrides': [
                    {'method': 'email', 'minutes': 15},
                    {'method': 'popup', 'minutes': 15}
                ]
            }
        }
        created_event = calendar_service.events().insert(calendarId='primary', body=event).execute()
        await query.edit_message_text(f"Встреча создана: {created_event.get('htmlLink', 'Нет ссылки')}")
        return ConversationHandler.END

async def stats_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Введите диапазон дат (ГГГГ-ММ-ДД - ГГГГ-ММ-ДД):")
    return STATS_DATE_RANGE

async def stats_process(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        date_range = update.message.text.strip()
        if " - " not in date_range:
            await update.message.reply_text("Формат: ГГГГ-ММ-ДД - ГГГГ-ММ-ДД")
            return STATS_DATE_RANGE

        start_date_str, end_date_str = map(str.strip, date_range.split(" - "))
        start_date = datetime.datetime.strptime(start_date_str, "%Y-%m-%d")
        end_date = datetime.datetime.strptime(end_date_str, "%Y-%m-%d") + datetime.timedelta(days=1, seconds=-1)
        time_min = start_date.isoformat() + 'Z'
        time_max = end_date.isoformat() + 'Z'

        events_result = calendar_service.events().list(
            calendarId='primary',
            timeMin=time_min,
            timeMax=time_max,
            singleEvents=True,
            orderBy='startTime',
            showDeleted=True
        ).execute()
        events = events_result.get('items', [])

        event_count = len([e for e in events if e.get('status') != 'cancelled'])
        rescheduled_count = sum(1 for e in events if e.get('status') == 'cancelled')
        total_duration = sum(
            (parser.isoparse(e['end']['dateTime']) - parser.isoparse(e['start']['dateTime'])).total_seconds()
            for e in events if 'dateTime' in e['start'] and 'dateTime' in e['end'] and e.get('status') != 'cancelled'
        ) / 3600

        await update.message.reply_text(
            f"Статистика {start_date_str} - {end_date_str}:\n"
            f"Событий: {event_count}\n"
            f"Продолжительность: {total_duration:.2f} ч\n"
            f"Перенесено: {rescheduled_count}"
        )

        date_durations = {}
        for e in events:
            if e.get('status') == 'cancelled':
                continue
            start_dt = parser.isoparse(e['start']['dateTime']).astimezone(local_tz).date()
            duration = (parser.isoparse(e['end']['dateTime']) - parser.isoparse(e['start']['dateTime'])).total_seconds() / 3600
            date_durations[start_dt] = date_durations.get(start_dt, 0) + duration

        dates = sorted(date_durations.keys())
        durations = [date_durations[d] for d in dates]

        plt.figure(figsize=(10, 5))
        plt.bar(dates, durations, width=0.6)
        plt.xlabel('Дата')
        plt.ylabel('Часы')
        plt.title('Загруженность по дням')
        plt.xticks(rotation=45)
        plt.tight_layout()
        buf = io.BytesIO()
        plt.savefig(buf, format='png')
        buf.seek(0)
        await update.message.reply_photo(photo=buf)
        plt.close()

    except Exception as e:
        await update.message.reply_text(f"Ошибка: {e}")
    return ConversationHandler.END

async def today(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Введите дату (ГГГГ-ММ-ДД) или 'сегодня':")
    return TODAY_DATE

async def get_today_schedule(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    date_input = update.message.text.strip()
    if date_input.lower() == 'сегодня':
        target_date = datetime.datetime.now(local_tz).date()
    else:
        try:
            target_date = datetime.datetime.strptime(date_input, "%Y-%m-%d").date()
        except ValueError:
            await update.message.reply_text("Неверный формат. Попробуйте ещё раз.")
            return TODAY_DATE

    time_min = local_tz.localize(datetime.datetime.combine(target_date, datetime.time.min)).isoformat()
    time_max = local_tz.localize(datetime.datetime.combine(target_date, datetime.time.max)).isoformat()

    events_result = calendar_service.events().list(
        calendarId='primary',
        timeMin=time_min,
        timeMax=time_max,
        singleEvents=True,
        orderBy='startTime'
    ).execute()
    events = events_result.get('items', [])

    if not events:
        await update.message.reply_text("На этот день нет событий.")
        return ConversationHandler.END

    schedule_text = f"Расписание на {target_date}:\n"
    for event in events:
        start = event['start'].get('dateTime', event['start'].get('date'))
        start_dt = parser.isoparse(start).astimezone(local_tz)
        title = event.get('summary', 'Без названия')
        schedule_text += f"- {start_dt.strftime('%H:%M')} {title}\n"

    await update.message.reply_text(schedule_text)
    return ConversationHandler.END

def main():
    application = Application.builder().token(token).build()

    add_event_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex('^📅 Добавить событие$'), add_event_start)],
        states={
            TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_title)],
            DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_date)],
            TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_time)],
            END_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_end_time)],
            ATTENDEES: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_attendees)],
            DESCRIPTION: [
                MessageHandler(filters.Regex('^(да|нет)$'), get_description),
                MessageHandler(filters.TEXT & ~filters.COMMAND, get_description_text)
            ],
            FIND_TIME_CONFIRM_OVERLAP: [CallbackQueryHandler(confirm_overlap)]
        },
        fallbacks=[MessageHandler(filters.Regex('^🚫 Отмена$'), cancel)],
    )

    modify_event_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex('^✏️ Изменить событие$'), modify_event_start)],
        states={
            MODIFY_SELECT_EVENT: [CallbackQueryHandler(select_event_to_modify)],
            MODIFY_CHOICE: [CallbackQueryHandler(modify_choice)],
            MODIFY_FIELD: [MessageHandler(filters.TEXT & ~filters.COMMAND, modify_field)],
        },
        fallbacks=[MessageHandler(filters.Regex('^🚫 Отмена$'), cancel)],
    )

    find_time_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex('^🔍 Найти свободное время$'), find_time_start)],
        states={
            FIND_TIME_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, find_time_date)],
            FIND_TIME_DURATION: [MessageHandler(filters.TEXT & ~filters.COMMAND, find_time_duration)],
            FIND_TIME_ATTENDEES: [MessageHandler(filters.TEXT & ~filters.COMMAND, find_time_attendees)],
            FIND_TIME_HOURS: [MessageHandler(filters.TEXT & ~filters.COMMAND, find_time_hours)],
            FIND_TIME_SELECT_SLOT: [CallbackQueryHandler(select_time_slot)],
            FIND_TIME_CONFIRM_OVERLAP: [CallbackQueryHandler(confirm_overlap)]
        },
        fallbacks=[MessageHandler(filters.Regex('^🚫 Отмена$'), cancel)],
    )

    stats_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex('^📊 Статистика$'), stats_start)],
        states={
            STATS_DATE_RANGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, stats_process)],
        },
        fallbacks=[MessageHandler(filters.Regex('^🚫 Отмена$'), cancel)],
    )

    today_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex('^📖 Расписание на день$'), today)],
        states={
            TODAY_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_today_schedule)],
        },
        fallbacks=[MessageHandler(filters.Regex('^🚫 Отмена$'), cancel)],
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(add_event_handler)
    application.add_handler(modify_event_handler)
    application.add_handler(find_time_handler)
    application.add_handler(stats_handler)
    application.add_handler(today_handler)
    application.add_handler(CommandHandler("cancel", cancel))
    application.add_handler(MessageHandler(filters.Regex('^🚫 Отмена$'), cancel))

    application.run_polling()

if __name__ == "__main__":
    main()
