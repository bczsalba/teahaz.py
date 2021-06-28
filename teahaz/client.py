"""
teahaz.client
----------------
author: bczsalba


The module containing the main objects for the Teahaz API wrapper
"""

# pylint: disable=too-many-instance-attributes

from __future__ import annotations

from time import sleep
from enum import Enum, auto
from threading import Thread
from dataclasses import dataclass
from typing import Callable, Any, Union, Optional

import requests

__all__ = [
    "Event",
    "Teacup",
    "Chatroom",
    "Channel",
    "Message",
]

MessageCallback = Callable[["Message", "Chatroom"], Any]
ErrorCallback = Callable[[requests.Response, str, dict[str, Any]], Any]
ExceptionCallback = Callable[[Exception, str, dict[str, Any]], Any]
EventCallback = Union[MessageCallback, ErrorCallback]


class Event(Enum):
    """Events that `Chatroom` and `Teacup` can subscribe to"""

    ERROR = auto()
    MSG_NEW = auto()
    MSG_DEL = auto()
    MSG_SYS = auto()
    MSG_SENT = auto()
    USER_JOIN = auto()
    USER_LEAVE = auto()
    SERVER_INFO = auto()
    MSG_SYS_SILENT = auto()
    NETWORK_EXCEPTION = auto()


class EndpointContainer:
    """Endpoints of the Teahaz API"""

    _items = {
        "base": "{url}/api/v0",
        "login": "{base}/login/{chatroom_id}",
        "chatroom": "{base}/chatroom",
        "files": "{base}/files/{chatroom_id}",
        "messages": "{base}/messages/{chatroom_id}",
        "channels": "{base}/channels/{chatroom_id}",
    }

    def __init__(self, url: str, uid: Optional[str] = None) -> None:
        """Create object"""

        self._url = url
        self._uid = uid

    def set(self, item: str, value: str) -> None:
        """Set normally private argument"""

        item = "_" + item
        if not item in dir(self):
            raise KeyError(f"Invalid setter key {item}.")

        setattr(self, item, value)

    def __getattr__(self, item: str) -> str:
        """Get attribute"""

        # this would recurse
        if item == "base":
            return self._items["base"].format(url=self._url)

        return self._items[item].format(
            url=self._url, base=self.base, chatroom_id=self._uid
        )


@dataclass
class Message:
    """A dataclass to store messages

    Note: This is only meant to be used internally."""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Message:
        """Create Message from server-data"""


@dataclass
class Channel:
    """A dataclass to store channels

    Note: This is only meant to be used internally."""

    uid: str
    name: str
    public: bool
    permissions: dict[str, bool]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Channel:
        """Create Channel from server-data"""

        return cls(
            uid=data["channelID"],
            name=data["channel_name"],
            public=data["public"],
            permissions=data["permissions"],
        )


@dataclass
class User:
    """A dataclass to store users"""

    uid: str
    username: str
    color: dict[str, int]

    def get_color(self) -> str:
        """Get user's color as markup tag"""

        return ";".join(value for value in self.color.values())

    @classmethod
    def from_dict(cls, data: dict) -> User:
        """Create User from server-data"""

        return cls(
            uid=data["userID"],
            username=data["username"],
            color=data["color"],
        )


class Chatroom:
    """TODO"""

    def __init__(
        self,
        url: str,
        uid: Optional[str] = None,
        name: Optional[str] = None,
        session: Optional[requests.Session] = None,
    ) -> None:
        """Initialize object"""

        self.uid = uid
        self.url = url
        self.name = name
        self.interval = 1

        self.user_id: Optional[str] = None
        self.session = session or requests.Session()
        self.active_channel: Optional[Channel] = None
        self.channels: list[Channel] = []

        self.endpoints: EndpointContainer

        # If the chatroom doesn't exist yet its endpoints' uid
        # is only filled in the create() method
        self.endpoints = EndpointContainer(self.url, self.uid)

        self._listeners: dict[Event, EventCallback] = {}
        self._is_looping: bool = False

    def _request(self, method_name: str, **req_args: Any) -> Optional[Any]:
        """Handle internal request, deal with error event calling"""

        method = getattr(self.session, method_name)
        if method is None:
            raise ValueError(f'Session does not have a method for "{method_name}".')

        error_handler = self._listeners.get(Event.ERROR)
        exception_handler = self._listeners.get(Event.NETWORK_EXCEPTION)

        try:
            response = method(**req_args)
        except Exception as exception:
            # This should just raise a custom Exception.
            if exception_handler is not None:
                # mypy thinks this will get a self argument
                exception_handler(exception, method_name, req_args)  # type: ignore
                return None

            raise exception

        if response.status_code == 200:
            return response.json()

        if error_handler is not None:
            # mypy thinks this will get a self argument
            error_handler(response, method_name, req_args)  # type: ignore
            return None

        raise RuntimeError(
            f"{method_name.upper()} request with data {req_args} failed"
            f" with no error or exception handler: {response.status_code} -> {response.text}"
        )

    def _notify(self, event: Event, *data: Any) -> None:
        """Notify listener of event"""

        callback = self._listeners.get(event)
        if callback is None:
            return

        callback(*data)

    def _run(self) -> None:
        """Run monitoring loop"""

        def _loop() -> None:
            """The main event loop for a chatroom"""

            while True:
                # This needs server-side support
                # messages = self.get_since(self._last_get_time)
                messages: list[Message] = []

                for message in messages:
                    if message.type == "delete":
                        self._notify(Event.MSG_DEL, message)

                    elif message.type == "system":
                        self._notify(Event.MSG_SYS, message)

                    elif message.type == "system-silent":
                        self._notify(Event.MSG_SYS_SILENT, message)

                    else:
                        self._notify(Event.MSG_NEW, message)

                sleep(self.interval)

        event_thread = Thread(target=_loop, name=self.uid)
        event_thread.start()

    def _update_channels(self, channels: Optional[list[Channel]] = None) -> None:
        """Update channels available to the user"""

        assert self.user_id, "Please log in before getting channels!"

        if channels is None:
            channels = self._request(
                "get",
                url=self.endpoints.channels,
                headers={"userID": self.user_id},
            )

            if channels is None:
                # Getting channels failed, but error was captured
                return

        for data in channels:
            channel = Channel.from_dict(data)

            if channel not in self.channels:
                self.channels.append(channel)

        if self.active_channel is None and len(self.channels) > 0:
            self.active_channel = self.channels[0]

    def subscribe(self, event: Event, callback: EventCallback) -> None:
        """Listen for event and run callback"""

        self._listeners[event] = callback

        if not self._is_looping and not event in [Event.ERROR, Event.NETWORK_EXCEPTION]:
            self._run()

    def create(self, username: str, password: str) -> None:
        """Create chatroom on the server"""

        data = {
            "chatroom_name": self.name,
            "username": username,
            "password": password,
        }

        response = self._request(
            "post",
            headers={"Content-Type": "application/json"},
            url=self.endpoints.chatroom,
            json=data,
        )

        if response is None:
            # Creation did not succeed, but error was captured
            return

        self.name = response["chatroom_name"]
        self.uid = response["chatroomID"]
        self.user_id = response["userID"]

        self.endpoints.set("uid", self.uid)

        self._update_channels(response["channels"])

    def create_channel(self, name: str) -> Optional[Channel]:
        """Create a channel"""

        data = {
            "userID": self.user_id,
            "channel_name": name,
        }

        response = self._request("post", url=self.endpoints.channels, json=data)

        if response is None:
            # Creation of chatroom failed, but error was captured
            return None

        channel = Channel.from_dict(response)
        self._update_channels([channel])

        return channel

    def login(self, user_id: str, password: str) -> Optional[Any]:
        """Log into the chatroom with given credentials

        Temporary: user_id will be replaced with username"""

        data = {
            "userID": user_id,
            "password": password,
        }

        response = self._request(
            "post",
            url=self.endpoints.login,
            json=data,
        )

        self.user_id = user_id
        self._update_channels()

        return response

    def get_messages(
        self,
        channel: Optional[Channel] = None,
        count: Optional[int] = None,
        time: Optional[int] = None,
    ) -> Any:  # list[Message]:
        """Get `count` messages"""

        if channel is not None:
            self.active_channel = channel

        elif self.active_channel is None:
            raise ValueError(
                "Please use either the Chatroom.set_channel() function"
                + " or provide `channel` as a non-null value!"
            )

        else:
            raise ValueError("No channel was provided.")

        headers = {
            "channelID": channel.uid,
            "count": count,
            "time": time,
        }

        data = self._request(
            "get",
            url=self.endpoints.messages,
            headers=headers,
        )

        return data

        # return [Message.from_dict(message_data) for message_data in data]

    def send(
        self,
        content: Union[str, bytes],
        channel: Optional[Channel] = None,
        reply_id: Optional[str] = None,
    ) -> Optional[Any]:
        """Send a message

        The message type is detected automatically, so sending files & text
        is done through the same method."""

        msg = {}
        if channel is not None:
            self.active_channel = channel

        elif self.active_channel is None:
            raise ValueError(
                "Please use either the Chatroom.set_channel() function"
                + " or provide `channel` as a non-null value!"
            )

        if isinstance(content, bytes):
            endpoint = self.endpoints.files
        else:
            endpoint = self.endpoints.messages

        msg = {
            "userID": self.user_id,
            "channelID": self.active_channel.uid,
            "replyID": reply_id,
            "data": content,
        }

        return self._request(
            "post",
            url=self.url + endpoint.format(chatroom_id=self.uid),
            json=msg,
        )


class Teacup:
    """TODO"""

    def __init__(self) -> None:
        """Initialize object"""

        self.chatrooms: list[Chatroom] = []
        self._global_listeners: dict[Event, EventCallback] = {}

    def login(self, username: str, password: str, chatroom: str, url: str) -> Chatroom:
        """Create a logged-in chatroom instance"""

        chat = Chatroom(url=url, uid=chatroom)

        for event, callback in self._global_listeners.items():
            chat.subscribe(event, callback)

        chat.login(username, password)

        return chat

    def get_chatroom(self, name: str) -> Optional[Chatroom]:
        """Get first chatroom by matching name"""

        for chatroom in self.chatrooms:
            if chatroom.name == name:
                return chatroom

        return None

    def create_chatroom(
        self, url: str, name: str, username: str, password: str
    ) -> Chatroom:
        """Create a new chatroom with given user as its owner, return a logged-in instance"""

        chat = Chatroom(url=url, name=name)

        for event, callback in self._global_listeners.items():
            chat.subscribe(event, callback)

        response = chat.create(username, password)
        if response is None:
            return None

        return chat

    def subscribe_all(self, event: Event, callback: EventCallback) -> None:
        """Subscribe callback to event in all (current & future) Chatrooms"""

        for chatroom in self.chatrooms:
            chatroom.subscribe(event, callback)

        self._global_listeners[event] = callback

    def thread(
        self,
        target: Callable[..., Any],
        callback: Callable[..., Any],
        target_args: Optional[tuple[Any, ...]] = None,
        target_kwargs: Optional[dict[str, Any]] = None,
    ) -> None:
        """Run target(*target_args, **target_kwargs) in a thread, call callback with its result

        Note: the signature of the callback function depends on the thread's target."""

        def _inner() -> None:
            """The wrapper that calls target & callback"""

            callback(target(*target_args, **target_kwargs))

        if target_args is None:
            target_args = ()

        if target_kwargs is None:
            target_kwargs = {}

        runner = Thread(target=_inner)
        runner.start()
