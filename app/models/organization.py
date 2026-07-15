"""Organization and Site (facility) models.

An organization is the top-level access boundary: users and quality events are
scoped to an organization. A site is a physical facility belonging to an
organization, replacing the former free-text ``facility`` field with a real
foreign key.
"""

from datetime import datetime

from sqlalchemy import Column, String, Integer, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.orm import relationship

from app.database import Base


class Organization(Base):
    """A customer organization; the top-level access scope."""

    __tablename__ = "organizations"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False)
    code = Column(String(50), unique=True, nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    users = relationship("User", back_populates="organization")
    sites = relationship("Site", back_populates="organization")

    def __repr__(self) -> str:
        return f"<Organization(id={self.id}, code={self.code}, name={self.name})>"


class Site(Base):
    """A physical facility within an organization."""

    __tablename__ = "sites"

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(
        Integer, ForeignKey("organizations.id"), nullable=False, index=True
    )
    name = Column(String(255), nullable=False)
    code = Column(String(50), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    organization = relationship("Organization", back_populates="sites")

    def __repr__(self) -> str:
        return f"<Site(id={self.id}, name={self.name}, organization_id={self.organization_id})>"


class OrgSetting(Base):
    """A per-organization key/value configuration entry.

    A generic store for admin-managed toggles (e.g. whether standalone alerts are
    allowed, the default alert expiry). Values are stored as strings and coerced
    by the accessors in ``app.services.org_settings``.
    """

    __tablename__ = "org_settings"
    __table_args__ = (
        UniqueConstraint("organization_id", "key", name="uq_org_settings_org_key"),
    )

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(
        Integer, ForeignKey("organizations.id"), nullable=False, index=True
    )
    key = Column(String(100), nullable=False)
    value = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    def __repr__(self) -> str:
        return f"<OrgSetting(org={self.organization_id}, key={self.key})>"
