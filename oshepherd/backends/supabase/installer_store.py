"""Encrypted, expiring control-service records; no credentials in filenames.

The encryption key is supplied by the service owner, outside this directory.
Single-use transitions use the same cross-process lock as operator journals.
"""

import json
import re
import time

from cryptography.fernet import Fernet, InvalidToken

from .operator import installation_lock
from .private_files import private_directory, read_private_json, write_private_json
from .releases import OperatorError


class InstallerStore:
    def __init__(self, directory, encryption_key, *, clock=time.time):
        self.directory = private_directory(directory)
        self.cipher = Fernet(encryption_key)
        self.clock = clock

    def lock(self):
        return installation_lock(self.directory)

    def path(self, key):
        if not re.fullmatch(r"[a-z]+-[a-z0-9]{20,64}", key):
            raise OperatorError("invalid_installer_record")
        return self.directory / (key + ".json")

    def save(self, key, value, *, expires_at):
        envelope = {"key": key, "expires_at": expires_at, "value": value}
        content = json.dumps(envelope).encode()
        if len(content) > 65536:
            raise OperatorError("installer_record_too_large")
        path = self.path(key)
        if not path.exists() and len(list(self.directory.glob("*.json"))) >= 256:
            raise OperatorError("installer_record_limit")
        write_private_json(
            path, {"ciphertext": self.cipher.encrypt(content).decode("ascii")}
        )

    def load(self, key):
        try:
            record = read_private_json(self.path(key), limit=100000)
            data = json.loads(self.cipher.decrypt(record["ciphertext"].encode()))
            if data["key"] != key:
                raise OperatorError("installer_record_mismatch")
            if data["expires_at"] <= self.clock():
                raise OperatorError("installer_record_expired")
            return data["value"]
        except FileNotFoundError:
            raise OperatorError("installer_record_missing") from None
        except (InvalidToken, KeyError, ValueError, TypeError):
            raise OperatorError("invalid_installer_record") from None

    def delete(self, key):
        self.path(key).unlink(missing_ok=True)

    def purge(self):
        """Called under lock; expired encrypted credentials are physically removed."""
        for path in self.directory.glob("*.json"):
            try:
                self.load(path.stem)
            except OperatorError as error:
                if str(error) == "installer_record_expired":
                    path.unlink()
                else:
                    raise
