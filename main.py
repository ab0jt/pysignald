import json
import random
import re
import socket
from typing import Iterator, List  # noqa

from .types import Attachment, Message

# We'll need to know the compiled RE object later.
RE_TYPE = type(re.compile(""))


def readlines(s: socket.socket) -> Iterator[bytes]:
    "Read a socket, line by line."
    buf = []  # type: List[bytes]
    while True:
        char = s.recv(1)
        if not char:
            raise ConnectionResetError("connection was reset")

        if char == b"\n":
            yield b"".join(buf)
            buf = []
        else:
            buf.append(char)


class Signal:
    def __init__(self, username, socket_path="/var/run/signald/signald.sock"):
        self.username = username
        self.socket_path = socket_path
        self._chat_handlers = []

    def _get_id(self):
        "Generate a random ID."
        return "".join(random.choice("abcdefghijklmnopqrstuvwxyz0123456789") for _ in range(10))

    def _get_socket(self) -> socket.socket:
        "Create a socket, connect to the server and return it."

        # Support TCP sockets on the sly.
        if isinstance(self.socket_path, tuple):
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        else:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.connect(self.socket_path)
        return s

    def _send_command(self, payload: dict, block: bool = False):
        s = self._get_socket()
        msg_id = self._get_id()
        payload["id"] = msg_id
        s.recv(1024)  # Flush the buffer.
        s.send(json.dumps(payload).encode("utf8") + b"\n")

        if not block:
            return

        response = s.recv(4 * 1024)
        for line in response.split(b"\n"):
            if msg_id.encode("utf8") not in line:
                continue

            data = json.loads(line)

            if data.get("id") != msg_id:
                continue

            if data["type"] == "unexpected_error":
                raise ValueError("unexpected error occurred")
            
            return(data)

    def register(self, voice=False):
        """
        Register the given number.

        voice: Whether to receive a voice call or an SMS for verification.
        """
        payload = {"type": "register", "username": self.username, "voice": voice}
        self._send_command(payload)

    def verify(self, code: str):
        """
        Verify the given number by entering the code you received.

        code: The code Signal sent you.
        """
        payload = {"type": "verify", "username": self.username, "code": code}
        self._send_command(payload)

    def receive_messages(self) -> Iterator[Message]:
        "Keep returning received messages."
        s = self._get_socket()
        s.send(json.dumps({"type": "subscribe", "username": self.username}).encode("utf8") + b"\n")

        for line in readlines(s):
            try:
                message = json.loads(line.decode())
            except json.JSONDecodeError:
                print("Invalid JSON")

            #print(json.dumps(message))
            if message.get("type") != "message":
                # If the message type isn't "message", we don't care about it.
                continue
            elif message.get("type") == "message" and 'typing' in message['data']:
                # We don't care about typing notifications
                continue

            #print(json.dumps(message))
            message = message["data"]
            data_message = message.get("dataMessage", {})

            yield Message(
                username=message.get("username", ""),
                source=message.get("source", ""),
                text=data_message.get("body", ""),
                source_device=message.get("sourceDevice"),
                timestamp=data_message.get("timestamp", None),
                timestamp_iso=message.get("timestampISO", None),
                group_info=data_message.get("group", {}),
                attachments=[
                    Attachment(
                        content_type=attachment["contentType"],
                        id=attachment["id"],
                        size=attachment["size"],
                        stored_filename=attachment["storedFilename"],
                    )
                    for attachment in data_message.get("attachments", [])
                ],
            )

    def get_group_list(self, block: bool = True) -> None:
        """
        Get a list of groups that the bot is a member of.

        Response is monitored for in self.receive_messages()
        """
        payload = {"type": "list_groups", "username": self.username}
        data = self._send_command(payload, block)
        return(data)

    def mark_read(self, recipient: str, timestamps: list, block: bool = False) -> None:
        """
        Mark a message as read.

        recipient:  The recipient's phone number, in E.123 format.
        timestamps: A list of timestamp integers found in the message originally sent.
        """
        payload = {"type": "mark_read", "username": self.username, "recipientAddress": recipient, "timestamps": timestamps}
        self._send_command(payload, block)

    def send_message(self, recipient: str, text: str, block: bool = True) -> None:
        """
        Send a message.

        recipient: The recipient's phone number, in E.123 format.
        text:      The text of the message to send.
        block:     Whether to block while sending. If you choose not to block, you won't get an exception if there
                   are any errors.
        """
        payload = {"type": "send", "username": self.username, "recipientAddress": recipient, "messageBody": text}
        self._send_command(payload, block)

    def send_group_message(self, recipient_group_id: str, text: str, block: bool = False) -> None:
        """
        Send a group message.

        recipient_group_id: The base64 encoded group ID to send to.
        text:               The text of the message to send.
        block:              Whether to block while sending. If you choose not to block, you won't get an exception if
                            there are any errors.
        """
        payload = {
            "type": "send",
            "username": self.username,
            "recipientGroupId": recipient_group_id,
            "messageBody": text,
        }
        self._send_command(payload, block)

    def chat_handler(self, regex, order=100):
        """
        A decorator that registers a chat handler function with a regex.
        """
        if not isinstance(regex, RE_TYPE):
            regex = re.compile(regex, re.I)

        def decorator(func):
            self._chat_handlers.append((order, regex, func))
            # Use only the first value to sort so that declaration order doesn't change.
            self._chat_handlers.sort(key=lambda x: x[0])
            return func

        return decorator

    def run_chat(self):
        """
        Start the chat event loop.
        """
        for message in self.receive_messages():
            if not message.text:
                continue

            for _, regex, func in self._chat_handlers:
                match = re.search(regex, message.text)
                if not match:
                    continue

                try:
                    reply = func(message, match)
                except:  # noqa - We don't care why this failed.
                    continue

                if isinstance(reply, tuple):
                    stop, reply = reply
                else:
                    stop = True

                # In case a message came from a group chat
                group_id = message.group_info.get("groupId")

                if group_id:
                    self.send_group_message(recipient_group_id=group_id, text=reply)
                else:
                    self.send_message(recipient=message.source, text=reply)

                if stop:
                    # We don't want to continue matching things.
                    break
