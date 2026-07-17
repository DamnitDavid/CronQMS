"""SQLAlchemy models for CronQMS."""

from app.models.user import User, Role
from app.models.role import RoleDefinition, RolePermission
from app.models.organization import Organization, Site, OrgSetting
from app.models.event import Event
from app.models.capa import Capa, CapaStatus, VerificationOutcome, capa_events
from app.models.document import (
    Document,
    DocumentVersion,
    DocumentCategory,
    DocumentVersionStatus,
)
from app.models.audit import (
    Audit,
    AuditChecklistItem,
    AuditFinding,
    AuditType,
    AuditStatus,
    ChecklistResult,
    FindingSeverity,
    FindingStatus,
)
from app.models.training import (
    Employee,
    TrainingCourse,
    TrainingRecord,
    TrainingStatus,
)
from app.models.change import (
    ChangeRequest,
    ChangeImpact,
    ChangeAction,
    ChangeType,
    ChangeStatus,
    RiskLevel,
    ImpactArea,
    ImpactLevel,
    ActionStatus,
)
from app.models.attachment import Attachment
from app.models.comment import Comment
from app.models.event_history import EventHistory
from app.models.custom_field import CustomField, CustomFieldType, EventCustomValue
from app.models.assignee_group import AssigneeGroup, assignee_group_members
from app.models.alert import (
    Alert,
    AlertAcknowledgement,
    AlertImage,
    AlertSeverity,
    AlertStatus,
    AlertType,
    Notification,
    alert_recipient_groups,
)

# Wire the audit choke point once, after the models are defined. CAPAs and
# alerts are audited from birth alongside events.
from app.core.audit import register_auditing

register_auditing(Event)
register_auditing(Capa)
register_auditing(Alert)
register_auditing(Document)
register_auditing(DocumentVersion)
register_auditing(Audit)
register_auditing(AuditFinding)
register_auditing(Employee)
register_auditing(TrainingCourse)
register_auditing(TrainingRecord)
register_auditing(ChangeRequest)
register_auditing(ChangeAction)

__all__ = [
    "User",
    "Role",
    "RoleDefinition",
    "RolePermission",
    "Organization",
    "Site",
    "OrgSetting",
    "Event",
    "Capa",
    "CapaStatus",
    "VerificationOutcome",
    "capa_events",
    "Document",
    "DocumentVersion",
    "DocumentCategory",
    "DocumentVersionStatus",
    "Audit",
    "AuditChecklistItem",
    "AuditFinding",
    "AuditType",
    "AuditStatus",
    "ChecklistResult",
    "FindingSeverity",
    "FindingStatus",
    "Employee",
    "TrainingCourse",
    "TrainingRecord",
    "TrainingStatus",
    "ChangeRequest",
    "ChangeImpact",
    "ChangeAction",
    "ChangeType",
    "ChangeStatus",
    "RiskLevel",
    "ImpactArea",
    "ImpactLevel",
    "ActionStatus",
    "Attachment",
    "Comment",
    "EventHistory",
    "CustomField",
    "CustomFieldType",
    "EventCustomValue",
    "AssigneeGroup",
    "assignee_group_members",
    "Alert",
    "AlertAcknowledgement",
    "AlertImage",
    "AlertType",
    "AlertSeverity",
    "AlertStatus",
    "Notification",
    "alert_recipient_groups",
]
