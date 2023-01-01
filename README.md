# Blackout notifier script

This is a blackout notifier script that ping specified ip address and send notification usnig `telethon` library which helps to interact with the Telegram API.

## Prerequisites

- Python 3.9 or higher
- [Poetry](https://python-poetry.org/)

## Installation

1. Create a new virtual environment:
```bash
python3 -m venv env
```

2. Activate the virtual environment:

```bash
source env/bin/activate
```

3. Install the dependencies using Poetry:

```bash
poetry install
```

## Usage

To run the script, use the following command:

```bash
python3 main.py --api-id YOUR_API_ID --api-hash YOUR_API_HASH --session-name YOUR_SESSION_NAME --chat-id YOUR_CHAT_ID --ip YOUR_IP_ADDRESS
```

Replace YOUR_API_ID, YOUR_API_HASH, YOUR_SESSION_NAME, YOUR_CHAT_ID, and YOUR_IP_ADDRESS with your own values.

- The `api_id` and `api_hash` arguments are provided by Telegram when you create a new application. You can find your API ID and API hash at https://my.telegram.org/apps.
- The `session_name` argument is the name of the session file to use for saving the authorization information.
- The `chat-id` argument is the ID of the Telegram chat where the script will send messages. You can find the chat ID by using the telethon library to get the entity for the chat.
- The `ip` argument is the IP address that the script will ping.

## Example Output

The script will connect to the Telegram servers and perform some actions using the telethon library. The output of the
script will depend on the actions that it performs.

## Additional Arguments

You can also specify additional arguments to customize the behavior of the script. For example:

```bash
python main.py --api-id YOUR_API_ID --api-hash YOUR_API_HASH --session-name YOUR_SESSION_NAME --chat-id YOUR_CHAT_ID --ip YOUR_IP_ADDRESS --n-packets 20 --wait-time 1
```

The `--n-packets` argument specifies the number of packets to send, and the `--wait-time` argument specifies the time to wait
between packets in seconds.

## Cleaning Up

To deactivate the virtual environment and remove it, use the following commands:

```bash
deactivate
rm -rf env
```

This will deactivate the virtual environment and delete the env directory, effectively removing the virtual environment.
