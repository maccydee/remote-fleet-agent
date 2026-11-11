"""Linux connection kits share the Mac TLS, scope and SSH verification boundary."""

from remote_fleet_agent.mac_enrollment import MAC_SETUP_DEFAULTS, MacEnrollment

LINUX_SETUP_DEFAULTS = dict(MAC_SETUP_DEFAULTS)


class LinuxEnrollment(MacEnrollment):
    platform = "linux"
    label = "Linux"
    kernel = "Linux"
    kind = "linux_enrollment"
    defaults = LINUX_SETUP_DEFAULTS
    connect_asset = "Connect.sh"
    remove_asset = "Remove.sh"
    architectures = None  # Native pairing supports other Linux architectures too.
