# Copyright (c) 2020 Tulir Asokan
#
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.
from typing import Any, Dict, List, Union
from collections import defaultdict
from datetime import timedelta
import json

from mautrix.types import (EncryptedMegolmEventContent, EventType, UserID, DeviceID, Serializable,
                           EncryptionAlgorithm, RoomID, EncryptedOlmEventContent, SessionID,
                           RoomKeyWithheldEventContent, RoomKeyWithheldCode, IdentityKey,
                           SigningKey, RelatesTo)

from .types import DeviceIdentity, TrustState, EncryptionError, SessionShareError
from .encrypt_olm import OlmEncryptionMachine
from .device_lists import DeviceListMachine
from .sessions import OutboundGroupSession, InboundGroupSession


class Sentinel:
    pass


already_shared = Sentinel()
key_missing = Sentinel()

SessionEncryptResult = Union[
    type(already_shared),  # already shared
    EncryptedOlmEventContent,  # share successful
    RoomKeyWithheldEventContent,  # won't share
    type(key_missing),  # missing device
]


class MegolmEncryptionMachine(OlmEncryptionMachine, DeviceListMachine):
    async def encrypt_megolm_event(self, room_id: RoomID, event_type: EventType, content: Any
                                   ) -> EncryptedMegolmEventContent:
        self.log.debug(f"Encrypting event of type {event_type} for {room_id}")
        session = await self.crypto_store.get_outbound_group_session(room_id)
        if not session:
            raise EncryptionError("No group session created")
        ciphertext = session.encrypt(json.dumps({
            "room_id": room_id,
            "type": event_type.serialize(),
            "content": content.serialize() if isinstance(content, Serializable) else content,
        }))
        try:
            relates_to = content.relates_to
        except AttributeError:
            try:
                relates_to = RelatesTo.deserialize(content["m.relates_to"])
            except KeyError:
                relates_to = None
        await self.crypto_store.update_outbound_group_session(session)
        return EncryptedMegolmEventContent(sender_key=self.account.identity_key,
                                           device_id=self.client.device_id, session_id=session.id,
                                           ciphertext=ciphertext, relates_to=relates_to)

    async def share_group_session(self, room_id: RoomID, users: List[UserID]) -> None:
        self.log.debug(f"Sharing group session for room {room_id} with {users}")
        session = await self.crypto_store.get_outbound_group_session(room_id)
        if session and session.shared and not session.expired:
            raise SessionShareError("Group session has already been shared")
        if not session or session.expired:
            session = await self._new_outbound_group_session(room_id)

        encryption_info = await self.state_store.get_encryption_info(room_id)
        if encryption_info:
            if encryption_info.algorithm != EncryptionAlgorithm.MEGOLM_V1:
                raise SessionShareError("Room encryption algorithm is not supported")
            session.max_messages = encryption_info.rotation_period_msgs or session.max_messages
            session.max_age = (timedelta(milliseconds=encryption_info.rotation_period_ms)
                               if encryption_info.rotation_period_ms else session.max_age)
            self.log.debug("Got stored encryption state event and configured session to rotate "
                           f"after {session.max_messages} messages or {session.max_age}")

        share_key_msgs = defaultdict(lambda: {})
        withhold_key_msgs = defaultdict(lambda: {})
        missing_sessions: Dict[UserID, Dict[DeviceID, DeviceIdentity]] = defaultdict(lambda: {})
        fetch_keys = []

        for user_id in users:
            devices = await self.crypto_store.get_devices(user_id)
            if devices is None:
                self.log.debug(f"get_devices returned nil for {user_id}, will fetch keys and retry")
                fetch_keys.append(user_id)
            elif len(devices) == 0:
                self.log.debug(f"{user_id} has no devices, skipping")
            else:
                self.log.debug(f"Trying to encrypt group session {session.id} for {user_id}")
                for device_id, device in devices.items():
                    result = await self._encrypt_group_session_for_device(session, user_id,
                                                                          device_id, device)
                    if isinstance(result, EncryptedOlmEventContent):
                        share_key_msgs[user_id][device_id] = result
                    elif isinstance(result, RoomKeyWithheldEventContent):
                        withhold_key_msgs[user_id][device_id] = result
                    elif result == key_missing:
                        missing_sessions[user_id][device_id] = device

        if fetch_keys:
            self.log.debug(f"Fetching missing keys for {fetch_keys}")
            fetched_keys = await self._fetch_keys(users, include_untracked=True)
            for user_id, devices in fetched_keys.items():
                missing_sessions[user_id] = devices

        if missing_sessions:
            self.log.debug(f"Creating missing outbound sessions {missing_sessions}")
            await self._create_outbound_sessions(missing_sessions)

        for user_id, devices in missing_sessions.items():
            for device_id, device in devices.items():
                result = await self._encrypt_group_session_for_device(session, user_id, device_id,
                                                                      device)
                if isinstance(result, EncryptedOlmEventContent):
                    share_key_msgs[user_id][device_id] = result
                elif isinstance(result, RoomKeyWithheldEventContent):
                    withhold_key_msgs[user_id][device_id] = result
                # We don't care about missing keys at this point

        await self.client.send_to_device(EventType.TO_DEVICE_ENCRYPTED, share_key_msgs)
        await self.client.send_to_device(EventType.ROOM_KEY_WITHHELD, withhold_key_msgs)
        self.log.info(f"Group session for {room_id} successfully shared")
        session.shared = True
        await self.crypto_store.add_outbound_group_session(session)

    async def _new_outbound_group_session(self, room_id: RoomID) -> OutboundGroupSession:
        session = OutboundGroupSession(room_id)
        await self._create_group_session(self.account.identity_key, self.account.signing_key,
                                         room_id, session.id, session.session_key)
        return session

    async def _create_group_session(self, sender_key: IdentityKey, signing_key: SigningKey,
                                    room_id: RoomID, session_id: SessionID, session_key: str
                                    ) -> None:
        session = InboundGroupSession(session_key=session_key, signing_key=signing_key,
                                      sender_key=sender_key, room_id=room_id)
        await self.crypto_store.put_group_session(room_id, sender_key, session_id, session)
        self.log.debug(f"Created inbound group session {room_id}/{sender_key}/{session_id}")

    async def _encrypt_group_session_for_user(self, session: OutboundGroupSession, user_id: UserID,
                                              devices: Dict[DeviceID, DeviceIdentity],
                                              ) -> Dict[DeviceID, SessionEncryptResult]:
        return {device_id: await self._encrypt_group_session_for_device(session, user_id, device_id,
                                                                        device)
                for device_id, device in devices.items()}

    async def _encrypt_group_session_for_device(self, session: OutboundGroupSession,
                                                user_id: UserID, device_id: DeviceID,
                                                device: DeviceIdentity) -> SessionEncryptResult:
        key = (user_id, device_id)
        if key in session.users_ignored or key in session.users_shared_with:
            return already_shared
        elif user_id == self.client.mxid and device_id == self.client.device_id:
            session.users_ignored.add(key)
            return already_shared

        if device.trust == TrustState.BLACKLISTED:
            self.log.debug(f"Not encrypting group session {session.id} for {device_id} "
                           f"of {user_id}: device is blacklisted")
            session.users_ignored.add(key)
            return RoomKeyWithheldEventContent(
                room_id=session.room_id, algorithm=EncryptionAlgorithm.MEGOLM_V1,
                session_id=session.id, sender_key=self.account.identity_key,
                code=RoomKeyWithheldCode.BLACKLISTED, reason="Device is blacklisted")
        elif not self.allow_unverified_devices and device.trust == TrustState.UNSET:
            self.log.debug(f"Not encrypting group session {session.id} for {device_id} "
                           f"of {user_id}: device is not verified")
            session.users_ignored.add(key)
            return RoomKeyWithheldEventContent(
                room_id=session.room_id, algorithm=EncryptionAlgorithm.MEGOLM_V1,
                session_id=session.id, sender_key=self.account.identity_key,
                code=RoomKeyWithheldCode.UNVERIFIED, reason="This device does not encrypt "
                                                            "messages for unverified devices")
        device_session = await self.crypto_store.get_latest_session(device.identity_key)
        if not device_session:
            return key_missing
        encrypted = await self._encrypt_olm_event(device_session, device, EventType.ROOM_KEY,
                                                  session.share_content)
        session.users_shared_with.add(key)
        self.log.debug(f"Encrypted group session {session.id} for {device_id} of {user_id}")
        return encrypted