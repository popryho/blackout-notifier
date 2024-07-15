import argparse
import asyncio
import logging
import os
from time import sleep

import telethon.hints
from emoji import emojize
from telethon import TelegramClient

logging.basicConfig(
    filename="logs.log",
    filemode="a",
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO,
)


def create_parser() -> argparse.ArgumentParser:
    """
    Create an argparse.ArgumentParser instance and add the necessary arguments for the TelegramClient.
    Returns:
        parser (argparse.ArgumentParser): The argparse parser object.
    """
    # Create a new ArgumentParser object
    parser = argparse.ArgumentParser()

    # Add the "api_id" argument
    parser.add_argument(
        "--api-id",
        type=int,
        required=True,
        help="The API ID provided by Telegram when you create a new application. "
        "You can find your API ID and API hash at https://my.telegram.org/apps. "
        "Example: 1234567",
    )

    # Add the "api_hash" argument
    parser.add_argument(
        "--api-hash",
        type=str,
        required=True,
        help="The API hash provided by Telegram when you create a new application. "
        "You can find your API ID and API hash at https://my.telegram.org/apps. "
        "Example: 0123456789abcdef0123456789abcdef",
    )

    # Add the "session-name" argument
    parser.add_argument(
        "--session-name",
        type=str,
        default="my_account",
        help="The name of the session file to use for saving the authorization information",
    )

    # Add the "ip" argument
    parser.add_argument("--ip", type=str, help="The IP address or hostname to ping")

    # Add the "count" argument
    parser.add_argument(
        "--n-packets",
        type=int,
        default=20,
        help="The number of packets to send (default: 20)",
    )

    # Add the "wait" argument
    parser.add_argument(
        "--wait-time",
        type=float,
        default=1,
        help="The time to wait between packets in seconds (default: 1)",
    )

    # Add the "chat_id" argument
    parser.add_argument(
        "--chat-id",
        type=int,
        required=True,
        default=-1001178379217,
        help="The chat ID of the chat to get the entity for",
    )
    # Add the "my_chat_id" argument
    parser.add_argument(
        "--my-chat-id", type=int, help="The chat ID of the chat to get the entity for"
    )

    # Return the parser object
    return parser


def is_host_up(ip: str = "8.8.8.8", n_packets: int = 20, wait_time: int = 1) -> bool:
    """
    Send 20 packets using ICMP, wait for response 1 second
    If there is at least one packet, that was delivered successfully:
        os system function return 0, else not
    :param wait_time: -W Time in seconds to wait for a reply for each packet sent.
    If a reply arrives later, the packet is not printed as replied,
    but considered as replied when calculating statistics.
    :param n_packets: -c count packets for echo response
    :param ip: ip address to ping
    :return: True or False whether ping was successful or not
    """
    return (
        True
        if os.system(f"ping -c {n_packets} -W {wait_time} {ip} > /dev/null 2>&1") == 0
        else False
    )


async def is_dtek_message_forwarded(
    client: TelegramClient, forwarded_chat: telethon.hints.Entity
) -> bool:
    """
    Get message from DTEK
        Check if it starts with red exclamation mark
        Forward it to recipient
    :param client: Telegram Client
    :param forwarded_chat: telegram chat,
        to which the message should be forwarded
    :return: True if dtek message is forwarded,
        else return False
    """
    # get dtek bot entity
    dtek_bot = await client.get_entity("https://t.me/DTEKKyivskielectromerezhibot")

    # send request for power status update
    await client.send_message(dtek_bot, emojize(":light_bulb:Можливі відключення"))

    # wait 25 seconds in order to receive response and
    # in some way avoid flood in case of regular request
    sleep(25)
    dtek_message: telethon.custom.message.Message | None = await get_dtek_message(
        client, dtek_bot
    )

    if dtek_message is None:
        return False
    # log dtek message
    logging.info(dtek_message.text)

    await client.forward_messages(forwarded_chat, dtek_message)
    return True


async def get_dtek_message(
    client: TelegramClient, dtek_bot: telethon.hints.Entity
) -> telethon.custom.message.Message | None:
    """
    Get last two messages from DTEK
        - sort them by id
        - get the first one
        - check if it starts with exclamation mark
        - return the message or None

    :param client: Telegram Client
    :param dtek_bot: Telegram entity, from which the response is expected
    :return: telegram message entity,
        if it starts with the red exclamation mark
        else return None
    """
    messages = await client.get_messages(dtek_bot, limit=2)
    # Sort the messages by date
    sorted_messages = sorted(messages, key=lambda m: m.id)
    dtek_message = sorted_messages[0]
    # check if message startswith red exclamation mark and forward it
    if dtek_message.text.startswith(emojize(":red_exclamation_mark:")):
        return dtek_message
    else:
        return None


async def work(config):
    async with TelegramClient(
        config.session_name, config.api_id, config.api_hash
    ) as client:
        # get telethon entities
        if config.my_chat_id:
            my_chat = await client.get_entity(config.my_chat_id)
        osbb_chat = await client.get_entity(config.chat_id)
        # set last states
        last_host_state_up = True
        dtek_message_forwarded = True
        while True:
            # forward message from dtek if host is down
            # and the message wasn't forwarded yet
            if not last_host_state_up and not dtek_message_forwarded:
                dtek_message_forwarded = await is_dtek_message_forwarded(
                    client, osbb_chat
                )

            # get host state
            host_status = is_host_up(
                ip=config.ip, n_packets=config.n_packets, wait_time=config.wait_time
            )
            if host_status == last_host_state_up:
                continue
            last_host_state_up = host_status

            # notify entity members
            sentence = emojize(
                ":green_circle: світло є" if host_status else ":red_circle: світла нема"
            )
            logging.info(sentence)
            if config.my_chat_id:
                await client.send_message(await client.get_entity(my_chat), sentence)
            await client.send_message(osbb_chat, sentence)

            dtek_message_forwarded = False

        await client.run_until_disconnected()


async def main():
    args = create_parser().parse_args()
    await work(args)


asyncio.run(main())
