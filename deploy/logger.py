"""Deploy tooling log shim.

Historically deploy/* used a standalone stdout-only logger whose hr() drew
box banners; those lines bypassed the file log and rendered as noise on the
desktop startup page.  deploy.config already hard-depends on module.logger,
so re-export the project logger here and keep output consistent across
console, file log and the startup page.
"""

from module.logger import logger

__all__ = ['logger']
