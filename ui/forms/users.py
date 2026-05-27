from __future__ import annotations

import subprocess
from typing import Any

from model.config import GentlyConfig, UserAccountConfig, UsersConfig
from ui.abstract import FieldSpec, FormSpec
from ui.forms.base import SectionForm


def _hash_password(plaintext: str) -> str:
    """Hash a plaintext password to SHA-512 crypt format for /etc/shadow."""
    try:
        result = subprocess.run(
            ["openssl", "passwd", "-6", plaintext],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except Exception:
        pass
    try:
        import crypt  # type: ignore[import]  # removed in Python 3.13
        return crypt.crypt(plaintext, crypt.mksalt(crypt.METHOD_SHA512))
    except (ImportError, AttributeError):
        pass
    # Last resort: return plaintext so the installer can still set the password
    return plaintext


class UsersForm(SectionForm):
    section_name = "Users"
    section_key  = "users"

    def is_complete(self, config: GentlyConfig) -> bool:
        u = config.users
        if u is None:
            return False
        if u.credentials_file:
            return True
        root = next((a for a in u.accounts if a.name == "root"), None)
        return root is not None and bool(root.password or root.password_hash)

    def build_form(
        self,
        config: GentlyConfig,
        draft_credentials_file: str | None = None,
        draft_root_password: str | None = None,
        draft_root_password_confirm: str | None = None,
    ) -> FormSpec:
        u = config.users or UsersConfig()
        extra_accounts = [a for a in u.accounts if a.name != "root"]
        return FormSpec(
            title="Users",
            subtitle="Root authentication and additional local users",
            fields=[
                FieldSpec(
                    key="credentials_file",
                    label="Credentials file",
                    type="text",
                    default=draft_credentials_file if draft_credentials_file is not None else u.credentials_file,
                    required=False,
                    help="Path to a file with pre-hashed credentials (shadow format)",
                ),
                FieldSpec(
                    key="root_password",
                    label="Root password",
                    type="password",
                    default=draft_root_password,
                    required=False,
                    help="Hashed automatically with SHA-512 before saving",
                ),
                FieldSpec(
                    key="root_password_confirm",
                    label="Confirm root password",
                    type="password",
                    default=draft_root_password_confirm,
                    required=False,
                    help="Repeat the same password to avoid typos",
                ),
                FieldSpec(
                    key="accounts_editor",
                    label="User accounts",
                    type="subsection",
                    default=f"{len(extra_accounts)} configured",
                    required=False,
                    help="Press Enter to add/edit/delete non-root users",
                ),
            ],
        )

    @staticmethod
    def _accounts_section_items(accounts: list[UserAccountConfig]) -> list[tuple[str, dict[str, Any]]]:
        items: list[tuple[str, dict[str, Any]]] = []
        for account in accounts:
            name = account.name or "(unnamed)"
            entry: dict[str, Any] = {}
            if account.shell:
                entry["shell"] = account.shell
            if account.groups:
                entry["groups"] = ",".join(account.groups)
            if account.comment:
                entry["comment"] = account.comment
            if account.create_home is not None:
                entry["create_home"] = account.create_home
            if account.ssh_authorized_keys:
                entry["ssh_keys"] = len(account.ssh_authorized_keys)
            items.append((name, entry))
        return items

    @staticmethod
    def _account_form(account: UserAccountConfig, existing: bool) -> FormSpec:
        actions = [("Save", "save"), ("Cancel", "cancel")]
        if existing:
            actions.insert(1, ("Delete", "delete"))

        return FormSpec(
            title=f"Users — account {account.name or 'new'}",
            subtitle=None,
            fields=[
                FieldSpec(
                    key="name",
                    label="Username",
                    type="text",
                    default=account.name,
                    help="Local account name, e.g. alice",
                ),
                FieldSpec(
                    key="password",
                    label="Password",
                    type="password",
                    default=None,
                    required=False,
                    help="Set or change password. Required for new accounts.",
                ),
                FieldSpec(
                    key="password_confirm",
                    label="Confirm password",
                    type="password",
                    default=None,
                    required=False,
                    help="Repeat password to avoid typos",
                ),
                FieldSpec(
                    key="groups",
                    label="Groups",
                    type="list",
                    default=list(account.groups) if account.groups else None,
                    required=False,
                    help="Supplementary groups, e.g. wheel audio video",
                ),
                FieldSpec(
                    key="shell",
                    label="Shell",
                    type="text",
                    default=account.shell,
                    required=False,
                    help="Login shell, e.g. /bin/bash",
                ),
                FieldSpec(
                    key="comment",
                    label="Comment",
                    type="text",
                    default=account.comment,
                    required=False,
                ),
                FieldSpec(
                    key="create_home",
                    label="Create home",
                    type="bool",
                    default=True if account.create_home is None else account.create_home,
                    required=False,
                ),
                FieldSpec(
                    key="ssh_authorized_keys",
                    label="SSH authorized keys",
                    type="list",
                    default=list(account.ssh_authorized_keys) if account.ssh_authorized_keys else None,
                    required=False,
                    help="Public SSH keys for this user",
                ),
            ],
            actions=actions,
        )

    @staticmethod
    def _account_from_values(values: dict, previous: UserAccountConfig | None = None) -> UserAccountConfig:
        previous = previous or UserAccountConfig()
        account = UserAccountConfig(
            name=values.get("name") or None,
            password=previous.password,
            password_hash=previous.password_hash,
            groups=values.get("groups") or None,
            shell=values.get("shell") or None,
            comment=values.get("comment") or None,
            create_home=bool(values.get("create_home", True)),
            ssh_authorized_keys=values.get("ssh_authorized_keys") or None,
        )

        raw_password = values.get("password") or None
        if raw_password:
            hashed = _hash_password(raw_password)
            if hashed != raw_password:
                account.password = None
                account.password_hash = hashed
            else:
                account.password = raw_password
                account.password_hash = None
        return account

    def apply(self, config: GentlyConfig, values: dict) -> GentlyConfig:
        u = config.users or UsersConfig()
        u.credentials_file = values.get("credentials_file") or None

        root_pwd = values.get("root_password") or None
        if root_pwd:
            root = next((a for a in u.accounts if a.name == "root"), None)
            if root is None:
                root = UserAccountConfig(name="root")
                u.accounts = [root] + list(u.accounts)
            hashed = _hash_password(root_pwd)
            if hashed != root_pwd:        # successfully hashed — don't store plaintext
                root.password      = None
                root.password_hash = hashed
            else:                         # hashing unavailable — store plaintext for installer
                root.password      = root_pwd
                root.password_hash = None

        config.users = u
        return config

    def run(self, config: GentlyConfig, backend):  # type: ignore[override]
        u = config.users or UsersConfig()
        accounts = list(u.accounts)
        draft_credentials_file = u.credentials_file
        draft_root_password: str | None = None
        draft_root_password_confirm: str | None = None

        while True:
            temp = GentlyConfig(users=UsersConfig(
                credentials_file=draft_credentials_file,
                credentials_file_shadow=u.credentials_file_shadow,
                accounts=accounts,
            ))
            values = backend.show_form(
                self.build_form(
                    temp,
                    draft_credentials_file=draft_credentials_file,
                    draft_root_password=draft_root_password,
                    draft_root_password_confirm=draft_root_password_confirm,
                )
            )

            # Cancel is only allowed when the section is already complete.
            if values is None:
                snapshot = GentlyConfig(users=UsersConfig(
                    credentials_file=draft_credentials_file,
                    credentials_file_shadow=u.credentials_file_shadow,
                    accounts=accounts,
                ))
                if self.is_complete(snapshot):
                    return config
                backend.show_error(
                    "Users configuration requires either credentials_file "
                    "or a root password."
                )
                continue

            base_values = values.get("__values__", values)
            draft_credentials_file = base_values.get("credentials_file") or None
            draft_root_password = base_values.get("root_password") or None
            draft_root_password_confirm = base_values.get("root_password_confirm") or None

            if values.get("__action__") == "subsection" and values.get("__field__") == "accounts_editor":
                while True:
                    action = backend.show_subsection(
                        "Users — local accounts",
                        self._accounts_section_items([a for a in accounts if a.name != "root"]),
                    )
                    if action == "done":
                        break

                    visible_accounts = [a for a in accounts if a.name != "root"]
                    if action == "add":
                        draft_account = UserAccountConfig(create_home=True)
                        while True:
                            acc_values = backend.show_form(self._account_form(draft_account, existing=False))
                            if acc_values is None or acc_values.get("__action__") == "delete":
                                break

                            username = (acc_values.get("name") or "").strip()
                            password = acc_values.get("password") or None
                            password_confirm = acc_values.get("password_confirm") or None
                            if not username:
                                backend.show_error("Username is required.")
                                draft_account = self._account_from_values(acc_values, previous=draft_account)
                                continue
                            if username == "root":
                                backend.show_error("Use root password fields for root account.")
                                draft_account = self._account_from_values(acc_values, previous=draft_account)
                                continue
                            if any((a.name or "") == username for a in accounts):
                                backend.show_error(f"User '{username}' already exists.")
                                draft_account = self._account_from_values(acc_values, previous=draft_account)
                                continue
                            if not password:
                                backend.show_error("Password is required for new users.")
                                draft_account = self._account_from_values(acc_values, previous=draft_account)
                                continue
                            if password != password_confirm:
                                backend.show_error("User password confirmation does not match.")
                                draft_account = self._account_from_values(acc_values, previous=draft_account)
                                continue

                            accounts.append(self._account_from_values(acc_values, previous=draft_account))
                            break
                        continue

                    if action.startswith("edit:"):
                        try:
                            edit_visible_idx = int(action.split(":", 1)[1])
                        except ValueError:
                            continue
                        if edit_visible_idx < 0 or edit_visible_idx >= len(visible_accounts):
                            continue

                        current = visible_accounts[edit_visible_idx]
                        draft_account = current
                        while True:
                            acc_values = backend.show_form(self._account_form(draft_account, existing=True))
                            if acc_values is None:
                                break
                            if acc_values.get("__action__") == "delete":
                                accounts = [a for a in accounts if a is not current]
                                break

                            username = (acc_values.get("name") or "").strip()
                            password = acc_values.get("password") or None
                            password_confirm = acc_values.get("password_confirm") or None
                            if not username:
                                backend.show_error("Username is required.")
                                draft_account = self._account_from_values(acc_values, previous=draft_account)
                                continue
                            if username == "root":
                                backend.show_error("Use root password fields for root account.")
                                draft_account = self._account_from_values(acc_values, previous=draft_account)
                                continue
                            if any((a is not current) and ((a.name or "") == username) for a in accounts):
                                backend.show_error(f"User '{username}' already exists.")
                                draft_account = self._account_from_values(acc_values, previous=draft_account)
                                continue
                            if password and password != password_confirm:
                                backend.show_error("User password confirmation does not match.")
                                draft_account = self._account_from_values(acc_values, previous=draft_account)
                                continue
                            if (not password) and (not (current.password or current.password_hash)):
                                backend.show_error("User must have a password.")
                                draft_account = self._account_from_values(acc_values, previous=draft_account)
                                continue

                            updated = self._account_from_values(acc_values, previous=current)
                            for idx, acc in enumerate(accounts):
                                if acc is current:
                                    accounts[idx] = updated
                                    break
                            break
                continue

            credentials_file = draft_credentials_file
            root_password = draft_root_password
            root_password_confirm = draft_root_password_confirm

            if root_password and root_password_confirm != root_password:
                backend.show_error("Root password confirmation does not match.")
                continue
            if root_password_confirm and not root_password:
                backend.show_error("Enter root password before confirmation.")
                continue

            # Existing root secret also satisfies completeness when password
            # is not being changed in this run.
            existing_root_has_secret = False
            if accounts:
                root = next((a for a in accounts if a.name == "root"), None)
                existing_root_has_secret = bool(root and (root.password or root.password_hash))

            if not (credentials_file or root_password or existing_root_has_secret):
                backend.show_error(
                    "Users configuration requires either credentials_file "
                    "or a root password."
                )
                continue

            u.credentials_file = credentials_file
            u.accounts = accounts

            updated = self.apply(GentlyConfig(users=u), values)
            config.users = updated.users
            return config
