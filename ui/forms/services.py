from __future__ import annotations

from model.config import GentlyConfig, ServicesConfig, ServicesExtraConfig, ServicesRolesConfig
from ui.abstract import FieldSpec, FormSpec
from ui.forms.base import SectionForm

_NETWORK  = ["netifrc", "NetworkManager", "dhcpcd", "iwd", "systemd-networkd", "none"]
_CRON     = ["cronie", "dcron", "fcron", "bcron", "none"]
_LOGGING  = ["sysklogd", "syslog-ng", "metalog", "none"]
_SSH      = ["openssh", "dropbear", "none"]
_NTP      = ["chrony", "openntpd", "ntp", "systemd-timesyncd", "none"]
_FIREWALL = ["iptables", "nftables", "ufw", "none"]
_PRINT    = ["cups", "none"]
_BT       = ["bluez", "none"]


class ServicesForm(SectionForm):
    section_name = "Services"
    section_key  = "services"

    def is_complete(self, config: GentlyConfig) -> bool:
        return (
            config.services is not None
            and config.services.roles is not None
            and config.services.roles.network is not None
        )

    def build_form(self, config: GentlyConfig) -> FormSpec:
        svc = config.services or ServicesConfig()
        r   = svc.roles or ServicesRolesConfig()
        return FormSpec(
            title="Services",
            subtitle="Select the daemon for each system role (none = do not install)",
            fields=[
                FieldSpec(key="network",   label="Network manager", type="choice",
                          default=r.network   or "netifrc",            options=_NETWORK),
                FieldSpec(key="cron",      label="Cron daemon",     type="choice",
                          default=r.cron      or "none",               options=_CRON,
                          required=False),
                FieldSpec(key="logging",   label="Logging daemon",  type="choice",
                          default=r.logging   or "sysklogd",           options=_LOGGING,
                          required=False),
                FieldSpec(key="ssh",       label="SSH server",      type="choice",
                          default=r.ssh       or "openssh",            options=_SSH,
                          required=False),
                FieldSpec(key="ntp",       label="NTP daemon",      type="choice",
                          default=r.ntp       or "none",               options=_NTP,
                          required=False),
                FieldSpec(key="firewall",  label="Firewall",        type="choice",
                          default=r.firewall  or "none",               options=_FIREWALL,
                          required=False),
                FieldSpec(key="printing",  label="Printing",        type="choice",
                          default=r.printing  or "none",               options=_PRINT,
                          required=False),
                FieldSpec(key="bluetooth", label="Bluetooth",       type="choice",
                          default=r.bluetooth or "none",               options=_BT,
                          required=False),
                FieldSpec(
                    key="extra_enable",
                    label="Extra services to enable",
                    type="list",
                    default=list(svc.extra.enable) if svc.extra else [],
                    help="Service names to emerge and activate (e.g. libvirtd, docker, acpid)",
                    required=False,
                ),
            ],
        )

    def apply(self, config: GentlyConfig, values: dict) -> GentlyConfig:
        roles = ServicesRolesConfig(
            network=values.get("network")   or None,
            cron=values.get("cron")         or None,
            logging=values.get("logging")   or None,
            ssh=values.get("ssh")           or None,
            ntp=values.get("ntp")           or None,
            firewall=values.get("firewall") or None,
            printing=values.get("printing") or None,
            bluetooth=values.get("bluetooth") or None,
        )
        svc = config.services or ServicesConfig()
        svc.roles = roles
        extra_list = values.get("extra_enable") or []
        svc.extra = ServicesExtraConfig(enable=extra_list)
        config.services = svc
        return config
