# ========================================================================== #
#                                                                            #
#    KVMD - The main Pi-KVM daemon.                                          #
#                                                                            #
#    Copyright (C) 2018  Maxim Devaev <mdevaev@gmail.com>                    #
#                                                                            #
#    This program is free software: you can redistribute it and/or modify    #
#    it under the terms of the GNU General Public License as published by    #
#    the Free Software Foundation, either version 3 of the License, or       #
#    (at your option) any later version.                                     #
#                                                                            #
#    This program is distributed in the hope that it will be useful,         #
#    but WITHOUT ANY WARRANTY; without even the implied warranty of          #
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the           #
#    GNU General Public License for more details.                            #
#                                                                            #
#    You should have received a copy of the GNU General Public License       #
#    along with this program.  If not, see <https://www.gnu.org/licenses/>.  #
#                                                                            #
# ========================================================================== #


import asyncio
import contextlib

from typing import Dict
from typing import Optional

import hid

from ...logging import get_logger

from ... import aiotools

from ...yamlconf import Option

from ...validators.basic import valid_float_f01

from ...validators.os import valid_abs_path

from . import GpioDriverOfflineError
from . import BaseUserGpioDriver


# =====
class Plugin(BaseUserGpioDriver):
    # http://vusb.wikidot.com/project:driver-less-usb-relays-hid-interface
    # https://github.com/trezor/cython-hidapi/blob/6057d41b5a2552a70ff7117a9d19fc21bf863867/chid.pxd

    def __init__(  # pylint: disable=super-init-not-called
        self,
        instance_name: str,
        notifier: aiotools.AioNotifier,

        device_path: str,
        state_poll: float,
    ) -> None:

        super().__init__(instance_name, notifier)

        self.__device_path = device_path
        self.__state_poll = state_poll

        self.__device: Optional[hid.device] = None
        self.__stop = False

        self.__initials: Dict[int, Optional[bool]] = {}

    @classmethod
    def get_plugin_options(cls) -> Dict:
        return {
            "device":     Option("", type=valid_abs_path, unpack_as="device_path"),
            "state_poll": Option(5.0, type=valid_float_f01),
        }

    def register_input(self, pin: int) -> None:
        _ = pin

    def register_output(self, pin: int, initial: Optional[bool]) -> None:
        self.__initials[pin] = initial

    def prepare(self) -> None:
        logger = get_logger(0)
        logger.info("Initializing %s ...", self)
        try:
            for (pid, state) in self.__initials.items():
                if state is not None:
                    self.write(pid, state)
        except Exception:
            logger.exception("Can't perform first initialization of %s", self)

    async def run(self) -> None:
        prev_raw = -1
        while True:
            try:
                raw = self.__read_raw()
            except Exception:
                raw = -1
            if raw != prev_raw:
                await self._notifier.notify()
                prev_raw = raw
            await asyncio.sleep(self.__state_poll)

    def cleanup(self) -> None:
        self.__close_device()
        self.__stop = True

    def read(self, pin: int) -> bool:
        if self.__check_pin(pin):
            try:
                return bool(self.__read_raw() & (1 << pin))
            except Exception:
                raise GpioDriverOfflineError(self) from None
        return False

    def write(self, pin: int, state: bool) -> None:
        if self.__check_pin(pin):
            try:
                with self.__ensure_device("writing") as device:
                    report = [(0xFF if state else 0xFD), pin + 1]  # Pin numeration starts from 0
                    result = device.send_feature_report(report)
                    if result < 0:
                        raise RuntimeError(f"Retval of send_feature_report() < 0: {result}")
            except Exception:
                raise GpioDriverOfflineError(self) from None

    # =====

    def __check_pin(self, pin: int) -> bool:
        ok = (0 <= pin <= 7)
        if not ok:
            get_logger(0).warning("Unsupported pin for %s: %d", self, pin)
        return ok

    def __read_raw(self) -> int:
        with self.__ensure_device("reading") as device:
            return device.get_feature_report(1, 8)[7]

    @contextlib.contextmanager
    def __ensure_device(self, context: str) -> hid.device:
        assert not self.__stop
        if self.__device is None:
            device = hid.device()
            device.open_path(self.__device_path.encode("utf-8"))
            device.set_nonblocking(True)
            self.__device = device
            get_logger(0).info("Opened %s while %s", self, context)
        try:
            yield self.__device
        except Exception as err:
            get_logger(0).error("Error occured on %s while %s: %s: %s",
                                self, context, type(err).__name__, err)
            self.__close_device()
            raise

    def __close_device(self) -> None:
        if self.__device:
            try:
                self.__device.close()
            except Exception:
                pass
            self.__device = None
            get_logger(0).info("Closed %s", self)

    def __str__(self) -> str:
        return f"HidRelay({self._instance_name}, {self.__device_path})"

    __repr__ = __str__